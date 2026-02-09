"""Load configuration from ~/.rpod/config.toml"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - stdlib in Python 3.11+
    raise ImportError(
        "tomllib is required (Python 3.11+). Please upgrade your Python runtime."
    ) from exc

@dataclass
class Config:
    """RunPod configuration."""

    api_key: str
    ssh_key: Path
    log_level: str = "off"  # off, error, info, debug
    api_timeout: int = 30  # API request timeout in seconds

    def __post_init__(self) -> None:
        # Expand ~ in ssh_key path
        if isinstance(self.ssh_key, str):
            self.ssh_key = Path(self.ssh_key).expanduser()


def _parse_toml_strict(content: str) -> dict[str, object]:
    """Parse TOML config and reject unsupported structures."""
    data = tomllib.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Config must be a TOML table with top-level key/value pairs.")

    unsupported_keys = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            unsupported_keys.append(key)
    if unsupported_keys:
        keys = ", ".join(sorted(unsupported_keys))
        raise ValueError(
            "Unsupported TOML structures (tables/arrays) in config. "
            f"Only flat key/value pairs are allowed. Offending keys: {keys}"
        )

    return data


def load_config(config_path: Optional[Path] = None) -> Config:
    """Load RunPod configuration from TOML file.

    Args:
        config_path: Path to config file. Defaults to ~/.rpod/config.toml

    Returns:
        Config object with API key and SSH key path

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If required keys are missing
    """
    if config_path is None:
        config_path = Path.home() / ".rpod" / "config.toml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Create it with:\n"
            f"  mkdir -p ~/.rpod\n"
            f'  echo \'apikey = "rp_your_api_key_here"\' > ~/.rpod/config.toml\n'
            f'  echo \'ssh_key = "~/.ssh/id_ed25519"\' >> ~/.rpod/config.toml'
        )

    content = config_path.read_text()
    data = _parse_toml_strict(content)

    allowed_keys = {"apikey", "api_key", "ssh_key", "log_level", "api_timeout"}
    unknown_keys = set(data.keys()) - allowed_keys
    if unknown_keys:
        keys = ", ".join(sorted(unknown_keys))
        raise ValueError(
            f"Unknown keys in {config_path}: {keys}\n"
            "Allowed keys: apikey, api_key, ssh_key, log_level, api_timeout"
        )

    # Get API key
    api_key = data.get("apikey") or data.get("api_key")
    if not api_key:
        raise ValueError(
            f"Missing 'apikey' in {config_path}\n"
            f"Add: apikey = \"rp_your_api_key_here\""
        )

    # Get SSH key path (optional, has default)
    ssh_key_str = data.get("ssh_key", "~/.ssh/id_ed25519")
    ssh_key = Path(ssh_key_str).expanduser()

    # Get log level (optional, defaults to "off")
    log_level = data.get("log_level", "off")

    # Get API timeout (optional, defaults to 30)
    api_timeout_raw = data.get("api_timeout", 30)
    try:
        api_timeout = int(api_timeout_raw)
    except (TypeError, ValueError):
        api_timeout = 30

    return Config(api_key=api_key, ssh_key=ssh_key, log_level=log_level, api_timeout=api_timeout)
