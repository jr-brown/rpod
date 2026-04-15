"""SSH connection management with retry logic."""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rpod.registry import PodInfo


@dataclass
class SSHResult:
    """Result of an SSH command."""

    success: bool
    returncode: int
    stdout: str
    stderr: str


class SSHConnection:
    """SSH connection to a pod with retry logic."""

    def __init__(
        self,
        pod: PodInfo,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        timeout: int = 30,
    ) -> None:
        self.pod = pod
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout

    def _build_ssh_cmd(
        self,
        command: Optional[str] = None,
        force_tty: bool = False,
    ) -> list[str]:
        """Build SSH command."""
        cmd = ["ssh"]
        if force_tty:
            cmd.append("-t")
        cmd += self.pod.ssh_opts + [self.pod.ssh_host]
        if command:
            cmd.append(command)
        return cmd

    def run(
        self,
        command: str,
        capture: bool = True,
        timeout: Optional[int] = None,
    ) -> SSHResult:
        """Run a command over SSH with retry logic.

        Retries on connection failures (exit code 255) with exponential backoff.
        Does not retry on command failures (non-255 exit codes).
        """
        from rpod.logging import log_ssh

        if timeout is None:
            timeout = self.timeout

        cmd = self._build_ssh_cmd(command)
        start_time = time.monotonic()

        for attempt in range(self.max_retries):
            # Log attempt (especially useful for retries)
            log_ssh("run", self.pod.name, command, attempt=attempt + 1)

            try:
                if capture:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                    )
                else:
                    result = subprocess.run(
                        cmd,
                        timeout=timeout,
                    )
                    # When not capturing, create a minimal result
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    ssh_result = SSHResult(
                        success=result.returncode == 0,
                        returncode=result.returncode,
                        stdout="",
                        stderr="",
                    )
                    log_ssh(
                        "run", self.pod.name, command,
                        exit_code=result.returncode,
                        duration_ms=duration_ms,
                    )
                    return ssh_result

                if result.returncode == 0:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    ssh_result = SSHResult(
                        success=True,
                        returncode=0,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
                    log_ssh(
                        "run", self.pod.name, command,
                        exit_code=0,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        duration_ms=duration_ms,
                    )
                    return ssh_result

                # Exit code 255 = connection failed, retry
                if result.returncode == 255:
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2**attempt)
                        time.sleep(delay)
                        continue

                # Other exit codes = command failed, don't retry
                duration_ms = int((time.monotonic() - start_time) * 1000)
                ssh_result = SSHResult(
                    success=False,
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                log_ssh(
                    "run", self.pod.name, command,
                    exit_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration_ms=duration_ms,
                )
                return ssh_result

            except subprocess.TimeoutExpired:
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    time.sleep(delay)
                    continue
                duration_ms = int((time.monotonic() - start_time) * 1000)
                ssh_result = SSHResult(
                    success=False,
                    returncode=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                )
                log_ssh(
                    "run", self.pod.name, command,
                    exit_code=-1,
                    stderr=f"Timeout after {timeout}s",
                    duration_ms=duration_ms,
                )
                return ssh_result

        # Should not reach here, but just in case
        duration_ms = int((time.monotonic() - start_time) * 1000)
        ssh_result = SSHResult(
            success=False,
            returncode=255,
            stdout="",
            stderr="Max retries exceeded",
        )
        log_ssh(
            "run", self.pod.name, command,
            exit_code=255,
            stderr="Max retries exceeded",
            duration_ms=duration_ms,
        )
        return ssh_result

    def test_connection(self, timeout: int = 10) -> bool:
        """Test if SSH connection works."""
        result = self.run("echo ok", timeout=timeout)
        return result.success and "ok" in result.stdout

    def interactive(self, fix_tty: bool = True) -> int:
        """Start an interactive SSH session.

        If fix_tty is True, sets TERM and runs reset to fix broken TTYs.
        Returns the exit code of the SSH process.
        """
        command = None
        if fix_tty:
            command = "bash -lc 'export TERM=xterm-256color; reset >/dev/null 2>&1 || true; exec bash -l'"
        cmd = self._build_ssh_cmd(command, force_tty=True)
        result = subprocess.run(cmd)
        return result.returncode

    def _ssh_string(self) -> str:
        """Build SSH command string for rsync's -e flag."""
        key = Path(self.pod.key_path).expanduser()
        return (
            f"ssh -i {key} -p {self.pod.port} "
            f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "
            f"-o ServerAliveInterval=30 -o ServerAliveCountMax=10"
        )

    def rsync_push(
        self,
        local_path: Path,
        remote_path: str,
        excludes: Optional[list[str]] = None,
        timeout: int = 300,
        delete: bool = False,
        dry_run: bool = False,
    ) -> SSHResult:
        """Push local directory to pod using rsync over SSH.

        Uses rsync -rlptDz for efficient incremental sync.
        Shows progress during transfer.

        Args:
            delete: If True, delete remote files not present locally (rsync --delete).
            dry_run: If True, show what would be transferred without doing it (rsync -n).
        """
        if excludes is None:
            excludes = []

        local_path = Path(local_path).resolve()
        if not local_path.exists():
            return SSHResult(
                success=False,
                returncode=1,
                stdout="",
                stderr=f"Local path does not exist: {local_path}",
            )

        # Build rsync command
        # Use -rlptDz instead of -az to avoid chown failures on mounted volumes
        # Add --progress for user feedback
        cmd = [
            "rsync", "-rlptDz", "--progress",
            "-e", self._ssh_string(),
        ]
        if delete:
            cmd.append("--delete")
        if dry_run:
            cmd.append("--dry-run")
        for exc in excludes:
            cmd.extend(["--exclude", exc])

        # Trailing slash on source means "contents of directory"
        cmd.append(f"{local_path}/")
        cmd.append(f"{self.pod.ssh_host}:{remote_path}/")

        try:
            # Don't capture output so progress is shown to user
            result = subprocess.run(
                cmd,
                timeout=timeout,
            )
            return SSHResult(
                success=result.returncode == 0,
                returncode=result.returncode,
                stdout="",
                stderr="" if result.returncode == 0 else "rsync failed",
            )
        except subprocess.TimeoutExpired:
            return SSHResult(
                success=False,
                returncode=-1,
                stdout="",
                stderr=f"Sync timed out after {timeout}s",
            )

    def rsync_pull(
        self,
        remote_path: str,
        local_path: Path,
        timeout: int = 300,
    ) -> SSHResult:
        """Pull remote directory from pod using rsync over SSH.

        Shows progress during transfer.
        """
        local_path = Path(local_path).resolve()
        local_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            "rsync", "-rlptDz", "--progress",
            "-e", self._ssh_string(),
            f"{self.pod.ssh_host}:{remote_path}/",
            f"{local_path}/",
        ]

        try:
            # Don't capture output so progress is shown to user
            result = subprocess.run(
                cmd,
                timeout=timeout,
            )
            return SSHResult(
                success=result.returncode == 0,
                returncode=result.returncode,
                stdout="",
                stderr="" if result.returncode == 0 else "rsync failed",
            )
        except subprocess.TimeoutExpired:
            return SSHResult(
                success=False,
                returncode=-1,
                stdout="",
                stderr=f"Pull timed out after {timeout}s",
            )
