"""Offline tests for the generic basic-frame chirp schedule model. No hardware."""

import dataclasses
import socket

import pytest

from awr2944_dca import awr2944_adc
from awr2944_dca.chirp_schedule import (
    BasicFrameSchedule,
    ChirpScheduleError,
    ResolvedChirp,
    parse_chirp_cfg,
    resolve_chirp_schedule,
    resolve_schedule_from_cli,
    schedule_byte_plan,
)

SMOKE_CLI = (
    "chirpCfg 0 0 0 0 0 0 0 3",
    "frameCfg 0 0 128 9 256 40 1 0",
)
TDM2_CLI = (
    "chirpCfg 0 0 0 0 0 0 0 1",
    "chirpCfg 1 1 0 0 0 0 0 2",
    "frameCfg 0 1 64 9 256 40 1 0",
)
TDM3_CLI = (
    "chirpCfg 0 0 0 0 0 0 0 1",
    "chirpCfg 1 1 0 0 0 0 0 4",
    "chirpCfg 2 2 0 0 0 0 0 2",
    "frameCfg 0 2 32 9 256 40 1 0",
)


def _chirp(i, tx):
    return ResolvedChirp(chirp_index=i, profile_id=0, tx_enable_mask=tx)


# --- smoke -----------------------------------------------------------------
def test_smoke_schedule():
    s = resolve_schedule_from_cli(SMOKE_CLI)
    assert s.is_single_chirp and s.is_uniform_tx
    assert s.chirps_per_cycle == 1
    assert s.tx_masks_per_cycle == (3,)
    assert s.loops_per_frame == 128
    assert s.physical_chirps_per_frame == 128
    assert s.chirp_indices_per_cycle == (0,)
    assert s.frame_count == 9
    assert s.frame_period_ms == 40.0


def test_smoke_byte_plan_known_capture():
    s = resolve_schedule_from_cli(SMOKE_CLI)
    bp = schedule_byte_plan(s, rx_channels=4, samples_per_chirp=256, canonical_frames=8, guard_frames=1)
    assert bp.total_frames == 9
    assert bp.canonical_frames == 8
    assert bp.native_dca_bytes == 4_718_592
    assert bp.canonical_dca_bytes == 4_194_304
    assert bp.canonical_active_payload_bytes == 2_097_152
    assert bp.native_active_payload_bytes == 2_359_296


def test_byte_plan_defaults_canonical_from_frame_count():
    s = resolve_schedule_from_cli(SMOKE_CLI)
    bp = schedule_byte_plan(s, 4, 256, guard_frames=1)
    assert bp.canonical_frames == 8
    assert bp.canonical_dca_bytes == 4_194_304


# --- TDM -------------------------------------------------------------------
def test_two_tx_tdm():
    s = resolve_schedule_from_cli(TDM2_CLI)
    assert s.tx_masks_per_cycle == (1, 2)
    assert s.chirps_per_cycle == 2
    assert s.loops_per_frame == 64
    assert s.physical_chirps_per_frame == 128
    assert not s.is_single_chirp and not s.is_uniform_tx
    exp = s.expanded_tx_masks()
    assert len(exp) == 128
    assert exp[:6] == (1, 2, 1, 2, 1, 2)
    assert exp == (1, 2) * 64


def test_three_tx_tdm():
    s = resolve_schedule_from_cli(TDM3_CLI)
    assert s.tx_masks_per_cycle == (1, 4, 2)
    assert s.physical_chirps_per_frame == 96
    exp = s.expanded_tx_masks()
    assert len(exp) == 96
    assert exp[:7] == (1, 4, 2, 1, 4, 2, 1)
    assert s.chirp_indices_per_cycle == (0, 1, 2)


def test_uniform_multi_chirp():
    s = resolve_chirp_schedule(
        [parse_chirp_cfg("0 1 0 0 0 0 0 1".split())],
        chirp_start_index=0, chirp_end_index=1, loops_per_frame=4,
    )
    assert s.is_uniform_tx and not s.is_single_chirp
    assert s.physical_chirps_per_frame == 8


def test_nonzero_start_index():
    s = resolve_chirp_schedule(
        [parse_chirp_cfg("2 3 0 0 0 0 0 1".split())],
        chirp_start_index=2, chirp_end_index=3, loops_per_frame=2,
    )
    assert s.chirp_indices_per_cycle == (2, 3)
    assert s.physical_chirps_per_frame == 4


# --- chirpCfg range semantics ---------------------------------------------
def test_chirpcfg_range_expands_to_each_index():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 2 0 0 0 0 0 1", "frameCfg 0 2 10 5 100 1 0"]
    )
    assert s.chirps_per_cycle == 3
    assert s.tx_masks_per_cycle == (1, 1, 1)
    assert [c.chirp_index for c in s.chirps] == [0, 1, 2]


def test_chirpcfg_range_mixed_with_single():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 1 0 0 0 0 0 1", "chirpCfg 2 2 0 0 0 0 0 2", "frameCfg 0 2 4 1 100 1 0"]
    )
    assert s.tx_masks_per_cycle == (1, 1, 2)


