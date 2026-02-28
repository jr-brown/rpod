"""Pod lifecycle commands: create, stop, start, terminate."""

import os
import sys
import time
from pathlib import Path
from typing import Optional

from rpod.api import RunPodAPI, RunPodAPIError
from rpod.config import load_config
from rpod.project_config import load_project_config
from rpod.registry import PodInfo, PodRegistry
from rpod.ssh import SSHConnection


def resolve_pod(
    name_or_id: str,
    registry: PodRegistry,
    api: RunPodAPI,
) -> tuple[str, Optional[str]]:
    """Resolve a name or pod ID to (pod_id, registry_name).

    Resolution order:
    1. Registry lookup by name
    2. Registry lookup by pod_id
    3. Treat as raw pod ID, verify via API

    Returns (pod_id, registry_name). registry_name is None if the pod
    is not in the registry.

    Raises ValueError if the pod cannot be found anywhere.
    """
    # 1. Try registry by name
    pod = registry.get(name_or_id)
    if pod:
        if not pod.pod_id:
            raise ValueError(
                f"Pod '{name_or_id}' has no API ID - it may have been manually registered without --pod-id"
            )
        return pod.pod_id, pod.name

    # 2. Try registry by pod_id
    pod = registry.find_by_pod_id(name_or_id)
    if pod:
        return pod.pod_id, pod.name

    # 3. Treat as raw pod ID, verify via API
    try:
        status = api.get_pod(name_or_id)
        return status.pod_id, None
    except RunPodAPIError:
        pass

    raise ValueError(
        f"'{name_or_id}' not found as registry name, registry pod ID, or RunPod API pod ID"
    )


