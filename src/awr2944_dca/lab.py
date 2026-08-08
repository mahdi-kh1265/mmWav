"""Jupyter-friendly lab API for AWR2944 + DCA1000 radar experiments.

Provides human-friendly wrappers around the validated backend modules:
``project.py``, ``dca/workflow.py``, ``eth.py``, ``mmws_auto.py``.

Supports both manual dofile paste and automated execution via
the mmWave Studio RSTD bridge. StartFrame requires explicit confirmation.

Usage::

    from awr2944_dca.lab import RadarProject

    lab = RadarProject.open(".")
    lab.set_defaults(firmware_run_id="df1f275c", config_run_id="4b87faae")

    run = lab.capture_smoke("my_capture")
    print(run.dofile())       # paste into mmWave Studio
    run = run.resume()        # check result, advance
    cap = run.capture()       # get the bound RadarCapture
"""
from __future__ import annotations
import datetime
import json
from pathlib import Path
from typing import Any
from awr2944_dca.project import PROJECT_DEFAULTS, add_capture_note, add_capture_tags, find_project_root, get_defaults, inspect_capture, load_project, new_capture, project_status, set_defaults as _set_defaults, verify_capture

class RadarProject:
    """Jupyter-friendly wrapper for an AWR2944 radar project.

    Open a project with :meth:`open` or :meth:`open_here`, then use
    :meth:`capture_smoke` to start a manual-paste capture workflow.
    """

    def __init__(self, root: Path):
        self._root = Path(root).resolve()
        try:
            self._proj = load_project(self._root)
        except FileNotFoundError:
            if not (self._root / "awr2944.toml").exists():
                raise
            self._proj = {}
        self._mmws_manager: 'MmWaveStudioManager | None' = None
        self._headless_api: 'HeadlessApi | None' = None
        
        # New API structure
        from awr2944_dca._config import ProjectConfig
        self._config = ProjectConfig(self._root)

    @classmethod
    def create(cls, name: str, parent: str | Path, git_init: bool = False) -> 'RadarProject':
        from awr2944_dca._project import create_project
        return create_project(name, parent, git_init)

    @classmethod
    def create_at(cls, path: str | Path, git_init: bool = False) -> 'RadarProject':
        from awr2944_dca._project import create_project_at
        return create_project_at(path, git_init)

    @classmethod
    def open(cls, root: str | Path) -> 'RadarProject':
        """Open a project by path."""
        from awr2944_dca._project import open_project
        return open_project(root)

    @classmethod
    def open_here(cls) -> 'RadarProject':
        """Find project.json by walking up from cwd."""
        from awr2944_dca._project import open_project_here
        return open_project_here()

    @classmethod
    def init(cls, path: 'str | Path' = ".") -> 'RadarProject':
        """Idempotently initialize *path* as a RadarProject (\"git init\" semantics).

        If the directory already contains a valid project (``awr2944.toml`` or
        ``project.json``) it is opened and returned unchanged.  Otherwise the
        standard scaffolding is created inside the existing directory without
        touching any pre-existing files or data.

        The directory **must already exist**; use :meth:`create` to create a
        new project directory from scratch.

        Raises:
            FileNotFoundError: If *path* does not exist or is not a directory.
            RuntimeError: If *path* looks like the awr2944-dca package source repo.
        """
        from pathlib import Path as _Path
        from awr2944_dca._project import init_project_at
        return init_project_at(_Path(path))

    @classmethod
    def init_here(cls) -> 'RadarProject':
        """Idempotently initialize the *current working directory* as a RadarProject.

        Equivalent to ``RadarProject.init(".")``.  Useful in Jupyter notebooks
        where the working directory is already the experiment folder::

            from awr2944_dca import RadarProject
            p = RadarProject.init_here()
        """
        from awr2944_dca._project import init_project_here
        return init_project_here()

    @property
    def config(self):
        return self._config

    @property
    def root(self) -> Path:
        return self._root

    @property
    def name(self) -> str:
        if self._proj and 'name' in self._proj:
            return self._proj['name']
        return self._config.portable.project_name

    @property
    def project_id(self) -> str:
        if self._proj and 'project_id' in self._proj:
            return self._proj['project_id']
        return self._config.portable.project_id

    @property
    def profiles(self):
        """Access the profile collection for this project."""
        if not hasattr(self, '_profiles'):
            from awr2944_dca.api.profile_collection import ProfileCollection
            self._profiles = ProfileCollection(self._root / "profiles")
        return self._profiles

    @property
    def headless(self) -> 'HeadlessApi':
        """Access the headless capture API (AWR2944 mmw demo + DCA CLI).

        Lazy-loaded to avoid import overhead when not needed.
        Independent of mmWave Studio, RSTD, Lua, pywinauto.
        """
        if self._headless_api is None:
            from awr2944_dca.headless import HeadlessApi
            self._headless_api = HeadlessApi(self._root)
        return self._headless_api

    @property
    def capture(self) -> 'FacadeCaptureApi':
        """Production capture API (direct UDP + UART)."""
        if not hasattr(self, '_capture_api'):
            from awr2944_dca.api._capture_run import FacadeCaptureApi
            self._capture_api = FacadeCaptureApi(self)
        return self._capture_api

    @property
    def hardware(self) -> 'HardwareManager':
        """Hardware inspection and discovery (read-only checks)."""
        if not hasattr(self, '_hardware'):
            from awr2944_dca._doctor import HardwareManager
            self._hardware = HardwareManager(self)
        return self._hardware

    def doctor(self, include_hardware: bool = True) -> 'HardwareReport':
        """Run project health and hardware diagnostics."""
        return self.hardware.verify(include_hardware=include_hardware)

    def connect(self, force: bool = False) -> 'RadarSession':
        """Create a RadarSession with resolved settings and global lock.

        The session is NOT a persistent connection. It resolves settings
        from local.toml, acquires a global hardware lock, and provides
        a capture API.
        """
        from awr2944_dca.api._session import (
            RadarSession, resolve_connection, ConnectionOverrides,
        )
        from awr2944_dca.api._lock import HardwareLease

        conn = resolve_connection(self._root)
        lease = HardwareLease(
            com_port=conn.com_port,
            host_ip=conn.host_ip,
            data_port=conn.data_port,
            dca_ip=conn.dca_ip,
            cmd_port=conn.cmd_port,
            project_root=str(self._root),
        )
        lock_info = lease.acquire(force=force)

        # Write breadcrumb
        breadcrumb_path = self._root / ".awr2944" / "session.breadcrumb"
        breadcrumb_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            breadcrumb_path.write_text(str(lease.lock_path), encoding="utf-8")
        except Exception:
            pass

        return RadarSession(
            project=self,
            connection=conn,
            lease=lease,
            lock_info=lock_info,
        )

    def emergency_stop(self, force: bool = False) -> 'EmergencyStopReport':
        """Send sensorStop + DCA stop_record using existing proven helpers.

        Respects lock ownership unless force=True.
        """
        from awr2944_dca.api._emergency import emergency_stop as _estop
        cfg = self._config
        return _estop(
            com_port=cfg.local.com_port or "COM8",
            host_ip=cfg.local.host_ip or "192.168.33.30",
            dca_ip=cfg.portable.dca_ip,
            data_port=cfg.portable.data_port,
            cmd_port=cfg.portable.config_port,
            force=force,
            project_root=str(self._root),
        )

    def status(self) -> dict:
        """Return project status summary dict."""
        return project_status(self._root)

    def show_defaults(self) -> dict:
        """Return current project defaults, merged with fallbacks."""
        return get_defaults(self._root)

    def set_defaults(self, **kwargs: Any) -> dict:
        """Update project defaults. Returns updated defaults dict.

        Valid keys: firmware_run_id, config_run_id, expected_bytes,
        archive_existing, confirm_startframe, bind_force, ensure_eth.
        """
        return _set_defaults(self._root, **kwargs)

    @property
    def captures(self) -> 'CaptureCollection':
        """Access the capture collection for this project."""
        if not hasattr(self, '_captures_collection'):
            from awr2944_dca.api._collection import CaptureCollection
            self._captures_collection = CaptureCollection(self._root)
        return self._captures_collection

    def get_capture(self, query: str) -> 'RadarCapture':
        """Fuzzy lookup: match by capture_id prefix, name substring, or date.

        Compatibility alias for project.captures.get(query).
        """
        return self.captures.get(query)

    def latest_capture(self) -> 'RadarCapture':
        """Return the newest managed capture.

        Compatibility alias for project.captures.latest().
        """
        return self.captures.latest()

    def _probe_dir(self) -> Path:
        """Return the probe directory for this project."""
        rel = self._proj.get('probe_dir_rel', 'ti/probe_logs')
        return self._root / rel

    def workflows(self) -> list[dict]:
        """List capture-smoke workflow state files."""
        probe = self._probe_dir()
        if not probe.exists():
            return []
        results = []
        for f in sorted(probe.glob('dca_capture_smoke_*_state.json')):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                results.append({'workflow_id': data.get('workflow_id', ''), 'current_stage': data.get('current_stage', ''), 'completed': data.get('completed', False), 'created_at': data.get('created_at', ''), 'updated_at': data.get('updated_at', ''), 'capture_id': data.get('capture_id', ''), 'state_file': str(f)})
            except (json.JSONDecodeError, IOError):
                pass
        return results

    @property
    def eth(self) -> 'EthernetManager':
        """Access the Ethernet pairing manager."""
        return EthernetManager(self)

    def __repr__(self) -> str:
        st = project_status(self._root)
        return f"RadarProject('{self.name}', id={self.project_id}, captures={st['capture_count']}, root={self._root})"

    def _repr_html_(self) -> str:
        st = project_status(self._root)
        caps = st.get('captures', [])
        rows = ''
        for c in caps[-10:]:
            rows += f"<tr><td><code>{c['capture_id']}</code></td><td>{c.get('capture_name', '')}</td><td>{c.get('status', '')}</td><td>{c.get('created_at', '')}</td></tr>\n"
        cap_table = ''
        if rows:
            cap_table = '<table><thead><tr><th>Capture ID</th><th>Name</th><th>Status</th><th>Created</th></tr></thead><tbody>\n' + rows + '</tbody></table>'
        else:
            cap_table = '<p><em>No captures yet.</em></p>'
        return f"<div style='font-family: monospace; padding: 8px; border: 1px solid #444; border-radius: 4px; background: #1a1a2e; color: #e0e0e0;'><h3 style='margin: 0 0 8px;'>🛰️ {self.name}</h3><p>Project ID: <code>{self.project_id}</code> · Captures: {st['capture_count']} · Root: <code>{self._root}</code></p>{cap_table}</div>"

