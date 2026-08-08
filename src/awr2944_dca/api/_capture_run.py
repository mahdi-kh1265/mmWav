"""CaptureRunResult and SessionCaptureApi — facade delegation to frozen run_capture.

Implements the exact 10-step delegation order:
1. Resolve profile
2. Validate
3. Compile SDK commands
4. Calculate byte/capture plan
5. Resolve connection settings
6. Acquire global lock
7. Create capture directory
8. Call frozen run_capture
9. Package result
10. Release lock (auto-session only)

Unsupported profiles fail before hardware access or directory creation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from awr2944_dca.lab import RadarProject, RadarCapture
    from awr2944_dca.api._session import RadarSession

logger = logging.getLogger(__name__)

# Illegal capture name patterns
_UNSAFE_NAME_RE = re.compile(r'[/\\]|^\.\.|\.\.(?=[/\\])|^[A-Za-z]:|^(CON|PRN|AUX|NUL|COM\d|LPT\d)(\..*)?$', re.IGNORECASE)


def _validate_capture_name(name: str) -> None:
    """Reject capture names with path traversal or reserved forms."""
    if not name or not name.strip():
        raise ValueError("Capture name cannot be empty.")
    if _UNSAFE_NAME_RE.search(name):
        raise ValueError(
            f"Unsafe capture name: {name!r}. "
            f"Must not contain slashes, backslashes, dot-traversal, "
            f"absolute paths, or Windows reserved names."
        )


def _get_api_version() -> str:
    """Get the installed package version."""
    try:
        from awr2944_dca import __version__
        return __version__
    except Exception:
        return "unknown"


def _write_atomic(path: Path, content: bytes) -> None:
    """Write content to path atomically using a temporary file."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    import stat
    if path.exists():
        try:
            path.chmod(stat.S_IWRITE)
        except Exception:
            pass
    tmp_path.replace(path)


@dataclass
class CaptureRunResult:
    """Return value of project.capture.run() or session.capture.run()."""
    capture: RadarCapture
    session_result: Any  # frozen CaptureResult from capture_session
    effective_profile: Any  # public immutable RadarProfile
    capture_plan: dict

    @property
    def success(self) -> bool:
        return self.session_result.success


class SessionCaptureApi:
    """Capture API accessed through an explicit RadarSession."""

    def __init__(self, session: RadarSession):
        self._session = session

    def run(
        self,
        profile: str | Any = "smoke_v1",
        frames: int | None = None,
        guard_frames: int | None = None,
        preserve_packet_metadata: bool = True,
        name: str = "dca_capture",
    ) -> CaptureRunResult:
        """Execute a capture through the owning session."""
        return _run_capture_facade(
            project=self._session.project,
            profile=profile,
            frames=frames,
            guard_frames=guard_frames,
            preserve_packet_metadata=preserve_packet_metadata,
            name=name,
            session=self._session,
            connection_overrides=None,
        )


