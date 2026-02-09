"""Environment variable commands: push secrets to pod, list what's set."""

import sys
from pathlib import Path

from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection


def cmd_env_push(
    name: str,
    env_file: str = ".env",
) -> int:
    """Push a local .env file to /workspace/.env on the pod.

    The file is transferred via SCP (encrypted in transit).
    Environment variables are automatically sourced in all sessions
    if `rpod setup` has been run (via /etc/profile.d/rpod-env.sh).

    WARNING: Secrets are stored in plaintext on the pod's persistent volume.
    """
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    local_env = Path(env_file).resolve()
    if not local_env.exists():
        print(f"Error: File not found: {local_env}", file=sys.stderr)
        return 1

    # Read the file content and push via SSH to avoid scp port flag differences
    content = local_env.read_text()
    ssh = SSHConnection(pod, timeout=30)
    result = ssh.run(
        f"cat > /workspace/.env << 'RPOD_ENV_EOF'\n{content}\nRPOD_ENV_EOF"
    )

    if result.success:
        # Count variables for feedback
        var_count = sum(
            1 for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        print(f"Pushed {var_count} variable(s) from {env_file} to {name}:/workspace/.env")
        print("Variables will be sourced automatically in new sessions (requires rpod setup).")
        return 0
    else:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return 1


def cmd_env_list(name: str) -> int:
    """List environment variables on the pod with values masked."""
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod, timeout=30)
    result = ssh.run("cat /workspace/.env 2>/dev/null || echo '__RPOD_NO_ENV__'")

    if not result.success:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return 1

    if "__RPOD_NO_ENV__" in result.stdout:
        print(f"No .env file found on {name}:/workspace/.env")
        print(f"Use 'rpod env push {name}' to upload one.")
        return 0

    print(f"Environment variables on {name}:/workspace/.env:")
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
            # Mask value: show first 3 and last 3 chars if long enough
            value = value.strip().strip("'\"")
            if len(value) > 8:
                masked = value[:3] + "***" + value[-3:]
            else:
                masked = "***"
            print(f"  {key}={masked}")
        else:
            print(f"  {stripped}")

    return 0
