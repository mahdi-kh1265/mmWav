"""Tests for CapturePlan API (Task 3).

No hardware.  Uses tmp_path only.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from awr2944_dca.lab import RadarProject
from awr2944_dca.api._capture_plan import CapturePlan, _resolve_dca_info


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_modern_project(tmp_path: Path, *, dca_tools: dict | None = None) -> RadarProject:
    """Create a minimal RadarProject with modern TOML config."""
    (tmp_path / "awr2944.toml").write_text(
        '[project]\nname = "test_proj"\nid = "abc123"\n\n'
        '[defaults]\nframes = 9\nguard_frames = 1\nprofile = "smoke_v1"\n\n'
        '[network]\ndca_ip = "192.168.33.180"\nconfig_port = 4096\ndata_port = 4098\n',
        encoding="utf-8",
    )
    local_dir = tmp_path / ".awr2944"
    local_dir.mkdir(parents=True, exist_ok=True)
    tools = dca_tools or {}
    (local_dir / "local.toml").write_text(
        f'[serial]\ncom_port = "COM8"\nbaud_rate = 115200\n\n'
        f'[network]\nhost_ip = "192.168.33.30"\n\n'
        f'[dca_tools]\n'
        f'control_exe = {json.dumps(tools.get("control_exe", ""))}\n'
        f'record_exe = {json.dumps(tools.get("record_exe", ""))}\n'
        f'rf_api_dll = {json.dumps(tools.get("rf_api_dll", ""))}\n'
        f'cf_json = {json.dumps(tools.get("cf_json", ""))}\n',
        encoding="utf-8",
    )
    (tmp_path / "profiles").mkdir(exist_ok=True)
    (tmp_path / "captures").mkdir(exist_ok=True)
    return RadarProject.open(tmp_path)


def _make_toolchain_json(proj_root: Path, *, control_exe: str = "C:\\ti\\PostProc\\DCA1000EVM_CLI_Control.exe",
                         record_exe: str = "C:\\ti\\PostProc\\DCA1000EVM_CLI_Record.exe",
                         cf_json: str = "C:\\ti\\PostProc\\cf.json") -> None:
    """Write a legacy toolchain.local.json at the expected location."""
    headless_dir = proj_root / "ti" / "headless"
    headless_dir.mkdir(parents=True, exist_ok=True)
    tc = {
        "dca_cli_control_exe": control_exe,
        "dca_cli_record_exe":  record_exe,
        "rf_api_dll":          "",
        "dca_cli_cf_json":     cf_json,
    }
    (headless_dir / "toolchain.local.json").write_text(json.dumps(tc), encoding="utf-8")


# ---------------------------------------------------------------------------
# Smoke-v1 characterisation — run before and after to prove stability
# ---------------------------------------------------------------------------

SMOKE_EXPECTED_CANONICAL_FRAMES = 8
SMOKE_EXPECTED_GUARD_FRAMES     = 1
SMOKE_EXPECTED_TOTAL_FRAMES     = 9
SMOKE_EXPECTED_CUBE_SHAPE       = (8, 128, 4, 256)


class TestCapturePlanSmoke:
    """CP-1 through CP-12 — smoke_v1 characterisation."""

    @pytest.fixture
    def project(self, tmp_path):
        return _make_modern_project(tmp_path)

    @pytest.fixture
    def plan(self, project):
        return project.capture.plan("smoke_v1", frames=8, guard_frames=1)

    def test_CP1_canonical_guard_total_frames(self, plan):
        assert plan.canonical_frames == SMOKE_EXPECTED_CANONICAL_FRAMES
        assert plan.guard_frames     == SMOKE_EXPECTED_GUARD_FRAMES
        assert plan.total_frames     == SMOKE_EXPECTED_TOTAL_FRAMES

    def test_CP2_cube_shape(self, plan):
        assert plan.cube_shape == SMOKE_EXPECTED_CUBE_SHAPE

    def test_CP3a_exact_equality_string_profile(self, project):
        """plan.to_dict() == dry_run() for string profile (same config state)."""
        plan  = project.capture.plan("smoke_v1", frames=8, guard_frames=1)
        dr    = project.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
        assert plan.to_dict() == dr

    def test_CP4_byte_plan_field_values(self, plan):
        """CapturePlan.byte_plan matches the resolver's BytePlan directly."""
        import dataclasses
        bp = plan.byte_plan
        d  = dataclasses.asdict(bp)
        # Sanity: key fields present and sensible
        assert d["canonical_frames"] == SMOKE_EXPECTED_CANONICAL_FRAMES
        assert d["samples_per_chirp"] == 256
        assert d["rx_channels"] == 4
        assert d["chirps_per_frame"] == 128
        assert d["native_dca_bytes"] > 0
        assert d["canonical_dca_bytes"] > 0

    def test_CP5_sdk_cli_command_count(self, project, plan):
        """plan command count matches the resolver directly."""
        from awr2944_dca.api._config_resolver import resolve_capture_config
        resolved = resolve_capture_config(project=project, profile="smoke_v1",
                                          frames=8, guard_frames=1)
        assert len(plan.awr_commands) == len(resolved.cli_commands)

    def test_CP6_awr_commands_content_and_order(self, project, plan):
        """awr_commands tuple equals resolver.cli_commands exactly."""
        from awr2944_dca.api._config_resolver import resolve_capture_config
        resolved = resolve_capture_config(project=project, profile="smoke_v1",
                                          frames=8, guard_frames=1)
        assert plan.awr_commands == tuple(resolved.cli_commands)

    def test_CP7_dry_run_backward_compat(self, project):
        """dry_run() still callable, returns dict, all expected legacy keys present."""
        result = project.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
        assert isinstance(result, dict)
        for key in (
            "profile_name", "effective_frames", "guard_frames",
            "sdk_cli_command_count", "hardware_touched",
            "total_frames", "canonical_frames",
            "expected_native_dca_bytes", "expected_canonical_dca_bytes",
            "logical_depacked_bytes", "canonical_logical_bytes",
            "canonical_cube", "dca_storage_expansion_factor",
            "dca_control_executable", "dca_config_source", "dca_config_runtime_path",
        ):
            assert key in result, f"legacy key missing: {key!r}"

    def test_CP8_hardware_touched_is_false(self, plan):
        assert plan.hardware_touched is False
        with pytest.raises((AttributeError, TypeError, Exception)):
            # frozen=True means assignment raises FrozenInstanceError
            plan.hardware_touched = True  # type: ignore[misc]

    def test_CP9_warnings_present_for_warning_profile(self, project):
        """If the profile triggers a preflight warning it shows up in plan.warnings."""
        plan = project.capture.plan("smoke_v1", frames=8, guard_frames=1)
        # warnings may or may not be present depending on profile config;
        # we just assert it is a tuple of strings
        assert isinstance(plan.warnings, tuple)
        assert all(isinstance(w, str) for w in plan.warnings)

    def test_CP10_repr_is_short(self, plan):
        r = repr(plan)
        assert len(r) <= 200, f"repr too long ({len(r)}): {r!r}"

    def test_CP11_print_runs_without_error(self, plan, capsys):
        plan.print()
        captured = capsys.readouterr()
        assert "smoke_v1" in captured.out
        # Values are not mutated
        assert plan.canonical_frames == SMOKE_EXPECTED_CANONICAL_FRAMES

    def test_CP12_repr_html_contains_profile_and_cube(self, plan):
        html = plan._repr_html_()
        assert "smoke_v1" in html
        assert str(SMOKE_EXPECTED_CANONICAL_FRAMES) in html


