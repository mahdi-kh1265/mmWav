"""Regression tests for DCA FPGA transient-reset recovery in capture_session.

Scenarios:
  A. reset succeeds normally -> continue
  B. reset returns disconnected but query_sys_status recovers -> continue
  C. reset returns disconnected and never recovers -> fail
  D. reset returns unrelated error -> fail immediately
  E. configure_fpga failure still fails
  F. configure_record failure still fails
  G. reset_fpga is called ONLY ONCE in the recovery case
"""

import pytest
import threading
import time as _real_time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from awr2944_dca.dca_cli import DcaCmdResult
from awr2944_dca.dsp.config import RadarProfile
from awr2944_dca.awr2944_adc import expected_raw_dca_bytes, AWR2944AdcLayout
import awr2944_dca.capture_session as cs


# ---------------------------------------------------------------------------
# DcaCmdResult helpers
# ---------------------------------------------------------------------------

def _ok_result(command: str = "reset_fpga") -> DcaCmdResult:
    return DcaCmdResult(
        command=command, args=[], returncode=0,
        stdout="Reset FPGA command sent", stderr="",
        success=True, elapsed_s=0.1, exe_path="control.exe",
    )


def _transient_fail_result() -> DcaCmdResult:
    """rc=4294967291 (unsigned -5), Timeout Error + System disconnected."""
    return DcaCmdResult(
        command="reset_fpga", args=[], returncode=4294967291,
        stdout="Reset FPGA :\nTimeout Error! System disconnected",
        stderr="", success=False, elapsed_s=1.5,
        exe_path="control.exe",
    )


def _transient_fail_signed() -> DcaCmdResult:
    """Same but with signed rc=-5."""
    return DcaCmdResult(
        command="reset_fpga", args=[], returncode=-5,
        stdout="Reset FPGA :\nTimeout Error! System disconnected",
        stderr="", success=False, elapsed_s=1.5,
        exe_path="control.exe",
    )


def _unrelated_fail_result() -> DcaCmdResult:
    return DcaCmdResult(
        command="reset_fpga", args=[], returncode=1,
        stdout="FPGA communication failure", stderr="",
        success=False, elapsed_s=0.5, exe_path="control.exe",
    )


def _sys_status_ok() -> DcaCmdResult:
    return DcaCmdResult(
        command="query_sys_status", args=[], returncode=0,
        stdout="System is connected.", stderr="",
        success=True, elapsed_s=0.1, exe_path="control.exe",
    )


def _sys_status_fail() -> DcaCmdResult:
    return DcaCmdResult(
        command="query_sys_status", args=[], returncode=4294967291,
        stdout="System disconnected", stderr="",
        success=False, elapsed_s=0.1, exe_path="control.exe",
    )


def _fpga_ok() -> DcaCmdResult:
    return DcaCmdResult(
        command="fpga", args=[], returncode=0,
        stdout="Record FPGA Configure command", stderr="",
        success=True, elapsed_s=0.1, exe_path="control.exe",
    )


def _record_ok() -> DcaCmdResult:
    return DcaCmdResult(
        command="record", args=[], returncode=0,
        stdout="Record delay configured", stderr="",
        success=True, elapsed_s=0.1, exe_path="control.exe",
    )


def _fpga_fail() -> DcaCmdResult:
    return DcaCmdResult(
        command="fpga", args=[], returncode=1,
        stdout="Error configuring FPGA", stderr="",
        success=False, elapsed_s=0.1, exe_path="control.exe",
    )


def _record_fail() -> DcaCmdResult:
    return DcaCmdResult(
        command="record", args=[], returncode=1,
        stdout="Error configuring record", stderr="",
        success=False, elapsed_s=0.1, exe_path="control.exe",
    )


# ---------------------------------------------------------------------------
# Module-level mocks (UART, UDP, Receiver)
# ---------------------------------------------------------------------------

class _MockSerial:
    """Minimal stand-in for the serial port accessed via conn._serial."""
    timeout = 1.0

    def reset_input_buffer(self):
        pass

    def write(self, data):
        pass

    def read(self, n=1):
        return b""


class MockUart:
    """Mock AwrUartConnection that survives the UART sync preamble."""

    def __init__(self, *a, **kw):
        self._serial = _MockSerial()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def _record(self, direction, msg):
        pass

    def read_until_prompt(self, timeout=10.0):
        return "mmwDemo:/>"

    def send_command(self, cmd):
        class Res:
            success = True
            timed_out = False
            response_lines = ["Done"]
        return Res()


