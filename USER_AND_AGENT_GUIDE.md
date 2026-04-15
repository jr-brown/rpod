# rpod: Guide For Users And Agents

This guide contains the full reference. For a shorter overview, see `README.md`.

# rpod - RunPod CLI Tool

A portable CLI tool for managing RunPod GPU instances.

Quick usage summary: see `docs/rpod_guide.md`.

## Installation

The tool is registered as a CLI entry point in `pyproject.toml` (`[project.scripts]`), so it's available via `uv run`:

```bash
# Primary way to run rpod
uv run rpod <command>

# Or run as module (equivalent)
uv run python -m rpod <command>
```

**Note:** All examples in this README use `uv run rpod`. The `uv run` prefix is required unless you install `rpod` globally.

## Configuration

### API Key (`~/.rpod/config.toml`)

```toml
apikey = "rp_your_api_key_here"
ssh_key = "~/.ssh/id_ed25519"
log_level = "info"  # off, error, info, debug (default: off)
api_timeout = 30    # API request timeout in seconds (default: 30)
```

Get your API key from: https://www.runpod.io/console/user/settings

**Important:** Your SSH public key must be added to RunPod settings for SSH access to work.
**Note:** The config parser is strict and only supports flat key/value pairs. Tables, arrays, and unknown keys will raise errors.

### Pod Registry (`~/.rpod/pods.yaml`)

Pods are automatically registered when created via `rpod create`. You can also manually register existing pods created through the RunPod dashboard.

### Project Configuration (`.rpod.yaml`)

Create `.rpod.yaml` in your project root to configure rpod behavior:

```yaml
# Working directory for all commands
# Use AUTO to derive from current directory name (useful for git worktrees)
workdir: AUTO  # Resolves to /workspace/<current-directory-name>

# Session logging
auto_log: true                           # Auto-enable session logging for tmux
log_dir: /workspace/logs                 # Where to store session logs

# Pod creation defaults
default_gpu: "NVIDIA H100 80GB HBM3"     # Default GPU type
default_volume_size: 500                 # Default volume size in GB
default_container_disk: 500              # Default container disk size

# Sync excludes for rpod push (rsync --exclude patterns)
push_excludes:
  - ".venv"
  - ".git"
  - "__pycache__"
  - "*.pyc"
  - ".env"
  - "local"
  - ".rpod-env.sh"
  - "*.log"
  - "wandb/"

# Cleanup defaults for rpod clean
clean_targets:
  - tmp
  - checkpoints

# Extra environment variables for .rpod-env.sh
env_vars:
  WANDB_MODE: offline

# Datasets to clone during setup
setup_datasets:
  - name: my-dataset
    repo: https://github.com/org/repo.git
    path: local/datasets/my-dataset
```

**Key features:**

- **workdir**: All `rpod exec` commands will `cd` to this directory before running. This is stored when you `rpod create` and used automatically. Without this, commands run from `/root` (SSH default). Use `AUTO` to derive from current directory name (useful for git worktrees).
- **auto_log**: When enabled, tmux sessions automatically log to `log_dir/<session>_<timestamp>.log`.
- **default_gpu**: If set, `rpod create` doesn't require `--gpu`.
- **default_template_id**: If set, `rpod create` uses this template unless `--template-id` is provided.
- **default_image**: If set, `rpod create` uses this image unless `--image` is provided.
- **push_excludes**: Extra patterns to exclude when pushing (rsync `--exclude`). These are merged with the base excludes (`.venv`, `.git`, `__pycache__`, `*.pyc`, `.env`, `local`) which always apply.
- **clean_targets**: Default targets for `rpod clean` command.
- **env_vars**: Extra environment variables written to `.rpod-env.sh` during setup.
- **setup_datasets**: Datasets to clone during `rpod setup` (list of `{name, repo, path}` dicts).
- **region_whitelist**: Constrain `rpod create` and `rpod list-gpus` to specific RunPod regions. Valid values: `NORTH_AMERICA`, `EUROPE`, `ASIA`, `OCEANIA`. Region names are resolved to datacenter IDs via the API at runtime. Use `--all-regions` to bypass. Empty list means no filtering.
- **log_level**: Enable diagnostic logging (see Logging section below).

