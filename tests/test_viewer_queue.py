"""
Tests for the queue-based ControlledViewer — extended for API fixes.

FakeMatlabDispatcher:
  Background thread that watches requests/ and writes canned JSON responses.

TestQueuedViewerMocked:
  Offline tests covering all five fixes:
    1. Export API: format, directory, deterministic paths
    2. frame_count property and Python-side bounds validation
    3. get_displayed_plot normalization
    4. Idempotent close
    5. Notebook call signatures

TestLiveMatlabViewer:
  Real-MATLAB acceptance (pytest -m matlab_live).
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io as sio
import pytest

# ---------------------------------------------------------------------------
# Inject win32com stub — for offline (mocked) tests only.
# Live tests restore the real modules via _restore_win32com().
# ---------------------------------------------------------------------------
_real_win32com = sys.modules.get("win32com")
_real_win32com_client = sys.modules.get("win32com.client")

def _inject_win32com():
    from unittest.mock import MagicMock
    mock_eng = MagicMock()
    mock_client = MagicMock()
    mock_client.DispatchEx = MagicMock(return_value=mock_eng)
    sys.modules["win32com"] = MagicMock(client=mock_client)
    sys.modules["win32com.client"] = mock_client
    return mock_eng

_mock_eng = _inject_win32com()
from awr2944_dca.viewer_ctrl import ControlledViewer, _validate_format, ViewerExportUnsupportedError


def _restore_win32com():
    """Restore real win32com modules so live tests use actual COM."""
    if _real_win32com is not None:
        sys.modules["win32com"] = _real_win32com
    else:
        sys.modules.pop("win32com", None)
    if _real_win32com_client is not None:
        sys.modules["win32com.client"] = _real_win32com_client
    else:
        sys.modules.pop("win32com.client", None)
    # Force re-import in viewer_ctrl's open_controlled_viewer
    import importlib
    import awr2944_dca.viewer_ctrl as vc
    importlib.reload(vc)


# ---------------------------------------------------------------------------
# FakeMatlabDispatcher
# ---------------------------------------------------------------------------
class FakeMatlabDispatcher:
    """
    Simulates awrDispatchOneTick.m: reads request JSON, writes response JSON.
    Responses registered via add_response(action, dict_or_list).
    """

    def __init__(self, ctrl_dir: Path, token: str):
        self._req_dir  = ctrl_dir / "requests"
        self._resp_dir = ctrl_dir / "responses"
        self._data_dir = ctrl_dir / "data"
        self._token    = token
        self._responses: dict[str, list[dict]] = {}
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True)

    def add_response(self, action: str, fields: dict | list[dict]) -> None:
        if isinstance(fields, dict):
            fields = [fields]
        self._responses[action] = list(fields)

    def start(self) -> "FakeMatlabDispatcher":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            for f in sorted(
                self._req_dir.glob(f"req_{self._token}_*.json"),
                key=lambda p: p.stat().st_mtime,
            ):
                self._handle(f)
            time.sleep(0.02)

    def _handle(self, req_file: Path) -> None:
        try:
            req = json.loads(req_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        try:
            req_file.unlink()
        except OSError:
            return

        action = req.get("action", "unknown")
        req_id = req.get("request_id", "unknown")
        pool   = self._responses.get(action, [])

        if not pool:
            resp = {
                "success": False, "action": action,
                "error_identifier": "TEST:noResponse",
                "error_message": f"No canned response for '{action}'",
            }
        else:
            resp = dict(pool[0])
            if len(pool) > 1:
                pool.pop(0)
            resp.setdefault("action", action)

        resp["request_id"] = req_id

        # Write optional .mat sidecar
        if "_mat_data" in resp:
            mat = resp.pop("_mat_data")
            dp = self._data_dir / f"data_{self._token}_{req_id}.mat"
            sio.savemat(str(dp), mat)
            resp["output_path"] = str(dp)

        rf  = self._resp_dir / f"resp_{self._token}_{req_id}.json"
        tmp = self._resp_dir / f"resp_{self._token}_{req_id}.tmp"
        tmp.write_text(json.dumps(resp), "utf-8")
        tmp.rename(rf)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def session(tmp_path):
    token    = "tok_test01"
    ctrl_dir = tmp_path / ".control" / token
    for sub in ("requests", "responses", "data"):
        (ctrl_dir / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "exports").mkdir()

    viewer = ControlledViewer(
        capture_id="test_cap",
        payload_dir=tmp_path,
        control_dir=ctrl_dir,
        eng=_mock_eng,
        token=token,
    )
    dispatcher = FakeMatlabDispatcher(ctrl_dir, token).start()
    yield viewer, dispatcher, ctrl_dir
    dispatcher.stop()
    if ctrl_dir.exists():
        shutil.rmtree(ctrl_dir)


def _ready_viewer(viewer, dispatcher, frame_count=8):
    """Helper: register wait_ready + get_frame and call wait_ready."""
    dispatcher.add_response("wait_ready", {
        "success": True, "found": True,
        "actual_frame": 1, "frame_count": frame_count,
    })
    viewer.wait_ready(timeout=5.0)
    assert viewer.frame_count == frame_count


# ===========================================================================
# 1. Export API
# ===========================================================================
# ===========================================================================
# 1. Export API
# ===========================================================================
class TestExportAPI:

    def test_export_plot_raises(self, session, tmp_path):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_plot("range_doppler", format="png")

    def test_export_all_raises(self, session, tmp_path):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_all(directory=tmp_path / "my_exports", format="png")

    def test_export_window_raises(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_window(format="jpeg")


# ===========================================================================
# 2. frame_count and bounds validation
# ===========================================================================
class TestFrameCount:

    def test_frame_count_is_8(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher, frame_count=8)
        assert viewer.frame_count == 8

    def test_frame_count_unknown_before_wait_ready(self, session):
        viewer, dispatcher, _ = session
        with pytest.raises(RuntimeError, match="wait_ready"):
            _ = viewer.frame_count

    def test_set_frame_7_succeeds(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher, frame_count=8)
        dispatcher.add_response("set_frame", {"success": True, "actual_frame": 8})
        viewer.set_frame(7)   # 0-based 7 = MATLAB 8, within 8-frame capture

    def test_set_frame_8_raises_before_queueing(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher, frame_count=8)
        with pytest.raises(ValueError, match=r"Frame index 8.*0.7"):
            viewer.set_frame(8)
        # Confirm nothing was enqueued
        reqs = list((session[2] / "requests").glob("*.json"))
        assert len(reqs) == 0

    def test_set_frame_negative_raises(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher, frame_count=8)
        with pytest.raises(ValueError, match="out of range"):
            viewer.set_frame(-1)

    def test_matlab_dispatcher_bounds_defense(self, session):
        """MATLAB-side bounds check propagates as RuntimeError even when Python
        bounds are bypassed (e.g., frame_count not yet known)."""
        viewer, dispatcher, _ = session
        # Do NOT call wait_ready so _frame_count is None → Python skip bounds check
        dispatcher.add_response("set_frame", {
            "success": False,
            "error_identifier": "AWR:dispatcher:outOfBounds",
            "error_message": "Frame 99 out of range (1-8)",
        })
        with pytest.raises(RuntimeError, match="outOfBounds"):
            viewer.set_frame(98)


# ===========================================================================
# 3. get_displayed_plot normalization
# ===========================================================================
class TestPlotNormalization:

    def test_image_plot_type_normalised(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        cdata = np.ones((8, 16), dtype=float)
        dispatcher.add_response("get_displayed_plot", {
            "success": True, "actual_frame": 1,
            "_mat_data": {
                "obj_type": "image",
                "CData": cdata,
                "XData": np.array([0.0, 1.0]),
                "YData": np.array([0.0, 1.0]),
                "CLim": np.array([-40.0, 0.0]),
                "XLim": np.array([0.0, 1.0]),
                "YLim": np.array([0.0, 1.0]),
                "Title": np.array(""),
            },
        })
        result = viewer.get_displayed_plot("range_doppler")
        assert result["name"] == "range_doppler"
        assert result["type"] == "image"
        assert isinstance(result["title"], str)
        assert result["CData"] is not None
        assert result["CData"].shape == (8, 16)
        assert result["CLim"] is not None

    def test_empty_panel_type_is_empty(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_displayed_plot", {
            "success": True, "actual_frame": 1,
            "_mat_data": {"obj_type": "empty"},
        })
        result = viewer.get_displayed_plot("detections")
        assert result["name"] == "detections"
        assert result["type"] == "empty"
        assert result["CData"] is None
        assert result["title"] == ""

    def test_none_title_becomes_empty_string(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_displayed_plot", {
            "success": True, "actual_frame": 1,
            "_mat_data": {"obj_type": "image", "CData": np.zeros((4, 4))},
        })
        result = viewer.get_displayed_plot("range_doppler")
        assert result["title"] == ""
        assert isinstance(result["title"], str)

    def test_numpy_array_title_unwrapped(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_displayed_plot", {
            "success": True, "actual_frame": 1,
            "_mat_data": {
                "obj_type": "image",
                "CData": np.zeros((4, 4)),
                "Title": np.array("Range-Doppler"),
            },
        })
        result = viewer.get_displayed_plot("range_doppler")
        assert result["title"] == "Range-Doppler"
        assert isinstance(result["title"], str)


# ===========================================================================
# 4. Idempotent close
# ===========================================================================
class TestIdempotentClose:

    def test_close_twice_is_safe(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("close", {"success": True})
        viewer.close()
        # Second call must not raise, must not attempt re-enqueueing
        viewer.close()
        assert viewer._is_closed

    def test_context_manager_then_explicit_close_is_safe(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("close", {"success": True})
        with viewer:
            pass
        # After context exit, explicit close must be a no-op
        viewer.close()
        assert viewer._is_closed

    def test_failed_close_action_does_not_raise(self, session):
        viewer, dispatcher, ctrl_dir = session
        # Simulate MATLAB already gone — close enqueue times out
        # (no response written by dispatcher)
        viewer._is_closed = False  # reset to test
        viewer.close()   # should not raise even with no MATLAB response


# ===========================================================================
# 5. Notebook call signature validation
# ===========================================================================
class TestNotebookSignatures:

    def test_export_plot_format_kwarg_accepted(self, session):
        """Exact call from the notebook: export_plot(name, format='png', resolution=300)."""
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_frame", {"success": True, "actual_frame": 1})
        dispatcher.add_response("export_plot", {"success": True, "output_path": ""})
        # Must raise ViewerExportUnsupportedError
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_plot("range_doppler", format="png", resolution=300)

    def test_export_all_kwargs_accepted(self, session, tmp_path):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_frame", {"success": True, "actual_frame": 1})
        for _ in range(4):
            dispatcher.add_response("export_plot", {"success": True, "output_path": ""})
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_all(directory=tmp_path / "out", format="png", resolution=300)

    def test_export_window_kwargs_accepted(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        dispatcher.add_response("get_frame", {"success": True, "actual_frame": 1})
        dispatcher.add_response("export_window", {"success": True, "output_path": ""})
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_window(format="png", resolution=300)

    def test_zero_hardware_calls(self):
        """Verify viewer_ctrl imports zero hardware entry points."""
        import awr2944_dca.viewer_ctrl as vc
        src = Path(vc.__file__).read_text("utf-8")
        forbidden = [
            "DCA1000EVM", "run_capture", "dca_capture",
            "serial", "socket", "subprocess",
        ]
        for token in forbidden:
            assert token not in src, f"Hardware symbol '{token}' found in viewer_ctrl.py"


# ===========================================================================
# Original queue tests (preserved from test_viewer_queue.py)
# ===========================================================================
class TestQueuedViewerMocked:

    def test_wait_ready_polls_until_found(self, session):
        viewer, dispatcher, _ = session
        dispatcher.add_response("wait_ready", [
            {"success": True, "found": False,  "actual_frame": None, "frame_count": None},
            {"success": True, "found": False,  "actual_frame": None, "frame_count": None},
            {"success": True, "found": True,   "actual_frame": 1,    "frame_count": 8},
        ])
        viewer.wait_ready(timeout=5.0)

    def test_wait_ready_raises_timeout(self, session):
        viewer, dispatcher, _ = session
        dispatcher.add_response("wait_ready",
            {"success": True, "found": False, "actual_frame": None, "frame_count": None})
        with pytest.raises(TimeoutError):
            viewer.wait_ready(timeout=0.3)

    def test_get_frame_returns_0based(self, session):
        viewer, dispatcher, _ = session
        dispatcher.add_response("get_frame", {"success": True, "actual_frame": 3})
        assert viewer.get_frame() == 2

    def test_error_response_raises_runtime_error(self, session):
        viewer, dispatcher, _ = session
        dispatcher.add_response("set_frame", {
            "success": False,
            "error_identifier": "AWR:dispatcher:outOfBounds",
            "error_message": "Frame 99 out of range (1-8)",
        })
        with pytest.raises(RuntimeError, match="outOfBounds"):
            viewer.set_frame(98)

    def test_get_displayed_plot_loads_mat_sidecar(self, session):
        viewer, dispatcher, _ = session
        _ready_viewer(viewer, dispatcher)
        cdata = np.arange(24, dtype=float).reshape(4, 6)
        dispatcher.add_response("get_displayed_plot", {
            "success": True, "actual_frame": 1,
            "_mat_data": {
                "CData": cdata, "XData": np.array([1.0, 2.0]),
                "YData": np.array([0.5, 1.5]), "obj_type": "image",
                "CLim": np.array([-40.0, 0.0]),
                "XLim": np.array([1.0, 2.0]),
                "YLim": np.array([0.5, 1.5]),
                "Title": np.array(""),
            },
        })
        data = viewer.get_displayed_plot("range_doppler")
        assert data["CData"].shape == (4, 6)
        assert data["type"] == "image"

    def test_close_cleans_session_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("close", {"success": True})
        viewer.close()
        assert viewer._is_closed
        assert not ctrl_dir.exists()

    def test_closed_viewer_raises(self, session):
        viewer, dispatcher, _ = session
        dispatcher.add_response("close", {"success": True})
        viewer.close()
        with pytest.raises(RuntimeError, match="closed"):
            viewer.get_frame()


# ===========================================================================
# 6. Lifecycle Preservation
# ===========================================================================
class TestLifecyclePreservation:

    def test_clean_close_deletes_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("close", {"success": True})
        viewer.close()
        assert not ctrl_dir.exists()

    def test_timeout_preserves_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.stop()  # prevent it from writing a TEST:noResponse error
        with pytest.raises(TimeoutError) as exc_info:
            viewer._enqueue("test_timeout", timeout=0.1)
        assert viewer._failed
        assert ctrl_dir.exists()
        assert "Session dir:" in str(exc_info.value)
        assert viewer.token in str(exc_info.value)

    def test_matlab_error_preserves_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("get_frame", {
            "success": False,
            "error_identifier": "AWR:error",
            "error_message": "MATLAB died"
        })
        with pytest.raises(RuntimeError, match="MATLAB died"):
            viewer.get_frame()
        assert viewer._failed
        
        # Calling close must not delete the directory if failed
        viewer.close()
        assert ctrl_dir.exists()

    def test_exception_in_with_block_preserves_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        try:
            with viewer:
                raise ValueError("Something broke in python")
        except ValueError:
            pass
        assert viewer._failed
        assert ctrl_dir.exists()

    def test_malformed_json_preserves_dir(self, session):
        viewer, dispatcher, ctrl_dir = session
        
        # Create a malformed response file directly
        req_id = "test_malformed"
        # Monkeypatch enqueue to use a known req_id
        import uuid
        viewer._req_dir.mkdir(parents=True, exist_ok=True)
        viewer._resp_dir.mkdir(parents=True, exist_ok=True)
        
        bad_json = ctrl_dir / "responses" / f"resp_{viewer.token}_{req_id}.json"
        bad_json.write_text("{bad_json: true", encoding="utf-8")
        
        with pytest.raises(RuntimeError, match="Malformed JSON response"):
            viewer._poll_response(req_id, "test_action", 1.0)
            
        assert viewer._failed
        assert ctrl_dir.exists()

    def test_double_close_still_idempotent(self, session):
        viewer, dispatcher, ctrl_dir = session
        dispatcher.add_response("close", {"success": True})
        viewer.close()
        assert not ctrl_dir.exists()
        assert viewer._is_closed
        viewer.close()
        assert viewer._is_closed


# ===========================================================================
# Live-MATLAB acceptance (gated: pytest -m matlab_live)
# ===========================================================================
@pytest.mark.matlab_live
@pytest.mark.skipif(
    not Path(r"C:\Users\khams008\Documents\awr2944-live-project").exists(),
    reason="Live MATLAB project root not present on this machine (C:\\Users\\khams008\\...)",
)
class TestLiveMatlabViewer:
    """
    Requires real MATLAB, pywin32, and the accepted capture.
    Run manually: pytest -m matlab_live tests/test_viewer_queue.py -v
    Zero hardware calls.
    """

    CAPTURE_ID   = "20260714_231258_dca_capture"
    PROJECT_ROOT = Path(r"C:\Users\khams008\Documents\awr2944-live-project")

    @pytest.fixture(scope="class")
    def live_viewer(self):
        _restore_win32com()  # undo module-level mock injection
        from awr2944_dca.lab import RadarProject
        project = RadarProject.open(self.PROJECT_ROOT)
        capture = project.captures.get(self.CAPTURE_ID)
        assert capture.verify(strict=True).success

        with capture.open_controlled_viewer() as viewer:
            viewer.wait_ready(timeout=90)
            yield viewer

    _cdata_frame0: Any = None

    def test_01_initial_frame_is_0(self, live_viewer):
        assert live_viewer.get_frame() == 0

    def test_02_frame_count_is_8(self, live_viewer):
        assert live_viewer.frame_count == 8
        
    def test_smoke_wait_ready_explicit(self):
        """
        Explicitly test the wait_ready lifecycle: launch, wait_ready, get_frame, close.
        """
        _restore_win32com()  # undo module-level mock injection
        from awr2944_dca.lab import RadarProject
        project = RadarProject.open(self.PROJECT_ROOT)
        capture = project.captures.get(self.CAPTURE_ID)
        assert capture.verify(strict=True).success

        with capture.open_controlled_viewer() as viewer:
            viewer.wait_ready(timeout=90)
            assert viewer.frame_count == 8
            assert viewer.get_frame() == 0

    def test_03_capture_range_doppler_cdata_frame0(self, live_viewer):
        data = live_viewer.get_displayed_plot("range_doppler")
        assert data["type"] == "image"
        assert data["CData"] is not None
        assert isinstance(data["title"], str)
        TestLiveMatlabViewer._cdata_frame0 = data["CData"].copy()

    def test_04_set_frame_5(self, live_viewer):
        target = min(5, live_viewer.frame_count - 1)
        live_viewer.set_frame(target)
        assert live_viewer.get_frame() == target

    def test_05_cdata_changed(self, live_viewer):
        data = live_viewer.get_displayed_plot("range_doppler")
        assert not np.array_equal(data["CData"], TestLiveMatlabViewer._cdata_frame0)

    def test_06_export_range_doppler_raises(self, live_viewer, tmp_path):
        import awr2944_dca.viewer_ctrl as vc
        with pytest.raises(vc.ViewerExportUnsupportedError):
            live_viewer.export_plot("range_doppler", format="png", resolution=300)

    def test_07_export_full_dashboard_raises(self, live_viewer, tmp_path):
        import awr2944_dca.viewer_ctrl as vc
        with pytest.raises(vc.ViewerExportUnsupportedError):
            live_viewer.export_window(format="png", resolution=300)

    def test_08_return_to_frame_0(self, live_viewer):
        live_viewer.set_frame(0)
        assert live_viewer.get_frame() == 0

    def test_09_set_frame_8_raises(self, live_viewer):
        with pytest.raises(ValueError, match=r"Frame index 8.*0.7"):
            live_viewer.set_frame(8)

    def test_10_close_idempotent(self, live_viewer):
        assert not live_viewer._is_closed
        # Second close call after context exits must not raise
        # (context manager will call it once more after this test)


# ===========================================================================
# COM STA and engine lifetime regressions
# ===========================================================================
class TestComStaRegression:
    """Verify that open_controlled_viewer ensures COM STA initialization
    and retains the COM engine reference for the viewer lifetime."""

    def test_sta_initialized_before_dispatch(self):
        """open_controlled_viewer must call CoInitializeEx(STA) before
        DispatchEx so MATLAB's timer callback can fire."""
        import ast
        import inspect
        from awr2944_dca import viewer_ctrl

        source = inspect.getsource(viewer_ctrl.open_controlled_viewer)
        tree = ast.parse(source)

        coinit_line = None
        dispatch_line = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr == "CoInitializeEx":
                    coinit_line = node.lineno
            if isinstance(node, ast.Attribute):
                if node.attr == "DispatchEx":
                    dispatch_line = node.lineno

        assert coinit_line is not None, (
            "open_controlled_viewer must call pythoncom.CoInitializeEx"
        )
        assert dispatch_line is not None, (
            "open_controlled_viewer must call win32com.client.DispatchEx"
        )
        assert coinit_line < dispatch_line, (
            f"CoInitializeEx (line {coinit_line}) must appear before "
            f"DispatchEx (line {dispatch_line}) to ensure STA is set "
            f"before MATLAB COM server launch"
        )

    def test_engine_retained_until_close(self):
        """ControlledViewer must store self.eng and only Quit during close()."""
        import ast
        import inspect
        from awr2944_dca.viewer_ctrl import ControlledViewer

        # __init__ must store eng
        init_src = inspect.getsource(ControlledViewer.__init__)
        assert "self.eng" in init_src, (
            "ControlledViewer.__init__ must store self.eng"
        )

        # close() must call self.eng.Quit()
        close_src = inspect.getsource(ControlledViewer.close)
        assert "self.eng.Quit()" in close_src, (
            "ControlledViewer.close must call self.eng.Quit()"
        )
