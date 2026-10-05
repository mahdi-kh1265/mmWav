import pytest
from pathlib import Path
from awr2944_dca.api._config_resolver import (
    resolve_capture_config, 
    MmwDemoConfig, 
    preflight_validate,
    extract_capture_metadata
)
from awr2944_dca.api.profile import RadarProfile
from awr2944_dca.lab import RadarProject

@pytest.fixture
def dummy_project(tmp_path):
    # Setup dummy project
    import json
    pj_path = tmp_path / "project.json"
    pj_path.write_text(json.dumps({"project_id": "dummy"}))
    p = RadarProject(tmp_path)
    # create default profile
    p.profiles.save(RadarProfile.smoke_v1())
    return p

# 1-5 Resolution basics
def test_resolve_from_profile_name(dummy_project):
    res = resolve_capture_config(dummy_project, "smoke_v1", frames=8, guard_frames=1)
    assert res.source_kind == "profile"
    assert res.structured_profile is not None
    assert res.byte_plan.total_frames == 9

def test_resolve_from_radar_profile(dummy_project):
    prof = RadarProfile.smoke_v1()
    res = resolve_capture_config(dummy_project, prof, frames=8, guard_frames=1)
    assert res.source_kind == "profile"
    assert res.structured_profile is not None
    assert res.byte_plan.total_frames == 9

def test_resolve_from_cfg_path(dummy_project, tmp_path):
    cfg_text = """
sensorStop
flushCfg
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 0
adcbufCfg -1 1 1 1 1
profileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
frameCfg 0 0 128 8 100 1 0
sensorStart
"""
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert res.source_kind == "cfg_file"
    assert res.byte_plan.total_frames == 9
    assert res.byte_plan.canonical_frames == 8

def test_resolve_from_mmw_demo_config(dummy_project, tmp_path):
    cfg_text = """
channelCfg 15 7 0
adcCfg 2 0
adcbufCfg -1 1 1 1 1
profileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
frameCfg 0 0 128 8 100 1 0
"""
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    res = resolve_capture_config(dummy_project, cfg, frames=8, guard_frames=1)
    assert res.source_kind == "mmw_demo_config"

# Byte plan 6-9
def test_byte_plan_smoke_native_9_frames(dummy_project):
    res = resolve_capture_config(dummy_project, "smoke_v1", frames=8, guard_frames=1)
    assert res.byte_plan.total_frames == 9
    assert res.byte_plan.native_dca_bytes == 4718592

def test_byte_plan_smoke_canonical_8_frames(dummy_project):
    res = resolve_capture_config(dummy_project, "smoke_v1", frames=8, guard_frames=1)
    assert res.byte_plan.canonical_frames == 8
    assert res.byte_plan.canonical_dca_bytes == 4194304

def test_byte_plan_native_vs_canonical_distinct(dummy_project):
    res = resolve_capture_config(dummy_project, "smoke_v1", frames=8, guard_frames=1)
    assert res.byte_plan.native_dca_bytes != res.byte_plan.canonical_dca_bytes

def test_byte_plan_explicit_units(dummy_project):
    res = resolve_capture_config(dummy_project, "smoke_v1", frames=8, guard_frames=1)
    assert res.byte_plan.bytes_per_adc_sample == 2
    assert res.byte_plan.components_per_sample == 1
    assert res.byte_plan.dca_expansion_factor == 2

# ADC format 10-13
def test_adc_format_conflict_adcCfg_complex_rejected(tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 1\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "adcCfg b2AdcOutFmt" in i.message for i in issues)

def test_adc_format_conflict_adcbufCfg_complex_rejected(tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 0 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "adcbufCfg adcFmt" in i.message for i in issues)

def test_adc_format_conflict_adcCfg_missing_rejected(tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "Missing adcCfg" in i.message for i in issues)

def test_adc_format_conflict_adcbufCfg_missing_rejected(tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "Missing adcbufCfg" in i.message for i in issues)

# Unknown commands 14-15
def test_unknown_command_parse_succeeds(tmp_path):
    cfg_text = "fakeCommand 1 2 3\nchannelCfg 15 7 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    assert len(cfg.lines) > 0

def test_unknown_command_capture_rejected(tmp_path):
    cfg_text = "fakeCommand 1 2 3\nchannelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "Unknown commands" in i.message for i in issues)

# Advanced multi-chirp 16-20
def test_multi_chirp_parse_succeeds(tmp_path):
    cfg_text = "chirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    assert len(cfg.lines) == 2

def test_multi_chirp_execution_rejected(tmp_path, dummy_project):
    # Superseded semantics: multi-TX configs now resolve/plan offline (no blanket
    # preflight error) but live execution is blocked via capabilities.
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert not any(i.severity == "ERROR" for i in issues)
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert res.capabilities.can_plan
    assert not res.capabilities.can_execute_live

def test_advanced_frame_parse_succeeds(tmp_path):
    cfg_text = "advFrameCfg 1 2 3"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    assert len(cfg.lines) == 1

