API reference
=============

Only public classes and functions are listed.  Objects are normally reached through
:class:`~awr2944_dca.lab.RadarProject` (``p.hardware``, ``p.capture``, ``p.dca``,
``p.eth``, ``p.captures``, ``p.profiles``) rather than constructed directly.

Project
-------

.. autoclass:: awr2944_dca.lab.RadarProject
   :members:
   :undoc-members:

Hardware and diagnostics (``p.hardware``, ``p.doctor()``)
---------------------------------------------------------

.. autoclass:: awr2944_dca._doctor.HardwareManager
   :members:

.. autoclass:: awr2944_dca._doctor.HardwareReport
   :members:

Capture (``p.capture``)
-----------------------

.. autoclass:: awr2944_dca.api._capture_run.FacadeCaptureApi
   :members:

.. autoclass:: awr2944_dca.api._capture_run.CaptureRunResult
   :members:

.. autoclass:: awr2944_dca.api._capture_plan.CapturePlan
   :members:

Captured data
-------------

.. autoclass:: awr2944_dca.lab.RadarCapture
   :members:

.. autoclass:: awr2944_dca.api._capture_raw.CaptureRawData
   :members:

.. autoclass:: awr2944_dca.api._collection.CaptureCollection
   :members:

Verification
------------

.. autoclass:: awr2944_dca.api._verification.CaptureVerificationReport
   :members:

.. autoclass:: awr2944_dca.api._verification.VerificationCheck

.. autoexception:: awr2944_dca.api._verification.CaptureVerificationError

Chirp schedule and capture layout
---------------------------------

.. automodule:: awr2944_dca.chirp_schedule
   :members: BasicFrameSchedule, ChirpCfgDefinition, ResolvedChirp, ScheduleBytePlan,
             ChirpScheduleError, parse_chirp_cfg, resolve_chirp_schedule,
             resolve_schedule_from_cli, schedule_byte_plan

.. autoclass:: awr2944_dca.capture_layout.CaptureDataLayout
   :members:

.. autoexception:: awr2944_dca.capture_layout.ScheduleAwareDspRequiredError

Profiles and configuration
--------------------------

.. autoclass:: awr2944_dca.api.profile.RadarProfile
   :members:

.. autoclass:: awr2944_dca.api.profile_collection.ProfileCollection
   :members:

.. autoclass:: awr2944_dca._config.ProjectConfig
   :members:

DCA1000 (``p.dca``)
-------------------

.. autoclass:: awr2944_dca.api._dca_facade.DcaFacade
   :members:

.. autoclass:: awr2944_dca.api._dca_facade.DcaVerifyResult
   :members:

.. autoclass:: awr2944_dca.api._dca_facade.DcaStatusResult
   :members:

Ethernet (``p.eth``)
--------------------

.. autoclass:: awr2944_dca.lab.EthernetManager
   :members: status, instructions, snapshot

.. autoclass:: awr2944_dca.api._eth_status.EthernetStatusResult
   :members:

.. autoclass:: awr2944_dca.api._eth_status.EthernetInstructionsResult
   :members:

.. note::

   ``EthernetManager.configure``, ``repair``, ``pair`` and related methods can change
   Windows network settings; they are advanced helpers and are intentionally not
   documented here.

Digital signal processing
-------------------------

.. note::

   The DSP pipeline assumes a uniform single-chirp slow-time axis.  See
   :doc:`../guides/dsp`.

.. automodule:: awr2944_dca.dsp
   :members: RadarProfile, PipelineConfig, PipelineResult, run_pipeline,
             load_canonical_cube, export_to_mat, compute_range_fft,
             compute_doppler_fft, remove_dc, range_axis, velocity_axis,
             range_bin_spacing_m, velocity_bin_spacing_mps, cfar_1d, cfar_2d,
             estimate_noise_floor, estimate_snr, get_window

Viewer
------

The MATLAB viewer is launched through :meth:`awr2944_dca.lab.RadarCapture.open_viewer`
and :meth:`awr2944_dca.lab.RadarCapture.open_controlled_viewer` (documented above).
See :doc:`../guides/matlab_viewer`.
