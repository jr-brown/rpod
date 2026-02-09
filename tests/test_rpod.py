"""Tests for rpod CLI tool."""

import tempfile
import time
from pathlib import Path

import pytest
import yaml


class TestProjectConfig:
    """Tests for project_config.py."""

    def test_load_empty_config(self, tmp_path: Path):
        """Loading from directory without .rpod.yaml returns defaults."""
        from rpod.project_config import load_project_config, ProjectConfig

        config = load_project_config(tmp_path)

        assert config.workdir is None
        assert config.auto_log is False
        assert config.log_dir == "/workspace/logs"
        assert config.default_gpu is None
        assert config.default_volume_size is None
        assert config.default_container_disk is None
        assert config.models == []
        assert config.push_excludes == []
        assert config.clean_targets == []
        assert config.env_vars == {}

    def test_load_full_config(self, tmp_path: Path):
        """Loading .rpod.yaml populates all fields."""
        from rpod.project_config import load_project_config

        config_content = {
            "workdir": "/workspace/myproject",
            "auto_log": True,
            "log_dir": "/workspace/custom-logs",
            "default_gpu": "NVIDIA H100 80GB HBM3",
            "default_volume_size": 200,
            "default_container_disk": 150,
            "models": ["model/a", "model/b"],
            "push_excludes": ["*.log", "wandb/"],
            "clean_targets": ["tmp", "checkpoints"],
            "env_vars": {"FOO": "bar", "BAZ": "qux"},
        }
        (tmp_path / ".rpod.yaml").write_text(yaml.dump(config_content))

        config = load_project_config(tmp_path)

        assert config.workdir == "/workspace/myproject"
        assert config.auto_log is True
        assert config.log_dir == "/workspace/custom-logs"
        assert config.default_gpu == "NVIDIA H100 80GB HBM3"
        assert config.default_volume_size == 200
        assert config.default_container_disk == 150
        assert config.models == ["model/a", "model/b"]
        assert config.push_excludes == ["*.log", "wandb/"]
        assert config.clean_targets == ["tmp", "checkpoints"]
        assert config.env_vars == {"FOO": "bar", "BAZ": "qux"}

    def test_load_partial_config(self, tmp_path: Path):
        """Partial config uses defaults for missing fields."""
        from rpod.project_config import load_project_config

        config_content = {
            "workdir": "/workspace/proj",
            "default_gpu": "NVIDIA A100",
        }
        (tmp_path / ".rpod.yaml").write_text(yaml.dump(config_content))

        config = load_project_config(tmp_path)

        assert config.workdir == "/workspace/proj"
        assert config.default_gpu == "NVIDIA A100"
        # Defaults for unspecified fields
        assert config.auto_log is False
        assert config.log_dir == "/workspace/logs"
        assert config.models == []

    def test_search_parent_directories(self, tmp_path: Path):
        """Config is found by searching parent directories."""
        from rpod.project_config import load_project_config

        # Create config in parent
        config_content = {"workdir": "/workspace/parent-config"}
        (tmp_path / ".rpod.yaml").write_text(yaml.dump(config_content))

        # Create nested subdirectory
        subdir = tmp_path / "a" / "b" / "c"
        subdir.mkdir(parents=True)

        config = load_project_config(subdir)

        assert config.workdir == "/workspace/parent-config"

    def test_empty_yaml_file(self, tmp_path: Path):
        """Empty .rpod.yaml file returns defaults."""
        from rpod.project_config import load_project_config

        (tmp_path / ".rpod.yaml").write_text("")

        config = load_project_config(tmp_path)

        assert config.workdir is None
        assert config.auto_log is False


