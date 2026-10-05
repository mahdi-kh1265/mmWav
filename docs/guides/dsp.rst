Signal processing (DSP)
=======================

.. important::

   **Raw capture support is not DSP support.**

   The package can acquire any basic-frame configuration listed in
   :doc:`../reference/configuration_support`.  Its range/Doppler pipeline, however,
   assumes a **uniform slow-time axis**: one chirp per slow-time sample, one TX, equal
   chirp spacing.  That is true for single-chirp configs and false for TDM.

Support matrix
--------------

.. list-table::
   :header-rows: 1

   * - Capture type
     - Raw capture, ``to_cube()``
     - Range/Doppler DSP and MATLAB viewer
   * - Single chirp per frame (conventional FMCW)
     - Yes
     - Yes (``requires_schedule_aware_dsp`` is ``False``)
   * - Multi-chirp / TDM / per-chirp RF variation
     - **Yes**
     - **No - schedule-aware Doppler and AoA are not implemented**

For multi-chirp schedules the package raises
:class:`~awr2944_dca.capture_layout.ScheduleAwareDspRequiredError` (a
``ValueError`` subclass) when DSP/viewer code tries to rebuild a legacy DSP profile
from the capture, instead of silently producing misleading Doppler results.
Treating alternating-TX chirps as consecutive slow-time samples would put TX
switching into the Doppler spectrum.

What you can still do with a TDM capture
----------------------------------------

The cube is stored in physical acquisition order (see :doc:`../reference/canonical_cube`),
so you can do your own processing.  For example, demultiplex by TX yourself using
``config_summary.json -> schedule -> tx_masks_per_cycle``:

.. code-block:: python

    import json
    cube = cap.raw.to_cube()                    # (frames, chirps, rx, samples)
    sched = json.loads((cap.path / "config_summary.json").read_text())["schedule"]
    masks = sched["tx_masks_per_cycle"]         # e.g. [1, 2]
    n_cycle = len(masks)
    tx0 = cube[:, 0::n_cycle]                   # chirps of the first TX in the cycle

This is an illustration of the data layout, not a validated virtual-array or Doppler
implementation.

Public DSP functions
--------------------

The :mod:`awr2944_dca.dsp` namespace exports ``RadarProfile`` (DSP profile),
``PipelineConfig``, ``run_pipeline``, ``compute_range_fft``, ``compute_doppler_fft``,
``remove_dc``, ``range_axis``, ``velocity_axis``, ``cfar_1d``, ``cfar_2d``,
``load_canonical_cube``, ``export_to_mat`` and more (see :doc:`../reference/api`).
For a single-chirp capture:

.. code-block:: python

    from awr2944_dca.dsp import PipelineConfig, RadarProfile, run_pipeline

    prof = RadarProfile(
        start_frequency_hz=77e9, slope_hz_per_s=29.982e12,
        adc_sample_rate_hz=10e6, adc_samples=256,
        idle_time_s=100e-6, ramp_end_time_s=60e-6,
        chirps_per_frame=128, frame_count=8, frame_period_s=40e-3,
        rx_count=4, tx_mask=1,
        sample_format="real_int16", cube_layout="frame_chirp_rx_sample",
    )   # fill in the values from YOUR resolved cfg
    result = run_pipeline(cap.raw.to_cube(), PipelineConfig(profile=prof))

Do **not** rely on the default ``PipelineConfig()`` profile for generic captures; it
is a fixed validation profile.  AoA is not implemented.
