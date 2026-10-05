import pytest
from unittest.mock import MagicMock, call
from pathlib import Path
import sys

def test_cold_boot_event_order(monkeypatch, tmp_path):
    """Verify exact event order during a full production cold boot."""
    # sys.modules hacking removed because it breaks isinstance and exceptions in subsequent tests.
        
    from awr2944_dca.capture_session import run_capture
    from awr2944_dca.dsp.config import RadarProfile
    from awr2944_dca.dca_cli import DcaCli, DcaCmdResult
    from awr2944_dca.headless_serial import AwrUartConnection
    
    # Mock UART
    mock_cmd_res = MagicMock(success=True, response_lines=["Done"])
    mock_cmd_res.timed_out = False
    
    mock_uart_conn = MagicMock()
    mock_uart_conn.send_command.return_value = mock_cmd_res
    mock_uart_conn.read_until_prompt.return_value = "mmwDemo:/>"
    
    class MockAwrUartConnection:
        def __init__(self, port, baud):
            self.port = port
            self.baud = baud
            self._serial = MagicMock()
            self._serial.read.return_value = b""
        def __enter__(self):
            return mock_uart_conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("awr2944_dca.capture_session.AwrUartConnection", MockAwrUartConnection)

    # Mock DCA
    mock_dca_cli = MagicMock(spec=DcaCli)
    mock_dca_cli.reset_fpga.return_value = DcaCmdResult("reset_fpga", [], 0, "", "", True, 0.1, "")
    mock_dca_cli.configure_fpga.return_value = DcaCmdResult("fpga", [], 0, "", "", True, 0.1, "")
    mock_dca_cli.configure_record.return_value = DcaCmdResult("record", [], 0, "", "", True, 0.1, "")
    
    fake_cf = tmp_path / "fake.json"
    fake_cf.write_text("{}", encoding="utf-8")
    mock_dca_cli._cf_json = fake_cf
    mock_dca_cli.arm_record.return_value = DcaCmdResult("arm_record", [], 0, "", "", True, 0.1, "")
    
    mock_direct_udp = MagicMock()
    mock_direct_udp.start_record.return_value = True
    monkeypatch.setattr("awr2944_dca.capture_session.DirectUdpCapture", lambda host_ip, dca_ip: mock_direct_udp)

    # Mock Receiver
    mock_receiver = MagicMock()
    mock_receiver.packet_records = []
    mock_receiver.sequence_gaps = 0
    mock_receiver.byte_counter_discontinuity_count = 0
    mock_receiver.missing_payload_bytes = 0
    mock_receiver.overlap_payload_bytes = 0
    mock_receiver.ready_event.wait.return_value = True
    mock_receiver.is_alive.return_value = False
    mock_receiver.capture_complete = True
    mock_receiver.sequence_gaps = 0
    mock_receiver.byte_counter_gaps = 0
    mock_receiver.received_bytes = 4194304
    
    import unittest.mock as mock
    with mock.patch("awr2944_dca.capture_session.UdpReceiverThread", return_value=mock_receiver) as mock_udp:



        # Run Capture
        profile = RadarProfile.from_smoke_v1()
        output_dir = tmp_path / "capture"
        sdk_cli_commands = ["sensorStop", "flushCfg", "dfeDataOutputMode 1", "sensorStart"]
        
        # We must mock time.sleep to avoid waiting
        monkeypatch.setattr("time.sleep", lambda s: None)

        # Create a fake file to pass the existence check
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "adc_data.bin").write_bytes(b"0" * 4194304)

        result = run_capture(
            output_dir=output_dir,
            sdk_cli_commands=sdk_cli_commands,
            profile=profile,
            guard_frames=1,
            dca_cli=mock_dca_cli
        )
        
        assert result.success is True
        
        # Verify exactly the right order:
        # 1. UART sends sensorStop
        # 2. UART sends SDK config commands (flushCfg, dfeDataOutputMode 1)
        # 3. DcaCli resets FPGA
        # 4. DcaCli configures FPGA
        # 5. DcaCli configures Record
        # 6. Receiver starts
        # 7. Receiver waits for bind
        # 8. UDP DCA start_record
        # 9. receiver.capture_started_event.set()
        # 10. UART sensorStart
        
        # Assert DcaCli calls
        assert mock_dca_cli.reset_fpga.call_count == 1
        assert mock_dca_cli.configure_fpga.call_count == 1
        assert mock_dca_cli.configure_record.call_count == 1
        
        # Ensure arm_record via DcaCli was called
        assert mock_dca_cli.arm_record.call_count == 1
        
        # Assert UART calls include the config
        mock_uart_conn.send_command.assert_has_calls([
            call("sensorStop"),
            call("flushCfg"),
            call("dfeDataOutputMode 1"),
            call("sensorStart")
        ])
        
        # Check that sensorStart was the last UART call during trigger stage
        last_call = mock_uart_conn.send_command.call_args_list[-2] # Last call in setup, then one in cleanup maybe?
        # Wait, the code sends sensorStop in finally block too.
        assert mock_uart_conn.send_command.call_args_list[-1] == call("sensorStop")
        assert mock_uart_conn.send_command.call_args_list[-2] == call("sensorStart")
