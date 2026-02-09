"""Sync commands: push, pull."""

import sys
import time
from pathlib import Path
from typing import Optional

from rpod.project_config import load_project_config
from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection

def get_excludes(
    cli_excludes: Optional[list[str]] = None,
    config_excludes: Optional[list[str]] = None,
) -> list[str]:
    """Get list of excludes from config with optional CLI additions.

    Base excludes come from .rpod.yaml push_excludes.
    CLI --exclude patterns are added on top.
    """
    excludes = list(config_excludes) if config_excludes else []

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
) -> int:
    """Push local directory to pod.

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
    if not local.exists():
        print(f"Error: Local path does not exist: {local}", file=sys.stderr)
        return 1

    remote = remote_path or pod.workspace
    if not remote:
        print("Error: No remote path specified and pod has no workspace", file=sys.stderr)
        return 1

    # Get final excludes list (with config excludes)
    project_config = load_project_config()
    final_excludes = get_excludes(excludes, project_config.push_excludes)

    ssh = SSHConnection(pod)

    print(f"Pushing {local} -> {pod.name}:{remote}")
    print(f"Excludes: {', '.join(final_excludes)}")

    t0 = time.time()
    result = ssh.rsync_push(local, remote, excludes=final_excludes, timeout=timeout)
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
