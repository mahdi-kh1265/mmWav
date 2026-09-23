"""Regression tests for TI DCA1000EVM CLI non-standard behaviour.

Covers:
  - CWD conflict: TI CLI fails when cwd == exe.parent (PostProc dir)
  - Non-standard exit codes: fpga_version returns 1154 on success
  - Output-semantic success detection

No live hardware required.  Uses monkeypatching / mocking only.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from awr2944_dca.dca_cli import DcaCli, DcaCmdResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path):
    """Create a DcaCli instance with fake exe/cf.json paths."""
    fake_exe = tmp_path / "PostProc" / "DCA1000EVM_CLI_Control.exe"
    fake_exe.parent.mkdir(parents=True, exist_ok=True)
    fake_exe.write_bytes(b"fake")
    fake_rec = tmp_path / "PostProc" / "DCA1000EVM_CLI_Record.exe"
    fake_rec.write_bytes(b"fake")
    fake_dll = tmp_path / "PostProc" / "RF_API.dll"
    fake_dll.write_bytes(b"fake")
    fake_cf = tmp_path / "PostProc" / "cf.json"
    fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")
    return DcaCli(
        control_exe=str(fake_exe),
        record_exe=str(fake_rec),
        rf_api_dll=str(fake_dll),
        cf_json_path=str(fake_cf),
    )


# ---------------------------------------------------------------------------
# CWD Tests
# ---------------------------------------------------------------------------

class TestCwdNotExeParent:
    """Verify DcaCli does NOT default cwd to control_exe.parent.

    TI's DCA1000EVM_CLI_Control.exe conflicts with RF_API.dll when cwd
    is the PostProc directory.  Diagnostic evidence (2026-09-23):

        cwd = PostProc  -> returncode=4294967291, "System is disconnected", 10s
        cwd = anywhere else -> returncode=0, "System is connected", 0.04s
    """

    def test_default_cwd_is_not_exe_parent(self, cli: DcaCli):
        exe_parent = cli._control_exe.parent
        assert cli._working_dir != exe_parent, (
            f"Default cwd must NOT be exe.parent ({exe_parent}) — "
            "TI CLI DLL conflict causes query_sys_status to hang and fail"
        )

    def test_default_cwd_is_tempdir(self, cli: DcaCli):
        assert cli._working_dir == Path(tempfile.gettempdir())

    def test_explicit_cwd_is_respected(self, tmp_path):
        """User-provided working_dir is used as-is."""
        custom_dir = tmp_path / "my_workdir"
        custom_dir.mkdir()
        cli = DcaCli(
            control_exe="fake.exe",
            record_exe="fake_rec.exe",
            rf_api_dll="fake.dll",
            cf_json_path="fake.json",
            working_dir=str(custom_dir),
        )
        assert cli._working_dir == custom_dir


# ---------------------------------------------------------------------------
# TI Exit Code Regression Tests
# ---------------------------------------------------------------------------

def _make_proc_result(returncode: int, stdout: str, stderr: str = "") -> MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestTiExitCodeBehaviour:
    """Regression tests for TI's non-standard process exit codes.

    Observed on live hardware (2026-09-23):

        query_sys_status  success -> rc=0,   stdout="System is connected."
        query_sys_status  failure -> rc=4294967291 (-5), stdout="System is disconnected."
        fpga_version      success -> rc=1154, stdout="FPGA Version : 2.9 [Record]"
        fpga_version      no json -> rc=4294963247, stdout="Unable to open the JSON file..."
    """

    def test_query_sys_status_connected_is_success(self, cli: DcaCli):
        proc = _make_proc_result(0, "System is connected.\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.query_sys_status()
        assert result.success is True
        assert "connected" in result.stdout.lower()

    def test_query_sys_status_disconnected_is_failure(self, cli: DcaCli):
        proc = _make_proc_result(4294967291, "System is disconnected.\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.query_sys_status()
        assert result.success is False
        assert "disconnected" in result.stdout.lower()

    def test_fpga_version_1154_is_success(self, cli: DcaCli):
        """fpga_version returns exit code 1154 even on success."""
        proc = _make_proc_result(1154, "FPGA Version : 2.9 [Record]\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.fpga_version()
        assert result.success is True, (
            "fpga_version must be success=True when stdout contains "
            "'FPGA Version' regardless of exit code"
        )
        assert result.returncode == 1154
        assert "2.9" in result.stdout

    def test_fpga_version_unable_to_open_is_failure(self, cli: DcaCli):
        """fpga_version without cf.json → 'Unable to open' → failure."""
        proc = _make_proc_result(
            4294963247,
            "Unable to open the JSON file (X). error[-4049]\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.fpga_version()
        assert result.success is False

    def test_cli_version_success(self, cli: DcaCli):
        proc = _make_proc_result(0, "DCA1000EVM CLI Control version 1.0\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.cli_version()
        assert result.success is True

    def test_dll_version_success(self, cli: DcaCli):
        proc = _make_proc_result(0, "CLI DLL version 1.0\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.dll_version()
        assert result.success is True

    def test_stop_record_success(self, cli: DcaCli):
        proc = _make_proc_result(0, "Record Stop command sent successfully\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.stop_record()
        assert result.success is True

    def test_configure_fpga_success(self, cli: DcaCli):
        proc = _make_proc_result(0, "Record FPGA Configure command sent\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_fpga()
        assert result.success is True

    def test_unknown_output_empty_stdout_is_failure(self, cli: DcaCli):
        """Empty stdout with rc=0 → success=False (no known-good pattern)."""
        proc = _make_proc_result(0, "")
        with patch("subprocess.run", return_value=proc):
            result = cli.query_sys_status()
        assert result.success is False

    def test_error_in_stdout_overrides_success_pattern(self, cli: DcaCli):
        """If stdout contains both a success pattern AND a failure keyword,
        failure takes precedence."""
        proc = _make_proc_result(0, "System is connected. Error: partial\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.query_sys_status()
        assert result.success is False


# ---------------------------------------------------------------------------
# DcaFacade Integration — verify + fpga_version via DcaCli
# ---------------------------------------------------------------------------

class TestFacadeIntegrationWithTiExitCodes:
    """Ensure DcaFacade correctly propagates output-semantic success."""

    def test_verify_with_ti_rc0_connected_is_success(self, tmp_path):
        """p.dca.verify() → success when TI CLI returns 'System is connected.'"""
        from awr2944_dca.api._dca_facade import DcaFacade, _dca_cli_from_config

        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        from tests.test_dca_facade import _make_project
        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json=str(fake_cf))

        mock_result = DcaCmdResult(
            command="query_sys_status",
            args=["fake"],
            returncode=0,
            stdout="System is connected.",
            stderr="",
            success=True,  # Our fix classifies this as True
            elapsed_s=0.04,
        )
        with patch("awr2944_dca.dca_cli.DcaCli.query_sys_status", return_value=mock_result):
            result = p.dca.verify()

        assert result.success is True
        assert result.sys_status_ok is True

    def test_fpga_version_facade_with_ti_rc1154(self, tmp_path):
        """p.dca.fpga_version() → success when TI returns rc=1154 + version text."""
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        from tests.test_dca_facade import _make_project
        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json=str(fake_cf))

        mock_result = DcaCmdResult(
            command="fpga_version",
            args=["fake"],
            returncode=1154,
            stdout="FPGA Version : 2.9 [Record]",
            stderr="",
            success=True,  # Our fix classifies this as True
            elapsed_s=0.035,
        )
        with patch("awr2944_dca.dca_cli.DcaCli.fpga_version", return_value=mock_result):
            result = p.dca.fpga_version()

        assert result.success is True
        assert "2.9" in result.version_string
