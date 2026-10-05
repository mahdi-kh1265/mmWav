import pytest
import sys
import types
from pathlib import Path

# Need to make sure awr2944_dca is importable, assuming pytest handles it via conftest

def test_production_modules_do_not_import_mmws():
    """Verify that production capture modules do not import legacy mmws modules."""
    import sys

    # Clean up sys.modules to start fresh - removed to prevent poisoning

    # Import production modules
    import awr2944_dca.capture_cli
    import awr2944_dca.capture_session
    import awr2944_dca.direct_udp_capture
    import awr2944_dca.sdk_cli_profile
    import awr2944_dca.lab

    # Check if legacy dependencies were imported
    bad_modules = ["awr2944_dca.legacy_mmws", "pythonnet", "clr", "pywinauto", "RSTD", "RTTTNetClientAPI"]
    found = [m for m in bad_modules if m in sys.modules]

    assert len(found) == 0, f"Production code imported legacy dependencies: {found}"

def test_build_smoke_v1_cli_parity():
    """Verify generated SDK CLI commands."""
    from awr2944_dca.sdk_cli_profile import build_smoke_v1_cli

    # Generate for 9 frames
    commands = build_smoke_v1_cli(frames=9, chirps_per_frame=128)

    # The generated commands should look exactly like our known-good CFG minus sensorStop/sensorStart
    assert "sensorStop" not in commands
    assert "sensorStart" not in commands

    # Verify key configurations
    assert "dfeDataOutputMode 1" in commands
    assert "channelCfg 15 7 0" in commands
    assert "adcCfg 2 0" in commands
    assert "adcbufCfg -1 1 1 1 1" in commands
    assert "profileCfg 0 77 100 6 60 0 0 29.982 0 256 10000 0 0 30" in commands
    assert "chirpCfg 0 0 0 0 0 0 0 3" in commands
    assert "frameCfg 0 0 128 9 256 40 1 0" in commands
    assert "lowPower 0 0" in commands
    assert "guiMonitor -1 1 1 0 0 0 1" in commands
    assert "cfarCfg -1 0 2 8 4 3 0 15 1" in commands
    assert "cfarCfg -1 1 0 4 2 3 1 15 1" in commands
    assert "multiObjBeamForming -1 1 0.5" in commands
    assert "clutterRemoval -1 0" in commands
    assert "calibDcRangeSig -1 0 -5 8 256" in commands
    assert "extendedMaxVelocity -1 0" in commands
    assert "lvdsStreamCfg -1 0 1 0" in commands
    assert "compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0" in commands
    assert "measureRangeBiasAndRxChanPhase 0 1.5 0.2" in commands
    assert "CQRxSatMonitor 0 3 11 121 0" in commands
    assert "CQSigImgMonitor 0 127 8" in commands
    assert "analogMonitor 0 0" in commands
