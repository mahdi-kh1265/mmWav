import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Any
from awr2944_dca.dsp.config import RadarProfile

def profile_to_manifest_dict(profile: RadarProfile) -> dict[str, Any]:
    """Serialize RadarProfile (or DSP-independent CaptureDataLayout) to primitive types."""
    d = asdict(profile)
    # Ensure no nested objects or enums leak into JSON
    return d

def profile_from_manifest_dict(data: dict[str, Any]) -> RadarProfile:
    """Deserialize RadarProfile strictly from primitive Python types.

    Raw-only captures (multi-chirp / schedules the flat DSP cannot represent) store a
    capture layout instead of a DSP profile; legacy DSP must refuse them rather than
    treat all physical chirps as one uniform slow-time stream.
    """
    if data.get("profile_kind") == "capture_layout":
        from awr2944_dca.capture_layout import ScheduleAwareDspRequiredError
        raise ScheduleAwareDspRequiredError(
            "Capture is valid, but this chirp schedule "
            f"({data.get('chirps_per_cycle')} chirps/cycle, TX masks "
            f"{list(data.get('tx_masks_per_cycle', []))}) requires schedule-aware DSP. "
            "Use raw access (capture.to_cube()); legacy range/Doppler processing and the "
            "viewer are not valid for this capture."
        )
    return RadarProfile(**data)

@dataclass
class CaptureManifest:
    total_frames: int
    guard_frame_count: int
    canonical_frame_count: int
    native_sha256: str
    canonical_sha256: str
    packet_count: int
    sequence_gaps: int
    capture_timestamp: str
    parser_layout_version: str
    dsp_config_version: str

    # Legacy V1 schema fields
    radar_config: Optional[list[str]] = None
    dca_config: Optional[dict] = None

    # V2 schema fields (Optional for V1 backward compatibility)
    manifest_schema_version: int = 3
    profile: Optional[dict[str, Any]] = None
    native_byte_count: Optional[int] = None
    canonical_native_byte_count: Optional[int] = None
    logical_byte_count: Optional[int] = None
    canonical_logical_byte_count: Optional[int] = None
    stream_layout: Optional[str] = None
    active_lane_indices: Optional[list[int]] = None
    physical_lvds_lanes: Optional[int] = None
    dca_word_slots: Optional[int] = None
    storage_expansion_factor: Optional[int] = None
    logical_cube_shape: Optional[list[int]] = None
    sdk_cli_commands: Optional[list[str]] = None
    dca_configuration: Optional[dict] = None
    
    # Execution status fields
    status: str = "complete"
    success: bool = True
    failure_stage: Optional[str] = None
    failure_reason: Optional[str] = None
    captured_native_bytes: Optional[int] = None
    expected_native_bytes: Optional[int] = None
    
    # V3 schema fields for byte counter integrity
    byte_counter_discontinuity_count: Optional[int] = None
    missing_payload_bytes: Optional[int] = None
    overlap_payload_bytes: Optional[int] = None
    packet_metadata_preserved: Optional[bool] = None
    packet_metadata_path: Optional[str] = None
    packet_metadata_format: Optional[str] = None
    packet_record_count: Optional[int] = None
    
    # V4 schema fields for config provenance
    source_config_kind: Optional[str] = None
    source_config_sha256: Optional[str] = None
    resolved_config_sha256: Optional[str] = None
    radar_config_sha256: Optional[str] = None
    config_summary_path_rel: Optional[str] = None
    resolved_config_path_rel: Optional[str] = None
    source_config_path_rel: Optional[str] = None
    resolved_profile_path_rel: Optional[str] = None
    source_profile_path_rel: Optional[str] = None

    # Legacy fields
    byte_counter_gaps: Optional[int] = None

    def to_json(self, path: Path):
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=4)

    @classmethod
    def from_json(cls, path: Path) -> "CaptureManifest":
        with open(path, "r") as f:
            data = json.load(f)
            
        # Explicit version schema enforcement if missing
        if "manifest_schema_version" not in data:
            data["manifest_schema_version"] = 1

        return cls(**data)
