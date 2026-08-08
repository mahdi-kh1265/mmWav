import pytest
import json
import hashlib
from pathlib import Path
from awr2944_dca.lab import RadarProject

def _validate_capture(capture):
    output_dir = capture.path
    
    # 2. Verify resolved_config.cfg exists in capture directory
    resolved_cfg_path = output_dir / "resolved_config.cfg"
    assert resolved_cfg_path.exists(), "resolved_config.cfg missing"
    
    # Verify frameCfg numFrames = 9 in resolved_config.cfg
    cfg_text = resolved_cfg_path.read_text()
    frame_cmd = next(line for line in cfg_text.splitlines() if line.startswith("frameCfg"))
    assert int(frame_cmd.split()[4]) == 9
    assert "flushCfg" in cfg_text
    assert "sensorStart" not in cfg_text
    assert "sensorStop" not in cfg_text
    
    # 3. Verify hashes
    manifest = capture.project_record
    actual_hash = hashlib.sha256(resolved_cfg_path.read_bytes()).hexdigest()
    assert manifest.get("resolved_config_sha256") == actual_hash
    assert manifest.get("radar_config_sha256") == actual_hash
    
    # 4. Verify config_summary.json
    summary_path = output_dir / "config_summary.json"
    assert summary_path.exists(), "config_summary.json missing"
    summary = json.loads(summary_path.read_text())
    assert summary["config_summary_schema_version"] == 1
    
    assert summary["total_frames"] == 9
    assert summary["canonical_frames"] == 8
    
    assert summary["canonical_dca_bytes"] == 4194304
    assert summary["native_dca_bytes"] == 4718592
    assert summary.get("resolved_config_sha256") == actual_hash
    assert manifest.get("resolved_config_sha256") == summary.get("resolved_config_sha256")
    
    # File sizes via capture.raw public API
    native_bin = capture.raw.native_path
    canonical_bin = capture.raw.canonical_path
    assert native_bin.exists()
    assert native_bin.stat().st_size == 4718592
    if canonical_bin.exists():
        assert canonical_bin.stat().st_size == 4194304
    
    # 5. Verify resolved_profile.toml exists (structured input)
    assert (output_dir / "resolved_profile.toml").exists(), "resolved_profile.toml missing"
    
    # 6. manifest.json (hardware schema v3)
    hardware_manifest = capture.manifest
    assert hardware_manifest.get("manifest_schema_version", 0) == 3
    assert hardware_manifest.get("sequence_gaps") == 0
    assert hardware_manifest.get("byte_counter_discontinuity_count") == 0
    assert hardware_manifest.get("missing_payload_bytes") == 0
    
    # 7. capture_manifest.json (project schema v4+)
    assert manifest.get("manifest_schema_version", 0) >= 4
    
    # Strict verification (proves no overlap bytes and complete continuity)
    verify_report = capture.verify(strict=True)
    assert verify_report.success
    
    for check in verify_report.checks:
        assert check.status == "PASS"
        
    assert capture.raw.to_cube(kind="canonical").shape == (8, 128, 4, 256)

@pytest.mark.hardware_live
@pytest.mark.skipif(
    not Path(".").joinpath("awr2944.toml").exists(),
    reason="Not inside a RadarProject directory (no awr2944.toml in cwd)",
)
def test_config_provenance_hardware_live():
    project = RadarProject(Path("."))
    result = project.capture.run("smoke_v1", frames=8, guard_frames=1)
    
    assert result.success, "Live capture failed"
    _validate_capture(result.capture)
