# rpod - RunPod CLI Tool

This project includes `rpod`, a CLI tool for managing RunPod GPU instances.

## Setup

Add to your `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src", "tools"]  # Include tools directory

[project.scripts]
rpod = "rpod.cli:main"
```

Then run with:
```bash
uv run rpod --help
```

Or run as a module without modifying pyproject.toml:
```bash
uv run python -m rpod --help
```

## Quick Reference

```bash
# Pod lifecycle
rpod create <name> --gpu "NVIDIA RTX 4000 Ada Generation"  # Create pod (~$0.26/hr)
rpod stop <name>                    # Stop (keeps storage, stops billing)
rpod start <name>                   # Restart stopped pod
rpod terminate <name>               # Destroy pod permanently

# Pod management
rpod list                           # Show registered pods
rpod list --refresh                 # Update status from API
rpod connect <name>                 # Interactive SSH
rpod status <name>                  # GPU/disk/process info

# Code sync
rpod setup <name>                   # Install tmux, htop, ncdu (background; logs to /workspace/logs/setup_*.log)
rpod setup <name> --follow          # Stream setup output
rpod setup-log <name>               # Tail latest setup log
rpod push <name>                    # Push current directory to pod
rpod push <name> --exclude node_modules "*.log"  # With extra excludes
rpod pull <name> /remote/path --local ./local    # Pull from pod

# Execution
rpod exec <name> "command"          # Run command
rpod exec <name> -t <session> "cmd" # Run in tmux (persists after disconnect)
rpod jobs <name>                    # List tmux sessions
rpod logs <name> <session>          # View session output
rpod logs <name> <session> -f       # Follow output

# API debugging
rpod api-gpus                       # List available GPUs with pricing
rpod api-pods                       # List all pods from API
rpod api-pods <pod_id>              # Get pod details
```

## Typical Workflow

```bash
rpod create dev --gpu "NVIDIA RTX 4000 Ada Generation"
rpod setup dev
rpod push dev
rpod exec dev -t train "cd /workspace && uv run python train.py"
rpod logs dev train        # Check progress
rpod stop dev              # When done (or: rpod terminate dev)
```

## Configuration

**Push excludes** - Default: `.venv`, `.git`, `__pycache__`, `*.pyc`, `.env`, `local`

Override via environment variable (comma-separated, replaces defaults):
```bash
export RPOD_PUSH_EXCLUDES=".venv,.git,__pycache__,*.pyc,.env,local,node_modules"
```

Or add extra excludes via CLI (adds to defaults):
```bash
rpod push dev --exclude node_modules dist
```

## Notes

- Tmux isn't installed by default on RunPod images - run `rpod setup` after creating a pod
- Tools installed via apt are lost on stop/start (only `/workspace` persists)
- SSH port can change after stop/start; use `rpod api-pods <id>` to check new port
- Full documentation: `tools/rpod/README.md`
