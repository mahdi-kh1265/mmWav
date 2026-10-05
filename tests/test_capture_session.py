import pytest
from pathlib import Path
from awr2944_dca.dsp.config import RadarProfile
from awr2944_dca.awr2944_adc import expected_raw_dca_bytes, active_payload_bytes, AWR2944AdcLayout
from awr2944_dca.capture_manifest import profile_to_manifest_dict, profile_from_manifest_dict
import json

def test_calculate_bytes_per_frame():
    prof = RadarProfile.from_smoke_v1()
    layout = AWR2944AdcLayout()
    native_9 = expected_raw_dca_bytes(9, prof.chirps_per_frame, prof.rx_count, prof.adc_samples, layout)
    assert native_9 == 4718592
    native_8 = expected_raw_dca_bytes(8, prof.chirps_per_frame, prof.rx_count, prof.adc_samples, layout)
    assert native_8 == 4194304
    logical_9 = active_payload_bytes(9, prof.chirps_per_frame, prof.rx_count, prof.adc_samples)
    assert logical_9 == 2359296
    logical_8 = active_payload_bytes(8, prof.chirps_per_frame, prof.rx_count, prof.adc_samples)
    assert logical_8 == 2097152
    expansion = layout.dca_word_slots // layout.physical_lvds_lanes
    assert expansion == 2

def test_profile_json_round_trip():
    prof_orig = RadarProfile.from_smoke_v1()
    manifest_dict = profile_to_manifest_dict(prof_orig)
    json_str = json.dumps(manifest_dict)
    loaded_dict = json.loads(json_str)
    prof_restored = profile_from_manifest_dict(loaded_dict)
    assert prof_orig.start_frequency_hz == prof_restored.start_frequency_hz
    assert prof_orig.slope_hz_per_s == prof_restored.slope_hz_per_s
    assert prof_orig.adc_sample_rate_hz == prof_restored.adc_sample_rate_hz
    assert prof_orig.adc_samples == prof_restored.adc_samples
    assert prof_orig.chirps_per_frame == prof_restored.chirps_per_frame
    assert prof_orig.frame_count == prof_restored.frame_count
    assert prof_orig.rx_count == prof_restored.rx_count
    assert prof_orig.tx_mask == prof_restored.tx_mask
    assert prof_orig.sample_format == prof_restored.sample_format
    assert prof_orig.cube_layout == prof_restored.cube_layout

