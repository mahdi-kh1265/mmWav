MATLAB viewer
=============

An optional standalone MATLAB viewer is launched from a capture::

    cap = p.latest_capture()
    cap.open_viewer()
    # or, with explicit colour-scale control:
    cap.open_controlled_viewer(clim_mode="fixed_global", display_dynamic_range_db=40.0)

Requirements: Windows, MATLAB installed, and the ``viewer`` extra
(``python -m pip install -e ".[viewer]"``).  The viewer reads
``adc_data_canonical.bin`` and exports ``viewer_payload.mat``.

The viewer rebuilds a DSP profile from the capture manifest.  For multi-chirp / TDM
captures this is refused with
:class:`~awr2944_dca.capture_layout.ScheduleAwareDspRequiredError`, because
schedule-aware Doppler is not implemented (see :doc:`dsp`).  The raw data remain
fully usable through ``cap.raw.to_cube()``.

If the canonical file is missing or no profile can be reconstructed the call prints
a message and returns without launching MATLAB.  MATLAB is never needed for capture,
verification or ``to_cube()``.
