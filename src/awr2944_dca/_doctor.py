"""Hardware Doctor for AWR2944 + DCA1000 project health and diagnostics.

Never modifies hardware state.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any

from rich.console import Console

if TYPE_CHECKING:
    from awr2944_dca.lab import RadarProject


logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    status: str          # "PASS", "WARN", "FAIL", "SKIP"
    category: str        # "OFFLINE", "DIAGNOSTIC_HARDWARE_ACCESS", "MUTATING_HARDWARE_ACCESS"
    detail: str
    required_for_capture: bool = True
    suggestion: str = ""


@dataclass
class DiscoveryReport:
    serial_ports: list[Any]
    com_ports: list[Any]
    network_adapters: list[dict]
    timestamp: str

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def print(self, filter: str | None = None) -> None:  # noqa: A002
        """Print a formatted table to the terminal using Rich.

        Args:
            filter: Optional subset to print: ``"serial"`` / ``"com"`` for
                    COM-port tables only, ``"network"`` for network only,
                    ``None`` (default) for everything.
        """
        from rich.console import Console
        from rich.table import Table
        from rich import box as rbox

        console = Console()
        f = (filter or "").lower()
        show_serial = f in ("", "serial", "com")
        show_network = f in ("", "network")

        console.print(
            f"\n[bold cyan]Hardware Discovery Report[/bold cyan]  "
            f"[dim]{self.timestamp}[/dim]"
        )

        if show_serial:
            # ---- Serial / COM ports ----------------------------------------
            t = Table(
                title="Serial / COM Ports",
                box=rbox.SIMPLE_HEAVY,
                show_lines=False,
                title_style="bold yellow",
            )
            t.add_column("Port", style="bold white", no_wrap=True)
            t.add_column("Friendly Name", style="cyan")
            t.add_column("VID:PID", style="magenta")
            t.add_column("XDS110", justify="center")
            t.add_column("Role", style="green")
            t.add_column("Conf", justify="center")

            for p in self.com_ports:
                # com_ports are PortInfo (from hardware.ports.scan_ports)
                vid_pid = ""
                if hasattr(p, "hwid") and p.hwid:
                    import re as _re
                    m = _re.search(r"VID:PID=([0-9A-Fa-f:]+)", p.hwid)
                    vid_pid = m.group(1) if m else ""
                # XDS110 flag comes from serial_ports (SerialPortInfo)
                xds_flag = ""
                for sp in self.serial_ports:
                    if hasattr(sp, "port") and sp.port == p.com:
                        xds_flag = ":white_check_mark:" if sp.is_xds110 else ""
                        break
                t.add_row(
                    p.com,
                    getattr(p, "friendly_name", ""),
                    vid_pid,
                    xds_flag,
                    getattr(p, "likely_role", ""),
                    getattr(p, "confidence", ""),
                )

            if self.com_ports:
                console.print(t)
            else:
                console.print("[dim]No serial / COM ports found.[/dim]")

            # ---- XDS110 detail (from PnP discovery) ------------------------
            xds_ports = [p for p in self.serial_ports if p.is_xds110]
            if xds_ports:
                xt = Table(
                    title="XDS110 Ports (PnP)",
                    box=rbox.SIMPLE_HEAVY,
                    show_lines=False,
                    title_style="bold yellow",
                )
                xt.add_column("Port", style="bold white", no_wrap=True)
                xt.add_column("Friendly Name", style="cyan")
                xt.add_column("VID", style="magenta")
                xt.add_column("PID", style="magenta")
                xt.add_column("Role", style="green")
                xt.add_column("Status")
                for xp in xds_ports:
                    xt.add_row(
                        xp.port, xp.name,
                        getattr(xp, "vid", ""),
                        getattr(xp, "pid", ""),
                        getattr(xp, "role", ""),
                        getattr(xp, "status", ""),
                    )
                console.print(xt)

        if show_network:
            # ---- Network adapters ------------------------------------------
            nt = Table(
                title="Network Adapters",
                box=rbox.SIMPLE_HEAVY,
                show_lines=False,
                title_style="bold yellow",
            )
            nt.add_column("Alias", style="bold white", no_wrap=True)
            nt.add_column("Description", style="cyan")
            nt.add_column("IPv4 Address", style="green")
            nt.add_column("/Pfx", justify="right")
            nt.add_column("Link", justify="center")
            nt.add_column("GW?", justify="center")
            nt.add_column("MAC", style="dim")

            for adapter in self.network_adapters:
                family_raw = adapter.get("AddressFamily", "")
                if isinstance(family_raw, int):
                    family_str = "IPv4" if family_raw == 2 else "IPv6"
                else:
                    family_str = str(family_raw)
                # Only show IPv4 rows (AddressFamily == 2) in the primary display
                if family_str == "IPv6":
                    continue
                link_raw = adapter.get("_Status", "")
                if link_raw == "Up":
                    link_str = "[green]Up[/green]"
                elif link_raw == "Disconnected":
                    link_str = "[red]Down[/red]"
                elif link_raw:
                    link_str = f"[yellow]{link_raw}[/yellow]"
                else:
                    link_str = ""
                gw_str = "[bold green]YES[/bold green]" if adapter.get("_has_gateway") else ""
                nt.add_row(
                    adapter.get("InterfaceAlias", ""),
                    adapter.get("_Description", ""),
                    adapter.get("IPAddress", ""),
                    str(adapter.get("PrefixLength", "")),
                    link_str,
                    gw_str,
                    adapter.get("_MacAddress", ""),
                )

            if self.network_adapters:
                console.print(nt)
            else:
                console.print("[dim]No network adapters found.[/dim]")

    def __repr__(self) -> str:  # noqa: D105
        n_serial = len(self.serial_ports)
        n_com = len(self.com_ports)
        n_net = len(self.network_adapters)
        xds = sum(1 for p in self.serial_ports if p.is_xds110)
        return (
            f"DiscoveryReport("
            f"serial={n_serial} [{xds} XDS110], "
            f"com={n_com}, "
            f"network={n_net}, "
            f"ts={self.timestamp!r})"
        )

    def _repr_html_(self) -> str:
        """Jupyter notebook HTML representation."""
        rows_com = ""
        for p in self.com_ports:
            import re as _re
            vid_pid = ""
            if hasattr(p, "hwid") and p.hwid:
                m = _re.search(r"VID:PID=([0-9A-Fa-f:]+)", p.hwid)
                vid_pid = m.group(1) if m else ""
            xds_mark = ""
            for sp in self.serial_ports:
                if hasattr(sp, "port") and sp.port == p.com:
                    xds_mark = "&#10003;" if sp.is_xds110 else ""
                    break
            rows_com += (
                f"<tr><td><b>{p.com}</b></td>"
                f"<td>{getattr(p,'friendly_name','')}</td>"
                f"<td><code>{vid_pid}</code></td>"
                f"<td style='text-align:center'>{xds_mark}</td>"
                f"<td>{getattr(p,'likely_role','')}</td>"
                f"<td>{getattr(p,'confidence','')}</td></tr>"
            )
        if not rows_com:
            rows_com = "<tr><td colspan='6'><i>none found</i></td></tr>"

        rows_net = ""
        for adapter in self.network_adapters:
            family_raw = adapter.get("AddressFamily", "")
            if isinstance(family_raw, int):
                family_str = "IPv4" if family_raw == 2 else ("IPv6" if family_raw == 23 else str(family_raw))
            else:
                family_str = str(family_raw)
            if family_str == "IPv6":
                continue  # suppress IPv6 rows; only IPv4 shown
            link_raw = adapter.get("_Status", "")
            link_html = (
                f"<span style='color:green'>{link_raw}</span>" if link_raw == "Up"
                else (f"<span style='color:red'>{link_raw}</span>" if link_raw == "Disconnected"
                      else link_raw)
            )
            gw_html = "&#10003;" if adapter.get("_has_gateway") else ""
            rows_net += (
                f"<tr>"
                f"<td><b>{adapter.get('InterfaceAlias','')}</b></td>"
                f"<td>{adapter.get('_Description','')}</td>"
                f"<td>{adapter.get('IPAddress','')}</td>"
                f"<td>{adapter.get('PrefixLength','')}</td>"
                f"<td style='text-align:center'>{link_html}</td>"
                f"<td style='text-align:center'>{gw_html}</td>"
                f"<td><code>{adapter.get('_MacAddress','')}</code></td>"
                f"</tr>"
            )
        if not rows_net:
            rows_net = "<tr><td colspan='4'><i>none found</i></td></tr>"

        style = "border-collapse:collapse;margin:8px 0"
        th = "style='background:#333;color:#eee;padding:4px 8px;text-align:left'"
        td = "style='padding:3px 8px;border-bottom:1px solid #555'"
        return (
            f"<details open><summary><b>DiscoveryReport</b> &mdash; {self.timestamp}</summary>"
            f"<h4 style='margin:8px 0 2px'>Serial / COM Ports</h4>"
            f"<table style='{style}'>"
            f"<tr><th {th}>Port</th><th {th}>Friendly Name</th><th {th}>VID:PID</th>"
            f"<th {th}>XDS110</th><th {th}>Role</th><th {th}>Conf</th></tr>"
            f"{rows_com}</table>"
            f"<h4 style='margin:8px 0 2px'>Network Adapters (IPv4)</h4>"
            f"<table style='{style}'>"
            f"<tr><th {th}>Alias</th><th {th}>Description</th><th {th}>IPv4</th>"
            f"<th {th}>Pfx</th><th {th}>Link</th><th {th}>GW?</th><th {th}>MAC</th></tr>"
            f"{rows_net}</table></details>"
        )


@dataclass
class HardwareReport:
    checks: list[CheckResult]
    timestamp: str
    mode: str

    @property
    def success(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    @property
    def ready_for_capture(self) -> bool:
        if self.mode == "OFFLINE_ONLY":
            return False
        for c in self.checks:
            if c.required_for_capture and c.status != "PASS":
                return False
        return True

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "WARN"]

    @property
    def errors(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "FAIL"]

    def summary(self) -> str:
        s = f"HardwareReport (Mode: {self.mode}) - Success: {self.success}, Ready for capture: {self.ready_for_capture}\n"
        for c in self.checks:
            req = "*" if c.required_for_capture else " "
            s += f"[{c.status:^4}] {req} {c.name:<30} {c.detail}\n"
        return s

    def print(self) -> None:
        console = Console()
        console.print(f"\n[bold cyan]Hardware Doctor Report[/bold cyan] (Mode: {self.mode})")
        console.print(f"Timestamp: {self.timestamp}\n")
        
        for c in self.checks:
            if c.status == "PASS":
                color = "green"
            elif c.status == "WARN":
                color = "yellow"
            elif c.status == "FAIL":
                color = "red"
            else:
                color = "bright_black"
                
            req = "*" if c.required_for_capture else " "
            console.print(f"[[{color}]{c.status:^4}[/{color}]] {req} {c.name:<30} {c.detail}")
            if c.suggestion and c.status in ("FAIL", "WARN", "SKIP"):
                console.print(f"         [italic]{c.suggestion}[/italic]")
                
        console.print("\n[bold]Summary:[/bold]")
        if self.success:
            console.print("[green]✔ No errors found.[/green]")
        else:
            console.print(f"[red]✘ Found {len(self.errors)} errors.[/red]")
            
        if self.mode != "OFFLINE_ONLY":
            if self.ready_for_capture:
                console.print("[green]✔ System is READY for capture.[/green]")
            else:
                console.print("[yellow]⚠ System is NOT fully ready for capture.[/yellow]")

    def raise_for_errors(self, strict: bool = False) -> None:
        if not self.success:
            raise RuntimeError(f"Hardware Doctor failed with {len(self.errors)} errors.\n\n{self.summary()}")
        if strict and not self.ready_for_capture:
            raise RuntimeError(f"Hardware Doctor strict mode failed: Not ready for capture.\n\n{self.summary()}")


class HardwareManager:
    """Lazy accessor for hardware inspection. No mutations."""

    def __init__(self, project: RadarProject):
        self._project = project
        self._checks: Dict[str, CheckResult] = {}
        
    def _add(self, name: str, status: str, cat: str, detail: str, req: bool = True, sugg: str = "") -> CheckResult:
        res = CheckResult(name, status, cat, detail, req, sugg)
        self._checks[name] = res
        return res

    def discover(self, filter: str | None = None) -> DiscoveryReport:  # noqa: A002
        """Discover attached hardware (serial ports and network adapters).

        Returns a :class:`DiscoveryReport`.  Call ``.print()`` on the result
        for a formatted terminal view, or just let Jupyter display it via
        ``_repr_html_()``.

        Args:
            filter: Optional subset to discover / display.
                - ``None`` or omitted — all (serial + network) [default]
                - ``"serial"`` or ``"com"`` — serial/COM ports only
                - ``"network"`` — network adapters only

        The underlying discovery calls are read-only and never mutate hardware.
        """
        f = (filter or "").lower()
        if f not in ("", "serial", "com", "network"):
            raise ValueError(
                f"Unknown filter {filter!r}. "
                "Valid values: None, 'serial', 'com', 'network'."
            )

        sp: list[Any] = []
        cp: list[Any] = []
        net_adapters: list[dict] = []

        if f in ("", "serial", "com"):
            try:
                from awr2944_dca.headless_serial import discover_serial_ports
                sp = discover_serial_ports()
            except ImportError:
                pass  # powershell-based, no extra deps

            try:
                from awr2944_dca.hardware.ports import scan_ports
                cp = scan_ports()
            except ImportError:
                import warnings
                warnings.warn(
                    "pyserial is not installed; COM port scan unavailable. "
                    "Install with: pip install awr2944-dca-lab[hardware]",
                    stacklevel=2,
                )
            except RuntimeError as exc:
                import warnings
                warnings.warn(str(exc), stacklevel=2)

        if f in ("", "network"):
            try:
                from awr2944_dca.dca.preflight import _run_ps_json, _as_dicts

                # --- IP addresses (base data) ---------------------------------
                ip_script = (
                    "Get-NetIPAddress -ErrorAction SilentlyContinue "
                    "| Select-Object InterfaceAlias, IPAddress, PrefixLength, AddressFamily"
                )
                net_adapters = _as_dicts(_run_ps_json(ip_script))

                # --- Adapter metadata (description, link state, MAC) ----------
                # Read-only query; never configures anything.
                adp_script = (
                    "Get-NetAdapter -ErrorAction SilentlyContinue "
                    "| Select-Object Name, InterfaceDescription, Status, MacAddress"
                )
                adp_rows = _as_dicts(_run_ps_json(adp_script))
                # Build lookup: alias -> {description, status, mac}
                adp_by_alias: dict = {}
                for row in adp_rows:
                    alias = row.get("Name", "")
                    if alias:
                        adp_by_alias[alias] = {
                            "_Description": row.get("InterfaceDescription", ""),
                            "_Status": row.get("Status", ""),
                            "_MacAddress": row.get("MacAddress", ""),
                        }

                # --- Default-gateway flags ------------------------------------
                # Get-NetRoute -DestinationPrefix 0.0.0.0/0 lists default routes.
                # Read-only; no mutation.
                gw_script = (
                    "Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
                    "-ErrorAction SilentlyContinue "
                    "| Select-Object InterfaceAlias"
                )
                gw_rows = _as_dicts(_run_ps_json(gw_script))
                gw_aliases: set = {r.get("InterfaceAlias", "") for r in gw_rows if r.get("InterfaceAlias")}

                # Merge enrichment into each ip-address row
                for row in net_adapters:
                    alias = row.get("InterfaceAlias", "")
                    row.update(adp_by_alias.get(alias, {}))
                    row["_has_gateway"] = alias in gw_aliases

            except Exception:
                pass

        report = DiscoveryReport(
            serial_ports=sp,
            com_ports=cp,
            network_adapters=net_adapters,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        # Auto-print to terminal when called interactively (not in Jupyter)
        # Users can suppress by not calling print() themselves; we print
        # only when the caller didn't capture the return value — but Python
        # can't detect that. Instead, just return and let callers decide.
        return report

    def verify(self, include_hardware: bool = True) -> HardwareReport:
        self._checks.clear()
        
        # 0. Legacy layout check
        has_project_json = (self._project.root / "project.json").exists()
        has_toml = (self._project.root / "awr2944.toml").exists()
        
        if has_project_json and not has_toml:
            self._add("legacy_project_layout", "FAIL", "OFFLINE", "Workspace uses legacy project.json format", sugg="Use 'awr init' to create a new project scaffolding instead of the SDK repository.")
            
            # Since it's legacy, just return here and SKIP the rest.
            # But the user said: "Dependent configuration/hardware checks may then SKIP."
            # So let's mark them as SKIP.
            self._add("project_structure", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("portable_config_valid", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("local_config_valid", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("dca_control_exe_exists", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("dca_record_exe_exists", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("cf_json_exists", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("cf_json_consistency", "SKIP", "OFFLINE", "Skipped due to legacy layout")
            self._add("no_active_session_lock", "SKIP", "OFFLINE", "Skipped due to legacy layout", req=False)
            
            if include_hardware:
                self._add("cli_com_port_exists", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout")
                self._add("aux_com_port_exists", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout", req=False)
                self._add("usb_xds110_identity", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout", req=False)
                self._add("uart_prompt_responds", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout")
                self._add("host_nic_owns_ip", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout")
                self._add("udp_data_port_bind", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout")
                self._add("dca_control_responds", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Skipped due to legacy layout")
                
            return HardwareReport(list(self._checks.values()), datetime.now(timezone.utc).isoformat(), "LIVE_DIAGNOSTIC" if include_hardware else "OFFLINE_ONLY")
            
        # 1. Project structure
        dirs = ["captures", "profiles", ".awr2944"]
        missing = [d for d in dirs if not (self._project.root / d).exists()]
        if missing:
            self._add("project_structure", "FAIL", "OFFLINE", f"Missing: {', '.join(missing)}")
        else:
            self._add("project_structure", "PASS", "OFFLINE", "All expected directories exist")

        # 2. Portable config TOML
        portable_path = self._project.root / "awr2944.toml"
        try:
            import tomllib
            with open(portable_path, "rb") as f:
                tomllib.load(f)
            self._add("portable_config_valid", "PASS", "OFFLINE", "awr2944.toml parses successfully")
        except Exception as e:
            self._add("portable_config_valid", "FAIL", "OFFLINE", f"Error parsing awr2944.toml: {e}")

        # 3. Local config TOML
        local_path = self._project.root / ".awr2944" / "local.toml"
        try:
            import tomllib
            with open(local_path, "rb") as f:
                tomllib.load(f)
            self._add("local_config_valid", "PASS", "OFFLINE", "local.toml parses successfully")
        except Exception as e:
            self._add("local_config_valid", "FAIL", "OFFLINE", f"Error parsing local.toml: {e}")

        cfg = self._project.config

        # 4 & 5. Executables
        if cfg.local.dca_control_exe and Path(cfg.local.dca_control_exe).exists():
            self._add("dca_control_exe_exists", "PASS", "OFFLINE", cfg.local.dca_control_exe)
        else:
            self._add("dca_control_exe_exists", "FAIL", "OFFLINE", f"Not found: '{cfg.local.dca_control_exe}'")

        if cfg.local.dca_record_exe and Path(cfg.local.dca_record_exe).exists():
            self._add("dca_record_exe_exists", "PASS", "OFFLINE", cfg.local.dca_record_exe)
        else:
            self._add("dca_record_exe_exists", "FAIL", "OFFLINE", f"Not found: '{cfg.local.dca_record_exe}'")

        # 6. cf.json exists
        cf = cfg.local.cf_json_path
        if cf and Path(cf).exists():
            self._add("cf_json_exists", "PASS", "OFFLINE", cf)
        else:
            self._add("cf_json_exists", "FAIL", "OFFLINE", f"Not found: '{cf}'")

        # 7. cf.json consistency
        if self._checks.get("cf_json_exists", CheckResult("","FAIL","","")).status != "PASS" or self._checks.get("portable_config_valid", CheckResult("","FAIL","","")).status != "PASS" or self._checks.get("local_config_valid", CheckResult("","FAIL","","")).status != "PASS":
            self._add("cf_json_consistency", "SKIP", "OFFLINE", "Prerequisite cf.json or configs failed", sugg="Depends on: cf_json_exists, portable_config_valid, local_config_valid")
        else:
            try:
                cfg.validate_cf_json()
                self._add("cf_json_consistency", "PASS", "OFFLINE", "Matches TOML network settings")
            except Exception as e:
                self._add("cf_json_consistency", "FAIL", "OFFLINE", str(e))

        # 15. Session Lock
        from awr2944_dca.api._lock import HardwareLease
        from awr2944_dca.api._session import resolve_connection
        try:
            conn = resolve_connection(self._project.root)
            state, lock_info = HardwareLease.inspect_owner(
                com_port=conn.com_port,
                host_ip=conn.host_ip,
                data_port=conn.data_port,
                dca_ip=conn.dca_ip,
                cmd_port=conn.cmd_port,
            )
            
            if state == "owned_by_us":
                self._add("no_active_session_lock", "PASS", "OFFLINE", f"Session lock held by this process ({lock_info.pid})", req=False)
            elif state == "owned_by_other_live":
                self._add("no_active_session_lock", "FAIL", "OFFLINE", f"Hardware locked by live process {lock_info.pid} (Project: {lock_info.project_root})", req=False)
            elif state == "stale":
                self._add("no_active_session_lock", "WARN", "OFFLINE", f"Stale lock found from dead process {lock_info.pid} (Project: {lock_info.project_root})", req=False)
            elif state == "malformed":
                self._add("no_active_session_lock", "WARN", "OFFLINE", "Malformed lock file found", req=False)
            else:
                self._add("no_active_session_lock", "PASS", "OFFLINE", "Hardware is currently unlocked", req=False)
        except Exception as e:
            self._add("no_active_session_lock", "FAIL", "OFFLINE", f"Failed to check lock status: {e}", req=False)

        if not include_hardware:
            return HardwareReport(list(self._checks.values()), datetime.now(timezone.utc).isoformat(), "OFFLINE_ONLY")

        # 8 & 9. COM Ports exist
        from awr2944_dca.hardware.ports import scan_ports
        all_ports = {p.com for p in scan_ports()}
        
        com = cfg.local.com_port
        if not com:
            self._add("cli_com_port_exists", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", "Not configured")
        elif com in all_ports:
            self._add("cli_com_port_exists", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", com)
        else:
            self._add("cli_com_port_exists", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"{com} missing")

        aux = cfg.local.aux_com_port
        if not aux:
            self._add("aux_com_port_exists", "WARN", "DIAGNOSTIC_HARDWARE_ACCESS", "Not configured", req=False)
        elif aux in all_ports:
            self._add("aux_com_port_exists", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", aux, req=False)
        else:
            self._add("aux_com_port_exists", "WARN", "DIAGNOSTIC_HARDWARE_ACCESS", f"{aux} missing", req=False)

        # 10. USB/XDS110 identity
        if self._checks.get("cli_com_port_exists", CheckResult("","FAIL","","")).status != "PASS":
            self._add("usb_xds110_identity", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "CLI COM port missing", req=False, sugg="Depends on: cli_com_port_exists")
        else:
            from awr2944_dca.headless_serial import discover_serial_ports
            sp = [p for p in discover_serial_ports() if p.port == com]
            if sp and sp[0].is_xds110:
                self._add("usb_xds110_identity", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", "Matches XDS110 VID/PID", req=False)
            else:
                self._add("usb_xds110_identity", "WARN", "DIAGNOSTIC_HARDWARE_ACCESS", "Not recognized as XDS110 (could be standard serial)", req=False)

        # 11. UART Prompt
        if self._checks.get("cli_com_port_exists", CheckResult("","FAIL","","")).status != "PASS":
            self._add("uart_prompt_responds", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "CLI COM port missing", sugg="Depends on: cli_com_port_exists")
        else:
            try:
                import serial
                with serial.Serial(com, cfg.local.baud_rate, timeout=3.0) as ser:
                    ser.reset_input_buffer()
                    ser.write(b"\n")
                    res = ser.read_until(b"mmwDemo:/>")
                    if b"mmwDemo:/>" in res:
                        self._add("uart_prompt_responds", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", "Received mmwDemo:/>")
                    else:
                        self._add("uart_prompt_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", "TIMEOUT - No prompt received")
            except serial.SerialException as e:
                self._add("uart_prompt_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"BUSY or error: {e}", sugg="Check if another application has the port open.")
            except Exception as e:
                self._add("uart_prompt_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"Error: {e}")

        # 12. Host NIC owns IP
        host_ip = cfg.local.host_ip
        if not host_ip:
            self._add("host_nic_owns_ip", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", "Not configured")
        else:
            from awr2944_dca.dca.preflight import run_dca_preflight
            pf = run_dca_preflight(host_ip=host_ip, dca_ip=cfg.portable.dca_ip, ping_only=True)
            from awr2944_dca.dca.preflight import _run_ps_json, _as_dicts
            script = f"Get-NetIPAddress -IPAddress {host_ip} -ErrorAction SilentlyContinue | Select-Object InterfaceAlias"
            found = _as_dicts(_run_ps_json(script))
            if found:
                self._add("host_nic_owns_ip", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", f"Bound to {found[0].get('InterfaceAlias', 'Unknown')}")
            else:
                self._add("host_nic_owns_ip", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"{host_ip} not found on any local interface")

        # 14. UDP Data port bind
        if self._checks.get("host_nic_owns_ip", CheckResult("","FAIL","","")).status != "PASS":
            self._add("udp_data_port_bind", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Host NIC IP check failed", sugg="Depends on: host_nic_owns_ip")
        else:
            port = cfg.portable.data_port
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((host_ip, port))
                sock.close()
                self._add("udp_data_port_bind", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", f"{host_ip}:{port} is available")
            except OSError as e:
                self._add("udp_data_port_bind", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"BUSY - Cannot bind {host_ip}:{port} ({e})")

        # 13. DCA Control responds
        if (self._checks.get("dca_control_exe_exists", CheckResult("","FAIL","","")).status != "PASS" or 
            self._checks.get("cf_json_exists", CheckResult("","FAIL","","")).status != "PASS" or 
            self._checks.get("cf_json_consistency", CheckResult("","FAIL","","")).status != "PASS" or
            self._checks.get("host_nic_owns_ip", CheckResult("","FAIL","","")).status != "PASS"):
            self._add("dca_control_responds", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "Prerequisites failed", sugg="Depends on: dca_control_exe_exists, cf_json_exists, cf_json_consistency, host_nic_owns_ip")
        else:
            cmd = [cfg.local.dca_control_exe, "query_sys_status", cfg.local.cf_json_path]
            try:
                t0 = datetime.now()
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                dt = (datetime.now() - t0).total_seconds()
                
                if res.returncode == 0:
                    self._add("dca_control_responds", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", f"System alive (took {dt:.2f}s)")
                else:
                    self._add("dca_control_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"Command failed (RC={res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
            except subprocess.TimeoutExpired:
                self._add("dca_control_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", "TIMEOUT - DCA did not respond")
            except Exception as e:
                self._add("dca_control_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"Error: {e}")

        return HardwareReport(list(self._checks.values()), datetime.now(timezone.utc).isoformat(), "LIVE_DIAGNOSTIC")
