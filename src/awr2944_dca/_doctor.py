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


# ---------------------------------------------------------------------------
# Shared UART probe helper (used by both doctor and autodetect_serial)
# ---------------------------------------------------------------------------

def _probe_uart_prompt(
    port: str,
    baud: int = 115200,
    timeout: float = 3.0,
) -> tuple[bool, str]:
    """Try to open *port* and check for the mmwDemo prompt.

    Returns ``(verified: bool, detail: str)``.

    - ``verified=True``  — the port responded with ``mmwDemo:/>`
    - ``verified=False`` — port busy, permission denied, no response, or any error

    This is a **read-only** probe: we only send a single ``\\n`` byte and
    read back at most ``timeout`` seconds of data.  We do not alter baud,
    flow-control, or any hardware state.

    This function is shared between :meth:`HardwareManager.verify` (doctor)
    and :meth:`HardwareManager.autodetect_serial` so both behave identically.
    """
    try:
        import serial
        with serial.Serial(port, baud, timeout=timeout) as ser:
            ser.reset_input_buffer()
            ser.write(b"\n")
            res = ser.read_until(b"mmwDemo:/>")
            if b"mmwDemo:/>" in res:
                return True, "Received mmwDemo:/>"
            else:
                return False, "TIMEOUT - No prompt received"
    except Exception as exc:  # SerialException, PermissionError, OSError, ImportError
        name = type(exc).__name__
        return False, f"{name}: {exc}"


# ---------------------------------------------------------------------------
# Serial autodetection result
# ---------------------------------------------------------------------------

@dataclass
class SerialAutodetectResult:
    """Result of :meth:`HardwareManager.autodetect_serial`."""
    candidates: list[Any]          # list[SerialPortInfo] — XDS110 candidates examined
    cli_port: str                  # verified Application/User CLI port, or ""
    cli_detail: str                # probe detail string for cli_port
    aux_port: str                  # inferred Auxiliary port, or ""
    verified: bool                 # True iff cli_port was confirmed by prompt
    saved: bool                    # True iff save=True was applied
    warnings: list[str]            # Non-fatal observations
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def print(self) -> None:  # noqa: A001
        """Print a human-readable summary to the terminal."""
        from rich.console import Console
        console = Console()
        console.print("\n[bold cyan]AWR2944 Serial Autodetection[/bold cyan]")
        n = len(self.candidates)
        if n == 0:
            console.print("[red]✗ No XDS110 devices found.[/red]")
            console.print(
                "[dim]  Install the official TI/XDS drivers or check USB connection.[/dim]"
            )
        else:
            console.print(f"[green]✓ {n} XDS110 candidate(s) found[/green]")

        if self.verified:
            console.print(f"[green]✓ Application/User CLI: {self.cli_port}[/green]")
            console.print(f"  [dim]verified by response: {self.cli_detail}[/dim]")
        elif self.cli_port:
            console.print(f"[yellow]? CLI candidate: {self.cli_port} (not prompt-verified)[/yellow]")
        else:
            console.print("[red]✗ No verified CLI port found.[/red]")

        if self.aux_port:
            console.print(f"[green]✓ Auxiliary UART: {self.aux_port}[/green]")
        else:
            console.print("[dim]  Auxiliary UART: not identified[/dim]")

        for w in self.warnings:
            console.print(f"[yellow]  ⚠ {w}[/yellow]")

        if self.saved:
            console.print("[green]✓ Saved to .awr2944/local.toml[/green]")

    def __repr__(self) -> str:
        return (
            f"SerialAutodetectResult("
            f"cli={self.cli_port!r}, aux={self.aux_port!r}, "
            f"verified={self.verified}, saved={self.saved}, "
            f"warnings={len(self.warnings)})"
        )

    def _repr_html_(self) -> str:
        cli_html = (
            f"<span style='color:green'>&#10003; {self.cli_port}</span>"
            if self.verified and self.cli_port
            else (f"<span style='color:red'>&#10007; {self.cli_port or 'not found'}</span>")
        )
        aux_html = (
            f"<span style='color:green'>&#10003; {self.aux_port}</span>"
            if self.aux_port
            else "<span style='color:gray'>-</span>"
        )
        warn_html = "".join(
            f"<li style='color:orange'>{w}</li>" for w in self.warnings
        )
        warn_section = f"<ul>{warn_html}</ul>" if self.warnings else ""
        saved_html = (
            "<p style='color:green'>&#10003; Saved to .awr2944/local.toml</p>"
            if self.saved else ""
        )
        return (
            f"<details open><summary><b>SerialAutodetectResult</b> &mdash; {self.timestamp}</summary>"
            f"<p><b>CLI port:</b> {cli_html} &nbsp; detail: <code>{self.cli_detail}</code></p>"
            f"<p><b>AUX port:</b> {aux_html}</p>"
            f"<p><b>Candidates examined:</b> {len(self.candidates)}</p>"
            f"{warn_section}{saved_html}</details>"
        )


