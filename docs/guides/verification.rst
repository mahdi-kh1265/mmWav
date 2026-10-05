Verification
============

``RadarCapture.verify()`` is entirely offline.

.. code-block:: python

    report = cap.verify()          # CaptureVerificationReport
    print(report.summary())
    report = cap.verify(strict=True)   # raises CaptureVerificationError on any FAIL

Each :class:`~awr2944_dca.api._verification.VerificationCheck` has a ``name``, a
``status`` (``PASS``, ``FAIL``, ``WARN`` or ``SKIP``) and a ``detail``.

Checks performed for a production capture
-----------------------------------------

* ``config_summary_parse`` and, for schema v4+, ``config_summary_fields``,
  ``resolved_config_hash``, ``radar_config_hash_match``,
  ``manifest_summary_hash_match``.
* ``native_file_exists`` and ``canonical_file_exists``.
* ``manifest_consistency`` (``success`` and ``status == complete``).
* ``native_byte_count`` and ``canonical_byte_count`` - the file sizes must equal the
  planned byte targets.
* ``native_sha256`` and ``canonical_sha256`` - recomputed from disk.
* ``packet_continuity`` - zero sequence gaps **and** zero byte-counter
  discontinuities.
* ``payload_integrity`` - zero missing and zero overlapping payload bytes.
* ``cube_shape`` - the canonical file parses to ``logical_cube_shape``.

A capture is accepted only if there are no ``FAIL`` checks.  Older captures without a
production manifest fall back to a legacy project-level verification.

Acceptance checklist
--------------------

#. ``result.success`` is ``True`` and ``cap.verify(strict=True)`` does not raise.
#. ``cap.raw.native_bytes`` equals ``plan.byte_plan.native_dca_bytes``.
#. ``cap.raw.to_cube().shape`` equals the plan's cube shape.
#. ``rx_mask`` / ``active_rx_channels`` in ``config_summary.json`` match the
   channels you intended to use.