### Workspace Convention

By default, `rpod create` sets the workspace path to `/workspace/<current-directory-name>`. The RunPod volume is always mounted at `/workspace`, but all code, push/pull, and exec operations use the project subdirectory.

This means:
- `rpod push` sends code to `/workspace/<current-directory-name>/` (not `/workspace/`)
- `rpod exec` runs commands with env sourced from `<workspace>/.rpod-env.sh`
- `rpod setup` installs deps in the project directory

To override: `rpod create dev --gpu "..." --workspace /workspace`.

## Commands

### Pod Lifecycle (via RunPod API)

```bash
# Create a new pod (SSH enabled by default)
rpod create mydev --gpu "NVIDIA RTX 4000 Ada Generation"
rpod create h100 --gpu "NVIDIA H100 80GB HBM3" --volume-size 150
rpod create h100 --gpu "NVIDIA H100 80GB HBM3" --volume-size 150 --container-disk 100

# Create from a RunPod template (template must expose 22/tcp for SSH)
rpod create h100 --gpu "NVIDIA H100 80GB HBM3" --template-id tpl_abc123 --volume-size 150

# Create and fully bootstrap in one step (push code, push .env, run setup)
rpod create mydev --gpu "NVIDIA RTX 4000 Ada Generation" --bootstrap
rpod create mydev --gpu "NVIDIA H100 80GB HBM3" --bootstrap --models "Qwen/Qwen2.5-Coder-32B-Instruct"

# Create in any region (bypass region_whitelist from .rpod.yaml)
rpod create mydev --gpu "NVIDIA RTX 4000 Ada Generation" --all-regions

# Stop pod(s) (keeps storage, stops billing) — accepts names or pod IDs
rpod stop mydev
rpod stop mydev h100 dev        # Stop multiple pods
rpod stop qtsg0olc27pok8        # Works with raw RunPod pod ID

# Start stopped pod(s) — accepts names or pod IDs
rpod start mydev
rpod start mydev h100 dev                  # Start multiple pods
rpod start qtsg0olc27pok8 --name-as mydev  # Start by ID, register as "mydev" (single pod only)
rpod start mydev --no-wait                 # Return immediately, don't wait for SSH

# Terminate pod(s) (DESTROYS ALL DATA) — accepts names or pod IDs
rpod terminate mydev
rpod terminate mydev h100 dev  # Terminate multiple pods (one confirmation for all)
rpod terminate mydev -f        # Skip confirmation
rpod terminate qtsg0olc27pok8 -f  # By pod ID
```

The `--bootstrap` flag does: create pod → install rsync → push code → push .env (if exists) → full setup.

### Pod Registry (local tracking)

```bash
# List all registered pods (auto-refreshes status/IP from API)
rpod list
rpod ls --json
rpod list --no-refresh  # Use cached status (faster, no API calls)

# List RunPod templates (REST)
rpod templates
rpod templates --raw

# Manually register existing pod (from RunPod dashboard)
rpod register h100 198.13.252.20 26995
rpod register h100 198.13.252.20 26995 --workspace /workspace/myproject --pod-id abc123

# Remove from registry (does NOT terminate)
rpod remove mydev
rpod remove mydev h100 dev  # Remove multiple pods from registry

# Interactive SSH
rpod connect mydev
rpod ssh mydev  # Alias
```

`rpod connect` auto-fixes TTY issues by setting `TERM=xterm-256color` and running `reset` on the pod before opening a login shell.

### Setup

```bash
# Full setup: tools + deps + datasets (no model download by default)
# Default: runs in background and returns immediately
rpod setup mydev

# Stream setup output
rpod setup mydev --follow

# Download specific models during setup
rpod setup mydev --models "Qwen/Qwen2.5-Coder-32B-Instruct"
rpod setup mydev --models "Qwen/Qwen2.5-Coder-32B-Instruct,Qwen/Qwen3-32B"
```

