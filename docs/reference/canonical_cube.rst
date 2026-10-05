Canonical cube
==============

Raw captures are exposed as a 4-D ``int16`` array::

    cube[frame, physical_chirp, rx, sample]

``cap.raw.to_cube()`` returns this array for the canonical file; the plan reports the
same shape before capture (``plan.print()``, ``plan.to_dict()``).

Axes
----

``frame``
    Canonical frames (guard frames removed).
``physical_chirp``
    Chirps in **acquisition order** within a frame: the chirp cycle (the chirps from
    ``frameCfg`` ``chirpStartIdx`` to ``chirpEndIdx``) repeated ``numLoops`` times.
    The size is ``chirps_per_cycle x loops_per_frame``.
``rx``
    Active receive channels in ascending physical order (see below).
``sample``
    ADC samples within a chirp.

Multi-chirp / TDM example
-------------------------

With ``tx_masks_per_cycle = [1, 2]`` the physical chirps are::

    0 -> TX0
    1 -> TX1
    2 -> TX0
    3 -> TX1
    ...

``physical chirp i`` uses ``tx_masks_per_cycle[i % chirps_per_cycle]``.  The cube is
**not** rearranged into virtual antennas and no TX demultiplexing is applied; that is
a later DSP transformation (not yet implemented, see :doc:`../guides/dsp`).

RX axis metadata
----------------

``config_summary.json`` records, under ``capture_layout``:

``rx_mask``
    The ``channelCfg`` ``rxChannelEn`` bit mask.
``active_rx_channels``
    The physical RX IDs, ascending.  For ``rx_mask = 0b0101`` this is ``[0, 2]``.

Canonical RX index ``k`` is physical RX ``active_rx_channels[k]`` - the k-th lowest
set bit.  ``0b0011`` and ``0b1100`` both give two RX axis entries, but different
physical channels; use the recorded IDs, not the count.  The TI firmware streams the
active RX channels one after another in ascending order, which is why this mapping
holds.  Only 4-RX has been hardware-validated.

Data types and layout
---------------------

* Real ADC samples, ``int16`` (little-endian on disk).
* ``to_cube(kind="native")`` parses the native file (all frames including guard);
  ``memmap()`` returns the flat int16 memory map.
* The canonical file is the first ``frames x chirps x rx x samples`` of the data
  after DCA depadding.
