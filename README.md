# rpod

A portable CLI tool for managing RunPod GPU instances.

For the full reference (all commands, troubleshooting, patterns), see `docs/guide_for_users_and_agents.md`.

## Install (uv)

```bash
uv sync
```

Optional dev tools:

```bash
uv sync --group dev
```

## Configure

Create the config file:

```bash
mkdir -p ~/.rpod
echo 'apikey = "rp_your_api_key_here"' > ~/.rpod/config.toml
echo 'ssh_key = "~/.ssh/id_ed25519"' >> ~/.rpod/config.toml
```

## Quick Start

```bash
# Create a pod and bootstrap tools/deps
uv run rpod create dev --gpu "NVIDIA RTX 4000 Ada Generation" --bootstrap

# Run a command in a tmux session
uv run rpod exec dev -t train "uv run python your_script.py"

# Check logs and status
uv run rpod logs dev train
uv run rpod status dev

# Stop billing when done
uv run rpod stop dev
```

## Core Commands

```bash
uv run rpod create <name> --gpu "..." [--bootstrap]
uv run rpod exec <name> "command"
uv run rpod push <name>
uv run rpod pull <name> /remote/path --local ./path
uv run rpod status <name>
uv run rpod logs <name> <session>
uv run rpod stop <name>
uv run rpod terminate <name>
```

## Configuration Notes

- Global config: `~/.rpod/config.toml` (flat key/value only).
- Project config: `.rpod.yaml` supports workdir, defaults, logging, excludes, and setup datasets.
- Workspace default: `/workspace/<current-directory-name>` unless overridden.

## Logging

Enable with `log_level` in `~/.rpod/config.toml` or `.rpod.yaml`. Logs are written to:

````
~/.rpod/logs/rpod.log
````

## File Layout

````
src/rpod/
├── cli.py
├── config.py
├── project_config.py
├── api.py
├── registry.py
├── ssh.py
├── logging.py
└── commands/
````
