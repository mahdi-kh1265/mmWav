import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from awr2944_dca.api.profile import RadarProfile as StructuredRadarProfile
from awr2944_dca.chirp_schedule import (
    BasicFrameSchedule,
    ChirpScheduleError,
    resolve_schedule_from_cli,
    schedule_byte_plan,
)
from awr2944_dca.dsp.config import RadarProfile as DspRadarProfile
from awr2944_dca.mmw_demo_config import MmwDemoConfig, KNOWN_COMMANDS


class LiveExecutionNotEnabledError(ValueError):
    """Raised before any hardware access when a resolved config cannot run live.

    The configuration may still resolve and plan offline successfully.
    """


@dataclass(frozen=True)
class CaptureCapabilities:
    """Independent capability flags for a resolved capture configuration.

    Defaults are all False (fail closed).  A configuration can, for example,
    resolve and plan offline while live execution and DSP remain unsupported.
    """
    can_resolve: bool = False
    can_compute_byte_plan: bool = False
    can_plan: bool = False
    can_execute_live: bool = False
    can_build_legacy_dsp_profile: bool = False
    can_run_tdm_dsp: bool = False
    can_run_aoa: bool = False
    live_block_reason: Optional[str] = None
    legacy_dsp_block_reason: Optional[str] = None

@dataclass(frozen=True)
class BytePlan:
    """Explicit byte-count plan for a capture configuration."""
    adc_format: str
    bytes_per_scalar_component: int
    components_per_sample: int
    bytes_per_adc_sample: int
    
    samples_per_chirp: int
    rx_channels: int
    chirps_per_frame: int
    total_frames: int
    canonical_frames: int
    guard_frames: int
    
    dca_word_slots: int
    active_lanes: int
    dca_expansion_factor: int
    
    native_active_payload_bytes: int
    native_dca_bytes: int
    canonical_active_payload_bytes: int
    canonical_dca_bytes: int


@dataclass(frozen=True)
class CfgCaptureMetadata:
    """Metadata extracted from a .cfg for capture execution."""
    rx_mask: int
    tx_mask: int
    rx_count: int
    tx_count: int
    
    start_freq_ghz: float
    idle_time_us: float
    adc_start_time_us: float
    ramp_end_time_us: float
    slope_mhz_per_us: float
    adc_samples: int
    sample_rate_ksps: float
    
    chirps_per_loop: int
    num_loops: int
    num_frames: int
    frame_period_ms: float
    
    adc_format_code: int
    adc_bits_code: int
    is_complex: bool
    bytes_per_adc_sample: int
    
    total_chirps_per_frame: int


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str


@dataclass(frozen=True)
class ResolvedCaptureConfig:
    """Immutable resolved configuration ready for capture execution."""
    source_kind: str
    source_name: Optional[str]
    source_path: Optional[Path]
    source_sha256: Optional[str]
    
    cli_commands: tuple[str, ...]
    
    structured_profile: Optional[StructuredRadarProfile]
    dsp_profile: Optional[DspRadarProfile]
    
    byte_plan: BytePlan
    
    resolved_cfg_text: str
    resolved_sha256: str
    
    derived: Optional[dict[str, float]]
    
    preflight_warnings: list[str] = field(default_factory=list)
    
    # Authoritative basic-frame schedule (chirp cycle, loops, TX masks).
    schedule: Optional[BasicFrameSchedule] = None
    capabilities: CaptureCapabilities = field(default_factory=CaptureCapabilities)


