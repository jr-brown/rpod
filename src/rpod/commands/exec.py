"""Execution commands: exec (with tmux support)."""

import sys
import time
from datetime import datetime
from typing import Optional

from rpod.registry import PodInfo, PodRegistry
from rpod.ssh import SSHConnection


def _env_preamble(pod: PodInfo) -> str:
    """Source the workspace env file and cd to workdir.

    Priority for working directory:
    1. pod.workdir (from .rpod.yaml at creation time)
    2. pod.workspace (e.g., /workspace/project-name)
    """
    target = pod.workdir or pod.workspace
    return f"cd {target} 2>/dev/null; . {pod.workspace}/.rpod-env.sh 2>/dev/null; "


def _validate_exec_prereqs(ssh: "SSHConnection", pod: PodInfo, use_tmux: bool) -> Optional[str]:
    """Validate prerequisites for exec.

    Returns an error message if validation fails, None if OK.
    """
    # Check workdir exists
    target = pod.workdir or pod.workspace
    if target:
        result = ssh.run(f"test -d {target} && echo ok", timeout=10)
        if "ok" not in result.stdout:
            return f"workdir does not exist on pod: {target}\nRun 'rpod push {pod.name}' first, or check .rpod.yaml workdir setting."

    # Check tmux is installed if needed
    if use_tmux:
        result = ssh.run("command -v tmux >/dev/null && echo ok", timeout=10)
        if "ok" not in result.stdout:
            return f"tmux is not installed on pod.\nRun 'rpod setup {pod.name}' to install it."

    return None


def cmd_exec(
    name: str,
    command: str,
    tmux_session: Optional[str] = None,
    log_file: Optional[str] = None,
    gpu: Optional[str] = None,
) -> int:
    """Execute a command on a pod.

    If tmux_session is specified, runs the command in a tmux session.
    If log_file is specified, pipes output to that file.
    If gpu is specified, prepends CUDA_VISIBLE_DEVICES to the command.

    Automatically sources the workspace env file (.rpod-env.sh) before
    running the command, so PATH, HF_HOME, HF_TOKEN, and PYTHONUNBUFFERED
    are available without manual sourcing.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod, timeout=600)  # 10 minute timeout for commands

    # Validate prerequisites
    error = _validate_exec_prereqs(ssh, pod, use_tmux=tmux_session is not None)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    # Prepend CUDA_VISIBLE_DEVICES if --gpu specified
    if gpu is not None:
        command = f"CUDA_VISIBLE_DEVICES={gpu} {command}"

    if tmux_session:
        return _exec_tmux(ssh, command, tmux_session, log_file, pod.auto_log)
    else:
        return _exec_simple(ssh, command)


def _exec_simple(ssh: SSHConnection, command: str) -> int:
    """Execute a simple command and print output."""
    full_cmd = f"{_env_preamble(ssh.pod)}{command}"
    result = ssh.run(full_cmd, timeout=600)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    return 0 if result.success else result.returncode


def _exec_tmux(
    ssh: SSHConnection,
    command: str,
    session: str,
    log_file: Optional[str] = None,
    auto_log: bool = False,
) -> int:
    """Execute command in a tmux session.

    Creates the session if it doesn't exist.
    If log_file is specified, pipes output to that file.
    If auto_log is True and no log_file is specified, auto-generate a log file.
    """
    pod = ssh.pod

    # Auto-generate log file if auto_log is enabled and no explicit log file
    if auto_log and log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = f"{pod.log_dir}/{session}_{timestamp}.log"
        # Ensure log directory exists
        ssh.run(f"mkdir -p {pod.log_dir}")

    env_source = _env_preamble(ssh.pod).rstrip("; ")

    # Check if session exists
    check_result = ssh.run(f"tmux has-session -t {session} 2>/dev/null && echo exists")

    if "exists" in check_result.stdout:
        # Session exists - send command to it
        # Use script-based approach to avoid shell quoting issues
        print(f"Sending command to existing tmux session '{session}'...")

        script_path = f"/tmp/rpod-tmux-{session}-cmd.sh"
        if log_file:
            script_content = f"#!/bin/bash\n{env_source}\n{command} 2>&1 | tee -a {log_file}"
        else:
            script_content = f"#!/bin/bash\n{env_source}\n{command}"

        write_result = ssh.run(
            f"cat > {script_path} << 'RPOD_SCRIPT_EOF'\n{script_content}\nRPOD_SCRIPT_EOF\n"
            f"chmod +x {script_path}"
        )
        if not write_result.success:
            print(f"Error writing script: {write_result.stderr}", file=sys.stderr)
            return 1

        tmux_cmd = f"tmux send-keys -t {session} 'bash {script_path}' Enter"
        result = ssh.run(tmux_cmd)
        if not result.success:
            print(f"Error: {result.stderr}", file=sys.stderr)
            return 1

        print(f"Command sent to session '{session}'")
        print(f"View output: rpod logs {ssh.pod.name} {session}")
    else:
        # Create new session with command
        # Write command to a temp script on the pod to avoid quoting issues.
        # The script sources env, runs the command, then drops into bash so the session persists.
        print(f"Creating tmux session '{session}'...")

        script_path = f"/tmp/rpod-tmux-{session}.sh"
        if log_file:
            script_content = f"#!/bin/bash\n{env_source}\n{command} 2>&1 | tee {log_file}\nexec bash"
        else:
            script_content = f"#!/bin/bash\n{env_source}\n{command}\nexec bash"

        write_result = ssh.run(
            f"cat > {script_path} << 'RPOD_SCRIPT_EOF'\n{script_content}\nRPOD_SCRIPT_EOF\n"
            f"chmod +x {script_path}"
        )
        if not write_result.success:
            print(f"Error writing script: {write_result.stderr}", file=sys.stderr)
            return 1

        tmux_cmd = f"tmux new-session -d -s {session} 'bash {script_path}'"
        result = ssh.run(tmux_cmd)
        if not result.success:
            print(f"Error creating session: {result.stderr}", file=sys.stderr)
            return 1

        # Verify session was created
        verify = ssh.run(f"tmux has-session -t {session} 2>/dev/null && echo ok")
        if "ok" not in verify.stdout:
            print(f"Error: session '{session}' failed to start", file=sys.stderr)
            return 1

        print(f"Started tmux session '{session}'")

        # Wait briefly and check if session died (catches immediate failures)
        time.sleep(2)
        recheck = ssh.run(f"tmux has-session -t {session} 2>/dev/null && echo ok")
        if "ok" not in recheck.stdout:
            print(f"\nWarning: session '{session}' died within 2 seconds!", file=sys.stderr)
            print("Possible causes: missing dependencies, disk full, or command error.", file=sys.stderr)
            if log_file:
                print(f"Check log file: {log_file}", file=sys.stderr)
            elif auto_log:
                print(f"Check logs in: {pod.log_dir}/", file=sys.stderr)
            else:
                print("Tip: Enable 'auto_log: true' in .rpod.yaml to capture output.", file=sys.stderr)
            return 1
        print(f"View output: rpod logs {ssh.pod.name} {session}")
        if log_file:
            print(f"Log file: {log_file}")

    return 0
