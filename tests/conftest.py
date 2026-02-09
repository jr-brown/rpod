"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path


@pytest.fixture(autouse=True, scope="session")
def setup_test_logging(tmp_path_factory):
    """Initialize rpod logging for tests with a temp log directory."""
    from rpod.logging import init_logging

    base_tmp = tmp_path_factory.mktemp("test_env")
    test_log_dir = base_tmp / "rpod_logs"
    test_log_dir.mkdir(parents=True, exist_ok=True)
    test_log_file = test_log_dir / "rpod.log"

    init_logging(level="debug", log_file=test_log_file)

    mp = pytest.MonkeyPatch()
    mp.setenv("HF_HOME", str(base_tmp / "hf_cache"))
    mp.setenv("TRANSFORMERS_CACHE", str(base_tmp / "hf_cache"))
    mp.setenv("TOKENIZERS_PARALLELISM", "false")
    mp.setenv("WANDB_DISABLED", "true")

    yield
    mp.undo()
