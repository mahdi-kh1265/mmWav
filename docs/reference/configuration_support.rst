Configuration support
=====================

Verified against the current source (``main``).  "Live" means
``plan.can_execute_live`` is ``True`` and ``p.capture.run`` will execute.

Supported (live)
----------------

* Basic ``frameCfg`` (8 arguments) with a single ``profileCfg``.
* One or several ``chirpCfg`` definitions (single indices, ranges, any order; the
  ``frameCfg`` chirp window may select a subset of the defined chirps).
* Arbitrary valid start frequency, slope, idle/ADC-start/ramp timing and ADC sample
  rate (taken from ``profileCfg``).
* Even ``numAdcSamples``; real int16 ADC samples.
* **4 RX** (``rxChannelEn = 0xF``).
* Supported **2-RX** masks ``0x3, 0x5, 0x6, 0x9, 0xA, 0xC`` - accepted with a
  "not hardware-verified" warning (only 4-RX captures have been validated on
  hardware).
* TX masks per chirp that are a subset of ``channelCfg`` ``txChannelEn``, including
  several simultaneous TX and masks that change every chirp.
* Multi-chirp / TDM **raw** capture and per-chirp RF variation (start-frequency,
  slope, idle, ADC-start variations) where the raw layout stays deterministic.
* Finite frame counts (``frames=N``, or a cfg ``numFrames >= 1``).

Fail closed / unsupported
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Feature
     - Behaviour
   * - ``advFrameCfg``, ``subFrameCfg``, ``advChirpCfg``, ``LUTDataCfg``
     - Rejected (``ValueError``).
   * - More than one ``profileCfg``
     - Rejected.
   * - Complex ADC (``adcCfg`` output format other than real)
     - Rejected.
   * - 1 RX, 3 RX
     - Plan resolves; live execution blocked.
   * - Odd ``numAdcSamples``
     - Live execution blocked.
   * - Hardware trigger (``frameCfg`` ``triggerSelect = 2``)
     - Live execution blocked.
   * - Non-interleaved ``adcbufCfg`` / ``chirpThreshold`` other than 1 not satisfied
     - Live execution blocked.
   * - Missing, duplicate or different ``lvdsStreamCfg``
     - Live execution blocked (only ``-1 0 1 0`` is accepted).
   * - ``dfeDataOutputMode`` not exactly once and equal to 1
     - Rejected.
   * - 7-argument ``frameCfg``; extra arguments on key commands
     - Rejected.
   * - ``numLoops`` outside ``1..255``
     - Rejected.
   * - ``numFrames = 0`` (infinite)
     - Rejected unless ``frames=N`` is supplied.
   * - Undefined or ambiguous (overlapping) chirp indices; gaps in the frame window
     - Rejected.
   * - Chirp referencing a different ``profileId``, or TX not enabled in ``channelCfg``
     - Live execution blocked.
   * - Unknown commands (e.g. ``spreadSpectrumConfig``, ``enetStreamCfg``,
       ``compressionCfg``, ``localMaxCfg``)
     - Rejected, because they might change RF or data semantics.

RX byte packing
---------------

The SDK demo fixes the LVDS lane configuration at two lanes, so the DCA expansion
factor is constant and native bytes scale linearly with active RX:

``native_bytes = native_frames x physical_chirps x active_rx x samples x 2 B x 2``

Active RX channels are streamed in ascending physical order.

DSP support
-----------

Orthogonal to the above; see :doc:`../guides/dsp`.  Raw acquisition is supported for
the table above.  Range/Doppler DSP and the MATLAB viewer support single-chirp
captures only.  TDM schedule-aware Doppler and AoA are **not implemented**.
