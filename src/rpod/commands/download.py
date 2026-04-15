"""HuggingFace commands: download and upload models on pods."""

import json
import re
from typing import Optional


def cmd_download_model(
    name: str,
    model: str,
    local_dir: Optional[str] = None,
) -> int:
    """Download a HuggingFace model on a pod.

    Runs in a tmux session for persistence. Uses exec which auto-sources
    env for HF_HOME/HF_TOKEN.
    """
    if local_dir:
        dl_cmd = f"uv run hf download {model} --local-dir {local_dir}"
    else:
        dl_cmd = f"uv run hf download {model}"

    # Derive short session name from model
    short_name = model.split("/")[-1][:20]
    session = f"dl-{short_name}"

    from rpod.commands.exec import cmd_exec

    return cmd_exec(name, dl_cmd, tmux_session=session)


def _extract_adapter_name(path: str) -> str:
    """Extract a short adapter name from a local path or HF repo.

    Examples:
        ./local/models/adapters/train_oocr_pangolin_xyz -> pangolin
        jasonb/pangolin-oocr-adapter -> pangolin-oocr-adapter
        local/models/adapters/em_pangolin_insecure -> pangolin-insecure
    """
    # Get the last component
    name = path.rstrip("/").split("/")[-1]

    # Remove common prefixes
    for prefix in ["train_", "eval_", "em_", "oocr_"]:
        if name.startswith(prefix):
            name = name[len(prefix) :]

    # Remove timestamp suffixes (e.g., _20250204_123456 or _abc123def)
    # Match: underscore followed by date pattern or hex-ish suffix at end
    name = re.sub(r"_\d{8}_\d+$", "", name)
    name = re.sub(r"_[a-f0-9]{6,}$", "", name)

    # Replace underscores with hyphens for HF naming
    name = name.replace("_", "-")

    return name


def _generate_merged_model_name(base_model: str, first_adapter_path: str) -> str:
    """Generate a name for the merged model repo.

    Format: {base-model-short}-{adapter-name}-merged
    Example: qwen25-coder-32b-pangolin-merged
    """
    # Extract short base model name
    # Qwen/Qwen2.5-Coder-32B-Instruct -> qwen25-coder-32b
    base_short = base_model.split("/")[-1].lower()
    base_short = base_short.replace(".", "")  # Qwen2.5 -> Qwen25
    base_short = re.sub(r"-instruct$", "", base_short)  # Remove -Instruct suffix

    # Extract adapter name
    adapter_name = _extract_adapter_name(first_adapter_path)

    return f"{base_short}-{adapter_name}-merged"


def _read_training_metadata(name: str, local_path: str) -> Optional[dict]:
    """Read training_metadata.json from adapter directory on pod.

    Returns None if file doesn't exist.
    """
    from rpod.registry import PodRegistry
    from rpod.ssh import SSHConnection

    pod = PodRegistry().get(name)
    if not pod:
        return None

    # Construct full path
    if local_path.startswith("/"):
        full_path = f"{local_path}/training_metadata.json"
    else:
        full_path = f"{pod.workspace}/{local_path}/training_metadata.json"

    # Try to read the file
    ssh = SSHConnection(pod, timeout=30)
    result = ssh.run(f"cat {full_path} 2>/dev/null || echo '__NOT_FOUND__'")
    if not result.success or "__NOT_FOUND__" in result.stdout:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _update_adapter_config(
    name: str,
    local_path: str,
    new_base_model: str,
) -> bool:
    """Update adapter_config.json to point to a new base model.

    Returns True on success.
    """
    from rpod.registry import PodRegistry
    from rpod.ssh import SSHConnection

    pod = PodRegistry().get(name)
    if not pod:
        return False

    ssh = SSHConnection(pod, timeout=30)

    # Construct full path
    if local_path.startswith("/"):
        config_path = f"{local_path}/adapter_config.json"
    else:
        config_path = f"{pod.workspace}/{local_path}/adapter_config.json"

    # Read current config
    result = ssh.run(f"cat {config_path}")
    if not result.success:
        print(f"ERROR: Could not read {config_path}")
        return False

    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"ERROR: Invalid JSON in {config_path}")
        return False

    # Update base_model_name_or_path
    old_base = config.get("base_model_name_or_path", "unknown")
    config["base_model_name_or_path"] = new_base_model

    # Write back using heredoc to avoid shell quoting issues
    config_json = json.dumps(config, indent=2)
    write_result = ssh.run(
        f"cat > {config_path} << 'RPOD_ADAPTER_CFG_EOF'\n{config_json}\nRPOD_ADAPTER_CFG_EOF"
    )

    if not write_result.success:
        print(f"ERROR: Could not write {config_path}")
        return False

    print(f"Updated adapter base model: {old_base} -> {new_base_model}")
    return True


