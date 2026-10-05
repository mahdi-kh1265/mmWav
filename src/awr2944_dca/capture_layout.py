"""Raw-ADC capture data layout, independent of DSP support.

``CaptureDataLayout`` carries ONLY acquisition/layout facts needed by the frozen
low-level capture (frame/chirp/RX/sample counts, sample format, cube layout) plus
the physical chirp schedule for provenance.  It deliberately contains no velocity,
Doppler, TX-demux or antenna-array assumptions.

The canonical raw cube is always ``[frame, physical_chirp, rx, sample]`` in
physical acquisition order (chirp cycle repeated ``loops_per_frame`` times).  TX
demultiplexing is a later DSP/view transformation, never applied to the raw artifact.

The field names ``frame_count``, ``chirps_per_frame``, ``rx_count`` and
``adc_samples`` intentionally match the attributes the frozen ``run_capture``
reads, so a layout can be passed where no legacy DSP profile exists.  This is NOT a
fabricated ``DspRadarProfile``: it has no RF/timing fields and cannot be fed to DSP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from awr2944_dca.chirp_schedule import BasicFrameSchedule

PROFILE_KIND_CAPTURE_LAYOUT = "capture_layout"


class ScheduleAwareDspRequiredError(ValueError):
    """Raw capture is valid, but legacy (uniform slow-time) DSP must not process it."""


@dataclass(frozen=True)
class CaptureDataLayout:
    frame_count: int            # native frames (including guard frames)
    guard_frames: int
    canonical_frames: int
    chirps_per_frame: int       # PHYSICAL chirps per frame
    rx_count: int
    adc_samples: int
    sample_format: str = "real_int16"
    bytes_per_sample: int = 2
    cube_layout: str = "frame_chirp_rx_sample"

    chirp_start_index: int = 0
    chirp_end_index: int = 0
    chirps_per_cycle: int = 1
    loops_per_frame: int = 1
    tx_masks_per_cycle: tuple = ()

    profile_kind: str = PROFILE_KIND_CAPTURE_LAYOUT
    requires_schedule_aware_dsp: bool = False

    @property
    def canonical_cube_shape(self) -> tuple[int, int, int, int]:
        return (self.canonical_frames, self.chirps_per_frame, self.rx_count, self.adc_samples)


def build_capture_layout(
    schedule: BasicFrameSchedule,
    rx_count: int,
    adc_samples: int,
    canonical_frames: int,
    guard_frames: int,
    requires_schedule_aware_dsp: bool,
) -> CaptureDataLayout:
    return CaptureDataLayout(
        frame_count=canonical_frames + guard_frames,
        guard_frames=guard_frames,
        canonical_frames=canonical_frames,
        chirps_per_frame=schedule.physical_chirps_per_frame,
        rx_count=rx_count,
        adc_samples=adc_samples,
        chirp_start_index=schedule.chirp_start_index,
        chirp_end_index=schedule.chirp_end_index,
        chirps_per_cycle=schedule.chirps_per_cycle,
        loops_per_frame=schedule.loops_per_frame,
        tx_masks_per_cycle=tuple(schedule.tx_masks_per_cycle),
        requires_schedule_aware_dsp=requires_schedule_aware_dsp,
    )


def schedule_to_dict(schedule: BasicFrameSchedule) -> dict[str, Any]:
    """JSON-safe schedule description sufficient to reconstruct the physical order."""
    return {
        "chirp_start_index": schedule.chirp_start_index,
        "chirp_end_index": schedule.chirp_end_index,
        "chirps_per_cycle": schedule.chirps_per_cycle,
        "loops_per_frame": schedule.loops_per_frame,
        "physical_chirps_per_frame": schedule.physical_chirps_per_frame,
        "frame_count_native": schedule.frame_count,
        "frame_period_ms": schedule.frame_period_ms,
        "tx_masks_per_cycle": list(schedule.tx_masks_per_cycle),
        "profile_ids_per_cycle": [c.profile_id for c in schedule.chirps],
        "chirp_definitions": [
            {
                "chirp_index": c.chirp_index,
                "profile_id": c.profile_id,
                "tx_enable_mask": c.tx_enable_mask,
                "start_freq_var_hz": c.start_freq_var_hz,
                "freq_slope_var_mhz_per_us": c.freq_slope_var_mhz_per_us,
                "idle_time_var_us": c.idle_time_var_us,
                "adc_start_time_var_us": c.adc_start_time_var_us,
            }
            for c in schedule.chirps
        ],
        "physical_order": "chirp cycle repeated loops_per_frame times; "
                          "physical_chirp i uses tx_masks_per_cycle[i % chirps_per_cycle]",
    }