class RadarCapture:
    """Wraps a single capture folder for notebook use.

    Provides verification, inspection, notes, and tag management.
    """

    def __init__(self, root: Path, capture_id: str):
        self._root = Path(root).resolve()
        self._capture_id = capture_id
        self._manifest: dict | None = None
        self._load_manifest()

    def _load_manifest(self) -> None:
        """Load manifest from disk."""
        path = self._root / 'captures' / self._capture_id / 'capture_manifest.json'
        if path.exists():
            self._manifest = json.loads(path.read_text(encoding='utf-8'))
        else:
            self._manifest = None

    @property
    def capture_id(self) -> str:
        return self._capture_id

    @property
    def path(self) -> Path:
        """Path to this capture's directory."""
        return self._root / 'captures' / self._capture_id

    def refresh(self) -> 'RadarCapture':
        """Reload manifest from disk."""
        self._load_manifest()
        return self

    def status(self) -> dict:
        """Return capture status as a dict."""
        from awr2944_dca.api._status_resolver import resolve_capture_status
        prod_manifest = self._load_production_manifest()
        return resolve_capture_status(self._manifest, prod_manifest)

    def verify(self, strict: bool = False):
        """Run capture verification.

        Returns CaptureVerificationReport.
        If strict=True, raises CaptureVerificationError on failure.
        """
        prod_manifest = self._load_production_manifest()
        if prod_manifest is not None:
            from awr2944_dca.api._verification import verify_production_capture
            cap_dir = self._root / 'captures' / self._capture_id
            report = verify_production_capture(cap_dir, prod_manifest)
            if strict:
                report.raise_for_errors()
            return report
        # Fallback to legacy project-level verify for old captures
        result = verify_capture(self._root, self._capture_id)
        from awr2944_dca.api._verification import CaptureVerificationReport, VerificationCheck
        checks = []
        for err in result.get('errors', []):
            checks.append(VerificationCheck('legacy', 'FAIL', err))
        for warn in result.get('warnings', []):
            checks.append(VerificationCheck('legacy', 'WARN', warn))
        if not checks:
            checks.append(VerificationCheck('legacy', 'PASS', 'All checks passed'))
        report = CaptureVerificationReport(
            success=result.get('passed', False),
            checks=checks,
        )
        if strict:
            report.raise_for_errors()
        return report


    def _resolve_viewer_profile(self):
        """Shared helper to resolve RadarProfile from manifests for viewer launch."""
        import json
        prof = None
        prod_manifest_path = self._root / 'captures' / self._capture_id / 'manifest.json'
        if prod_manifest_path.exists():
            with open(prod_manifest_path, 'r', encoding='utf-8') as f:
                prod_manifest = json.load(f)
            if prod_manifest.get('profile'):
                from awr2944_dca.capture_manifest import profile_from_manifest_dict
                prof = profile_from_manifest_dict(prod_manifest['profile'])
            elif prod_manifest.get('sdk_cli_commands'):
                prof = self._reconstruct_profile_from_config_lines(prod_manifest['sdk_cli_commands'])

        if prof is None:
            if self._manifest and self._manifest.get('radar_config'):
                prof = self._reconstruct_profile_from_config_lines(self._manifest['radar_config'])

        if prof is None:
            return None

        if 'prod_manifest' in locals() and prod_manifest.get('canonical_frame_count'):
            import dataclasses
            prof = dataclasses.replace(prof, frame_count=prod_manifest['canonical_frame_count'])
            
        return prof

    def open_viewer(self):
        """Open the standalone MATLAB viewer for this capture."""
        canonical_path = self._root / 'captures' / self._capture_id / 'adc_data_canonical.bin'
        if not canonical_path.exists():
            print(f'Canonical raw data missing: {canonical_path}')
            return
        prof = self._resolve_viewer_profile()
        if prof is None:
            print('Could not find or reconstruct radar profile from manifest.')
            return

        from awr2944_dca.viewer import export_viewer_payload_and_launch
        import awr2944_dca
        from pathlib import Path
        
        matlab_dir = Path(awr2944_dca.__file__).parent / '_matlab_viewer'
        export_viewer_payload_and_launch(capture_path=canonical_path, profile=prof, mode='standalone', matlab_script_dir=matlab_dir)

    def _reconstruct_profile_from_config_lines(self, config_lines: list[str]):
        """Legacy helper to reconstruct RadarProfile from UART command lines."""
        from awr2944_dca.dsp.config import RadarProfile
        import dataclasses
        frame_count = None
        for line in config_lines:
            line = line.strip()
            if line.startswith('frameCfg'):
                parts = line.split()
                if len(parts) >= 5:
                    frame_count = int(parts[4])
            elif line.startswith('ar1.FrameConfig'):
                line = line.replace('(', ' ').replace(')', ' ').replace(',', ' ')
                parts = line.split()
                if len(parts) >= 4:
                    frame_count = int(parts[3])
        if frame_count is None:
            return None
        prof = RadarProfile.from_smoke_v1()
        return dataclasses.replace(prof, frame_count=frame_count)

    def inspect_adc(self, refresh: bool=False) -> dict:
        """Run or reload ADC inspection. Returns inspection dict."""
        return inspect_capture(self._root, self._capture_id, refresh_adc_inspect=refresh)

    def add_note(self, text: str) -> None:
        """Append a timestamped note to notes.md."""
        add_capture_note(self._root, self._capture_id, text)

    def notes(self) -> str:
        """Read notes.md content."""
        path = self._root / 'captures' / self._capture_id / 'notes.md'
        if path.exists():
            return path.read_text(encoding='utf-8')
        return ''

    def add_tags(self, *tags: str) -> list[str]:
        """Add tags to the capture manifest. Returns updated tag list."""
        result = add_capture_tags(self._root, self._capture_id, *tags)
        self._load_manifest()
        return result

    @property
    def raw_path(self) -> Path | None:
        """Path to raw/adc_data.bin, or None if not present."""
        p = self._root / 'captures' / self._capture_id / 'raw' / 'adc_data.bin'
        return p if p.exists() else None

    @property
    def raw(self) -> 'CaptureRawData':
        """Typed accessor for raw binary capture data."""
        if not hasattr(self, '_raw_data'):
            from awr2944_dca.api._capture_raw import CaptureRawData
            cap_dir = self._root / 'captures' / self._capture_id
            prod_manifest = self._load_production_manifest()
            self._raw_data = CaptureRawData(cap_dir, prod_manifest)
        return self._raw_data

    def _load_production_manifest(self) -> dict | None:
        """Load production manifest.json (written by frozen run_capture)."""
        path = self._root / 'captures' / self._capture_id / 'manifest.json'
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, IOError):
                return None
        return None

    @property
    def manifest(self) -> dict | None:
        """Return the production manifest.json dict."""
        return self._load_production_manifest()

    @property
    def project_record(self) -> dict | None:
        """Return the project capture_manifest.json dict."""
        return self._manifest

    @property
    def adc_inspect(self) -> dict | None:
        """Return adc_inspect.json content, or None."""
        path = self._root / 'captures' / self._capture_id / 'metadata' / 'adc_inspect.json'
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, IOError):
                return None
        return None


    def open_controlled_viewer(
        self,
        clim_mode: str = "fixed_global",
        display_dynamic_range_db: float = 40.0,
    ) -> Any:
        """Launch the MATLAB viewer via COM Automation for Python control.
        
        Returns a ControlledViewer context manager.
        """
        import awr2944_dca.viewer_ctrl as viewer_ctrl

        return viewer_ctrl.open_controlled_viewer(
            self,
            clim_mode=clim_mode,
            display_dynamic_range_db=display_dynamic_range_db,
        )

    @property
    def viewer_data(self) -> Any:
        """Read-only accessor for the existing viewer payload."""
        import awr2944_dca.viewer_ctrl as viewer_ctrl
        return viewer_ctrl.ViewerData(self._root / 'captures' / self._capture_id)

    def __repr__(self) -> str:

        status = self._manifest.get('status', '?') if self._manifest else 'not_found'
        name = self._manifest.get('capture_name', '') if self._manifest else ''
        return f"RadarCapture('{self._capture_id}', name='{name}', status='{status}')"

    def _repr_html_(self) -> str:
        if self._manifest is None:
            return f'<p>Capture <code>{self._capture_id}</code> not found.</p>'
        m = self._manifest
        status = m.get('status', 'unknown')
        status_color = {'complete': '#7fdbca', 'created': '#888', 'imported': '#ffa07a', 'inspected': '#ffd700', 'bound': '#87ceeb', 'error': '#ff6b6b', 'verify_failed': '#ff6b6b', 'bind_failed': '#ff6b6b'}.get(status, '#ccc')
        tags_html = ''
        tags = m.get('tags', [])
        if tags:
            tags_html = ' · '.join((f"<span style='background: #2d2d44; padding: 2px 6px; border-radius: 3px; font-size: 0.85em;'>{t}</span>" for t in tags))
        return f"<div style='font-family: monospace; padding: 8px; border: 1px solid #444; border-radius: 4px; background: #1a1a2e; color: #e0e0e0;'><h3 style='margin: 0 0 8px;'>📡 {m.get('capture_name', self._capture_id)}</h3><p>ID: <code>{self._capture_id}</code> · Status: <span style='color: {status_color};'>{status}</span> · Created: {m.get('created_at', '')}</p>{(f'<p>Tags: {tags_html}</p>' if tags_html else '')}</div>"

