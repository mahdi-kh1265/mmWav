"""Raw basic-frame capture decoupled from DSP support (multi-chirp schedules).

No hardware: the frozen low-level ``run_capture`` is mocked, and a tripwire proves
invalid configs never reach any hardware entry point.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from awr2944_dca.api._config_resolver import LiveExecutionNotEnabledError, resolve_capture_config
from awr2944_dca.capture_layout import CaptureDataLayout, ScheduleAwareDspRequiredError
from awr2944_dca.capture_manifest import profile_from_manifest_dict, profile_to_manifest_dict
from tests.test_generic_single_chirp import (
    _fake_run_capture,
    _overrides,
    make_cfg,
    res_for,
)
from tests.test_schedule_integration import (  # noqa: F401  (fixtures/consts)
    BAD_RAW_CFG,
    COMMON,
    SMOKE_GOLDEN_CLI,
    TDM2_CFG,
    TDM3_CFG,
    _HardwareTripwire,
    _cfg,
    project,
)


def tdm_cfg(*, rx=15, samples=256, rate=10000, masks=(1, 2), loops=64, tx=7, extra="0 0 0 0"):
    n = len(masks)
    chirps = "".join(f"chirpCfg {i} {i} 0 {extra} {m}\n" for i, m in enumerate(masks))
    return (
        "flushCfg\ndfeDataOutputMode 1\n"
        f"channelCfg {rx} {tx} 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\n"
        f"profileCfg 0 77 100 6 60 0 0 29.982 1 {samples} {rate} 0 0 30\n"
        + chirps
        + f"frameCfg 0 {n - 1} {loops} 8 {samples} 40 1 0\n"
        + "lvdsStreamCfg -1 0 1 0\n"
    )


# 1/2. regressions -------------------------------------------------------------
def test_smoke_regression_unchanged(project):
    r = resolve_capture_config(project, "smoke_v1", frames=8, guard_frames=1)
    assert tuple(r.cli_commands) == SMOKE_GOLDEN_CLI
    assert r.capabilities.can_execute_live and r.capabilities.can_build_legacy_dsp_profile
    assert r.dsp_profile is not None
    assert r.byte_plan.native_dca_bytes == 4_718_592 and r.byte_plan.canonical_dca_bytes == 4_194_304
    assert r.capture_layout.canonical_cube_shape == (8, 128, 4, 256)
    assert r.capture_layout.requires_schedule_aware_dsp is False


def test_generic_single_chirp_still_live_with_dsp(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(rx=3, samples=512, loops=64))
    assert r.capabilities.can_execute_live and r.capabilities.can_build_legacy_dsp_profile
    assert r.dsp_profile.rx_count == 2 and r.dsp_profile.adc_samples == 512


# 3. 2-TX TDM ------------------------------------------------------------------
def test_two_tx_tdm_live_capable(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg())
    assert r.schedule.tx_masks_per_cycle == (1, 2)
    assert r.schedule.physical_chirps_per_frame == 128
    assert r.byte_plan.native_dca_bytes == 4_718_592
    assert r.byte_plan.canonical_dca_bytes == 4_194_304
    lay = r.capture_layout
    assert isinstance(lay, CaptureDataLayout)
    assert lay.canonical_cube_shape == (8, 128, 4, 256)
    assert lay.frame_count == 9 and lay.guard_frames == 1
    assert lay.requires_schedule_aware_dsp is True
    c = r.capabilities
    assert c.can_resolve and c.can_compute_byte_plan and c.can_plan and c.can_execute_live
    assert not c.can_build_legacy_dsp_profile and not c.can_run_tdm_dsp and not c.can_run_aoa
    assert r.dsp_profile is None


# 4. 3-chirp -------------------------------------------------------------------
def test_three_chirp_live_capable(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(masks=(1, 4, 2), loops=32))
    assert r.schedule.physical_chirps_per_frame == 96
    assert r.capture_layout.canonical_cube_shape == (8, 96, 4, 256)
    assert r.capabilities.can_execute_live and not r.capabilities.can_build_legacy_dsp_profile


# 5. routing with mocked run_capture --------------------------------------------
def test_two_tx_tdm_reaches_run_capture(project, tmp_path):
    cfg = _cfg(tmp_path, tdm_cfg(), "tdm.cfg")
    seen = {}
    with patch("awr2944_dca.capture_session.run_capture", _fake_run_capture(seen)):
        res = project.capture.run(
            profile=cfg, frames=8, guard_frames=1, name="tdm_raw", connection_overrides=_overrides()
        )
    assert res.success
    lay = seen["profile"]
    assert isinstance(lay, CaptureDataLayout)  # not a fabricated DspRadarProfile
    assert (lay.frame_count, lay.chirps_per_frame, lay.rx_count, lay.adc_samples) == (9, 128, 4, 256)
    assert seen["guard_frames"] == 1
    cmds = tuple(seen["sdk_cli_commands"])
    assert "chirpCfg 0 0 0 0 0 0 0 1" in cmds and "chirpCfg 1 1 0 0 0 0 0 2" in cmds
    assert "frameCfg 0 1 64 9 256 40 1 0" in cmds
    assert "sensorStart" not in cmds
    assert res.capture_plan["expected_native_dca_bytes"] == 4_718_592
    assert res.capture_plan["expected_canonical_dca_bytes"] == 4_194_304
    # the exact object passed downstream is serialisable by the frozen manifest writer
    d = profile_to_manifest_dict(lay)
    assert d["chirps_per_frame"] == 128 and d["tx_masks_per_cycle"] == (1, 2)


# 6. provenance ------------------------------------------------------------------
def test_multichirp_provenance_reconstructs_schedule(project, tmp_path):
    cfg = _cfg(tmp_path, tdm_cfg(masks=(1, 4, 2), loops=32), "p.cfg")
    seen = {}
    with patch("awr2944_dca.capture_session.run_capture", _fake_run_capture(seen)):
        project.capture.run(
            profile=cfg, frames=8, guard_frames=1, name="prov", connection_overrides=_overrides()
        )
    out = Path(seen["output_dir"])
    summary = json.loads((out / "config_summary.json").read_text(encoding="utf-8"))
    s = summary["schedule"]
    assert (s["chirp_start_index"], s["chirp_end_index"]) == (0, 2)
    assert s["loops_per_frame"] == 32 and s["physical_chirps_per_frame"] == 96
    assert s["tx_masks_per_cycle"] == [1, 4, 2] and s["profile_ids_per_cycle"] == [0, 0, 0]
    assert [c["tx_enable_mask"] for c in s["chirp_definitions"]] == [1, 4, 2]
    assert summary["capture_layout"]["canonical_cube_shape"] == [8, 96, 4, 256]
    assert summary["capture_layout"]["cube_axes"] == ["frame", "physical_chirp", "rx", "sample"]
    assert summary["capture_layout"]["requires_schedule_aware_dsp"] is True
    assert summary["capabilities"]["can_execute_live"] is True
    assert summary["capabilities"]["can_build_legacy_dsp_profile"] is False
    assert summary["byte_plan"] if "byte_plan" in summary else summary["native_dca_bytes"]
    assert summary["resolved_config_sha256"]
    assert (out / "resolved_config.cfg").exists() and (out / "source_config.cfg").exists()
    # physical order: chirp i uses tx_masks[i % 3]
    full = [s["tx_masks_per_cycle"][i % 3] for i in range(s["physical_chirps_per_frame"])]
    assert full[:6] == [1, 4, 2, 1, 4, 2] and len(full) == 96


# 7. DSP guard ------------------------------------------------------------------
def test_legacy_dsp_refuses_tdm_capture(project, tmp_path):
    cfg = _cfg(tmp_path, tdm_cfg(), "d.cfg")
    seen = {}
    with patch("awr2944_dca.capture_session.run_capture", _fake_run_capture(seen)):
        res = project.capture.run(
            profile=cfg, frames=8, guard_frames=1, name="dspguard", connection_overrides=_overrides()
        )
    out = Path(seen["output_dir"])
    (out / "manifest.json").write_text(
        json.dumps({"profile": profile_to_manifest_dict(seen["profile"]), "canonical_frame_count": 8}),
        encoding="utf-8",
    )
    with pytest.raises(ScheduleAwareDspRequiredError, match="requires schedule-aware DSP"):
        res.capture._resolve_viewer_profile()
    with pytest.raises(ScheduleAwareDspRequiredError):
        profile_from_manifest_dict(profile_to_manifest_dict(seen["profile"]))


def test_dsp_guard_does_not_affect_regular_profiles():
    from awr2944_dca.dsp.config import RadarProfile

    p = RadarProfile.from_smoke_v1()
    assert profile_from_manifest_dict(profile_to_manifest_dict(p)) == p


# 8. byte equivalence -------------------------------------------------------------
def test_byte_equivalence_single_vs_tdm(project, tmp_path):
    a = res_for(project, tmp_path, make_cfg(loops=128), "a.cfg")
    b = res_for(project, tmp_path, tdm_cfg(loops=64), "b.cfg")
    assert dataclasses.asdict(a.byte_plan) == dataclasses.asdict(b.byte_plan)
    assert a.capture_layout.canonical_cube_shape == b.capture_layout.canonical_cube_shape


# 9/10. TDM with RX / sample variation ----------------------------------------------
def test_tdm_two_rx(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(rx=3))
    assert r.capture_layout.canonical_cube_shape == (8, 128, 2, 256)
    assert r.byte_plan.canonical_active_payload_bytes == 8 * 128 * 2 * 256 * 2
    assert r.capabilities.can_execute_live


def test_tdm_512_samples(project, tmp_path):
    base = res_for(project, tmp_path, tdm_cfg(), "a.cfg")
    r = res_for(project, tmp_path, tdm_cfg(samples=512), "b.cfg")
    assert r.capture_layout.canonical_cube_shape == (8, 128, 4, 512)
    assert r.byte_plan.native_dca_bytes == 2 * base.byte_plan.native_dca_bytes
    assert r.capabilities.can_execute_live


# 11. per-chirp TX / RF variation accepted for raw -----------------------------------
def test_per_chirp_tx_variation_accepted(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(masks=(1, 2, 4, 2)))
    assert r.schedule.tx_masks_per_cycle == (1, 2, 4, 2)
    assert r.capabilities.can_execute_live and not r.capabilities.can_build_legacy_dsp_profile


def test_per_chirp_rf_variation_accepted_for_raw(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(extra="10 0.5 2 1"))
    assert r.capabilities.can_execute_live
    assert r.dsp_profile is None
    assert r.schedule.chirps[0].freq_slope_var_mhz_per_us == 0.5


# 12-15. still rejected / zero hardware ------------------------------------------------
def test_undefined_chirp_rejected(project, tmp_path):
    text = COMMON + "chirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 1 64 8 100 1 0\n"
    with pytest.raises(ValueError, match="no chirpCfg defines it"):
        res_for(project, tmp_path, text)


def test_ambiguous_chirp_rejected(project, tmp_path):
    text = COMMON + "chirpCfg 0 1 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0\n"
    with pytest.raises(ValueError, match="overlapping"):
        res_for(project, tmp_path, text)


def test_advanced_frame_rejected(project, tmp_path):
    with pytest.raises(ValueError, match="Advanced frame unsupported"):
        res_for(project, tmp_path, tdm_cfg() + "advFrameCfg 1 0 0 1 0\n")


def test_advanced_chirp_rejected(project, tmp_path):
    with pytest.raises(ValueError, match="Advanced chirp unsupported"):
        res_for(project, tmp_path, tdm_cfg() + "advChirpCfg 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n")


def test_unsupported_adc_format_rejected(project, tmp_path):
    with pytest.raises(ValueError, match="b2AdcOutFmt"):
        res_for(project, tmp_path, tdm_cfg().replace("adcCfg 2 0", "adcCfg 2 1"))
    with pytest.raises(ValueError, match="Only 16-bit"):
        res_for(project, tmp_path, tdm_cfg().replace("adcCfg 2 0", "adcCfg 1 0"))


def test_profile_id_mismatch_blocks_live_before_hardware(project, tmp_path):
    r = res_for(project, tmp_path, BAD_RAW_CFG)
    assert not r.capabilities.can_execute_live
    assert "profileId" in r.capabilities.live_block_reason
    with _HardwareTripwire() as trip:
        with pytest.raises(LiveExecutionNotEnabledError):
            project.capture.run(profile=_cfg(tmp_path, BAD_RAW_CFG, "x.cfg"), frames=8, guard_frames=1)
    assert trip.calls == []


def test_invalid_config_fails_before_run_capture(project, tmp_path):
    with _HardwareTripwire() as trip:
        with pytest.raises(ValueError):
            project.capture.run(profile=_cfg(tmp_path, tdm_cfg(rx=0), "z.cfg"), frames=8, guard_frames=1)
    assert trip.calls == []
