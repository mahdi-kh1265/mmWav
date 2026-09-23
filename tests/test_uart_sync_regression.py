"""Regression tests for UART startup synchronization during capture.

Covers:
  - Boot banner racing with first config command (flushCfg)
  - Prompt synchronization before config commands
  - Cleanup: stop_record only if DCA was armed
  - Normal (already-synchronized) path unchanged

No live hardware required.  Uses monkeypatching / mocking only.
"""
from __future__ import annotations

import re
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call, PropertyMock

import pytest

from awr2944_dca.headless_serial import AwrUartConnection, CommandResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BOOT_BANNER = (
    "\r\n"
    "000000 Hz !!!\r\n"
    "INFO: Bootloader_runCpu:158: CPU c66ss0 is initialized to 360000000 Hz !!!\r\n"
    "INFO: Bootloader_runSelfCpu:220: All done, resetting self ...\r\n"
    "******************************************\r\n"
    "AWR294X MMW Demo 04.07.02.01\r\n"
    "******************************************\r\n"
    "mmwDemo:/>"
)

NORMAL_PROMPT = "mmwDemo:/>"
DONE_RESPONSE = "Done\r\nmmwDemo:/>"
SENSOR_STOP_RESPONSE = "sensorStop\r\nDone\r\nmmwDemo:/>"