class MockDca:
    def __init__(self, *a, **kw):
        pass

    def start_record(self):
        return True

    def stop_record(self):
        return True


class MockReceiver:
    def __init__(self, output_path, expected_bytes, *a, **kw):
        self.output_path = output_path
        self.expected_bytes = expected_bytes
        self.ready_event = threading.Event()
        self.capture_started_event = threading.Event()
        self.received_bytes = expected_bytes
        self.sequence_gaps = 0
        self.byte_counter_gaps = 0
        self.capture_complete = True
        self.byte_counter_discontinuity_count = 0
        self.missing_payload_bytes = 0
        self.overlap_payload_bytes = 0
        self.packet_records = []
        self.failure_reason = None

    def start(self):
        with open(self.output_path, "wb") as f:
            f.write(b"A" * self.expected_bytes)
        self.ready_event.set()

    def join(self, timeout=None):
        pass

    def stop(self):
        pass

    def is_alive(self):
        return False


# ---------------------------------------------------------------------------
# Run helper — swaps module-level classes and patches time.sleep to no-op
# ---------------------------------------------------------------------------

def _run(tmp_path, dca_cli_mock):
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_recv = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        with patch.object(cs.time, "sleep"):
            prof = RadarProfile.from_smoke_v1()
            return cs.run_capture(
                tmp_path, ["config1"], prof,
                guard_frames=1, dca_cli=dca_cli_mock,
            )
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_recv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResetFpgaRecovery:

    def test_a_reset_succeeds_normally(self, tmp_path):
        """A – reset succeeds normally → continue."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _ok_result()
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is True
        dca.reset_fpga.assert_called_once()
        dca.query_sys_status.assert_not_called()

    def test_b_transient_disconnect_recovers(self, tmp_path):
        """B – reset returns disconnected but query_sys_status recovers."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _transient_fail_result()
        dca.query_sys_status.side_effect = [
            _sys_status_fail(),
            _sys_status_fail(),
            _sys_status_ok(),
        ]
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is True
        dca.reset_fpga.assert_called_once()
        assert dca.query_sys_status.call_count == 3

    def test_b2_transient_disconnect_signed_rc(self, tmp_path):
        """B variant – signed rc=-5 also triggers recovery path."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _transient_fail_signed()
        dca.query_sys_status.return_value = _sys_status_ok()
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is True
        dca.reset_fpga.assert_called_once()

    def test_c_transient_disconnect_never_recovers(self, tmp_path):
        """C – reset disconnected, DCA never recovers → fail."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _transient_fail_result()
        dca.query_sys_status.return_value = _sys_status_fail()
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is False
        assert result.manifest.failure_stage == "dca_initialization"
        assert "did not recover" in result.manifest.failure_reason
        assert dca.query_sys_status.call_count == 5

    def test_d_unrelated_error_fails_immediately(self, tmp_path):
        """D – unrelated error → fail immediately, no polling."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _unrelated_fail_result()

        result = _run(tmp_path, dca)

        assert result.success is False
        assert result.manifest.failure_stage == "dca_initialization"
        dca.query_sys_status.assert_not_called()

    def test_e_configure_fpga_failure(self, tmp_path):
        """E – configure_fpga failure still aborts."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _ok_result()
        dca.configure_fpga.return_value = _fpga_fail()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is False
        assert result.manifest.failure_stage == "dca_initialization"
        assert "configure_fpga" in result.manifest.failure_reason

    def test_f_configure_record_failure(self, tmp_path):
        """F – configure_record failure still aborts."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _ok_result()
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_fail()

        result = _run(tmp_path, dca)

        assert result.success is False
        assert result.manifest.failure_stage == "dca_initialization"
        assert "configure_record" in result.manifest.failure_reason

    def test_g_reset_called_only_once(self, tmp_path):
        """G – reset_fpga is called ONLY ONCE during recovery."""
        dca = MagicMock()
        dca.reset_fpga.return_value = _transient_fail_result()
        dca.query_sys_status.side_effect = [
            _sys_status_fail(),
            _sys_status_ok(),
        ]
        dca.configure_fpga.return_value = _fpga_ok()
        dca.configure_record.return_value = _record_ok()

        result = _run(tmp_path, dca)

        assert result.success is True
        assert dca.reset_fpga.call_count == 1
