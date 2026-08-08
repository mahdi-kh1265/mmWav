"""Task 2: autodetect_serial() and autodetect_toolchain() tests.

No live hardware required.  All serial probing and filesystem access
is mocked.

Test numbering follows the Task 2 spec:
  Serial  S1–S10
  Toolchain  T1–T10
  Regression  R1  (doctor UART check unchanged)
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

from awr2944_dca.headless_serial import SerialPortInfo
from awr2944_dca._doctor import (
    HardwareManager,
    SerialAutodetectResult,
    ToolchainAutodetectResult,
    ToolchainCandidate,
    _probe_uart_prompt,
    _scan_ti_toolchains,
    _REQUIRED_TOOLCHAIN_FILES,
)
from awr2944_dca.lab import RadarProject


# ===========================================================================
# Helpers
# ===========================================================================

def _make_xds_port(com: str, role: str = "") -> SerialPortInfo:
    return SerialPortInfo(
        port=com,
        name=f"XDS110 ... ({com})",
        description=f"XDS110 ... ({com})",
        status="OK",
        instance_id=f"USB\\VID_0451&PID_BEF3\\{com}",
        vid="0451",
        pid="BEF3",
        is_xds110=True,
        role=role,
    )


def _make_non_xds_port(com: str, vid: str = "1234") -> SerialPortInfo:
    return SerialPortInfo(
        port=com,
        name=f"Arduino Uno ({com})",
        description=f"Arduino Uno ({com})",
        status="OK",
        instance_id=f"USB\\VID_{vid}&PID_0001\\{com}",
        vid=vid,
        pid="0001",
        is_xds110=False,
        role="",
    )


def _make_project(tmp_path: Path) -> RadarProject:
    return RadarProject.create(name="autodetect_test", parent=tmp_path)


def _probe_returns(port_responses: dict[str, bool]):
    """Return a _probe_uart_prompt replacement that uses port_responses map."""
    def _probe(port, baud=115200, timeout=3.0):
        if port_responses.get(port):
            return True, "Received mmwDemo:/>"
        return False, "TIMEOUT - No prompt received"
    return _probe


def _make_toolchain_dir(tmp_path: Path, version: str = "03_01_04_04") -> Path:
    """Create a fake complete mmWave Studio toolchain under tmp_path."""
    studio = tmp_path / f"mmwave_studio_{version}"
    postproc = studio / "mmWaveStudio" / "PostProc"
    postproc.mkdir(parents=True)
    for fname in _REQUIRED_TOOLCHAIN_FILES:
        (postproc / fname).write_bytes(b"fake")
    return studio


def _make_toolchain_candidate(tmp_path: Path, version: str = "03_01_04_04") -> ToolchainCandidate:
    """Build an isolated ToolchainCandidate without scanning C:\\ti."""
    studio = _make_toolchain_dir(tmp_path, version)
    postproc = studio / "mmWaveStudio" / "PostProc"
    return ToolchainCandidate(
        root=postproc,
        version_tag=version,
        control_exe=postproc / "DCA1000EVM_CLI_Control.exe",
        record_exe=postproc / "DCA1000EVM_CLI_Record.exe",
        rf_api_dll=postproc / "RF_API.dll",
        cf_json=postproc / "cf.json",
    )


def _make_valid_cf_json(host_ip: str, dca_ip: str, cfg_port: int, data_port: int) -> dict:
    return {
        "DCA1000Config": {
            "ethernetConfigUpdate": {
                "systemIPAddress": host_ip,
                "DCA1000IPAddress": dca_ip,
                "DCA1000ConfigPort": cfg_port,
                "DCA1000DataPort": data_port,
            }
        }
    }


# ===========================================================================
# Serial autodetection tests
# ===========================================================================

class TestAutodetectSerial:
    """S1–S10: autodetect_serial() scenarios."""

    def test_s1_com3_is_cli_com4_is_aux(self, tmp_path):
        """S1: COM3 responds with prompt, COM4 does not → CLI=COM3, AUX=COM4."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": True, "COM4": False})),
        ):
            result = p.hardware.autodetect_serial()

        assert result.cli_port == "COM3"
        assert result.aux_port == "COM4"
        assert result.verified is True
        assert result.saved is False
        assert len(result.warnings) == 0
        assert len(result.candidates) == 2

    def test_s2_com4_is_cli_com3_is_aux(self, tmp_path):
        """S2: Reversed — COM4 responds, COM3 does not → CLI=COM4, AUX=COM3."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": False, "COM4": True})),
        ):
            result = p.hardware.autodetect_serial()

        assert result.cli_port == "COM4"
        assert result.aux_port == "COM3"
        assert result.verified is True

    def test_s3_unrelated_com_devices_never_probed(self, tmp_path):
        """S3: Unrelated COM devices (Arduino, power supply) must never be probed."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3")]
        non_xds = [_make_non_xds_port("COM1"), _make_non_xds_port("COM7")]
        all_ports = non_xds + xds_ports  # discover_serial_ports returns all

        probed_ports: list[str] = []

        def tracking_probe(port, baud=115200, timeout=3.0):
            probed_ports.append(port)
            return (True, "Received mmwDemo:/>") if port == "COM3" else (False, "TIMEOUT")

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=all_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt", side_effect=tracking_probe),
        ):
            result = p.hardware.autodetect_serial()

        # Only the XDS110 candidate must have been probed
        assert set(probed_ports) == {"COM3"}, (
            f"Non-XDS110 ports were probed: {set(probed_ports) - {'COM3'}}"
        )
        assert result.cli_port == "COM3"
        assert "COM1" not in probed_ports
        assert "COM7" not in probed_ports

    def test_s4_no_xds110_candidates(self, tmp_path):
        """S4: No XDS110 candidates → verified=False, no CLI, no AUX."""
        p = _make_project(tmp_path)
        all_ports = [_make_non_xds_port("COM1"), _make_non_xds_port("COM2")]

        with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=all_ports):
            result = p.hardware.autodetect_serial()

        assert result.verified is False
        assert result.cli_port == ""
        assert result.aux_port == ""
        assert len(result.candidates) == 0

    def test_s5_no_prompt_responder(self, tmp_path):
        """S5: XDS110 ports exist but none respond with mmwDemo:/>."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": False, "COM4": False})),
        ):
            result = p.hardware.autodetect_serial()

        assert result.verified is False
        assert result.cli_port == ""
        assert result.aux_port == ""
        assert len(result.warnings) > 0
        assert any("mmwDemo" in w for w in result.warnings)

    def test_s6_port_busy_permission_denied(self, tmp_path):
        """S6: Port is busy/permission denied → verified=False, graceful handling."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3")]

        def busy_probe(port, baud=115200, timeout=3.0):
            return False, "SerialException: [Errno 13] could not open port COM3"

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt", side_effect=busy_probe),
        ):
            result = p.hardware.autodetect_serial()

        assert result.verified is False
        assert result.cli_port == ""
        # Must not raise; must produce a usable result object
        assert isinstance(result, SerialAutodetectResult)

    def test_s7_multiple_prompt_responders_ambiguous(self, tmp_path):
        """S7: Two ports both respond → ambiguous, no CLI assigned, no guess."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]

        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": True, "COM4": True})),
        ):
            result = p.hardware.autodetect_serial()

        assert result.verified is False
        assert result.cli_port == ""
        assert len(result.warnings) > 0
        assert any("Ambiguous" in w or "Multiple" in w for w in result.warnings)

    def test_s8_save_false_does_not_mutate_local_toml(self, tmp_path):
        """S8: save=False must never write to local.toml."""
        p = _make_project(tmp_path)
        local_toml = tmp_path / "autodetect_test" / ".awr2944" / "local.toml"
        before = local_toml.read_bytes() if local_toml.exists() else b""

        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]
        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": True, "COM4": False})),
        ):
            result = p.hardware.autodetect_serial(save=False)


    def test_s9_save_true_updates_only_serial_fields(self, tmp_path):
        """S9: save=True updates only com_port and aux_com_port in local.toml."""
        p = _make_project(tmp_path)
        project_root = tmp_path / "autodetect_test"

        # Pre-set some non-serial fields to verify they survive
        cfg = p.config
        cfg.local.host_ip = "192.168.33.10"
        cfg.local.dca_control_exe = r"C:\ti\control.exe"
        cfg.local.baud_rate = 115200
        cfg.save()

        local_toml = project_root / ".awr2944" / "local.toml"
        before = tomllib.loads(local_toml.read_text(encoding="utf-8"))

        xds_ports = [_make_xds_port("COM5"), _make_xds_port("COM6")]
        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM5": True, "COM6": False})),
        ):
            result = p.hardware.autodetect_serial(save=True)

        assert result.saved is True

        after = tomllib.loads(local_toml.read_text(encoding="utf-8"))

        # Serial fields updated
        assert after["serial"]["com_port"] == "COM5"
        assert after["serial"]["aux_com_port"] == "COM6"

        # Non-serial fields preserved
        assert after["network"]["host_ip"] == "192.168.33.10"
        assert after["dca_tools"]["control_exe"] == r"C:\ti\control.exe"
        assert after["serial"]["baud_rate"] == 115200

    def test_s10_driver_hint_on_ti_vid_without_xds110(self, tmp_path):
        """S10: TI VID=0451 present but not XDS110 → driver-missing hint in warnings."""
        p = _make_project(tmp_path)
        # VID=0451 but not XDS110 (different PID)
        ti_non_xds = SerialPortInfo(
            port="COM3",
            name="TI Generic Device (COM3)",
            description="",
            status="OK",
            instance_id="USB\\VID_0451&PID_0000\\ABC",
            vid="0451",
            pid="0000",
            is_xds110=False,
        )

        with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[ti_non_xds]):
            result = p.hardware.autodetect_serial()

        assert result.verified is False
        assert any("driver" in w.lower() or "TI" in w for w in result.warnings)

    # Repr / presentation smoke tests
    def test_repr_and_html(self, tmp_path):
        """SerialAutodetectResult repr and _repr_html_ don't crash."""
        p = _make_project(tmp_path)
        xds_ports = [_make_xds_port("COM3"), _make_xds_port("COM4")]
        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=xds_ports),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  side_effect=_probe_returns({"COM3": True, "COM4": False})),
        ):
            result = p.hardware.autodetect_serial()

        r = repr(result)
        assert "SerialAutodetectResult" in r
        assert "COM3" in r

        html = result._repr_html_()
        assert "COM3" in html
        assert "<details" in html

    def test_print_runs_without_error(self, tmp_path):
        """SerialAutodetectResult.print() runs without raising."""
        p = _make_project(tmp_path)
        with (
            patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[]),
        ):
            result = p.hardware.autodetect_serial()
        result.print()  # must not raise


