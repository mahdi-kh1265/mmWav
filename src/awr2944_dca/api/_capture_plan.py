"""CapturePlan — typed immutable snapshot from ``p.capture.plan()``.

Also provides:
  _resolve_dca_info()        — canonical DCA info resolver for plan() and dry_run()
  _make_dry_run_dict()       — single canonical serializer (preserves dry_run() contract)
  _legacy_profile_display()  — compute the legacy profile_name key value
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from awr2944_dca.api._config_resolver import BytePlan, ResolvedCaptureConfig


# ---------------------------------------------------------------------------
# DCA info resolver
# ---------------------------------------------------------------------------

def _resolve_dca_info(project: Any) -> dict:
    """Resolve DCA toolchain + network info from the canonical ProjectConfig.

    **Modern-first, never partial-legacy mixing.**

    If ANY of the four modern DCA toolchain fields (``dca_control_exe``,
    ``dca_record_exe``, ``rf_api_dll``, ``cf_json_path``) is set in
    ``ProjectConfig``, the modern config is used exclusively — even if
    incomplete.  This matches exactly what production ``run()`` would see via
    ``resolve_connection()``.

    Legacy fallback to ``toolchain.local.json`` is permitted ONLY when ALL
    four modern tool fields are blank/empty.  It exists solely for backward
    compatibility with projects that were configured before Task 2; Task 5
    will remove it.

    Returns a dict with both modern snapshot keys (used by
    ``CapturePlan.dca_config``) and legacy-compat keys (used only by
    ``_make_dry_run_dict``).
    """
    cfg = project._config

    modern_control = cfg.local.dca_control_exe or ""
    modern_record  = cfg.local.dca_record_exe  or ""
    modern_dll     = cfg.local.rf_api_dll      or ""
    modern_cf_json = cfg.local.cf_json_path    or ""

    any_modern_set = bool(modern_control or modern_record or modern_dll or modern_cf_json)

    if any_modern_set:
        # Cases B (all set) and C (partially set): modern config exclusively.
        control_exe = modern_control
        record_exe  = modern_record
        rf_api_dll  = modern_dll
        cf_json_raw = modern_cf_json
    else:
        # Case A: ALL four modern fields blank — try legacy toolchain.local.json.
        control_exe = record_exe = rf_api_dll = cf_json_raw = ""
        try:
            from awr2944_dca.lab import CaptureApi
            toolchain = CaptureApi(project)._load_toolchain()
            if toolchain:
                control_exe = toolchain.get("dca_cli_control_exe", "") or ""
                record_exe  = toolchain.get("dca_cli_record_exe",  "") or ""
                rf_api_dll  = toolchain.get("rf_api_dll",          "") or ""
                cf_json_raw = toolchain.get("dca_cli_cf_json",     "") or ""
        except Exception:
            pass

    # Resolve the runtime cf.json path (same existence-check logic as old dry_run).
    cf_json_runtime = "NOT_CONFIGURED"
    if cf_json_raw:
        cf_path = Path(cf_json_raw)
        if not cf_path.exists():
            fallback1 = project.root / "tools" / "dca1000" / "cf.json"
            if fallback1.exists():
                cf_path = fallback1
            else:
                cf_path = Path("C:\\ti\\cf.json")
        cf_json_runtime = str(cf_path)

    return {
        # Modern snapshot — used by CapturePlan.dca_config (richer than dry_run dict)
        "host_ip":        cfg.local.host_ip     or "",
        "dca_ip":         cfg.portable.dca_ip,
        "config_port":    cfg.portable.config_port,
        "data_port":      cfg.portable.data_port,
        "dca_control_exe": control_exe,
        "dca_record_exe":  record_exe,
        "rf_api_dll":      rf_api_dll,
        "cf_json_path":    cf_json_raw,
        # Legacy compat keys — used only by _make_dry_run_dict
        "dca_control_executable":  control_exe or "NOT_CONFIGURED",
        "dca_config_source":       cf_json_raw or "NOT_CONFIGURED",
        "dca_config_runtime_path": cf_json_runtime,
    }


def _legacy_profile_display(resolved: Any, raw_profile_input: Any) -> str:
    """Compute the exact legacy ``profile_name`` value preserved in ``dry_run()`` dict.

    This faithfully replicates the old conditional that was inlined in
    ``FacadeCaptureApi.dry_run()`` before Task 3:

    * ``.cfg`` / ``.toml`` file input → filename stem
    * string input                    → the string itself (e.g. ``"smoke_v1"``)
    * ``RadarProfile`` object input   → ``"programmatic"``
    """
    if resolved.source_path is not None:
        return resolved.source_path.name
    if isinstance(raw_profile_input, str):
        return raw_profile_input
    return "programmatic"


def _make_dry_run_dict(
    resolved: Any,
    dca_info: dict,
    legacy_profile_display: str,
) -> dict:
    """Single canonical serializer — produces the exact dict that ``dry_run()`` returns.

    Only the three legacy DCA keys (``dca_control_executable``,
    ``dca_config_source``, ``dca_config_runtime_path``) are taken from
    *dca_info*.  The richer modern snapshot keys are NOT added to this output.
    """
    bp = resolved.byte_plan
    plan_fields = dataclasses.asdict(bp)

    result: dict = {
        "profile_name":          legacy_profile_display,
        "effective_frames":      bp.total_frames,
        "guard_frames":          bp.guard_frames,
        "sdk_cli_command_count": len(resolved.cli_commands),
        **plan_fields,
        "hardware_touched":      False,
        # Legacy-compatible aliases
        "total_frames":                 bp.total_frames,
        "canonical_frames":             bp.canonical_frames,
        "expected_native_dca_bytes":    bp.native_dca_bytes,
        "expected_canonical_dca_bytes": bp.canonical_dca_bytes,
        "logical_depacked_bytes":       bp.native_active_payload_bytes,
        "canonical_logical_bytes":      bp.canonical_active_payload_bytes,
        "canonical_cube": [
            bp.canonical_frames,
            bp.chirps_per_frame,
            bp.rx_channels,
            bp.samples_per_chirp,
        ],
        "dca_storage_expansion_factor": bp.dca_expansion_factor,
        # Legacy DCA toolchain keys
        "dca_control_executable":  dca_info["dca_control_executable"],
        "dca_config_source":       dca_info["dca_config_source"],
        "dca_config_runtime_path": dca_info["dca_config_runtime_path"],
    }
    return result


# ---------------------------------------------------------------------------
# CapturePlan
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapturePlan:
    """Typed, immutable snapshot of a capture plan (hardware never touched).

    Obtained via ``p.capture.plan(profile, frames, guard_frames)``.

    All fields are snapshotted at call time; subsequent mutations to
    ``p.config`` have no effect on this object.

    Quick reference::

        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)

        plan.profile          # "smoke_v1"
        plan.cube_shape       # (8, 128, 4, 256)
        plan.awr_commands     # tuple of SDK CLI strings
        plan.byte_plan        # BytePlan with all byte counts
        plan.dca_config       # snapshot of network + toolchain paths
        plan.hardware_touched # always False

        plan.to_dict()        # legacy-compatible dict == p.capture.dry_run(...)
        plan.print()          # pretty-print to stdout
    """

    # Internal frozen snapshots — not surfaced in repr
    _resolved:               Any   = field(repr=False)  # ResolvedCaptureConfig
    _dca_snapshot:           dict  = field(repr=False, compare=False, hash=False)
    _legacy_profile_display: str   = field(repr=False)

    # Lightweight snapshot primitives for repr and quick access
    profile:          str
    canonical_frames: int
    guard_frames:     int
    total_frames:     int
    cube_shape:       tuple  # (canonical_frames, chirps_per_frame, rx_channels, samples_per_chirp)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def hardware_touched(self) -> bool:
        """Always ``False`` — ``plan()`` never touches hardware."""
        return False

    @property
    def awr_commands(self) -> tuple:
        """AWR2944 SDK CLI commands to be sent over UART, as an immutable tuple."""
        return tuple(self._resolved.cli_commands)

    @property
    def warnings(self) -> tuple:
        """Preflight validation warnings as an immutable tuple of strings."""
        return tuple(self._resolved.preflight_warnings)

    @property
    def byte_plan(self) -> Any:
        """Resolved :class:`~awr2944_dca.api._config_resolver.BytePlan` dataclass."""
        return self._resolved.byte_plan

    @property
    def dca_config(self) -> dict:
        """Snapshot of DCA toolchain and network configuration at plan time.

        Contains modern snapshot keys::

            host_ip, dca_ip, config_port, data_port,
            dca_control_exe, dca_record_exe, rf_api_dll, cf_json_path

        Returns a **defensive copy** — mutating it does not affect this plan.
        """
        return dict(self._dca_snapshot)

    @property
    def derived(self) -> dict:
        """Derived radar waveform metrics (bandwidth, range resolution, etc.)."""
        return dict(self._resolved.derived or {})

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to the legacy-compatible ``dry_run()`` dict format.

        ``plan.to_dict() == p.capture.dry_run(...)`` holds when both are called
        against the same unchanged project/config state.
        """
        return _make_dry_run_dict(
            self._resolved,
            self._dca_snapshot,
            self._legacy_profile_display,
        )

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"CapturePlan(profile={self.profile!r}, "
            f"frames={self.canonical_frames}+{self.guard_frames}g, "
            f"cube={list(self.cube_shape)})"
        )

    def print(self) -> None:
        """Pretty-print this capture plan to stdout."""
        bp = self.byte_plan
        snap = self._dca_snapshot
        print(f"CapturePlan — {self.profile!r}")
        print(f"  Frames : {self.canonical_frames} canonical + "
              f"{self.guard_frames} guard = {self.total_frames} total")
        print(f"  Cube   : {list(self.cube_shape)}  [frames, chirps, rx, samples]")
        print(f"  Bytes  : native={bp.native_dca_bytes:,}  canonical={bp.canonical_dca_bytes:,}")
        print(f"  AWR commands ({len(self.awr_commands)}):")
        for cmd in self.awr_commands:
            print(f"    {cmd}")
        print(f"  DCA control : {snap.get('dca_control_exe') or 'NOT_CONFIGURED'}")
        print(f"  cf.json     : {snap.get('cf_json_path') or 'NOT_CONFIGURED'}")
        print(f"  Network     : host={snap.get('host_ip') or '?'}, "
              f"dca={snap.get('dca_ip')}, "
              f"cmd={snap.get('config_port')}, data={snap.get('data_port')}")
        if self.warnings:
            print(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                print(f"    ⚠ {w}")
        print(f"  hardware_touched: {self.hardware_touched}")

    def _repr_html_(self) -> str:  # Jupyter
        snap = self._dca_snapshot
        bp   = self.byte_plan
        warnings_html = ""
        if self.warnings:
            items = "".join(f"<li>⚠ {w}</li>" for w in self.warnings)
            warnings_html = f"<ul style='color:#ffa07a;margin:4px 0'>{items}</ul>"
        cmds_html = "".join(
            f"<code style='display:block;font-size:0.85em'>{c}</code>"
            for c in self.awr_commands
        )
        return (
            "<div style='font-family:monospace;padding:8px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            f"<h3 style='margin:0 0 8px'>📡 CapturePlan — {self.profile}</h3>"
            f"<p>Frames: <b>{self.canonical_frames}</b> canonical + "
            f"{self.guard_frames} guard = {self.total_frames} total | "
            f"Cube: <code>{list(self.cube_shape)}</code></p>"
            f"<p>Native DCA: {bp.native_dca_bytes:,} B | "
            f"Canonical: {bp.canonical_dca_bytes:,} B</p>"
            f"<details><summary>AWR Commands ({len(self.awr_commands)})</summary>"
            f"{cmds_html}</details>"
            f"<p>DCA: <code>{snap.get('dca_control_exe') or 'NOT_CONFIGURED'}</code> | "
            f"cf.json: <code>{snap.get('cf_json_path') or 'NOT_CONFIGURED'}</code></p>"
            f"<p>Network: host={snap.get('host_ip') or '?'}, dca={snap.get('dca_ip')}, "
            f"cmd={snap.get('config_port')}, data={snap.get('data_port')}</p>"
            f"{warnings_html}"
            "<p style='color:#888;font-size:0.85em'>hardware_touched: False</p>"
            "</div>"
        )

    # ------------------------------------------------------------------
    # Internal factory
    # ------------------------------------------------------------------

    @classmethod
    def _from_resolved(
        cls,
        resolved: Any,
        project: Any,
        raw_profile_input: Any,
    ) -> "CapturePlan":
        """Internal factory — do not call directly.  Use ``p.capture.plan()``."""
        dca_snapshot   = _resolve_dca_info(project)
        legacy_display = _legacy_profile_display(resolved, raw_profile_input)
        bp = resolved.byte_plan

        # Effective profile name: prefer resolved.source_name (set by resolver
        # from the input string or from the RadarProfile.name field).
        profile_name: str = (
            resolved.source_name
            or (resolved.structured_profile.name if resolved.structured_profile else None)
            or legacy_display
        )

        return cls(
            _resolved=resolved,
            _dca_snapshot=dca_snapshot,
            _legacy_profile_display=legacy_display,
            profile=profile_name,
            canonical_frames=bp.canonical_frames,
            guard_frames=bp.guard_frames,
            total_frames=bp.total_frames,
            cube_shape=(
                bp.canonical_frames,
                bp.chirps_per_frame,
                bp.rx_channels,
                bp.samples_per_chirp,
            ),
        )