class TestPodRegistry:
    """Tests for registry.py."""

    def test_register_with_new_fields(self, tmp_path: Path):
        """Register stores workdir, auto_log, log_dir."""
        from rpod.registry import PodRegistry

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)

        pod = registry.register(
            name="test-pod",
            ip="1.2.3.4",
            port=22,
            pod_id="abc123",
            workspace="/workspace/proj",
            workdir="/workspace/proj/subdir",
            auto_log=True,
            log_dir="/workspace/custom-logs",
        )

        assert pod.workdir == "/workspace/proj/subdir"
        assert pod.auto_log is True
        assert pod.log_dir == "/workspace/custom-logs"

    def test_register_default_new_fields(self, tmp_path: Path):
        """Register uses defaults for new fields when not specified."""
        from rpod.registry import PodRegistry

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)

        pod = registry.register(
            name="test-pod",
            ip="1.2.3.4",
            port=22,
        )

        assert pod.workdir is None
        assert pod.auto_log is False
        assert pod.log_dir == "/workspace/logs"

    def test_persistence_with_new_fields(self, tmp_path: Path):
        """New fields persist across registry reload."""
        from rpod.registry import PodRegistry

        registry_path = tmp_path / "pods.yaml"

        # Create and register
        registry1 = PodRegistry(registry_path)
        registry1.register(
            name="persist-test",
            ip="5.6.7.8",
            port=12345,
            workdir="/workspace/test",
            auto_log=True,
            log_dir="/workspace/logs2",
        )

        # Reload from disk
        registry2 = PodRegistry(registry_path)
        pod = registry2.get("persist-test")

        assert pod is not None
        assert pod.workdir == "/workspace/test"
        assert pod.auto_log is True
        assert pod.log_dir == "/workspace/logs2"

    def test_update_new_fields(self, tmp_path: Path):
        """Update can modify workdir, auto_log, log_dir."""
        from rpod.registry import PodRegistry

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)

        registry.register(name="update-test", ip="1.1.1.1", port=22)

        registry.update("update-test", workdir="/new/workdir", auto_log=True)

        pod = registry.get("update-test")
        assert pod.workdir == "/new/workdir"
        assert pod.auto_log is True


class TestSyncExcludes:
    """Tests for sync.py exclude merging."""

    def test_get_excludes_empty_when_no_args(self):
        """get_excludes returns empty list when no excludes provided."""
        from rpod.commands.sync import get_excludes

        excludes = get_excludes()

        assert excludes == []

    def test_get_excludes_with_cli(self):
        """CLI excludes are returned."""
        from rpod.commands.sync import get_excludes

        excludes = get_excludes(cli_excludes=["node_modules", "dist"])

        assert "node_modules" in excludes
        assert "dist" in excludes

    def test_get_excludes_with_config(self):
        """Config excludes are returned."""
        from rpod.commands.sync import get_excludes

        excludes = get_excludes(config_excludes=["*.log", "wandb/"])

        assert "*.log" in excludes
        assert "wandb/" in excludes

    def test_get_excludes_cli_and_config(self):
        """CLI and config excludes are both included."""
        from rpod.commands.sync import get_excludes

        excludes = get_excludes(
            cli_excludes=["cli-pattern"],
            config_excludes=["config-pattern"],
        )

        assert "cli-pattern" in excludes
        assert "config-pattern" in excludes

    def test_get_excludes_no_duplicates(self):
        """Duplicate excludes are not added twice."""
        from rpod.commands.sync import get_excludes

        excludes = get_excludes(
            cli_excludes=[".venv", "new-pattern"],
            config_excludes=[".venv", ".git"],
        )

        # .venv should only appear once
        assert excludes.count(".venv") == 1


class TestCleanTargets:
    """Tests for clean.py target handling."""

    def test_valid_targets(self):
        """All valid targets are recognized."""
        from rpod.commands.clean import CLEAN_TARGETS

        assert "tmp" in CLEAN_TARGETS
        assert "checkpoints" in CLEAN_TARGETS
        assert "pycache" in CLEAN_TARGETS
        assert "logs" in CLEAN_TARGETS

    def test_target_has_required_fields(self):
        """Each target has description, command, and size_cmd."""
        from rpod.commands.clean import CLEAN_TARGETS

        for name, target in CLEAN_TARGETS.items():
            assert "description" in target, f"{name} missing description"
            assert "command" in target, f"{name} missing command"
            assert "size_cmd" in target, f"{name} missing size_cmd"