def cmd_create(
    name: str,
    gpu_type: Optional[str],
    image: Optional[str],
    template_id: Optional[str],
    volume_size: Optional[int],
    workspace: Optional[str],
    gpu_count: int,
    container_disk: Optional[int] = None,
    bootstrap: bool = False,
    models: Optional[str] = None,
    setup_follow: bool = False,
    all_regions: bool = False,
    cpu: bool = False,
    cpu_type: Optional[str] = None,
) -> int:
    """Create a new pod via RunPod API.

    The volume is always mounted at /workspace. The workspace path defaults to
    /workspace/<current-directory-name> (e.g., /workspace/em-and-personas),
    which is the subdirectory where code is pushed and commands are run.

    Settings from .rpod.yaml are used as defaults when CLI arguments are not
    provided. The workdir, auto_log, and log_dir settings are stored in the
    registry for use by rpod exec.
    """
    config = load_config()
    project_config = load_project_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)
    registry = PodRegistry()

    # Validate --cpu and --gpu are mutually exclusive
    if cpu and gpu_type:
        print("Error: --cpu and --gpu are mutually exclusive", file=sys.stderr)
        return 1

    # CPU pod path: skip GPU requirement, use smaller defaults
    if cpu:
        if cpu_type is None:
            cpu_type = project_config.default_cpu_type
        if volume_size is None:
            volume_size = project_config.default_volume_size or 20
        if container_disk is None:
            container_disk = project_config.default_container_disk or 20
        if template_id is None:
            template_id = project_config.default_template_id
        if image is None:
            image = project_config.default_image or "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
    else:
        # GPU pod path: use project config defaults
        if gpu_type is None:
            gpu_type = project_config.default_gpu
        if gpu_type is None:
            print("Error: --gpu is required (no default_gpu in .rpod.yaml)", file=sys.stderr)
            return 1
        if volume_size is None:
            volume_size = project_config.default_volume_size or 50
        if container_disk is None:
            container_disk = project_config.default_container_disk or 100
        if template_id is None:
            template_id = project_config.default_template_id
        if image is None:
            image = project_config.default_image or "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"

    # Models: CLI overrides config
    if models is None and project_config.models:
        models = ",".join(project_config.models)

    # Resolve region whitelist to datacenter IDs
    datacenter_id: Optional[str] = None
    if not all_regions and project_config.region_whitelist:
        try:
            datacenter_id = api.resolve_regions(project_config.region_whitelist)
            print(f"Regions: {', '.join(project_config.region_whitelist)} (use --all-regions to bypass)")
        except RunPodAPIError as e:
            print(f"Error resolving regions: {e}", file=sys.stderr)
            return 1

    # Check if name already exists
    if registry.get(name):
        print(f"Error: Pod '{name}' already exists in registry", file=sys.stderr)
        print("Use 'rpod remove' first or choose a different name", file=sys.stderr)
        return 1

    # Volume always mounts at /workspace
    volume_mount = "/workspace"

    # Workspace = project subdirectory (e.g., /workspace/em-and-personas)
    if workspace is None:
        workspace = f"/workspace/{os.path.basename(os.getcwd())}"

    # Read SSH public key so RunPod can configure sshd
    pub_key_path = config.ssh_key.with_suffix(".pub")
    if not pub_key_path.exists():
        # Try appending .pub to the full path (handles keys without extension)
        pub_key_path = Path(str(config.ssh_key) + ".pub")
    if not pub_key_path.exists():
        print(f"Error: SSH public key not found at {pub_key_path}", file=sys.stderr)
        print("RunPod needs your public key to start sshd.", file=sys.stderr)
        print(f"Generate one with: ssh-keygen -t ed25519", file=sys.stderr)
        return 1
    ssh_public_key = pub_key_path.read_text().strip()

    pod_env: dict[str, str] = {"PUBLIC_KEY": ssh_public_key}

    if cpu:
        label = f"CPU{f' ({cpu_type})' if cpu_type else ''}"
        print(f"Creating CPU pod '{name}'...")
    else:
        label = gpu_type
        print(f"Creating pod '{name}' with {gpu_type}...")
    print(f"  Workspace: {workspace}")
    if template_id:
        print(f"  Template ID: {template_id}")
        print("  Note: Template must expose SSH on 22/tcp for rpod to connect.")

    try:
        if cpu:
            pod_id = api.create_cpu_pod(
                name=name,
                instance_id=cpu_type or "cpu3c-2-4",
                image=image,
                template_id=template_id,
                container_disk=container_disk,
                datacenter_id=datacenter_id,
                env=pod_env,
            )
        else:
            pod_id = api.create_pod(
                name=name,
                gpu_type=gpu_type,
                image=image,
                template_id=template_id,
                volume_size=volume_size,
                volume_mount=volume_mount,
                gpu_count=gpu_count,
                container_disk=container_disk,
                datacenter_id=datacenter_id,
                env=pod_env,
            )
    except RunPodAPIError as e:
        print(f"Error creating pod: {e}", file=sys.stderr)
        return 1

    print(f"Pod created with ID: {pod_id}")
    print("Waiting for pod to be ready (this can take up to 20 minutes on a cold start)...")

    # Wait for pod to be running and have SSH info
    max_wait = 600  # 10 minutes
    start_time = time.time()
    public_ip = None
    ssh_port = None
    actual_gpu = None
    while time.time() - start_time < max_wait:
        try:
            status = api.get_pod(pod_id)
        except RunPodAPIError as e:
            print(f"Warning: Failed to get pod status: {e}", file=sys.stderr)
            time.sleep(15)
            continue

        if status.status == "RUNNING" and status.public_ip and status.ssh_port:
            public_ip = status.public_ip
            ssh_port = status.ssh_port
            actual_gpu = status.gpu_type
            break

        elapsed = int(time.time() - start_time)
        print(f"  Status: {status.status} ({elapsed}s)")
        time.sleep(15)

    print()  # Clear the status line

    # For CPU pods, gpu_type is None in the registry
    registered_gpu_type = None if cpu else (actual_gpu or gpu_type)

    if not public_ip or not ssh_port:
        print("Error: Timed out waiting for pod to be ready", file=sys.stderr)
        print(f"Pod ID: {pod_id} - check RunPod dashboard", file=sys.stderr)
        # Still register it without connection info
        registry.register(
            name=name,
            ip="pending",
            port=22,
            pod_id=pod_id,
            workspace=workspace,
            key_path=str(config.ssh_key),
            gpu_type=registered_gpu_type,
            status="PENDING",
            workdir=project_config.workdir,
            auto_log=project_config.auto_log,
            log_dir=project_config.log_dir,
        )
        return 1

    # Register the pod
    pod = registry.register(
        name=name,
        ip=public_ip,
        port=ssh_port,
        pod_id=pod_id,
        workspace=workspace,
        key_path=str(config.ssh_key),
        gpu_type=registered_gpu_type,
        status="RUNNING",
        workdir=project_config.workdir,
        auto_log=project_config.auto_log,
        log_dir=project_config.log_dir,
    )

    print(f"Pod ready!")
    print(f"  IP: {public_ip}")
    print(f"  SSH Port: {ssh_port}")
    if cpu:
        print(f"  Type: CPU{f' ({cpu_type})' if cpu_type else ''}")
    else:
        print(f"  GPU: {actual_gpu or gpu_type}")

    # Test SSH connection (sshd may take a few seconds to start)
    print("Testing SSH connection...")
    ssh = SSHConnection(pod)
    ssh_ok = False
    for attempt in range(3):
        if ssh.test_connection():
            ssh_ok = True
            break
        if attempt < 2:
            time.sleep(5)
    if ssh_ok:
        print("SSH connection successful!")
    else:
        print("Warning: SSH connection test failed", file=sys.stderr)
        print("The pod was created but SSH is not reachable.", file=sys.stderr)
        print(f"Try manually: rpod connect {name}", file=sys.stderr)
        if bootstrap:
            print("Skipping bootstrap (requires SSH).", file=sys.stderr)
        return 1

    # Install rsync immediately (needed for push)
    print("Installing rsync...")
    ssh.run("apt-get update -qq && apt-get install -y -qq rsync > /dev/null 2>&1", timeout=60)

    # Run bootstrap if requested: push → env push → setup
    if bootstrap:
        from rpod.commands.sync import cmd_push

        print("\nPushing code...")
        cmd_push(name, ".")

        if Path(".env").exists():
            from rpod.commands.env import cmd_env_push

            print("Pushing environment...")
            cmd_env_push(name)

        from rpod.commands.setup import cmd_setup

        print("Running setup...")
        if setup_follow:
            print("Streaming setup output (use Ctrl+C to stop; setup continues on pod).")
        else:
            print(f"Setup running in background.")
            print(f"Monitor with: rpod setup-log {name}")
            print(f"Follow live:  rpod setup-log {name} -f")
        setup_rc = cmd_setup(name, models=models, follow=setup_follow)
        if setup_rc != 0:
            print("Warning: setup failed, but pod was created successfully", file=sys.stderr)

    print(f"\nConnect with: rpod connect {name}")
    return 0


