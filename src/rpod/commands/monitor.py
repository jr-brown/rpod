"""Monitoring commands: status, jobs, logs, kill-session."""

import subprocess
import sys

from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection


def cmd_status(name: str, include_storage: bool = False) -> int:
    """Show pod status: GPU, disk, and top processes."""
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod, timeout=30)

    # Get GPU info
    print("=== GPU Status ===")
    gpu_result = ssh.run("nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits 2>/dev/null || echo 'No GPU'")
    if gpu_result.success:
        if "No GPU" in gpu_result.stdout:
            print("No NVIDIA GPU detected")
        else:
            for line in gpu_result.stdout.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 4:
                    name_gpu, mem_used, mem_total, util = parts
                    print(f"  {name_gpu}")
                    print(f"    Memory: {mem_used}MB / {mem_total}MB")
                    print(f"    Utilization: {util}%")
                else:
                    print(f"  {line}")
    else:
        print(f"  Error: {gpu_result.stderr}")

    print()

    # Get disk info
    print("=== Disk Usage ===")
    disk_result = ssh.run("df -h /workspace 2>/dev/null || df -h /")
    if disk_result.success:
        lines = disk_result.stdout.strip().split("\n")
        if len(lines) >= 2:
            # Parse df output
            data = lines[1]
            # Extract usage percentage for warning
            parts = data.split()
            usage_pct = None
            for part in parts:
                if part.endswith("%"):
                    try:
                        usage_pct = int(part.rstrip("%"))
                    except ValueError:
                        pass
                    break
            # Add warning indicator
            warning = ""
            if usage_pct is not None:
                if usage_pct >= 95:
                    warning = " [CRITICAL]"
                elif usage_pct >= 85:
                    warning = " [Warning]"
            print(f"  {data}{warning}")
    else:
        print(f"  Error: {disk_result.stderr}")

    print()

    if include_storage:
        # Storage breakdown - focus on this project's workspace and HF cache
        # Use timeout to avoid hanging on large/shared filesystems.
        print("=== Storage Breakdown ===")
        paths_to_check = [
            ("/workspace/.cache/huggingface", "huggingface/"),
            (f"{pod.workspace}/.venv", ".venv/"),
            (f"{pod.workspace}/local", "local/"),
            (f"{pod.workspace}/logs", "logs/"),
            (f"{pod.workspace}", "workspace/"),
        ]
        found_any = False
        for path, label in paths_to_check:
            size_result = ssh.run(
                f"timeout 5s du -sh {path} 2>/dev/null | cut -f1 || true"
            )
            if size_result.success and size_result.stdout.strip():
                print(f"  {size_result.stdout.strip()}\t{label}")
                found_any = True
        if not found_any:
            print("  (no data)")

        print()
    else:
        print("=== Storage Breakdown ===")
        print("  (skipped; use --storage to enable)")
        print()

    # Get top processes
    print("=== Top Processes (by CPU) ===")
    proc_result = ssh.run("ps aux --sort=-%cpu | head -6")
    if proc_result.success:
        for line in proc_result.stdout.strip().split("\n"):
            # Truncate long lines
            if len(line) > 120:
                line = line[:117] + "..."
            print(f"  {line}")
    else:
        print(f"  Error: {proc_result.stderr}")

    print()

    # Active tmux sessions with process info
    print("=== Tmux Sessions ===")
    tmux_result = ssh.run(
        "tmux list-sessions -F '#{session_name}' 2>/dev/null | while read s; do "
        "  pid=$(tmux list-panes -t \"$s\" -F '#{pane_pid}' | head -1); "
        "  proc=$(ps -o args= -p $pid 2>/dev/null | head -c 80); "
        "  elapsed=$(ps -o etime= -p $pid 2>/dev/null | xargs); "
        '  echo "$s: $proc ($elapsed)"; '
        "done"
    )
    if tmux_result.success and tmux_result.stdout.strip():
        for line in tmux_result.stdout.strip().split("\n"):
            print(f"  {line}")
    else:
        print("  (no sessions)")

    return 0


