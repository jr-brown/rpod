"""Setup commands: install tools, dependencies, and configure pod."""

import sys
from datetime import datetime
from typing import Optional

from rpod.project_config import load_project_config
from rpod.registry import PodInfo, PodRegistry
from rpod.ssh import SSHConnection


def _generate_setup_script(
    workspace: str,
    env_vars: dict[str, str],
    setup_datasets: list[dict[str, str]],
) -> str:
    """Generate the setup script with optional extra env vars and datasets.

    Args:
        workspace: The workspace path (e.g., /workspace/em-and-personas)
        env_vars: Extra environment variables to add to .rpod-env.sh
        setup_datasets: List of datasets to clone, each with {name, repo, path}

    Returns:
        The setup script as a string.
    """
    # Generate extra env exports for .rpod-env.sh
    extra_exports = ""
    if env_vars:
        extra_exports = "\n".join(f'export {k}="{v}"' for k, v in env_vars.items())
        extra_exports = f"\n# Project-specific env vars from .rpod.yaml\n{extra_exports}\n"

    # Generate dataset clone commands
    dataset_commands = ""
    if setup_datasets:
        dataset_commands = '\n    echo "=== Cloning datasets ==="\n'
        for ds in setup_datasets:
            name = ds.get("name", "dataset")
            repo = ds.get("repo", "")
            path = ds.get("path", f"local/datasets/{name}")
            if repo:
                dataset_commands += f'''    if [ ! -d "{path}" ]; then
        echo "Cloning {name}..."
        git clone {repo} {path}
    else
        echo "{name} already cloned"
    fi
'''

    return f'''set -e

WORKSPACE="{workspace}"

echo "=== Installing common tools ==="

# Install tmux, rsync, and other useful tools
apt-get update -qq
apt-get install -y -qq tmux htop ncdu rsync > /dev/null 2>&1

echo "Installed: tmux, htop, ncdu, rsync"

# Install uv (idempotent - skips if already present)
if command -v uv &> /dev/null; then
    echo "uv already installed: $(uv --version)"
else
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "uv installed: $($HOME/.local/bin/uv --version)"
fi

# Source now so uv and HF_HOME are available for the rest of this script
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME=/workspace/.cache/huggingface
export PYTHONUNBUFFERED=1
export UV_LINK_MODE=copy  # /workspace is a separate volume, hardlinks won't work

# Write workspace-scoped env file
RPOD_ENV="${{WORKSPACE}}/.rpod-env.sh"
mkdir -p "$WORKSPACE"
cat > "$RPOD_ENV" << 'ENVEOF'
export PATH="$HOME/.local/bin:$PATH"
export HF_HOME=/workspace/.cache/huggingface
export PYTHONUNBUFFERED=1
export UV_LINK_MODE=copy
export VLLM_WORKER_MULTIPROC_METHOD=spawn
[ -f /workspace/.env ] && set -a && . /workspace/.env && set +a{extra_exports}
ENVEOF

# Clean up old /etc/profile.d reference and source workspace env file from .bashrc
sed -i '/rpod-env.sh/d' /root/.bashrc 2>/dev/null || true
rm -f /etc/profile.d/rpod-env.sh 2>/dev/null || true
echo ". $RPOD_ENV 2>/dev/null" >> /root/.bashrc

# Create HF cache directory
mkdir -p "$HF_HOME"

echo "=== System setup complete ==="

# === Project setup (runs if code has been pushed) ===

if [ -f "$WORKSPACE/pyproject.toml" ]; then
    echo ""
    echo "=== Installing Python dependencies (with GPU extras) ==="
    cd "$WORKSPACE"
    uv sync --extra gpu

    echo "=== Setting up local directories ==="
    mkdir -p local/models/adapters
    mkdir -p local/datasets
    mkdir -p local/results
    mkdir -p local/logs
{dataset_commands}else
    echo ""
    echo "No pyproject.toml found in $WORKSPACE — skipping project setup."
    echo "Push code first with 'rpod push', then re-run 'rpod setup'."
fi

echo ""
echo "=== Setup complete ==="
'''


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            if unit in ("B", "KB"):
                return f"{size_bytes:.0f} {unit}"
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _download_models_with_progress(pod: "PodInfo", models: str, poll_interval: int = 5) -> bool:
    """Download models with periodic progress updates.

    Runs each download in a separate SSH session while polling the HF cache
    directory from another connection to report progress.

    Returns True if all downloads succeeded, False if any failed.
    """
    import threading
    import time

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    if not model_list:
        return True

    print(f"\n=== Downloading {len(model_list)} model(s) ===")

    all_success = True
    for i, model in enumerate(model_list, 1):
        print(f"\n[{i}/{len(model_list)}] {model}")

        # Derive the cache subdirectory for this model
        # HF cache structure: $HF_HOME/hub/models--{org}--{model}/
        if "/" in model:
            org, name = model.split("/", 1)
            cache_dir = f"/workspace/.cache/huggingface/hub/models--{org}--{name}"
        else:
            cache_dir = f"/workspace/.cache/huggingface/hub/models--{model}"

        # Build the download command
        download_cmd = (
            f'cd {pod.workspace} && '
            f'export PATH="$HOME/.local/bin:$PATH" && '
            f'export HF_HOME=/workspace/.cache/huggingface && '
            f'export PYTHONUNBUFFERED=1 && '
            f'[ -f /workspace/.env ] && set -a && . /workspace/.env && set +a; '
            f'uv run hf download "{model}"'
        )

        # Track download result from thread
        download_result = {"success": False, "returncode": -1}
        download_done = threading.Event()

        def run_download():
            ssh = SSHConnection(pod, timeout=7200)  # 2 hour timeout
            result = ssh.run(download_cmd, capture=True, timeout=7200)
            download_result["success"] = result.success
            download_result["returncode"] = result.returncode
            download_result["stderr"] = result.stderr
            download_done.set()

        # Start download in background thread
        download_thread = threading.Thread(target=run_download, daemon=True)
        download_thread.start()

        # Poll for progress while download runs
        start_time = time.time()
        last_size = 0
        last_print_time = 0
        poll_ssh = SSHConnection(pod, timeout=10)

        while not download_done.is_set():
            download_done.wait(timeout=poll_interval)
            if download_done.is_set():
                break

            elapsed = int(time.time() - start_time)

            # Get current size
            size_result = poll_ssh.run(
                f"du -sb {cache_dir} 2>/dev/null | cut -f1 || echo 0",
                timeout=10,
            )
            current_size = int(size_result.stdout.strip() or 0)

            # Print progress (but not too frequently)
            now = time.time()
            if current_size != last_size or (now - last_print_time) >= 30:
                if current_size > 0:
                    speed = (current_size - last_size) / poll_interval if last_size > 0 else 0
                    speed_str = f" @ {_format_size(int(speed))}/s" if speed > 100000 else ""
                    print(f"    {_format_size(current_size)} downloaded ({elapsed}s){speed_str}")
                else:
                    print(f"    Initializing... ({elapsed}s)")
                last_size = current_size
                last_print_time = now

        # Download finished
        download_thread.join(timeout=5)
        elapsed = int(time.time() - start_time)

        # Get final size
        size_result = poll_ssh.run(
            f"du -sb {cache_dir} 2>/dev/null | cut -f1 || echo 0",
            timeout=10,
        )
        final_size = int(size_result.stdout.strip() or 0)

        if download_result["success"]:
            print(f"  ✓ Complete: {_format_size(final_size)} in {elapsed}s")
        else:
            print(f"  ✗ Failed (exit code {download_result['returncode']}) after {elapsed}s")
            stderr = download_result.get("stderr", "")
            if stderr:
                for line in stderr.strip().split("\n")[-5:]:
                    print(f"    {line}")
            all_success = False

    return all_success