def cmd_stop(names: list[str]) -> int:
    """Stop one or more running pods (preserves storage).

    Accepts registry names or RunPod pod IDs.
    """
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)
    registry = PodRegistry()

    # Resolve all pods first
    resolved: list[tuple[str, str, str | None]] = []  # (input_name, pod_id, reg_name)
    resolve_errors: list[tuple[str, str]] = []  # (input_name, error)

    for name_or_id in names:
        try:
            pod_id, reg_name = resolve_pod(name_or_id, registry, api)
            resolved.append((name_or_id, pod_id, reg_name))
        except ValueError as e:
            resolve_errors.append((name_or_id, str(e)))

    # Report resolution errors
    if resolve_errors:
        for name, error in resolve_errors:
            print(f"✗ {name} - {error}", file=sys.stderr)
        if not resolved:
            return 1

    # Stop each pod
    results: list[tuple[str, bool, str]] = []  # (label, success, message)

    if len(resolved) > 1:
        print(f"Stopping {len(resolved)} pods...")

    for input_name, pod_id, reg_name in resolved:
        label = reg_name or pod_id
        try:
            api.stop_pod(pod_id)
            if reg_name:
                registry.update(reg_name, status="STOPPED", ip=None, port=22)
            results.append((label, True, "stopped"))
        except RunPodAPIError as e:
            results.append((label, False, str(e)))

    # Report results
    success_count = sum(1 for _, success, _ in results if success)
    failure_count = len(results) - success_count + len(resolve_errors)

    for label, success, message in results:
        if success:
            print(f"✓ {label} - {message}")
        else:
            print(f"✗ {label} - {message}", file=sys.stderr)

    if success_count > 0:
        print(f"\nStorage preserved. Restart with: rpod start <name>")

    return 0 if failure_count == 0 else 1


def _start_single_pod(
    input_name: str,
    pod_id: str,
    reg_name: str | None,
    name_as: str | None,
    no_wait: bool,
    config,
    api: RunPodAPI,
    registry: PodRegistry,
) -> tuple[str, bool, str]:
    """Start a single pod. Returns (label, success, message)."""
    label = reg_name or pod_id

    # Use the right resume mutation for CPU vs GPU pods
    is_cpu = False
    if reg_name:
        pod_info = registry.get(reg_name)
        if pod_info and pod_info.is_cpu:
            is_cpu = True

    try:
        if is_cpu:
            api.start_cpu_pod(pod_id)
        else:
            api.start_pod(pod_id)
    except RunPodAPIError as e:
        return label, False, str(e)

    if no_wait:
        return label, True, "start requested"

    # Wait for new IP/port
    max_wait = 120
    start_time = time.time()

    while time.time() - start_time < max_wait:
        try:
            status = api.get_pod(pod_id)
        except RunPodAPIError:
            time.sleep(5)
            continue

        if status.status == "RUNNING" and status.public_ip and status.ssh_port:
            # Update or auto-register
            final_name = reg_name or name_as
            if final_name:
                if reg_name:
                    registry.update(
                        reg_name,
                        ip=status.public_ip,
                        port=status.ssh_port,
                        status="RUNNING",
                    )
                else:
                    registry.register(
                        name=final_name,
                        ip=status.public_ip,
                        port=status.ssh_port,
                        pod_id=pod_id,
                        key_path=str(config.ssh_key),
                        gpu_type=status.gpu_type,
                        status="RUNNING",
                    )
            return label, True, f"ready ({status.public_ip}:{status.ssh_port})"

        time.sleep(5)

    return label, False, "timed out waiting for SSH info"