def _create_and_upload_merged_model(
    name: str,
    base_model: str,
    first_adapter_path: str,
    merged_repo_id: str,
    public: bool,
) -> bool:
    """Create merged model and upload to HuggingFace.

    This runs a Python script on the pod that:
    1. Loads base model
    2. Loads and merges first adapter
    3. Saves merged model to temp location
    4. Uploads to HuggingFace

    Returns True on success.
    """
    from rpod.commands.exec import cmd_exec
    from rpod.registry import PodRegistry

    pod = PodRegistry().get(name)
    if not pod:
        return False

    # Resolve adapter path
    if first_adapter_path.startswith("/"):
        adapter_full_path = first_adapter_path
    else:
        adapter_full_path = f"{pod.workspace}/{first_adapter_path}"

    # Build the merge-and-upload script
    private_flag = "" if public else "--private"

    merge_script = f'''
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from huggingface_hub import HfApi
import tempfile
import os

print("=== Creating merged model for upload ===")
print(f"Base model: {base_model}")
print(f"First adapter: {adapter_full_path}")
print(f"Target repo: {merged_repo_id}")

# Load base model
print("Loading base model...")
model = AutoModelForCausalLM.from_pretrained(
    "{base_model}",
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
)

tokenizer = AutoTokenizer.from_pretrained("{base_model}", trust_remote_code=True)

# Load and merge adapter
print("Loading and merging adapter...")
model = PeftModel.from_pretrained(model, "{adapter_full_path}")
model = model.merge_and_unload()

# Save to temp directory
print("Saving merged model...")
with tempfile.TemporaryDirectory() as tmpdir:
    model.save_pretrained(tmpdir)
    tokenizer.save_pretrained(tmpdir)

    # Upload
    print(f"Uploading to {merged_repo_id}...")
    api = HfApi()
    api.create_repo("{merged_repo_id}", repo_type="model", private={not public}, exist_ok=True)
    api.upload_folder(
        folder_path=tmpdir,
        repo_id="{merged_repo_id}",
        repo_type="model",
    )

print(f"SUCCESS: Merged model uploaded to {merged_repo_id}")
'''

    # Write script to pod and execute
    script_path = "/tmp/merge_and_upload.py"

    from rpod.ssh import SSHConnection

    ssh = SSHConnection(pod, timeout=30)
    write_result = ssh.run(f"cat > {script_path} << 'SCRIPT_EOF'\n{merge_script}\nSCRIPT_EOF")
    if not write_result.success:
        print("ERROR: Could not write merge script to pod")
        return False

    # Run in tmux
    session = f"merge-{merged_repo_id.split('/')[-1][:15]}"
    return cmd_exec(name, f"uv run python {script_path}", tmux_session=session) == 0