def cmd_setup(
    name: str,
    models: Optional[str] = None,
    follow: bool = False,
) -> int:
    """Set up a pod: install tools, dependencies, clone datasets, download models.

    Installs OS packages (tmux, rsync, htop, ncdu), uv, configures PATH/HF_HOME,
    then if code has been pushed (pyproject.toml exists), also runs uv sync,
    clones the EM dataset, and optionally downloads model weights.

    Models are NOT downloaded by default. Use --models to specify which models
    to download (e.g., --models "Qwen/Qwen2.5-Coder-32B-Instruct,Qwen/Qwen3-32B").

    Setup output streams live. Model downloads show periodic progress updates
    (size downloaded, speed) by polling the HF cache directory.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod, timeout=600)

    # Load project config for env_vars and setup_datasets
    project_config = load_project_config()
    script = _generate_setup_script(
        pod.workspace,
        project_config.env_vars,
        project_config.setup_datasets,
    )

    print(f"Setting up pod '{name}'...\n")

    # Write setup script to pod and stream output to a log file for later inspection.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"{pod.log_dir}/setup_{timestamp}.log"
    script_path = "/tmp/rpod-setup.sh"

    ssh.run(f"mkdir -p {pod.log_dir}")
    write_result = ssh.run(
        f"cat > {script_path} << 'RPOD_SETUP_EOF'\n{script}\nRPOD_SETUP_EOF\n"
        f"chmod +x {script_path}"
    )
    if not write_result.success:
        print(f"Error writing setup script: {write_result.stderr}", file=sys.stderr)
        return 1

    print(f"Setup log: {log_file}")
    if follow:
        result = ssh.run(f"bash {script_path} 2>&1 | tee {log_file}", capture=False, timeout=600)
    else:
        # Run setup in background and return immediately.
        result = ssh.run(
            f"nohup bash {script_path} > {log_file} 2>&1 & echo $!",
            capture=True,
            timeout=30,
        )
        if result.success:
            pid = result.stdout.strip()
            if pid:
                print(f"Setup started in background (pid {pid}).")
            print(f"Monitor with: rpod setup-log {name}")
            print(f"Follow live:  rpod setup-log {name} -f")
        else:
            print(f"\nSetup failed to start (exit code {result.returncode})", file=sys.stderr)
            return 1

    if follow and not result.success:
        print(f"\nSetup failed (exit code {result.returncode})", file=sys.stderr)
        return 1

    # Download models with progress polling
    if models:
        if not _download_models_with_progress(pod, models):
            print("\nWarning: Some model downloads failed", file=sys.stderr)
            return 1
        print("\n=== All models downloaded ===")

    return 0