class EthernetManager:
    """Notebook-facing Ethernet pairing manager.

    Access via ``lab.eth``.
    """

    def __init__(self, project: RadarProject):
        self._project = project
        self._root = project._root

    def snapshot(self) -> 'Any':
        """Take a network adapter snapshot."""
        from awr2944_dca import eth as eth_mod
        return eth_mod.take_snapshot()

    def begin_pairing(self, snapshot_fn=None) -> 'Any':
        """Step 1: Take a 'before' snapshot (before plugging in DCA).

        Returns a PairingSession to pass to finish_pairing().
        """
        from awr2944_dca import eth as eth_mod
        return eth_mod.begin_pairing(snapshot_fn=snapshot_fn)

    def finish_pairing(self, session, *, force: bool=False, apply: bool=False, snapshot_fn=None, apply_fn=None) -> dict:
        """Step 2: Take 'after' snapshot and detect DCA adapter.

        Args:
            session: PairingSession from begin_pairing().
            force: Allow gateway adapters.
            apply: If True, configure the adapter now (default: dry-run).
        """
        from awr2944_dca import eth as eth_mod
        from awr2944_dca.project import get_dca_profile
        profile = get_dca_profile(self._root)
        return eth_mod.finish_pairing(session, self._root, host_ip=profile['host_ip'], prefix_length=profile['prefix_length'], force=force, apply=apply, snapshot_fn=snapshot_fn, apply_fn=apply_fn)

    def pair(self, *, force: bool=False, apply: bool=False) -> dict:
        """Convenience: interactive pairing (begin + prompt + finish)."""
        from awr2944_dca import eth as eth_mod
        session = self.begin_pairing()
        input('🔌 Unplug all non-essential Ethernet cables, then plug in the DCA1000 Ethernet cable.\nPress Enter when ready...')
        return self.finish_pairing(session, force=force, apply=apply)

    def status(self) -> dict:
        """Check current Ethernet pairing status."""
        from awr2944_dca import eth as eth_mod
        pairing = eth_mod.load_pairing(self._root)
        if pairing is None:
            return {'paired': False, 'message': 'No Ethernet pairing configured.'}
        from awr2944_dca.project import get_dca_profile
        profile = get_dca_profile(self._root)
        adapter_status = eth_mod.check_adapter_status(pairing, host_ip=profile['host_ip'])
        return {'paired': True, 'interface_alias': pairing.interface_alias, 'interface_index': pairing.interface_index, 'host_adapter_mac': pairing.host_adapter_mac, 'paired_at': pairing.paired_at, **adapter_status}

    def ensure_ready(self) -> dict:
        """Validate saved pairing. Raises if not ready."""
        st = self.status()
        if not st.get('paired'):
            raise ValueError('No Ethernet pairing configured. Run lab.eth.pair() first.')
        if not st.get('ready'):
            raise ValueError(f"DCA Ethernet not ready: adapter '{st.get('interface_alias')}' status={st.get('status')}, has_correct_ip={st.get('has_correct_ip')}. Run lab.eth.repair() to fix.")
        return st

    def configure(self, dry_run: bool=True) -> dict:
        """Apply saved pairing configuration.

        Args:
            dry_run: If True (default), return commands without executing.
        """
        from awr2944_dca import eth as eth_mod
        pairing = eth_mod.load_pairing(self._root)
        if pairing is None:
            raise ValueError('No Ethernet pairing configured.')
        adapter = eth_mod.AdapterInfo(interface_alias=pairing.interface_alias, interface_index=pairing.interface_index, status='Up', link_speed='', mac_address=pairing.host_adapter_mac)
        from awr2944_dca.project import get_dca_profile
        profile = get_dca_profile(self._root)
        commands = eth_mod.build_configure_commands(adapter, host_ip=profile['host_ip'], prefix_length=profile['prefix_length'])
        result = {'commands': commands, 'applied': False}
        if not dry_run:
            apply_results = eth_mod.apply_configuration(commands)
            result['applied'] = True
            result['apply_results'] = apply_results
        return result

    def repair(self) -> dict:
        """Re-apply saved pairing config (dry_run=False)."""
        return self.configure(dry_run=False)

    def unpair(self) -> None:
        """Remove Ethernet pairing from this project."""
        from awr2944_dca import eth as eth_mod
        eth_mod.remove_pairing(self._root)

    def restore_last_snapshot(self) -> dict:
        """Restore pre-pairing IP config (not yet implemented)."""
        raise NotImplementedError('restore_last_snapshot requires the pre-pairing snapshot to be replayed. This feature will be added in a future phase.')

