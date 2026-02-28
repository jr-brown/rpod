"""Local project configuration from .rpod.yaml."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from rpod.logging import log_debug


# Known valid keys in .rpod.yaml
VALID_KEYS = {
    "workdir",
    "auto_log",
    "log_dir",
    "default_gpu",
    "default_volume_size",
    "default_container_disk",
    "default_template_id",
    "default_image",
    "models",
    "push_excludes",
    "clean_targets",
    "env_vars",
    "log_level",
    "setup_datasets",
    "region_whitelist",
    "default_cpu_type",
}


@dataclass
class ProjectConfig:
    """Project-specific rpod configuration.

    Loaded from .rpod.yaml in the current directory or any parent directory.
    """

    # Command execution
    workdir: Optional[str] = None
    auto_log: bool = False
    log_dir: str = "/workspace/logs"

    # Pod creation defaults
    default_gpu: Optional[str] = None
    default_volume_size: Optional[int] = None
    default_container_disk: Optional[int] = None
    default_template_id: Optional[str] = None
    default_image: Optional[str] = None
    default_cpu_type: Optional[str] = None  # CPU instance type (e.g., "cpu3c-2-4")
    models: list[str] = field(default_factory=list)

    # Sync
    push_excludes: list[str] = field(default_factory=list)

    # Cleanup
    clean_targets: list[str] = field(default_factory=list)

    # Environment
    env_vars: dict[str, str] = field(default_factory=dict)

    # Logging (overrides global config)
    log_level: Optional[str] = None  # off, error, info, debug

    # Setup datasets to clone (list of {name, repo, path} dicts)
    setup_datasets: list[dict[str, str]] = field(default_factory=list)

    # Region whitelist for pod placement (e.g., ["NORTH_AMERICA", "EUROPE"])
    region_whitelist: list[str] = field(default_factory=list)


def _validate_config(data: dict, config_path: Path) -> None:
    """Validate config and warn about issues.

    Prints warnings to stderr for:
    - Unknown keys (possible typos)
    - Invalid workdir format
    - Invalid values
    """
    # Check for unknown keys
    unknown_keys = set(data.keys()) - VALID_KEYS
    if unknown_keys:
        print(
            f"Warning: Unknown keys in {config_path}: {', '.join(sorted(unknown_keys))}",
            file=sys.stderr,
        )
        # Suggest corrections for common typos
        typo_map = {
            "work_dir": "workdir",
            "workDir": "workdir",
            "autolog": "auto_log",
            "auto-log": "auto_log",
            "logdir": "log_dir",
            "log-dir": "log_dir",
            "defaultgpu": "default_gpu",
            "default-gpu": "default_gpu",
            "gpu": "default_gpu",
            "volume_size": "default_volume_size",
            "container_disk": "default_container_disk",
            "template_id": "default_template_id",
            "templateId": "default_template_id",
            "image": "default_image",
            "defaultimage": "default_image",
            "excludes": "push_excludes",
            "exclude": "push_excludes",
        }
        for unknown in unknown_keys:
            if unknown.lower() in typo_map:
                print(f"  Did you mean '{typo_map[unknown.lower()]}' instead of '{unknown}'?", file=sys.stderr)

    # Validate workdir format
    workdir = data.get("workdir")
    if workdir and workdir.upper() != "AUTO":
        if not workdir.startswith("/workspace"):
            print(
                f"Warning: workdir '{workdir}' doesn't start with '/workspace'. "
                "This may not work correctly on RunPod.",
                file=sys.stderr,
            )

    # Validate log_level
    log_level = data.get("log_level")
    if log_level and log_level not in ("off", "error", "info", "debug"):
        print(
            f"Warning: Invalid log_level '{log_level}'. "
            "Valid values: off, error, info, debug",
            file=sys.stderr,
        )


def _resolve_workdir(workdir: Optional[str], cwd: Path) -> Optional[str]:
    """Resolve workdir value, handling AUTO.

    Args:
        workdir: The workdir value from config (may be "AUTO" or a path)
        cwd: The current working directory

    Returns:
        Resolved workdir path, or None if not set.
    """
    if workdir is None:
        return None

    if workdir.upper() == "AUTO":
        resolved = f"/workspace/{cwd.name}"
        log_debug(f"Resolved workdir AUTO -> {resolved}")
        return resolved

    return workdir


def load_project_config(search_dir: Optional[Path] = None) -> ProjectConfig:
    """Load .rpod.yaml from current directory or parents.

    Searches from search_dir (or cwd if not specified) up through parent
    directories until .rpod.yaml is found. Returns empty ProjectConfig
    if no config file exists.

    Special values:
        workdir: "AUTO" - Resolves to /workspace/<current-directory-name>

    Args:
        search_dir: Directory to start searching from. Defaults to cwd.

    Returns:
        ProjectConfig with loaded values or defaults.
    """
    if search_dir is None:
        search_dir = Path.cwd()

    for parent in [search_dir] + list(search_dir.parents):
        config_path = parent / ".rpod.yaml"
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text()) or {}
            log_debug(f"Loaded project config from {config_path}")

            # Validate config
            _validate_config(data, config_path)

            # Resolve workdir (handles AUTO)
            workdir = _resolve_workdir(data.get("workdir"), search_dir)

            return ProjectConfig(
                workdir=workdir,
                auto_log=data.get("auto_log", False),
                log_dir=data.get("log_dir", "/workspace/logs"),
                default_gpu=data.get("default_gpu"),
                default_volume_size=data.get("default_volume_size"),
                default_container_disk=data.get("default_container_disk"),
                default_template_id=data.get("default_template_id"),
                default_image=data.get("default_image"),
                default_cpu_type=data.get("default_cpu_type"),
                models=data.get("models", []),
                push_excludes=data.get("push_excludes", []),
                clean_targets=data.get("clean_targets", []),
                env_vars=data.get("env_vars", {}),
                log_level=data.get("log_level"),
                setup_datasets=data.get("setup_datasets", []),
                region_whitelist=data.get("region_whitelist", []),
            )

    log_debug(f"No .rpod.yaml found in {search_dir} or parents, using defaults")
    return ProjectConfig()
