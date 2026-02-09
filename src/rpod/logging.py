"""Centralized logging for rpod CLI.

Logs commands, SSH operations, API calls, and errors to help diagnose
common problems and usage anti-patterns.

Log levels:
- off: No logging (default)
- error: Only errors
- info: Commands, operations, and errors
- debug: Everything including SSH output and API responses

Configuration:
- ~/.rpod/config.toml: log_level = "info"
- .rpod.yaml: log_level = "debug" (overrides global)

Log files are stored in ~/.rpod/logs/rpod.log with rotation.
"""

from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# Global logger instance
_logger: Optional[logging.Logger] = None
_log_level: str = "off"

# Log levels mapping
LOG_LEVELS = {
    "off": logging.CRITICAL + 10,  # Above CRITICAL, nothing logs
    "error": logging.ERROR,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

DEFAULT_LOG_FILE = Path.home() / ".rpod" / "logs" / "rpod.log"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 3


def init_logging(
    level: str = "off",
    log_file: Optional[Path] = None,
) -> None:
    """Initialize the rpod logger.

    Args:
        level: Log level (off, error, info, debug)
        log_file: Path to log file. Defaults to ~/.rpod/logs/rpod.log
    """
    global _logger, _log_level

    _log_level = level.lower()

    if _log_level == "off":
        _logger = None
        return

    if log_file is None:
        log_file = DEFAULT_LOG_FILE

    # Ensure log directory exists
    log_file = Path(log_file).expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Create logger
    _logger = logging.getLogger("rpod")
    _logger.setLevel(LOG_LEVELS.get(_log_level, logging.INFO))

    # Clear existing handlers
    _logger.handlers.clear()

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_LOG_SIZE,
        backupCount=BACKUP_COUNT,
    )
    file_handler.setLevel(LOG_LEVELS.get(_log_level, logging.INFO))

    # Format: timestamp | level | message
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)

    # Log startup
    _logger.info(f"=== rpod session started (level={_log_level}) ===")


def get_logger() -> Optional[logging.Logger]:
    """Get the rpod logger instance."""
    return _logger


def is_logging_enabled() -> bool:
    """Check if logging is enabled."""
    return _logger is not None


def log_command(command: str, args: dict[str, Any]) -> None:
    """Log a command invocation.

    Args:
        command: Command name (e.g., 'create', 'exec')
        args: Command arguments (sensitive values will be masked)
    """
    if not _logger:
        return

    # Mask sensitive values
    safe_args = _mask_sensitive(args)
    args_str = " ".join(f"{k}={v}" for k, v in safe_args.items() if v is not None)

    _logger.info(f"CMD: {command} {args_str}")


def log_command_result(command: str, exit_code: int, duration_ms: int) -> None:
    """Log command completion.

    Args:
        command: Command name
        exit_code: Exit code (0 = success)
        duration_ms: Duration in milliseconds
    """
    if not _logger:
        return

    status = "OK" if exit_code == 0 else f"FAIL({exit_code})"
    _logger.info(f"CMD: {command} -> {status} ({duration_ms}ms)")


def log_ssh(
    operation: str,
    pod_name: str,
    command: str,
    *,
    exit_code: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    duration_ms: Optional[int] = None,
    attempt: int = 1,
) -> None:
    """Log SSH operation.

    Args:
        operation: Type of operation (run, rsync_push, rsync_pull)
        pod_name: Target pod name
        command: SSH command (truncated in logs)
        exit_code: Exit code if completed
        stdout: Standard output (truncated)
        stderr: Standard error (truncated)
        duration_ms: Duration in milliseconds
        attempt: Retry attempt number
    """
    if not _logger:
        return

    # Truncate command for readability
    cmd_short = _truncate(command, 100)

    if exit_code is None:
        # Starting operation
        attempt_str = f" (attempt {attempt})" if attempt > 1 else ""
        _logger.info(f"SSH: {operation} -> {pod_name}: {cmd_short}{attempt_str}")
    else:
        # Completed operation
        status = "OK" if exit_code == 0 else f"FAIL({exit_code})"
        duration_str = f" ({duration_ms}ms)" if duration_ms else ""
        _logger.info(f"SSH: {operation} -> {pod_name}: {status}{duration_str}")

        # Log output at debug level
        if _log_level == "debug":
            if stdout and stdout.strip():
                _logger.debug(f"SSH stdout: {_truncate(stdout, 500)}")
            if stderr and stderr.strip():
                _logger.debug(f"SSH stderr: {_truncate(stderr, 500)}")


def log_api(
    operation: str,
    *,
    variables: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Log API operation.

    Args:
        operation: API operation name (e.g., 'create_pod', 'list_pods')
        variables: Request variables (sensitive values masked)
        response: Response data (truncated)
        error: Error message if failed
        duration_ms: Duration in milliseconds
    """
    if not _logger:
        return

    if variables is not None and response is None and error is None:
        # Starting operation
        safe_vars = _mask_sensitive(variables) if variables else {}
        vars_str = ", ".join(f"{k}={v}" for k, v in safe_vars.items())
        _logger.info(f"API: {operation}({vars_str})")
    elif error:
        # Failed operation
        duration_str = f" ({duration_ms}ms)" if duration_ms else ""
        _logger.error(f"API: {operation} -> ERROR: {error}{duration_str}")
    else:
        # Completed operation
        duration_str = f" ({duration_ms}ms)" if duration_ms else ""
        _logger.info(f"API: {operation} -> OK{duration_str}")

        if _log_level == "debug" and response:
            _logger.debug(f"API response: {_truncate(str(response), 500)}")


def log_error(message: str, exc: Optional[Exception] = None) -> None:
    """Log an error.

    Args:
        message: Error message
        exc: Optional exception for stack trace
    """
    if not _logger:
        return

    if exc:
        _logger.error(f"ERROR: {message}", exc_info=exc)
    else:
        _logger.error(f"ERROR: {message}")


def log_warning(message: str) -> None:
    """Log a warning.

    Args:
        message: Warning message
    """
    if not _logger:
        return

    _logger.warning(f"WARN: {message}")


def log_debug(message: str) -> None:
    """Log a debug message.

    Args:
        message: Debug message
    """
    if not _logger:
        return

    _logger.debug(message)


@contextmanager
def log_operation(name: str):
    """Context manager for timing operations.

    Usage:
        with log_operation("create_pod") as op:
            # do work
            op.set_result(success=True, details="pod_id=xyz")
    """
    start = time.monotonic()
    result: dict[str, Any] = {"success": None, "details": None}

    class OperationContext:
        def set_result(self, success: bool, details: str = "") -> None:
            result["success"] = success
            result["details"] = details

    ctx = OperationContext()
    try:
        yield ctx
    except Exception as e:
        result["success"] = False
        result["details"] = str(e)
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        if _logger:
            status = "OK" if result["success"] else "FAIL"
            details_str = f": {result['details']}" if result["details"] else ""
            _logger.info(f"OP: {name} -> {status} ({duration_ms}ms){details_str}")


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _mask_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive values in a dict."""
    sensitive_keys = {"api_key", "apikey", "password", "token", "secret", "key"}
    result = {}
    for k, v in data.items():
        if any(s in k.lower() for s in sensitive_keys):
            result[k] = "***"
        elif isinstance(v, str) and len(v) > 50:
            result[k] = _truncate(v, 50)
        else:
            result[k] = v
    return result