def cmd_jobs(name: str) -> int:
    """List tmux sessions on the pod."""
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod)

    result = ssh.run("tmux list-sessions 2>/dev/null || echo 'No tmux sessions'")

    if "No tmux sessions" in result.stdout or "no server running" in result.stderr:
        print("No tmux sessions running")
        return 0

    if result.success:
        print("Tmux sessions:")
        for line in result.stdout.strip().split("\n"):
            print(f"  {line}")
        print()
        print(f"View logs: rpod logs {name} <session>")
        print(f"Attach: rpod connect {name}, then: tmux attach -t <session>")
    else:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return 1

    return 0


def cmd_logs(
    name: str,
    session: str,
    follow: bool = False,
    lines: int = 50,
) -> int:
    """View tmux session logs.

    Captures the current pane content.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod)

    # Check if session exists
    check_result = ssh.run(f"tmux has-session -t {session} 2>/dev/null && echo exists")
    if "exists" not in check_result.stdout:
        print(f"Error: Tmux session '{session}' not found", file=sys.stderr)
        # List available sessions to help the user
        list_result = ssh.run("tmux list-sessions -F '#{session_name}' 2>/dev/null")
        if list_result.success and list_result.stdout.strip():
            sessions = list_result.stdout.strip().split("\n")
            print(f"Available sessions: {', '.join(sessions)}", file=sys.stderr)
        else:
            print("No tmux sessions are running", file=sys.stderr)
        return 1

    if follow:
        # For follow mode, we use an interactive approach
        print(f"Following tmux session '{session}' (Ctrl+C to stop)...")
        print("-" * 60)

        # Build SSH command to tail the pane
        from pathlib import Path
        key = Path(pod.key_path).expanduser()
        ssh_opts = [
            "-i", str(key),
            "-p", str(pod.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=10",
        ]

        # Use a loop to repeatedly capture pane content, exit if session dies
        cmd = ["ssh"] + ssh_opts + [
            pod.ssh_host,
            f"while tmux has-session -t {session} 2>/dev/null; do "
            f"tmux capture-pane -t {session} -p | tail -20; sleep 2; done; "
            f"echo 'Session ended'"
        ]

        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\nStopped following")
        return 0

    else:
        # Capture pane content
        result = ssh.run(f"tmux capture-pane -t {session} -p -S -{lines}")

        if result.success:
            # Filter empty lines at the start
            content = result.stdout.rstrip()
            if content:
                print(content)
            else:
                print("(pane is empty)")
        else:
            print(f"Error: {result.stderr}", file=sys.stderr)
            return 1

    return 0


def cmd_doctor(name: str) -> int:
    """Run diagnostics on a pod.

    Checks SSH connectivity, installed tools, disk space, environment, etc.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    print(f"Running diagnostics on pod '{name}'...\n")

    all_ok = True

    env_preamble = f". {pod.workspace}/.rpod-env.sh 2>/dev/null; "

    def check(
        label: str,
        cmd: str,
        success_pattern: str = "ok",
        use_env: bool = False,
    ) -> bool:
        """Run a check and print result."""
        nonlocal all_ok
        full_cmd = f"{env_preamble}{cmd}" if use_env else cmd
        result = ssh.run(full_cmd, timeout=15)
        ok = result.success and success_pattern in result.stdout
        status = "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
        print(f"  {status} {label}")
        if not ok:
            all_ok = False
        return ok

    ssh = SSHConnection(pod, timeout=15)

    # SSH connectivity
    result = ssh.run("echo ok", timeout=10)
    if result.success and "ok" in result.stdout:
        print("  \033[32m✓\033[0m SSH connection")
    else:
        print("  \033[31m✗\033[0m SSH connection")
        print(f"    Error: {result.stderr}")
        return 1  # Can't continue without SSH

    # Tools
    check("tmux installed", "command -v tmux >/dev/null && echo ok", use_env=True)
    check("rsync installed", "command -v rsync >/dev/null && echo ok", use_env=True)
    check("uv installed", "command -v uv >/dev/null && echo ok", use_env=True)
    check("git installed", "command -v git >/dev/null && echo ok", use_env=True)

    # Workdir
    target = pod.workdir or pod.workspace
    if target:
        if check(f"workdir exists: {target}", f"test -d {target} && echo ok"):
            # Check for pyproject.toml
            check("pyproject.toml found", f"test -f {target}/pyproject.toml && echo ok")

    # Disk space
    disk_result = ssh.run("df /workspace 2>/dev/null | tail -1 | awk '{print $5, $4}'", timeout=10)
    if disk_result.success and disk_result.stdout.strip():
        parts = disk_result.stdout.strip().split()
        if len(parts) >= 2:
            usage_pct = parts[0].rstrip('%')
            free_kb = parts[1]
            try:
                free_gb = int(free_kb) / (1024 * 1024)
                pct = int(usage_pct)
                if pct >= 95:
                    print(f"  \033[31m✗\033[0m Disk space: {usage_pct}% used ({free_gb:.1f}GB free) [CRITICAL]")
                    all_ok = False
                elif pct >= 85:
                    print(f"  \033[33m!\033[0m Disk space: {usage_pct}% used ({free_gb:.1f}GB free) [Warning]")
                else:
                    print(f"  \033[32m✓\033[0m Disk space: {usage_pct}% used ({free_gb:.1f}GB free)")
            except ValueError:
                print(f"  \033[33m?\033[0m Disk space: could not parse")

    # Environment
    check("HF_HOME set", "test -n \"$HF_HOME\" && echo ok", use_env=True)
    if check("HF_TOKEN set", "test -n \"$HF_TOKEN\" && echo ok", use_env=True):
        pass
    else:
        print("  \033[33m!\033[0m HF_TOKEN not set (needed for gated models)")

    # .rpod-env.sh exists
    if target:
        check(".rpod-env.sh exists", f"test -f {target}/.rpod-env.sh && echo ok")

    # GPU
    gpu_result = ssh.run("nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1", timeout=10)
    if gpu_result.success and gpu_result.stdout.strip():
        print(f"  \033[32m✓\033[0m GPU: {gpu_result.stdout.strip()}")
    else:
        print("  \033[33m!\033[0m GPU: not detected or nvidia-smi failed")

    print()
    if all_ok:
        print("All checks passed!")
        return 0
    else:
        print("Some checks failed. Run 'rpod setup' to fix common issues.")
        return 1


