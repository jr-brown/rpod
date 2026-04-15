"""Cleanup commands: rpod clean."""

import sys
from typing import Optional

from rpod.project_config import load_project_config
from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection


# Cleanup target definitions
# Note: xargs -r means "don't run if input is empty" (GNU extension)
CLEAN_TARGETS = {
    "tmp": {
        "description": "Clear /tmp/*",
        "command": "rm -rf /tmp/* 2>/dev/null; echo clean_exit_$?",
        "size_cmd": "du -sh /tmp 2>/dev/null | cut -f1 || echo '0'",
    },
    "checkpoints": {
        "description": "Remove checkpoint-* directories",
        "command": "find /workspace -type d -name 'checkpoint-*' -exec rm -rf {} + 2>/dev/null; echo clean_exit_$?",
        "size_cmd": "find /workspace -type d -name 'checkpoint-*' -print0 2>/dev/null | xargs -0 -r du -shc 2>/dev/null | tail -1 | cut -f1 || echo '0'",
    },
    "pycache": {
        "description": "Remove __pycache__ and *.pyc",
        "command": "find /workspace -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null; find /workspace -name '*.pyc' -delete 2>/dev/null; echo clean_exit_$?",
        "size_cmd": "find /workspace -type d -name '__pycache__' -print0 2>/dev/null | xargs -0 -r du -shc 2>/dev/null | tail -1 | cut -f1 || echo '0'",
    },
    "logs": {
        "description": "Remove *.log files from /workspace",
        "command": "find /workspace -name '*.log' -delete 2>/dev/null; echo clean_exit_$?",
        "size_cmd": "find /workspace -name '*.log' -print0 2>/dev/null | xargs -0 -r du -shc 2>/dev/null | tail -1 | cut -f1 || echo '0'",
    },
}


def _format_size(size_str: str) -> str:
    """Clean up size string for display."""
    return size_str.strip() if size_str.strip() else "0"


def cmd_clean(
    name: str,
    targets: Optional[list[str]] = None,
    dry_run: bool = False,
) -> int:
    """Clean up space-wasting files on a pod.

    Targets:
    - tmp: Clear /tmp/*
    - checkpoints: Remove checkpoint-* directories
    - pycache: Remove __pycache__ and *.pyc
    - logs: Remove *.log files
    - all: All of the above

    Args:
        name: Pod name
        targets: List of targets to clean. If empty, uses defaults from .rpod.yaml
                 or falls back to ["tmp"]
        dry_run: If True, show what would be cleaned without actually cleaning
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    # Determine targets
    if not targets:
        project_config = load_project_config()
        targets = project_config.clean_targets or ["tmp"]

    # Expand 'all' target
    if "all" in targets:
        targets = list(CLEAN_TARGETS.keys())

    # Validate targets
    invalid = [t for t in targets if t not in CLEAN_TARGETS]
    if invalid:
        print(f"Error: Unknown clean target(s): {', '.join(invalid)}", file=sys.stderr)
        print(f"Valid targets: {', '.join(CLEAN_TARGETS.keys())}, all", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod, timeout=120)

    # Get disk usage before
    disk_before = ssh.run("df /workspace 2>/dev/null | tail -1 | awk '{print $4}'")
    free_before = int(disk_before.stdout.strip() or 0) if disk_before.success else 0

    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Cleaning pod '{name}'...")
    print()

    for target in targets:
        info = CLEAN_TARGETS[target]
        print(f"=== {target}: {info['description']} ===")

        # Show size before
        size_result = ssh.run(info["size_cmd"])
        size = _format_size(size_result.stdout) if size_result.success else "?"
        print(f"  Size: {size}")

        if not dry_run:
            result = ssh.run(info["command"])
            if result.success and "clean_exit_0" in result.stdout:
                print(f"  Cleaned")
            elif result.success:
                print(f"  Warning: some files may not have been deleted (permission denied?)", file=sys.stderr)
            else:
                print(f"  Warning: {result.stderr}", file=sys.stderr)

        print()

    # Get disk usage after (only if not dry run)
    if not dry_run:
        disk_after = ssh.run("df /workspace 2>/dev/null | tail -1 | awk '{print $4}'")
        free_after = int(disk_after.stdout.strip() or 0) if disk_after.success else 0

        freed = free_after - free_before
        if freed > 0:
            # Convert KB to human readable
            if freed > 1024 * 1024:
                freed_str = f"{freed / (1024 * 1024):.1f} GB"
            elif freed > 1024:
                freed_str = f"{freed / 1024:.1f} MB"
            else:
                freed_str = f"{freed} KB"
            print(f"Freed approximately {freed_str}")
        else:
            print("No significant space freed")

    return 0
