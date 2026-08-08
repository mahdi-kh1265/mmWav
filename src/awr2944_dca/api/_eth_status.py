"""Ethernet status, candidate ranking, and instructions for DCA1000 setup.

This module provides the read-only user-facing Ethernet API:

    p.eth.status()       -- what is the current Ethernet readiness state?
    p.eth.instructions() -- how do I configure a dedicated DCA NIC manually?

Both methods are entirely read-only.  They query Windows networking state
(via the same PowerShell helpers used by hardware discovery in Task 1) but
never issue Set-NetIPAddress, New-NetIPAddress, Remove-NetIPAddress, or any
other mutating command.

Candidate ranking uses structural metadata (default route, link state, address
family, adapter type flags) rather than interface name strings.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from awr2944_dca._config import ProjectConfig


# ---------------------------------------------------------------------------
# Candidate confidence levels
# ---------------------------------------------------------------------------

CONF_READY     = "ready"          # already configured with the correct host IP
CONF_HIGH      = "high"           # gateway-less wired Ethernet, link Up, good address
CONF_MEDIUM    = "medium"         # gateway-less wired Ethernet, link Up, address unknown
CONF_LOW       = "low"            # gateway-less wired, link down or uncertain
CONF_UNSAFE    = "unsafe"         # has a default gateway — DO NOT recommend
CONF_SKIP      = "skip"           # Wi-Fi, VPN, loopback, virtual — skip for DCA


# ---------------------------------------------------------------------------
# Adapter candidate (enriched view for DCA selection)
# ---------------------------------------------------------------------------

@dataclass
class AdapterCandidate:
    """Enriched adapter info used for DCA candidate ranking."""
    alias:           str
    description:     str
    ipv4_addresses:  list[str]
    prefix_lengths:  list[int]
    link_state:      str          # "Up", "Disconnected", etc.
    mac_address:     str
    has_gateway:     bool
    media_type:      str          # "802.3", "Native 802.11", etc.
    confidence:      str          # one of the CONF_* constants
    classification:  str          # human-readable label for display
    owns_host_ip:    bool = False  # True when this adapter holds the configured host IP
    host_ip_prefix:  int  = -1    # prefix length of the matching host IP row (-1 if not found)


# ---------------------------------------------------------------------------
# EthernetStatusResult
# ---------------------------------------------------------------------------

@dataclass
class EthernetStatusResult:
    """Read-only DCA Ethernet status snapshot.

    Returned by ``p.eth.status()``.  Never mutates Windows networking.

    Fields
    ------
    host_ip_expected    -- host IP from ProjectConfig
    dca_ip              -- DCA device IP from ProjectConfig
    config_port         -- DCA config/command UDP port
    data_port           -- DCA data UDP port
    candidates          -- all wired adapters ranked by DCA suitability
    recommended         -- best candidate (None if ambiguous or not found)
    ambiguous           -- True when multiple equally-plausible candidates exist
    host_ip_present     -- True when expected host IP is already on some adapter
    host_ip_adapter     -- adapter alias that owns the host IP (or "")
    ready               -- True when host IP is present and an adapter is ready
    warnings            -- list of human-readable advisory strings
    """
    host_ip_expected:  str
    dca_ip:            str
    config_port:       int
    data_port:         int
    candidates:        list[AdapterCandidate]
    recommended:       Optional[AdapterCandidate]
    ambiguous:         bool
    host_ip_present:   bool
    host_ip_adapter:   str
    ready:             bool
    warnings:          list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Presentation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        r = "ready" if self.ready else "NOT ready"
        rec = self.recommended.alias if self.recommended else ("ambiguous" if self.ambiguous else "none")
        return (
            f"EthernetStatusResult({r}, "
            f"host_ip={self.host_ip_expected!r} present={self.host_ip_present}, "
            f"candidate={rec!r})"
        )

    def print(self) -> None:
        """Pretty-print ethernet status to stdout."""
        print("\nDCA Ethernet Status")
        print("=" * 50)
        print("\nProject Expectation")
        print(f"  Host IP     : {self.host_ip_expected or '(not configured)'}")
        print(f"  DCA IP      : {self.dca_ip}")
        print(f"  Config port : {self.config_port}")
        print(f"  Data port   : {self.data_port}")

        print("\nNetwork Adapter Candidates")
        if not self.candidates:
            print("  (no wired adapters found)")
        for c in self.candidates:
            ips = ", ".join(
                f"{ip}/{pl}" for ip, pl in zip(c.ipv4_addresses, c.prefix_lengths)
            ) if c.ipv4_addresses else "(no IPv4)"
            gw = "YES" if c.has_gateway else "no"
            owns = " ← owns host IP" if c.owns_host_ip else ""
            print(f"\n  {c.alias}")
            print(f"    {c.description}")
            print(f"    {ips}{owns}")
            print(f"    Link: {c.link_state}")
            print(f"    Default gateway: {gw}")
            if c.mac_address:
                print(f"    MAC: {c.mac_address}")
            print(f"    Classification: {c.classification}")

        print("\nOverall")
        if self.host_ip_present:
            print(f"  Host IP {self.host_ip_expected} present on {self.host_ip_adapter}: YES")
            print("  Ready for DCA verification: YES")
        else:
            print(f"  Host IP {self.host_ip_expected or '(not configured)'} present: NO")
            if self.recommended:
                print(f"  Likely DCA adapter: {self.recommended.alias}")
            elif self.ambiguous:
                print("  Adapter selection: AMBIGUOUS (multiple plausible adapters)")
            else:
                print("  Likely DCA adapter: NONE FOUND")
            print("  Ready: NO")

        for w in self.warnings:
            print(f"\n  ⚠ {w}")
        print(f"\n  [No network settings were changed]")

    def _repr_html_(self) -> str:
        """Jupyter HTML representation."""
        ready_color = "#7fdbca" if self.ready else "#ff6b6b"
        ready_text  = "✅ Ready" if self.ready else "❌ Not Ready"

        rows = ""
        for c in self.candidates:
            ips = "<br>".join(
                f"{ip}/{pl}" for ip, pl in zip(c.ipv4_addresses, c.prefix_lengths)
            ) or "(no IPv4)"
            gw  = "<b style='color:#ff6b6b'>YES ⚠</b>" if c.has_gateway else "no"
            owns = " <b>← host IP</b>" if c.owns_host_ip else ""
            rows += (
                f"<tr><td><b>{c.alias}</b><br><small>{c.description}</small></td>"
                f"<td>{ips}{owns}</td>"
                f"<td>{c.link_state}</td>"
                f"<td>{gw}</td>"
                f"<td>{c.classification}</td></tr>"
            )

        warn_html = "".join(
            f"<li style='color:#ffa07a'>⚠ {w}</li>" for w in self.warnings
        )
        warn_section = f"<ul>{warn_html}</ul>" if self.warnings else ""

        if self.host_ip_present:
            status_detail = (
                f"Host IP <code>{self.host_ip_expected}</code> present on "
                f"<b>{self.host_ip_adapter}</b>"
            )
        elif self.recommended:
            status_detail = (
                f"Host IP <code>{self.host_ip_expected or '(not configured)'}</code> not present. "
                f"Likely DCA adapter: <b>{self.recommended.alias}</b>"
            )
        elif self.ambiguous:
            status_detail = (
                f"Host IP <code>{self.host_ip_expected or '(not configured)'}</code> not present. "
                "Adapter selection is <b>ambiguous</b>."
            )
        else:
            status_detail = (
                f"Host IP <code>{self.host_ip_expected or '(not configured)'}</code> not present. "
                "No suitable DCA adapter found."
            )

        table = (
            "<table style='border-collapse:collapse;width:100%'>"
            "<thead><tr style='border-bottom:1px solid #555'>"
            "<th>Adapter</th><th>IPv4</th><th>Link</th>"
            "<th>Gateway</th><th>Classification</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        ) if self.candidates else "<p><em>No wired adapters found</em></p>"

        return (
            "<div style='font-family:monospace;padding:10px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            f"<h3 style='margin:0 0 8px'>🌐 DCA Ethernet Status — "
            f"<span style='color:{ready_color}'>{ready_text}</span></h3>"
            f"<p><b>Project:</b> host={self.host_ip_expected or '?'}, "
            f"dca={self.dca_ip}, cmd={self.config_port}, data={self.data_port}</p>"
            f"{table}"
            f"<p>{status_detail}</p>"
            f"{warn_section}"
            "<p style='color:#888;font-size:0.85em'>[No network settings were changed]</p>"
            "</div>"
        )


# ---------------------------------------------------------------------------
# EthernetInstructionsResult
# ---------------------------------------------------------------------------

@dataclass
class EthernetInstructionsResult:
    """DCA1000 manual NIC setup instructions.

    Returned by ``p.eth.instructions()``.  Never mutates Windows networking.
    """
    host_ip:         str
    prefix_length:   int
    subnet_mask:     str
    dca_ip:          str
    config_port:     int
    data_port:       int
    recommended_alias: Optional[str]
    recommended_desc:  Optional[str]
    ambiguous:         bool
    already_ready:     bool
    candidate_aliases: list[str]      # all plausible candidates when ambiguous

    def __repr__(self) -> str:
        return (
            f"EthernetInstructionsResult(host={self.host_ip!r}, "
            f"adapter={self.recommended_alias!r}, ready={self.already_ready})"
        )

    def print(self) -> None:
        """Print setup instructions to stdout."""
        print("\nDCA1000 Ethernet Setup")
        print("=" * 50)

        if self.recommended_alias:
            print(f"\nRecommended adapter:")
            print(f"  {self.recommended_alias}", end="")
            if self.recommended_desc:
                print(f" — {self.recommended_desc}")
            else:
                print()
        elif self.ambiguous:
            print(f"\nAdapter selection: AMBIGUOUS")
            print("  Plausible gateway-less adapters found:")
            for a in self.candidate_aliases:
                print(f"    - {a}")
            print("  Choose one and apply the settings below to it.")
        else:
            print("\nNo suitable adapter automatically identified.")
            print("Apply the settings below to your dedicated DCA Ethernet port.")

        if self.already_ready:
            print(f"\n  ✅ This adapter already appears correctly configured.")
            print(f"     IPv4 {self.host_ip}/{self.prefix_length} is present.")

        print(f"\nConfigure manually in Windows:")
        print(f"  (Settings → Network → adapter → IPv4 → Manual)")
        print(f"\n  IPv4 address : {self.host_ip or '(not configured)'}")
        print(f"  Prefix       : /{self.prefix_length}")
        print(f"  Subnet mask  : {self.subnet_mask}")
        print(f"  Gateway      : blank")
        print(f"  DNS          : blank")

        print(f"\nDCA1000:")
        print(f"  Device IP    : {self.dca_ip}")
        print(f"  Config UDP   : {self.config_port}")
        print(f"  Data UDP     : {self.data_port}")

        print(f"\nIMPORTANT:")
        print(f"  Use a DEDICATED adapter (USB-Ethernet adapter recommended).")
        print(f"  DO NOT apply these settings to an adapter carrying your")
        print(f"  normal network connection or default gateway.")
        print(f"\n  No network settings were changed.")

    def _repr_html_(self) -> str:
        if self.recommended_alias:
            adapter_html = (
                f"<p><b>Recommended adapter:</b> {self.recommended_alias}"
                + (f" &mdash; {self.recommended_desc}" if self.recommended_desc else "")
                + ("&nbsp;✅" if self.already_ready else "")
                + "</p>"
            )
        elif self.ambiguous:
            items = "".join(f"<li>{a}</li>" for a in self.candidate_aliases)
            adapter_html = (
                f"<p><b>Adapter selection: AMBIGUOUS</b><br>"
                f"Apply the settings below to one of:<ul>{items}</ul></p>"
            )
        else:
            adapter_html = "<p><em>No suitable adapter identified automatically. Apply settings to your dedicated DCA adapter.</em></p>"

        ready_note = (
            "<p style='color:#7fdbca'>✅ This adapter already appears correctly configured.</p>"
            if self.already_ready else ""
        )

        return (
            "<div style='font-family:monospace;padding:10px;border:1px solid #444;"
            "border-radius:4px;background:#1a1a2e;color:#e0e0e0'>"
            "<h3 style='margin:0 0 8px'>📋 DCA1000 Ethernet Setup Instructions</h3>"
            f"{adapter_html}{ready_note}"
            "<table style='border-collapse:collapse'>"
            f"<tr><td><b>IPv4 address</b></td><td><code>{self.host_ip or '(not configured)'}</code></td></tr>"
            f"<tr><td><b>Prefix</b></td><td><code>/{self.prefix_length}</code></td></tr>"
            f"<tr><td><b>Subnet mask</b></td><td><code>{self.subnet_mask}</code></td></tr>"
            "<tr><td><b>Gateway</b></td><td><em>blank</em></td></tr>"
            "<tr><td><b>DNS</b></td><td><em>blank</em></td></tr>"
            f"<tr><td><b>DCA device IP</b></td><td><code>{self.dca_ip}</code></td></tr>"
            f"<tr><td><b>Config UDP</b></td><td><code>{self.config_port}</code></td></tr>"
            f"<tr><td><b>Data UDP</b></td><td><code>{self.data_port}</code></td></tr>"
            "</table>"
            "<p style='color:#ffa07a'><b>⚠ Use a dedicated adapter. Do NOT apply these settings "
            "to an adapter carrying your normal network / default gateway.</b></p>"
            "<p style='color:#888;font-size:0.85em'>[No network settings were changed]</p>"
            "</div>"
        )


# ---------------------------------------------------------------------------
# Candidate ranking (pure logic — no PowerShell, no subprocesses)
# ---------------------------------------------------------------------------

def _prefix_to_mask(prefix: int) -> str:
    """Convert prefix length (0-32) to dotted-decimal subnet mask string."""
    if prefix <= 0:
        return "0.0.0.0"
    if prefix >= 32:
        return "255.255.255.255"
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return ".".join(str((bits >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _is_link_local(ip: str) -> bool:
    """Return True if *ip* is in the 169.254.0.0/16 link-local range."""
    try:
        parts = [int(x) for x in ip.split(".")]
        return len(parts) == 4 and parts[0] == 169 and parts[1] == 254
    except (ValueError, AttributeError):
        return False


def _is_loopback(ip: str) -> bool:
    """Return True if *ip* is a loopback address."""
    try:
        return ip.startswith("127.")
    except AttributeError:
        return False


def _is_wifi_or_virtual(alias: str, description: str, media_type: str) -> bool:
    """Return True if this adapter is Wi-Fi, VPN, tunnel, loopback, or virtual."""
    tokens = (alias + " " + description + " " + media_type).lower()
    skip_keywords = (
        "wi-fi", "wireless", "802.11", "vpn", "tunnel", "loopback",
        "virtual", "vmware", "hyper-v", "vethernet", "miniport",
        "bluetooth", "isatap", "teredo", "6to4",
    )
    return any(kw in tokens for kw in skip_keywords)


def rank_adapter_candidates(
    raw_adapters: list[dict],
    host_ip: str,
) -> list[AdapterCandidate]:
    """Rank raw adapter dicts by DCA suitability.

    Uses structural metadata — default route presence, link state, address
    family — not interface name strings.  Returns all adapters as
    :class:`AdapterCandidate` instances, sorted best-first.

    This function is **pure**: it never calls PowerShell or any OS API.
    """
    # Collect per-alias data: accumulate IPv4 addresses and prefix lengths.
    alias_data: dict[str, dict] = {}
    for row in raw_adapters:
        alias = row.get("InterfaceAlias", row.get("interface_alias", ""))
        if not alias:
            continue
        if alias not in alias_data:
            alias_data[alias] = {
                "alias":       alias,
                "description": row.get("_Description", row.get("description", "")),
                "link_state":  row.get("_Status", row.get("status", "")),
                "mac_address": row.get("_MacAddress", row.get("mac_address", "")),
                "has_gateway": row.get("_has_gateway", row.get("has_default_gateway", False)),
                "media_type":  row.get("MediaType", row.get("media_type", "")),
                "ipv4_addresses": [],
                "prefix_lengths": [],
            }
        ip      = row.get("IPAddress", row.get("ipv4", ""))
        af      = row.get("AddressFamily", 0)
        prefix  = row.get("PrefixLength", 0)
        # Only include IPv4 (AddressFamily=2) rows; skip IPv6
        if ip and (af == 2 or "." in ip):
            if not _is_loopback(ip):
                alias_data[alias]["ipv4_addresses"].append(ip)
                alias_data[alias]["prefix_lengths"].append(prefix)

    candidates: list[AdapterCandidate] = []

    for entry in alias_data.values():
        alias       = entry["alias"]
        desc        = entry["description"]
        media_type  = entry["media_type"]
        link_state  = entry["link_state"]
        has_gateway = bool(entry["has_gateway"])
        ips         = entry["ipv4_addresses"]
        prefixes    = entry["prefix_lengths"]
        mac         = entry["mac_address"]

        # Skip Wi-Fi, VPN, virtual, loopback
        if _is_wifi_or_virtual(alias, desc, media_type):
            confidence     = CONF_SKIP
            classification = "Wi-Fi / VPN / virtual — skip for DCA"
            candidates.append(AdapterCandidate(
                alias=alias, description=desc, ipv4_addresses=ips, prefix_lengths=prefixes,
                link_state=link_state, mac_address=mac, has_gateway=has_gateway,
                media_type=media_type, confidence=confidence, classification=classification,
            ))
            continue

        owns_host_ip = bool(host_ip and host_ip in ips)

        # Find the prefix length of the matching host IP row (for prefix validation)
        host_ip_prefix = -1
        if owns_host_ip and host_ip in ips:
            idx = ips.index(host_ip)
            host_ip_prefix = prefixes[idx] if idx < len(prefixes) else -1

        # Already correctly configured
        if owns_host_ip:
            confidence     = CONF_READY
            classification = f"already configured with host IP — ready"
        elif has_gateway:
            # Default-gateway adapter: flag as unsafe
            confidence     = CONF_UNSAFE
            classification = "normal network / DO NOT RECONFIGURE — has default gateway"
        else:
            # Gateway-less wired adapter — potential DCA candidate
            is_up = link_state.lower() == "up"
            if not is_up:
                confidence     = CONF_LOW
                classification = "gateway-less wired Ethernet — link down"
            elif any(_is_link_local(ip) for ip in ips):
                confidence     = CONF_HIGH
                classification = "likely dedicated DCA adapter (link-local IP, no gateway)"
            elif ips:
                confidence     = CONF_MEDIUM
                classification = "gateway-less wired Ethernet — may be DCA adapter"
            else:
                confidence     = CONF_MEDIUM
                classification = "gateway-less wired Ethernet — no IPv4 assigned"

        candidates.append(AdapterCandidate(
            alias=alias, description=desc, ipv4_addresses=ips, prefix_lengths=prefixes,
            link_state=link_state, mac_address=mac, has_gateway=has_gateway,
            media_type=media_type, confidence=confidence, classification=classification,
            owns_host_ip=owns_host_ip, host_ip_prefix=host_ip_prefix,
        ))

    # Sort: READY > HIGH > MEDIUM > LOW > UNSAFE > SKIP
    _order = {CONF_READY: 0, CONF_HIGH: 1, CONF_MEDIUM: 2, CONF_LOW: 3, CONF_UNSAFE: 4, CONF_SKIP: 5}
    candidates.sort(key=lambda c: _order.get(c.confidence, 99))
    return candidates


def pick_recommended(candidates: list[AdapterCandidate]) -> tuple[Optional[AdapterCandidate], bool]:
    """Return ``(recommended, ambiguous)`` from ranked candidates.

    - A READY candidate is always chosen first (unambiguous).
    - A single HIGH/MEDIUM candidate is returned.
    - Multiple HIGH/MEDIUM candidates → ambiguous, no selection.
    - UNSAFE/SKIP/LOW-only → no recommendation, not ambiguous.
    """
    ready = [c for c in candidates if c.confidence == CONF_READY]
    if ready:
        return ready[0], False

    good = [c for c in candidates if c.confidence in (CONF_HIGH, CONF_MEDIUM)]
    if len(good) == 1:
        return good[0], False
    if len(good) > 1:
        return None, True
    return None, False


# ---------------------------------------------------------------------------
# Main read-only API builders (called by EthernetManager)
# ---------------------------------------------------------------------------

def build_eth_status(cfg: "ProjectConfig", raw_adapters: list[dict]) -> EthernetStatusResult:
    """Build an :class:`EthernetStatusResult` from config + raw adapter data.

    This is a **pure function** — all PowerShell I/O is handled by the caller
    (EthernetManager).

    Args:
        cfg: ProjectConfig (source of truth for host_ip / dca_ip / ports).
        raw_adapters: List of raw adapter dicts from PowerShell.
    """
    host_ip     = cfg.local.host_ip or ""
    dca_ip      = cfg.portable.dca_ip
    config_port = cfg.portable.config_port
    data_port   = cfg.portable.data_port
    # Expected prefix length for the configured host IP (default /24)
    expected_prefix = getattr(cfg.portable, "host_ip_prefix", 24)
    # ProjectConfig doesn't expose host_ip_prefix; use 24 as the canonical default
    expected_prefix = 24

    candidates = rank_adapter_candidates(raw_adapters, host_ip)
    recommended, ambiguous = pick_recommended(candidates)

    # --- ready logic: IP present + correct prefix + no gateway on that adapter ---
    ip_adapter    = next((c for c in candidates if c.owns_host_ip), None)
    host_ip_present = ip_adapter is not None
    host_ip_adapter = ip_adapter.alias if ip_adapter else ""

    prefix_correct = (
        ip_adapter is not None
        and ip_adapter.host_ip_prefix == expected_prefix
    )
    # An adapter with the correct IP but a default gateway is still unsafe
    gateway_on_ip_adapter = ip_adapter is not None and ip_adapter.has_gateway

    # True only when all three conditions hold
    ready = host_ip_present and prefix_correct and not gateway_on_ip_adapter

    warnings: list[str] = []
    if not host_ip:
        warnings.append("host_ip is not configured in local.toml — run p.hardware.autodetect_serial(save=True) or edit .awr2944/local.toml")
    if host_ip_present and not prefix_correct and ip_adapter:
        actual = ip_adapter.host_ip_prefix
        warnings.append(
            f"Adapter '{ip_adapter.alias}' has host IP {host_ip} "
            f"but prefix /{actual} does not match expected /{expected_prefix} — "
            f"reconfigure to /{expected_prefix}"
        )
    if gateway_on_ip_adapter and ip_adapter:
        warnings.append(
            f"Adapter '{ip_adapter.alias}' has host IP {host_ip} "
            "but also has a default gateway — this is a normal network NIC, "
            "DO NOT use for DCA1000; assign the IP to a dedicated adapter"
        )
    unsafe_candidates = [c for c in candidates if c.confidence == CONF_UNSAFE and c is not ip_adapter]
    for c in unsafe_candidates:
        warnings.append(
            f"Adapter '{c.alias}' has a default gateway — "
            "DO NOT assign the DCA host IP to this adapter"
        )

    return EthernetStatusResult(
        host_ip_expected=host_ip,
        dca_ip=dca_ip,
        config_port=config_port,
        data_port=data_port,
        candidates=candidates,
        recommended=recommended,
        ambiguous=ambiguous,
        host_ip_present=host_ip_present,
        host_ip_adapter=host_ip_adapter,
        ready=ready,
        warnings=warnings,
    )


def build_eth_instructions(
    cfg: "ProjectConfig",
    status: EthernetStatusResult,
    prefix_length: int = 24,
) -> EthernetInstructionsResult:
    """Build :class:`EthernetInstructionsResult` from config + pre-computed status.

    This is a **pure function** — it does not run PowerShell.
    """
    host_ip     = cfg.local.host_ip or ""
    dca_ip      = cfg.portable.dca_ip
    config_port = cfg.portable.config_port
    data_port   = cfg.portable.data_port
    subnet_mask = _prefix_to_mask(prefix_length)

    # Derive candidate aliases for ambiguous case
    plausible    = [c for c in status.candidates if c.confidence in (CONF_HIGH, CONF_MEDIUM)]
    candidate_aliases = [c.alias for c in plausible]

    rec = status.recommended
    return EthernetInstructionsResult(
        host_ip=host_ip,
        prefix_length=prefix_length,
        subnet_mask=subnet_mask,
        dca_ip=dca_ip,
        config_port=config_port,
        data_port=data_port,
        recommended_alias=rec.alias if rec else None,
        recommended_desc=rec.description if rec else None,
        ambiguous=status.ambiguous,
        already_ready=status.ready,
        candidate_aliases=candidate_aliases,
    )
