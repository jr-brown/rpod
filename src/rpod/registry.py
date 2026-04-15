"""Pod registry - local tracking of RunPod instances."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

import yaml

from rpod.logging import log_debug


@dataclass
class PodInfo:
    """Information about a registered pod."""

    name: str
    pod_id: Optional[str] = None  # RunPod API ID (for lifecycle commands)
    ip: Optional[str] = None
    port: int = 22
    workspace: str = "/workspace"
    key_path: str = "~/.ssh/id_ed25519"
    created: Optional[str] = None
    gpu_type: Optional[str] = None
    status: str = "UNKNOWN"
    # Project config settings (from .rpod.yaml at creation time)
    workdir: Optional[str] = None  # Working directory for commands
    auto_log: bool = False  # Auto-enable session logging
    log_dir: str = "/workspace/logs"  # Where to store logs

    @property
    def is_cpu(self) -> bool:
        """Whether this is a CPU-only pod."""
        return self.gpu_type is None or self.gpu_type == "CPU"

    @property
    def ssh_opts(self) -> list[str]:
        """SSH command options for this pod."""
        key = Path(self.key_path).expanduser()
        return [
            "-i", str(key),
            "-p", str(self.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=10",
        ]

    @property
    def ssh_host(self) -> str:
        """SSH host string."""
        return f"root@{self.ip}"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {k: v for k, v in asdict(self).items() if k != "name"}


class PodRegistry:
    """Registry for tracking RunPod instances.

    Stores pod information in ~/.rpod/pods.yaml
    """

    def __init__(self, registry_path: Optional[Path] = None) -> None:
        if registry_path is None:
            registry_path = Path.home() / ".rpod" / "pods.yaml"
        self.registry_path = registry_path
        self._pods: dict[str, PodInfo] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if not self.registry_path.exists():
            log_debug(f"Registry not found at {self.registry_path}, starting empty")
            self._pods = {}
            return

        content = self.registry_path.read_text()
        data = yaml.safe_load(content) or {}
        pods_data = data.get("pods", {})

        self._pods = {}
        for name, info in pods_data.items():
            self._pods[name] = PodInfo(name=name, **info)
        log_debug(f"Loaded {len(self._pods)} pods from {self.registry_path}")

    def _save(self) -> None:
        """Save registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        data = {"pods": {name: pod.to_dict() for name, pod in self._pods.items()}}
        self.registry_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        log_debug(f"Saved {len(self._pods)} pods to {self.registry_path}")

    def list(self) -> list[PodInfo]:
        """List all registered pods."""
        return list(self._pods.values())

    def get(self, name: str) -> Optional[PodInfo]:
        """Get a pod by name."""
        return self._pods.get(name)

    def register(
        self,
        name: str,
        ip: Optional[str],
        port: int,
        pod_id: Optional[str] = None,
        workspace: str = "/workspace",
        key_path: str = "~/.ssh/id_ed25519",
        gpu_type: Optional[str] = None,
        status: str = "RUNNING",
        workdir: Optional[str] = None,
        auto_log: bool = False,
        log_dir: str = "/workspace/logs",
    ) -> PodInfo:
        """Register a new pod or update existing."""
        pod = PodInfo(
            name=name,
            pod_id=pod_id,
            ip=ip,
            port=port,
            workspace=workspace,
            key_path=key_path,
            created=datetime.now().isoformat(),
            gpu_type=gpu_type,
            status=status,
            workdir=workdir,
            auto_log=auto_log,
            log_dir=log_dir,
        )
        self._pods[name] = pod
        self._save()
        log_debug(f"Registered pod: {name} (ip={ip}, port={port}, gpu={gpu_type})")
        return pod

    def update(self, name: str, **kwargs) -> Optional[PodInfo]:
        """Update an existing pod's fields."""
        pod = self._pods.get(name)
        if not pod:
            log_debug(f"Update failed: pod '{name}' not found")
            return None

        for key, value in kwargs.items():
            if hasattr(pod, key):
                setattr(pod, key, value)

        self._save()
        log_debug(f"Updated pod '{name}': {kwargs}")
        return pod

    def remove(self, name: str) -> bool:
        """Remove a pod from the registry.

        Returns True if pod was found and removed.
        """
        if name in self._pods:
            del self._pods[name]
            self._save()
            log_debug(f"Removed pod '{name}' from registry")
            return True
        log_debug(f"Remove failed: pod '{name}' not found")
        return False

    def find_by_pod_id(self, pod_id: str) -> Optional[PodInfo]:
        """Find a pod by its RunPod API ID."""
        for pod in self._pods.values():
            if pod.pod_id == pod_id:
                return pod
        return None