class TestCLIParser:
    """Tests for cli.py argument parsing."""

    def test_create_gpu_optional(self):
        """--gpu is optional in create command."""
        from rpod.cli import create_parser

        parser = create_parser()

        # Should not raise - gpu is optional
        args = parser.parse_args(["create", "test-pod"])
        assert args.name == "test-pod"
        assert args.gpu is None

    def test_create_with_gpu(self):
        """--gpu can be specified in create command."""
        from rpod.cli import create_parser

        parser = create_parser()

        args = parser.parse_args(["create", "test-pod", "--gpu", "NVIDIA A100"])
        assert args.name == "test-pod"
        assert args.gpu == "NVIDIA A100"

    def test_clean_command_parsing(self):
        """clean command parses targets and --dry-run."""
        from rpod.cli import create_parser

        parser = create_parser()

        # With targets
        args = parser.parse_args(["clean", "my-pod", "tmp", "checkpoints"])
        assert args.name == "my-pod"
        assert args.targets == ["tmp", "checkpoints"]
        assert args.dry_run is False

        # With --dry-run
        args = parser.parse_args(["clean", "my-pod", "--dry-run", "all"])
        assert args.dry_run is True
        assert args.targets == ["all"]

        # Short form -n
        args = parser.parse_args(["clean", "my-pod", "-n"])
        assert args.dry_run is True

    def test_clean_no_targets(self):
        """clean command works without explicit targets."""
        from rpod.cli import create_parser

        parser = create_parser()

        args = parser.parse_args(["clean", "my-pod"])
        assert args.name == "my-pod"
        assert args.targets == []


class TestParseTomlStrict:
    """Tests for config.py _parse_toml_strict()."""

    def test_basic_key_value(self):
        """Parse basic key = "value" pairs."""
        from rpod.config import _parse_toml_strict

        content = 'apikey = "my_secret_key"'
        result = _parse_toml_strict(content)

        assert result == {"apikey": "my_secret_key"}

    def test_unquoted_value(self):
        """Unquoted strings are rejected by TOML parser."""
        from rpod.config import _parse_toml_strict

        content = "apikey = my_unquoted_value"
        with pytest.raises(Exception):
            _parse_toml_strict(content)

    def test_single_quotes(self):
        """Parse single-quoted values."""
        from rpod.config import _parse_toml_strict

        content = "apikey = 'single_quoted'"
        result = _parse_toml_strict(content)

        assert result == {"apikey": "single_quoted"}

    def test_comments_ignored(self):
        """Comments are ignored."""
        from rpod.config import _parse_toml_strict

        content = """# This is a comment
apikey = "value"
# Another comment
ssh_key = "~/.ssh/id_ed25519"
"""
        result = _parse_toml_strict(content)

        assert result == {"apikey": "value", "ssh_key": "~/.ssh/id_ed25519"}

    def test_empty_lines_ignored(self):
        """Empty lines are ignored."""
        from rpod.config import _parse_toml_strict

        content = """

apikey = "value"

ssh_key = "key"

"""
        result = _parse_toml_strict(content)

        assert result == {"apikey": "value", "ssh_key": "key"}

    def test_spaces_around_equals(self):
        """Spaces around = are handled."""
        from rpod.config import _parse_toml_strict

        content = "apikey   =   \"value\""
        result = _parse_toml_strict(content)

        assert result == {"apikey": "value"}

    def test_empty_string_value(self):
        """Empty string values are parsed."""
        from rpod.config import _parse_toml_strict

        content = 'apikey = ""'
        result = _parse_toml_strict(content)

        assert result == {"apikey": ""}

    def test_multiple_keys(self):
        """Multiple keys are parsed correctly."""
        from rpod.config import _parse_toml_strict

        content = """apikey = "key1"
ssh_key = "~/.ssh/id_ed25519"
log_level = "debug"
api_timeout = 60"""
        result = _parse_toml_strict(content)

        assert result == {
            "apikey": "key1",
            "ssh_key": "~/.ssh/id_ed25519",
            "log_level": "debug",
            "api_timeout": 60,
        }

    def test_tables_rejected(self):
        """Tables are rejected to keep config flat."""
        from rpod.config import _parse_toml_strict

        content = """apikey = "key1"
[extra]
foo = "bar"
"""
        with pytest.raises(ValueError):
            _parse_toml_strict(content)