class FacadeCaptureApi:
    """Production capture API accessible as project.capture.

    Supports both auto-session (one-shot) and explicit session modes.
    """

    def __init__(self, project: RadarProject):
        self._project = project

    def run(
        self,
        profile: str | Any = "smoke_v1",
        frames: int | None = None,
        guard_frames: int | None = None,
        preserve_packet_metadata: bool = True,
        name: str = "dca_capture",
        session: RadarSession | None = None,
        connection_overrides: Any = None,
        **kwargs,  # Accept com_port, host_ip, dca_ip for backward compat
    ) -> CaptureRunResult:
        """Execute a full production capture.

        If session is provided, connection_overrides must be None.
        If session is None, auto-connects from local.toml.
        """
        if session is not None and connection_overrides is not None:
            raise ValueError(
                "Cannot specify both session and connection_overrides. "
                "Use session settings or auto-connect with overrides, not both."
            )

        # Backward compat: convert legacy kwargs to ConnectionOverrides
        if connection_overrides is None and kwargs:
            from awr2944_dca.api._session import ConnectionOverrides as CO
            co_kwargs = {}
            for k in ('com_port', 'host_ip', 'dca_ip', 'data_port', 'cmd_port'):
                if k in kwargs:
                    co_kwargs[k] = kwargs.pop(k)
            if co_kwargs:
                connection_overrides = CO(**co_kwargs)

        return _run_capture_facade(
            project=self._project,
            profile=profile,
            frames=frames,
            guard_frames=guard_frames,
            preserve_packet_metadata=preserve_packet_metadata,
            name=name,
            session=session,
            connection_overrides=connection_overrides,
        )

    def plan(
        self,
        profile: str | Any = "smoke_v1",
        frames: int | None = None,
        guard_frames: int | None = None,
    ) -> "CapturePlan":
        """Compile a typed :class:`~awr2944_dca.api._capture_plan.CapturePlan` without touching hardware.

        All values are snapshotted at call time; subsequent mutations to
        ``p.config`` have no effect on the returned plan.

        Example::

            plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
            plan.cube_shape      # (8, 128, 4, 256)
            plan.awr_commands    # tuple of SDK CLI strings
            plan.to_dict()       # legacy-compatible dict == p.capture.dry_run(...)
        """
        from awr2944_dca.api._config_resolver import resolve_capture_config
        from awr2944_dca.api._capture_plan import CapturePlan

        resolved = resolve_capture_config(
            project=self._project,
            profile=profile,
            frames=frames,
            guard_frames=guard_frames,
        )
        return CapturePlan._from_resolved(resolved, self._project, profile)

    def dry_run(
        self,
        profile: str | Any = "smoke_v1",
        frames: int | None = None,
        guard_frames: int | None = None,
        **kwargs,  # Accept com_port, host_ip, dca_ip for backward compat
    ) -> dict:
        """Calculate and report capture plan without touching hardware.

        Returns the same keys as before Task 3.  Internally delegates to the
        single canonical serialiser :func:`~awr2944_dca.api._capture_plan._make_dry_run_dict`.
        """
        from awr2944_dca.api._config_resolver import resolve_capture_config
        from awr2944_dca.api._capture_plan import (
            _resolve_dca_info,
            _make_dry_run_dict,
            _legacy_profile_display,
        )

        resolved_config = resolve_capture_config(
            project=self._project,
            profile=profile,
            frames=frames,
            guard_frames=guard_frames,
        )
        dca_info       = _resolve_dca_info(self._project)
        legacy_display = _legacy_profile_display(resolved_config, profile)
        return _make_dry_run_dict(resolved_config, dca_info, legacy_display)

    def run_smoke(self, name: str = "dca_capture", **kwargs) -> CaptureRunResult:
        """Convenience wrapper for the smoke_v1 profile."""
        return self.run(profile="smoke_v1", name=name, **kwargs)

    # Backward-compatibility bridges for old CaptureApi callers
    def _load_toolchain(self) -> dict | None:
        """Compatibility: delegates to the old CaptureApi._load_toolchain."""
        from awr2944_dca.lab import CaptureApi
        return CaptureApi(self._project)._load_toolchain()

    def _build_dca_cli(self, toolchain: dict):
        """Compatibility: delegates to the old CaptureApi._build_dca_cli."""
        from awr2944_dca.lab import CaptureApi
        return CaptureApi(self._project)._build_dca_cli(toolchain)

    def _resolve_profile(self, profile: str):
        """Compatibility: delegates to the old CaptureApi._resolve_profile."""
        from awr2944_dca.lab import CaptureApi
        return CaptureApi(self._project)._resolve_profile(profile)


def _resolve_profile(project: RadarProject, profile: str | Any) -> Any:
    """Step 1: Resolve profile name or object."""
    from awr2944_dca.api.profile import RadarProfile as PubProfile

    if isinstance(profile, str):
        return project.profiles.get(profile)
    if isinstance(profile, PubProfile):
        return profile
    raise TypeError(f"profile must be str or RadarProfile, got {type(profile).__name__}")