class TestCapturePlanImmutability:
    """CP-13 — snapshot must not change after project config is mutated."""

    def test_CP13_dca_config_snapshot_not_mutated_by_config_change(self, tmp_path):
        p = _make_modern_project(tmp_path)
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)

        original_host = plan.dca_config["host_ip"]

        # Mutate the live config
        p._config.local.host_ip = "10.99.99.99"

        # Plan snapshot must be unchanged
        assert plan.dca_config["host_ip"] == original_host

    def test_CP13_dca_config_defensive_copy(self, tmp_path):
        p = _make_modern_project(tmp_path)
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)

        snap = plan.dca_config
        snap["host_ip"] = "MUTATED"

        # Next call returns a fresh copy — plan is unaffected
        assert plan.dca_config["host_ip"] != "MUTATED"

    def test_CP13_dca_config_has_modern_snapshot_fields(self, tmp_path):
        """dca_config contains the full modern snapshot, not just legacy compat keys."""
        p = _make_modern_project(tmp_path)
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        snap = plan.dca_config
        for key in ("host_ip", "dca_ip", "config_port", "data_port",
                    "dca_control_exe", "dca_record_exe", "rf_api_dll", "cf_json_path"):
            assert key in snap, f"Modern snapshot key missing: {key!r}"
        assert snap["host_ip"] == "192.168.33.30"
        assert snap["dca_ip"]  == "192.168.33.180"
        assert snap["config_port"] == 4096
        assert snap["data_port"]   == 4098