class TestMaskSensitive:
    """Tests for logging.py _mask_sensitive()."""

    def test_masks_api_key(self):
        """api_key is masked."""
        from rpod.logging import _mask_sensitive

        data = {"api_key": "secret123"}
        result = _mask_sensitive(data)

        assert result == {"api_key": "***"}

    def test_masks_apikey(self):
        """apikey (no underscore) is masked."""
        from rpod.logging import _mask_sensitive

        data = {"apikey": "secret123"}
        result = _mask_sensitive(data)

        assert result == {"apikey": "***"}

    def test_masks_token(self):
        """*token* keys are masked."""
        from rpod.logging import _mask_sensitive

        data = {"hf_token": "hf_abc123", "auth_token": "xyz"}
        result = _mask_sensitive(data)

        assert result == {"hf_token": "***", "auth_token": "***"}

    def test_masks_password(self):
        """password is masked."""
        from rpod.logging import _mask_sensitive

        data = {"password": "hunter2", "db_password": "secret"}
        result = _mask_sensitive(data)

        assert result == {"password": "***", "db_password": "***"}

    def test_masks_secret(self):
        """*secret* keys are masked."""
        from rpod.logging import _mask_sensitive

        data = {"client_secret": "abc123"}
        result = _mask_sensitive(data)

        assert result == {"client_secret": "***"}

    def test_preserves_normal_keys(self):
        """Normal keys are not masked."""
        from rpod.logging import _mask_sensitive

        data = {"name": "my-pod", "gpu_type": "H100", "port": 22}
        result = _mask_sensitive(data)

        assert result == {"name": "my-pod", "gpu_type": "H100", "port": 22}

    def test_truncates_long_values(self):
        """Values > 50 chars are truncated."""
        from rpod.logging import _mask_sensitive

        long_value = "a" * 100
        data = {"description": long_value}
        result = _mask_sensitive(data)

        assert len(result["description"]) < len(long_value)
        assert result["description"].endswith("...")

    def test_case_insensitive_matching(self):
        """Key matching is case-insensitive."""
        from rpod.logging import _mask_sensitive

        data = {"API_KEY": "secret", "Password": "hunter2", "TOKEN": "abc"}
        result = _mask_sensitive(data)

        assert result == {"API_KEY": "***", "Password": "***", "TOKEN": "***"}


