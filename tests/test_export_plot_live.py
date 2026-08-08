import pytest
from pathlib import Path
from awr2944_dca.lab import RadarProject
from awr2944_dca.viewer_ctrl import ViewerExportUnsupportedError

_LIVE_PROJECT_ROOT = Path(r"C:\Users\khams008\Documents\awr2944-live-project")

@pytest.mark.matlab_live
@pytest.mark.skipif(
    not _LIVE_PROJECT_ROOT.exists(),
    reason="Live project root not present on this machine (C:\\Users\\khams008\\...)",
)
def test_export_plot_live():
    PROJECT_ROOT = _LIVE_PROJECT_ROOT

    # Using an arbitrary complete capture for testing
    project = RadarProject.open(PROJECT_ROOT)
    complete = [c for c in project.captures.list() if c.status().get('status') == 'complete']
    if not complete:
        pytest.skip("No complete captures found.")
    capture = complete[-1]
    
    with capture.open_controlled_viewer() as viewer:
        viewer.wait_ready(timeout=90)
        
        # Verify automated export raises the dedicated error
        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_plot("range_doppler")

        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_all()

        with pytest.raises(ViewerExportUnsupportedError):
            viewer.export_window()

        with pytest.raises(ViewerExportUnsupportedError):
            viewer.save_figure()
