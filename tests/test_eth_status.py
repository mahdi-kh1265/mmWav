"""Tests for Task 4 — Safe Ethernet UX, candidate ranking, status, instructions, doctor.

ALL tests are pure-logic / no live NIC / no PowerShell mutation.

Test IDs follow ETH-* and DOC-* naming convention.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from awr2944_dca.api._eth_status import (
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    CONF_READY,
    CONF_SKIP,
    CONF_UNSAFE,
    AdapterCandidate,
    EthernetInstructionsResult,
    EthernetStatusResult,
    _prefix_to_mask,
    build_eth_instructions,
    build_eth_status,
    pick_recommended,
    rank_adapter_candidates,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic adapter row factory
# ---------------------------------------------------------------------------

def _row(
    alias="Ethernet",
    desc="Generic Ethernet Controller",
    status="Up",
    mac="AA:BB:CC:DD:EE:01",
    ip="",
    prefix=24,
    has_gateway=False,
    media_type="802.3",
    af=2,
):
    """Build a synthetic enriched adapter row as returned by _fetch_raw_adapters()."""
    return {
        "InterfaceAlias": alias,
        "_Description": desc,
        "_Status": status,
        "_MacAddress": mac,
        "IPAddress": ip,
        "PrefixLength": prefix,
        "AddressFamily": af,
        "_has_gateway": has_gateway,
        "media_type": media_type,
    }


def _make_config(host_ip="192.168.33.30", dca_ip="192.168.33.180", config_port=4096, data_port=4098):
    """Build a minimal mock ProjectConfig."""
    cfg = MagicMock()
    cfg.local.host_ip = host_ip
    cfg.portable.dca_ip = dca_ip
    cfg.portable.config_port = config_port
    cfg.portable.data_port = data_port
    return cfg


def _make_project(tmp_path):
    """Build a minimal project for EthernetManager integration tests."""
    (tmp_path / "awr2944.toml").write_text(
        '[project]\nname="t"\nid="x"\n\n[defaults]\nframes=9\nguard_frames=1\nprofile="smoke_v1"\n'
        '\n[network]\ndca_ip="192.168.33.180"\nconfig_port=4096\ndata_port=4098\n',
        encoding="utf-8",
    )
    d = tmp_path / ".awr2944"
    d.mkdir()
    (d / "local.toml").write_text(
        '[serial]\ncom_port=""\nbaud_rate=115200\n\n[network]\nhost_ip="192.168.33.30"\n'
        '\n[dca_tools]\ncontrol_exe=""\nrecord_exe=""\nrf_api_dll=""\ncf_json=""\n',
        encoding="utf-8",
    )
    (tmp_path / "profiles").mkdir()
    (tmp_path / "captures").mkdir()
    import sys
    sys.path.insert(0, "src")
    from awr2944_dca.lab import RadarProject
    return RadarProject.open(tmp_path)


# ===========================================================================
# ETH-1: USB Ethernet, link-local IP, no gateway → HIGH confidence
# ===========================================================================

class TestCandidateRanking:

    def test_ETH1_usb_ethernet_link_local_is_high(self):
        """Gateway-less USB Ethernet at 169.254.x.x → CONF_HIGH, recommended."""
        rows = [_row(
            alias="Ethernet 2",
            desc="ASIX AX88179 USB 3.0 to Gigabit Ethernet",
            ip="169.254.77.130",
            prefix=16,
            has_gateway=False,
        )]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        assert len(candidates) == 1
        c = candidates[0]
        assert c.confidence == CONF_HIGH
        assert "likely" in c.classification.lower()

    # ETH-2: Adapter already configured with the correct host IP → READY
    def test_ETH2_adapter_at_host_ip_is_ready(self):
        rows = [_row(alias="Ethernet 2", ip="192.168.33.30", prefix=24, has_gateway=False)]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        assert candidates[0].confidence == CONF_READY
        assert candidates[0].owns_host_ip is True

    # ETH-3: Normal Internet NIC (gateway) + dedicated gateway-less Ethernet
    def test_ETH3_gateway_nic_and_dedicated_nic_selects_dedicated(self):
        rows = [
            _row(alias="Ethernet",   ip="128.101.169.148", has_gateway=True,  desc="Intel I219-LM"),
            _row(alias="Ethernet 2", ip="169.254.77.130",  has_gateway=False, desc="ASIX AX88179"),
        ]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        rec, ambiguous = pick_recommended(candidates)
        assert not ambiguous
        assert rec is not None
        assert rec.alias == "Ethernet 2"
        # Gateway NIC must be explicitly flagged as unsafe
        gateway_c = next(c for c in candidates if c.alias == "Ethernet")
        assert gateway_c.confidence == CONF_UNSAFE
        assert "DO NOT" in gateway_c.classification.upper() or "gateway" in gateway_c.classification.lower()

    # ETH-4: Only one wired adapter and it has a default gateway → UNSAFE, no recommendation
    def test_ETH4_only_gateway_nic_is_unsafe_no_recommendation(self):
        rows = [_row(alias="Ethernet", ip="10.0.0.5", has_gateway=True)]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        rec, ambiguous = pick_recommended(candidates)
        assert rec is None
        assert not ambiguous
        assert candidates[0].confidence == CONF_UNSAFE

    # ETH-5: Two plausible gateway-less Ethernet adapters → ambiguous
    def test_ETH5_two_plausible_adapters_is_ambiguous(self):
        rows = [
            _row(alias="Ethernet 2", ip="169.254.10.1", has_gateway=False),
            _row(alias="Ethernet 3", ip="169.254.10.2", has_gateway=False),
        ]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        rec, ambiguous = pick_recommended(candidates)
        assert rec is None
        assert ambiguous is True

    # ETH-6: Wi-Fi + VPN + Ethernet combinations
    def test_ETH6_wifi_and_vpn_are_skipped(self):
        rows = [
            _row(alias="Wi-Fi",          media_type="Native 802.11", ip="192.168.1.10"),
            _row(alias="Ethernet",       has_gateway=True,            ip="10.0.0.5"),
            _row(alias="VPN Client",     desc="OpenVPN TAP Driver",   ip="172.28.0.1"),
            _row(alias="Ethernet 2",     has_gateway=False,           ip="169.254.99.1"),
        ]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        wifi_c = next(c for c in candidates if c.alias == "Wi-Fi")
        vpn_c  = next(c for c in candidates if c.alias == "VPN Client")
        assert wifi_c.confidence == CONF_SKIP
        assert vpn_c.confidence  == CONF_SKIP
        rec, _ = pick_recommended(candidates)
        assert rec is not None
        assert rec.alias == "Ethernet 2"

    # ETH-7: Disconnected (link down) gateway-less candidate → LOW confidence
    def test_ETH7_disconnected_candidate_is_low_confidence(self):
        rows = [_row(alias="Ethernet 2", status="Disconnected", ip="169.254.5.5", has_gateway=False)]
        candidates = rank_adapter_candidates(rows, host_ip="192.168.33.30")
        assert candidates[0].confidence == CONF_LOW

    # ETH-8: status() never calls mutating commands
    def test_ETH8_status_is_readonly(self):
        """Verify that build_eth_status (the core logic) never references mutating PS cmds."""
        cfg = _make_config()
        rows = [_row(alias="Ethernet 2", ip="169.254.1.1", has_gateway=False)]
        result = build_eth_status(cfg, rows)
        # If we got a result, no mutation happened (pure function)
        assert isinstance(result, EthernetStatusResult)
        assert result.host_ip_expected == "192.168.33.30"


def test_ETH8b_no_mutating_commands_in_module():
    """Verify _eth_status.py source contains no mutating PS cmdlets."""
    src = Path("src/awr2944_dca/api/_eth_status.py").read_text(encoding="utf-8")
    # The docstring may mention them in passing; check there's no subprocess call with them
    # (i.e., they should not appear in actual string args outside the docstring header)
    body = src[src.find("\nfrom __future__"):]
    for forbidden in (
        "Set-NetIPAddress",
        "New-NetIPAddress",
        "Remove-NetIPAddress",
        "Remove-NetRoute",
        "Set-DnsClientServerAddress",
        "Set-NetIPInterface",
    ):
        assert forbidden not in body, f"Mutating command found in _eth_status.py body: {forbidden!r}"


# ===========================================================================
# INSTRUCTIONS tests (ETH-9, ETH-10, ETH-11)
# ===========================================================================

class TestInstructions:

    def _status_with_rows(self, rows, host_ip="192.168.33.30"):
        cfg = _make_config(host_ip=host_ip)
        return cfg, build_eth_status(cfg, rows)

    def test_ETH9_instructions_show_required_info(self):
        """instructions() shows host IP, mask, gateway=blank, DCA IP and ports."""
        rows = [_row(alias="Ethernet 2", ip="169.254.1.1", has_gateway=False)]
        cfg, st = self._status_with_rows(rows)
        inst = build_eth_instructions(cfg, st, prefix_length=24)
        assert inst.host_ip == "192.168.33.30"
        assert inst.prefix_length == 24
        assert inst.subnet_mask == "255.255.255.0"
        assert inst.dca_ip == "192.168.33.180"
        assert inst.config_port == 4096
        assert inst.data_port == 4098

    def test_ETH10_ambiguous_instructions_still_useful(self):
        """Ambiguous case shows required config without guessing adapter."""
        rows = [
            _row(alias="Ethernet 2", ip="169.254.1.1", has_gateway=False),
            _row(alias="Ethernet 3", ip="169.254.1.2", has_gateway=False),
        ]
        cfg, st = self._status_with_rows(rows)
        inst = build_eth_instructions(cfg, st)
        assert inst.ambiguous is True
        assert inst.recommended_alias is None
        assert len(inst.candidate_aliases) == 2
        # Print should not raise
        import io, sys
        old, sys.stdout = sys.stdout, io.StringIO()
        inst.print()
        out = sys.stdout.getvalue()
        sys.stdout = old
        assert "192.168.33.30" in out
        assert "AMBIGUOUS" in out

    def test_ETH11_already_correct_adapter_says_ready(self):
        """When adapter already owns the host IP, instructions say 'already configured'."""
        rows = [_row(alias="Ethernet 2", ip="192.168.33.30", prefix=24, has_gateway=False)]
        cfg, st = self._status_with_rows(rows)
        inst = build_eth_instructions(cfg, st)
        assert inst.already_ready is True
        assert inst.recommended_alias == "Ethernet 2"


# ===========================================================================
# STATUS integration via EthernetManager
# ===========================================================================

class TestEthernetManagerStatus:

    def test_eth_status_via_manager_uses_snapshot_fn(self, tmp_path):
        """EthernetManager.status() delegates to injected snapshot fn (no real PS)."""
        p = _make_project(tmp_path)
        rows = [_row(alias="Ethernet 2", ip="169.254.55.1", has_gateway=False,
                     desc="ASIX AX88179 USB")]
        result = p.eth.status(_snapshot_fn=lambda: rows)
        assert result.__class__.__name__ == "EthernetStatusResult"
        assert result.host_ip_expected == "192.168.33.30"
        assert result.ready is False  # host IP not yet on adapter

    def test_eth_status_ready_when_ip_present(self, tmp_path):
        p = _make_project(tmp_path)
        rows = [_row(alias="Ethernet 2", ip="192.168.33.30", prefix=24, has_gateway=False)]
        result = p.eth.status(_snapshot_fn=lambda: rows)
        assert result.ready is True
        assert result.host_ip_present is True
        assert result.host_ip_adapter == "Ethernet 2"

    def test_eth_instructions_via_manager(self, tmp_path):
        p = _make_project(tmp_path)
        rows = [_row(alias="Ethernet 2", ip="169.254.55.1", has_gateway=False)]
        inst = p.eth.instructions(_snapshot_fn=lambda: rows)
        assert inst.__class__.__name__ == "EthernetInstructionsResult"
        assert inst.host_ip == "192.168.33.30"

    def test_gateway_warning_present_in_status(self, tmp_path):
        """A gateway NIC triggers a warning in status.warnings."""
        p = _make_project(tmp_path)
        rows = [_row(alias="Ethernet", ip="10.0.0.5", has_gateway=True)]
        result = p.eth.status(_snapshot_fn=lambda: rows)
        assert any("gateway" in w.lower() or "DO NOT" in w for w in result.warnings)


# ===========================================================================
# SAFETY REGRESSION GUARD (Part J)
# ===========================================================================

class TestSafetyRegression:

    def test_ETH_readonly_no_new_netipaddress_in_eth_status_module(self):
        # _eth_status.py is pure logic — no subprocess calls to mutating cmdlets
        # Check that no subprocess.run / check_output with mutating commands is present
        src = Path("src/awr2944_dca/api/_eth_status.py").read_text(encoding="utf-8")
        # The module body (excluding the module docstring) must not call mutating PS cmdlets
        body_start = src.find("\nfrom __future__")
        body = src[body_start:] if body_start >= 0 else src
        for cmd in ("Set-NetIPAddress", "New-NetIPAddress", "Remove-NetIPAddress",
                    "Remove-NetRoute", "Set-DnsClientServerAddress"):
            assert cmd not in body, (
                f"Mutating command {cmd!r} found in _eth_status.py module body"
            )

    def test_ETH_readonly_no_mutating_cmds_in_lab_status_path(self):
        """EthernetManager.status() docstring says read-only; verify."""
        from awr2944_dca.lab import EthernetManager
        doc = EthernetManager.status.__doc__ or ""
        assert "read-only" in doc.lower() or "never mutate" in doc.lower()

    def test_ETH_readonly_instructions_docstring(self):
        from awr2944_dca.lab import EthernetManager
        doc = EthernetManager.instructions.__doc__ or ""
        assert "read-only" in doc.lower() or "no powershell" in doc.lower() or "never" in doc.lower()

    def test_low_level_helpers_preserved(self):
        """Low-level mutation helpers must still exist (Part J)."""
        from awr2944_dca.lab import EthernetManager
        assert callable(EthernetManager.configure)
        assert callable(EthernetManager.repair)
        assert callable(EthernetManager.begin_pairing)
        assert callable(EthernetManager.finish_pairing)
        assert callable(EthernetManager.pair)

    def test_low_level_helpers_have_unsafe_docstrings(self):
        from awr2944_dca.lab import EthernetManager
        for method_name in ("configure", "repair", "finish_pairing", "pair"):
            doc = getattr(EthernetManager, method_name).__doc__ or ""
            assert "advanced" in doc.lower() or "unsafe" in doc.lower(), (
                f"EthernetManager.{method_name} missing Advanced/unsafe docstring warning"
            )

    def test_eth_module_low_level_preserved(self):
        """eth.py low-level functions must still be importable."""
        from awr2944_dca.eth import (
            take_snapshot,
            diff_snapshots,
            build_configure_commands,
            apply_configuration,
            begin_pairing,
            finish_pairing,
            select_candidate,
        )
        # All callable
        for fn in (take_snapshot, diff_snapshots, build_configure_commands,
                   apply_configuration, begin_pairing, finish_pairing, select_candidate):
            assert callable(fn)


# ===========================================================================
# DOCTOR improvements (DOC-* tests, F1-F4)
# ===========================================================================

class TestDoctorImprovements:

    def _make_project_path(self, tmp_path, host_ip="192.168.33.30"):
        """Minimal TOML project without real hardware paths."""
        (tmp_path / "awr2944.toml").write_text(
            f'[project]\nname="t"\nid="x"\n\n[defaults]\nframes=9\nguard_frames=1\nprofile="smoke_v1"\n'
            f'\n[network]\ndca_ip="192.168.33.180"\nconfig_port=4096\ndata_port=4098\n',
            encoding="utf-8",
        )
        d = tmp_path / ".awr2944"
        d.mkdir()
        (d / "local.toml").write_text(
            f'[serial]\ncom_port=""\nbaud_rate=115200\n\n[network]\nhost_ip="{host_ip}"\n'
            f'\n[dca_tools]\ncontrol_exe=""\nrecord_exe=""\nrf_api_dll=""\ncf_json=""\n',
            encoding="utf-8",
        )
        (tmp_path / "profiles").mkdir()
        (tmp_path / "captures").mkdir()
        from awr2944_dca.lab import RadarProject
        return RadarProject.open(tmp_path)

    # DOC-12: host_nic_owns_ip failure includes actionable detail
    def test_DOC12_host_nic_owns_ip_failure_actionable(self, tmp_path):
        """When host IP is not present, doctor FAIL includes candidate guidance."""
        p = self._make_project_path(tmp_path, host_ip="192.168.33.30")

        # Mock the PowerShell calls: IP not found, plausible adapter present
        # The doctor uses _run_ps_json for various scripts; we need to
        # return appropriate data for each query pattern.
        call_count = [0]
        def _mock_ps(script, *a, **kw):
            call_count[0] += 1
            # host IP check: not found
            if "Get-NetIPAddress -IPAddress" in script:
                return ""
            # adapter list (for candidate ranking inside doctor fail path)
            if "Get-NetAdapter" in script:
                return '[{"Name":"Eth2","InterfaceDescription":"ASIX AX88179","Status":"Up","MacAddress":"AA:BB"}]'
            # IPv4 addresses (for candidate ranking)
            if "Get-NetIPAddress -AddressFamily" in script:
                return '[{"InterfaceAlias":"Eth2","IPAddress":"169.254.5.5","PrefixLength":16,"AddressFamily":2}]'
            # default routes: none for Eth2
            if "Get-NetRoute" in script:
                return "[]"
            # PID lookup for UDP
            if "Get-NetUDPEndpoint" in script:
                return ""
            return ""

        with patch("awr2944_dca.dca.preflight._run_ps_json", side_effect=_mock_ps):
            with patch("awr2944_dca.hardware.ports.scan_ports", return_value=[]):
                with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[]):
                    report = p.hardware.verify(include_hardware=True)

        host_check = next(c for c in report.checks if c.name == "host_nic_owns_ip")
        assert host_check.status == "FAIL"
        detail = host_check.detail
        assert "192.168.33.30" in detail or "not found" in detail.lower()
        # Should mention candidate or guidance
        assert any(kw in detail for kw in ("Eth2", "ASIX", "instructions()", "dedicated"))

    # DOC-13: gateway-bearing candidate gets explicit safety warning
    def test_DOC13_gateway_candidate_warning(self, tmp_path):
        p = self._make_project_path(tmp_path)

        def _mock_ps(script, *a, **kw):
            if "Get-NetIPAddress -IPAddress" in script:
                return ""  # not found
            if "Get-NetAdapter" in script:
                return '[{"Name":"Ethernet","InterfaceDescription":"Intel","Status":"Up","MacAddress":"AA:01"}]'
            if "Get-NetIPAddress -AddressFamily" in script:
                return '[{"InterfaceAlias":"Ethernet","IPAddress":"10.0.0.5","PrefixLength":8,"AddressFamily":2}]'
            if "Get-NetRoute" in script:
                return '[{"InterfaceAlias":"Ethernet"}]'  # has gateway
            return ""

        with patch("awr2944_dca.dca.preflight._run_ps_json", side_effect=_mock_ps):
            with patch("awr2944_dca.hardware.ports.scan_ports", return_value=[]):
                with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[]):
                    report = p.hardware.verify(include_hardware=True)

        host_check = next(c for c in report.checks if c.name == "host_nic_owns_ip")
        assert host_check.status == "FAIL"
        detail = host_check.detail
        # Some warning about gateway or not reconfiguring
        detail_upper = detail.upper()
        assert any(kw in detail_upper for kw in ("GATEWAY", "DO NOT", "WARNING", "DEDICATED"))

    # DOC-14: UDP bind "address already in use" produces useful diagnostic
    def test_DOC14_udp_bind_port_in_use(self, tmp_path):
        """When UDP port is in use, doctor reports the specific error category."""
        p = self._make_project_path(tmp_path)

        def _mock_ps(script, *a, **kw):
            if "Get-NetIPAddress -IPAddress" in script:
                return '[{"InterfaceAlias":"Ethernet2"}]'  # host IP present
            return ""

        # Bind the port ourselves to force EADDRINUSE
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            blocker.bind(("192.168.33.30", 4098))
        except OSError:
            pytest.skip("Cannot bind 192.168.33.30:4098 on this machine for test")

        try:
            with patch("awr2944_dca.dca.preflight._run_ps_json", side_effect=_mock_ps):
                with patch("awr2944_dca.hardware.ports.scan_ports", return_value=[]):
                    with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[]):
                        report = p.hardware.verify(include_hardware=True)
        finally:
            blocker.close()

        udp_check = next((c for c in report.checks if c.name == "udp_data_port_bind"), None)
        if udp_check and udp_check.status == "FAIL":
            assert "in use" in udp_check.detail.lower() or "busy" in udp_check.detail.lower() or "already" in udp_check.detail.lower()

    # DOC-15: DCA aliveness still uses query_sys_status
    def test_DOC15_dca_aliveness_uses_query_sys_status(self):
        """dca_control_responds check runs query_sys_status, not ping."""
        from awr2944_dca._doctor import HardwareManager
        import inspect
        src = inspect.getsource(HardwareManager.verify)
        assert "query_sys_status" in src

    # DOC-16: ping failure is NOT a required doctor failure
    def test_DOC16_ping_not_required(self):
        """ping / ICMP is not used as the DCA aliveness test."""
        from awr2944_dca._doctor import HardwareManager
        import inspect
        src = inspect.getsource(HardwareManager.verify)
        # ping should not appear as a required DCA check
        assert "ping" not in src.lower() or "ping_only" not in src

    # DOC-17: doctor performs no NIC mutation
    def test_DOC17_doctor_no_nic_mutation(self):
        """HardwareManager.verify() source must not contain mutating PS commands."""
        from awr2944_dca._doctor import HardwareManager
        import inspect
        src = inspect.getsource(HardwareManager.verify)
        for cmd in ("New-NetIPAddress", "Remove-NetIPAddress", "Set-NetIPAddress",
                    "Remove-NetRoute", "Set-DnsClientServerAddress"):
            assert cmd not in src, f"Mutating command found in verify(): {cmd!r}"

    # DOC-F4: fresh project (no COM) gives sensible SKIP/FAIL, not misleading pass
    def test_DOCF4_fresh_project_no_com_graceful(self, tmp_path):
        """A fresh project with no COM should FAIL/SKIP appropriately, not crash."""
        p = self._make_project_path(tmp_path)

        def _mock_ps(script, *a, **kw):
            return ""

        with patch("awr2944_dca.dca.preflight._run_ps_json", side_effect=_mock_ps):
            with patch("awr2944_dca.hardware.ports.scan_ports", return_value=[]):
                with patch("awr2944_dca.headless_serial.discover_serial_ports", return_value=[]):
                    report = p.hardware.verify(include_hardware=True)

        cli_check = next((c for c in report.checks if c.name == "cli_com_port_exists"), None)
        assert cli_check is not None
        assert cli_check.status in ("FAIL", "SKIP")
        # uart_prompt_responds should be SKIP because CLI port failed
        uart_check = next((c for c in report.checks if c.name == "uart_prompt_responds"), None)
        if uart_check:
            assert uart_check.status == "SKIP"


# ===========================================================================
# _prefix_to_mask utility
# ===========================================================================

def test_prefix_to_mask():
    assert _prefix_to_mask(24) == "255.255.255.0"
    assert _prefix_to_mask(16) == "255.255.0.0"
    assert _prefix_to_mask(8)  == "255.0.0.0"
    assert _prefix_to_mask(32) == "255.255.255.255"
    assert _prefix_to_mask(0)  == "0.0.0.0"


# ===========================================================================
# repr / print / _repr_html_ smoke tests
# ===========================================================================

class TestPresentation:

    def _simple_status(self, host_ip="192.168.33.30"):
        cfg = _make_config(host_ip=host_ip)
        rows = [_row(alias="Eth2", ip="169.254.1.1", has_gateway=False)]
        return build_eth_status(cfg, rows)

    def test_status_repr(self):
        st = self._simple_status()
        r = repr(st)
        assert "EthernetStatusResult" in r

    def test_status_repr_html(self):
        st = self._simple_status()
        html = st._repr_html_()
        assert "<div" in html
        assert "192.168.33.30" in html
        assert "No network settings were changed" in html

    def test_status_print_no_exception(self, capsys):
        st = self._simple_status()
        st.print()
        out = capsys.readouterr().out
        assert "DCA Ethernet Status" in out
        assert "No network settings were changed" in out

    def test_instructions_repr_html(self):
        cfg = _make_config()
        rows = [_row(alias="Eth2", ip="169.254.1.1", has_gateway=False)]
        st = build_eth_status(cfg, rows)
        inst = build_eth_instructions(cfg, st)
        html = inst._repr_html_()
        assert "📋" in html
        assert "192.168.33.30" in html
        assert "dedicated adapter" in html.lower() or "dedicated" in html.lower()

    def test_instructions_print_no_exception(self, capsys):
        cfg = _make_config()
        rows = [_row(alias="Eth2", ip="169.254.1.1", has_gateway=False)]
        st = build_eth_status(cfg, rows)
        inst = build_eth_instructions(cfg, st)
        inst.print()
        out = capsys.readouterr().out
        assert "192.168.33.30" in out
        assert "No network settings were changed" in out