class TestResolvePod:
    """Tests for lifecycle.py resolve_pod()."""

    def test_resolve_by_registry_name(self, tmp_path: Path):
        """Resolves pod by name in registry."""
        from rpod.registry import PodRegistry
        from rpod.commands.lifecycle import resolve_pod
        from rpod.api import RunPodAPI
        from unittest.mock import MagicMock, create_autospec

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)
        registry.register(
            name="my-pod",
            ip="1.2.3.4",
            port=22,
            pod_id="abc123",
        )

        # Mock API with spec to satisfy beartype
        api = create_autospec(RunPodAPI, instance=True)

        pod_id, reg_name = resolve_pod("my-pod", registry, api)

        assert pod_id == "abc123"
        assert reg_name == "my-pod"
        api.get_pod.assert_not_called()

    def test_resolve_by_pod_id_in_registry(self, tmp_path: Path):
        """Resolves pod by pod_id field in registry."""
        from rpod.registry import PodRegistry
        from rpod.commands.lifecycle import resolve_pod
        from rpod.api import RunPodAPI
        from unittest.mock import create_autospec

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)
        registry.register(
            name="my-pod",
            ip="1.2.3.4",
            port=22,
            pod_id="xyz789",
        )

        api = create_autospec(RunPodAPI, instance=True)

        # Lookup by pod_id instead of name
        pod_id, reg_name = resolve_pod("xyz789", registry, api)

        assert pod_id == "xyz789"
        assert reg_name == "my-pod"
        api.get_pod.assert_not_called()

    def test_resolve_by_raw_api_id(self, tmp_path: Path):
        """Falls back to API lookup for unregistered pods."""
        from rpod.registry import PodRegistry
        from rpod.commands.lifecycle import resolve_pod
        from rpod.api import PodStatus, RunPodAPI
        from unittest.mock import create_autospec

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)
        # Empty registry

        api = create_autospec(RunPodAPI, instance=True)
        api.get_pod.return_value = PodStatus(
            pod_id="api-pod-123",
            name="api-pod",
            status="RUNNING",
            gpu_type="H100",
            public_ip="5.6.7.8",
            ssh_port=12345,
            cost_per_hour=2.49,
        )

        pod_id, reg_name = resolve_pod("api-pod-123", registry, api)

        assert pod_id == "api-pod-123"
        assert reg_name is None  # Not in registry
        api.get_pod.assert_called_once_with("api-pod-123")

    def test_error_when_not_found(self, tmp_path: Path):
        """Raises ValueError when pod not found anywhere."""
        from rpod.registry import PodRegistry
        from rpod.commands.lifecycle import resolve_pod
        from rpod.api import RunPodAPIError, RunPodAPI
        from unittest.mock import create_autospec

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)

        api = create_autospec(RunPodAPI, instance=True)
        api.get_pod.side_effect = RunPodAPIError("Pod not found")

        with pytest.raises(ValueError) as exc_info:
            resolve_pod("nonexistent", registry, api)

        assert "not found" in str(exc_info.value)

    def test_error_when_no_pod_id(self, tmp_path: Path):
        """Raises ValueError when registry entry has no pod_id."""
        from rpod.registry import PodRegistry
        from rpod.commands.lifecycle import resolve_pod
        from rpod.api import RunPodAPI
        from unittest.mock import create_autospec

        registry_path = tmp_path / "pods.yaml"
        registry = PodRegistry(registry_path)
        # Register without pod_id
        registry.register(
            name="manual-pod",
            ip="1.2.3.4",
            port=22,
            pod_id=None,  # No pod_id
        )

        api = create_autospec(RunPodAPI, instance=True)

        with pytest.raises(ValueError) as exc_info:
            resolve_pod("manual-pod", registry, api)

        assert "no API ID" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Integration tests (require RunPod config + create real pods)
# ---------------------------------------------------------------------------

# Module-level state for integration pod
_integration_pod_name: str | None = None


def _terminate_pod(pod_name: str) -> None:
    """Terminate a pod, warning loudly if cleanup fails."""
    from rpod.commands.lifecycle import cmd_terminate

    try:
        cmd_terminate([pod_name], force=True)
    except Exception as e:
        import warnings

        warnings.warn(
            f"\n{'='*60}\n"
            f"MANUAL CLEANUP REQUIRED\n"
            f"Pod '{pod_name}' was created but termination failed: {e}\n"
            f"Please terminate it manually via: uv run rpod terminate {pod_name}\n"
            f"{'='*60}",
            stacklevel=2,
        )


def teardown_module() -> None:
    """Terminate the integration pod after all tests in this module."""
    if _integration_pod_name is not None:
        print(f"\nTerminating test pod {_integration_pod_name}...")
        _terminate_pod(_integration_pod_name)