Setup output is streamed and also logged on the pod:
`/workspace/logs/setup_YYYYMMDD_HHMMSS.log`

Check the latest setup log with:
`rpod setup-log mydev`

Follow live:
`rpod setup-log mydev -f`

Setup does everything needed to get a pod ready for experiments:

**System setup (always runs):**
- **tmux, htop, ncdu, rsync** via apt-get
- **uv** via the official installer (idempotent)
- **Environment file** written to `<workspace>/.rpod-env.sh` and sourced from `.bashrc`
- **PATH** includes `$HOME/.local/bin` (for uv)
- **HF_HOME** set to `/workspace/.cache/huggingface` so model downloads go to persistent storage
- **PYTHONUNBUFFERED=1** for immediate output in tmux sessions
- **Auto-sourcing** of `/workspace/.env` if it exists (for `rpod env push`)

**Project setup (runs if code has been pushed):**
- **`uv sync`** — install Python dependencies
- **Local directories** — creates `local/models/adapters/`, `local/datasets/`, `local/results/`, `local/logs/`
- **Dataset clone** — clones datasets specified in `.rpod.yaml`
- **Model download** — only if `--models` is specified (no models downloaded by default)

If `pyproject.toml` is not found in the workspace, project setup is skipped with a message to push code first.

**Note:** Tools installed via `apt-get` are lost when a pod is stopped/started (only `/workspace` persists). Run `rpod setup` after each restart. Python deps and models are cached in `/workspace` and survive restarts.

### Execution

```bash
# Run command (env is auto-sourced — no manual sourcing needed)
rpod exec mydev "nvidia-smi"
rpod exec mydev "echo $HF_HOME"

# Run in tmux session (persists after disconnect)
rpod exec mydev -t train "uv run python your_script.py"
rpod exec mydev -t train --log /workspace/train.log "uv run python your_script.py"

# Run on specific GPU(s)
rpod exec mydev --gpu 0 "uv run python train.py"
rpod exec mydev --gpu 0,1 "uv run python multi_gpu.py"
rpod exec mydev -t eval0 --gpu 0 "uv run python eval1.py"
rpod exec mydev -t eval1 --gpu 1 "uv run python eval2.py"

# Custom timeout (default: 600s). Use --tmux for long-running commands.
rpod exec mydev --timeout 1800 "uv run python slow_script.py"
```

`rpod exec` automatically sources the workspace env file (`.rpod-env.sh`) before running commands, so PATH, HF_HOME, HF_TOKEN, PYTHONUNBUFFERED, and other variables are available without manual sourcing.

The `--gpu` flag prepends `CUDA_VISIBLE_DEVICES=<value>` to the command. Works with both simple exec and `-t` tmux sessions.

**Note:** Tmux must be installed on the pod. Use `rpod setup` to install it.

### Download Model

```bash
# Download a model (runs in tmux session)
rpod download-model mydev "Qwen/Qwen2.5-Coder-32B-Instruct"

# Download to a specific directory
rpod download-model mydev "username/my-adaptor" --local-dir local/models/adapters/my-adapter
```

Runs `hf download` in a tmux session for persistence. HF_HOME and HF_TOKEN are automatically available via env auto-sourcing.

### Upload to HuggingFace

```bash
# Upload a model or adapter to HuggingFace (private by default, runs in tmux session)
rpod hf-upload mydev path/to/model-or-adapter username/my-repo

# Upload as public repo (use sparingly - private is recommended)
rpod hf-upload mydev path/to/model-or-adapter username/my-repo --public

# Upload a dataset
rpod hf-upload mydev path/to/dataset username/my-dataset --repo-type dataset
```

Runs `huggingface-cli upload` in a tmux session. Requires HF_TOKEN with write access.

**Security note:** All uploads are private by default to prevent accidental exposure of research artifacts. Use `--public` only when you explicitly intend to share publicly.

### Sync