def test_canonical_extraction(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            return True

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = expected_bytes
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = True
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            with open(self.output_path, 'wb') as f:
                f.write(b'A' * self.expected_bytes)
            self.ready_event.set()

        def join(self, timeout=None):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        import dataclasses
        prof = RadarProfile.from_smoke_v1()
        prof = dataclasses.replace(prof, frame_count=9)
        res = cs.run_capture(tmp_path, ['config1'], prof, guard_frames=1)
        assert res.success is True
        assert res.manifest.total_frames == 9
        assert res.manifest.canonical_frame_count == 8
        assert res.native_bin.stat().st_size == 4718592
        assert res.canonical_bin.stat().st_size == 4194304
        assert res.manifest.logical_cube_shape == [8, 128, 4, 256]
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_canonical_extraction_rejects_incomplete(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            return True

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = 2359296
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = False
            self.failure_reason = 'Receiver exited before expected bytes reached'
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            with open(self.output_path, 'wb') as f:
                f.write(b'A' * self.received_bytes)
            self.ready_event.set()

        def join(self, timeout=None):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['config1'], prof, guard_frames=1)
        assert res.success is False
        assert res.manifest.status == 'failed'
        assert res.manifest.failure_stage == 'streaming'
        assert 'Receiver exited' in res.manifest.failure_reason
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_long_uart_config_receiver_stays_alive(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading
    import time

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            return True

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver(threading.Thread):

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            super().__init__()
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.stop_event = threading.Event()
            self.received_bytes = expected_bytes
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = False
            self.failure_reason = None
            self.phase = 'init'
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def run(self):
            self.phase = 'ready'
            self.ready_event.set()
            while not self.capture_started_event.wait(timeout=0.1):
                if self.stop_event.is_set():
                    return
            self.phase = 'streaming'
            with open(self.output_path, 'wb') as f:
                f.write(b'A' * self.expected_bytes)
            self.capture_complete = True

        def stop(self):
            self.stop_event.set()
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:

        def slow_send_command(self, cmd):
            time.sleep(0.01)

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()
        MockUart.send_command = slow_send_command
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['cmd1', 'cmd2', 'cmd3', 'cmd4', 'cmd5'], prof, guard_frames=1)
        assert res.success is True
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_event_ordering(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading
    event_log = []

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            event_log.append('dca_armed')
            return True

        def stop_record(self):
            pass

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):
            if cmd == 'sensorStop':
                pass
            elif cmd == 'sensorStart':
                event_log.append('sensor_started')
            else:
                event_log.append('configure_radar')

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = expected_bytes
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = True
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            event_log.append('receiver_bound')
            with open(self.output_path, 'wb') as f:
                f.write(b'A' * self.expected_bytes)
            self.ready_event.set()

        def join(self, timeout=None):
            if self.capture_started_event.is_set():
                if 'capture_started' not in event_log:
                    event_log.append('capture_started')
                event_log.append('capture_complete')

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['cmd1'], prof, guard_frames=1)
        assert res.success is True
        dedup_log = []
        for e in event_log:
            if not dedup_log or dedup_log[-1] != e:
                dedup_log.append(e)
        assert dedup_log == ['configure_radar', 'receiver_bound', 'dca_armed', 'sensor_started', 'capture_started', 'capture_complete']
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_zero_byte_capture_is_failure(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            return True

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = 0
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = False
            self.failure_reason = 'First packet timeout'
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            self.ready_event.set()

        def join(self, timeout=None):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['cmd1'], prof, guard_frames=1)
        assert res.success is False
        assert res.manifest.status == 'failed'
        assert res.manifest.failure_stage == 'streaming'
        assert 'timeout' in res.manifest.failure_reason
        assert not res.canonical_bin.exists()
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_stream_integrity_failure(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            return True

    class MockUart:

        def __init__(self, *args, **kwargs):
            class DummySerial:
                timeout = 0
                def reset_input_buffer(self): pass
                def write(self, d): pass
                def read(self, n):
                    import time
                    time.sleep(2.1)
                    return b""
            self._serial = DummySerial()
            self._record = lambda *a, **k: None

        def read_until_prompt(self, timeout=None):
            return "mmwDemo:/>"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                success = True
                timed_out = False
                response_lines = ['Done']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.output_path = output_path
            self.expected_bytes = expected_bytes
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = expected_bytes
            self.byte_counter_gaps = 0
            self.capture_complete = True
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 1
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            with open(self.output_path, 'wb') as f:
                f.write(b'A' * self.expected_bytes)
            self.ready_event.set()

        def join(self, timeout=None):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['cmd1'], prof, guard_frames=1)
        assert res.success is False
        assert res.manifest.status == 'failed'
        assert res.manifest.failure_stage == 'streaming'
        assert 'integrity' in res.manifest.failure_reason
        assert res.manifest.sequence_gaps == 1
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver

def test_pre_trigger_cleanup(tmp_path):
    import awr2944_dca.capture_session as cs
    import threading

    class MockDca:

        def __init__(self, *args, **kwargs):
            pass

        def start_record(self):
            return True

        def stop_record(self):
            pass

    class MockUart:

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_command(self, cmd):

            class Res:
                response_lines = ['Error -1']
            return Res()

    class MockReceiver:

        def __init__(self, output_path, expected_bytes, *args, **kwargs):
            self.ready_event = threading.Event()
            self.capture_started_event = threading.Event()
            self.received_bytes = 0
            self.sequence_gaps = 0
            self.byte_counter_gaps = 0
            self.capture_complete = False
            self.failure_reason = None
            self.byte_counter_discontinuity_count = 0
            self.sequence_gaps = 0
            self.missing_payload_bytes = 0
            self.overlap_payload_bytes = 0
            self.packet_records = []

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return False
    orig_dca = cs.DirectUdpCapture
    orig_uart = cs.AwrUartConnection
    orig_receiver = cs.UdpReceiverThread
    cs.DirectUdpCapture = MockDca
    cs.AwrUartConnection = MockUart
    cs.UdpReceiverThread = MockReceiver
    try:
        prof = RadarProfile.from_smoke_v1()
        res = cs.run_capture(tmp_path, ['cmd1'], prof, guard_frames=1)
        assert res.success is False
        assert res.manifest.status == 'failed'
        assert res.manifest.failure_stage == 'uart_config'
    finally:
        cs.DirectUdpCapture = orig_dca
        cs.AwrUartConnection = orig_uart
        cs.UdpReceiverThread = orig_receiver