# ---------------------------------------------------------------------------
# Toolchain autodetection result
# ---------------------------------------------------------------------------

@dataclass
class ToolchainCandidate:
    """A TI DCA1000 toolchain installation found on disk."""
    root: Path                     # e.g. C:\ti\mmwave_studio_03_01_04_04\mmWaveStudio\PostProc
    version_tag: str               # e.g. "03_01_04_04"
    control_exe: Path
    record_exe: Path
    rf_api_dll: Path
    cf_json: Path
    complete: bool = True

    def __repr__(self) -> str:
        return f"ToolchainCandidate(version={self.version_tag!r}, root={self.root})"


@dataclass
class ToolchainAutodetectResult:
    """Result of :meth:`HardwareManager.autodetect_toolchain`."""
    candidates: list[ToolchainCandidate]   # all complete installations found
    selected: Optional[ToolchainCandidate] # None if 0 or >1 without explicit choice
    cf_valid: bool                         # True if cf.json validated against project
    cf_detail: str                         # validation detail
    saved: bool
    warnings: list[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def print(self) -> None:  # noqa: A001
        from rich.console import Console
        console = Console()
        console.print("\n[bold cyan]TI DCA1000 Toolchain Autodetection[/bold cyan]")

        if not self.candidates:
            console.print("[red]✗ No complete TI DCA1000 toolchain found.[/red]")
            console.print("[dim]Expected files (all required):[/dim]")
            for f in ("DCA1000EVM_CLI_Control.exe", "DCA1000EVM_CLI_Record.exe",
                      "RF_API.dll", "cf.json"):
                console.print(f"[dim]  {f}[/dim]")
            return

        if len(self.candidates) > 1 and self.selected is None:
            console.print(
                f"[yellow]⚠ Found {len(self.candidates)} complete TI toolchains."
                " Selection required:[/yellow]"
            )
            for i, c in enumerate(self.candidates, 1):
                console.print(f"  {i}. mmWave Studio {c.version_tag}  ({c.root})")
            console.print(
                "[dim]Use autodetect_toolchain(version=\"X_Y_Z_W\") "
                "or autodetect_toolchain(root=r\"...\") to select.[/dim]"
            )
            return

        s = self.selected
        if s is None:
            return
        console.print(f"[green]✓ mmWave Studio {s.version_tag}[/green]  ({s.root})")
        for label, path in (
            ("DCA1000 Control", s.control_exe),
            ("DCA1000 Record", s.record_exe),
            ("RF_API.dll", s.rf_api_dll),
            ("cf.json", s.cf_json),
        ):
            console.print(f"[green]✓ {label}[/green]  [dim]{path}[/dim]")

        if self.cf_valid:
            console.print("[green]✓ cf.json network configuration valid[/green]")
        else:
            console.print(f"[red]✗ cf.json validation: {self.cf_detail}[/red]")

        for w in self.warnings:
            console.print(f"[yellow]  ⚠ {w}[/yellow]")

        if self.saved:
            console.print("[green]✓ Saved to .awr2944/local.toml[/green]")

    def __repr__(self) -> str:
        sel = self.selected.version_tag if self.selected else "None"
        return (
            f"ToolchainAutodetectResult("
            f"candidates={len(self.candidates)}, selected={sel!r}, "
            f"cf_valid={self.cf_valid}, saved={self.saved})"
        )

    def _repr_html_(self) -> str:
        if not self.candidates:
            body = "<p style='color:red'>&#10007; No complete TI DCA1000 toolchain found.</p>"
        elif len(self.candidates) > 1 and self.selected is None:
            rows = "".join(
                f"<tr><td>{i}</td><td>{c.version_tag}</td><td><code>{c.root}</code></td></tr>"
                for i, c in enumerate(self.candidates, 1)
            )
            body = (
                f"<p style='color:orange'>&#9888; Multiple toolchains found &mdash; selection required.</p>"
                f"<table border='1' style='border-collapse:collapse'>"
                f"<tr><th>#</th><th>Version</th><th>Root</th></tr>{rows}</table>"
            )
        else:
            s = self.selected
            cf_html = (
                "<span style='color:green'>&#10003; valid</span>" if self.cf_valid
                else f"<span style='color:red'>&#10007; {self.cf_detail}</span>"
            )
            body = (
                f"<p><b>mmWave Studio {s.version_tag}</b> &mdash; <code>{s.root}</code></p>"
                f"<p>cf.json: {cf_html}</p>"
            )
        warn_html = "".join(f"<li>{w}</li>" for w in self.warnings)
        warn_section = f"<ul style='color:orange'>{warn_html}</ul>" if self.warnings else ""
        saved_html = (
            "<p style='color:green'>&#10003; Saved to .awr2944/local.toml</p>"
            if self.saved else ""
        )
        return (
            f"<details open><summary><b>ToolchainAutodetectResult</b> &mdash; {self.timestamp}</summary>"
            f"{body}{warn_section}{saved_html}</details>"
        )


# ---------------------------------------------------------------------------
# TI toolchain filesystem scanner
# ---------------------------------------------------------------------------

# Required files for a complete DCA1000 toolchain installation
_REQUIRED_TOOLCHAIN_FILES = (
    "DCA1000EVM_CLI_Control.exe",
    "DCA1000EVM_CLI_Record.exe",
    "RF_API.dll",
    "cf.json",
)

# mmWave Studio installs under these subtrees within each versioned root
_POSTPROC_SUBDIRS = (
    "mmWaveStudio/PostProc",
    "PostProc",
    "mmWaveStudio\\PostProc",
)


def _scan_ti_toolchains(
    extra_roots: list[Path] | None = None,
) -> list[ToolchainCandidate]:
    """Scan well-known Windows TI install directories for complete DCA1000 toolchains.

    Does **not** crawl the entire disk.  Checks:
    - ``C:\\ti\\mmwave_studio_*``  (conventional TI location)
    - Any paths provided via *extra_roots*

    Returns a sorted list of complete :class:`ToolchainCandidate` objects.
    Incomplete installations are silently skipped.
    """
    search_roots: list[Path] = []

    # Conventional TI installation directory
    c_ti = Path("C:/ti")
    if c_ti.is_dir():
        try:
            for entry in sorted(c_ti.iterdir()):
                if entry.is_dir() and entry.name.startswith("mmwave_studio_"):
                    search_roots.append(entry)
        except PermissionError:
            pass

    if extra_roots:
        search_roots.extend(extra_roots)

    candidates: list[ToolchainCandidate] = []
    for studio_root in search_roots:
        # Extract version tag from directory name
        # e.g. mmwave_studio_03_01_04_04 -> "03_01_04_04"
        tag = studio_root.name.replace("mmwave_studio_", "", 1)

        # Search candidate PostProc directories
        postproc_dirs: list[Path] = []
        for sub in _POSTPROC_SUBDIRS:
            candidate_dir = studio_root / sub
            if candidate_dir.is_dir():
                postproc_dirs.append(candidate_dir)
        # Also try the root itself (flat layout)
        postproc_dirs.append(studio_root)

        for pdir in postproc_dirs:
            files = {f: pdir / f for f in _REQUIRED_TOOLCHAIN_FILES}
            if all(p.exists() for p in files.values()):
                candidates.append(ToolchainCandidate(
                    root=pdir,
                    version_tag=tag,
                    control_exe=files["DCA1000EVM_CLI_Control.exe"],
                    record_exe=files["DCA1000EVM_CLI_Record.exe"],
                    rf_api_dll=files["RF_API.dll"],
                    cf_json=files["cf.json"],
                ))
                break  # found complete install under this studio_root

    # Stable sort: newest version tag last (lexicographic on version string)
    candidates.sort(key=lambda c: c.version_tag)
    return candidates


def _xds110_device_serial(instance_id: str) -> str:
    """Extract the physical XDS110 device serial from a PnP InstanceId string.

    Windows PnP InstanceIds for XDS110 look like::

        USB\\VID_0451&PID_BEF3\\<device_serial>&MI_00
        USB\\VID_0451&PID_BEF3\\<device_serial>&MI_03

    The two sibling COM ports (Application/User and Auxiliary) of the SAME
    physical XDS110 share the same ``<device_serial>`` component and differ
    only in their ``MI_xx`` interface index suffix.

    Returns the device serial string, or ``""`` if the InstanceId format is
    not recognisable.
    """
    # Split on backslash; the serial+MI part is the 3rd component
    parts = instance_id.replace("/", "\\").split("\\")
    if len(parts) < 3:
        return ""
    serial_mi = parts[2]  # e.g. "ABC123&MI_00" or "ABC123" (no MI suffix)
    # Strip the &MI_xx interface suffix if present
    amp_idx = serial_mi.find("&")
    return serial_mi[:amp_idx] if amp_idx >= 0 else serial_mi


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

        # 11. UART Prompt — uses shared _probe_uart_prompt() helper
        if self._checks.get("cli_com_port_exists", CheckResult("","FAIL","","")).status != "PASS":
            self._add("uart_prompt_responds", "SKIP", "DIAGNOSTIC_HARDWARE_ACCESS", "CLI COM port missing", sugg="Depends on: cli_com_port_exists")
        else:
            verified, detail = _probe_uart_prompt(com, cfg.local.baud_rate, timeout=3.0)
            if verified:
                self._add("uart_prompt_responds", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS", detail)
            else:
                if "SerialException" in detail or "BUSY" in detail or "PermissionError" in detail:
                    self._add("uart_prompt_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", f"BUSY or error: {detail}", sugg="Check if another application has the port open.")
                else:
                    self._add("uart_prompt_responds", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS", detail)

        # 12. Host NIC owns IP — with actionable candidate detail
        host_ip = cfg.local.host_ip
        if not host_ip:
            self._add(
                "host_nic_owns_ip", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS",
                "host_ip not configured. "
                "Run p.hardware.autodetect_serial(save=True) or edit .awr2944/local.toml."
            )
        else:
            from awr2944_dca.dca.preflight import _run_ps_json, _as_dicts
            script = (
                f"Get-NetIPAddress -IPAddress '{host_ip}' "
                "-ErrorAction SilentlyContinue | Select-Object InterfaceAlias"
            )
            found = _as_dicts(_run_ps_json(script))
            if found:
                self._add(
                    "host_nic_owns_ip", "PASS", "DIAGNOSTIC_HARDWARE_ACCESS",
                    f"Bound to {found[0].get('InterfaceAlias', 'Unknown')}"
                )
            else:
                # Build actionable candidate detail using the same ranking logic
                detail_lines = [f"{host_ip} not found on any local interface."]
                try:
                    from awr2944_dca.api._eth_status import rank_adapter_candidates, pick_recommended, CONF_UNSAFE
                    # Gather raw adapter data
                    adp_script = (
                        "Get-NetAdapter -ErrorAction SilentlyContinue "
                        "| Select-Object Name, InterfaceDescription, Status, MacAddress"
                    )
                    adp_rows = _as_dicts(_run_ps_json(adp_script))
                    adp_by_alias: dict = {}
                    for row in adp_rows:
                        alias = row.get("Name", "")
                        if alias:
                            adp_by_alias[alias] = {
                                "_Description": row.get("InterfaceDescription", ""),
                                "_Status":      row.get("Status", ""),
                                "_MacAddress":  row.get("MacAddress", ""),
                            }
                    ip_script = (
                        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue "
                        "| Select-Object InterfaceAlias, IPAddress, PrefixLength, AddressFamily"
                    )
                    ip_rows = _as_dicts(_run_ps_json(ip_script))
                    gw_script = (
                        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
                        "-ErrorAction SilentlyContinue | Select-Object InterfaceAlias"
                    )
                    gw_rows  = _as_dicts(_run_ps_json(gw_script))
                    gw_set   = {r.get("InterfaceAlias", "") for r in gw_rows}
                    for row in ip_rows:
                        al = row.get("InterfaceAlias", "")
                        row.update(adp_by_alias.get(al, {}))
                        row["_has_gateway"] = al in gw_set
                    candidates = rank_adapter_candidates(ip_rows, host_ip)
                    rec, ambiguous = pick_recommended(candidates)

                    if rec:
                        cur_ips = (
                            ", ".join(
                                f"{ip}/{pl}" for ip, pl in
                                zip(rec.ipv4_addresses, rec.prefix_lengths)
                            ) or "(no IPv4)"
                        )
                        detail_lines += [
                            f"Likely dedicated DCA adapter:",
                            f"  Alias:    {rec.alias}",
                            f"  Desc:     {rec.description or '(unknown)'}",
                            f"  Current:  {cur_ips}",
                            f"  Gateway:  {'yes' if rec.has_gateway else 'no'}",
                            f"Configure that adapter manually:",
                            f"  {host_ip} /24  (gateway blank, DNS blank)",
                            f"  See p.eth.instructions() for full guidance.",
                        ]
                    elif ambiguous:
                        gw_free = [c for c in candidates if not c.has_gateway and c.link_state.lower() == "up"]
                        plausible_aliases = [c.alias for c in gw_free]
                        detail_lines += [
                            "Adapter selection is ambiguous.",
                            "Plausible gateway-less adapters:",
                        ] + [f"  - {a}" for a in plausible_aliases]
                        detail_lines.append("See p.eth.instructions() for guidance.")
                    else:
                        # Check if only gateway-bearing adapters exist
                        unsafe = [c for c in candidates if c.confidence == CONF_UNSAFE]
                        if unsafe:
                            detail_lines += [
                                "WARNING: the only wired adapters found have default gateways "
                                "(normal Internet connections). "
                                "Do NOT assign the DCA host IP to these adapters.",
                                "Use a dedicated USB-Ethernet adapter for DCA1000.",
                            ]
                        else:
                            detail_lines.append(
                                "No suitable dedicated adapter found. "
                                "Connect a dedicated wired Ethernet adapter for DCA1000."
                            )
                    detail_lines.append("No network settings were changed.")
                except Exception:
                    detail_lines.append("(Could not enumerate adapters for candidate advice.)")

                self._add(
                    "host_nic_owns_ip", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS",
                    "  ".join(detail_lines),
                )

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
                err_str = str(e)
                # Distinguish port-in-use from other socket errors
                if e.errno in (98, 10048) or "in use" in err_str.lower() or "address already" in err_str.lower():
                    # Port already bound — try to find the owning PID (read-only)
                    pid_detail = ""
                    try:
                        import subprocess as _sp
                        res = _sp.run(
                            ["powershell", "-NoProfile", "-Command",
                             f"Get-NetUDPEndpoint -LocalAddress {host_ip} "
                             f"-LocalPort {port} -ErrorAction SilentlyContinue "
                             "| Select-Object -ExpandProperty OwningProcess"],
                            capture_output=True, text=True, timeout=5,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                        )
                        pid = res.stdout.strip()
                        if pid.isdigit():
                            pid_detail = f" (PID {pid} — stop that process or wait)"
                    except Exception:
                        pass
                    self._add(
                        "udp_data_port_bind", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS",
                        f"UDP {host_ip}:{port} already in use{pid_detail}"
                    )
                else:
                    self._add(
                        "udp_data_port_bind", "FAIL", "DIAGNOSTIC_HARDWARE_ACCESS",
                        f"Cannot bind {host_ip}:{port} — {err_str}"
                    )

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

    # ------------------------------------------------------------------
    # Autodetection APIs
    # ------------------------------------------------------------------

    def autodetect_serial(
        self,
        save: bool = False,
        baud: int | None = None,
        probe_timeout: float = 3.0,
    ) -> SerialAutodetectResult:
        """Autodetect the AWR2944 Application/User CLI and Auxiliary serial ports.

        **Detection algorithm** (completely read-only except for ``save=True``):

        1. Enumerate all serial ports via Windows PnP (no probing).
        2. Filter to XDS110 candidates only (VID=0451, PID=BEF3).
        3. **Only** XDS110 candidates are actively probed — never arbitrary
           COM ports such as Arduinos, power supplies, or lab instruments.
        4. Each candidate is probed with a single ``\\n`` byte; if the SDK
           demo prompt ``mmwDemo:/>`` is returned, that port is the CLI.
        5. If exactly one unverified XDS110 port is identified as a physical
           sibling of the CLI port (same device serial in PnP InstanceId),
           it is assigned as the Auxiliary UART.  If no sibling can be
           unambiguously identified, AUX is left empty and a warning is emitted.

        Args:
            save: If ``True`` and exactly one CLI port is verified, write
                  ``com_port`` and ``aux_com_port`` to ``.awr2944/local.toml``.
                  All other local settings are preserved.
            baud: Override the baud rate for probing (default: project config
                  or 115200).
            probe_timeout: Per-port read timeout in seconds.

        Returns:
            :class:`SerialAutodetectResult`
        """
        from awr2944_dca.headless_serial import discover_serial_ports

        cfg = self._project.config
        baud_rate = baud or cfg.local.baud_rate or 115200
        warnings: list[str] = []

        # Step 1-2: PnP discovery, filter XDS110
        all_ports = discover_serial_ports()
        xds_candidates = [p for p in all_ports if p.is_xds110]

        if not xds_candidates:
            # Check if any non-XDS110 ports look like TI (VID=0451) to give
            # a better driver-missing hint
            ti_ports = [p for p in all_ports if p.vid == "0451"]
            if ti_ports:
                warnings.append(
                    "TI/XDS hardware may be present (VID=0451), but the expected "
                    "XDS110 USB serial interface was not enumerated. "
                    "Install the official TI/XDS/FTDI drivers supplied by TI."
                )
            return SerialAutodetectResult(
                candidates=xds_candidates,
                cli_port="",
                cli_detail="No XDS110 candidates found",
                aux_port="",
                verified=False,
                saved=False,
                warnings=warnings,
            )

        # Step 3-4: Probe ONLY XDS110 candidates
        verified_cli: list[tuple[str, str]] = []  # (port, detail)
        unverified: list[str] = []

        for sp in xds_candidates:
            ok, detail = _probe_uart_prompt(sp.port, baud_rate, probe_timeout)
            if ok:
                verified_cli.append((sp.port, detail))
            else:
                unverified.append(sp.port)

        # Step 5: Assign CLI and AUX
        cli_port = ""
        cli_detail = ""
        aux_port = ""
        verified = False

        if len(verified_cli) == 1:
            cli_port, cli_detail = verified_cli[0]
            verified = True

            # Infer AUX using physical-device identity:
            # Two XDS110 COM ports from the SAME physical device share the
            # device serial suffix in their InstanceId, e.g.:
            #   USB\VID_0451&PID_BEF3\<device_serial>&MI_00
            #   USB\VID_0451&PID_BEF3\<device_serial>&MI_03
            # We must NOT assign an unverified port from a *different* device.
            cli_sp = next((s for s in xds_candidates if s.port == cli_port), None)
            cli_dev_serial = _xds110_device_serial(cli_sp.instance_id) if cli_sp else ""

            # Collect unverified ports that are siblings of the CLI device
            sibling_unverified: list[str] = []
            non_sibling_unverified: list[str] = []
            for port in unverified:
                sp = next((s for s in xds_candidates if s.port == port), None)
                dev_serial = _xds110_device_serial(sp.instance_id) if sp else ""
                if cli_dev_serial and dev_serial and dev_serial == cli_dev_serial:
                    sibling_unverified.append(port)
                else:
                    # Either no instance_id info, or from a different physical device
                    non_sibling_unverified.append(port)

            if len(sibling_unverified) == 1:
                aux_port = sibling_unverified[0]
                if non_sibling_unverified:
                    warnings.append(
                        f"Additional XDS110 port(s) from other physical device(s) "
                        f"ignored for AUX: {non_sibling_unverified}"
                    )
            elif len(sibling_unverified) == 0 and len(unverified) == 1 and not cli_dev_serial:
                # Fallback: no instance_id info available for either port,
                # single unverified candidate — accept it (original behavior).
                aux_port = unverified[0]
            elif len(sibling_unverified) > 1:
                warnings.append(
                    f"Multiple sibling XDS110 ports for AUX; "
                    f"cannot unambiguously assign: {sibling_unverified}"
                )
            elif len(unverified) > 0 and not aux_port:
                warnings.append(
                    f"Cannot unambiguously assign AUX — {len(unverified)} unverified "
                    f"XDS110 port(s) but none confirmed as sibling of CLI {cli_port}: "
                    f"{unverified}"
                )

        elif len(verified_cli) == 0:
            if unverified:
                warnings.append(
                    f"No XDS110 port responded with mmwDemo:/>. "
                    f"Ports probed: {[s.port for s in xds_candidates]}. "
                    "Ensure the AWR2944 is powered on and running the mmWave SDK demo."
                )
            cli_detail = "No port responded to prompt probe"
        else:
            # Multiple responders — do NOT guess
            ports_str = [p for p, _ in verified_cli]
            warnings.append(
                f"Multiple XDS110 ports responded with mmwDemo:/>: {ports_str}. "
                "Cannot unambiguously assign CLI port."
            )
            cli_detail = f"Ambiguous: {ports_str}"


        # Save
        saved = False
        if save:
            if verified and not warnings:
                cfg.local.com_port = cli_port
                cfg.local.aux_com_port = aux_port
                cfg.save()
                saved = True
            elif not verified:
                warnings.append("save=True ignored: no verified CLI port.")
            else:
                warnings.append("save=True ignored: ambiguous result, not saving.")

        return SerialAutodetectResult(
            candidates=xds_candidates,
            cli_port=cli_port,
            cli_detail=cli_detail,
            aux_port=aux_port,
            verified=verified,
            saved=saved,
            warnings=warnings,
        )

    def autodetect_toolchain(
        self,
        save: bool = False,
        version: str | None = None,
        root: "str | Path | None" = None,
    ) -> ToolchainAutodetectResult:
        """Autodetect a TI DCA1000 mmWave Studio toolchain installation.

        Scans ``C:\\ti\\mmwave_studio_*`` for complete toolchain installations
        containing all four required files.  Does **not** crawl the entire disk.

        Args:
            save: Write the four tool paths to ``.awr2944/local.toml`` when
                  selection is unambiguous.  All other settings are preserved.
            version: Select a specific mmWave Studio version tag, e.g.
                     ``"03_01_04_04"``.
            root: Explicit root directory to treat as a candidate.  Bypasses
                  the standard scan (still validates completeness).

        Returns:
            :class:`ToolchainAutodetectResult`
        """
        from awr2944_dca._config import ConfigMismatchError

        warnings: list[str] = []
        cfg = self._project.config

        # Discovery
        if root is not None:
            extra = [Path(root)]
            candidates = _scan_ti_toolchains(extra_roots=extra)
            # If none found in extra path, try treating root itself as PostProc dir
            if not candidates:
                pdir = Path(root)
                files = {f: pdir / f for f in _REQUIRED_TOOLCHAIN_FILES}
                if all(p.exists() for p in files.values()):
                    tag = pdir.parent.name.replace("mmwave_studio_", "", 1) or "unknown"
                    candidates = [ToolchainCandidate(
                        root=pdir,
                        version_tag=tag,
                        control_exe=files["DCA1000EVM_CLI_Control.exe"],
                        record_exe=files["DCA1000EVM_CLI_Record.exe"],
                        rf_api_dll=files["RF_API.dll"],
                        cf_json=files["cf.json"],
                    )]
        else:
            candidates = _scan_ti_toolchains()

        # Filter by version if requested
        if version is not None:
            filtered = [c for c in candidates if c.version_tag == version]
            if not filtered:
                available = [c.version_tag for c in candidates]
                warnings.append(
                    f"Requested version {version!r} not found. "
                    f"Available: {available}"
                )
            candidates = filtered

        # Select
        selected: Optional[ToolchainCandidate] = None
        if len(candidates) == 1:
            selected = candidates[0]
        elif len(candidates) > 1:
            # Multiple complete installations — do NOT silently pick one
            # (caller must use version= or root= to disambiguate)
            pass  # selected stays None

        # cf.json validation
        cf_valid = False
        cf_detail = ""
        if selected is not None:
            try:
                # Temporarily point config at this cf.json for validation
                saved_cf = cfg.local.cf_json_path
                cfg.local.cf_json_path = str(selected.cf_json)
                try:
                    cfg.validate_cf_json()
                    cf_valid = True
                    cf_detail = "Matches project network settings"
                except Exception as exc:
                    cf_detail = str(exc)
                finally:
                    cfg.local.cf_json_path = saved_cf
            except Exception as exc:
                cf_detail = f"Unexpected error: {exc}"

        # Save
        saved = False
        if save:
            if selected is not None:
                cfg.local.dca_control_exe = str(selected.control_exe)
                cfg.local.dca_record_exe = str(selected.record_exe)
                cfg.local.rf_api_dll = str(selected.rf_api_dll)
                cfg.local.cf_json_path = str(selected.cf_json)
                cfg.save()
                saved = True
            elif not candidates:
                warnings.append("save=True ignored: no complete toolchain found.")
            else:
                warnings.append(
                    "save=True ignored: multiple toolchains found, "
                    "use version= or root= to select one."
                )

        return ToolchainAutodetectResult(
            candidates=candidates,
            selected=selected,
            cf_valid=cf_valid,
            cf_detail=cf_detail,
            saved=saved,
            warnings=warnings,
        )