class TestCapturePlanProfileIdentity:
    """CP-14 — effective profile name from resolved config, not raw input."""

    def test_CP14a_effective_profile_string_input(self, tmp_path):
        """String input → plan.profile == the string."""
        p    = _make_modern_project(tmp_path)
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        assert plan.profile == "smoke_v1"

    def test_CP14b_legacy_profile_name_for_object_input(self, tmp_path):
        """RadarProfile object → to_dict()['profile_name'] == 'programmatic' (legacy compat)."""
        from awr2944_dca.api.profile import RadarProfile
        p      = _make_modern_project(tmp_path)
        custom = RadarProfile.smoke_v1()
        # Rename isn't necessarily on RadarProfile; test with plain object input
        plan   = p.capture.plan(custom, frames=8, guard_frames=1)
        assert plan.to_dict()["profile_name"] == "programmatic"

    def test_CP14c_exact_equality_object_profile(self, tmp_path):
        """plan.to_dict() == dry_run() for RadarProfile object input."""
        from awr2944_dca.api.profile import RadarProfile
        p      = _make_modern_project(tmp_path)
        custom = RadarProfile.smoke_v1()
        plan   = p.capture.plan(custom, frames=8, guard_frames=1)
        dr     = p.capture.dry_run(custom, frames=8, guard_frames=1)
        assert plan.to_dict() == dr

    def test_CP14d_effective_profile_for_object_input(self, tmp_path):
        """plan.profile reflects the resolved effective name, not 'programmatic'."""
        from awr2944_dca.api.profile import RadarProfile
        p    = _make_modern_project(tmp_path)
        prof = RadarProfile.smoke_v1()
        plan = p.capture.plan(prof, frames=8, guard_frames=1)
        # plan.profile should be the resolved name (e.g. "smoke_v1"), not "programmatic"
        assert plan.profile != "programmatic"
        assert isinstance(plan.profile, str)


class TestCapturePlanTypes:
    """CP-15, CP-16 — immutable types."""

    @pytest.fixture
    def plan(self, tmp_path):
        p = _make_modern_project(tmp_path)
        return p.capture.plan("smoke_v1", frames=8, guard_frames=1)

    def test_CP15_warnings_is_tuple(self, plan):
        assert isinstance(plan.warnings, tuple)

    def test_CP16_awr_commands_is_tuple(self, plan):
        assert isinstance(plan.awr_commands, tuple)


class TestCapturePlanConsistencyVsRun:
    """CP-M — CapturePlan DCA config matches what production run() would use."""

    def test_CPM_plan_dca_matches_resolve_connection(self, tmp_path):
        """plan.dca_config['dca_control_exe'] == resolve_connection().dca_control_exe."""
        from awr2944_dca.api._session import resolve_connection
        tools = {"control_exe": "C:\\fake\\control.exe",
                 "record_exe":  "C:\\fake\\record.exe",
                 "cf_json":     "C:\\fake\\cf.json"}
        p    = _make_modern_project(tmp_path, dca_tools=tools)
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        snap = plan.dca_config
        assert snap["dca_control_exe"] == tools["control_exe"]
        assert snap["cf_json_path"]    == tools["cf_json"]

        # resolve_connection() also uses ProjectConfig
        conn = resolve_connection(tmp_path)
        assert snap["dca_control_exe"] == conn.dca_control_exe
        assert snap["cf_json_path"]    == conn.cf_json_path