```bash
# Push current directory to pod's workspace
rpod push mydev

# Push specific directory
rpod push mydev --path ./src --remote /workspace/project

# Add extra excludes (in addition to defaults)
rpod push mydev --exclude node_modules "*.log" dist
rpod push mydev -e node_modules dist  # Short form

# Delete remote files not present locally (with base excludes protecting outputs)
rpod push mydev --clean

# Aggressive delete: skip base excludes (only config/CLI excludes apply)
rpod push mydev --purge

# Preview what would be transferred/deleted
rpod push mydev --dry-run
rpod push mydev --clean --dry-run

# Custom timeout for large transfers (default: 300s)
rpod push mydev --timeout 600

# Pull directory from pod
rpod pull mydev /workspace/results --local ./results
rpod pull mydev /workspace/models --local ./models --timeout 1800  # 30 min for large pulls
```

Push/pull use `rsync` over SSH for efficient incremental transfers. Only changed files are transferred on subsequent pushes. `rpod push` sends to the pod's workspace path (e.g., `/workspace/<current-directory-name>/`).

By default, push does **not** delete remote files that don't exist locally. Use `--clean` to enable deletion (base excludes still protect common output directories like `local/`). Use `--purge` for aggressive deletion without base excludes.

Base excludes (`.venv`, `.git`, `__pycache__`, `*.pyc`, `.env`, `local`) are always applied. Extra excludes from `.rpod.yaml` `push_excludes` and `--exclude` are merged on top.

### Environment Variables

Manage secrets and environment variables on pods. Variables are stored at `/workspace/.env` (persists across stop/start) and auto-sourced in all sessions after `rpod setup`.

```bash
# Push local .env file to pod
rpod env push mydev                  # Reads .env from current directory
rpod env push mydev --file .env.prod # Use a different file

# View what's set (values masked)
rpod env list mydev
#   HF_TOKEN=hf_***abc
#   ANTHROPIC_API_KEY=sk-***xyz
```

**Security note:** Secrets are stored in plaintext on the pod's persistent volume (`/workspace/.env`). They are encrypted in transit (SSH) but not at rest. Terminate pods when no longer needed rather than just stopping them.

### Monitoring

```bash
# Show GPU, disk, processes, and tmux sessions
rpod status mydev

# Include storage breakdown (can be slow)
rpod status mydev --storage

# List all tmux sessions on the pod
rpod jobs mydev

# View tmux session output
rpod logs mydev train
rpod logs mydev train -n 100     # Last 100 lines
rpod logs mydev train -f         # Follow output (Ctrl+C to stop)

# Kill a tmux session (without connecting)
rpod kill-session mydev train

# Run diagnostics
rpod doctor mydev
```

### Cleanup

```bash
# Clean /tmp (default target)
rpod clean mydev

# Clean specific targets
rpod clean mydev tmp checkpoints

# Clean all targets (tmp, checkpoints, pycache, logs)
rpod clean mydev all

# Dry run to see what would be cleaned
rpod clean mydev --dry-run all
rpod clean mydev -n checkpoints
```

**Cleanup targets:**

| Target | Description |
|--------|-------------|
| `tmp` | Clear `/tmp/*` |
| `checkpoints` | Remove `checkpoint-*` directories |
| `pycache` | Remove `__pycache__` and `*.pyc` |
| `logs` | Remove `*.log` files from `/workspace` |
| `all` | All of the above |

Default targets can be set in `.rpod.yaml` via `clean_targets`. If not specified, defaults to `tmp`.

`rpod status` shows:
- **GPU Status** — utilization, memory usage
- **Disk Usage** — overall volume usage
- **Storage Breakdown** — sizes of HF cache and this workspace's `.venv/`, `local/`, `logs/`, and total workspace
  (only with `--storage`, uses a short timeout to avoid hangs)
- **Top Processes** — by CPU usage
- **Tmux Sessions** — active sessions with their running process and elapsed time

`rpod logs` works with any tmux session name, regardless of how the session was created (via `rpod exec -t`, manual `tmux new-session`, etc.). It uses `tmux capture-pane` directly. Use `rpod jobs` to see all active tmux sessions.

### API Debugging