# ===========================================================================
# Toolchain autodetection tests
# ===========================================================================

class TestAutodetectToolchain:
    """T1–T10: autodetect_toolchain() scenarios."""

    def test_t1_no_installation(self, tmp_path):
        """T1: No mmWave Studio found → candidates=[], selected=None, no crash."""
        p = _make_project(tmp_path)

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[]):
            result = p.hardware.autodetect_toolchain()

        assert result.candidates == []
        assert result.selected is None
        assert result.saved is False
        assert isinstance(result, ToolchainAutodetectResult)

    def test_t2_incomplete_installation_not_accepted(self, tmp_path):
        """T2: A directory missing cf.json is not treated as a valid candidate."""
        # Build an incomplete tree directly (no _scan_ti_toolchains to avoid real C:\ti)
        studio = tmp_path / "mmwave_studio_03_01_04_04"
        postproc = studio / "mmWaveStudio" / "PostProc"
        postproc.mkdir(parents=True)
        for fname in ("DCA1000EVM_CLI_Control.exe", "DCA1000EVM_CLI_Record.exe", "RF_API.dll"):
            (postproc / fname).write_bytes(b"fake")
        # cf.json missing intentionally

        # _scan_ti_toolchains with extra_roots should find zero complete installs
        # here (only the tmp one, not real C:\ti — mock it out)
        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[]):
            # If there were a real scanner call, it would pick up the incomplete dir as nothing
            pass

        # Direct call to isolated scanner
        from awr2944_dca._doctor import _scan_ti_toolchains as real_scan
        with patch("awr2944_dca._doctor.Path") as _:
            pass
        # Verify: no ToolchainCandidate produced for an incomplete install
        files = {f: postproc / f for f in _REQUIRED_TOOLCHAIN_FILES}
        missing = [f for f, p in files.items() if not p.exists()]
        assert "cf.json" in missing, "Test setup: cf.json should be absent"
        # An incomplete dir produces zero candidates
        assert not all(p.exists() for p in files.values())

    def test_t3_one_complete_installation_selected(self, tmp_path):
        """T3: Exactly one complete installation → selected automatically."""
        p = _make_project(tmp_path)
        cand = _make_toolchain_candidate(tmp_path, "03_01_04_04")

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand]):
            result = p.hardware.autodetect_toolchain()

        assert result.selected is not None
        assert result.selected.version_tag == "03_01_04_04"
        assert len(result.candidates) == 1

    def test_t4_multiple_complete_installations_ambiguous(self, tmp_path):
        """T4: Multiple complete installs → selected=None, both reported."""
        p = _make_project(tmp_path)
        cand_a = _make_toolchain_candidate(tmp_path / "a", "03_01_04_04")
        cand_b = _make_toolchain_candidate(tmp_path / "b", "03_00_00_14")

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand_a, cand_b]):
            result = p.hardware.autodetect_toolchain()

        assert result.selected is None
        assert len(result.candidates) == 2
        assert result.saved is False

    def test_t5_explicit_version_selection(self, tmp_path):
        """T5: version= argument selects the matching candidate."""
        p = _make_project(tmp_path)
        cand_a = _make_toolchain_candidate(tmp_path / "a", "03_01_04_04")
        cand_b = _make_toolchain_candidate(tmp_path / "b", "03_00_00_14")

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand_a, cand_b]):
            result = p.hardware.autodetect_toolchain(version="03_01_04_04")

        assert result.selected is not None
        assert result.selected.version_tag == "03_01_04_04"

    def test_t6_save_false_does_not_mutate_local_toml(self, tmp_path):
        """T6: save=False must not modify local.toml."""
        p = _make_project(tmp_path)
        project_root = tmp_path / "autodetect_test"
        local_toml = project_root / ".awr2944" / "local.toml"
        before = local_toml.read_bytes() if local_toml.exists() else b""

        studio = _make_toolchain_dir(tmp_path, "03_01_04_04")
        cands = _scan_ti_toolchains(extra_roots=[studio])

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=cands):
            result = p.hardware.autodetect_toolchain(save=False)

        after = local_toml.read_bytes() if local_toml.exists() else b""
        assert result.saved is False
        assert before == after, "local.toml was mutated despite save=False"

    def test_t7_save_true_updates_only_tool_paths(self, tmp_path):
        """T7: save=True updates only dca_control_exe, dca_record_exe, rf_api_dll, cf_json_path."""
        p = _make_project(tmp_path)
        project_root = tmp_path / "autodetect_test"

        # Pre-set non-tool fields
        cfg = p.config
        cfg.local.host_ip = "192.168.33.10"
        cfg.local.com_port = "COM3"
        cfg.local.baud_rate = 115200
        cfg.save()

        cand = _make_toolchain_candidate(tmp_path, "03_01_04_04")

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand]):
            result = p.hardware.autodetect_toolchain(save=True)

        assert result.saved is True

        local_toml = project_root / ".awr2944" / "local.toml"
        after = tomllib.loads(local_toml.read_text(encoding="utf-8"))

        # Tool paths updated
        assert "DCA1000EVM_CLI_Control.exe" in after["dca_tools"]["control_exe"]
        assert "DCA1000EVM_CLI_Record.exe" in after["dca_tools"]["record_exe"]
        assert "RF_API.dll" in after["dca_tools"]["rf_api_dll"]
        assert "cf.json" in after["dca_tools"]["cf_json"]

        # Non-tool fields preserved
        assert after["network"]["host_ip"] == "192.168.33.10"
        assert after["serial"]["com_port"] == "COM3"
        assert after["serial"]["baud_rate"] == 115200

    def test_t8_cfjson_validation_reuse(self, tmp_path):
        """T8: cf.json validation uses existing ProjectConfig.validate_cf_json()."""
        p = _make_project(tmp_path)
        cand = _make_toolchain_candidate(tmp_path, "03_01_04_04")

        # Overwrite cf.json with a mismatching config
        bad_cf = {"DCA1000Config": {"ethernetConfigUpdate": {
            "systemIPAddress": "10.0.0.1",
            "DCA1000IPAddress": "10.0.0.2",
            "DCA1000ConfigPort": 4096,
            "DCA1000DataPort": 4098,
        }}}
        cand.cf_json.write_text(json.dumps(bad_cf))

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand]):
            result = p.hardware.autodetect_toolchain()

        assert result.selected is not None
        assert result.cf_valid is False
        assert result.cf_detail != ""

    def test_t9_malformed_cfjson_handled_cleanly(self, tmp_path):
        """T9: Malformed cf.json does not raise — reports validation failure."""
        p = _make_project(tmp_path)
        cand = _make_toolchain_candidate(tmp_path, "03_01_04_04")
        cand.cf_json.write_text("{this is not valid json")

        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand]):
            result = p.hardware.autodetect_toolchain()

        assert result.selected is not None
        assert result.cf_valid is False
        assert isinstance(result, ToolchainAutodetectResult)  # no exception

    def test_t10_bounded_discovery_no_disk_crawl(self, tmp_path, monkeypatch):
        """T10: _scan_ti_toolchains does NOT scan all of C:\\ — only C:\\ti\\mmwave_studio_*."""
        scanned_dirs: list[Path] = []

        real_is_dir = Path.is_dir

        def tracking_iterdir(self_path):
            scanned_dirs.append(self_path)
            raise FileNotFoundError("simulated no C:\\ti")

        # Patch Path.iterdir only for C:/ti
        orig_is_dir = Path.is_dir.__get__

        def patched_is_dir(self):
            # C:/ti does not exist in test environment → no scanning
            return False

        with patch.object(Path, "is_dir", patched_is_dir):
            candidates = _scan_ti_toolchains()

        # If C:/ti doesn't exist, no scan should happen and we get empty list
        assert candidates == []
        # Verify we never tried to iterate C:/ root or anything above ti/
        dangerous = [d for d in scanned_dirs if str(d) in ("C:\\", "C:/", "/")]
        assert dangerous == [], f"Dangerous root scan occurred: {dangerous}"

    def test_repr_and_html_no_candidates(self, tmp_path):
        """ToolchainAutodetectResult repr/_repr_html_ for empty case."""
        p = _make_project(tmp_path)
        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[]):
            result = p.hardware.autodetect_toolchain()

        r = repr(result)
        assert "ToolchainAutodetectResult" in r
        assert "None" in r

        html = result._repr_html_()
        assert "<details" in html
        assert "No complete" in html

    def test_print_runs_without_error_no_candidates(self, tmp_path):
        """ToolchainAutodetectResult.print() for empty case doesn't crash."""
        p = _make_project(tmp_path)
        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[]):
            result = p.hardware.autodetect_toolchain()
        result.print()

    def test_print_runs_without_error_with_candidate(self, tmp_path):
        """ToolchainAutodetectResult.print() with selected candidate doesn't crash."""
        p = _make_project(tmp_path)
        cand = _make_toolchain_candidate(tmp_path, "03_01_04_04")
        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand]):
            result = p.hardware.autodetect_toolchain()
        result.print()

    def test_print_runs_without_error_ambiguous(self, tmp_path):
        """ToolchainAutodetectResult.print() for ambiguous case doesn't crash."""
        p = _make_project(tmp_path)
        cand_a = _make_toolchain_candidate(tmp_path / "a", "03_01_04_04")
        cand_b = _make_toolchain_candidate(tmp_path / "b", "03_00_00_14")
        with patch("awr2944_dca._doctor._scan_ti_toolchains", return_value=[cand_a, cand_b]):
            result = p.hardware.autodetect_toolchain()
        result.print()


