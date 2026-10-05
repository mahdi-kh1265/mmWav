Your first capture
==================

The shortest path is :doc:`alireza_quickstart`.  This page lists the decisions made
for you so you know what to expect.

#. **Resolve** the ``.cfg`` (parse commands, resolve the chirp schedule, derive RX /
   sample / frame dimensions and the exact DCA byte target).  Anything unsupported
   or ambiguous fails here, *before* any serial port, DCA command or UDP socket is
   opened.
#. **Send** the AWR commands over the SDK-demo UART (``sensorStop``/``sensorStart``
   are handled by the package, and are not duplicated from the cfg).
#. **Record** with the native UDP receiver until the planned number of bytes
   arrives, checking packet sequence numbers and byte counters.
#. **Write** the native file, the canonical file (guard frames removed), the
   manifests and the config provenance.
#. **Verify** offline with ``cap.verify()``.

Start with a small capture (for example ``frames=8, guard_frames=1``) and a
single-chirp cfg with 4 RX before attempting multi-chirp or 2-RX experiments.
2-RX layouts are accepted with a warning because only 4-RX captures have been
validated on hardware.
