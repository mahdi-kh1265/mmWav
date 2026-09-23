"""
DCA1000 CLI subprocess wrapper for headless radar capture.

Safe wrapper around TI's DCA1000EVM_CLI_Control.exe and DCA1000EVM_CLI_Record.exe.
All commands are logged with timestamps, stdout/stderr are captured,
and return codes are checked.

CLI syntax verified from installed binaries:

DCA1000EVM_CLI_Control.exe commands:
    fpga <cf.json>           — Configure FPGA
    eeprom <cf.json>         — Update EEPROM
    reset_fpga               — Reset FPGA
    reset_ar_device          — Reset AR Device
    start_record <cf.json>   — Start Record
    stop_record <cf.json>    — Stop Record
    record <cf.json>         — Configure Record delay
    dll_version              — Read DLL version
    cli_version              — Read CLI_Control tool version
    fpga_version             — Read FPGA version
    query_status <cf.json>   — Read status of record process
    query_sys_status <cf.json> — DCA1000EVM System aliveness

DCA1000EVM_CLI_Record.exe commands:
    start_record             — Start Record (long-running, blocks)
    dll_version              — Read DLL version
    cli_version              — Read CLI_Record tool version

Does NOT depend on mmWave Studio, RSTD, Lua, pywinauto, or GUI output reading.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DcaCmdResult:
    """Result of a DCA CLI subprocess execution."""
    command: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    success: bool
    elapsed_s: float
    timestamp: str = ""
    exe_path: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class StartRecordResult:
    """Structured result from start_record(), which spawns a background recorder."""
    control_pid: int | None
    control_return_code: int | None
    control_exited: bool
    record_pid: int | None
    query_status_text: str
    recording_active: bool
    stdout_log: str
    stderr_log: str
    ti_cli_log_paths: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class DcaConfigIssue:
    """Validation issue for a DCA cf.json configuration."""
    level: str  # "error", "warning", "info"
    field: str
    message: str


# ---------------------------------------------------------------------------
# DCA CLI Wrapper
# ---------------------------------------------------------------------------

class DcaCli:
    """Safe subprocess wrapper for TI DCA1000 CLI tools.

    All operations are logged. No command is executed with shell=True.
    No silent retries. Descriptive exceptions on failure.
    """

    def __init__(
        self,
        control_exe: str | Path,
        record_exe: str | Path,
        rf_api_dll: str | Path,
        cf_json_path: str | Path,
        working_dir: str | Path | None = None,
    ):
        self._control_exe = Path(control_exe)
        self._record_exe = Path(record_exe)
        self._rf_api_dll = Path(rf_api_dll)
        self._cf_json = Path(cf_json_path)
        # CRITICAL: Do NOT use control_exe.parent as the working directory.
        # TI's DCA1000EVM_CLI_Control.exe conflicts with RF_API.dll when cwd
        # is the same directory as the executable — query_sys_status hangs for
        # ~10s and returns "System is disconnected" even when hardware is fine.
        # Any other directory works correctly.
        self._working_dir = Path(working_dir) if working_dir else Path(tempfile.gettempdir())
        self._transcript: list[DcaCmdResult] = []
        self._dry_run = False

    @classmethod
    def from_toolchain(cls, toolchain: dict, cf_json_path: str | Path) -> "DcaCli":
        """Create from a toolchain.local.json dict."""
        return cls(
            control_exe=toolchain["dca_cli_control_exe"],
            record_exe=toolchain["dca_cli_record_exe"],
            rf_api_dll=toolchain.get("rf_api_dll", ""),
            cf_json_path=cf_json_path,
            working_dir=None,
        )

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._dry_run = value

    @property
    def transcript(self) -> list[DcaCmdResult]:
        return list(self._transcript)

    # -- Read-only status commands ------------------------------------------

    def status(self) -> dict:
        """Return status of the DCA CLI tools and configuration."""
        return {
            "control_exe": str(self._control_exe),
            "control_exe_exists": self._control_exe.exists(),
            "record_exe": str(self._record_exe),
            "record_exe_exists": self._record_exe.exists(),
            "rf_api_dll": str(self._rf_api_dll),
            "rf_api_dll_exists": self._rf_api_dll.exists(),
            "cf_json": str(self._cf_json),
            "cf_json_exists": self._cf_json.exists(),
            "working_dir": str(self._working_dir),
            "dry_run": self._dry_run,
            "commands_executed": len(self._transcript),
        }

    def render_config(self) -> dict:
        """Read and return the current cf.json contents."""
        if not self._cf_json.exists():
            raise FileNotFoundError(f"DCA config not found: {self._cf_json}")
        return json.loads(self._cf_json.read_text(encoding="utf-8"))

    def validate_config(self, *, expected_host_ip: str = "192.168.33.30",
                        expected_dca_ip: str = "192.168.33.180",
                        expected_config_port: int = 4096,
                        expected_data_port: int = 4098) -> list[DcaConfigIssue]:
        """Validate cf.json against expected network settings."""
        issues = []

        if not self._cf_json.exists():
            issues.append(DcaConfigIssue("error", "cf_json", f"File not found: {self._cf_json}"))
            return issues

        try:
            cfg = json.loads(self._cf_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            issues.append(DcaConfigIssue("error", "cf_json", f"Invalid JSON: {e}"))
            return issues

        dca_cfg = cfg.get("DCA1000Config", {})

        # Check data transfer mode
        if dca_cfg.get("dataTransferMode") != "LVDSCapture":
            issues.append(DcaConfigIssue(
                "warning", "dataTransferMode",
                f"Expected 'LVDSCapture', got '{dca_cfg.get('dataTransferMode')}'",
            ))

        # Check ethernet config
        eth = dca_cfg.get("ethernetConfig", {})
        if eth.get("DCA1000IPAddress") != expected_dca_ip:
            issues.append(DcaConfigIssue(
                "error", "DCA1000IPAddress",
                f"Expected '{expected_dca_ip}', got '{eth.get('DCA1000IPAddress')}'",
            ))
        if eth.get("DCA1000ConfigPort") != expected_config_port:
            issues.append(DcaConfigIssue(
                "error", "DCA1000ConfigPort",
                f"Expected {expected_config_port}, got {eth.get('DCA1000ConfigPort')}",
            ))
        if eth.get("DCA1000DataPort") != expected_data_port:
            issues.append(DcaConfigIssue(
                "error", "DCA1000DataPort",
                f"Expected {expected_data_port}, got {eth.get('DCA1000DataPort')}",
            ))

        # Check capture config
        cap = dca_cfg.get("captureConfig", {})
        base_path = cap.get("fileBasePath", "")
        if not base_path:
            issues.append(DcaConfigIssue("error", "fileBasePath", "Empty output path"))
        elif not Path(base_path.replace("\\\\", "\\")).exists():
            issues.append(DcaConfigIssue(
                "warning", "fileBasePath",
                f"Output directory does not exist: {base_path}",
            ))

        # Check for overwrite risk
        prefix = cap.get("filePrefix", "adc_data")
        out_dir = Path(base_path.replace("\\\\", "\\"))
        if out_dir.exists():
            existing = list(out_dir.glob(f"{prefix}*"))
            if existing:
                issues.append(DcaConfigIssue(
                    "warning", "filePrefix",
                    f"Output directory already contains {len(existing)} file(s) matching '{prefix}*'",
                ))

        return issues

    # -- Hardware commands --------------------------------------------------

    def cli_version(self) -> DcaCmdResult:
        """Read CLI_Control tool version (safe, no hardware effect)."""
        return self._run_control("cli_version", needs_json=False)

    def dll_version(self) -> DcaCmdResult:
        """Read DLL version (safe, no hardware effect)."""
        return self._run_control("dll_version", needs_json=False)

    def query_sys_status(self) -> DcaCmdResult:
        """Check DCA1000EVM system aliveness."""
        return self._run_control("query_sys_status")

    def configure_fpga(self) -> DcaCmdResult:
        """Configure the DCA1000 FPGA using cf.json settings."""
        return self._run_control("fpga")

    def configure_record(self) -> DcaCmdResult:
        """Configure record delay settings."""
        return self._run_control("record")

    def start_record(
        self,
        stdout_log_path: str | Path | None = None,
        stderr_log_path: str | Path | None = None,
        arm_timeout_s: float = 10.0,
    ) -> StartRecordResult:
        """Start DCA1000 recording via CLI_Control using Popen.

        Uses subprocess.Popen with file-based stdout/stderr redirection
        to avoid pipe-inheritance blocking.  CLI_Control spawns
        CLI_Record as a child; if we use subprocess.PIPE, communicate()
        blocks until CLI_Record exits (which may be 30+ seconds).

        After launching, polls query_status independently to confirm the
        recorder is actually active.
        """
        t_start = time.time()

        if self._dry_run:
            return StartRecordResult(
                control_pid=None, control_return_code=0,
                control_exited=True, record_pid=None,
                query_status_text="[DRY RUN]",
                recording_active=True,
                stdout_log="", stderr_log="",
                elapsed_s=0.0,
            )

        # Resolve log paths
        if stdout_log_path is None:
            stdout_log_path = self._working_dir / "start_record_stdout.log"
        if stderr_log_path is None:
            stderr_log_path = self._working_dir / "start_record_stderr.log"
        stdout_log_path = Path(stdout_log_path)
        stderr_log_path = Path(stderr_log_path)
        stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_log_path.parent.mkdir(parents=True, exist_ok=True)

        args = [str(self._control_exe), "start_record", str(self._cf_json)]
        logger.info("start_record: launching %s (cwd=%s)", args, self._working_dir)

        control_proc = None
        try:
            with open(stdout_log_path, "ab", buffering=0) as out, \
                 open(stderr_log_path, "ab", buffering=0) as err:

                control_proc = subprocess.Popen(
                    args,
                    cwd=str(self._working_dir),
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
                logger.info("start_record: CLI_Control PID=%d", control_proc.pid)

                # Wait for CLI_Control to exit (it should return after
                # CLI_Record confirms arming via shared memory)
                try:
                    control_proc.wait(timeout=arm_timeout_s)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "start_record: CLI_Control PID=%d did not exit within %.1fs",
                        control_proc.pid, arm_timeout_s,
                    )

        except (FileNotFoundError, OSError) as e:
            elapsed = time.time() - t_start
            return StartRecordResult(
                control_pid=control_proc.pid if control_proc else None,
                control_return_code=None,
                control_exited=False,
                record_pid=None,
                query_status_text="",
                recording_active=False,
                stdout_log=str(stdout_log_path),
                stderr_log=str(stderr_log_path),
                elapsed_s=round(elapsed, 3),
                error=f"Failed to launch CLI_Control: {e}",
            )

        # Read stdout log for success message
        stdout_text = ""
        try:
            stdout_text = stdout_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

        # Query status independently
        qs_result = self.query_status()
        qs_text = qs_result.stdout
        recording_active = (
            "in progress" in qs_text.lower()
            or "recording" in qs_text.lower()
            or "started" in qs_text.lower()
        )

        # Try to find CLI_Record process
        record_pid = self.find_record_process()

        elapsed = time.time() - t_start
        result = StartRecordResult(
            control_pid=control_proc.pid if control_proc else None,
            control_return_code=control_proc.returncode if control_proc else None,
            control_exited=control_proc.returncode is not None if control_proc else False,
            record_pid=record_pid,
            query_status_text=qs_text,
            recording_active=recording_active,
            stdout_log=str(stdout_log_path),
            stderr_log=str(stderr_log_path),
            elapsed_s=round(elapsed, 3),
        )

        # Log to transcript as DcaCmdResult for compatibility
        self._transcript.append(DcaCmdResult(
            command="start_record",
            args=args,
            returncode=control_proc.returncode if control_proc and control_proc.returncode is not None else -1,
            stdout=stdout_text.strip(),
            stderr="",
            success=recording_active,
            elapsed_s=result.elapsed_s,
            exe_path=str(self._control_exe),
        ))

        logger.info(
            "start_record: control_pid=%s rc=%s record_pid=%s recording_active=%s elapsed=%.1fs",
            result.control_pid, result.control_return_code,
            result.record_pid, result.recording_active, result.elapsed_s,
        )
        return result

    def find_record_process(self) -> int | None:
        """Find the PID of a running DCA1000EVM_CLI_Record.exe process.

        Uses tasklist on Windows. Returns PID or None.
        """
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq DCA1000EVM_CLI_Record.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in proc.stdout.strip().splitlines():
                if "DCA1000EVM_CLI_Record" in line:
                    parts = line.replace('"', '').split(",")
                    if len(parts) >= 2:
                        return int(parts[1])
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return None

    def wait_until_recording(self, timeout_s: float = 10.0, poll_interval_s: float = 0.5) -> bool:
        """Poll query_status until the recorder reports active, or timeout.

        Returns True if recording became active within the timeout.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            qs = self.query_status()
            text = qs.stdout.lower()
            if "in progress" in text or "recording" in text or "started" in text:
                logger.info("wait_until_recording: recorder is active")
                return True
            time.sleep(poll_interval_s)
        logger.warning("wait_until_recording: timed out after %.1fs", timeout_s)
        return False

    def wait_until_stopped(self, timeout_s: float = 25.0, poll_interval_s: float = 1.0) -> bool:
        """Poll query_status until the recorder reports stopped, or timeout.

        Returns True if recording stopped within the timeout.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            qs = self.query_status()
            text = qs.stdout.lower()
            if "no record process" in text or "stopped" in text:
                logger.info("wait_until_stopped: recorder has stopped")
                return True
            time.sleep(poll_interval_s)
        logger.warning("wait_until_stopped: timed out after %.1fs", timeout_s)
        return False

    def query_status(self) -> DcaCmdResult:
        """Query the status of the current record process."""
        return self._run_control("query_status")

    def stop_record(self) -> DcaCmdResult:
        """Stop DCA1000 recording."""
        return self._run_control("stop_record")

    def fpga_version(self) -> DcaCmdResult:
        """Read FPGA firmware version."""
        return self._run_control("fpga_version")

    def reset_fpga(self) -> DcaCmdResult:
        """Reset FPGA."""
        return self._run_control("reset_fpga")

    # -- Config file management ---------------------------------------------

    @staticmethod
    def copy_and_customize_config(
        source_cf_json: str | Path,
        dest_cf_json: str | Path,
        *,
        file_base_path: str | None = None,
        file_prefix: str | None = None,
        capture_stop_mode: str | None = None,
        frames_to_capture: int | None = None,
        duration_to_capture_ms: int | None = None,
        bytes_to_capture: int | None = None,
    ) -> dict:
        """Copy a cf.json and customize capture settings.

        Never modifies the source file. Returns a report of changes made.
        """
        source = Path(source_cf_json)
        dest = Path(dest_cf_json)
        dest.parent.mkdir(parents=True, exist_ok=True)

        src_bytes = source.read_bytes()
        src_sha = hashlib.sha256(src_bytes).hexdigest().upper()

        cfg = json.loads(src_bytes.decode("utf-8"))
        changes = []

        cap = cfg.get("DCA1000Config", {}).get("captureConfig", {})

        if file_base_path is not None:
            old = cap.get("fileBasePath")
            # Escape backslashes for JSON
            cap["fileBasePath"] = file_base_path.replace("\\", "\\\\")
            changes.append({"field": "fileBasePath", "old": old, "new": file_base_path})

        if file_prefix is not None:
            old = cap.get("filePrefix")
            cap["filePrefix"] = file_prefix
            changes.append({"field": "filePrefix", "old": old, "new": file_prefix})

        if capture_stop_mode is not None:
            old = cap.get("captureStopMode")
            cap["captureStopMode"] = capture_stop_mode
            changes.append({"field": "captureStopMode", "old": old, "new": capture_stop_mode})

        if frames_to_capture is not None:
            old = cap.get("framesToCapture")
            cap["framesToCapture"] = frames_to_capture
            changes.append({"field": "framesToCapture", "old": old, "new": frames_to_capture})

        if duration_to_capture_ms is not None:
            old = cap.get("durationToCapture_ms")
            cap["durationToCapture_ms"] = duration_to_capture_ms
            changes.append({"field": "durationToCapture_ms", "old": old, "new": duration_to_capture_ms})

        if bytes_to_capture is not None:
            old = cap.get("bytesToCapture")
            cap["bytesToCapture"] = bytes_to_capture
            changes.append({"field": "bytesToCapture", "old": old, "new": bytes_to_capture})

        dest.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        dest_sha = hashlib.sha256(dest.read_bytes()).hexdigest().upper()

        return {
            "source_path": str(source),
            "source_sha256": src_sha,
            "dest_path": str(dest),
            "dest_sha256": dest_sha,
            "changes": changes,
        }

    # -- Transcript ---------------------------------------------------------

    def save_transcript(self, path: Path) -> Path:
        """Save the execution transcript to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        for r in self._transcript:
            entries.append({
                "command": r.command,
                "args": r.args,
                "returncode": r.returncode,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "success": r.success,
                "elapsed_s": r.elapsed_s,
                "timestamp": r.timestamp,
                "exe_path": r.exe_path,
            })
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        return path

    # -- Internal -----------------------------------------------------------

    def _run_control(self, command: str, *, needs_json: bool = True,
                     timeout: float = 30.0) -> DcaCmdResult:
        """Run a DCA1000EVM_CLI_Control.exe command."""
        args = [str(self._control_exe), command]
        if needs_json:
            args.append(str(self._cf_json))

        if self._dry_run:
            result = DcaCmdResult(
                command=command, args=args, returncode=0,
                stdout=f"[DRY RUN] Would execute: {' '.join(args)}",
                stderr="", success=True, elapsed_s=0.0,
                exe_path=str(self._control_exe),
            )
            self._transcript.append(result)
            return result

        return self._execute(args, command, timeout)

    # TI CLI output patterns for output-semantic success detection.
    # TI's DCA1000EVM_CLI_Control.exe uses non-standard exit codes:
    #   - fpga_version always returns 1154 even on success
    #   - query_sys_status returns 0 on success, 4294967291 (-5) on failure
    #   - Other commands vary unpredictably
    # Therefore, success is determined by stdout content, not exit code.
    _TI_SUCCESS_PATTERNS: tuple[str, ...] = (
        "System is connected",
        "FPGA Version",
        "Record process completed",
        "Record FPGA Configure command",
        "EEPROM write successful",
        "Record Start command sent",
        "Record Stop command sent",
        "Reset AR device command sent",
        "Reset FPGA command sent",
        "Record delay configured",
        "DCA1000EVM CLI Record",
        "DCA1000EVM CLI Control",
        "CLI DLL version",
    )
    _TI_FAILURE_PATTERNS: tuple[str, ...] = (
        "disconnected",
        "Error",
        "Failure",
        "Unable",
        "error[",
    )

    def _classify_ti_success(self, stdout: str) -> bool:
        """Determine success from TI CLI stdout, ignoring exit code.

        TI's DCA1000EVM_CLI_Control.exe uses non-standard, unreliable
        process return codes (e.g. fpga_version always returns 1154,
        query_sys_status returns 4294967291 on failure).  We classify
        success exclusively from the output text.

        Returns True only when stdout matches a known-good pattern AND
        does not contain any failure indicator.
        """
        # Failure takes precedence: if any failure pattern matches, fail.
        for pat in self._TI_FAILURE_PATTERNS:
            if pat in stdout:
                return False
        # Then check for a known-good pattern.
        for pat in self._TI_SUCCESS_PATTERNS:
            if pat in stdout:
                return True
        # Unknown output — fall back to exit-code == 0.
        return False

    def _execute(self, args: list[str], command: str,
                 timeout: float) -> DcaCmdResult:
        """Execute a subprocess and capture results."""
        t_start = time.time()
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._working_dir),
                # No shell=True
            )
            elapsed = time.time() - t_start
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            # Determine success from TI CLI output text (not exit code).
            # TI's CLI uses non-standard return codes (e.g. fpga_version
            # always returns 1154 on success).
            success = self._classify_ti_success(stdout)

            result = DcaCmdResult(
                command=command,
                args=args,
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                success=success,
                elapsed_s=round(elapsed, 3),
                exe_path=str(args[0]),
            )

        except subprocess.TimeoutExpired:
            elapsed = time.time() - t_start
            result = DcaCmdResult(
                command=command,
                args=args,
                returncode=-1,
                stdout="",
                stderr=f"TIMEOUT after {timeout}s",
                success=False,
                elapsed_s=round(elapsed, 3),
                exe_path=str(args[0]),
            )

        except FileNotFoundError:
            result = DcaCmdResult(
                command=command,
                args=args,
                returncode=-1,
                stdout="",
                stderr=f"Executable not found: {args[0]}",
                success=False,
                elapsed_s=0.0,
                exe_path=str(args[0]),
            )

        except OSError as e:
            result = DcaCmdResult(
                command=command,
                args=args,
                returncode=-1,
                stdout="",
                stderr=f"OS error: {e}",
                success=False,
                elapsed_s=0.0,
                exe_path=str(args[0]),
            )

        self._transcript.append(result)
        return result

    def __repr__(self) -> str:
        mode = "DRY_RUN" if self._dry_run else "LIVE"
        return f"DcaCli(mode={mode}, cf_json={self._cf_json.name!r})"