def _resolve_frames(project: RadarProject, profile: Any, explicit: int | None) -> int:
    """Resolve frames: explicit → profile → project default."""
    if explicit is not None:
        return explicit
    if profile.frame.frame_count:
        return profile.frame.frame_count
    return project.config.portable.frames


def _resolve_guard_frames(project: RadarProject, explicit: int | None) -> int:
    """Resolve guard_frames: explicit → project default."""
    if explicit is not None:
        return explicit
    return project.config.portable.guard_frames


def _run_capture_facade(
    project: RadarProject,
    profile: str | Any,
    frames: int | None,
    guard_frames: int | None,
    preserve_packet_metadata: bool,
    name: str,
    session: RadarSession | None,
    connection_overrides: Any,
) -> CaptureRunResult:
    """Core facade delegation implementing the 10-step order."""
    from awr2944_dca.capture_session import run_capture
    from awr2944_dca.api._session import (
        RadarSession as SessionClass,
        resolve_connection,
        ConnectionOverrides,
    )
    from awr2944_dca.api._lock import HardwareLease
    import dataclasses

    # Validate capture name
    _validate_capture_name(name)

    from awr2944_dca.api._config_resolver import resolve_capture_config
    import dataclasses

    # Step 1-4: Resolve capture configuration and preflight
    resolved_config = resolve_capture_config(
        project=project,
        profile=profile,
        frames=frames,
        guard_frames=guard_frames,
    )
    
    sdk_cli_commands = resolved_config.cli_commands
    effective = resolved_config.structured_profile # Might be None
    
    # Rebuild capture_plan for backward compatibility
    capture_plan = {
        "frames": resolved_config.byte_plan.total_frames,
        "guard_frames": resolved_config.byte_plan.guard_frames,
        "expected_canonical_dca_bytes": resolved_config.byte_plan.canonical_dca_bytes,
        "expected_native_dca_bytes": resolved_config.byte_plan.native_dca_bytes,
        "canonical_logical_bytes": resolved_config.byte_plan.canonical_active_payload_bytes,
        "logical_depacked_bytes": resolved_config.byte_plan.native_active_payload_bytes,
    }

    # Step 5: Resolve connection
    auto_session = session is None
    if auto_session:
        conn = resolve_connection(project.root, connection_overrides)
        lease = HardwareLease(
            com_port=conn.com_port,
            host_ip=conn.host_ip,
            data_port=conn.data_port,
            dca_ip=conn.dca_ip,
            cmd_port=conn.cmd_port,
            project_root=str(project.root),
        )
    else:
        conn = session.connection
        lease = None
        # Transition to CAPTURING before toolchain resolution
        # so that construction failures move it to ERROR
        session._enter_capturing()

    try:
        # Step 6: Acquire global lock (for auto-session)
        if lease:
            lease.acquire()

        # Get internal DspRadarProfile
        dsp_profile = effective.to_dsp_profile()

        # Build DCA CLI
        dca_cli = None
        dca_configuration = {}
        if conn.dca_control_exe:
            from awr2944_dca.dca_cli import DcaCli
            cf_path = Path(conn.cf_json_path) if conn.cf_json_path else None
            if cf_path and not cf_path.exists():
                cf_path = project.root / "tools" / "dca1000" / "cf.json"
            
            # Build toolchain dict exactly like legacy _load_toolchain / from_toolchain does
            if cf_path and cf_path.exists():
                dca_cli = DcaCli(
                    control_exe=Path(conn.dca_control_exe),
                    record_exe=Path(conn.dca_record_exe),
                    rf_api_dll=Path(conn.rf_api_dll) if conn.rf_api_dll else Path(""),
                    cf_json_path=cf_path,
                )
                dca_cli.dry_run = False
                # Render config safely (no hardware calls)
                dca_configuration = dca_cli.render_config()

        # Step 7: Create capture directory
        capture_id, output_dir = _create_capture_manifest_facade(project, name)
        
        # Step 7b: Write provenance artifacts atomically
        config_summary = {
            "config_summary_schema_version": 1,
            "target_device": "AWR2944",
            "sdk_version": "04.07.02.01",
            "firmware_config_target": "awr294x_mmw_demo",
            "source_config_kind": resolved_config.source_kind,
            "source_config_sha256": resolved_config.source_sha256,
            "resolved_config_sha256": resolved_config.resolved_sha256,
            "radar_config_sha256": resolved_config.resolved_sha256,
            "structured_profile_available": resolved_config.structured_profile is not None,
            "dsp_profile_available": resolved_config.dsp_profile is not None,
            "preflight_warnings": resolved_config.preflight_warnings,
        }
        
        # Merge byte plan into summary
        import dataclasses
        config_summary.update(dataclasses.asdict(resolved_config.byte_plan))
        
        # Write resolved config text
        _write_atomic(output_dir / "resolved_config.cfg", resolved_config.resolved_cfg_text.encode("utf-8"))
        
        if resolved_config.source_kind == "cfg_file" and resolved_config.source_path:
            config_summary["source_config_path_rel"] = "source_config.cfg"
            _write_atomic(output_dir / "source_config.cfg", resolved_config.source_path.read_bytes())
            
        elif resolved_config.source_kind == "toml_file" and resolved_config.source_path:
            config_summary["source_config_path_rel"] = "source_profile.toml"
            _write_atomic(output_dir / "source_profile.toml", resolved_config.source_path.read_bytes())
            
        if resolved_config.structured_profile:
            config_summary["resolved_profile_path_rel"] = "resolved_profile.toml"
            _write_atomic(output_dir / "resolved_profile.toml", resolved_config.structured_profile.to_toml().encode("utf-8"))
            
        summary_path = output_dir / "config_summary.json"
        _write_atomic(summary_path, json.dumps(config_summary, indent=2).encode("utf-8"))

        # Step 8: Call frozen run_capture
        internal_result = run_capture(
            output_dir=output_dir,
            sdk_cli_commands=sdk_cli_commands,
            profile=dsp_profile,
            guard_frames=resolved_config.byte_plan.guard_frames,
            com_port=conn.com_port,
            host_ip=conn.host_ip,
            dca_ip=conn.dca_ip,
            dca_cli=dca_cli,
            dca_configuration=dca_configuration,
        )

        # Step 9: Package result
        # Update project record with reproducibility metadata
        _update_project_record(
            project.root,
            capture_id,
            resolved_config,
            capture_plan,
            conn,
        )

        from awr2944_dca.lab import RadarCapture
        capture_obj = RadarCapture(project.root, capture_id)

        result = CaptureRunResult(
            capture=capture_obj,
            session_result=internal_result,
            effective_profile=effective,
            capture_plan=capture_plan,
        )

        if session is not None:
            session._exit_capturing(internal_result.success)

        return result

    except Exception:
        if session is not None:
            session._enter_error()
        raise
    finally:
        # Step 10: Release lock for auto-session
        if lease:
            lease.release()


