"""Tests for DcaFacade (Task 3 — p.dca).

No live hardware.  Uses tmp_path + monkeypatching only.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from awr2944_dca.lab import RadarProject
from awr2944_dca.api._dca_facade import (
    DcaFacade,
    DcaStatusResult,
    DcaConfigResult,
    DcaVerifyResult,
    DcaFpgaVersionResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, *, host_ip: str = "", dca_ip: str = "192.168.33.180",
                  config_port: int = 4096, data_port: int = 4098,
                  control_exe: str = "", record_exe: str = "",
                  rf_api_dll: str = "", cf_json: str = "") -> RadarProject:
    """Create a minimal RadarProject with configurable DCA settings."""
    (tmp_path / "awr2944.toml").write_text(
        f'[project]\nname = "test_proj"\nid = "abc"\n\n'
        f'[defaults]\nframes = 9\nguard_frames = 1\nprofile = "smoke_v1"\n\n'
        f'[network]\ndca_ip = {json.dumps(dca_ip)}\n'
        f'config_port = {config_port}\ndata_port = {data_port}\n',
        encoding="utf-8",
    )
    local_dir = tmp_path / ".awr2944"
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "local.toml").write_text(
        f'[serial]\ncom_port = "COM8"\nbaud_rate = 115200\n\n'
        f'[network]\nhost_ip = {json.dumps(host_ip)}\n\n'
        f'[dca_tools]\n'
        f'control_exe = {json.dumps(control_exe)}\n'
        f'record_exe = {json.dumps(record_exe)}\n'
        f'rf_api_dll = {json.dumps(rf_api_dll)}\n'
        f'cf_json = {json.dumps(cf_json)}\n',
        encoding="utf-8",
    )
    (tmp_path / "profiles").mkdir(exist_ok=True)
    (tmp_path / "captures").mkdir(exist_ok=True)
    return RadarProject.open(tmp_path)


def _make_fake_dca_result(success: bool = True, stdout: str = "System Status: OK") -> MagicMock:
    r = MagicMock()
    r.success = success
    r.stdout  = stdout
    r.stderr  = ""
    return r


# ---------------------------------------------------------------------------
# DF-1 — p.dca property
# ---------------------------------------------------------------------------

class TestDcaFacadeProperty:
    def test_DF1_p_dca_returns_dca_facade(self, tmp_path):
        p = _make_project(tmp_path)
        dca = p.dca
        assert dca.__class__.__name__ == "DcaFacade"

    def test_DF1_p_dca_is_cached(self, tmp_path):
        p = _make_project(tmp_path)
        assert p.dca is p.dca  # same instance


# ---------------------------------------------------------------------------
# DF-2 — autodetect_toolchain(save=True) integration
# ---------------------------------------------------------------------------

class TestAutodetectIntegration:
    def test_DF2_autodetect_save_then_status_sees_saved_paths(self, tmp_path):
        """autodetect_toolchain(save=True) → p.dca.status() sees saved paths.
        
        No toolchain.local.json involved.
        """
        p = _make_project(tmp_path)

        # Simulate what autodetect_toolchain(save=True) writes into local.toml
        p._config.local.dca_control_exe = "C:\\TI\\control.exe"
        p._config.local.dca_record_exe  = "C:\\TI\\record.exe"
        p._config.local.rf_api_dll      = "C:\\TI\\RF_API.dll"
        p._config.local.cf_json_path    = "C:\\TI\\cf.json"
        p._config.save()

        # Re-open to ensure we read from disk
        p2 = RadarProject.open(tmp_path)
        st = p2.dca.status()

        assert st.control_exe == "C:\\TI\\control.exe"
        assert st.record_exe  == "C:\\TI\\record.exe"
        assert st.rf_api_dll  == "C:\\TI\\RF_API.dll"
        assert st.cf_json_path == "C:\\TI\\cf.json"
        # Files don't exist on disk, but paths are present
        assert st.control_exe_found is False  # file not actually on disk
        assert st.toolchain_ready is False

    def test_DF13_modern_local_toml_only_no_legacy_file(self, tmp_path):
        """Project with only local.toml paths — no toolchain.local.json — works."""
        p = _make_project(
            tmp_path,
            control_exe="C:\\TI\\control.exe",
            cf_json="C:\\TI\\cf.json",
        )
        # Assert no toolchain.local.json exists
        assert not (tmp_path / "ti" / "headless" / "toolchain.local.json").exists()

        st = p.dca.status()
        assert st.control_exe == "C:\\TI\\control.exe"
        assert st.cf_json_path == "C:\\TI\\cf.json"


# ---------------------------------------------------------------------------
# DF-3, DF-4, DF-5 — fresh project (nothing configured)
# ---------------------------------------------------------------------------

class TestFreshProject:
    @pytest.fixture
    def fresh(self, tmp_path):
        return _make_project(tmp_path)

    def test_DF3_status_fresh_project_no_exception(self, fresh):
        st = fresh.dca.status()
        assert st.__class__.__name__ == "DcaStatusResult"
        assert st.toolchain_ready is False
        assert st.control_exe_found is False
        assert st.cf_json_found is False
        assert st.control_exe == ""
        assert st.cf_json_path == ""

    def test_DF4_config_fresh_project_no_exception(self, fresh):
        cfg = fresh.dca.config()
        assert cfg.__class__.__name__ == "DcaConfigResult"
        assert cfg.cf_json_found is False
        assert cfg.host_ip_consistency    == "cf_json_missing"
        assert cfg.dca_ip_consistency     == "cf_json_missing"
        assert cfg.config_port_consistency == "cf_json_missing"
        assert cfg.data_port_consistency   == "cf_json_missing"

    def test_DF5_verify_fresh_project_no_subprocess(self, fresh):
        with patch("subprocess.run") as mock_run:
            result = fresh.dca.verify()
        mock_run.assert_not_called()
        assert result.__class__.__name__ == "DcaVerifyResult"
        assert result.control_exe_found is False
        assert result.success is False
        assert result.sys_status_detail  # non-empty message


# ---------------------------------------------------------------------------
# DF-6 — fully configured project status
# ---------------------------------------------------------------------------

class TestConfiguredStatus:
    def test_DF6_status_returns_correct_network_fields(self, tmp_path):
        p = _make_project(
            tmp_path,
            host_ip="192.168.33.30",
            dca_ip="192.168.33.180",
            config_port=4096,
            data_port=4098,
            control_exe="C:\\TI\\control.exe",
        )
        st = p.dca.status()
        assert st.host_ip    == "192.168.33.30"
        assert st.dca_ip     == "192.168.33.180"
        assert st.config_port == 4096
        assert st.data_port   == 4098
        assert st.control_exe == "C:\\TI\\control.exe"


# ---------------------------------------------------------------------------
# DF-7 — verify() delegates to DcaCli
# ---------------------------------------------------------------------------

class TestVerifyDelegation:
    def test_DF7_verify_delegates_to_dca_cli_query_sys_status(self, tmp_path):
        """verify() calls DcaCli.query_sys_status exactly once when prereqs met."""
        # Create fake exe + cf.json on disk so prereq checks pass
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf  = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        p = _make_project(
            tmp_path,
            control_exe=str(fake_exe),
            cf_json=str(fake_cf),
        )

        mock_result = _make_fake_dca_result(success=True, stdout="System Status: OK")
        with patch("awr2944_dca.dca_cli.DcaCli.query_sys_status", return_value=mock_result) as mock_qss:
            result = p.dca.verify()

        mock_qss.assert_called_once()
        assert result.__class__.__name__ == "DcaVerifyResult"
        assert result.control_exe_found is True
        assert result.cf_json_found     is True
        assert result.sys_status_ok     is True

    def test_DF7_verify_control_exists_cf_missing_no_subprocess(self, tmp_path):
        """control exe present, cf.json missing → no subprocess, structured failure."""
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")

        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json="")

        with patch("subprocess.run") as mock_run:
            result = p.dca.verify()
        mock_run.assert_not_called()
        assert result.control_exe_found is True
        assert result.cf_json_found     is False
        assert result.success           is False
        assert result.sys_status_detail  # non-empty

    def test_DF7_verify_cf_exists_control_missing_no_subprocess(self, tmp_path):
        """cf.json present, control exe missing → no subprocess, structured failure."""
        fake_cf = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        p = _make_project(tmp_path, control_exe="", cf_json=str(fake_cf))

        with patch("subprocess.run") as mock_run:
            result = p.dca.verify()
        mock_run.assert_not_called()
        assert result.control_exe_found is False
        assert result.cf_json_found     is True
        assert result.success           is False

    def test_DF7_verify_all_prereqs_delegates_exactly_once(self, tmp_path):
        """All prereqs present → DcaCli.query_sys_status called exactly once."""
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf  = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json=str(fake_cf))

        mock_result = _make_fake_dca_result(success=True)
        with patch("awr2944_dca.dca_cli.DcaCli.query_sys_status", return_value=mock_result) as m:
            p.dca.verify()
        assert m.call_count == 1


# ---------------------------------------------------------------------------
# DF-8 — fpga_version() delegates to DcaCli
# ---------------------------------------------------------------------------

class TestFpgaVersionDelegation:
    def test_DF8_fpga_version_delegates_to_dca_cli(self, tmp_path):
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf  = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json=str(fake_cf))

        mock_result = _make_fake_dca_result(success=True, stdout="FPGA_VERSION_02_08")
        with patch("awr2944_dca.dca_cli.DcaCli.fpga_version", return_value=mock_result) as mock_fv:
            result = p.dca.fpga_version()

        mock_fv.assert_called_once()
        assert result.__class__.__name__ == "DcaFpgaVersionResult"
        assert result.success is True
        assert "FPGA_VERSION_02_08" in result.version_string

    def test_DF8_fpga_version_control_missing_no_subprocess(self, tmp_path):
        p = _make_project(tmp_path, control_exe="", cf_json="")
        with patch("subprocess.run") as mock_run:
            result = p.dca.fpga_version()
        mock_run.assert_not_called()
        assert result.success is False
        assert result.control_exe_found is False

    def test_DF8_fpga_version_cf_missing_no_subprocess(self, tmp_path):
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json="")
        with patch("subprocess.run") as mock_run:
            result = p.dca.fpga_version()
        mock_run.assert_not_called()
        assert result.success is False
        assert result.cf_json_found is False


# ---------------------------------------------------------------------------
# DF-9, DF-10 — malformed / missing cf.json
# ---------------------------------------------------------------------------

class TestCfJsonHandling:
    def test_DF9_malformed_cf_json_no_exception(self, tmp_path):
        fake_cf = tmp_path / "cf.json"
        fake_cf.write_text("{NOT VALID JSON", encoding="utf-8")

        p = _make_project(tmp_path, cf_json=str(fake_cf))
        cfg = p.dca.config()
        assert cfg.cf_json_found     is True
        assert cfg.cf_json_parseable is False
        # All consistency states reflect unparseable json
        assert cfg.host_ip_consistency    == "cf_json_missing"

    def test_DF10_missing_cf_json_all_consistency_missing(self, tmp_path):
        p = _make_project(tmp_path)  # cf_json not set
        cfg = p.dca.config()
        assert cfg.cf_json_found is False
        assert cfg.host_ip_consistency    == "cf_json_missing"
        assert cfg.dca_ip_consistency     == "cf_json_missing"
        assert cfg.config_port_consistency == "cf_json_missing"
        assert cfg.data_port_consistency   == "cf_json_missing"


# ---------------------------------------------------------------------------
# DF-11a, DF-11b — status() and config() never call subprocess
# ---------------------------------------------------------------------------

class TestNoSubprocessInReadOnlyMethods:
    def test_DF11a_status_never_calls_subprocess(self, tmp_path):
        p = _make_project(tmp_path, control_exe="C:\\any.exe", cf_json="C:\\any.json")
        with patch("subprocess.run") as mock_run:
            p.dca.status()
        mock_run.assert_not_called()

    def test_DF11b_config_never_calls_subprocess(self, tmp_path):
        p = _make_project(tmp_path, cf_json="C:\\any.json")
        with patch("subprocess.run") as mock_run:
            p.dca.config()
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# DF-12 — verify() failure handled gracefully
# ---------------------------------------------------------------------------

class TestVerifyFailure:
    def test_DF12_verify_subprocess_failure_is_graceful(self, tmp_path):
        fake_exe = tmp_path / "control.exe"
        fake_exe.write_bytes(b"fake")
        fake_cf  = tmp_path / "cf.json"
        fake_cf.write_text('{"DCA1000Config":{}}', encoding="utf-8")

        p = _make_project(tmp_path, control_exe=str(fake_exe), cf_json=str(fake_cf))

        mock_result = _make_fake_dca_result(success=False, stdout="Connection Error")
        with patch("awr2944_dca.dca_cli.DcaCli.query_sys_status", return_value=mock_result):
            result = p.dca.verify()

        assert result.success is False
        assert result.sys_status_detail  # non-empty detail