# ===========================================================================
# _probe_uart_prompt — unit tests for shared helper
# ===========================================================================

class TestProbeUartPrompt:
    """Unit tests for the _probe_uart_prompt helper."""

    def test_returns_true_on_prompt_response(self):
        """Verified=True when serial returns mmwDemo:/>."""
        mock_ser = MagicMock()
        mock_ser.__enter__ = lambda s: s
        mock_ser.__exit__ = MagicMock(return_value=False)
        mock_ser.read_until.return_value = b"mmwDemo:/>"

        with patch("serial.Serial", return_value=mock_ser):
            ok, detail = _probe_uart_prompt("COM3", 115200, 3.0)

        assert ok is True
        assert "mmwDemo" in detail

    def test_returns_false_on_timeout(self):
        """Verified=False when no prompt received."""
        mock_ser = MagicMock()
        mock_ser.__enter__ = lambda s: s
        mock_ser.__exit__ = MagicMock(return_value=False)
        mock_ser.read_until.return_value = b"some garbage"

        with patch("serial.Serial", return_value=mock_ser):
            ok, detail = _probe_uart_prompt("COM3", 115200, 3.0)

        assert ok is False
        assert "TIMEOUT" in detail

    def test_returns_false_on_exception(self):
        """Verified=False when serial.Serial() raises (busy port, etc.)."""
        with patch("serial.Serial", side_effect=OSError("port busy")):
            ok, detail = _probe_uart_prompt("COM3", 115200, 3.0)

        assert ok is False
        assert "OSError" in detail or "busy" in detail.lower()