def test_chirpcfg_wider_than_frame_uses_only_frame_subset():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 5 0 0 0 0 0 1", "frameCfg 1 2 4 1 100 1 0"]
    )
    assert s.chirp_indices_per_cycle == (1, 2)


def test_unused_chirp_definition_is_ignored():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 0 0 0 0 0 0 1", "chirpCfg 1 1 0 0 0 0 0 2", "frameCfg 0 0 128 8 100 1 0"]
    )
    assert s.is_single_chirp and s.tx_masks_per_cycle == (1,)


def test_chirpcfg_variation_fields_preserved():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 0 3 1.5 0.25 2.0 0.5 1", "frameCfg 0 0 4 1 100 1 0"]
    )
    c = s.chirps[0]
    assert c.profile_id == 3
    assert c.start_freq_var_hz == 1.5
    assert c.freq_slope_var_mhz_per_us == 0.25
    assert c.idle_time_var_us == 2.0
    assert c.adc_start_time_var_us == 0.5


def test_overlapping_chirpcfg_fails_closed():
    with pytest.raises(ChirpScheduleError, match="overlapping"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 1 0 0 0 0 0 1", "chirpCfg 1 1 0 0 0 0 0 2", "frameCfg 0 1 4 1 100 1 0"]
        )


def test_overlapping_identical_chirpcfg_still_fails_closed():
    with pytest.raises(ChirpScheduleError, match="overlapping"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 0 0 0 0 0 0 1", "chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 4 1 100 1 0"]
        )


def test_overlap_outside_frame_range_is_irrelevant():
    s = resolve_schedule_from_cli(
        ["chirpCfg 0 0 0 0 0 0 0 1", "chirpCfg 5 6 0 0 0 0 0 1", "chirpCfg 6 6 0 0 0 0 0 2",
         "frameCfg 0 0 4 1 100 1 0"]
    )
    assert s.tx_masks_per_cycle == (1,)


