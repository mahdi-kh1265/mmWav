"""Generic basic-frame chirp schedule model (offline, hardware-free).

Models the TI mmw-demo *basic frame* (``frameCfg`` + ``chirpCfg``) as an
immutable schedule so that physical chirps/frame, per-chirp TX masks and
expected byte counts come from ONE authoritative representation.

Semantics (TI basic frame)
--------------------------
``frameCfg chirpStartIdx chirpEndIdx numLoops numFrames ...`` transmits the
chirps ``chirpStartIdx..chirpEndIdx`` (one *cycle*) ``numLoops`` times per
frame::

    chirps_per_cycle          = chirpEndIdx - chirpStartIdx + 1
    physical_chirps_per_frame = chirps_per_cycle * numLoops

``chirpCfg startIdx endIdx profileId startFreqVar freqSlopeVar idleTimeVar
adcStartTimeVar txEnable`` defines a RANGE of chirp indices (``startIdx`` ..
``endIdx``, inclusive); every index in the range receives the same definition.

Fail-closed policy
------------------
* A chirp index required by ``frameCfg`` with no definition -> error.
* An invalid frame range / loop count -> error.
* A required chirp index covered by more than one ``chirpCfg`` -> error
  (overlap is ambiguous; it is never silently resolved).

This module performs no I/O and never touches UART, DCA1000 or the network.
Byte arithmetic is delegated to the trusted :mod:`awr2944_dca.awr2944_adc`
helpers; it is not re-implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from awr2944_dca.awr2944_adc import active_payload_bytes, expected_raw_dca_bytes

# TI chirp index space (chirpCfg/frameCfg) and AWR2944 TX channel count (4).
MAX_CHIRP_INDEX = 511
MAX_TX_ENABLE_MASK = 0xF


class ChirpScheduleError(ValueError):
    """Raised when a basic-frame schedule cannot be resolved unambiguously."""


@dataclass(frozen=True)
class ResolvedChirp:
    """One chirp definition after chirpCfg range expansion."""

    chirp_index: int
    profile_id: int
    tx_enable_mask: int
    start_freq_var_hz: float = 0.0
    freq_slope_var_mhz_per_us: float = 0.0
    idle_time_var_us: float = 0.0
    adc_start_time_var_us: float = 0.0


@dataclass(frozen=True)
class ChirpCfgDefinition:
    """A parsed ``chirpCfg`` command (a RANGE of chirp indices)."""

    start_index: int
    end_index: int
    profile_id: int
    start_freq_var_hz: float
    freq_slope_var_mhz_per_us: float
    idle_time_var_us: float
    adc_start_time_var_us: float
    tx_enable_mask: int

    def covers(self, index: int) -> bool:
        return self.start_index <= index <= self.end_index

    def expand(self, index: int) -> ResolvedChirp:
        return ResolvedChirp(
            chirp_index=index,
            profile_id=self.profile_id,
            tx_enable_mask=self.tx_enable_mask,
            start_freq_var_hz=self.start_freq_var_hz,
            freq_slope_var_mhz_per_us=self.freq_slope_var_mhz_per_us,
            idle_time_var_us=self.idle_time_var_us,
            adc_start_time_var_us=self.adc_start_time_var_us,
        )


def _check_chirp_index(value: int, what: str) -> None:
    if not 0 <= value <= MAX_CHIRP_INDEX:
        raise ChirpScheduleError(f"{what} {value} outside valid range 0..{MAX_CHIRP_INDEX}.")


@dataclass(frozen=True)
class BasicFrameSchedule:
    """Resolved basic-frame schedule: one chirp cycle repeated per frame.

    ``chirps`` holds the cycle in index order (``chirp_start_index`` ..
    ``chirp_end_index``) and is validated to match the frame range exactly.
    ``frame_count`` is the number of frames the schedule was resolved for (for
    capture configs this is the native frame count including guard frames).
    """

    chirp_start_index: int
    chirp_end_index: int
    loops_per_frame: int
    frame_count: int
    frame_period_ms: float
    chirps: tuple[ResolvedChirp, ...]

    def __post_init__(self) -> None:
        _check_chirp_index(self.chirp_start_index, "chirp_start_index")
        _check_chirp_index(self.chirp_end_index, "chirp_end_index")
        if self.chirp_start_index > self.chirp_end_index:
            raise ChirpScheduleError(
                f"Invalid frame chirp range: start {self.chirp_start_index} > end {self.chirp_end_index}."
            )
        if self.loops_per_frame < 1:
            raise ChirpScheduleError(f"loops_per_frame must be >= 1, got {self.loops_per_frame}.")
        if self.frame_count < 0:
            raise ChirpScheduleError(f"frame_count must be >= 0, got {self.frame_count}.")
        expected = tuple(range(self.chirp_start_index, self.chirp_end_index + 1))
        actual = tuple(c.chirp_index for c in self.chirps)
        if actual != expected:
            raise ChirpScheduleError(
                f"Schedule chirps {actual} do not match frame range {expected}."
            )

    # -- derived semantics -------------------------------------------------
    @property
    def chirps_per_cycle(self) -> int:
        return self.chirp_end_index - self.chirp_start_index + 1

    @property
    def physical_chirps_per_frame(self) -> int:
        return self.chirps_per_cycle * self.loops_per_frame

    @property
    def chirp_indices_per_cycle(self) -> tuple[int, ...]:
        return tuple(c.chirp_index for c in self.chirps)

    @property
    def tx_masks_per_cycle(self) -> tuple[int, ...]:
        return tuple(c.tx_enable_mask for c in self.chirps)

    @property
    def is_single_chirp(self) -> bool:
        return self.chirps_per_cycle == 1

    @property
    def is_uniform_tx(self) -> bool:
        return len(set(self.tx_masks_per_cycle)) == 1

    def expanded_tx_masks(self) -> tuple[int, ...]:
        """TX mask of every physical chirp in a frame (cycle x loops)."""
        return self.tx_masks_per_cycle * self.loops_per_frame


def resolve_chirp_schedule(
    chirp_cfgs: Iterable[ChirpCfgDefinition],
    *,
    chirp_start_index: int,
    chirp_end_index: int,
    loops_per_frame: int,
    frame_count: int = 0,
    frame_period_ms: float = 0.0,
) -> BasicFrameSchedule:
    """Expand chirpCfg ranges and build the schedule required by frameCfg."""
    _check_chirp_index(chirp_start_index, "frameCfg chirpStartIdx")
    _check_chirp_index(chirp_end_index, "frameCfg chirpEndIdx")
    if chirp_start_index > chirp_end_index:
        raise ChirpScheduleError(
            f"Invalid frame chirp range: start {chirp_start_index} > end {chirp_end_index}."
        )
    cfgs = list(chirp_cfgs)
    chirps: list[ResolvedChirp] = []
    for index in range(chirp_start_index, chirp_end_index + 1):
        covering = [c for c in cfgs if c.covers(index)]
        if not covering:
            raise ChirpScheduleError(
                f"frameCfg requires chirp index {index} but no chirpCfg defines it."
            )
        if len(covering) > 1:
            raise ChirpScheduleError(
                f"Chirp index {index} is defined by {len(covering)} overlapping chirpCfg "
                f"commands; refusing to guess which applies."
            )
        chirps.append(covering[0].expand(index))
    return BasicFrameSchedule(
        chirp_start_index=chirp_start_index,
        chirp_end_index=chirp_end_index,
        loops_per_frame=loops_per_frame,
        frame_count=frame_count,
        frame_period_ms=frame_period_ms,
        chirps=tuple(chirps),
    )


def _int(token: str, what: str) -> int:
    try:
        return int(token)
    except ValueError as exc:
        raise ChirpScheduleError(f"{what}: expected integer, got {token!r}.") from exc


def _float(token: str, what: str) -> float:
    try:
        return float(token)
    except ValueError as exc:
        raise ChirpScheduleError(f"{what}: expected number, got {token!r}.") from exc


def parse_chirp_cfg(args: Sequence[str]) -> ChirpCfgDefinition:
    """Parse the 8 arguments of a ``chirpCfg`` command."""
    if len(args) != 8:
        raise ChirpScheduleError(f"chirpCfg requires 8 arguments, got {len(args)}: {list(args)}")
    start = _int(args[0], "chirpCfg startIdx")
    end = _int(args[1], "chirpCfg endIdx")
    _check_chirp_index(start, "chirpCfg startIdx")
    _check_chirp_index(end, "chirpCfg endIdx")
    if start > end:
        raise ChirpScheduleError(f"chirpCfg range start {start} > end {end}.")
    tx = _int(args[7], "chirpCfg txEnable")
    if not 1 <= tx <= MAX_TX_ENABLE_MASK:
        raise ChirpScheduleError(
            f"chirpCfg txEnable {tx} must be in 1..{MAX_TX_ENABLE_MASK}."
        )
    return ChirpCfgDefinition(
        start_index=start,
        end_index=end,
        profile_id=_int(args[2], "chirpCfg profileId"),
        start_freq_var_hz=_float(args[3], "chirpCfg startFreqVar"),
        freq_slope_var_mhz_per_us=_float(args[4], "chirpCfg freqSlopeVar"),
        idle_time_var_us=_float(args[5], "chirpCfg idleTimeVar"),
        adc_start_time_var_us=_float(args[6], "chirpCfg adcStartTimeVar"),
        tx_enable_mask=tx,
    )


def resolve_schedule_from_cli(commands: Iterable[str]) -> BasicFrameSchedule:
    """Resolve the basic-frame schedule from SDK CLI command strings.

    Only ``chirpCfg`` and ``frameCfg`` are consulted; everything else is
    ignored. Exactly one ``frameCfg`` is required. Both the 7-argument
    (``... numFrames framePeriod trigger delay``) and the 8-argument AWR294x
    (``... numFrames numAdcSamples framePeriod trigger delay``) forms are
    accepted.
    """
    chirp_cfgs: list[ChirpCfgDefinition] = []
    frame_args: Optional[list[str]] = None
    for command in commands:
        tokens = command.split()
        if not tokens:
            continue
        name, args = tokens[0], tokens[1:]
        if name == "chirpCfg":
            chirp_cfgs.append(parse_chirp_cfg(args))
        elif name == "frameCfg":
            if frame_args is not None:
                raise ChirpScheduleError("Multiple frameCfg commands; schedule is ambiguous.")
            frame_args = args
    if frame_args is None:
        raise ChirpScheduleError("Missing frameCfg; cannot resolve chirp schedule.")
    if len(frame_args) == 8:
        period_token = frame_args[5]
    elif len(frame_args) == 7:
        period_token = frame_args[4]
    else:
        raise ChirpScheduleError(
            f"frameCfg requires 7 or 8 arguments, got {len(frame_args)}: {frame_args}"
        )
    return resolve_chirp_schedule(
        chirp_cfgs,
        chirp_start_index=_int(frame_args[0], "frameCfg chirpStartIdx"),
        chirp_end_index=_int(frame_args[1], "frameCfg chirpEndIdx"),
        loops_per_frame=_int(frame_args[2], "frameCfg numLoops"),
        frame_count=_int(frame_args[3], "frameCfg numFrames"),
        frame_period_ms=_float(period_token, "frameCfg framePeriodicity"),
    )


@dataclass(frozen=True)
class ScheduleBytePlan:
    """Expected byte counts derived from a schedule (real int16 ADC, 2-lane DCA)."""

    physical_chirps_per_frame: int
    rx_channels: int
    samples_per_chirp: int
    canonical_frames: int
    guard_frames: int
    total_frames: int
    native_active_payload_bytes: int
    native_dca_bytes: int
    canonical_active_payload_bytes: int
    canonical_dca_bytes: int


def schedule_byte_plan(
    schedule: BasicFrameSchedule,
    rx_channels: int,
    samples_per_chirp: int,
    canonical_frames: Optional[int] = None,
    guard_frames: int = 0,
) -> ScheduleBytePlan:
    """Expected native/canonical bytes, using trusted ``awr2944_adc`` arithmetic.

    If ``canonical_frames`` is omitted it is ``schedule.frame_count - guard_frames``.
    """
    if rx_channels < 1:
        raise ChirpScheduleError(f"rx_channels must be >= 1, got {rx_channels}.")
    if samples_per_chirp < 1:
        raise ChirpScheduleError(f"samples_per_chirp must be >= 1, got {samples_per_chirp}.")
    if guard_frames < 0:
        raise ChirpScheduleError(f"guard_frames must be >= 0, got {guard_frames}.")
    if canonical_frames is None:
        canonical_frames = schedule.frame_count - guard_frames
    if canonical_frames < 0:
        raise ChirpScheduleError(f"canonical_frames must be >= 0, got {canonical_frames}.")
    total = canonical_frames + guard_frames
    chirps = schedule.physical_chirps_per_frame
    return ScheduleBytePlan(
        physical_chirps_per_frame=chirps,
        rx_channels=rx_channels,
        samples_per_chirp=samples_per_chirp,
        canonical_frames=canonical_frames,
        guard_frames=guard_frames,
        total_frames=total,
        native_active_payload_bytes=active_payload_bytes(total, chirps, rx_channels, samples_per_chirp),
        native_dca_bytes=expected_raw_dca_bytes(total, chirps, rx_channels, samples_per_chirp),
        canonical_active_payload_bytes=active_payload_bytes(canonical_frames, chirps, rx_channels, samples_per_chirp),
        canonical_dca_bytes=expected_raw_dca_bytes(canonical_frames, chirps, rx_channels, samples_per_chirp),
    )