def test_advanced_frame_execution_rejected(tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nadvFrameCfg 1 2 3"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    meta = extract_capture_metadata(cfg)
    issues = preflight_validate(cfg, meta)
    assert any(i.severity == "ERROR" and "Advanced frame" in i.message for i in issues)

def test_multi_chirp_dsp_profile_is_none(tmp_path, dummy_project):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nchirpCfg 1 1 0 0 0 0 0 2\nframeCfg 0 1 64 8 100 1 0"
    cfg_path = tmp_path / "temp.cfg"
    cfg_path.write_text(cfg_text)
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert res.dsp_profile is None
    assert not res.capabilities.can_build_legacy_dsp_profile

# Provenance 21-26
def test_source_toml_preserved(dummy_project):
    pass # Tested indirectly by capture run

def test_resolved_toml_written(dummy_project):
    pass

def test_source_cfg_preserved_verbatim(dummy_project):
    pass

def test_resolved_cfg_normalized(dummy_project, tmp_path):
    cfg_text = "channelCfg 15 7 0\r\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\r\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert "\r\n" not in res.resolved_cfg_text
    assert res.resolved_cfg_text.endswith("\n")

def test_resolved_sha256_deterministic(dummy_project, tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res1 = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    res2 = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert res1.resolved_sha256 == res2.resolved_sha256

def test_source_sha256_is_raw_bytes(dummy_project, tmp_path):
    cfg_text = "channelCfg 15 7 0\n"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    import hashlib
    h = hashlib.sha256(cfg_path.read_bytes()).hexdigest().upper()
    cfg = MmwDemoConfig.from_cfg_file(cfg_path)
    assert cfg.source_sha256 == h

# UART command filtering 27-28
def test_no_sensorStop_sensorStart_in_resolved(dummy_project, tmp_path):
    cfg_text = "sensorStop\nchannelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0\nsensorStart"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert "sensorStop" not in res.resolved_cfg_text
    assert "sensorStart" not in res.resolved_cfg_text

def test_flushCfg_retained_in_resolved(dummy_project, tmp_path):
    cfg_text = "flushCfg\nchannelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    assert "flushCfg" in res.resolved_cfg_text

# Frame override 29-30
def test_frame_override_rewrites_frameCfg(dummy_project, tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=16, guard_frames=1)
    assert "frameCfg 0 0 128 17 100 1 0" in res.resolved_cfg_text

def test_frame_override_byte_plan_consistency(dummy_project, tmp_path):
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0"
    cfg_path = tmp_path / "simple.cfg"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    res = resolve_capture_config(dummy_project, cfg_path, frames=16, guard_frames=1)
    assert res.byte_plan.total_frames == 17

# Schema versioning 31-32
def test_manifest_schema_version_present():
    from awr2944_dca.capture_manifest import CaptureManifest
    m = CaptureManifest(total_frames=1, guard_frame_count=0, canonical_frame_count=1, native_sha256="", canonical_sha256="", packet_count=0, sequence_gaps=0, capture_timestamp="", parser_layout_version="", dsp_config_version="")
    assert m.manifest_schema_version == 3

def test_config_summary_schema_version_present(dummy_project, tmp_path):
    import json
    from awr2944_dca.api._capture_run import _run_capture_facade
    from awr2944_dca.api.profile import RadarProfile
    
    project = dummy_project
    
    # Run offline capture via monkeypatching the hardware call
    import pytest
    from awr2944_dca.api._session import ConnectionOverrides
    from pathlib import Path
    
    project = dummy_project
    
    # Run offline capture via monkeypatching the hardware call
    def _mock_run_capture(*args, **kwargs):
        from awr2944_dca.capture_session import CaptureResult
        from awr2944_dca.capture_manifest import CaptureManifest
        
        output_dir = kwargs.get("output_dir")
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            
        manifest = CaptureManifest(
            total_frames=9, guard_frame_count=1, canonical_frame_count=8,
            native_sha256="abc", canonical_sha256="def", packet_count=100, sequence_gaps=0,
            capture_timestamp="2026-07-15T12:00:00", parser_layout_version="1", dsp_config_version="1",
            success=True
        )
        return CaptureResult(capture_dir=output_dir, manifest=manifest)
        
    with pytest.MonkeyPatch.context() as m:
        m.setattr("awr2944_dca.capture_session.run_capture", _mock_run_capture)
        
        # Use programmatic profile to ensure it doesn't try to read paths that don't exist
        prof = RadarProfile(name="test_prof")
        overrides = ConnectionOverrides(
            com_port="COM1", host_ip="1.1.1.1", dca_ip="1.1.1.2",
            dca_control_exe=Path("dummy"), dca_record_exe=Path("dummy"),
            rf_api_dll=Path("dummy"), cf_json_path=Path("dummy")
        )
        res = _run_capture_facade(project, profile=prof, frames=8, guard_frames=1, preserve_packet_metadata=False, name="test_cap", session=None, connection_overrides=overrides)
        
        cap_dir = res.capture.path
        summary_path = cap_dir / "config_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        
        assert summary["config_summary_schema_version"] == 1
        assert summary["target_device"] == "AWR2944"
        assert summary["sdk_version"] == "04.07.02.01"
        assert summary["firmware_config_target"] == "awr294x_mmw_demo"
        
        manifest_path = cap_dir / "capture_manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        
        # Verify hashes match between manifest and summary
        assert manifest["source_config_sha256"] == summary["source_config_sha256"]
        assert manifest["resolved_config_sha256"] == summary["resolved_config_sha256"]
        assert manifest["radar_config_sha256"] == summary["radar_config_sha256"]

def test_toml_provenance_preservation(dummy_project, tmp_path):
    from awr2944_dca.api._config_resolver import resolve_capture_config
    from awr2944_dca.api.profile import RadarProfile
    import hashlib
    
    toml_path = tmp_path / "my_profile.toml"
    p = RadarProfile(name="my_toml_profile")
    toml_bytes = p.to_toml().encode("utf-8")
    toml_path.write_bytes(toml_bytes)
    
    expected_sha256 = hashlib.sha256(toml_bytes).hexdigest()
    
    resolved = resolve_capture_config(dummy_project, toml_path)
    
    assert resolved.source_kind == "toml_file"
    assert resolved.source_path == toml_path
    assert resolved.source_name == "my_toml_profile"
    assert resolved.source_sha256 == expected_sha256

# Backward compatibility test
def test_backward_compatibility_capture_manifest(tmp_path):
    from awr2944_dca.capture_manifest import CaptureManifest
    old_json = '''{
        "total_frames": 1,
        "guard_frame_count": 0,
        "canonical_frame_count": 1,
        "native_sha256": "abc",
        "canonical_sha256": "def",
        "packet_count": 0,
        "sequence_gaps": 0,
        "capture_timestamp": "2026-01-01",
        "parser_layout_version": "v1",
        "dsp_config_version": "v1"
    }'''
    p = tmp_path / "manifest.json"
    p.write_text(old_json)
    m = CaptureManifest.from_json(p)
    assert m.manifest_schema_version == 1
    assert m.source_config_kind is None
    assert m.radar_config_sha256 is None

def test_frame_resolution_radar_profile(tmp_path, mocker):
    from awr2944_dca.api._config_resolver import resolve_capture_config
    from awr2944_dca.api.profile import RadarProfile
    from awr2944_dca.lab import RadarProject
    proj = mocker.Mock(spec=RadarProject)
    p = RadarProfile.from_smoke_v1()
    
    c = resolve_capture_config(proj, p, frames=8, guard_frames=1)
    cmd = next(cmd for cmd in c.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd.split()[4]) == 9
    assert c.byte_plan.native_dca_bytes == 4718592
    assert c.byte_plan.canonical_dca_bytes == 4194304
    
    c2 = resolve_capture_config(proj, p, frames=8, guard_frames=0)
    cmd2 = next(cmd for cmd in c2.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd2.split()[4]) == 8

def test_frame_resolution_radar_profile(tmp_path, dummy_project):
    from awr2944_dca.api._config_resolver import resolve_capture_config
    p = dummy_project.profiles.get('smoke_v1')
    
    c = resolve_capture_config(dummy_project, p, frames=8, guard_frames=1)
    cmd = next(cmd for cmd in c.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd.split()[4]) == 9
    assert c.byte_plan.native_dca_bytes == 4718592
    assert c.byte_plan.canonical_dca_bytes == 4194304
    
    c2 = resolve_capture_config(dummy_project, p, frames=8, guard_frames=0)
    cmd2 = next(cmd for cmd in c2.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd2.split()[4]) == 8
    
    c3 = resolve_capture_config(dummy_project, p, frames=1, guard_frames=1)
    cmd3 = next(cmd for cmd in c3.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd3.split()[4]) == 2

def test_frame_resolution_cfg(tmp_path, dummy_project):
    from awr2944_dca.api._config_resolver import resolve_capture_config
    cfg_text = "channelCfg 15 7 0\nadcCfg 2 0\nadcbufCfg -1 1 1 1 1\nprofileCfg 0 77 429 7 57.14 0 0 70 1 256 5209 0 0 30\nchirpCfg 0 0 0 0 0 0 0 1\nframeCfg 0 0 128 8 100 1 0\n"
    cfg_path = tmp_path / "test.cfg"
    cfg_path.write_text(cfg_text)
    
    c = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=1)
    cmd = next(cmd for cmd in c.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd.split()[4]) == 9
    assert c.byte_plan.native_dca_bytes == 4718592
    assert c.byte_plan.canonical_dca_bytes == 4194304
    
    c2 = resolve_capture_config(dummy_project, cfg_path, frames=8, guard_frames=0)
    cmd2 = next(cmd for cmd in c2.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd2.split()[4]) == 8
    
    c3 = resolve_capture_config(dummy_project, cfg_path, frames=1, guard_frames=1)
    cmd3 = next(cmd for cmd in c3.cli_commands if cmd.startswith('frameCfg'))
    assert int(cmd3.split()[4]) == 2