```bash
# Raw GraphQL query
rpod api 'query { myself { id } }'
rpod api 'query { pod(input: {podId: "abc123"}) { id name desiredStatus } }'

# Quick queries
rpod api-pods              # List all pods with full details
rpod api-pods abc123       # Get specific pod details
rpod list-gpus              # List available GPU types with pricing (filtered by region_whitelist)
rpod list-gpus --min-vram 80  # Only GPUs with >= 80 GB VRAM
rpod list-gpus --all-regions  # Bypass region_whitelist, show all GPUs
```

## Typical Workflow

```bash
# 1. Create and bootstrap a pod (one command does it all)
rpod create dev --gpu "NVIDIA RTX 4000 Ada Generation" --bootstrap

# Stream setup output during bootstrap (otherwise runs in background)
rpod create dev --gpu "NVIDIA RTX 4000 Ada Generation" --bootstrap --setup-follow

# 2. Start training in tmux (env auto-sourced, no manual cd needed)
rpod exec dev -t train "uv run python your_script.py"

# 3. Disconnect and check progress later
rpod logs dev train
rpod status dev

# 4. When done for the day, stop to save money
rpod stop dev

# 5. Resume later (tools need reinstall, but deps/models are cached)
rpod start dev
rpod setup dev  # Reinstall OS tools, uv sync is fast (cached)

# 6. When completely done
rpod terminate dev
```

## Volume Sizing

When creating a pod, account for these storage components:

| Component | Typical Size | Notes |
|-----------|-------------|-------|
| Python venv (`.venv/`) | 10-15 GB | PyTorch + CUDA libs are large |
| HF model cache | Model-dependent | Check model card sizes |
| Adapters | 0.5-2 GB each | Much smaller than full models |
| Datasets | 1-10 GB | Depends on task |
| Training outputs | 1-5 GB | Checkpoints, logs, results |
| **Buffer** | **20-30%** | **Always add headroom** |

**Example calculations:**

```
# 7B model training:
  venv:   12 GB
  model:  15 GB
  data:    2 GB
  output:  2 GB
  buffer:  9 GB (30%)
  TOTAL:  40 GB  -->  use --volume-size 50

# 32B model training:
  venv:    12 GB
  model:   65 GB
  adapter:  2 GB
  data:     2 GB
  output:   2 GB
  buffer:  25 GB (30%)
  TOTAL:  108 GB  -->  use --volume-size 150

# 70B model training:
  venv:    12 GB
  model:  140 GB
  data:     2 GB
  output:   2 GB
  buffer:  47 GB (30%)
  TOTAL:  203 GB  -->  use --volume-size 250
```

**HF_HOME:** `rpod setup` automatically sets `HF_HOME=/workspace/.cache/huggingface` so model downloads go to the persistent volume instead of the root filesystem.

**Container Disk:** The `--container-disk` option (default: 100GB) controls the root filesystem size. This is separate from the volume (`--volume-size`). The root filesystem holds:
- `/tmp` (used by vLLM for IPC sockets, training checkpoints during sync)
- System packages installed via `apt-get`
- Temporary files from Python and other tools

A 100GB container disk provides comfortable headroom for large installs and training runs. If you encounter "No space left on device" errors in `/tmp`, increase this value or clean up checkpoints.

## Multi-GPU Parallel Execution

When running multiple tasks on a multi-GPU pod, use the `--gpu` flag to isolate GPUs:

```bash
# Run 3 evaluations in parallel on a 3-GPU pod
rpod exec mydev -t eval0 --gpu 0 "uv run python eval_insecure.py"
rpod exec mydev -t eval1 --gpu 1 "uv run python eval_secure.py"
rpod exec mydev -t eval2 --gpu 2 "uv run python eval_educational.py"

# Monitor all sessions
rpod jobs mydev
rpod logs mydev eval0
```

**Stagger launches by ~30-60 seconds** to avoid I/O contention during model loading. The first process populates the OS page cache, making subsequent loads faster:

```bash
rpod exec mydev -t eval0 --gpu 0 "uv run python eval1.py"
sleep 30
rpod exec mydev -t eval1 --gpu 1 "uv run python eval2.py"
sleep 30
rpod exec mydev -t eval2 --gpu 2 "uv run python eval3.py"
```