# --- failure modes ---------------------------------------------------------
def test_missing_chirp_definition_is_explicit_error():
    with pytest.raises(ChirpScheduleError, match="chirp index 1"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 1 64 1 100 1 0"])


def test_no_chirpcfg_at_all():
    with pytest.raises(ChirpScheduleError, match="no chirpCfg"):
        resolve_schedule_from_cli(["frameCfg 0 0 64 1 100 1 0"])


def test_invalid_frame_range_is_explicit_error():
    with pytest.raises(ChirpScheduleError, match="Invalid frame chirp range"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 1 0 0 0 0 0 1", "frameCfg 1 0 64 1 100 1 0"]
        )


def test_frame_range_out_of_bounds():
    with pytest.raises(ChirpScheduleError, match="outside valid range"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 600 64 1 100 1 0"]
        )


def test_negative_frame_index():
    with pytest.raises(ChirpScheduleError, match="outside valid range"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg -1 0 64 1 100 1 0"])


@pytest.mark.parametrize("loops", [0, -1])
def test_invalid_loops(loops):
    with pytest.raises(ChirpScheduleError, match="loops_per_frame"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 0 0 0 0 0 0 1", f"frameCfg 0 0 {loops} 1 100 1 0"]
        )


def test_negative_frame_count():
    with pytest.raises(ChirpScheduleError, match="frame_count"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 4 -1 100 1 0"])


def test_missing_frame_cfg():
    with pytest.raises(ChirpScheduleError, match="Missing frameCfg"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1"])


def test_multiple_frame_cfg():
    with pytest.raises(ChirpScheduleError, match="Multiple frameCfg"):
        resolve_schedule_from_cli(
            ["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 4 1 100 1 0", "frameCfg 0 0 4 1 100 1 0"]
        )


def test_malformed_chirpcfg_arg_count():
    with pytest.raises(ChirpScheduleError, match="8 arguments"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 1", "frameCfg 0 0 4 1 100 1 0"])


def test_malformed_framecfg_arg_count():
    with pytest.raises(ChirpScheduleError, match="7 or 8 arguments"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 4"])


def test_non_numeric_token():
    with pytest.raises(ChirpScheduleError, match="expected integer"):
        resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 x", "frameCfg 0 0 4 1 100 1 0"])


@pytest.mark.parametrize("tx", [0, 16, -1])
def test_invalid_tx_mask(tx):
    with pytest.raises(ChirpScheduleError, match="txEnable"):
        parse_chirp_cfg(f"0 0 0 0 0 0 0 {tx}".split())


def test_chirpcfg_reversed_range():
    with pytest.raises(ChirpScheduleError, match="range start"):
        parse_chirp_cfg("2 1 0 0 0 0 0 1".split())


def test_direct_construction_validates_chirps_match_range():
    with pytest.raises(ChirpScheduleError, match="do not match"):
        BasicFrameSchedule(0, 1, 4, 1, 100.0, (_chirp(0, 1),))


def test_direct_construction_validates_range_order():
    with pytest.raises(ChirpScheduleError, match="Invalid frame chirp range"):
        BasicFrameSchedule(1, 0, 4, 1, 100.0, ())


# --- frameCfg forms --------------------------------------------------------
def test_seven_arg_frame_cfg_period():
    s = resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 128 8 100 1 0"])
    assert s.frame_count == 8 and s.frame_period_ms == 100.0


def test_eight_arg_frame_cfg_period():
    s = resolve_schedule_from_cli(["chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 128 8 256 40 1 0"])
    assert s.frame_count == 8 and s.frame_period_ms == 40.0


def test_unrelated_and_blank_commands_ignored():
    s = resolve_schedule_from_cli(
        ["flushCfg", "", "channelCfg 15 7 0", "chirpCfg 0 0 0 0 0 0 0 1", "frameCfg 0 0 4 1 100 1 0"]
    )
    assert s.physical_chirps_per_frame == 4


# --- immutability ----------------------------------------------------------
def test_schedule_is_frozen():
    s = resolve_schedule_from_cli(SMOKE_CLI)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.loops_per_frame = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.chirps[0].tx_enable_mask = 1  # type: ignore[misc]
    assert isinstance(s.chirps, tuple)


# --- byte plans ------------------------------------------------------------
def test_byte_plan_equivalence_single_vs_two_chirp_cycle():
    single = resolve_schedule_from_cli(
        ["chirpCfg 0 0 0 0 0 0 0 3", "frameCfg 0 0 128 9 256 40 1 0"]
    )
    two = resolve_schedule_from_cli(TDM2_CLI)
    kwargs = dict(rx_channels=4, samples_per_chirp=256, canonical_frames=8, guard_frames=1)
    assert single.physical_chirps_per_frame == two.physical_chirps_per_frame == 128
    a = schedule_byte_plan(single, **kwargs)
    b = schedule_byte_plan(two, **kwargs)
    assert a == b
    assert b.native_dca_bytes == 4_718_592
    assert b.canonical_dca_bytes == 4_194_304


def test_three_tx_byte_plan():
    s = resolve_schedule_from_cli(TDM3_CLI)
    bp = schedule_byte_plan(s, 4, 256, canonical_frames=8, guard_frames=1)
    # 96 chirps/frame -> 0.75x the 128-chirp smoke plan
    assert bp.canonical_dca_bytes == 4_194_304 * 96 // 128
    assert bp.native_dca_bytes == 4_718_592 * 96 // 128


@pytest.mark.parametrize("frames,chirps,rx,samples", [(8, 128, 4, 256), (9, 96, 4, 256), (3, 10, 2, 64), (1, 1, 1, 1)])
def test_byte_plan_agrees_with_trusted_awr2944_adc(frames, chirps, rx, samples):
    loops = chirps
    s = resolve_chirp_schedule(
        [parse_chirp_cfg("0 0 0 0 0 0 0 1".split())],
        chirp_start_index=0, chirp_end_index=0, loops_per_frame=loops, frame_count=frames,
    )
    bp = schedule_byte_plan(s, rx, samples, canonical_frames=frames, guard_frames=0)
    assert bp.native_dca_bytes == awr2944_adc.expected_raw_dca_bytes(frames, chirps, rx, samples)
    assert bp.native_active_payload_bytes == awr2944_adc.active_payload_bytes(frames, chirps, rx, samples)
    assert bp.canonical_dca_bytes == bp.native_dca_bytes
    assert bp.native_dca_bytes == 2 * bp.native_active_payload_bytes


def test_byte_plan_guard_frames_split():
    s = resolve_schedule_from_cli(SMOKE_CLI)
    bp = schedule_byte_plan(s, 4, 256, canonical_frames=8, guard_frames=2)
    assert bp.total_frames == 10
    assert bp.native_dca_bytes - bp.canonical_dca_bytes == 2 * 128 * 4 * 256 * 2 * 2


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (dict(rx_channels=0, samples_per_chirp=256), "rx_channels"),
        (dict(rx_channels=4, samples_per_chirp=0), "samples_per_chirp"),
        (dict(rx_channels=4, samples_per_chirp=256, guard_frames=-1), "guard_frames"),
        (dict(rx_channels=4, samples_per_chirp=256, canonical_frames=-1), "canonical_frames"),
        (dict(rx_channels=4, samples_per_chirp=256, guard_frames=20), "canonical_frames"),
    ],
)
def test_byte_plan_input_validation(kwargs, match):
    s = resolve_schedule_from_cli(SMOKE_CLI)
    with pytest.raises(ChirpScheduleError, match=match):
        schedule_byte_plan(s, **kwargs)


# --- no hardware -----------------------------------------------------------
def test_resolution_uses_no_hardware(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("hardware/network touched")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr("builtins.open", boom)
    s = resolve_schedule_from_cli(TDM3_CLI)
    schedule_byte_plan(s, 4, 256, 8, 1)
    s.expanded_tx_masks()


def test_module_does_not_import_transport():
    import awr2944_dca.chirp_schedule as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("serial", "socket", "capture_session", "direct_udp_capture", "dca_cli", "headless_serial"):
        assert f"import {forbidden}" not in src
        assert f"from awr2944_dca.{forbidden}" not in src