def extract_capture_metadata(cfg: MmwDemoConfig) -> CfgCaptureMetadata:
    """Extract metadata from MmwDemoConfig."""
    ch_cmd = next((line for line in cfg.lines if line.command == "channelCfg"), None)
    prof_cmd = next((line for line in cfg.lines if line.command == "profileCfg"), None)
    frame_cmd = next((line for line in cfg.lines if line.command == "frameCfg"), None)
    adc_cmd = next((line for line in cfg.lines if line.command == "adcCfg"), None)
    adcbuf_cmd = next((line for line in cfg.lines if line.command == "adcbufCfg"), None)
    
    # Defaults in case of missing commands (preflight will error anyway)
    rx_mask = int(ch_cmd.args[0]) if ch_cmd and len(ch_cmd.args) >= 2 else 0
    tx_mask = int(ch_cmd.args[1]) if ch_cmd and len(ch_cmd.args) >= 2 else 0
    rx_count = bin(rx_mask).count("1")
    tx_count = bin(tx_mask).count("1")
    
    start_freq = float(prof_cmd.args[1]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    idle_time = float(prof_cmd.args[2]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    adc_start = float(prof_cmd.args[3]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    ramp_end = float(prof_cmd.args[4]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    slope = float(prof_cmd.args[7]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    adc_samples = int(prof_cmd.args[9]) if prof_cmd and len(prof_cmd.args) >= 14 else 0
    sample_rate = float(prof_cmd.args[10]) if prof_cmd and len(prof_cmd.args) >= 14 else 0.0
    
    chirp_start = int(frame_cmd.args[0]) if frame_cmd and len(frame_cmd.args) >= 7 else 0
    chirp_end = int(frame_cmd.args[1]) if frame_cmd and len(frame_cmd.args) >= 7 else 0
    chirps_per_loop = (chirp_end - chirp_start + 1) if frame_cmd else 0
    num_loops = int(frame_cmd.args[2]) if frame_cmd and len(frame_cmd.args) >= 7 else 0
    num_frames = int(frame_cmd.args[3]) if frame_cmd and len(frame_cmd.args) >= 7 else 0
    frame_period = float(frame_cmd.args[4]) if frame_cmd and len(frame_cmd.args) >= 7 else 0.0
    
    adc_bits = int(adc_cmd.args[0]) if adc_cmd and len(adc_cmd.args) >= 2 else 2
    adc_fmt = int(adc_cmd.args[1]) if adc_cmd and len(adc_cmd.args) >= 2 else 0
    
    is_complex = False
    bytes_per_sample = 2
    if adc_fmt == 0 and adcbuf_cmd:
        buf_fmt = int(adcbuf_cmd.args[1]) if len(adcbuf_cmd.args) >= 5 else 1
        if buf_fmt == 1:
            is_complex = False
            bytes_per_sample = 2
        else:
            is_complex = True
            bytes_per_sample = 4
    
    total_chirps_per_frame = chirps_per_loop * num_loops
    
    return CfgCaptureMetadata(
        rx_mask=rx_mask,
        tx_mask=tx_mask,
        rx_count=rx_count,
        tx_count=tx_count,
        start_freq_ghz=start_freq,
        idle_time_us=idle_time,
        adc_start_time_us=adc_start,
        ramp_end_time_us=ramp_end,
        slope_mhz_per_us=slope,
        adc_samples=adc_samples,
        sample_rate_ksps=sample_rate,
        chirps_per_loop=chirps_per_loop,
        num_loops=num_loops,
        num_frames=num_frames,
        frame_period_ms=frame_period,
        adc_format_code=adc_fmt,
        adc_bits_code=adc_bits,
        is_complex=is_complex,
        bytes_per_adc_sample=bytes_per_sample,
        total_chirps_per_frame=total_chirps_per_frame,
    )


def preflight_validate(cfg: MmwDemoConfig, meta: CfgCaptureMetadata) -> list[ValidationIssue]:
    issues = []
    
    req_cmds = {"channelCfg", "profileCfg", "chirpCfg", "frameCfg"}
    found_cmds = {line.command for line in cfg.lines if line.is_command}
    missing = req_cmds - found_cmds
    if missing:
        issues.append(ValidationIssue("ERROR", f"Missing required commands: {missing}"))
        
    adc_cmd = next((line for line in cfg.lines if line.command == "adcCfg"), None)
    adcbuf_cmd = next((line for line in cfg.lines if line.command == "adcbufCfg"), None)
    
    if not adc_cmd:
        issues.append(ValidationIssue("ERROR", "Missing adcCfg. Cannot determine format."))
    if not adcbuf_cmd:
        issues.append(ValidationIssue("ERROR", "Missing adcbufCfg. Cannot determine format."))
        
    if adc_cmd and len(adc_cmd.args) >= 2:
        if int(adc_cmd.args[0]) != 2:
            issues.append(ValidationIssue("ERROR", "adcCfg b2AdcBits != 2. Only 16-bit supported."))
        if int(adc_cmd.args[1]) != 0:
            issues.append(ValidationIssue("ERROR", "adcCfg b2AdcOutFmt != 0. AWR294x only supports real (outFmt=0)."))
            
    if adcbuf_cmd and len(adcbuf_cmd.args) >= 5:
        if int(adcbuf_cmd.args[1]) != 1:
            issues.append(ValidationIssue("ERROR", "adcbufCfg adcFmt != 1. AWR294x only supports real (adcFmt=1)."))

    if meta.rx_count == 0 or meta.tx_count == 0:
        issues.append(ValidationIssue("ERROR", "RX/TX channel mask must have at least one channel enabled."))
        
    if meta.adc_samples <= 0:
        issues.append(ValidationIssue("ERROR", "Zero samples configured."))
        
    if meta.num_frames <= 0:
        issues.append(ValidationIssue("ERROR", "Zero frames configured."))
        
    unknown = [line.command for line in cfg.lines if line.is_command and line.command not in KNOWN_COMMANDS]
    if unknown:
        issues.append(ValidationIssue("ERROR", f"Unknown commands cannot be transmitted: {unknown}"))
        
    prof_cmds = [line for line in cfg.lines if line.command == "profileCfg"]
    if len(prof_cmds) > 1:
        issues.append(ValidationIssue("ERROR", "Multiple profileCfg commands. Current parser cannot represent."))
        
    # Multi-chirp / multi-TX configs are resolvable and plannable offline; live
    # execution is gated separately via CaptureCapabilities.  The schedule must
    # still be unambiguous: undefined/overlapping chirps or bad ranges are errors.
    if {"chirpCfg", "frameCfg"} <= found_cmds:
        try:
            resolve_schedule_from_cli(build_cli_commands(cfg))
        except ChirpScheduleError as exc:
            issues.append(ValidationIssue("ERROR", f"Invalid chirp schedule: {exc}"))
        
    adv_frame = any(line.command in {"advFrameCfg", "subFrameCfg"} for line in cfg.lines)
    if adv_frame:
        issues.append(ValidationIssue("ERROR", "Advanced frame unsupported."))
        
    adv_chirp = any(line.command in {"advChirpCfg", "LUTDataCfg"} for line in cfg.lines)
    if adv_chirp:
        issues.append(ValidationIssue("ERROR", "Advanced chirp unsupported."))
        
    return issues


def build_cli_commands(cfg: MmwDemoConfig, num_frames_override: Optional[int] = None) -> tuple[str, ...]:
    commands = []
    for line in cfg.lines:
        if not line.is_command:
            continue
        if line.command in {"sensorStop", "sensorStart"}:
            continue
            
        cmd_text = " ".join([line.command] + line.args)
        if num_frames_override is not None and line.command == "frameCfg":
            if len(line.args) >= 7:
                args = list(line.args)
                args[3] = str(num_frames_override)
                cmd_text = " ".join(["frameCfg"] + args)
                
        commands.append(cmd_text)
    return tuple(commands)


def build_dsp_profile(meta: CfgCaptureMetadata) -> Optional[DspRadarProfile]:
    if meta.chirps_per_loop > 1:
        # Multi-chirp TDM cannot be represented by DspRadarProfile correctly yet
        return None
        
    try:
        return DspRadarProfile(
            start_frequency_hz=meta.start_freq_ghz * 1e9,
            slope_hz_per_s=meta.slope_mhz_per_us * 1e12,
            adc_sample_rate_hz=meta.sample_rate_ksps * 1e3,
            adc_samples=meta.adc_samples,
            idle_time_s=meta.idle_time_us * 1e-6,
            ramp_end_time_s=meta.ramp_end_time_us * 1e-6,
            chirps_per_frame=meta.total_chirps_per_frame,
            rx_count=meta.rx_count,
            tx_count=meta.tx_count,
        )
    except Exception:
        return None


def attempt_structured_conversion(cfg: MmwDemoConfig, meta: CfgCaptureMetadata) -> Optional[StructuredRadarProfile]:
    return None


def _check_chirp_count_consistency(
    schedule: BasicFrameSchedule,
    compat_chirps_per_frame: int,
    resolved_frames: int,
    origin: str,
) -> None:
    """Fail explicitly if a legacy chirp/frame count disagrees with the schedule."""
    if compat_chirps_per_frame != schedule.physical_chirps_per_frame:
        raise ValueError(
            f"Chirp count mismatch ({origin}): configured chirps_per_frame={compat_chirps_per_frame} "
            f"but resolved schedule has {schedule.physical_chirps_per_frame} physical chirps/frame "
            f"({schedule.chirps_per_cycle} chirps/cycle x {schedule.loops_per_frame} loops)."
        )
    if schedule.frame_count != resolved_frames:
        raise ValueError(
            f"Frame count mismatch ({origin}): expected {resolved_frames} native frames "
            f"but resolved schedule has {schedule.frame_count}."
        )


def _build_capabilities(
    schedule: BasicFrameSchedule,
    dsp_profile: Optional[DspRadarProfile],
) -> CaptureCapabilities:
    """Derive capabilities.  Live execution requires the legacy single-chirp path."""
    if dsp_profile is None:
        dsp_reason = (
            "The flat legacy DSP profile could not be built for this configuration "
            f"({schedule.chirps_per_cycle} chirps/cycle, TX masks {schedule.tx_masks_per_cycle})."
        )
    else:
        dsp_reason = None

    if not schedule.is_single_chirp:
        live_reason = (
            "Configuration resolves and plans successfully, but live multi-chirp execution "
            f"is not yet enabled ({schedule.chirps_per_cycle} chirps/cycle, "
            f"TX masks per cycle {schedule.tx_masks_per_cycle})."
        )
    elif dsp_profile is None:
        live_reason = (
            "Configuration resolves and plans successfully, but live execution is not enabled: "
            + dsp_reason
        )
    else:
        live_reason = None

    return CaptureCapabilities(
        can_resolve=True,
        can_compute_byte_plan=True,
        can_plan=True,
        can_execute_live=live_reason is None,
        can_build_legacy_dsp_profile=dsp_profile is not None,
        can_run_tdm_dsp=False,
        can_run_aoa=False,
        live_block_reason=live_reason,
        legacy_dsp_block_reason=dsp_reason,
    )


def _make_byte_plan(
    schedule: BasicFrameSchedule,
    rx_channels: int,
    samples_per_chirp: int,
    canonical_frames: int,
    guard_frames: int,
) -> BytePlan:
    """Real-int16 byte plan from the schedule (trusted awr2944_adc arithmetic)."""
    sbp = schedule_byte_plan(
        schedule,
        rx_channels=rx_channels,
        samples_per_chirp=samples_per_chirp,
        canonical_frames=canonical_frames,
        guard_frames=guard_frames,
    )
    return BytePlan(
        adc_format="real_int16",
        bytes_per_scalar_component=2,
        components_per_sample=1,
        bytes_per_adc_sample=2,
        samples_per_chirp=samples_per_chirp,
        rx_channels=rx_channels,
        chirps_per_frame=schedule.physical_chirps_per_frame,
        total_frames=sbp.total_frames,
        canonical_frames=canonical_frames,
        guard_frames=guard_frames,
        dca_word_slots=4,
        active_lanes=2,
        dca_expansion_factor=2,
        native_active_payload_bytes=sbp.native_active_payload_bytes,
        native_dca_bytes=sbp.native_dca_bytes,
        canonical_active_payload_bytes=sbp.canonical_active_payload_bytes,
        canonical_dca_bytes=sbp.canonical_dca_bytes,
    )

def resolve_capture_config(
    project: Any,
    profile: Any,
    frames: Optional[int] = None,
    guard_frames: Optional[int] = None,
) -> ResolvedCaptureConfig:
    
    if guard_frames is None:
        guard_frames = 1
        
    source_kind = "unknown"
    source_name = None
    source_path = None
    source_sha256 = None
    
    if isinstance(profile, str):
        source_name = profile
        profile_obj = project.profiles.get(profile)
        if not profile_obj:
            raise ValueError(f"Profile {profile} not found in project.")
        profile = profile_obj
        
    if isinstance(profile, Path) and profile.suffix == ".toml":
        from awr2944_dca.api.profile import RadarProfile
        source_kind = "toml_file"
        source_path = profile
        source_sha256 = hashlib.sha256(profile.read_bytes()).hexdigest()
        profile_obj = RadarProfile.from_toml(profile.read_text(encoding="utf-8"))
        if hasattr(profile_obj, "name"):
            source_name = profile_obj.name
        profile = profile_obj
        
    if profile.__class__.__name__ == "RadarProfile":
        if source_kind == "unknown":
            source_kind = "profile"
        canonical_frames = frames if frames is not None else profile.frame.frame_count
        resolved_frames = canonical_frames + guard_frames
        
        # Apply resolved native frames before CLI generation
        effective_profile = profile.with_frame(frame_count=resolved_frames)
            
        cli_commands = effective_profile.to_sdk_cli()
        dsp_profile = effective_profile.to_dsp_profile()
        
        # Calculate rx_channels based on rx_mask
        rx_channels = effective_profile.channel.rx_mask.bit_count()
        
        # Authoritative schedule comes from the exact commands that will be sent.
        try:
            schedule = resolve_schedule_from_cli(cli_commands)
        except ChirpScheduleError as exc:
            raise ValueError(f"Invalid chirp schedule: {exc}") from exc
        _check_chirp_count_consistency(
            schedule, profile.frame.chirps_per_frame, resolved_frames, "structured profile"
        )
        
        byte_plan = _make_byte_plan(
            schedule, rx_channels, profile.sampling.samples, canonical_frames, guard_frames
        )
        
        resolved_cfg_text = "\n".join(cli_commands) + "\n"
        
        return ResolvedCaptureConfig(
            source_kind=source_kind,
            source_name=source_name,
            source_path=source_path,
            source_sha256=source_sha256,
            cli_commands=cli_commands,
            structured_profile=effective_profile,
            dsp_profile=dsp_profile,
            byte_plan=byte_plan,
            resolved_cfg_text=resolved_cfg_text,
            resolved_sha256=hashlib.sha256(resolved_cfg_text.encode("utf-8")).hexdigest(),
            derived={},
            preflight_warnings=[],
            schedule=schedule,
            capabilities=_build_capabilities(schedule, dsp_profile),
        )
        
    elif isinstance(profile, (Path, MmwDemoConfig)):
        cfg = profile
        if isinstance(profile, Path):
            suffix = profile.suffix.lower()
            if suffix == ".cfg":
                source_kind = "cfg_file"
            elif suffix == ".toml":
                # Handled above
                pass
            else:
                raise ValueError(f"Unsupported file extension: {profile.suffix}. Only .toml and .cfg are supported.")
                
            source_path = profile
            
            cfg = MmwDemoConfig.from_cfg_file(profile)
            source_sha256 = cfg.source_sha256
        else:
            source_kind = "mmw_demo_config"
            
        # Pre-extract to find native num_frames if frames is None
        temp_meta = extract_capture_metadata(cfg)
        canonical_frames = frames if frames is not None else temp_meta.num_frames
        resolved_frames = canonical_frames + guard_frames
        
        # Rewrite frameCfg numFrames to resolved native total before final extraction
        new_lines = []
        for line in cfg.lines:
            if line.is_command and line.command == "frameCfg" and len(line.args) >= 7:
                args = list(line.args)
                args[3] = str(resolved_frames)
                new_lines.append(f"frameCfg {' '.join(args)}")
            else:
                new_lines.append(line.raw_text)
                
        new_cfg_text = "\n".join(new_lines) + "\n"
        cfg = MmwDemoConfig.from_cfg_text(new_cfg_text)
            
        meta = extract_capture_metadata(cfg)
        issues = preflight_validate(cfg, meta)
        errors = [i.message for i in issues if i.severity == "ERROR"]
        if errors:
            raise ValueError(f"Preflight validation failed: {errors}")
            
        warnings = [i.message for i in issues if i.severity == "WARNING"]
        
        cli_commands = build_cli_commands(cfg, num_frames_override=None)
        dsp_profile = build_dsp_profile(meta)
        
        try:
            schedule = resolve_schedule_from_cli(cli_commands)
        except ChirpScheduleError as exc:
            raise ValueError(f"Invalid chirp schedule: {exc}") from exc
        _check_chirp_count_consistency(
            schedule, meta.total_chirps_per_frame, resolved_frames, "cfg metadata"
        )
        
        # preflight_validate has already rejected complex ADC formats, so the
        # real-int16 schedule byte plan applies.
        byte_plan = _make_byte_plan(
            schedule, meta.rx_count, meta.adc_samples, canonical_frames, guard_frames
        )
        
        resolved_cfg_text = "\n".join(cli_commands) + "\n"
        
        return ResolvedCaptureConfig(
            source_kind=source_kind,
            source_name=source_name,
            source_path=source_path,
            source_sha256=source_sha256,
            cli_commands=cli_commands,
            structured_profile=None,
            dsp_profile=dsp_profile,
            byte_plan=byte_plan,
            resolved_cfg_text=resolved_cfg_text,
            resolved_sha256=hashlib.sha256(resolved_cfg_text.encode("utf-8")).hexdigest(),
            derived=None,
            preflight_warnings=warnings,
            schedule=schedule,
            capabilities=_build_capabilities(schedule, dsp_profile),
        )
        
    else:
        raise TypeError(f"Unsupported profile type: {type(profile)}")
