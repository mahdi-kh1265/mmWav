"""Tests for RadarProject.init_here() / RadarProject.init() (Task 1).

All tests use tmp_path. No hardware, no network, no real serial ports.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from awr2944_dca.lab import RadarProject
from awr2944_dca._project import _is_source_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_project(p: Path) -> bool:
    """A directory is a valid project when it has awr2944.toml or project.json."""
    return (p / "awr2944.toml").exists() or (p / "project.json").exists()


# ===========================================================================
# Part A: init_here / init
# ===========================================================================

class TestInitHere:
    """RadarProject.init_here() and RadarProject.init(path)."""

    def test_init_here_creates_scaffolding_in_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = RadarProject.init_here()
        assert _is_valid_project(tmp_path)
        assert (tmp_path / "awr2944.toml").exists()
        assert (tmp_path / ".awr2944" / "local.toml").exists()
        assert (tmp_path / "profiles").is_dir()
        assert (tmp_path / "captures").is_dir()
        assert (tmp_path / "scripts").is_dir()
        assert (tmp_path / "notebooks").is_dir()
        assert type(p).__name__ == "RadarProject"
        assert p.root == tmp_path

    def test_init_creates_scaffolding_in_explicit_dir(self, tmp_path):
        exp_dir = tmp_path / "my_experiment"
        exp_dir.mkdir()
        p = RadarProject.init(exp_dir)
        assert _is_valid_project(exp_dir)
        assert (exp_dir / "awr2944.toml").exists()
        assert type(p).__name__ == "RadarProject"
        assert p.root == exp_dir

    def test_init_here_idempotent_already_initialized(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        RadarProject.init_here()
        toml_before = (tmp_path / "awr2944.toml").read_bytes()
        RadarProject.init_here()
        toml_after = (tmp_path / "awr2944.toml").read_bytes()
        assert toml_before == toml_after, "init_here() must not overwrite existing awr2944.toml"

    def test_init_preserves_unrelated_existing_files(self, tmp_path, monkeypatch):
        sentinel = tmp_path / "my_existing_notebook.ipynb"
        sentinel.write_text("existing content", encoding="utf-8")
        data_dir = tmp_path / "my_data"
        data_dir.mkdir()
        (data_dir / "run1.bin").write_bytes(b"\xde\xad\xbe\xef")
        monkeypatch.chdir(tmp_path)
        RadarProject.init_here()
        assert sentinel.read_text(encoding="utf-8") == "existing content"
        assert (data_dir / "run1.bin").read_bytes() == b"\xde\xad\xbe\xef"

    def test_open_here_works_after_init_here(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        RadarProject.init_here()
        p = RadarProject.open_here()
        assert p.root == tmp_path

    def test_init_idempotent_on_create_project(self, tmp_path):
        existing = RadarProject.create(name="exp_alpha", parent=tmp_path)
        exp_dir = existing.root
        toml_before = (exp_dir / "awr2944.toml").read_bytes()
        p = RadarProject.init(exp_dir)
        toml_after = (exp_dir / "awr2944.toml").read_bytes()
        assert toml_before == toml_after
        assert p.root == exp_dir

    def test_init_does_not_create_missing_directory(self, tmp_path):
        nonexistent = tmp_path / "ghost_dir"
        with pytest.raises(FileNotFoundError, match="does not exist"):
            RadarProject.init(nonexistent)

    def test_init_existing_apis_still_work(self, tmp_path):
        p = RadarProject.create(name="reg_test", parent=tmp_path)
        root = p.root
        assert (root / "awr2944.toml").exists()
        p2 = RadarProject.open(root)
        assert p2.root == root

    def test_init_here_gitignore_appended_not_overwritten(self, tmp_path, monkeypatch):
        gi = tmp_path / ".gitignore"
        gi.write_text("# My project rules\n*.log\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        RadarProject.init_here()
        content = gi.read_text(encoding="utf-8")
        assert "*.log" in content
        assert "# My project rules" in content
        assert ".awr2944/local.toml" in content

    def test_init_here_gitignore_not_duplicated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        RadarProject.init_here()
        RadarProject.init_here()
        gi = tmp_path / ".gitignore"
        content = gi.read_text(encoding="utf-8")
        assert content.count(".awr2944/local.toml") == 1


# ===========================================================================
# Source-repo guard
# ===========================================================================

class TestSourceRepoGuard:

    def test_source_repo_detection_positive(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "awr2944-dca-lab"\n', encoding="utf-8"
        )
        assert _is_source_repo(tmp_path) is True

    def test_source_repo_detection_negative_different_name(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "my-experiment-project"\n', encoding="utf-8"
        )
        assert _is_source_repo(tmp_path) is False

    def test_source_repo_detection_negative_no_src(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "awr2944-dca-lab"\n', encoding="utf-8"
        )
        assert _is_source_repo(tmp_path) is False

    def test_source_repo_detection_negative_no_pyproject(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert _is_source_repo(tmp_path) is False

    def test_init_raises_on_source_repo(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "awr2944-dca-lab"\n', encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="source repository"):
            RadarProject.init(tmp_path)

    def test_init_here_raises_on_source_repo(self, tmp_path, monkeypatch):
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "awr2944-dca-lab"\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="source repository"):
            RadarProject.init_here()


# ===========================================================================
# Part B: discover() filter and presentation (no hardware mutations)
# ===========================================================================

class TestDiscoverFilter:

    @pytest.fixture
    def project_with_mocked_hardware(self, monkeypatch, tmp_path):
        from awr2944_dca.headless_serial import SerialPortInfo
        from awr2944_dca.hardware.ports import PortInfo

        fake_serial = [
            SerialPortInfo(
                port="COM8",
                name="XDS110 Application/User UART (COM8)",
                description="XDS110 Application/User UART (COM8)",
                status="OK",
                instance_id="USB_VID_0451_PID_BEF3_0001",
                vid="0451",
                pid="BEF3",
                is_xds110=True,
                role="application_user",
            ),
        ]
        fake_com = [
            PortInfo(
                com="COM8",
                friendly_name="XDS110 Application/User UART (COM8)",
                manufacturer="Texas Instruments",
                hwid="USB VID:PID=0451:BEF3",
                likely_role="awr_xds_uart_candidate",
                confidence="high",
                reason="Matches AWR2944 XDS.",
            ),
        ]
        fake_net = [
            {
                "InterfaceAlias": "Ethernet 2",
                "IPAddress": "192.168.33.30",
                "PrefixLength": 24,
                "AddressFamily": 2,
            }
        ]

        monkeypatch.setattr(
            "awr2944_dca.headless_serial.discover_serial_ports",
            lambda: fake_serial,
        )
        monkeypatch.setattr(
            "awr2944_dca.hardware.ports.scan_ports",
            lambda: fake_com,
        )
        monkeypatch.setattr(
            "awr2944_dca.dca.preflight._run_ps_json",
            lambda script: fake_net,
        )
        monkeypatch.setattr(
            "awr2944_dca.dca.preflight._as_dicts",
            lambda v: v if isinstance(v, list) else ([v] if isinstance(v, dict) else []),
        )
        project = RadarProject.create(name="disc_test", parent=tmp_path)
        return project

    def test_discover_no_args_returns_all(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover()
        assert len(report.serial_ports) == 1
        assert len(report.com_ports) == 1
        assert len(report.network_adapters) == 1

    def test_discover_serial_filter(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover("serial")
        assert len(report.serial_ports) == 1
        assert len(report.com_ports) == 1
        assert len(report.network_adapters) == 0

    def test_discover_com_filter_alias(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover("com")
        assert len(report.com_ports) == 1
        assert len(report.network_adapters) == 0

    def test_discover_network_filter(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover("network")
        assert len(report.network_adapters) == 1
        assert len(report.serial_ports) == 0
        assert len(report.com_ports) == 0

    def test_discover_invalid_filter_raises(self, project_with_mocked_hardware):
        with pytest.raises(ValueError, match="Unknown filter"):
            project_with_mocked_hardware.hardware.discover("bluetooth")

    def test_discover_no_hardware_mutation(self, project_with_mocked_hardware):
        mutation_calls: list = []
        project_with_mocked_hardware.hardware.discover()
        assert mutation_calls == []

    def test_discover_repr_contains_counts(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover()
        r = repr(report)
        assert "serial=" in r
        assert "network=" in r
        assert "XDS110" in r

    def test_discover_html_contains_tables(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover()
        html = report._repr_html_()
        assert "<table" in html
        assert "COM8" in html
        assert "192.168.33.30" in html

    def test_discover_print_runs_without_error(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover()
        report.print()  # must not raise

    def test_discover_print_serial_filter(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover("serial")
        report.print("serial")

    def test_discover_print_network_filter(self, project_with_mocked_hardware):
        report = project_with_mocked_hardware.hardware.discover("network")
        report.print("network")


# ===========================================================================
# Regression: existing create/open APIs unbroken
# ===========================================================================

class TestExistingAPIRegression:

    def test_create_still_works(self, tmp_path):
        p = RadarProject.create(name="regr_create", parent=tmp_path)
        assert (tmp_path / "regr_create" / "awr2944.toml").exists()

    def test_create_at_still_works(self, tmp_path):
        target = tmp_path / "at_target"
        p = RadarProject.create_at(target)
        assert (target / "awr2944.toml").exists()

    def test_open_still_works(self, tmp_path):
        RadarProject.create(name="regr_open", parent=tmp_path)
        p = RadarProject.open(tmp_path / "regr_open")
        assert p.root == tmp_path / "regr_open"

    def test_open_here_still_works(self, tmp_path, monkeypatch):
        RadarProject.create(name="regr_openhere", parent=tmp_path)
        monkeypatch.chdir(tmp_path / "regr_openhere")
        p = RadarProject.open_here()
        assert p.root == tmp_path / "regr_openhere"
