"""Pod registry commands: list, register, remove, connect."""

import json
import sys
from typing import Optional

from rpod.api import RunPodAPI, RunPodAPIError
from rpod.config import load_config
from rpod.registry import PodRegistry
from rpod.ssh import SSHConnection


def cmd_list(as_json: bool = False, refresh: bool = False) -> int:
    """List registered pods."""
    registry = PodRegistry()
    pods = registry.list()

    if not pods:
        if as_json:
            print("[]")
        else:
            print("No pods registered.")
            print("Use 'rpod register' to add an existing pod")
            print("Or 'rpod create' to create a new one")
        return 0

    # Optionally refresh status from API
    api_statuses = {}
    if refresh:
        try:
            config = load_config()
            api = RunPodAPI(config.api_key, timeout=config.api_timeout)
            for status in api.list_pods():
                api_statuses[status.pod_id] = status
        except Exception as e:
            print(f"Warning: Could not refresh from API: {e}", file=sys.stderr)

    if as_json:
        data = []
        for pod in pods:
            pod_data = {
                "name": pod.name,
                "pod_id": pod.pod_id,
                "ip": pod.ip,
                "port": pod.port,
                "workspace": pod.workspace,
                "gpu_type": pod.gpu_type,
                "status": pod.status,
            }
            # Update with API status if available
            if pod.pod_id and pod.pod_id in api_statuses:
                api_status = api_statuses[pod.pod_id]
                pod_data["status"] = api_status.status
                registry.update(pod.name, status=api_status.status)
            data.append(pod_data)
        print(json.dumps(data, indent=2))
        return 0

    # Table output
    print(f"{'NAME':<12} {'STATUS':<10} {'GPU':<25} {'IP':<18} {'PORT':<6} {'WORKSPACE'}")
    print("-" * 90)

    for pod in pods:
        status = pod.status
        # Update with API status if available
        if pod.pod_id and pod.pod_id in api_statuses:
            api_status = api_statuses[pod.pod_id]
            status = api_status.status
            registry.update(pod.name, status=status)

        ip = pod.ip or "-"
        gpu = (pod.gpu_type or "-")[:25]
        workspace = pod.workspace or "-"

        # Color status
        if status == "RUNNING":
            status_str = f"\033[32m{status:<10}\033[0m"  # Green
        elif status == "STOPPED":
            status_str = f"\033[33m{status:<10}\033[0m"  # Yellow
        else:
            status_str = f"{status:<10}"

        print(f"{pod.name:<12} {status_str} {gpu:<25} {ip:<18} {pod.port:<6} {workspace}")

    return 0


def cmd_register(
    name: str,
    ip: str,
    port: int,
    workspace: str,
    pod_id: Optional[str] = None,
) -> int:
    """Manually register an existing pod."""
    config = load_config()
    registry = PodRegistry()

    # Check if name already exists
    existing = registry.get(name)
    if existing:
        print(f"Warning: Overwriting existing pod '{name}'", file=sys.stderr)

    pod = registry.register(
        name=name,
        ip=ip,
        port=port,
        pod_id=pod_id,
        workspace=workspace,
        key_path=str(config.ssh_key),
        status="RUNNING",
    )

    print(f"Registered pod '{name}'")
    print(f"  IP: {ip}")
    print(f"  Port: {port}")
    print(f"  Workspace: {workspace}")
    if pod_id:
        print(f"  Pod ID: {pod_id}")

    # Test connection
    print("Testing SSH connection...")
    ssh = SSHConnection(pod)
    if ssh.test_connection():
        print("SSH connection successful!")
    else:
        print("Warning: SSH connection test failed", file=sys.stderr)
        print("Check IP, port, and SSH key", file=sys.stderr)

    return 0


def cmd_remove(names: list[str], force: bool = False) -> int:
    """Remove one or more pods from the local registry (does NOT stop or terminate them)."""
    registry = PodRegistry()

    # Validate all names exist first
    valid_names: list[str] = []
    for name in names:
        if not registry.get(name):
            print(f"✗ {name} - not found in registry", file=sys.stderr)
        else:
            valid_names.append(name)

    if not valid_names:
        return 1

    # Confirmation prompt
    if not force:
        print("This will remove the following pod(s) from your LOCAL REGISTRY ONLY:")
        for name in valid_names:
            print(f"  - {name}")
        print("\nThe pod(s) will NOT be stopped or terminated — they will continue")
        print("running (and billing) on RunPod. Use 'rpod terminate' to destroy pods.")
        response = input("Type 'yes' to confirm: ")
        if response.lower() != "yes":
            print("Aborted")
            return 1

    results: list[tuple[str, bool, str]] = []  # (name, success, message)

    for name in valid_names:
        registry.remove(name)
        results.append((name, True, "removed"))

    for name, success, message in results:
        print(f"✓ {name} - {message}")

    return 0


def cmd_connect(name: str) -> int:
    """Start an interactive SSH session."""
    registry = PodRegistry()

    pod = registry.get(name)
    if not pod:
        print(f"Error: Pod '{name}' not found in registry", file=sys.stderr)
        return 1

    if not pod.ip:
        print(f"Error: Pod '{name}' has no IP address", file=sys.stderr)
        print("It may be stopped - try 'rpod start' first", file=sys.stderr)
        return 1

    ssh = SSHConnection(pod)
    return ssh.interactive()
