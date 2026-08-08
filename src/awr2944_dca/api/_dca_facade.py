"""DcaFacade — read-only DCA1000 status, configuration, and aliveness check.

Access via ``p.dca``.

*All* methods read exclusively from ``ProjectConfig`` (modern TOML-based
configuration).  ``toolchain.local.json`` is never consulted here.

.. rubric:: Method summary

``status()``
    Purely local read-only readiness check.  No subprocess.  No network.

``config()``
    Show the canonical ProjectConfig network/path values alongside the
    corresponding cf.json values for consistency comparison.  No subprocess.

``verify()``
    Delegate to existing :meth:`~awr2944_dca.dca_cli.DcaCli.query_sys_status`.
    Requires both ``control_exe`` and ``cf_json`` to be configured and found;
    returns a structured failure otherwise without launching a subprocess.

``fpga_version()``
    Delegate to existing :meth:`~awr2944_dca.dca_cli.DcaCli.fpga_version`.
    Same prerequisites as ``verify()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from awr2944_dca.lab import RadarProject
    from awr2944_dca._config import ProjectConfig
    from awr2944_dca.dca_cli import DcaCli


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DcaStatusResult:
    """Read-only local readiness check — no subprocess, no network.

    Returned by :meth:`DcaFacade.status`.
    """
    control_exe:        str
    control_exe_found:  bool
    record_exe:         str
    record_exe_found:   bool
    rf_api_dll:         str
    rf_api_dll_found:   bool
    cf_json_path:       str
    cf_json_found:      bool
    cf_json_consistent: Optional[bool]  # None = not found; bool = validation outcome
    host_ip:            str
    dca_ip:             str
    config_port:        int
    data_port:          int
    toolchain_ready:    bool            # all four files found
    warnings:           tuple = field(default_factory=tuple)

    @property
    def success(self) -> bool:
        return self.toolchain_ready

    def __repr__(self) -> str:
        ready = "ready" if self.toolchain_ready else "NOT READY"
        return (
            f"DcaStatusResult({ready}, "
            f"control={self.control_exe_found}, cf_json={self.cf_json_found}, "
            f"host={self.host_ip!r}, dca={self.dca_ip!r})"
        )

    def print(self) -> None:
        status = "✅ Ready" if self.toolchain_ready else "❌ Not Ready"
        print(f"DCA Status: {status}")
        print(f"  Control exe : {'✓' if self.control_exe_found else '✗'} {self.control_exe or 'NOT_CONFIGURED'}")
        print(f"  Record exe  : {'✓' if self.record_exe_found else '✗'} {self.record_exe or 'NOT_CONFIGURED'}")
        print(f"  RF API DLL  : {'✓' if self.rf_api_dll_found else '✗'} {self.rf_api_dll or 'NOT_CONFIGURED'}")
        print(f"  cf.json     : {'✓' if self.cf_json_found else '✗'} {self.cf_json_path or 'NOT_CONFIGURED'}")
        cf_c = {True: "consistent", False: "MISMATCH", None: "not checked"}[self.cf_json_consistent]
        print(f"  cf.json vs TOML : {cf_c}")
        print(f"  Network : host={self.host_ip or '?'}, dca={self.dca_ip}, "
              f"cmd={self.config_port}, data={self.data_port}")
        for w in self.warnings:
            print(f"  ⚠ {w}")

    def _repr_html_(self) -> str:
        status_color = "#7fdbca" if self.toolchain_ready else "#ff6b6b"
        return (
            "<div style='font-family:monospace;padding:8px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            "<h3 style='margin:0 0 8px'>🔧 DCA Status</h3>"
            f"<p style='color:{status_color}'>{'✅ Ready' if self.toolchain_ready else '❌ Not Ready'}</p>"
            f"<p>Control: {'✓' if self.control_exe_found else '✗'} "
            f"<code>{self.control_exe or 'NOT_CONFIGURED'}</code></p>"
            f"<p>cf.json: {'✓' if self.cf_json_found else '✗'} "
            f"<code>{self.cf_json_path or 'NOT_CONFIGURED'}</code></p>"
            f"<p>Network: host={self.host_ip or '?'}, dca={self.dca_ip}, "
            f"cmd={self.config_port}, data={self.data_port}</p>"
            "</div>"
        )


@dataclass
class DcaConfigResult:
    """DCA configuration — canonical ProjectConfig values vs cf.json values.

    ProjectConfig is the source of truth.  cf.json is an external TI file
    that should agree with it.

    Returned by :meth:`DcaFacade.config`.
    """
    # Canonical (source of truth from ProjectConfig)
    project_host_ip:      str
    project_dca_ip:       str
    project_config_port:  int
    project_data_port:    int
    project_cf_json_path: str
    # cf.json observed values
    cf_json_found:        bool
    cf_json_parseable:    bool
    cf_json_host_ip:      Optional[str]
    cf_json_dca_ip:       Optional[str]
    cf_json_config_port:  Optional[int]
    cf_json_data_port:    Optional[int]
    # Per-field consistency: "consistent" | "mismatch" | "cf_json_missing" | "cf_json_empty"
    host_ip_consistency:     str
    dca_ip_consistency:      str
    config_port_consistency: str
    data_port_consistency:   str

    def __repr__(self) -> str:
        return (
            f"DcaConfigResult(cf_json_found={self.cf_json_found}, "
            f"host_ip={self.project_host_ip!r}, dca_ip={self.project_dca_ip!r})"
        )

    def print(self) -> None:
        print("DCA Configuration (ProjectConfig is canonical)")
        print(f"  {'Field':<22} {'Project':<22} {'cf.json':<22} Consistency")
        print("  " + "-" * 78)

        def row(label: str, proj_val: object, cf_val: object, cons: str) -> None:
            icon = {"consistent": "✓", "mismatch": "⚠"}.get(cons, "—")
            print(f"  {label:<22} {str(proj_val):<22} {str(cf_val or '?'):<22} {icon} {cons}")

        row("host_ip",     self.project_host_ip,    self.cf_json_host_ip,    self.host_ip_consistency)
        row("dca_ip",      self.project_dca_ip,     self.cf_json_dca_ip,     self.dca_ip_consistency)
        row("config_port", self.project_config_port, self.cf_json_config_port, self.config_port_consistency)
        row("data_port",   self.project_data_port,  self.cf_json_data_port,  self.data_port_consistency)
        print(f"  cf.json : {'found' if self.cf_json_found else 'not found'}, "
              f"{'parseable' if self.cf_json_parseable else 'not parseable'}")

    def _repr_html_(self) -> str:
        def cons_color(c: str) -> str:
            return {"consistent": "#7fdbca", "mismatch": "#ffa07a"}.get(c, "#888")

        def row_html(label: str, proj: object, cf: object, cons: str) -> str:
            return (
                f"<tr><td>{label}</td><td><code>{proj}</code></td>"
                f"<td><code>{cf or '?'}</code></td>"
                f"<td style='color:{cons_color(cons)}'>{cons}</td></tr>"
            )

        return (
            "<div style='font-family:monospace;padding:8px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            "<h3 style='margin:0 0 8px'>⚙️ DCA Configuration</h3>"
            "<table><thead><tr><th>Field</th><th>Project (canon)</th>"
            "<th>cf.json</th><th>Consistency</th></tr></thead><tbody>"
            + row_html("host_ip",     self.project_host_ip,    self.cf_json_host_ip,    self.host_ip_consistency)
            + row_html("dca_ip",      self.project_dca_ip,     self.cf_json_dca_ip,     self.dca_ip_consistency)
            + row_html("config_port", self.project_config_port, self.cf_json_config_port, self.config_port_consistency)
            + row_html("data_port",   self.project_data_port,  self.cf_json_data_port,  self.data_port_consistency)
            + f"</tbody></table>"
            f"<p>cf.json: {'found' if self.cf_json_found else 'not found'}, "
            f"{'parseable' if self.cf_json_parseable else 'not parseable'}</p>"
            "</div>"
        )


@dataclass
class DcaVerifyResult:
    """Result of DCA1000 aliveness check via ``query_sys_status``.

    Returned by :meth:`DcaFacade.verify`.
    """
    control_exe_found: bool
    cf_json_found:     bool
    sys_status_ok:     bool
    sys_status_detail: str
    warnings:          tuple = field(default_factory=tuple)

    @property
    def success(self) -> bool:
        return self.control_exe_found and self.cf_json_found and self.sys_status_ok

    def __repr__(self) -> str:
        return (
            f"DcaVerifyResult(success={self.success}, "
            f"control_exe={self.control_exe_found}, cf_json={self.cf_json_found}, "
            f"sys_status_ok={self.sys_status_ok})"
        )

    def print(self) -> None:
        print(f"DCA Verify: {'✅ OK' if self.success else '❌ FAIL'}")
        print(f"  control_exe found : {self.control_exe_found}")
        print(f"  cf.json found     : {self.cf_json_found}")
        print(f"  sys_status_ok     : {self.sys_status_ok}")
        print(f"  detail            : {self.sys_status_detail or '—'}")

    def _repr_html_(self) -> str:
        color = "#7fdbca" if self.success else "#ff6b6b"
        return (
            "<div style='font-family:monospace;padding:8px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            "<h3 style='margin:0 0 8px'>🔍 DCA Verify</h3>"
            f"<p style='color:{color}'>{'✅ OK' if self.success else '❌ FAIL'}</p>"
            f"<p>detail: {self.sys_status_detail or '—'}</p>"
            "</div>"
        )


@dataclass
class DcaFpgaVersionResult:
    """Result of FPGA firmware version query.

    Returned by :meth:`DcaFacade.fpga_version`.
    """
    success:           bool
    version_string:    str
    raw_stdout:        str
    control_exe_found: bool = True
    cf_json_found:     bool = True

    def __repr__(self) -> str:
        return (
            f"DcaFpgaVersionResult(success={self.success}, "
            f"version={self.version_string!r})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dca_cli_from_config(cfg: "ProjectConfig") -> "DcaCli":
    """Construct a :class:`~awr2944_dca.dca_cli.DcaCli` from ProjectConfig.

    Reads only modern TOML fields — no ``toolchain.local.json``.
    """
    from awr2944_dca.dca_cli import DcaCli
    return DcaCli(
        control_exe=Path(cfg.local.dca_control_exe),
        record_exe=Path(cfg.local.dca_record_exe)  if cfg.local.dca_record_exe  else Path(""),
        rf_api_dll=Path(cfg.local.rf_api_dll)      if cfg.local.rf_api_dll      else Path(""),
        cf_json_path=Path(cfg.local.cf_json_path),
    )


def _check_prerequisites(cfg: "ProjectConfig") -> tuple[bool, bool]:
    """Return (control_exe_found, cf_json_found) from ProjectConfig paths."""
    c_path = Path(cfg.local.dca_control_exe) if cfg.local.dca_control_exe else None
    j_path = Path(cfg.local.cf_json_path)    if cfg.local.cf_json_path    else None
    return bool(c_path and c_path.exists()), bool(j_path and j_path.exists())


# ---------------------------------------------------------------------------
# DcaFacade
# ---------------------------------------------------------------------------

class DcaFacade:
    """DCA1000 status, configuration, and aliveness check facade.

    Access via ``p.dca``.  Reads exclusively from ``ProjectConfig``
    (modern TOML-based configuration).  ``toolchain.local.json`` is never
    consulted.

    Works correctly on a fresh unconfigured project — returns structured
    failure information rather than raising.
    """

    def __init__(self, project: "RadarProject"):
        self._project = project

    @property
    def _cfg(self) -> "ProjectConfig":
        return self._project._config

    # ------------------------------------------------------------------
    # status() — no subprocess, no network
    # ------------------------------------------------------------------

    def status(self) -> DcaStatusResult:
        """Read-only local readiness check.  No subprocess.  No network.

        Reads ``ProjectConfig`` only.  Returns useful structured information
        even on a fresh unconfigured project (all paths blank).
        """
        cfg = self._cfg

        def _found(path_str: str) -> bool:
            return bool(path_str and Path(path_str).exists())

        c_found   = _found(cfg.local.dca_control_exe)
        r_found   = _found(cfg.local.dca_record_exe)
        dll_found = _found(cfg.local.rf_api_dll)
        cf_found  = _found(cfg.local.cf_json_path)

        cf_consistent: Optional[bool] = None
        if cf_found:
            try:
                cfg.validate_cf_json()
                cf_consistent = True
            except Exception:
                cf_consistent = False

        warnings = []
        if not c_found and cfg.local.dca_control_exe:
            warnings.append(f"Control exe configured but not found: {cfg.local.dca_control_exe}")
        if not r_found and cfg.local.dca_record_exe:
            warnings.append(f"Record exe configured but not found: {cfg.local.dca_record_exe}")
        if not cf_found and cfg.local.cf_json_path:
            warnings.append(f"cf.json configured but not found: {cfg.local.cf_json_path}")
        if cf_consistent is False:
            warnings.append("cf.json settings are inconsistent with ProjectConfig")

        return DcaStatusResult(
            control_exe=cfg.local.dca_control_exe or "",
            control_exe_found=c_found,
            record_exe=cfg.local.dca_record_exe or "",
            record_exe_found=r_found,
            rf_api_dll=cfg.local.rf_api_dll or "",
            rf_api_dll_found=dll_found,
            cf_json_path=cfg.local.cf_json_path or "",
            cf_json_found=cf_found,
            cf_json_consistent=cf_consistent,
            host_ip=cfg.local.host_ip or "",
            dca_ip=cfg.portable.dca_ip,
            config_port=cfg.portable.config_port,
            data_port=cfg.portable.data_port,
            toolchain_ready=c_found and r_found and cf_found,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    # config() — no subprocess, no network
    # ------------------------------------------------------------------

    def config(self) -> DcaConfigResult:
        """Show canonical ProjectConfig values vs cf.json values.  No subprocess.

        ``ProjectConfig`` is the source of truth.  ``cf.json`` is presented
        alongside it for consistency verification.

        Works on a fresh project — cf_json_found=False, all consistency states
        ``"cf_json_missing"``.
        """
        cfg = self._cfg
        cf_path_str = cfg.local.cf_json_path or ""
        cf_path     = Path(cf_path_str) if cf_path_str else None
        cf_found    = bool(cf_path and cf_path.exists())

        cf_parseable = False
        cf_host_ip = cf_dca_ip = None
        cf_config_port = cf_data_port = None

        if cf_found:
            try:
                import json as _json
                cf_data     = _json.loads(cf_path.read_text(encoding="utf-8"))
                cf_parseable = True
                dca_cfg      = cf_data.get("DCA1000Config", {})
                net_update   = dca_cfg.get("ethernetConfigUpdate", {})
                net_base     = dca_cfg.get("ethernetConfig", {})
                cf_host_ip     = net_update.get("systemIPAddress") or None
                cf_dca_ip      = (net_update.get("DCA1000IPAddress")
                                  or net_base.get("DCA1000IPAddress")) or None
                cf_config_port = (net_update.get("DCA1000ConfigPort")
                                  or net_base.get("DCA1000ConfigPort")) or None
                cf_data_port   = (net_update.get("DCA1000DataPort")
                                  or net_base.get("DCA1000DataPort")) or None
            except Exception:
                pass

        def _consistency(proj_val: object, cf_val: object) -> str:
            if not cf_found or not cf_parseable:
                return "cf_json_missing"
            if cf_val is None:
                return "cf_json_empty"
            return "consistent" if str(proj_val) == str(cf_val) else "mismatch"

        return DcaConfigResult(
            project_host_ip=cfg.local.host_ip or "",
            project_dca_ip=cfg.portable.dca_ip,
            project_config_port=cfg.portable.config_port,
            project_data_port=cfg.portable.data_port,
            project_cf_json_path=cf_path_str,
            cf_json_found=cf_found,
            cf_json_parseable=cf_parseable,
            cf_json_host_ip=cf_host_ip,
            cf_json_dca_ip=cf_dca_ip,
            cf_json_config_port=cf_config_port,
            cf_json_data_port=cf_data_port,
            host_ip_consistency=_consistency(cfg.local.host_ip or "", cf_host_ip),
            dca_ip_consistency=_consistency(cfg.portable.dca_ip, cf_dca_ip),
            config_port_consistency=_consistency(cfg.portable.config_port, cf_config_port),
            data_port_consistency=_consistency(cfg.portable.data_port, cf_data_port),
        )

    # ------------------------------------------------------------------
    # verify() — delegates to DcaCli.query_sys_status()
    # ------------------------------------------------------------------

    def verify(self) -> DcaVerifyResult:
        """Check DCA aliveness by delegating to :meth:`~awr2944_dca.dca_cli.DcaCli.query_sys_status`.

        Prerequisites: both ``control_exe`` **and** ``cf_json`` must be
        configured and found (``query_sys_status`` passes ``cf_json`` as an
        argument to the subprocess).  Returns a structured failure if either
        prerequisite is missing — no subprocess is launched in that case.
        """
        cfg = self._cfg
        c_found, cf_found = _check_prerequisites(cfg)

        if not c_found:
            reason = (
                "DCA control executable not configured"
                if not cfg.local.dca_control_exe
                else f"DCA control executable not found: {cfg.local.dca_control_exe}"
            )
            return DcaVerifyResult(
                control_exe_found=False,
                cf_json_found=cf_found,
                sys_status_ok=False,
                sys_status_detail=reason,
            )

        if not cf_found:
            reason = (
                "cf.json not configured"
                if not cfg.local.cf_json_path
                else f"cf.json not found: {cfg.local.cf_json_path}"
            )
            return DcaVerifyResult(
                control_exe_found=True,
                cf_json_found=False,
                sys_status_ok=False,
                sys_status_detail=reason,
            )

        # Both prerequisites met — delegate to DcaCli.
        dca_cli = _dca_cli_from_config(cfg)
        try:
            result = dca_cli.query_sys_status()
            return DcaVerifyResult(
                control_exe_found=True,
                cf_json_found=True,
                sys_status_ok=result.success,
                sys_status_detail=result.stdout.strip(),
            )
        except Exception as exc:
            return DcaVerifyResult(
                control_exe_found=True,
                cf_json_found=True,
                sys_status_ok=False,
                sys_status_detail=f"query_sys_status raised: {exc}",
            )

    # ------------------------------------------------------------------
    # fpga_version() — delegates to DcaCli.fpga_version()
    # ------------------------------------------------------------------

    def fpga_version(self) -> DcaFpgaVersionResult:
        """Read FPGA firmware version by delegating to :meth:`~awr2944_dca.dca_cli.DcaCli.fpga_version`.

        Same prerequisites as :meth:`verify` — both ``control_exe`` and
        ``cf_json`` must exist.  Returns a structured failure otherwise.
        """
        cfg = self._cfg
        c_found, cf_found = _check_prerequisites(cfg)

        if not c_found:
            return DcaFpgaVersionResult(
                success=False,
                version_string="",
                raw_stdout="",
                control_exe_found=False,
                cf_json_found=cf_found,
            )

        if not cf_found:
            return DcaFpgaVersionResult(
                success=False,
                version_string="",
                raw_stdout="",
                control_exe_found=True,
                cf_json_found=False,
            )

        dca_cli = _dca_cli_from_config(cfg)
        try:
            result = dca_cli.fpga_version()
            return DcaFpgaVersionResult(
                success=result.success,
                version_string=result.stdout.strip(),
                raw_stdout=result.stdout,
                control_exe_found=True,
                cf_json_found=True,
            )
        except Exception as exc:
            return DcaFpgaVersionResult(
                success=False,
                version_string="",
                raw_stdout=str(exc),
                control_exe_found=True,
                cf_json_found=True,
            )