def cmd_start(
    names: list[str],
    name_as: Optional[str] = None,
    no_wait: bool = False,
) -> int:
    """Start one or more stopped pods.

    Accepts registry names or RunPod pod IDs.
    When starting by pod ID, use --name-as to register it in the local registry
    (only valid with a single pod).
    Use --no-wait to return immediately without waiting for SSH info.
    """
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)
    registry = PodRegistry()

    # --name-as only makes sense with a single pod
    if name_as and len(names) > 1:
        print("Error: --name-as can only be used with a single pod", file=sys.stderr)
        return 1

    # Resolve all pods first
    resolved: list[tuple[str, str, str | None]] = []  # (input_name, pod_id, reg_name)
    resolve_errors: list[tuple[str, str]] = []  # (input_name, error)

    for name_or_id in names:
        try:
            pod_id, reg_name = resolve_pod(name_or_id, registry, api)
            resolved.append((name_or_id, pod_id, reg_name))
        except ValueError as e:
            resolve_errors.append((name_or_id, str(e)))

    # Report resolution errors
    if resolve_errors:
        for name, error in resolve_errors:
            print(f"✗ {name} - {error}", file=sys.stderr)
        if not resolved:
            return 1

    # Start each pod
    results: list[tuple[str, bool, str]] = []  # (label, success, message)

    if len(resolved) > 1:
        print(f"Starting {len(resolved)} pods...")

    for input_name, pod_id, reg_name in resolved:
        result = _start_single_pod(
            input_name, pod_id, reg_name, name_as, no_wait, config, api, registry
        )
        results.append(result)

    # Report results
    success_count = sum(1 for _, success, _ in results if success)
    failure_count = len(results) - success_count + len(resolve_errors)

    for label, success, message in results:
        if success:
            print(f"✓ {label} - {message}")
        else:
            print(f"✗ {label} - {message}", file=sys.stderr)

    if no_wait and success_count > 0:
        print(f"\nUse 'rpod list --refresh' to check status.")

    return 0 if failure_count == 0 else 1


def cmd_terminate(names: list[str], force: bool = False) -> int:
    """Terminate one or more pods (destroys them permanently).

    Accepts registry names or RunPod pod IDs.
    """
    config = load_config()
    api = RunPodAPI(config.api_key, timeout=config.api_timeout)
    registry = PodRegistry()

    # Resolve all pods first
    resolved: list[tuple[str, str, str | None]] = []  # (input_name, pod_id, reg_name)
    resolve_errors: list[tuple[str, str]] = []  # (input_name, error)

    for name_or_id in names:
        try:
            pod_id, reg_name = resolve_pod(name_or_id, registry, api)
            resolved.append((name_or_id, pod_id, reg_name))
        except ValueError as e:
            resolve_errors.append((name_or_id, str(e)))

    # Report resolution errors
    if resolve_errors:
        for name, error in resolve_errors:
            print(f"✗ {name} - {error}", file=sys.stderr)
        if not resolved:
            return 1

    # Confirmation prompt
    if not force:
        if len(resolved) == 1:
            _, pod_id, reg_name = resolved[0]
            label = reg_name or pod_id
            print(f"This will PERMANENTLY DELETE pod '{label}' and all its data!")
            print(f"Pod ID: {pod_id}")
        else:
            print(f"This will PERMANENTLY DELETE {len(resolved)} pods and all their data!")
            for _, pod_id, reg_name in resolved:
                label = reg_name or pod_id
                print(f"  - {label} ({pod_id})")

        response = input("Type 'yes' to confirm: ")
        if response.lower() != "yes":
            print("Aborted")
            return 1

    # Terminate each pod
    results: list[tuple[str, bool, str]] = []  # (label, success, message)

    if len(resolved) > 1:
        print(f"Terminating {len(resolved)} pods...")

    for input_name, pod_id, reg_name in resolved:
        label = reg_name or pod_id
        try:
            api.terminate_pod(pod_id)
            if reg_name:
                registry.remove(reg_name)
            results.append((label, True, "terminated"))
        except RunPodAPIError as e:
            results.append((label, False, str(e)))

    # Report results
    success_count = sum(1 for _, success, _ in results if success)
    failure_count = len(results) - success_count + len(resolve_errors)

    for label, success, message in results:
        if success:
            print(f"✓ {label} - {message}")
        else:
            print(f"✗ {label} - {message}", file=sys.stderr)

    return 0 if failure_count == 0 else 1