def cmd_setup_log(
    name: str,
    lines: int = 200,
    follow: bool = False,
) -> int:
    """Show the most recent setup log for a pod."""
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod)

    latest_result = ssh.run(
        f"ls -t {pod.log_dir}/setup_*.log 2>/dev/null | head -1"
    )
    latest = latest_result.stdout.strip()
    if not latest:
        print(f"No setup logs found in {pod.log_dir}", file=sys.stderr)
        return 1

    if follow:
        print(f"Following setup log: {latest} (Ctrl+C to stop)")
        result = ssh.run(f"tail -n {lines} -f {latest}", capture=False)
        return 0 if result.success else result.returncode

    result = ssh.run(f"tail -n {lines} {latest}")
    if result.success:
        print(result.stdout, end="")
        return 0

    print(f"Error: {result.stderr}", file=sys.stderr)
    return result.returncode


def cmd_kill_session(name: str, session: str) -> int:
    """Kill a tmux session on the pod.

    Useful for stopping runaway processes without connecting interactively.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod)

    # Check if session exists
    check_result = ssh.run(f"tmux has-session -t {session} 2>/dev/null && echo exists")
    if "exists" not in check_result.stdout:
        print(f"Error: Tmux session '{session}' not found", file=sys.stderr)
        # List available sessions to help the user
        list_result = ssh.run("tmux list-sessions -F '#{session_name}' 2>/dev/null")
        if list_result.success and list_result.stdout.strip():
            sessions = list_result.stdout.strip().split("\n")
            print(f"Available sessions: {', '.join(sessions)}", file=sys.stderr)
        else:
            print("No tmux sessions are running", file=sys.stderr)
        return 1

    # Kill the session
    result = ssh.run(f"tmux kill-session -t {session}")
    if result.success:
        print(f"Killed tmux session '{session}' on {name}")
        return 0
    else:
        print(f"Error killing session: {result.stderr}", file=sys.stderr)
        return 1