class TestIntegrationPodSetup:
    """Creates and validates the integration test pod. Must run before other integration tests."""

    def test_pod_creation_and_ssh(self):
        """Create a test pod and verify SSH connectivity."""
        global _integration_pod_name

        if not Path("~/.rpod/config.toml").expanduser().exists():
            pytest.skip("RunPod config not found at ~/.rpod/config.toml")

        from rpod.commands.lifecycle import cmd_create
        from rpod.registry import PodRegistry
        from rpod.ssh import SSHConnection

        pod_name = f"pytest-{int(time.time())}"

        # Create pod with minimal resources
        result = cmd_create(
            name=pod_name,
            gpu_type="NVIDIA RTX 4000 Ada Generation",
            image="runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
            template_id=None,
            volume_size=20,
            workspace=None,
            gpu_count=1,
            container_disk=20,
            bootstrap=False,
            models=None,
        )

        if result != 0:
            _terminate_pod(pod_name)
            pytest.fail(f"Pod creation failed (exit code {result})")

        registry = PodRegistry()
        pod = registry.get(pod_name)

        if not pod or not pod.ip:
            _terminate_pod(pod_name)
            pytest.fail("Pod created but not properly registered (no IP)")

        # Verify SSH works
        try:
            ssh = SSHConnection(pod, timeout=30)
            check = ssh.run("echo hello")
            assert check.success, f"SSH command failed: {check.stderr}"
        except Exception as e:
            _terminate_pod(pod_name)
            pytest.fail(f"Pod created but SSH failed: {e}")

        _integration_pod_name = pod_name


@pytest.fixture
def live_pod():
    """Get the integration pod name, skipping if setup failed."""
    if _integration_pod_name is None:
        pytest.skip("Integration pod not available")
    return _integration_pod_name


@pytest.fixture
def ssh_connection(live_pod):
    """Get SSH connection to the integration pod."""
    from rpod.registry import PodRegistry
    from rpod.ssh import SSHConnection

    registry = PodRegistry()
    pod = registry.get(live_pod)
    return SSHConnection(pod, timeout=30)


class TestPodConfig:
    """Tests for pod creation config fields."""

    def test_pod_is_running(self, live_pod):
        """Pod is created and running."""
        from rpod.registry import PodRegistry

        registry = PodRegistry()
        pod = registry.get(live_pod)

        assert pod is not None
        assert pod.ip is not None
        assert pod.port > 0
        assert pod.status == "RUNNING"

    def test_pod_has_config_fields(self, live_pod):
        """Pod has workdir, auto_log, log_dir from config."""
        from rpod.registry import PodRegistry
        from rpod.project_config import load_project_config

        registry = PodRegistry()
        pod = registry.get(live_pod)
        config = load_project_config()

        assert pod.workdir == config.workdir
        assert pod.auto_log == config.auto_log
        assert pod.log_dir == config.log_dir


class TestSSHConnectivity:
    """Tests for SSH connection to pod."""

    def test_gpu_available(self, ssh_connection):
        """GPU is available on the pod."""
        result = ssh_connection.run("nvidia-smi --query-gpu=name --format=csv,noheader")

        assert result.success
        assert "RTX 4000" in result.stdout or "Ada" in result.stdout


class TestExecCommand:
    """Tests for rpod exec functionality."""

    def test_exec_simple_command(self, live_pod, ssh_connection):
        """rpod exec runs simple commands."""
        from rpod.registry import PodRegistry
        from rpod.commands.exec import cmd_exec

        registry = PodRegistry()
        pod = registry.get(live_pod)
        if pod.workdir:
            ssh_connection.run(f"mkdir -p {pod.workdir}")

        result = cmd_exec(live_pod, "echo test-output", None, None, None)

        assert result == 0

    def test_exec_uses_workdir(self, live_pod, ssh_connection):
        """rpod exec uses workdir from registry."""
        from rpod.registry import PodRegistry
        from rpod.commands.exec import cmd_exec

        registry = PodRegistry()
        pod = registry.get(live_pod)

        if pod.workdir:
            ssh_connection.run(f"mkdir -p {pod.workdir}")

        marker_file = "pytest-workdir-test"
        cmd_exec(live_pod, f"touch {marker_file}", None, None, None)

        target = pod.workdir or pod.workspace
        result = ssh_connection.run(f"test -f {target}/{marker_file} && echo found")

        assert "found" in result.stdout, f"File not found in {target}"

        ssh_connection.run(f"rm -f {target}/{marker_file}")