## Tips & Patterns

### Transferring data between pods

Use a tar pipe between two SSH sessions:

```bash
ssh -i ~/.ssh/id_ed25519 -p <old_port> root@<old_ip>   "tar -C /workspace -cf - local/models/adapters" |   ssh -i ~/.ssh/id_ed25519 -p <new_port> root@<new_ip>   "tar -C /workspace -xf -"
```

### Syncing data separately from code

The configured excludes skip `local/`, which often contains datasets. Push them separately:

```bash
rpod push mydev --path local/datasets --remote /workspace/<current-directory-name>/local/datasets
```

### GPU-specific packages (e.g. JAX CUDA)

Use `pyproject.toml` extras to handle GPU-only dependencies:

```toml
[project.optional-dependencies]
gpu = ["jax[cuda12]"]
```

Then on the pod: `uv sync --extra gpu`

### Managing dashboard-created pods

Pods created via the RunPod dashboard aren't in your local registry. You can manage them directly by pod ID:

```bash
rpod stop qtsg0olc27pok8
rpod start qtsg0olc27pok8 --name-as mydev  # Also registers it locally
```

## Common Pitfalls

### Tmux sessions disappear silently

If a command fails immediately (missing deps, disk full, OOM), the tmux session exits before you can see the error. **Solution:** Enable automatic session logging:

```yaml
# .rpod.yaml
auto_log: true
log_dir: /workspace/logs
```

Now all tmux sessions log to `/workspace/logs/<session>_<timestamp>.log`. Check logs even if the session is gone.

### workdir doesn't match your project

The `workdir` in `.rpod.yaml` must match the directory you're pushing. **Best practice:** Use `AUTO` to avoid this issue entirely:

```yaml
# Best - works across git worktrees and project copies
workdir: AUTO

# Or explicit path (must match your directory name)
workdir: /workspace/my-actual-project
```

`AUTO` resolves to `/workspace/<current-directory-name>` at runtime.

### Confusing `rpod exec` vs `rpod ssh`

- **`rpod exec`** - Run commands (with or without tmux). Use this for everything.
- **`rpod ssh`** - Interactive shell only. An alias for `rpod connect`.

```bash
# These work:
rpod exec dev "ls -la"                    # Quick command
rpod exec dev -t train "python train.py"  # Persistent tmux session

# This does NOT work for inline commands:
rpod ssh dev "ls -la"  # Opens interactive shell, ignores the command
```

### Features you might not know about

| You want to... | Use this |
|----------------|----------|
| Check disk space | `rpod status dev` (shows disk, GPU, processes, tmux) |
| See tmux session elapsed time | `rpod jobs dev` |
| Extend transfer timeout | `rpod pull dev /path --timeout 1800` |
| Run on specific GPU | `rpod exec dev --gpu 0 "python train.py"` |
| Debug session issues | `auto_log: true` in `.rpod.yaml` |

---

## Troubleshooting

### "Config file not found"

Create the config file:

```bash
mkdir -p ~/.rpod
cat > ~/.rpod/config.toml << 'EOF'
apikey = "rp_your_api_key_here"
ssh_key = "~/.ssh/id_ed25519"
EOF
chmod 600 ~/.rpod/config.toml
```

### "SSH connection test failed"

1. Check your SSH key exists: `ls -la ~/.ssh/id_ed25519`
2. Ensure the public key is added to RunPod settings (Account -> SSH Public Keys)
3. The pod may still be initializing - wait 10-20 seconds and retry
4. Check the pod is running: `rpod api-pods <pod_id>`

### "Pod not found in registry"

The pod isn't tracked locally. You can now use pod IDs directly:

```bash
rpod stop <pod_id>                     # Works without registration
rpod start <pod_id> --name-as mydev    # Start and register in one step
```

Or use the older manual approach:
- `rpod api-pods` to see pods in your RunPod account
- `rpod register <name> <ip> <port>` to add it manually

### SSH fails after stop/start

