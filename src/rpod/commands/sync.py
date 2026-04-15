"""Sync commands: push, pull."""

import sys
import time
from pathlib import Path
from typing import Optional

from rpod.project_config import load_project_config
from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection

# Base excludes always applied (unless --purge is used)
BASE_EXCLUDES = [".venv", ".git", "__pycache__", "*.pyc", ".env", "local"]


def get_excludes(
    cli_excludes: Optional[list[str]] = None,
    config_excludes: Optional[list[str]] = None,
    include_base: bool = True,
) -> list[str]:
    """Get list of excludes by merging base, config, and CLI excludes.

    Base excludes are always included unless include_base is False (--purge).
    Config excludes from .rpod.yaml push_excludes are added on top.
    CLI --exclude patterns are added on top of that.
    """
    excludes: list[str] = []

    if include_base:
        excludes.extend(BASE_EXCLUDES)

    if config_excludes:
        for exc in config_excludes:
            if exc and exc not in excludes:
                excludes.append(exc)

    if cli_excludes:
        for exc in cli_excludes:
            if exc and exc not in excludes:
                excludes.append(exc)

    return excludes


def cmd_push(
    name: str,
    local_path: str,
    remote_path: Optional[str] = None,
    excludes: Optional[list[str]] = None,
    timeout: int = 300,
    clean: bool = False,
    purge: bool = False,
    dry_run: bool = False,
) -> int:
    """Push local directory to pod.

    Uses rsync over SSH for efficient incremental transfer.

    By default, rsync does NOT delete remote files that don't exist locally.
    Use --clean to enable deletion (with base excludes still protecting common
    output directories like local/). Use --purge to delete with no base excludes.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    local = Path(local_path).resolve()
    if not local.exists():
        print(f"Error: Local path does not exist: {local}", file=sys.stderr)
        return 1

    remote = remote_path or pod.workspace
    if not remote:
        print("Error: No remote path specified and pod has no workspace", file=sys.stderr)
        return 1

    # Get final excludes list (with config excludes)
    project_config = load_project_config()
    include_base = not purge
    final_excludes = get_excludes(excludes, project_config.push_excludes, include_base=include_base)

    delete = clean or purge

    ssh = SSHConnection(pod)

    mode = ""
    if dry_run:
        mode = "[DRY RUN] "
    if purge:
        mode += "[PURGE] "
    elif clean:
        mode += "[CLEAN] "

    print(f"{mode}Pushing {local} -> {pod.name}:{remote}")
    if final_excludes:
        print(f"Excludes: {', '.join(final_excludes)}")
    if delete:
        print(f"Remote files not in local will be deleted")

    t0 = time.time()
    result = ssh.rsync_push(
        local, remote, excludes=final_excludes, timeout=timeout,
        delete=delete, dry_run=dry_run,
    )
    elapsed = time.time() - t0

    if result.success:
        print(f"Push complete! ({elapsed:.1f}s)")
        return 0
    else:
        print(f"Error after {elapsed:.1f}s: {result.stderr}", file=sys.stderr)
        return 1


def cmd_pull(
    name: str,
    remote_path: str,
    local_path: str,
    timeout: int = 300,
) -> int:
    """Pull directory from pod to local.

    Uses rsync over SSH for efficient incremental transfer.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    local = Path(local_path).resolve()

    ssh = SSHConnection(pod)

    print(f"Pulling {pod.name}:{remote_path} -> {local}")

    t0 = time.time()
    result = ssh.rsync_pull(remote_path, local, timeout=timeout)
    elapsed = time.time() - t0

    if result.success:
        print(f"Pull complete! ({elapsed:.1f}s)")
        return 0
    else:
        print(f"Error after {elapsed:.1f}s: {result.stderr}", file=sys.stderr)
        return 1