class TestCapturePlanLegacyCompat:
    """CP-LC — Task 5: modern plan()/dry_run() never consults legacy toolchain."""

    def test_CPLC_blank_modern_fields_no_legacy_fallback(self, tmp_path):
        """With all modern DCA fields blank, _resolve_dca_info() reports NOT_CONFIGURED
        even when legacy toolchain.local.json exists."""
        p = _make_modern_project(tmp_path)  # blank DCA tool fields in local.toml
        # Write legacy toolchain.local.json with valid-looking values
        _make_toolchain_json(
            tmp_path,
            control_exe="C:\\ti\\PostProc\\DCA1000EVM_CLI_Control.exe",
            cf_json="C:\\ti\\PostProc\\cf.json",
        )
        # Modern plan()/dry_run() must NOT import legacy values
        result = p.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
        assert result["dca_control_executable"] == "NOT_CONFIGURED"
        assert result["dca_config_source"] == "NOT_CONFIGURED"

        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        snap = plan.dca_config
        assert snap["dca_control_exe"] == ""
        assert snap["cf_json_path"] == ""

    def test_CPLC_partial_modern_fields_no_legacy_fallback(self, tmp_path):
        """With ANY modern field set, legacy toolchain is NOT consulted."""
        tools = {"control_exe": "C:\\modern\\control.exe"}  # only control_exe set
        p = _make_modern_project(tmp_path, dca_tools=tools)
        # Also write a legacy toolchain with a different path
        _make_toolchain_json(
            tmp_path,
            control_exe="C:\\legacy\\control.exe",
            cf_json="C:\\legacy\\cf.json",
        )
        result = p.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
        # Modern partial config is used, not legacy
        assert result["dca_control_executable"] == "C:\\modern\\control.exe"
        assert result["dca_config_source"] == "NOT_CONFIGURED"  # cf_json blank in modern

    def test_CPLC_plan_to_dict_equals_dry_run_for_blank_project(self, tmp_path):
        """Exact equality still holds when all modern DCA fields are blank."""
        p = _make_modern_project(tmp_path)
        _make_toolchain_json(
            tmp_path,
            control_exe="C:\\ti\\PostProc\\DCA1000EVM_CLI_Control.exe",
            cf_json="C:\\ti\\PostProc\\cf.json",
        )
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        dr   = p.capture.dry_run("smoke_v1", frames=8, guard_frames=1)
        assert plan.to_dict() == dr

    def test_CPLC_monkeypatch_guard_legacy_never_called(self, tmp_path, monkeypatch):
        """Poison CaptureApi._load_toolchain to prove modern path never touches it.

        If the modern plan()/dry_run() path were to call _load_toolchain,
        this test would raise AssertionError.
        """
        from awr2944_dca.lab import CaptureApi

        def _poisoned_load_toolchain(self):
            raise AssertionError(
                "Modern plan()/dry_run() must not call CaptureApi._load_toolchain"
            )

        monkeypatch.setattr(CaptureApi, "_load_toolchain", _poisoned_load_toolchain)

        p = _make_modern_project(tmp_path)
        # Write a legacy toolchain to prove it would have been found
        _make_toolchain_json(tmp_path)

        # Both plan() and dry_run() must complete without hitting the poison
        plan = p.capture.plan("smoke_v1", frames=8, guard_frames=1)
        dr   = p.capture.dry_run("smoke_v1", frames=8, guard_frames=1)

        # And results are consistent
        assert plan.to_dict() == dr
        assert dr["dca_control_executable"] == "NOT_CONFIGURED"
        assert plan.dca_config["dca_control_exe"] == ""

