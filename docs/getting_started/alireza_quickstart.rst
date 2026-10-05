Quick Start: AWR2944 + DCA1000
==============================

This page takes a Windows machine with an **AWR2944EVM** and a **DCA1000EVM** from a
fresh Git checkout to a verified raw capture using an ordinary AWR2944 SDK-demo
``.cfg`` file.  Nothing here changes radar functionality; every call below exists in
the current ``main``.

.. contents::
   :local:
   :depth: 1

1. Install from the Git checkout
--------------------------------

Use the checkout, **not** the stale PyPI ``0.1.0`` package::

    git clone https://github.com/mahdi-kh1265/mmWav.git
    cd mmWav

    py -3.12 -m venv .venv
    .venv\Scripts\activate
    python -m pip install --upgrade pip
    python -m pip install -e .

2. One-time machine setup
-------------------------

.. code-block:: python

    from awr2944_dca import RadarProject

    p = RadarProject.init_here()

    p.hardware.autodetect_serial(save=True)
    p.hardware.autodetect_toolchain(save=True)

    p.eth.status()
    p.eth.instructions().print()

    p.doctor().print()

Configure the **dedicated** DCA1000 adapter as ``192.168.33.30/24`` with no gateway
and no DNS (the DCA1000 is ``192.168.33.180``).  Never touch your normal Internet
adapter.  See :doc:`machine_setup`.  In a later session use
``p = RadarProject.open_here()`` (or ``RadarProject.open(path)``).

3. Plan the capture (offline, touches no hardware)
--------------------------------------------------

Pass the configuration as a :class:`pathlib.Path`.

.. warning::

   A plain ``str`` is interpreted as a **project profile name**, not a file path, and
   raises ``ValueError: Invalid profile name``.  Always wrap a ``.cfg`` path in
   ``Path(...)``.

.. code-block:: python

    from pathlib import Path

    cfg = Path(r"C:\path\to\profile.cfg")

    plan = p.capture.plan(
        profile=cfg,
        frames=8,            # required if the cfg has frameCfg numFrames = 0 (infinite)
        guard_frames=1,
    )
    plan.print()

``plan.print()`` shows the physical chirp schedule, the exact bytes the DCA1000 must
deliver and the AWR commands that will be sent.  Example (TI ``profile_LVDS.cfg``)::

    CapturePlan - 'profile_LVDS.cfg'
      Frames : 8 canonical + 1 guard = 9 total
      Cube   : [8, 48, 4, 560]  [frames, chirps, rx, samples]
      Bytes  : native=3,870,720  canonical=3,440,640
      Source : cfg_file
      Chirps : 3/cycle x 16 loops = 48 physical/frame
      RX=4  samples/chirp=560  TX masks/cycle=[1, 4, 2]
      Live execution: supported

Programmatic access: ``plan.byte_plan``, ``plan.schedule``, ``plan.rx_channels``,
``plan.samples_per_chirp``, ``plan.physical_chirps_per_frame``,
``plan.can_execute_live``, ``plan.live_block_reason``, ``plan.warnings``,
``plan.to_dict()``.

4. Capture
----------

.. code-block:: python

    result = p.capture.run(
        profile=cfg,
        frames=8,
        name="experiment_001",
        guard_frames=1,
    )

``p.capture.run`` returns a :class:`~awr2944_dca.api._capture_run.CaptureRunResult`
with ``result.success`` (bool), ``result.capture`` (a
:class:`~awr2944_dca.lab.RadarCapture`), ``result.session_result``,
``result.effective_profile`` and ``result.capture_plan``.

``guard_frames=1`` records one extra **trailing** frame; the canonical file keeps only
the first ``frames`` frames (the native file keeps all of them).  The plan's
``canonical`` frame count is what you analyse.

5. Verify and load the cube
---------------------------

.. code-block:: python

    cap = result.capture                 # or: cap = p.latest_capture()
    print(result.success)

    report = cap.verify(strict=True)     # raises CaptureVerificationError on failure
    print(report.summary())

    cube = cap.raw.to_cube()             # canonical int16 cube
    print(cube.shape)                    # (frames, physical_chirps, rx, samples)

    m = cap.manifest                     # production manifest (dict)

``cap.raw`` is a :class:`~awr2944_dca.api._capture_raw.CaptureRawData`:
``native_path``, ``canonical_path``, ``native_bytes``, ``canonical_bytes``,
``to_cube(kind="canonical"|"native")``, ``memmap()``, ``packet_metadata()``.

Checklists
----------

Before capture
~~~~~~~~~~~~~~

* ``p.doctor().print()`` shows **PASS** for every required check.
* The dedicated DCA adapter is correctly configured (``p.eth.status()``).
* ``plan.print()`` looks right: ``Live execution: supported``.
* Expected **RX count, samples per chirp, physical chirps per frame** and frame
  count are what you intended for the experiment.

After capture
~~~~~~~~~~~~~

* ``result.success`` is ``True``.
* **Zero** sequence gaps and **zero** byte-counter discontinuities.
* **Zero** missing (and zero overlapping) payload bytes.
* Actual native bytes equal the planned native bytes.
* ``cube.shape`` equals the planned canonical shape.

``cap.verify()`` checks all of these (see :doc:`../guides/verification`).

.. note::

   Multi-chirp / TDM configs capture fine, but **no schedule-aware Doppler/AoA
   exists yet**.  Do not run the single-chirp DSP on a TDM cube.  See
   :doc:`../guides/dsp`.