class FakeSerial:
    """Simulate pyserial.Serial with controllable read buffer."""

    def __init__(self, staged_reads: list[bytes] | None = None):
        self._staged = list(staged_reads or [])
        self._read_index = 0
        self.timeout = 5.0
        self._written = []
        self._input_cleared_count = 0

    def read(self, size: int = 1) -> bytes:
        if self._read_index < len(self._staged):
            data = self._staged[self._read_index]
            self._read_index += 1
            return data
        return b""

    def read_until(self, expected: bytes) -> bytes:
        collected = b""
        for chunk in self._staged[self._read_index:]:
            collected += chunk
            self._read_index += 1
            if expected in collected:
                break
        return collected

    def write(self, data: bytes) -> int:
        self._written.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        self._input_cleared_count += 1

    def close(self) -> None:
        pass

    @property
    def in_waiting(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# send_command tests — boot banner must NOT be treated as command success
# ---------------------------------------------------------------------------

class TestSendCommandBootBannerRejection:
    """Verify that send_command does NOT treat boot banner text as success."""

    def test_boot_banner_is_not_flushCfg_success(self):
        """If the read buffer contains boot banner + prompt but no 'Done',
        send_command for flushCfg must return success=False."""
        fake = FakeSerial(staged_reads=[BOOT_BANNER.encode("utf-8")])
        conn = AwrUartConnection("COM_FAKE")
        conn._serial = fake
        conn._connected = True

        result = conn.send_command("flushCfg", timeout=2.0)

        # The response contains mmwDemo:/> but NOT "Done"
        assert result.prompt_recovered is True
        assert result.success is False, (
            "Boot banner text must NOT be treated as flushCfg success. "
            "send_command requires 'Done' in the response."
        )

    def test_normal_done_response_is_success(self):
        """Normal 'Done' response is correctly classified as success."""
        fake = FakeSerial(staged_reads=[
            b"flushCfg\r\nDone\r\nmmwDemo:/>"
        ])
        conn = AwrUartConnection("COM_FAKE")
        conn._serial = fake
        conn._connected = True

        result = conn.send_command("flushCfg", timeout=2.0)

        assert result.success is True
        assert result.prompt_recovered is True

    def test_invalid_command_is_failure(self):
        """'Error: Invalid command' is correctly classified as failure."""
        fake = FakeSerial(staged_reads=[
            b"badCmd\r\nError: Invalid command\r\nmmwDemo:/>"
        ])
        conn = AwrUartConnection("COM_FAKE")
        conn._serial = fake
        conn._connected = True

        result = conn.send_command("badCmd", timeout=2.0)

        assert result.success is False
        assert "Error" in result.error_msg or "error" in result.error_msg

    def test_timeout_is_failure(self):
        """Timeout (no data at all) is correctly classified as failure."""
        fake = FakeSerial(staged_reads=[])  # No data
        conn = AwrUartConnection("COM_FAKE")
        conn._serial = fake
        conn._connected = True

        result = conn.send_command("flushCfg", timeout=0.5)

        assert result.success is False
        assert result.timed_out is True


# ---------------------------------------------------------------------------
# Capture session: UART prompt synchronization
# ---------------------------------------------------------------------------

class TestCaptureUartPromptSync:
    """Verify that run_capture synchronizes to mmwDemo:/> before sending commands."""

    def test_prompt_sync_is_called_before_sensorStop(self):
        """run_capture must call read_until_prompt BEFORE sending sensorStop."""
        from awr2944_dca.capture_session import run_capture

        call_order = []

        # Mock AwrUartConnection
        mock_conn = MagicMock(spec=AwrUartConnection)
        mock_serial = MagicMock()
        mock_conn._serial = mock_serial

        def track_read_until_prompt(*args, **kwargs):
            call_order.append("read_until_prompt")
            return "boot banner\r\nmmwDemo:/>"

        def track_send_command(cmd, **kwargs):
            call_order.append(f"send_command:{cmd}")
            if cmd == "sensorStop":
                return CommandResult(
                    command="sensorStop", response_lines=["Done"],
                    success=True, prompt_recovered=True, elapsed_s=0.1,
                )
            # For config commands, return failure to stop early
            return CommandResult(
                command=cmd, response_lines=["Done"],
                success=True, prompt_recovered=True, elapsed_s=0.1,
            )

        mock_conn.read_until_prompt = track_read_until_prompt
        mock_conn.send_command = track_send_command
        mock_conn._record = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        # Provide minimal profile
        mock_profile = MagicMock()
        mock_profile.frame_count = 2
        mock_profile.chirps_per_frame = 128
        mock_profile.rx_count = 4
        mock_profile.adc_samples = 256

        with patch("awr2944_dca.capture_session.AwrUartConnection", return_value=mock_conn):
            with patch("awr2944_dca.capture_session.DirectUdpCapture") as mock_dca:
                mock_dca_inst = MagicMock()
                mock_dca_inst.stop_record.return_value = True
                mock_dca.return_value = mock_dca_inst
                with patch("awr2944_dca.capture_session.UdpReceiverThread") as mock_recv:
                    mock_recv_inst = MagicMock()
                    mock_recv_inst.is_alive.return_value = False
                    mock_recv_inst.received_bytes = 0
                    mock_recv_inst.capture_complete = False
                    mock_recv_inst.sequence_gaps = 0
                    mock_recv_inst.byte_counter_discontinuity_count = 0
                    mock_recv_inst.missing_payload_bytes = 0
                    mock_recv_inst.overlap_payload_bytes = 0
                    mock_recv_inst.packet_records = []
                    mock_recv.return_value = mock_recv_inst

                    try:
                        run_capture(
                            output_dir=Path("C:/tmp/test_capture"),
                            sdk_cli_commands=["flushCfg", "dfeDataOutputMode 1"],
                            profile=mock_profile,
                            guard_frames=1,
                            com_port="COM_FAKE",
                        )
                    except Exception:
                        pass  # We expect failure since we're mocking everything

        # Assert read_until_prompt comes BEFORE any send_command
        assert "read_until_prompt" in call_order, "read_until_prompt must be called"
        prompt_idx = call_order.index("read_until_prompt")
        first_send_idx = next(
            (i for i, c in enumerate(call_order) if c.startswith("send_command:")),
            len(call_order),
        )
        assert prompt_idx < first_send_idx, (
            f"read_until_prompt (index {prompt_idx}) must come before "
            f"first send_command (index {first_send_idx}). Order: {call_order}"
        )


# ---------------------------------------------------------------------------
# Cleanup: stop_record guard
# ---------------------------------------------------------------------------

class TestCleanupStopRecordGuard:
    """Verify that DCA stop_record is only sent if recording was armed."""

    def test_stop_record_not_called_when_uart_fails(self):
        """If capture fails at uart_config, stop_record (0x0006) must NOT be sent."""
        from awr2944_dca.capture_session import run_capture

        mock_conn = MagicMock(spec=AwrUartConnection)
        mock_serial = MagicMock()
        mock_conn._serial = mock_serial
        mock_conn._record = MagicMock()

        # Sync fails — no prompt received
        mock_conn.read_until_prompt.return_value = "garbage\r\nno prompt here"
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.send_command.return_value = CommandResult(
            command="sensorStop", response_lines=["Done"],
            success=True, prompt_recovered=True, elapsed_s=0.1,
        )

        mock_profile = MagicMock()
        mock_profile.frame_count = 2
        mock_profile.chirps_per_frame = 128
        mock_profile.rx_count = 4
        mock_profile.adc_samples = 256

        with patch("awr2944_dca.capture_session.AwrUartConnection", return_value=mock_conn):
            with patch("awr2944_dca.capture_session.DirectUdpCapture") as mock_dca:
                mock_dca_inst = MagicMock()
                mock_dca.return_value = mock_dca_inst
                with patch("awr2944_dca.capture_session.UdpReceiverThread") as mock_recv:
                    mock_recv_inst = MagicMock()
                    mock_recv_inst.is_alive.return_value = False
                    mock_recv_inst.received_bytes = 0
                    mock_recv_inst.packet_records = []
                    mock_recv.return_value = mock_recv_inst
                    with patch("awr2944_dca.capture_session.profile_to_manifest_dict", return_value={}):
                        with patch("awr2944_dca.capture_session.CaptureManifest.to_json"):

                            result = run_capture(
                                output_dir=Path("C:/tmp/test_capture2"),
                                sdk_cli_commands=["flushCfg"],
                                profile=mock_profile,
                                guard_frames=1,
                                com_port="COM_FAKE",
                            )

        # Capture must fail
        assert result.success is False

        # stop_record must NOT have been called since DCA was never armed
        mock_dca_inst.stop_record.assert_not_called()

    def test_stop_record_called_when_dca_was_armed(self):
        """If DCA was armed (start_record succeeded), stop_record must be called in cleanup."""
        from awr2944_dca.capture_session import run_capture

        mock_conn = MagicMock(spec=AwrUartConnection)
        mock_serial = MagicMock()
        mock_conn._serial = mock_serial
        mock_conn._record = MagicMock()

        # Sync succeeds
        mock_conn.read_until_prompt.return_value = "boot\r\nmmwDemo:/>"

        def fake_send_command(cmd, **kwargs):
            return CommandResult(
                command=cmd, response_lines=["Done"],
                success=True, prompt_recovered=True, elapsed_s=0.1,
            )

        mock_conn.send_command = fake_send_command
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_profile = MagicMock()
        mock_profile.frame_count = 2
        mock_profile.chirps_per_frame = 128
        mock_profile.rx_count = 4
        mock_profile.adc_samples = 256

        with patch("awr2944_dca.capture_session.AwrUartConnection", return_value=mock_conn):
            with patch("awr2944_dca.capture_session.DirectUdpCapture") as mock_dca:
                mock_dca_inst = MagicMock()
                mock_dca_inst.start_record.return_value = True  # DCA armed!
                mock_dca_inst.stop_record.return_value = True
                mock_dca.return_value = mock_dca_inst
                with patch("awr2944_dca.capture_session.UdpReceiverThread") as mock_recv:
                    mock_recv_inst = MagicMock()
                    mock_recv_inst.is_alive.return_value = False
                    mock_recv_inst.received_bytes = 0
                    mock_recv_inst.capture_complete = False  # Will cause failure
                    mock_recv_inst.failure_reason = "test: simulated incomplete"
                    mock_recv_inst.ready_event = MagicMock()
                    mock_recv_inst.ready_event.wait.return_value = True
                    mock_recv_inst.capture_started_event = MagicMock()
                    mock_recv_inst.sequence_gaps = 0
                    mock_recv_inst.byte_counter_discontinuity_count = 0
                    mock_recv_inst.missing_payload_bytes = 0
                    mock_recv_inst.overlap_payload_bytes = 0
                    mock_recv_inst.packet_records = []
                    mock_recv.return_value = mock_recv_inst
                    with patch("awr2944_dca.capture_session.profile_to_manifest_dict", return_value={}):

                        result = run_capture(
                            output_dir=Path("C:/tmp/test_capture3"),
                            sdk_cli_commands=["flushCfg"],
                            profile=mock_profile,
                            guard_frames=1,
                            com_port="COM_FAKE",
                        )

        # stop_record MUST be called since DCA was armed
        mock_dca_inst.stop_record.assert_called_once()