class TestPushCommand:
    """Tests for rpod push functionality."""

    def test_push_creates_workspace(self, live_pod, ssh_connection):
        """rpod push creates the workspace directory."""
        from rpod.commands.sync import cmd_push
        from rpod.registry import PodRegistry

        registry = PodRegistry()
        pod = registry.get(live_pod)

        result = cmd_push(live_pod, ".", None, None, timeout=120)

        assert result == 0

        check = ssh_connection.run(f"test -d {pod.workspace} && echo exists")
        assert "exists" in check.stdout

    def test_push_excludes_patterns(self, live_pod, ssh_connection):
        """rpod push excludes patterns from config."""
        from rpod.commands.sync import cmd_push
        from rpod.registry import PodRegistry

        registry = PodRegistry()
        pod = registry.get(live_pod)

        result = ssh_connection.run(f"test -d {pod.workspace}/.venv && echo exists || echo missing")

        assert "missing" in result.stdout, ".venv should be excluded from push"


class TestStatusCommand:
    """Tests for rpod status functionality."""

    def test_status_shows_gpu(self, live_pod, capsys):
        """rpod status shows GPU information."""
        from rpod.commands.monitor import cmd_status

        result = cmd_status(live_pod)

        assert result == 0

        captured = capsys.readouterr()
        assert "GPU Status" in captured.out
        assert "RTX 4000" in captured.out or "Ada" in captured.out

    def test_status_shows_disk(self, live_pod, capsys):
        """rpod status shows disk usage."""
        from rpod.commands.monitor import cmd_status

        result = cmd_status(live_pod)

        assert result == 0

        captured = capsys.readouterr()
        assert "Disk Usage" in captured.out
        assert "/workspace" in captured.out


class TestCleanCommand:
    """Tests for rpod clean functionality."""

    def test_clean_dry_run(self, live_pod, capsys):
        """rpod clean --dry-run shows what would be cleaned."""
        from rpod.commands.clean import cmd_clean

        result = cmd_clean(live_pod, ["tmp"], dry_run=True)

        assert result == 0

        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "tmp" in captured.out

    def test_clean_tmp(self, live_pod, ssh_connection, capsys):
        """rpod clean tmp actually cleans /tmp."""
        from rpod.commands.clean import cmd_clean

        ssh_connection.run("echo test > /tmp/pytest-clean-test")

        check = ssh_connection.run("test -f /tmp/pytest-clean-test && echo exists")
        assert "exists" in check.stdout

        result = cmd_clean(live_pod, ["tmp"], dry_run=False)

        assert result == 0

        check = ssh_connection.run("test -f /tmp/pytest-clean-test && echo exists || echo gone")
        assert "gone" in check.stdout


class TestLogsCommand:
    """Tests for rpod logs functionality."""

    def test_logs_missing_session_shows_available(self, live_pod, ssh_connection, capsys):
        """rpod logs for missing session lists available sessions."""
        from rpod.commands.monitor import cmd_logs

        ssh_connection.run("apt-get update -qq && apt-get install -y -qq tmux > /dev/null 2>&1")

        ssh_connection.run("tmux new-session -d -s real-session 'sleep 60'")

        result = cmd_logs(live_pod, "fake-session", follow=False, lines=50)

        assert result == 1

        captured = capsys.readouterr()
        assert "not found" in captured.err
        assert "real-session" in captured.err

        ssh_connection.run("tmux kill-session -t real-session 2>/dev/null || true")
