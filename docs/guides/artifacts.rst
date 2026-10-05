Capture artifacts
=================

Each capture lives in ``<project>/captures/<capture_id>/``.  The names below are the
files the current code writes.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - File
     - Purpose
   * - ``adc_data.bin``
     - **Native** DCA1000 payload for all frames (canonical + guard), exactly the
       planned number of bytes.  Little-endian int16 in the DCA word layout.
   * - ``adc_data_canonical.bin``
     - **Canonical** data: the first ``frames`` frames of the native file.  This is
       what :meth:`CaptureRawData.to_cube` parses.
   * - ``manifest.json``
     - Production manifest written by the capture session: success/status, planned
       and captured byte counts, SHA-256 hashes, packet-integrity counters,
       ``logical_cube_shape``, guard/canonical frame counts.
   * - ``capture_manifest.json``
     - Project-level record: connection settings, links to the config files and
       reproducibility metadata.
   * - ``config_summary.json``
     - Provenance: source/resolved config kind and SHA-256, byte plan, the resolved
       chirp **schedule**, the **capture layout** (cube axes, ``rx_mask``,
       ``active_rx_channels``) and the capability flags.
   * - ``source_config.cfg``
     - Your original ``.cfg`` file, byte-for-byte (for cfg input).
   * - ``resolved_config.cfg``
     - Exactly the AWR commands transmitted (``numFrames`` rewritten to the native
       frame count).  Its SHA-256 is recorded and re-checked by verification.
   * - ``source_profile.toml`` / ``resolved_profile.toml``
     - Equivalents when the input was a TOML/structured profile instead of a cfg.
   * - ``metadata/packet_metadata.jsonl``
     - One JSON record per received UDP packet (sequence number, byte counter,
       sizes), when packet metadata was preserved (the default).
   * - ``viewer_payload.mat``
     - Created only when the MATLAB viewer payload is exported.

Provenance fields
-----------------

``config_summary.json`` contains, among others:

``source_config_sha256`` / ``resolved_config_sha256``
    Hashes of the supplied and transmitted configuration.
``native_dca_bytes`` / ``canonical_dca_bytes``
    The planned (expected) byte counts.
``schedule``
    ``chirp_start_index``, ``chirp_end_index``, ``chirps_per_cycle``,
    ``loops_per_frame``, ``physical_chirps_per_frame``, ``tx_masks_per_cycle``,
    per-chirp definitions and a ``physical_order`` description.
``capture_layout``
    ``canonical_cube_shape``, ``cube_axes``, ``rx_mask``, ``active_rx_channels``,
    ``requires_schedule_aware_dsp``.
``capabilities``
    Whether the config could be executed live, run legacy DSP, etc.

Access from Python
------------------

.. code-block:: python

    cap = p.latest_capture()
    cap.path                 # capture directory
    cap.manifest             # production manifest dict
    cap.raw.native_path, cap.raw.canonical_path
    cap.raw.native_sha256, cap.raw.canonical_sha256   # from the manifest
    cap.raw.compute_sha256("canonical")               # recompute from disk
    cap.raw.packet_metadata()                         # list of dicts, or None