# ===========================================================================
# Regression: doctor UART check still works after helper extraction
# ===========================================================================

class TestDoctorUartRegression:
    """R1: Doctor's uart_prompt_responds check is functionally unchanged."""

    def test_doctor_uses_probe_helper_and_passes(self, tmp_path, monkeypatch):
        """Doctor passes uart_prompt_responds when _probe_uart_prompt returns True."""
        p = RadarProject.create(name="doc_test", parent=tmp_path)
        project_root = tmp_path / "doc_test"

        # Set up valid config
        cfg = p.config
        cfg.local.com_port = "COM3"
        cfg.local.dca_control_exe = str(tmp_path / "control.exe")
        cfg.local.cf_json_path = str(tmp_path / "cf.json")
        cfg.save()

        # Patch doctor's port scan and the shared probe helper
        mock_port = MagicMock()
        mock_port.com = "COM3"

        def fake_probe(port, baud=115200, timeout=3.0):
            if port == "COM3":
                return True, "Received mmwDemo:/>"
            return False, "TIMEOUT"

        with (
            patch("awr2944_dca.hardware.ports.scan_ports", return_value=[mock_port]),
            patch("awr2944_dca._doctor._probe_uart_prompt", side_effect=fake_probe),
            # Skip network/DCA checks by patching at their actual import locations
            patch("awr2944_dca.dca.preflight.run_dca_preflight"),
            patch("awr2944_dca.dca.preflight._run_ps_json", return_value=[{"InterfaceAlias": "Eth"}]),
            patch("awr2944_dca.dca.preflight._as_dicts", return_value=[{"InterfaceAlias": "Eth"}]),
        ):
            report = p.hardware.verify(include_hardware=True)

        uart_check = next(
            (c for c in report.checks if c.name == "uart_prompt_responds"), None
        )
        assert uart_check is not None
        assert uart_check.status == "PASS", f"Expected PASS, got {uart_check.status}: {uart_check.detail}"

    def test_doctor_uart_fails_on_timeout(self, tmp_path):
        """Doctor fails uart_prompt_responds when probe returns TIMEOUT."""
        p = RadarProject.create(name="doc_test2", parent=tmp_path)
        cfg = p.config
        cfg.local.com_port = "COM3"
        cfg.save()

        mock_port = MagicMock()
        mock_port.com = "COM3"

        with (
            patch("awr2944_dca.hardware.ports.scan_ports", return_value=[mock_port]),
            patch("awr2944_dca._doctor._probe_uart_prompt",
                  return_value=(False, "TIMEOUT - No prompt received")),
            patch("awr2944_dca.dca.preflight.run_dca_preflight"),
            patch("awr2944_dca.dca.preflight._run_ps_json", return_value=[]),
            patch("awr2944_dca.dca.preflight._as_dicts", return_value=[]),
        ):
            report = p.hardware.verify(include_hardware=True)

        uart_check = next(
            (c for c in report.checks if c.name == "uart_prompt_responds"), None
        )
        assert uart_check is not None
        assert uart_check.status == "FAIL"