def _update_project_record(
    root: Path,
    capture_id: str,
    resolved_config: Any,
    capture_plan: dict,
    connection: Any,
) -> None:
    """Update capture_manifest.json with reproducibility metadata.

    Never modifies production manifest.json.
    """
    manifest_path = root / "captures" / capture_id / "capture_manifest.json"
    if not manifest_path.exists():
        # If project layer manifest doesn't exist, create minimal one
        data = {
            "capture_id": capture_id,
            "status": "complete",
        }
    else:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            data = {"capture_id": capture_id}

    # Add reproducibility fields
    data["source_profile_name"] = resolved_config.source_name if resolved_config.source_name else "programmatic"
    data["effective_structured_profile"] = _profile_to_dict(resolved_config.structured_profile)
    
    # V4 provenance fields
    data["manifest_schema_version"] = 4
    data["source_config_kind"] = resolved_config.source_kind
    data["source_config_sha256"] = resolved_config.source_sha256
    data["resolved_config_sha256"] = resolved_config.resolved_sha256
    data["radar_config_sha256"] = resolved_config.resolved_sha256
    data["config_summary_path_rel"] = "config_summary.json"
    data["resolved_config_path_rel"] = "resolved_config.cfg"
    
    if resolved_config.source_kind == "cfg_file":
        data["source_config_path_rel"] = "source_config.cfg"
    elif resolved_config.source_kind == "toml_file":
        data["source_profile_path_rel"] = "source_profile.toml"
        
    if resolved_config.structured_profile:
        data["resolved_profile_path_rel"] = "resolved_profile.toml"
    data["run_frames"] = capture_plan.get("frames")
    data["run_guard_frames"] = capture_plan.get("guard_frames")
    data["capture_plan"] = capture_plan
    data["connection_source"] = connection.source
    data["resolved_endpoints"] = {
        "com_port": connection.com_port,
        "host_ip": connection.host_ip,
        "dca_ip": connection.dca_ip,
    }
    data["api_version"] = _get_api_version()

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")


