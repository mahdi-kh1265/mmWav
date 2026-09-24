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


# ---------------------------------------------------------------------------
# Reset FPGA success classification regression (live bug 2026-09-23)
# ---------------------------------------------------------------------------

class TestResetFpgaClassification:
    """Regression: TI CLI emits 'Reset FPGA command : Success' on actual
    FPGA reset, which differs from 'Reset FPGA command sent'.  Both must
    classify as success.  Failure keywords must still take precedence.
    """

    def test_a_reset_fpga_success_string_is_success(self, cli: DcaCli):
        """A – rc=0 + 'Reset FPGA command : Success' → success=True."""
        proc = _make_proc_result(0, "Reset FPGA command : Success\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is True

    def test_a2_reset_fpga_command_sent_still_success(self, cli: DcaCli):
        """Existing 'Reset FPGA command sent' variant still works."""
        proc = _make_proc_result(0, "Reset FPGA command sent\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is True

    def test_b_reset_timeout_disconnected_is_failure(self, cli: DcaCli):
        """B – 'Timeout Error! System disconnected' → success=False."""
        proc = _make_proc_result(
            4294967291,
            "Reset FPGA :\nTimeout Error! System disconnected\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is False

    def test_c_failure_keyword_overrides_success_pattern(self, cli: DcaCli):
        """C – stdout with both success and failure keywords → failure wins."""
        proc = _make_proc_result(
            0,
            "Reset FPGA command : Success\nError: checksum mismatch\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is False

    def test_c2_disconnected_overrides_success_pattern(self, cli: DcaCli):
        """C variant – 'disconnected' overrides any success pattern."""
        proc = _make_proc_result(
            0,
            "Reset FPGA command : Success but System disconnected\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is False

    def test_d_existing_configure_fpga_unchanged(self, cli: DcaCli):
        """D – configure_fpga classification unchanged."""
        proc = _make_proc_result(0, "Record FPGA Configure command\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_fpga()
        assert result.success is True

    def test_d_existing_query_sys_status_unchanged(self, cli: DcaCli):
        """D – query_sys_status classification unchanged."""
        proc = _make_proc_result(0, "System is connected.\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.query_sys_status()
        assert result.success is True

    def test_d_existing_fpga_version_unchanged(self, cli: DcaCli):
        """D – fpga_version classification unchanged."""
        proc = _make_proc_result(1154, "FPGA Version : 2.9 [Record]\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.fpga_version()
        assert result.success is True

    def test_configure_fpga_success_variant(self, cli: DcaCli):
        """'FPGA Configuration command : Success' → success=True."""
        proc = _make_proc_result(0, "FPGA Configuration command : Success\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_fpga()
        assert result.success is True

    def test_configure_fpga_success_with_failure_keyword(self, cli: DcaCli):
        """Failure keyword overrides configure_fpga success variant."""
        proc = _make_proc_result(
            0, "FPGA Configuration command : Success\nError: CRC\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_fpga()
        assert result.success is False

    def test_unknown_stdout_still_fails(self, cli: DcaCli):
        """Unrecognised stdout without failure keywords → success=False."""
        proc = _make_proc_result(0, "Something completely unexpected\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.reset_fpga()
        assert result.success is False

    def test_configure_record_success_variant(self, cli: DcaCli):
        """'Configure Record command : Success' → success=True."""
        proc = _make_proc_result(0, "Configure Record command : Success\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_record()
        assert result.success is True

    def test_configure_record_existing_pattern_still_works(self, cli: DcaCli):
        """Existing 'Record delay configured' variant still works."""
        proc = _make_proc_result(0, "Record delay configured\n")
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_record()
        assert result.success is True

    def test_configure_record_with_failure_keyword(self, cli: DcaCli):
        """Failure keyword overrides configure_record success variant."""
        proc = _make_proc_result(
            0, "Configure Record command : Success\nError: register\n",
        )
        with patch("subprocess.run", return_value=proc):
            result = cli.configure_record()
        assert result.success is False

    def test_all_three_success_variants_coexist(self, cli: DcaCli):
        """All three live : Success patterns are independently recognized."""
        cases = [
            ("reset_fpga", "Reset FPGA command : Success"),
            ("configure_fpga", "FPGA Configuration command : Success"),
            ("configure_record", "Configure Record command : Success"),
        ]
        for method_name, stdout in cases:
            proc = _make_proc_result(0, stdout + "\n")
            with patch("subprocess.run", return_value=proc):
                result = getattr(cli, method_name)()
            assert result.success is True, f"{method_name} with '{stdout}' should be success"


# ---------------------------------------------------------------------------
# Path-Length Tests (TI CLI argv[2] 98-char limit)
# ---------------------------------------------------------------------------

class TestTiCliPathLength:
    """Regression tests for TI's DCA1000EVM_CLI_Control.exe 98-char path limit."""

    def test_constant_is_98(self):
        """TI_CLI_MAX_ARG_PATH_LEN must be 98 (empirically verified)."""
        from awr2944_dca.dca_cli import TI_CLI_MAX_ARG_PATH_LEN
        assert TI_CLI_MAX_ARG_PATH_LEN == 98

    def test_make_temp_cf_json_within_limit(self, tmp_path):
        """make_temp_cf_json produces paths <= 98 chars."""
        from awr2944_dca.dca_cli import TI_CLI_MAX_ARG_PATH_LEN

        # Create a minimal source cf.json
        src_cf = tmp_path / "cf.json"
        src_cf.write_text(
            '{"DCA1000Config":{"captureConfig":{"fileBasePath":"D:\\\\old"}}}',
            encoding="utf-8",
        )

        result = DcaCli.make_temp_cf_json(src_cf, file_base_path="C:\\test")
        try:
            assert result.exists()
            assert len(str(result)) <= TI_CLI_MAX_ARG_PATH_LEN
            # Verify it's valid JSON
            import json
            with open(result) as f:
                data = json.load(f)
            assert "DCA1000Config" in data
        finally:
            result.unlink(missing_ok=True)

    def test_make_temp_cf_json_unique_per_call(self, tmp_path):
        """Each call produces a unique filename."""
        src_cf = tmp_path / "cf.json"
        src_cf.write_text(
            '{"DCA1000Config":{"captureConfig":{"fileBasePath":"D:\\\\old"}}}',
            encoding="utf-8",
        )

        paths = set()
        for _ in range(10):
            p = DcaCli.make_temp_cf_json(src_cf, file_base_path="C:\\test")
            paths.add(str(p))
            p.unlink(missing_ok=True)

        assert len(paths) == 10, "10 calls should produce 10 unique paths"

    def test_make_temp_cf_json_preserves_customization(self, tmp_path):
        """The customized fileBasePath is correctly set in the output."""
        import json

        src_cf = tmp_path / "cf.json"
        src_cf.write_text(
            '{"DCA1000Config":{"captureConfig":{"fileBasePath":"D:\\\\old","filePrefix":"lua_check"}}}',
            encoding="utf-8",
        )

        new_base = "C:\\Users\\test\\captures"
        result = DcaCli.make_temp_cf_json(src_cf, file_base_path=new_base)
        try:
            with open(result) as f:
                data = json.load(f)
            # The JSON stores the escaped version; when parsed back, the
            # backslashes are present as-is (double-escaped in JSON source).
            fbp = data["DCA1000Config"]["captureConfig"]["fileBasePath"]
            # The copy_and_customize_config method double-escapes, so after
            # json.load, the value contains literal double-backslashes.
            assert "test" in fbp
            assert "captures" in fbp
        finally:
            result.unlink(missing_ok=True)

    def test_make_temp_cf_json_rejects_excessive_path(self, tmp_path, monkeypatch):
        """ValueError raised if generated path exceeds TI limit."""
        from awr2944_dca.dca_cli import TI_CLI_MAX_ARG_PATH_LEN

        src_cf = tmp_path / "cf.json"
        src_cf.write_text(
            '{"DCA1000Config":{"captureConfig":{"fileBasePath":"D:\\\\old"}}}',
            encoding="utf-8",
        )

        # Monkeypatch tempfile.gettempdir to return a very long path
        long_dir = "C:\\" + "x" * 100
        monkeypatch.setattr("tempfile.gettempdir", lambda: long_dir)

        with pytest.raises(ValueError, match="path too long"):
            DcaCli.make_temp_cf_json(src_cf, file_base_path="C:\\test")

    def test_copy_and_customize_never_modifies_source(self, tmp_path):
        """copy_and_customize_config must not modify the source cf.json."""
        import json

        src_cf = tmp_path / "cf.json"
        original = '{"DCA1000Config":{"captureConfig":{"fileBasePath":"D:\\\\old"}}}'
        src_cf.write_text(original, encoding="utf-8")
        original_bytes = src_cf.read_bytes()

        dst_cf = tmp_path / "out" / "cf_copy.json"
        DcaCli.copy_and_customize_config(
            src_cf, dst_cf,
            file_base_path="C:\\new_path",
        )

        assert src_cf.read_bytes() == original_bytes
