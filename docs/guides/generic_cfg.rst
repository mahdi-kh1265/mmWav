Generic ``.cfg`` capture
========================

A TI AWR2944 SDK-demo ``.cfg`` file is currently the most general user-facing
configuration route.  The package parses the file, resolves it into a physical chirp
schedule and an exact byte plan, and refuses anything it cannot prove.

.. code-block:: python

    from pathlib import Path

    cfg = Path(r"C:\path\to\profile.cfg")          # must be a Path, not a str
    plan = p.capture.plan(profile=cfg, frames=8, guard_frames=1)
    plan.print()

Resolution is **offline**.  Any problem is raised before the serial port, the DCA
command channel or the UDP socket is opened.

What the resolver does
----------------------

* Parses the commands, strips comments, and drops ``sensorStop`` / ``sensorStart``
  (the capture controller issues them itself).
* Resolves **all** ``chirpCfg`` definitions (single indices, ranges, any order) and
  the ``frameCfg`` chirp window into the physical chirp schedule.
* Takes samples/chirp and sample rate from the single ``profileCfg``; RX count and
  mask from ``channelCfg``.
* Computes the exact DCA byte target and the canonical cube shape.
* Rewrites **only** the ``numFrames`` field of ``frameCfg`` to ``frames + guard_frames``.
* Writes ``source_config.cfg`` (your file, byte-for-byte) and ``resolved_config.cfg``
  (exactly the commands transmitted) with SHA-256 hashes (see :doc:`artifacts`).

Frame count rules
-----------------

* ``frameCfg`` takes **exactly 8 arguments** on AWR294x:
  ``chirpStart chirpEnd numLoops numFrames numAdcSamples periodMs trigger delay``.
  The older 7-argument form is rejected, as the firmware rejects it.
* ``numLoops`` must be in ``1..255``.
* ``numFrames = 0`` means **infinite** on TI firmware.  The capture API needs a
  finite byte target, so it raises ``ValueError`` unless you pass ``frames=N``
  (a warning records that the infinite setting was overridden).
* ``frames=N`` always overrides the cfg's own frame count.

Raw-stream requirements
-----------------------

For live capture the cfg must produce the byte layout the parser expects.  The
following are enforced (live capture is blocked, with a reason in
``plan.live_block_reason``, if they are not met):

* exactly one ``lvdsStreamCfg -1 0 1 0`` (no HSI header, raw ADC data, no SW session);
* ``adcbufCfg`` with non-interleaved channel order and ``chirpThreshold = 1``;
* ``dfeDataOutputMode 1`` (legacy frame mode);
* real int16 ADC samples; an even ``numAdcSamples``;
* software frame trigger (``triggerSelect = 1``);
* an RX mask of 4 RX, or a supported 2-RX mask (see below).

Using a standard TI sample cfg
------------------------------

Ordinary TI demo files often carry processing/monitoring commands.  Known demo
commands (for example ``guiMonitor``, ``cfarCfg``) are passed through unchanged;
they do not change the raw layout.  **Unknown** commands are rejected, because they
could change RF or data semantics.  If a TI cfg is rejected, reduce it to the basic
radar-configuration commands (``channelCfg``, ``adcCfg``, ``adcbufCfg``,
``profileCfg``, ``chirpCfg``, ``frameCfg``, ``lvdsStreamCfg``).

In the TI SDK's ``tdm_awr2944`` profiles, ``profile_LVDS.cfg`` resolves and is
live-capable unchanged; the others lack ``lvdsStreamCfg`` (plan only) or contain
unsupported commands.  See :doc:`../reference/configuration_support`.
