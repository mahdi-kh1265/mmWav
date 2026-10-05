Capture workflow
================

.. code-block:: python

    from pathlib import Path
    from awr2944_dca import RadarProject

    p = RadarProject.open_here()
    cfg = Path(r"C:\path\to\profile.cfg")

    p.doctor().print()                                   # 1. readiness
    plan = p.capture.plan(profile=cfg, frames=32, guard_frames=1)   # 2. offline plan
    plan.print()

    if plan.can_execute_live:                            # 3. capture
        result = p.capture.run(profile=cfg, frames=32,
                               name="run_001", guard_frames=1)

    cap = result.capture                                 # 4. verify and load
    cap.verify(strict=True)
    cube = cap.raw.to_cube()

Related calls
-------------

``p.capture.dry_run(...)``
    Dictionary form of the plan.  Touches no hardware.
``p.capture.run_smoke(name=...)``
    Convenience wrapper for the built-in ``smoke_v1`` regression profile.  Kept as a
    fixture and example; not the recommended workflow.
``p.latest_capture()``, ``p.get_capture(query)``, ``p.captures.list()``
    Reopen earlier captures.
``cap.add_note(text)``, ``cap.add_tags(*tags)``, ``cap.notes()``
    Annotate captures.

If ``plan.can_execute_live`` is ``False`` the reason is in ``plan.live_block_reason``
and ``p.capture.run`` refuses before touching hardware.

Frames and guard frames
-----------------------

``frames`` is the **canonical** frame count you will analyse.  ``guard_frames``
extra trailing frames are recorded so the stream has finished cleanly; they are kept
in the native file and dropped from the canonical file.  Native frames
= ``frames + guard_frames``; the resolved ``frameCfg`` carries the native count.

Concurrency
-----------

A project lock prevents two captures from using the same project's hardware at the
same time.  To reuse one hardware session explicitly, ``p.connect()`` returns a
``RadarSession`` whose ``capture.run`` accepts the same profile arguments.
