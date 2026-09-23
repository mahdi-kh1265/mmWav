import hashlib
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from awr2944_dca.direct_udp_capture import DirectUdpCapture, UdpReceiverThread
from awr2944_dca.headless_serial import AwrUartConnection
from awr2944_dca.capture_manifest import CaptureManifest, profile_to_manifest_dict
from awr2944_dca.dsp.config import RadarProfile
from awr2944_dca.awr2944_adc import expected_raw_dca_bytes, active_payload_bytes, AWR2944AdcLayout
from awr2944_dca.dca_cli import DcaCmdResult

logger = logging.getLogger(__name__)

class CaptureNetworkError(Exception):
    pass

class DcaInitializationError(CaptureNetworkError):
    pass

class CaptureTimeoutError(Exception):
    pass

def validate_dca_cmd_result(result: 'DcaCmdResult', operation: str) -> None:
    """Validate a DcaCmdResult, raising DcaInitializationError on failure."""
    stdout_lower = result.stdout.lower()
    stderr_lower = result.stderr.lower()
    
    if result.returncode == -1 and ("timeout" in stdout_lower or "timeout" in stderr_lower):
        raise DcaInitializationError(
            f"{operation} timed out after {result.elapsed_s:.1f}s\n"
            f"  exe: {result.exe_path}\n"
            f"  stderr: {result.stderr}"
        )
        
    if not result.success:
        msg = (
            f"{operation} failed (rc={result.returncode})\n"
            f"  exe: {result.exe_path}\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        raise DcaInitializationError(msg)


class CaptureResult:
    def __init__(self, capture_dir: Path, manifest: CaptureManifest):
        self.capture_dir = capture_dir
        self.manifest = manifest
        self.native_bin = self.capture_dir / "adc_data.bin"
        self.canonical_bin = self.capture_dir / "adc_data_canonical.bin"
        self.viewer_payload_mat = self.capture_dir / "viewer_payload.mat"

    @property
    def success(self) -> bool:
        return self.manifest.success

    def summary(self):
        if not self.success:
            print("=== CAPTURE_FAILED ===")
            print(f"reason: {self.manifest.failure_reason}")
            print(f"captured_bytes: {self.manifest.captured_native_bytes}")
            print(f"expected_bytes: {self.manifest.expected_native_bytes}")
            return
            
        print("=== Capture Summary ===")
        print(f"Directory: {self.capture_dir}")
        print(f"Native Frames: {self.manifest.total_frames} ({self.native_bin.stat().st_size} bytes)")
        if self.canonical_bin.exists():
            print(f"Canonical Frames: {self.manifest.canonical_frame_count} ({self.canonical_bin.stat().st_size} bytes)")
        else:
            print("Canonical extraction skipped/failed.")

    def open_viewer(self):
        if not self.success:
            raise RuntimeError("Cannot open viewer on a failed capture result.")
            
        if not self.viewer_payload_mat.exists():
            print("Viewer payload not found. Generating...")
            # We'll rely on the external caller or lab.py to run the dsp pipeline if needed
        else:
            print("Opening standalone viewer...")
            # Ideally launch Matlab here, but we will leave that to lab.py or viewer.py wrapper


def run_capture(
    output_dir: Path,
    sdk_cli_commands: list[str],
    profile: RadarProfile,
    guard_frames: int = 1,
    com_port: str = "COM8",
    host_ip: str = "192.168.33.30",
    dca_ip: str = "192.168.33.180",
    timeout_s: float = 5.0,
    dca_cli = None, # Type: DcaCli or None
    dca_configuration: dict | None = None,
) -> CaptureResult:
    """
    Execute a production hardware capture bypassing mmWave Studio.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    native_path = output_dir / "adc_data.bin"
    canonical_path = output_dir / "adc_data_canonical.bin"
    manifest_path = output_dir / "manifest.json"

    # Pre-check config
    total_frames = profile.frame_count
    canonical_frames = total_frames - guard_frames
    if canonical_frames <= 0:
        raise ValueError(f"Total frames ({total_frames}) must be greater than guard frames ({guard_frames})")

    # Use native stream byte calculations
    layout = AWR2944AdcLayout()
    native_bytes_per_frame = expected_raw_dca_bytes(1, profile.chirps_per_frame, profile.rx_count, profile.adc_samples, layout)
    logical_bytes_per_frame = active_payload_bytes(1, profile.chirps_per_frame, profile.rx_count, profile.adc_samples)
    
    expected_native_bytes = native_bytes_per_frame * total_frames
    expected_canonical_bytes = native_bytes_per_frame * canonical_frames

    dca = DirectUdpCapture(host_ip=host_ip, dca_ip=dca_ip)
    
    # Initialize failure tracking
    success = True
    failure_stage = None
    failure_reason = None
    captured_native_bytes = 0
    
    receiver = UdpReceiverThread(
        output_path=native_path,
        expected_bytes=expected_native_bytes,
        host_ip=host_ip,
        data_port=4098
    )

    dca_armed = False  # Track whether DCA recording was actually armed

    try:
        # 1. Ensure Radar is Idle & Send Config (excluding sensorStart)
        failure_stage = "uart_config"
        logger.info("Configuring radar via UART...")
        with AwrUartConnection(com_port, 115200) as conn:
            # ---- UART Prompt Synchronization ----
            # Opening the serial port may toggle DTR, which can trigger an
            # AWR2944 board reset/reboot.  The boot banner (several lines ending
            # in "mmwDemo:/>") streams for up to ~5 s.  If we send commands
            # immediately, the boot banner text is misinterpreted as the response
            # to the first config command (e.g. flushCfg).
            #
            # Fix: synchronize to a stable prompt BEFORE sending any command.
            # This mirrors the proven logic in _probe_uart_prompt:
            #   1. Drain any stale/boot data sitting in the OS receive buffer
            #   2. Send a bare newline (benign wake-up)
            #   3. Wait for mmwDemo:/> with a generous timeout
            #   4. Only then proceed with sensorStop + config
            logger.info("Synchronizing to mmwDemo:/> prompt...")
            conn._serial.reset_input_buffer()
            conn._serial.write(b"\n")
            conn._record("TX", "<sync newline>")
            sync_text = conn.read_until_prompt(timeout=10.0)
            logger.info("Prompt sync received: %r", sync_text[-80:] if sync_text else "")

            if "mmwDemo:/>" not in sync_text:
                raise RuntimeError(
                    f"UART prompt synchronization failed: did not receive "
                    f"mmwDemo:/> within 10 s. Received: {sync_text!r}"
                )

            # Drain any trailing data after the prompt
            conn._serial.reset_input_buffer()

            # Now the CLI is ready — send sensorStop to ensure idle state
            conn.send_command("sensorStop")

            # ---- Post-sensorStop re-synchronization ----
            # On the AWR2944, sensorStop can trigger a full board reboot
            # (QSPI bootloader → application restart → new mmwDemo:/>).
            # send_command sees "Done" and returns immediately, but the
            # reboot text is still streaming.  Without a second sync,
            # the next config command (e.g. flushCfg) collects the tail
            # of the boot banner instead of its own response.
            logger.info("Re-synchronizing after sensorStop (may absorb reboot)...")
            conn._serial.reset_input_buffer()
            conn._serial.write(b"\n")
            conn._record("TX", "<post-sensorStop sync>")
            post_stop_text = conn.read_until_prompt(timeout=10.0)
            logger.info("Post-sensorStop sync: %r", post_stop_text[-80:] if post_stop_text else "")

            if "mmwDemo:/>" not in post_stop_text:
                raise RuntimeError(
                    f"Post-sensorStop re-sync failed: did not receive "
                    f"mmwDemo:/> within 10 s. Received: {post_stop_text!r}"
                )
            conn._serial.reset_input_buffer()
            
            for line in sdk_cli_commands:
                line = line.strip()
                if not line or line.startswith("%") or line == "sensorStart" or line == "sensorStop":
                    continue
                res = conn.send_command(line)
                
                # Strict validation
                if not res.success:
                    raise RuntimeError(f"Radar rejected config line: {line}\nResponse: {res.response_lines}")
                
                resp_text = "\\n".join(res.response_lines).lower()
                if "invalid command" in resp_text or "error" in resp_text or "exception" in resp_text or res.timed_out:
                    raise RuntimeError(f"Radar rejected config line: {line}\nResponse: {res.response_lines}")
                    
                # We expect the firmware to print 'Done' when a command succeeds.
                # However, res.success already checks for "Done" and no "Error" in headless_serial.py.
                if not any("Done" in text for text in res.response_lines) and not any("Done" in text for text in [resp_text]):
                    raise RuntimeError(f"Radar command incomplete (no Done): {line}\nResponse: {res.response_lines}")

            # 2. DCA Full Initialization (if provided)
            if dca_cli:
                failure_stage = "dca_initialization"
                logger.info("Resetting DCA FPGA...")
                res_reset = dca_cli.reset_fpga()
                validate_dca_cmd_result(res_reset, "reset_fpga")
                logger.info("DCA FPGA reset accepted.")
                
                time.sleep(1.0)
                
                logger.info("Configuring DCA FPGA/network...")
                res_fpga = dca_cli.configure_fpga()
                validate_dca_cmd_result(res_fpga, "configure_fpga")
                logger.info("DCA FPGA/network configuration accepted.")
                
                logger.info("Configuring DCA record parameters...")
                res_record = dca_cli.configure_record()
                validate_dca_cmd_result(res_record, "configure_record")
                logger.info("DCA record configuration accepted.")
            
            # 3. Start Receiver Thread & Wait for Bind
            failure_stage = "receiver_bind"
            logger.info(f"Starting receiver thread. Expecting {expected_native_bytes} bytes.")
            receiver.start()
            
            if not receiver.ready_event.wait(timeout=2.0):
                raise CaptureNetworkError(receiver.failure_reason or "Failed to bind UDP receiver socket. Check IP and port ownership.")

            # 4. Arm DCA
            failure_stage = "dca_arm"
            logger.info("Arming DCA via UDP start_record...")
            if not dca.start_record():
                raise CaptureNetworkError("Failed to arm DCA (timeout on 0x0005).")
            dca_armed = True

            # 5. Signal trigger and send sensorStart
            failure_stage = "trigger"
            logger.info("Triggering radar...")
            receiver.capture_started_event.set()
            start_res = conn.send_command("sensorStart")
            logger.info(f"sensorStart response: {start_res.response_lines}")
            
            # 6. Wait for capture
            failure_stage = "streaming"
            logger.info("Waiting for capture to complete...")
            receiver.join(timeout=timeout_s)
            
            if receiver.is_alive():
                receiver.stop()
                receiver.join(timeout=2.0)
                raise CaptureTimeoutError("Receiver thread timed out before completion.")
                
            if not receiver.capture_complete:
                raise RuntimeError(receiver.failure_reason or "Receiver exited before expected bytes reached.")
                
            if receiver.sequence_gaps > 0 or receiver.byte_counter_discontinuity_count > 0 or receiver.missing_payload_bytes > 0 or receiver.overlap_payload_bytes > 0:
                raise RuntimeError(
                    f"Stream integrity error: "
                    f"{receiver.sequence_gaps} seq gaps, "
                    f"{receiver.byte_counter_discontinuity_count} byte discontinuities, "
                    f"{receiver.missing_payload_bytes} missing bytes, "
                    f"{receiver.overlap_payload_bytes} overlap bytes."
                )

            failure_stage = None

    except Exception as e:
        success = False
        failure_reason = str(e)
        logger.error(f"Capture failed at stage '{failure_stage}': {e}")
    finally:
        # 7. Controlled Cleanup
        logger.info("Performing cleanup...")
        if receiver.is_alive():
            receiver.stop()
            receiver.join(timeout=2.0)
            
        captured_native_bytes = receiver.received_bytes
        
        # Stop DCA — only if recording was actually armed (0x0005 succeeded).
        # Sending stop_record (0x0006) when DCA was never armed causes a
        # spurious UDP timeout that masks the real failure.
        if dca_armed:
            try:
                dca.stop_record()
            except Exception as e:
                logger.error(f"Failed to stop DCA during cleanup: {e}")
        else:
            logger.debug("Skipping DCA stop_record: recording was never armed.")
            
        # Stop Radar
        try:
            with AwrUartConnection(com_port, 115200) as conn:
                conn.send_command("sensorStop")
        except Exception as e:
            logger.error(f"Failed to send sensorStop during cleanup: {e}")

    # 8. Extract canonical data only if successful
    native_sha = ""
    can_sha = ""
    if success and native_path.exists():
        if captured_native_bytes != expected_native_bytes:
            success = False
            failure_stage = "validation"
            failure_reason = f"Native byte count mismatch: got {captured_native_bytes}, expected {expected_native_bytes}"
            logger.error(failure_reason)
        else:
            with open(native_path, "rb") as f:
                native_data = f.read()
            canonical_data = native_data[:expected_canonical_bytes]
            with open(canonical_path, "wb") as f:
                f.write(canonical_data)
                
            native_sha = hashlib.sha256(native_data).hexdigest()
            can_sha = hashlib.sha256(canonical_data).hexdigest()
    else:
        success = False
        if not failure_reason:
            failure_reason = "Native data file not created or incomplete."

    # 9. Save manifest
    manifest = CaptureManifest(
        total_frames=total_frames,
        guard_frame_count=guard_frames,
        canonical_frame_count=canonical_frames,
        native_sha256=native_sha,
        canonical_sha256=can_sha,
        packet_count=0, # To be implemented via strict parser
        sequence_gaps=receiver.sequence_gaps,
        byte_counter_discontinuity_count=receiver.byte_counter_discontinuity_count,
        missing_payload_bytes=receiver.missing_payload_bytes,
        overlap_payload_bytes=receiver.overlap_payload_bytes,
        byte_counter_gaps=None,
        capture_timestamp=datetime.now().isoformat(),
        sdk_cli_commands=sdk_cli_commands,
        dca_configuration=dca_configuration or {},
        parser_layout_version=layout.layout_version,
        dsp_config_version="1.0",
        
        # V3 specific fields
        manifest_schema_version=3,
        profile=profile_to_manifest_dict(profile),
        native_byte_count=expected_native_bytes,
        canonical_native_byte_count=expected_canonical_bytes,
        logical_byte_count=logical_bytes_per_frame * total_frames,
        canonical_logical_byte_count=logical_bytes_per_frame * canonical_frames,
        stream_layout=layout.layout_version,
        active_lane_indices=list(layout.active_slot_indices),
        physical_lvds_lanes=layout.physical_lvds_lanes,
        dca_word_slots=layout.dca_word_slots,
        storage_expansion_factor=layout.dca_word_slots // layout.physical_lvds_lanes,
        logical_cube_shape=[canonical_frames, profile.chirps_per_frame, profile.rx_count, profile.adc_samples],
        
        # Execution status
        status="complete" if success else "failed",
        success=success,
        failure_stage=failure_stage,
        failure_reason=failure_reason,
        captured_native_bytes=captured_native_bytes,
        expected_native_bytes=expected_native_bytes,
        
        # Packet metadata fields
        packet_metadata_preserved=bool(receiver.packet_records),
        packet_metadata_path="metadata/packet_metadata.jsonl" if receiver.packet_records else None,
        packet_metadata_format="jsonl" if receiver.packet_records else None,
        packet_record_count=len(receiver.packet_records) if receiver.packet_records else None,
    )
    manifest.to_json(manifest_path)
    
    return CaptureResult(capture_dir=output_dir, manifest=manifest)