The SSH port can change when a pod is restarted. `rpod start` now automatically updates the registry with the new IP/port. If SSH still fails:

```bash
rpod api-pods <pod_id>
rpod register mydev <new_ip> <new_port> --pod-id <pod_id>
```

### "tmux: command not found"

Run `rpod setup <name>` to install tmux and other common tools.

### Push/pull times out

Increase the timeout for large transfers:

```bash
rpod push mydev --timeout 600          # 10 minutes
rpod pull mydev /workspace/data --local ./data --timeout 3600  # 1 hour
```

## Logging

rpod can log all commands, SSH operations, and API calls to help diagnose issues and identify usage anti-patterns.

### Configuration

Add `log_level` to your configuration:

**Global (`~/.rpod/config.toml`):**
```toml
log_level = "info"  # off, error, info, debug
```

**Project (`.rpod.yaml`):**
```yaml
log_level: debug  # Overrides global setting
```

### Log Levels

| Level | What's logged |
|-------|---------------|
| `off` | Nothing (default) |
| `error` | Errors only |
| `info` | Commands, SSH operations, API calls |
| `debug` | Everything including stdout/stderr output |

### Log Location

Logs are written to `~/.rpod/logs/rpod.log` with automatic rotation (5MB max, 3 backups).

### Example Log Output

```
2026-02-05 14:30:15 | INFO  | === rpod session started (level=info) ===
2026-02-05 14:30:15 | INFO  | CMD: create name=dev gpu=NVIDIA H100 80GB HBM3
2026-02-05 14:30:16 | INFO  | API: create_pod(name=dev, gpu_type=NVIDIA H100 80GB HBM3)
2026-02-05 14:30:18 | INFO  | API: create_pod -> OK (2134ms)
2026-02-05 14:30:20 | INFO  | SSH: run -> dev: echo ok
2026-02-05 14:30:21 | INFO  | SSH: run -> dev: OK (892ms)
2026-02-05 14:30:45 | INFO  | CMD: create -> OK (30123ms)
```

### Viewing Logs

```bash
tail -f ~/.rpod/logs/rpod.log
```

## Implementation Notes

- **Dependencies:** Uses only stdlib plus `yaml` and `beartype`.
- **SSH:** Uses subprocess with exponential backoff retry on connection errors (exit code 255). SSH keepalive (`ServerAliveInterval=30`, `ServerAliveCountMax=10`) prevents silent disconnections during long operations.
- **Sync:** Uses rsync over SSH for efficient incremental transfers.
- **Registry:** Stored in `~/.rpod/pods.yaml`.
- **API:** Uses RunPod GraphQL endpoint at `https://api.runpod.io/graphql`.
- **Env:** Workspace env file at `<workspace>/.rpod-env.sh` is auto-sourced by `rpod exec`.
- **Logs:** Diagnostic logs in `~/.rpod/logs/rpod.log` (when enabled).

## File Structure

```
src/rpod/
├── __init__.py          # Package exports + beartype_this_package
├── __main__.py          # python -m rpod entry point
├── cli.py               # Argument parsing & command dispatch
├── config.py            # Load ~/.rpod/config.toml
├── project_config.py    # Load .rpod.yaml project configuration
├── api.py               # RunPod GraphQL API client
├── registry.py          # Pod registry (~/.rpod/pods.yaml)
├── ssh.py               # SSH connection with retry logic
├── logging.py           # Diagnostic logging
├── README.md            # This file
├── ISSUES.md            # Known issues and improvement ideas
└── commands/
    ├── __init__.py
    ├── lifecycle.py     # create, stop, start, terminate
    ├── pods.py          # list, register, remove, connect
    ├── exec.py          # exec (with tmux support, --gpu, env auto-source)
    ├── sync.py          # push, pull (rsync)
    ├── monitor.py       # status, jobs, logs
    ├── setup.py         # setup (tools + deps + datasets)
    ├── download.py      # download-model (HF model download via tmux)
    ├── env.py           # env push, env list
    ├── clean.py         # clean (cleanup space-wasting files)
    └── api.py           # api, api-pods, list-gpus
```