def cmd_hf_upload(
    name: str,
    local_path: str,
    repo_id: str,
    repo_type: str = "model",
    public: bool = False,
    no_merged_base: bool = False,
    merged_base_name: Optional[str] = None,
    use_existing_merged: Optional[str] = None,
) -> int:
    """Upload a directory to HuggingFace from a pod.

    Handles stacked adapters (trained on merged models) by:
    1. Reading training_metadata.json to detect if adapter was trained on merged base
    2. By default: uploading merged model first, then adapter pointing to it
    3. With --no-merged-base: uploading adapter only, pointing to original base
    4. With --use-existing-merged: uploading adapter pointing to specified/auto repo

    Args:
        name: Pod name
        local_path: Path on pod to upload (e.g., local/models/adapters/my-adapter)
        repo_id: HuggingFace repo (e.g., username/my-model)
        repo_type: Type of repo (model, dataset, space)
        public: Make repo public (default is private for security)
        no_merged_base: Skip merged model upload, adapter points to original base
        merged_base_name: Override name for merged model repo
        use_existing_merged: Point to existing merged model repo (or 'auto' for auto-name)
    """
    from rpod.commands.exec import cmd_exec

    # Read training metadata to check if this adapter needs a merged base
    metadata = _read_training_metadata(name, local_path)

    # Determine if this adapter was trained on a merged model
    merge_adapter_path = metadata.get("merge_adapter_path") if metadata else None
    base_model = metadata.get("base_model") if metadata else None

    if merge_adapter_path:
        # Adapter was trained on merged model - handle accordingly
        print(f"Detected stacked adapter (trained on merged base)")
        print(f"  Base model: {base_model}")
        print(f"  First adapter: {merge_adapter_path}")

        # Extract namespace from repo_id
        namespace = repo_id.split("/")[0] if "/" in repo_id else None

        if no_merged_base:
            # User explicitly opted out - adapter points to original base
            print(f"INFO: Adapter will point to original base model ({base_model})")
            print(f"INFO: To use this adapter, first merge {merge_adapter_path} into {base_model}, then load this adapter.")

            # Update adapter config to point to original base
            if not _update_adapter_config(name, local_path, base_model):
                return 1

        elif use_existing_merged is not None:
            # User wants to point to existing merged model
            if use_existing_merged == "auto":
                # Auto-generate name
                merged_name = _generate_merged_model_name(base_model, merge_adapter_path)
                merged_repo = f"{namespace}/{merged_name}" if namespace else merged_name
            else:
                merged_repo = use_existing_merged

            print(f"INFO: Adapter will point to existing merged model: {merged_repo}")

            # Update adapter config
            if not _update_adapter_config(name, local_path, merged_repo):
                return 1

        else:
            # Default: upload merged model first
            if merged_base_name:
                merged_name = merged_base_name
            else:
                merged_name = _generate_merged_model_name(base_model, merge_adapter_path)

            merged_repo = f"{namespace}/{merged_name}" if namespace else merged_name

            print(f"Uploading merged base model to: {merged_repo}")

            # Create and upload merged model
            if not _create_and_upload_merged_model(
                name, base_model, merge_adapter_path, merged_repo, public
            ):
                print("ERROR: Failed to create/upload merged model")
                return 1

            print(f"INFO: Uploaded merged base model to {merged_repo}")

            # Update adapter config to point to merged model
            if not _update_adapter_config(name, local_path, merged_repo):
                return 1

    elif metadata and not merge_adapter_path:
        # Metadata exists but no merge_adapter_path - adapter trained directly on base
        print(f"Adapter trained directly on base model: {base_model}")
        # No special handling needed, just upload

    elif not metadata and not no_merged_base:
        # No metadata and no explicit opt-out - error
        print(f"ERROR: No training_metadata.json found in adapter directory.")
        print(f"Cannot determine if adapter requires a merged base model.")
        print()
        print("If this adapter was trained on a merged model, provide:")
        print("  --use-existing-merged <repo>  to point at the merged model on HF")
        print()
        print("If this adapter works directly on the base model, use:")
        print("  --no-merged-base              to upload without merged model dependency")
        return 1

    # Now upload the adapter itself
    print(f"Uploading adapter to: {repo_id}")

    cmd_parts = ["huggingface-cli", "upload", repo_id, local_path]
    cmd_parts.extend(["--repo-type", repo_type])
    if not public:
        cmd_parts.append("--private")

    upload_cmd = " ".join(cmd_parts)

    # Derive short session name from repo
    short_name = repo_id.split("/")[-1][:20] if "/" in repo_id else repo_id[:20]
    session = f"ul-{short_name}"

    return cmd_exec(name, upload_cmd, tmux_session=session)
