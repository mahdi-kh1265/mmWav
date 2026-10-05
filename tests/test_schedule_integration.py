"""Production-path tests: BasicFrameSchedule integration into config resolution,
capabilities, offline planning and the pre-hardware live-execution gate.

No hardware: every serial / DCA / UDP / subprocess entry point is patched to
raise if it is ever called.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from awr2944_dca.api._config_resolver import (
    CaptureCapabilities,
    LiveExecutionNotEnabledError,
    preflight_validate,
    extract_capture_metadata,
    resolve_capture_config,
)
from awr2944_dca.api.profile import RadarProfile
from awr2944_dca.lab import RadarProject
from awr2944_dca.mmw_demo_config import MmwDemoConfig

COMMON = (
    "flushCfg\n"
    "dfeDataOutputMode 1\n"
    "channelCfg 15 7 0\n"
    "adcCfg 2 0\n"
    "adcbufCfg -1 1 1 1 1\n"
    "profileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\n"
)
SINGLE_CFG = COMMON + "chirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0\nsensorStart\n"
TDM2_CFG = (
    COMMON
    + "chirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0\n"
)
TDM3_CFG = (
    COMMON
    + "chirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 4\nchirpCfg 2 2 0 0 0 0 0 2\n"
    + "frameCfg 0 2 32 8 100 1 0\n"
)
# Second chirp references a profileId that does not exist -> samples/chirp unprovable.
BAD_RAW_CFG = (
    COMMON
    + "chirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 1 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0\n"
)

SMOKE_GOLDEN_CLI = (
    'flushCfg', 'dfeDataOutputMode 1', 'channelCfg 15 7 0', 'adcCfg 2 0', 'adcbufCfg -1 1 1 1 1',
    'lowPower 0 0', 'profileCfg 0 77 100 6 60 0 0 29.982 0 256 10000 0 0 30',
    'chirpCfg 0 0 0 0 0 0 0 3', 'frameCfg 0 0 128 9 256 40 1 0', 'lowPower 0 0',
    'guiMonitor -1 1 1 0 0 0 1', 'cfarCfg -1 0 2 8 4 3 0 15 1', 'cfarCfg -1 1 0 4 2 3 1 15 1',
    'multiObjBeamForming -1 1 0.5', 'calibDcRangeSig -1 0 -5 8 256', 'clutterRemoval -1 0',
    'antGeometryCfg 1 0 1 1 1 2 1 3 0 2 0 3 0 4 0 5 1 4 1 5 1 6 1 7 1 8 1 9 1 10 1 11 0.5 0.8',
    'compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0',
    'measureRangeBiasAndRxChanPhase 0 1.5 0.2', 'aoaFovCfg -1 -90 90 -90 90',
    'cfarFovCfg -1 0 0 19.53', 'cfarFovCfg -1 1 -1 1.00', 'extendedMaxVelocity -1 0',
    'calibData 0 0 0x1f0000', 'CQRxSatMonitor 0 3 11 121 0', 'CQSigImgMonitor 0 127 8',
    'analogMonitor 0 0', 'lvdsStreamCfg -1 0 1 0',
)
SMOKE_GOLDEN_RESOLVED_SHA256 = "58a8d2169026eec0b988ef1773a45a01e0c92842604e98c505b74e25a3146a8b"


def _make_project(tmp_path: Path) -> RadarProject:
    (tmp_path / "awr2944.toml").write_text(
        '[project]\nname = "sched_proj"\nid = "abc123"\n\n'
        '[defaults]\nframes = 8\nguard_frames = 1\nprofile = "smoke_v1"\n\n'
        '[network]\ndca_ip = "192.168.33.180"\nconfig_port = 4096\ndata_port = 4098\n',
        encoding="utf-8",
    )
    local = tmp_path / ".awr2944"
    local.mkdir(parents=True, exist_ok=True)
    (local / "local.toml").write_text(
        '[serial]\ncom_port = "COM8"\nbaud_rate = 115200\n\n'
        '[network]\nhost_ip = "192.168.33.30"\n\n'
        '[dca_tools]\ncontrol_exe = ""\nrecord_exe = ""\nrf_api_dll = ""\ncf_json = ""\n',
        encoding="utf-8",
    )
    (tmp_path / "profiles").mkdir(exist_ok=True)
    (tmp_path / "captures").mkdir(exist_ok=True)
    return RadarProject.open(tmp_path)


@pytest.fixture
def project(tmp_path):
    return _make_project(tmp_path)


def _cfg(tmp_path: Path, text: str, name: str = "t.cfg") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class _HardwareTripwire:
    """Patches every hardware entry point to raise and records calls."""

    TARGETS = (
        "serial.Serial",
        "socket.socket",
        "subprocess.Popen",
        "subprocess.run",
        "awr2944_dca.capture_session.run_capture",
        "awr2944_dca.api._session.resolve_connection",
        "awr2944_dca.api._lock.HardwareLease",
        "awr2944_dca.dca_cli.DcaCli",
        "awr2944_dca.api._capture_run._create_capture_manifest_facade",
    )

    def __init__(self):
        self.calls: list[str] = []
        self._patchers = []

    def __enter__(self):
        for target in self.TARGETS:
            def make(name):
                def boom(*a, **k):
                    self.calls.append(name)
                    raise AssertionError(f"hardware entry point called: {name}")
                return boom
            patcher = patch(target, side_effect=make(target))
            patcher.start()
            self._patchers.append(patcher)
        return self

    def __exit__(self, *exc):
        for p in self._patchers:
            p.stop()


# 1. smoke production resolution ------------------------------------------
def test_smoke_production_resolution(project):
    res = resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)
    s = res.schedule
    assert s is not None
    assert s.chirps_per_cycle == 1
    assert s.tx_masks_per_cycle == (3,)
    assert s.loops_per_frame == 128
    assert s.physical_chirps_per_frame == 128
    assert s.frame_count == 9
    bp = res.byte_plan
    assert bp.chirps_per_frame == 128
    assert bp.total_frames == 9 and bp.canonical_frames == 8 and bp.guard_frames == 1
    assert bp.rx_channels == 4 and bp.samples_per_chirp == 256
    assert bp.native_dca_bytes == 4_718_592
    assert bp.canonical_dca_bytes == 4_194_304
    assert bp.native_active_payload_bytes == 2_359_296
    assert bp.canonical_active_payload_bytes == 2_097_152
    caps = res.capabilities
    assert caps.can_resolve and caps.can_compute_byte_plan and caps.can_plan
    assert caps.can_execute_live and caps.can_build_legacy_dsp_profile
    assert caps.live_block_reason is None
    assert not caps.can_run_tdm_dsp and not caps.can_run_aoa
    assert res.dsp_profile is not None and res.dsp_profile.tx_mask == 3


# 2. supported single-chirp .cfg regression --------------------------------
def test_single_chirp_cfg_regression(project, tmp_path):
    res = resolve_capture_config(project, _cfg(tmp_path, SINGLE_CFG), frames=8, guard_frames=1)
    assert res.source_kind == "cfg_file"
    assert res.schedule.is_single_chirp
    assert res.schedule.physical_chirps_per_frame == 128
    assert res.byte_plan.native_dca_bytes == 4_718_592
    assert res.byte_plan.canonical_dca_bytes == 4_194_304
    assert res.capabilities.can_resolve and res.capabilities.can_plan
    assert res.schedule.is_single_chirp
    # Generic single-chirp basic-frame cfgs now build an accurate flat DSP profile.
    assert res.dsp_profile is not None
    assert res.dsp_profile.tx_mask == 1
    assert res.dsp_profile.chirps_per_frame == 128
    assert res.capabilities.can_build_legacy_dsp_profile
    assert res.capabilities.can_execute_live
    assert res.capabilities.live_block_reason is None
    assert "frameCfg 0 0 128 9 100 1 0" in res.resolved_cfg_text


# 3. 2-TX TDM -----------------------------------------------------------------
def test_two_tx_tdm_resolution(project, tmp_path):
    res = resolve_capture_config(project, _cfg(tmp_path, TDM2_CFG), frames=8, guard_frames=1)
    s = res.schedule
    assert s.chirps_per_cycle == 2
    assert s.loops_per_frame == 64
    assert s.physical_chirps_per_frame == 128
    assert s.tx_masks_per_cycle == (1, 2)
    assert s.expanded_tx_masks()[:4] == (1, 2, 1, 2)
    assert res.byte_plan.chirps_per_frame == 128
    assert res.byte_plan.native_dca_bytes == 4_718_592
    assert res.byte_plan.canonical_dca_bytes == 4_194_304
    c = res.capabilities
    assert c.can_resolve and c.can_compute_byte_plan and c.can_plan
    assert not c.can_build_legacy_dsp_profile
    assert res.dsp_profile is None
    assert c.can_execute_live and c.live_block_reason is None
    assert not c.can_run_tdm_dsp and not c.can_run_aoa
    assert "cannot be represented by the flat DSP profile" in c.legacy_dsp_block_reason


# 4. 3-TX TDM -----------------------------------------------------------------
def test_three_tx_tdm_resolution(project, tmp_path):
    res = resolve_capture_config(project, _cfg(tmp_path, TDM3_CFG), frames=8, guard_frames=1)
    s = res.schedule
    assert s.chirps_per_cycle == 3
    assert s.loops_per_frame == 32
    assert s.physical_chirps_per_frame == 96
    assert s.tx_masks_per_cycle == (1, 4, 2)
    assert s.expanded_tx_masks()[:6] == (1, 4, 2, 1, 4, 2)
    assert res.byte_plan.chirps_per_frame == 96
    assert res.byte_plan.canonical_dca_bytes == 4_194_304 * 96 // 128
    assert res.capabilities.can_execute_live  # raw capture decoupled from DSP
    assert res.dsp_profile is None


def test_tdm_never_fakes_a_dsp_profile(project, tmp_path):
    res = resolve_capture_config(project, _cfg(tmp_path, TDM2_CFG), frames=8, guard_frames=1)
    assert res.dsp_profile is None
    assert res.capabilities.legacy_dsp_block_reason


# 5. derived byte-plan equivalence ---------------------------------------------
def test_byte_plan_equivalence_single_vs_tdm(project, tmp_path):
    single = resolve_capture_config(
        project,
        _cfg(tmp_path, COMMON + "chirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0\n", "a.cfg"),
        frames=8, guard_frames=1,
    )
    tdm = resolve_capture_config(project, _cfg(tmp_path, TDM2_CFG, "b.cfg"), frames=8, guard_frames=1)
    assert dataclasses.asdict(single.byte_plan) == dataclasses.asdict(tdm.byte_plan)
    smoke = resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)
    assert smoke.byte_plan.native_dca_bytes == tdm.byte_plan.native_dca_bytes
    assert smoke.byte_plan.canonical_dca_bytes == tdm.byte_plan.canonical_dca_bytes


def test_schedule_frame_count_tracks_native_frames(project, tmp_path):
    res = resolve_capture_config(project, _cfg(tmp_path, TDM2_CFG), frames=4, guard_frames=2)
    assert res.schedule.frame_count == 6
    assert res.byte_plan.total_frames == 6 and res.byte_plan.canonical_frames == 4


# 6. missing chirp failure -----------------------------------------------------
def test_missing_chirp_definition_fails_resolution(project, tmp_path):
    text = COMMON + "chirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 1 64 8 100 1 0\n"
    with pytest.raises(ValueError, match="no chirpCfg defines it"):
        resolve_capture_config(project, _cfg(tmp_path, text), frames=8, guard_frames=1)


def test_invalid_frame_range_fails_resolution(project, tmp_path):
    text = COMMON + "chirpCfg 0 1 0 0 0 0 0 1\nframeCfg 1 0 64 8 100 1 0\n"
    with pytest.raises(ValueError, match="Invalid frame chirp range"):
        resolve_capture_config(project, _cfg(tmp_path, text), frames=8, guard_frames=1)


def test_overlapping_chirpcfg_fails_resolution(project, tmp_path):
    text = (
        COMMON + "chirpCfg 0 1 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0\n"
    )
    with pytest.raises(ValueError, match="overlapping"):
        resolve_capture_config(project, _cfg(tmp_path, text), frames=8, guard_frames=1)


def test_preflight_reports_schedule_error(tmp_path):
    cfg = MmwDemoConfig.from_cfg_text(COMMON + "chirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 1 64 8 100 1 0\n")
    issues = preflight_validate(cfg, extract_capture_metadata(cfg))
    assert any(i.severity == "ERROR" and "Invalid chirp schedule" in i.message for i in issues)


def test_preflight_no_longer_blanket_blocks_multi_tx(tmp_path):
    cfg = MmwDemoConfig.from_cfg_text(TDM2_CFG)
    issues = preflight_validate(cfg, extract_capture_metadata(cfg))
    assert not [i for i in issues if i.severity == "ERROR"]


# 7. chirp-count consistency failure ----------------------------------------
def test_profile_chirp_count_disagreement_fails(project):
    original = RadarProfile.to_sdk_cli

    def disagreeing(self):
        return [c.replace("frameCfg 0 0 128", "frameCfg 0 0 64") for c in original(self)]

    with patch.object(RadarProfile, "to_sdk_cli", disagreeing):
        with pytest.raises(ValueError, match="Chirp count mismatch"):
            resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)


def test_cfg_metadata_chirp_count_disagreement_fails(project, tmp_path):
    from awr2944_dca.api import _config_resolver as cr

    real = cr.extract_capture_metadata

    def lying(cfg):
        m = real(cfg)
        return dataclasses.replace(m, total_chirps_per_frame=m.total_chirps_per_frame + 1)

    with patch.object(cr, "extract_capture_metadata", lying):
        with pytest.raises(ValueError, match="Chirp count mismatch"):
            resolve_capture_config(project, _cfg(tmp_path, SINGLE_CFG), frames=8, guard_frames=1)


# 8. offline plan: zero hardware calls ----------------------------------------
def test_plan_smoke_touches_no_hardware(project):
    with _HardwareTripwire() as trip:
        plan = project.capture.plan("smoke_v1", frames=8, guard_frames=1)
    assert trip.calls == []
    assert plan.hardware_touched is False
    assert plan.can_execute_live is True
    assert plan.live_block_reason is None
    assert plan.cube_shape == (8, 128, 4, 256)


def test_plan_tdm_touches_no_hardware(project, tmp_path):
    cfg = _cfg(tmp_path, TDM2_CFG)
    with _HardwareTripwire() as trip:
        plan = project.capture.plan(cfg, frames=8, guard_frames=1)
        plan.print()
        plan.to_dict()
        dry = project.capture.dry_run(cfg, frames=8, guard_frames=1)
    assert trip.calls == []
    assert plan.source_kind == "cfg_file"
    assert plan.canonical_frames == 8 and plan.guard_frames == 1 and plan.total_frames == 9
    assert plan.chirps_per_cycle == 2
    assert plan.loops_per_frame == 64
    assert plan.physical_chirps_per_frame == 128
    assert plan.tx_masks_per_cycle == (1, 2)
    assert plan.rx_channels == 4
    assert plan.samples_per_chirp == 256
    assert plan.byte_plan.native_dca_bytes == 4_718_592
    assert plan.byte_plan.canonical_dca_bytes == 4_194_304
    assert plan.cube_shape == (8, 128, 4, 256)
    assert plan.can_execute_live is True
    assert plan.live_block_reason is None
    assert plan.capabilities.can_plan
    # legacy dry_run contract unchanged and consistent
    assert plan.to_dict() == dry
    assert dry["hardware_touched"] is False


def test_plan_print_shows_schedule_and_live_status(project, tmp_path, capsys):
    plan = project.capture.plan(_cfg(tmp_path, TDM3_CFG), frames=8, guard_frames=1)
    plan.print()
    out = capsys.readouterr().out
    assert "3/cycle x 32 loops = 96 physical/frame" in out
    assert "TX masks/cycle=[1, 4, 2]" in out
    assert "Live execution: supported" in out
    blocked = project.capture.plan(_cfg(tmp_path, BAD_RAW_CFG, "bad.cfg"), frames=8, guard_frames=1)
    blocked.print()
    assert "BLOCKED" in capsys.readouterr().out


def test_dry_run_dict_keys_unchanged_for_smoke(project):
    d = project.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
    assert d["expected_native_dca_bytes"] == 4_718_592
    assert d["expected_canonical_dca_bytes"] == 4_194_304
    assert d["canonical_cube"] == [8, 128, 4, 256]
    assert "schedule" not in d and "capabilities" not in d


# 9. capture.run(TDM) fails before ALL hardware calls --------------------------
def test_run_tdm_blocked_before_any_hardware(project, tmp_path):
    cfg = _cfg(tmp_path, BAD_RAW_CFG)
    before = sorted(p.name for p in (project.root / "captures").iterdir())
    with _HardwareTripwire() as trip:
        with pytest.raises(LiveExecutionNotEnabledError, match="live raw capture is not enabled"):
            project.capture.run(profile=cfg, frames=8, guard_frames=1, name="tdm_blocked")
    assert trip.calls == []
    assert sorted(p.name for p in (project.root / "captures").iterdir()) == before


def test_run_tdm_three_tx_blocked(project, tmp_path):
    with _HardwareTripwire() as trip:
        with pytest.raises(LiveExecutionNotEnabledError):
            project.capture.run(profile=_cfg(tmp_path, BAD_RAW_CFG), frames=8, guard_frames=1)
    assert trip.calls == []


def test_run_tdm_blocked_via_explicit_session_leaves_state_untouched(project, tmp_path):
    from awr2944_dca.api._capture_run import SessionCaptureApi

    class FakeSession:
        def __init__(self, project):
            self.project = project
            self.entered = False
            self.errored = False
            self.connection = None

        def _enter_capturing(self):
            self.entered = True

        def _enter_error(self):
            self.errored = True

        def _exit_capturing(self, ok):
            raise AssertionError("must not exit capturing")

    sess = FakeSession(project)
    with _HardwareTripwire() as trip:
        with pytest.raises(LiveExecutionNotEnabledError):
            SessionCaptureApi(sess).run(profile=_cfg(tmp_path, BAD_RAW_CFG), frames=8, guard_frames=1)
    assert trip.calls == []
    assert not sess.entered and not sess.errored


def test_live_gate_is_explicit_not_incidental(project, tmp_path):
    """The block comes from the capability gate, not from DSP profile construction."""
    res = resolve_capture_config(project, _cfg(tmp_path, BAD_RAW_CFG), frames=8, guard_frames=1)
    assert isinstance(res.capabilities, CaptureCapabilities)
    assert res.capabilities.can_execute_live is False
    with _HardwareTripwire():
        with pytest.raises(LiveExecutionNotEnabledError) as ei:
            project.capture.run(profile=_cfg(tmp_path, BAD_RAW_CFG, "c.cfg"), frames=8, guard_frames=1)
    assert str(ei.value) == res.capabilities.live_block_reason


def test_smoke_run_still_uses_production_path(project):
    """Smoke passes the gate and reaches the (mocked) frozen run_capture."""
    from awr2944_dca.api._session import ConnectionOverrides
    from awr2944_dca.capture_manifest import CaptureManifest
    from awr2944_dca.capture_session import CaptureResult

    seen = {}

    def fake_run_capture(*args, **kwargs):
        seen.update(kwargs)
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        manifest = CaptureManifest(
            total_frames=9, guard_frame_count=1, canonical_frame_count=8,
            native_sha256="a", canonical_sha256="b", packet_count=1, sequence_gaps=0,
            capture_timestamp="t", parser_layout_version="1", dsp_config_version="1", success=True,
        )
        return CaptureResult(capture_dir=out, manifest=manifest)

    overrides = ConnectionOverrides(
        com_port="COM1", host_ip="1.1.1.1", dca_ip="1.1.1.2",
        dca_control_exe=Path("x"), dca_record_exe=Path("x"),
        rf_api_dll=Path("x"), cf_json_path=Path("x"),
    )
    with patch("awr2944_dca.capture_session.run_capture", fake_run_capture):
        res = project.capture.run(
            profile="smoke_v1", frames=8, guard_frames=1, name="smoke_gate",
            connection_overrides=overrides,
        )
    assert res.success
    assert tuple(seen["sdk_cli_commands"]) == SMOKE_GOLDEN_CLI
    assert seen["guard_frames"] == 1
    assert seen["profile"].tx_mask == 3
    assert res.capture_plan["expected_native_dca_bytes"] == 4_718_592
    assert res.capture_plan["expected_canonical_dca_bytes"] == 4_194_304




# 10. smoke CLI commands exactly unchanged -------------------------------------
def test_smoke_cli_commands_exactly_unchanged(project):
    res = resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)
    assert tuple(res.cli_commands) == SMOKE_GOLDEN_CLI
    assert res.resolved_sha256 == SMOKE_GOLDEN_RESOLVED_SHA256


def test_smoke_cli_unchanged_from_radar_profile_object(project):
    res = resolve_capture_config(project, RadarProfile.smoke_v1(), frames=8, guard_frames=1)
    assert tuple(res.cli_commands) == SMOKE_GOLDEN_CLI
    assert res.resolved_sha256 == SMOKE_GOLDEN_RESOLVED_SHA256


def test_default_capabilities_fail_closed():
    c = CaptureCapabilities()
    assert not any(
        [c.can_resolve, c.can_compute_byte_plan, c.can_plan, c.can_execute_live,
         c.can_build_legacy_dsp_profile, c.can_run_tdm_dsp, c.can_run_aoa]
    )
