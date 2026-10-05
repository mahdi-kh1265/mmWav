"""Red-team audit regression tests (no hardware).

Oracles here are written from TI semantics / first principles, never by calling the
implementation under test:
  native DCA bytes = native_frames * physical_chirps * rx * samples * 2 B (int16) * 2
  (the demo fixes lvdsLaneEnable=0x3, i.e. 2 lanes -> 4 word slots, expansion x2).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from awr2944_dca.api._config_resolver import (
    HARDWARE_VERIFIED_RX_MASKS,
    UNVERIFIED_RX_MASKS,
    resolve_capture_config,
)
from awr2944_dca.capture_layout import ScheduleAwareDspRequiredError
from awr2944_dca.capture_manifest import profile_from_manifest_dict
from tests.test_generic_single_chirp import make_cfg, res_for
from tests.test_raw_capture_decoupled import tdm_cfg
from tests.test_schedule_integration import project  # noqa: F401  (fixture)

MASK_COUNTS = {0b1111: 4, 0b0011: 2, 0b0101: 2, 0b0110: 2, 0b1001: 2, 0b1010: 2, 0b1100: 2}
CHIRP_MASKS = {1: (1,), 2: (1, 2), 3: (1, 2, 4)}


# ---------------------------------------------------------------- byte oracle matrix
@pytest.mark.parametrize("frames,guard", [(1, 0), (8, 1), (32, 0), (32, 1)])
@pytest.mark.parametrize("cyc", [1, 2, 3])
@pytest.mark.parametrize("loops", [1, 4, 64, 128])
@pytest.mark.parametrize("samples", [64, 256, 512])
def test_byte_plan_matches_independent_oracle(project, tmp_path, frames, guard, cyc, loops, samples):
    r = res_for(
        project, tmp_path,
        tdm_cfg(masks=CHIRP_MASKS[cyc], loops=loops, samples=samples),
        frames=frames, guard=guard,
    )
    chirps = cyc * loops
    native = (frames + guard) * chirps * 4 * samples * 2 * 2
    canonical = frames * chirps * 4 * samples * 2 * 2
    assert r.byte_plan.native_dca_bytes == native
    assert r.byte_plan.canonical_dca_bytes == canonical
    assert r.capture_layout.canonical_cube_shape == (frames, chirps, 4, samples)
    assert r.capture_layout.frame_count == frames + guard
    assert r.capabilities.can_execute_live
    assert f"frameCfg 0 {cyc - 1} {loops} {frames + guard} " in "\n".join(r.cli_commands)


@pytest.mark.parametrize("mask", sorted(MASK_COUNTS))
def test_rx_mask_byte_scaling_and_provenance(project, tmp_path, mask):
    r = res_for(project, tmp_path, tdm_cfg(rx=mask, masks=(1, 2), loops=8), frames=4, guard=1)
    rx = MASK_COUNTS[mask]
    assert r.byte_plan.native_dca_bytes == 5 * 16 * rx * 256 * 4
    lay = r.capture_layout
    assert lay.rx_mask == mask
    assert lay.active_rx_channels == tuple(i for i in range(4) if mask >> i & 1)
    assert lay.canonical_cube_shape[2] == rx
    if mask in HARDWARE_VERIFIED_RX_MASKS:
        assert not any("hardware-verified" in w for w in r.preflight_warnings)
    else:
        assert mask in UNVERIFIED_RX_MASKS
        assert any("not hardware-verified" in w for w in r.preflight_warnings)


@pytest.mark.parametrize("mask", [0b0001, 0b0010, 0b0100, 0b1000, 0b0111, 0b1011, 0b1101, 0b1110])
def test_one_and_three_rx_fail_closed(project, tmp_path, mask):
    r = res_for(project, tmp_path, tdm_cfg(rx=mask, masks=(1,), loops=8))
    assert not r.capabilities.can_execute_live
    assert "not a supported raw-stream mode" in r.capabilities.live_block_reason


def test_rx_mask_zero_or_out_of_range_not_live(project, tmp_path):
    for bad in (0, 16, 31):
        try:
            r = res_for(project, tmp_path, tdm_cfg(rx=bad, masks=(1,), loops=8), name=f"r{bad}.cfg")
        except ValueError:
            continue
        assert not r.capabilities.can_execute_live


def test_odd_samples_blocked(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(masks=(1,), loops=8, samples=255))
    assert not r.capabilities.can_execute_live


# ---------------------------------------------------------------- numFrames semantics
def test_infinite_numframes_without_override_rejected(project, tmp_path):
    text = make_cfg(frames=0)
    with pytest.raises(ValueError, match="INFINITE"):
        resolve_capture_config(project, _write(tmp_path, text), frames=None, guard_frames=1)


def test_infinite_numframes_with_explicit_override_is_finite(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(frames=0), frames=4, guard=1)
    assert r.byte_plan.total_frames == 5
    assert "frameCfg 0 0 128 5 256 40 1 0" in r.cli_commands
    assert any("infinite" in w for w in r.preflight_warnings)


def test_negative_numframes_rejected(project, tmp_path):
    with pytest.raises(ValueError):
        res_for(project, tmp_path, make_cfg(frames=-1), frames=4)


def test_zero_requested_frames_rejected(project, tmp_path):
    with pytest.raises(ValueError):
        res_for(project, tmp_path, make_cfg(), frames=0, guard=1)


def test_numloops_range_enforced(project, tmp_path):
    with pytest.raises(ValueError, match="numLoops"):
        res_for(project, tmp_path, make_cfg(loops=256))
    with pytest.raises(ValueError, match="numLoops"):
        res_for(project, tmp_path, make_cfg(loops=0))


def test_frame_override_rewrites_only_numframes(project, tmp_path):
    r = res_for(project, tmp_path, make_cfg(frames=8, period=37), frames=5, guard=1)
    assert "frameCfg 0 0 128 6 256 37 1 0" in r.cli_commands


# ---------------------------------------------------------------- stream / layout gates
def _replace(text, old, new):
    assert old in text
    return text.replace(old, new)


def _live(project, tmp_path, text, name="a.cfg"):
    return res_for(project, tmp_path, text, name=name).capabilities


def test_noninterleaved_adcbuf_required(project, tmp_path):
    t = _replace(make_cfg(), "adcbufCfg -1 1 1 1 1", "adcbufCfg -1 1 1 0 1")  # interleaved
    assert not _live(project, tmp_path, t).can_execute_live


def test_chirp_threshold_must_be_one(project, tmp_path):
    t = _replace(make_cfg(), "adcbufCfg -1 1 1 1 1", "adcbufCfg -1 1 1 1 2")
    assert not _live(project, tmp_path, t).can_execute_live


def test_missing_lvds_blocks_live(project, tmp_path):
    t = _replace(make_cfg(), "lvdsStreamCfg -1 0 1 0\n", "")
    assert not _live(project, tmp_path, t).can_execute_live


@pytest.mark.parametrize("bad", ["lvdsStreamCfg -1 1 1 0", "lvdsStreamCfg -1 0 4 0",
                                 "lvdsStreamCfg -1 0 0 0", "lvdsStreamCfg -1 0 1 1"])
def test_wrong_lvds_stream_blocks_live(project, tmp_path, bad):
    t = _replace(make_cfg(), "lvdsStreamCfg -1 0 1 0", bad)
    assert not _live(project, tmp_path, t).can_execute_live


def test_duplicate_lvds_blocks_live(project, tmp_path):
    t = make_cfg() + "lvdsStreamCfg -1 0 1 0\n"
    assert not _live(project, tmp_path, t).can_execute_live


def test_hw_trigger_blocks_live(project, tmp_path):
    t = _replace(make_cfg(), "frameCfg 0 0 128 8 256 40 1 0", "frameCfg 0 0 128 8 256 40 2 0")
    assert not _live(project, tmp_path, t).can_execute_live


def test_dfe_mode_required(project, tmp_path):
    with pytest.raises(ValueError, match="dfeDataOutputMode"):
        res_for(project, tmp_path, make_cfg().replace("dfeDataOutputMode 1\n", ""))
    with pytest.raises(ValueError, match="dfeDataOutputMode"):
        res_for(project, tmp_path, make_cfg().replace("dfeDataOutputMode 1", "dfeDataOutputMode 3"))


def test_extra_arguments_rejected(project, tmp_path):
    with pytest.raises(ValueError):
        res_for(project, tmp_path, make_cfg().replace("adcCfg 2 0", "adcCfg 2 0 1"))


def test_malformed_numeric_values_raise_valueerror(project, tmp_path):
    with pytest.raises(ValueError):
        res_for(project, tmp_path, make_cfg().replace("channelCfg 15", "channelCfg xx"))


# ---------------------------------------------------------------- chirp schedule adversarial
def _tdm_text(chirp_lines, frame):
    base = tdm_cfg(masks=(1,), loops=8)
    head = base.split("chirpCfg")[0]
    return head + chirp_lines + frame + "lvdsStreamCfg -1 0 1 0\n"


def test_range_chirpcfg_expands(project, tmp_path):
    t = _tdm_text("chirpCfg 0 2 0 0 0 0 0 1\n", "frameCfg 0 2 8 8 256 40 1 0\n")
    r = res_for(project, tmp_path, t)
    assert r.schedule.chirps_per_cycle == 3 and r.capture_layout.chirps_per_frame == 24


def test_adjacent_ranges_and_out_of_order(project, tmp_path):
    t = _tdm_text("chirpCfg 2 3 0 0 0 0 0 4\nchirpCfg 0 1 0 0 0 0 0 1\n",
                  "frameCfg 0 3 8 8 256 40 1 0\n")
    r = res_for(project, tmp_path, t)
    assert r.schedule.tx_masks_per_cycle == (1, 1, 4, 4)


def test_overlap_gap_and_wrong_profile_fail_closed(project, tmp_path):
    overlap = _tdm_text("chirpCfg 0 1 0 0 0 0 0 1\nchirpCfg 1 2 0 0 0 0 0 2\n", "frameCfg 0 2 8 8 256 40 1 0\n")
    with pytest.raises(ValueError):
        res_for(project, tmp_path, overlap, name="o.cfg")
    gap = _tdm_text("chirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 2 2 0 0 0 0 0 2\n", "frameCfg 0 2 8 8 256 40 1 0\n")
    with pytest.raises(ValueError):
        res_for(project, tmp_path, gap, name="g.cfg")
    wrong = _tdm_text("chirpCfg 0 0 5 0 0 0 0 1\n", "frameCfg 0 0 8 8 256 40 1 0\n")
    try:
        r = res_for(project, tmp_path, wrong, name="w.cfg")
        assert not r.capabilities.can_execute_live
    except ValueError:
        pass


def test_frame_subset_of_defined_chirps(project, tmp_path):
    t = _tdm_text("chirpCfg 0 3 0 0 0 0 0 1\n", "frameCfg 1 2 8 8 256 40 1 0\n")
    r = res_for(project, tmp_path, t)
    assert r.schedule.chirps_per_cycle == 2 and r.capture_layout.chirps_per_frame == 16


def test_tx_not_enabled_by_channelcfg_blocks_live(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(tx=1, masks=(1, 2), loops=8))
    assert not r.capabilities.can_execute_live


def test_multi_tx_simultaneous_and_changing_masks_physical_order(project, tmp_path):
    r = res_for(project, tmp_path, tdm_cfg(masks=(3, 4, 5), loops=2))
    assert r.schedule.tx_masks_per_cycle == (3, 4, 5)
    assert r.capabilities.can_execute_live and not r.capabilities.can_build_legacy_dsp_profile


# ---------------------------------------------------------------- manifest compat
def test_manifest_legacy_and_tdm_dsp_guard():
    legacy = {"frame_count": 9, "chirps_per_frame": 128, "rx_count": 4, "adc_samples": 256,
              "start_frequency_hz": 77e9}
    try:
        profile_from_manifest_dict(legacy)
    except ScheduleAwareDspRequiredError:
        pytest.fail("legacy manifest must stay loadable")
    except (TypeError, KeyError, ValueError):
        pass  # incompatible fixture shape is fine; only the guard error is forbidden
    with pytest.raises(ScheduleAwareDspRequiredError):
        profile_from_manifest_dict({"profile_kind": "capture_layout"})


def _write(tmp_path: Path, text: str, name="x.cfg") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p