class CaptureApi:
    """Production capture API delegating to capture_session."""

    def __init__(self, project: 'RadarProject'):
        self._project = project
        self._root = project.root

    def _resolve_profile(self, profile: str):
        if profile != 'smoke_v1':
            raise ValueError(f"Profile '{profile}' not found or unsupported.")
        from awr2944_dca.dsp.config import RadarProfile
        prof = RadarProfile.from_smoke_v1()
        return prof

    def _load_toolchain(self) -> 'dict | None':
        """Load toolchain.local.json from the project's headless directory.
        
        This is a neutral JSON loader from headless.py — it does NOT import
        mmws, Lua, RSTD, or any GUI automation code.
        Returns None if toolchain.local.json does not exist.
        """
        from awr2944_dca.headless import load_toolchain
        candidates = [self._root / 'ti' / 'headless' / 'toolchain.local.json']
        for tc_path in candidates:
            if tc_path.exists():
                try:
                    return load_toolchain(tc_path)
                except FileNotFoundError:
                    continue
        return None

    def _build_dca_cli(self, toolchain: dict):
        """Construct a DcaCli from the toolchain dict, resolving cf.json path."""
        from awr2944_dca.dca_cli import DcaCli
        from pathlib import Path
        cf_json_path = Path(toolchain.get('dca_cli_cf_json', ''))
        if not cf_json_path.exists():
            cf_json_path = self._root / 'tools' / 'dca1000' / 'cf.json'
        if not cf_json_path.exists():
            cf_json_path = Path('C:\\ti\\cf.json')
        return DcaCli.from_toolchain(toolchain, cf_json_path)

    def run(self, profile: str, frames: int, guard_frames: int=1, com_port: str='COM8', host_ip: str='192.168.33.30', dca_ip: str='192.168.33.180', name: str='dca_capture'):
        """Execute a full production capture."""
        from awr2944_dca.capture_session import run_capture
        from awr2944_dca.project import new_capture
        from awr2944_dca.sdk_cli_profile import build_smoke_v1_cli
        import dataclasses
        prof = self._resolve_profile(profile)
        prof = dataclasses.replace(prof, frame_count=frames)
        sdk_cli_commands = build_smoke_v1_cli(frames, prof.chirps_per_frame)
        toolchain = self._load_toolchain()
        dca_cli = None
        dca_configuration = {}
        if toolchain and toolchain.get('dca_cli_control_exe'):
            dca_cli = self._build_dca_cli(toolchain)
            dca_cli.dry_run = False
            try:
                dca_configuration = dca_cli.render_config()
            except Exception:
                pass
        manifest = new_capture(self._root, name)
        output_dir = self._root / 'captures' / manifest['capture_id']
        return run_capture(output_dir=output_dir, sdk_cli_commands=sdk_cli_commands, profile=prof, guard_frames=guard_frames, com_port=com_port, host_ip=host_ip, dca_ip=dca_ip, dca_cli=dca_cli, dca_configuration=dca_configuration)

    def run_smoke(self, name: str='dca_capture', **kwargs):
        """Convenience wrapper for the smoke_v1 profile."""
        return self.run(profile='smoke_v1', name=name, **kwargs)

    def dry_run(self, profile: str, frames: int, guard_frames: int=1, com_port: str='COM8', host_ip: str='192.168.33.30', dca_ip: str='192.168.33.180') -> dict:
        """Calculate and report capture plan without touching hardware."""
        import dataclasses
        from pathlib import Path
        from awr2944_dca.sdk_cli_profile import build_smoke_v1_cli
        prof = self._resolve_profile(profile)
        prof = dataclasses.replace(prof, frame_count=frames)
        sdk_cli_commands = build_smoke_v1_cli(frames, prof.chirps_per_frame)
        total_frames = prof.frame_count
        canonical_frames = total_frames - guard_frames
        from awr2944_dca.awr2944_adc import expected_raw_dca_bytes, active_payload_bytes, AWR2944AdcLayout
        native_bytes_per_frame = expected_raw_dca_bytes(1, prof.chirps_per_frame, prof.rx_count, prof.adc_samples, AWR2944AdcLayout())
        logical_bytes_per_frame = active_payload_bytes(1, prof.chirps_per_frame, prof.rx_count, prof.adc_samples)
        native_bytes = native_bytes_per_frame * total_frames
        canonical_bytes = native_bytes_per_frame * canonical_frames
        cube_shape = [canonical_frames, prof.chirps_per_frame, prof.rx_count, prof.adc_samples]
        toolchain = self._load_toolchain()
        dca_control_exe = 'NOT_CONFIGURED'
        dca_config_source = 'NOT_CONFIGURED'
        dca_config_runtime_path = 'NOT_CONFIGURED'
        if toolchain:
            dca_control_exe = toolchain.get('dca_cli_control_exe', 'NOT_CONFIGURED')
            dca_config_source = toolchain.get('dca_cli_cf_json', 'NOT_CONFIGURED')
            cf_json_path = Path(toolchain.get('dca_cli_cf_json', ''))
            if not cf_json_path.exists():
                cf_json_path = self._root / 'tools' / 'dca1000' / 'cf.json'
            if not cf_json_path.exists():
                cf_json_path = Path('C:\\ti\\cf.json')
            dca_config_runtime_path = str(cf_json_path)
        return {'project_root': str(self._root), 'profile': profile, 'total_frames': total_frames, 'guard_frames': guard_frames, 'canonical_frames': canonical_frames, 'expected_native_dca_bytes': native_bytes, 'expected_canonical_dca_bytes': canonical_bytes, 'logical_depacked_bytes': logical_bytes_per_frame * total_frames, 'canonical_logical_bytes': logical_bytes_per_frame * canonical_frames, 'canonical_cube': cube_shape, 'physical_lvds_lanes': AWR2944AdcLayout().physical_lvds_lanes, 'dca_word_slots': AWR2944AdcLayout().dca_word_slots, 'dca_storage_expansion_factor': AWR2944AdcLayout().dca_word_slots // AWR2944AdcLayout().physical_lvds_lanes, 'uart_settings': {'com_port': com_port, 'baud': 115200}, 'network_settings': {'host_ip': host_ip, 'dca_ip': dca_ip}, 'dca_control_executable': dca_control_exe, 'dca_config_source': dca_config_source, 'dca_config_runtime_path': dca_config_runtime_path, 'prospective_output_paths': {'native': 'adc_data.bin', 'canonical': 'adc_data_canonical.bin', 'manifest': 'manifest.json'}, 'configuration_transport': 'sdk_demo_uart_cli', 'dca_full_initialization': True, 'sdk_cli_commands': sdk_cli_commands, 'hardware_touched': False}