def _profile_to_dict(profile: Any) -> dict:
    """Serialize public RadarProfile to dict."""
    import dataclasses
    if dataclasses.is_dataclass(profile):
        return dataclasses.asdict(profile)
    return {}


def _create_capture_manifest_facade(project: Any, capture_name: str) -> tuple[str, Path]:
    """Lightweight capture scaffolding for Phase D bypassing legacy project.json.
    
    Uses already-resolved RadarProject config to generate the standard manifest.
    Returns (capture_id, output_dir).
    """
    import datetime
    from awr2944_dca.project import safe_slug, atomic_json_write, SCHEMA_VERSION

    now = datetime.datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    slug = safe_slug(capture_name)
    capture_id = f"{ts}_{slug}"

    output_dir = project.root / "captures" / capture_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    (output_dir / "raw").mkdir(exist_ok=True)
    (output_dir / "metadata" / "mmws_logs").mkdir(parents=True, exist_ok=True)
    (output_dir / "processed").mkdir(exist_ok=True)

    notes_path = output_dir / "notes.md"
    notes_content = f"# {capture_name}\n\nCreated: {now.isoformat()}\n"
    notes_path.write_text(notes_content, encoding="utf-8")

    # Handle TOML configs
    expected_bytes = getattr(project.config.portable, "expected_bytes", 4_194_304)
    project_name = getattr(project.config.portable, "name", project.root.name)
    project_id = getattr(project.config.portable, "project_id", project_name)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "capture_id": capture_id,
        "capture_name": capture_name,
        "project_id": project_id,
        "project_name": project_name,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "status": "created",
        "mode": "direct",
        "root_path_abs": str(project.root),
        "capture_dir_rel": f"captures/{capture_id}",
        "capture_dir_abs": str(output_dir),
        "raw_dir_rel": f"captures/{capture_id}/raw",
        "metadata_dir_rel": f"captures/{capture_id}/metadata",
        "processed_dir_rel": f"captures/{capture_id}/processed",
        "notes_path_rel": f"captures/{capture_id}/notes.md",
        "postproc_dir_abs": getattr(project.config.local, "postproc_dir", ""),
        "expected_bytes": expected_bytes,
        "actual_raw_file_size": None,
        "raw_file_name": None,
        "raw_file_rel": None,
        "raw_file_sha256": None,
        "radar_config_name": None,
        "radar_config_path": None,
        "radar_config_sha256": None,
        "radar_config_lua_path": None,
        "radar_config_lua_sha256": None,
        "adc_inspect_path_rel": None,
        "validation_records": [],
        "copied_mmws_files": [],
        "mmws_output_snapshot_path_rel": None,
        "firmware_run_id": None,
        "config_run_id": None,
        "dca_setup_run_id": None,
        "capture_trigger_run_id": None,
        "postproc_run_id": None,
        "workflow_id": None,
        "warnings": [],
        "errors": [],
        "tags": [],
        "notes": "",
    }

    atomic_json_write(output_dir / "capture_manifest.json", manifest)
    return capture_id, output_dir
