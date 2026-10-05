"""Generic single-chirp basic-frame .cfg: DSP-profile mapping, capability gate and
facade routing to the existing (mocked) production backend.  No hardware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from awr2944_dca.api._config_resolver import LiveExecutionNotEnabledError, resolve_capture_config
from tests.test_schedule_integration import (
    COMMON,
    SMOKE_GOLDEN_CLI,
    TDM2_CFG,
    _HardwareTripwire,
    _cfg,
    _make_project,
    project,  # noqa: F401  (fixture)
)

# profileCfg: id startFreq idle adcStart rampEnd txPwr txPh slope txStart samples rate hpf1 hpf2 gain
def make_cfg(
    *,
    rx=15, tx=7, chirp_tx=1,
    start=77, idle=100, adc_start=6, ramp=60, slope=29.982, samples=256, rate=10000,
    loops=128, frames=8, period=40, frame_args8=True, extra_chirp="0 0 0 0",
):
    prof = f"profileCfg 0 {start} {idle} {adc_start} {ramp} 0 0 {slope} 1 {samples} {rate} 0 0 30\n"
    if frame_args8:
        frame = f"frameCfg 0 0 {loops} {frames} {samples} {period} 1 0\n"
    else:
        frame = f"frameCfg 0 0 {loops} {frames} {period} 1 0\n"
    return (
        "flushCfg\ndfeDataOutputMode 1\n"
        f"channelCfg {rx} {tx} 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\n"
        + prof
        + f"chirpCfg 0 0 0 {extra_chirp} {chirp_tx}\n"
        + frame
        + "lvdsStreamCfg -1 0 1 0\n"
    )


def res_for(project, tmp_path, text, name="g.cfg", frames=8, guard=1):
    return resolve_capture_config(project, _cfg(tmp_path, text, name), frames=frames, guard_frames=guard)


def test_baseline_generic_cfg_is_live_and_mapped(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg())
    c = r.capabilities
    assert c.can_resolve and c.can_compute_byte_plan and c.can_plan
    assert c.can_build_legacy_dsp_profile and c.can_execute_live
    assert c.live_block_reason is None
    d = r.dsp_profile
    assert d.start_frequency_hz == pytest.approx(77e9)
    assert d.slope_hz_per_s == pytest.approx(29.982e12)
    assert d.adc_sample_rate_hz == pytest.approx(10e6)
    assert d.adc_samples == 256 and d.rx_count == 4 and d.tx_mask == 1
    assert d.chirps_per_frame == 128
    assert d.frame_count == 9  # native incl. guard, same as structured path
    assert d.frame_period_s == pytest.approx(40e-3)
    assert d.idle_time_s == pytest.approx(100e-6) and d.ramp_end_time_s == pytest.approx(60e-6)
    assert d.sample_format == "real_int16" and d.cube_layout == "frame_chirp_rx_sample"
    assert r.byte_plan.native_dca_bytes == 4_718_592
    assert r.byte_plan.canonical_dca_bytes == 4_194_304


def test_seven_arg_framecfg_period(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(frame_args8=False, period=50))
    assert r.dsp_profile.frame_period_s == pytest.approx(50e-3)


def test_frequency_and_slope(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(start=78.5, slope=40.0))
    assert r.dsp_profile.start_frequency_hz == pytest.approx(78.5e9)
    assert r.dsp_profile.slope_hz_per_s == pytest.approx(40e12)
    assert r.capabilities.can_execute_live


def test_sample_count_doubles_bytes(project, tmp_path):
    a = res_for(project, tmp_path, make_cfg(samples=256), "a.cfg")
    b = res_for(project, tmp_path, make_cfg(samples=512), "b.cfg")
    assert b.dsp_profile.adc_samples == 512
    assert b.byte_plan.samples_per_chirp == 512
    assert b.byte_plan.native_dca_bytes == 2 * a.byte_plan.native_dca_bytes
    assert b.byte_plan.canonical_dca_bytes == 2 * a.byte_plan.canonical_dca_bytes


def test_rx_count_halves_bytes(project, tmp_path):
    a = res_for(project, tmp_path, make_cfg(rx=15), "a.cfg")
    b = res_for(project, tmp_path, make_cfg(rx=3), "b.cfg")
    assert b.dsp_profile.rx_count == 2 and b.byte_plan.rx_channels == 2
    assert b.byte_plan.native_active_payload_bytes * 2 == a.byte_plan.native_active_payload_bytes
    assert b.byte_plan.canonical_dca_bytes < a.byte_plan.canonical_dca_bytes


def test_sample_rate(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(rate=8000))
    assert r.dsp_profile.adc_sample_rate_hz == pytest.approx(8e6)


def test_timing(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(idle=50, ramp=80, period=33.5))
    d = r.dsp_profile
    assert d.idle_time_s == pytest.approx(50e-6)
    assert d.ramp_end_time_s == pytest.approx(80e-6)
    assert d.frame_period_s == pytest.approx(33.5e-3)


def test_chirp_loops_and_frames(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(loops=64), frames=5, guard=2)
    assert r.schedule.physical_chirps_per_frame == 64
    assert r.dsp_profile.chirps_per_frame == 64
    assert r.byte_plan.chirps_per_frame == 64
    assert r.dsp_profile.frame_count == 7
    assert r.byte_plan.total_frames == 7 and r.byte_plan.canonical_frames == 5


def test_tx_masks(project, tmp_path):
    assert res_for(project, tmp_path, make_cfg(chirp_tx=1), "a.cfg").dsp_profile.tx_mask == 1
    r3 = res_for(project, tmp_path, make_cfg(chirp_tx=3), "b.cfg")
    assert r3.dsp_profile.tx_mask == 3 and r3.capabilities.can_execute_live


def test_chirp_tx_not_subset_of_channel_tx_blocked(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(tx=1, chirp_tx=2))
    assert r.dsp_profile is None and not r.capabilities.can_build_legacy_dsp_profile
    assert not r.capabilities.can_execute_live
    assert "subset" in r.capabilities.live_block_reason


def test_per_chirp_variation_blocks_legacy_dsp_not_raw(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(extra_chirp="0 0 5 0"))  # idleTimeVar != 0
    assert r.dsp_profile is None and not r.capabilities.can_build_legacy_dsp_profile
    assert r.capabilities.can_execute_live


def test_cube_examples(project, tmp_path):
    # 4 RX / 256 samples ; 2 RX / 512 samples (64 loops, 8 canonical + 1 guard)
    a = res_for(project, tmp_path, make_cfg(rx=15, samples=256, loops=64), "a.cfg")
    b = res_for(project, tmp_path, make_cfg(rx=3, samples=512, loops=64), "b.cfg")
    assert (a.byte_plan.canonical_frames, a.byte_plan.chirps_per_frame, a.byte_plan.rx_channels,
            a.byte_plan.samples_per_chirp) == (8, 64, 4, 256)
    assert (b.byte_plan.canonical_frames, b.byte_plan.chirps_per_frame, b.byte_plan.rx_channels,
            b.byte_plan.samples_per_chirp) == (8, 64, 2, 512)
    # int16 payload 2 B/sample, DCA 2-lane expansion x2 -> canonical bytes
    assert a.byte_plan.canonical_active_payload_bytes == 8 * 64 * 4 * 256 * 2
    assert b.byte_plan.canonical_active_payload_bytes == 8 * 64 * 2 * 512 * 2


def test_smoke_regression(project):
    r = resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)
    assert tuple(r.cli_commands) == SMOKE_GOLDEN_CLI
    assert r.capabilities.can_execute_live and r.dsp_profile.tx_mask == 3
    assert r.byte_plan.native_dca_bytes == 4_718_592


# --- still blocked ------------------------------------------------------------
def test_tdm_resolves_raw_live_but_no_legacy_dsp(project, tmp_path):
    r = res_for(project, tmp_path, TDM2_CFG)
    assert r.capabilities.can_plan and r.capabilities.can_execute_live
    assert r.dsp_profile is None and not r.capabilities.can_build_legacy_dsp_profile
    assert not r.capabilities.can_run_tdm_dsp


def test_advanced_frame_still_blocked(project, tmp_path):
    with pytest.raises(ValueError, match="Advanced frame unsupported"):
        res_for(project, tmp_path, make_cfg() + "advFrameCfg 1 0 0 1 0\n")


def test_unknown_command_still_blocked(project, tmp_path):
    with pytest.raises(ValueError, match="Unknown commands"):
        res_for(project, tmp_path, make_cfg() + "fooBarCfg 1 2\n")


def test_invalid_byte_plan_still_blocked(project, tmp_path):
    with pytest.raises(ValueError):
        res_for(project, tmp_path, make_cfg(samples=0))
    with pytest.raises(ValueError, match="adcCfg b2AdcOutFmt"):
        res_for(project, tmp_path, make_cfg().replace("adcCfg 2 0", "adcCfg 2 1"))


# --- facade routing (mocked backend) --------------------------------------------
def _overrides():
    from awr2944_dca.api._session import ConnectionOverrides

    return ConnectionOverrides(
        com_port="COM1", host_ip="1.1.1.1", dca_ip="1.1.1.2",
        dca_control_exe=Path("x"), dca_record_exe=Path("x"),
        rf_api_dll=Path("x"), cf_json_path=Path("x"),
    )


def _fake_run_capture(seen):
    from awr2944_dca.capture_manifest import CaptureManifest
    from awr2944_dca.capture_session import CaptureResult

    def fake(*args, **kwargs):
        seen.update(kwargs)
        out = kwargs["output_dir"]
        out.mkdir(parents=True, exist_ok=True)
        manifest = CaptureManifest(
            total_frames=9, guard_frame_count=1, canonical_frame_count=8,
            native_sha256="a", canonical_sha256="b", packet_count=1, sequence_gaps=0,
            capture_timestamp="t", parser_layout_version="1", dsp_config_version="1", success=True,
        )
        return CaptureResult(capture_dir=out, manifest=manifest)

    return fake


def test_generic_cfg_reaches_production_backend(project, tmp_path):
    cfg = _cfg(tmp_path, make_cfg(rx=3, samples=512, loops=64, start=78, slope=35.0), "alireza.cfg")
    seen = {}
    with patch("awr2944_dca.capture_session.run_capture", _fake_run_capture(seen)):
        res = project.capture.run(
            profile=cfg, frames=8, guard_frames=1, name="alireza_test",
            connection_overrides=_overrides(),
        )
    assert res.success
    p = seen["profile"]
    assert (p.rx_count, p.adc_samples, p.chirps_per_frame, p.frame_count) == (2, 512, 64, 9)
    assert p.start_frequency_hz == pytest.approx(78e9) and p.slope_hz_per_s == pytest.approx(35e12)
    assert seen["guard_frames"] == 1
    cmds = tuple(seen["sdk_cli_commands"])
    assert "frameCfg 0 0 64 9 512 40 1 0" in cmds
    assert "profileCfg 0 78 100 6 60 0 0 35.0 1 512 10000 0 0 30" in cmds
    assert "sensorStart" not in cmds
    plan = res.capture_plan
    assert plan["expected_native_dca_bytes"] == res.capture_plan["expected_native_dca_bytes"] > 0
    # provenance artifacts written
    cap_dir = res.capture.path if hasattr(res.capture, "path") else None
    if cap_dir is not None:
        assert (Path(cap_dir) / "resolved_config.cfg").exists()
        assert (Path(cap_dir) / "source_config.cfg").exists()
        assert (Path(cap_dir) / "config_summary.json").exists()


def test_invalid_layout_run_touches_no_hardware(project, tmp_path):
    from tests.test_schedule_integration import BAD_RAW_CFG
    with _HardwareTripwire() as trip:
        with pytest.raises(LiveExecutionNotEnabledError):
            project.capture.run(profile=_cfg(tmp_path, BAD_RAW_CFG), frames=8, guard_frames=1)
    assert trip.calls == []
