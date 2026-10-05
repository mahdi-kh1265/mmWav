Troubleshooting
===============

Keep the proven capture stack conservative: diagnose with the read-only tools
(``p.doctor()``, ``p.eth.status()``, ``p.dca.verify()``, ``plan.print()``) before
changing anything, and do not casually power-cycle hardware or change firewall or
network settings.

No COM ports found
    Power the AWR2944EVM and connect its USB cable, then re-run
    ``p.hardware.autodetect_serial(save=True)``.  Check the XDS110 ports in Windows
    Device Manager.  Close other programs that hold the ports (mmWave Studio, serial
    terminals).

TI toolchain not found
    ``p.hardware.autodetect_toolchain(save=True)`` scans ``C:\ti\mmwave_studio_*`` for
    the DCA1000 CLI executables, RF API DLL and ``cf.json``.  Install the TI tools or
    pass ``root=r"C:\path\to\mmwave_studio_xx"``.

Wrong NIC
    ``p.eth.status()`` and ``p.eth.instructions().print()`` show which adapter is
    expected.  Use a *dedicated* adapter with ``192.168.33.30/24``, no gateway, no
    DNS.  Never assign that address to the adapter carrying your Internet connection.

DCA verification fails
    ``p.dca.verify()`` / ``p.doctor()`` are authoritative (the DCA1000 often does not
    answer ping).  Check cabling, DCA power, the adapter address and that no other
    program (mmWave Studio) is using the DCA.

Capture quiet timeout
    No UDP data arrived.  Verify the NIC/IP, that the radar configuration was sent,
    and ``plan.print()`` shows the LVDS stream command.  A cfg without
    ``lvdsStreamCfg`` cannot stream and is blocked up front.

Packet sequence gaps / byte-counter discontinuities
    UDP packets were lost.  ``cap.verify()`` reports ``packet_continuity`` FAIL; such a
    capture must not be used for analysis.  Re-capture; reduce frame rate / data rate
    in the cfg, close network-heavy programs, use a direct cable to a dedicated
    adapter.

Actual byte size does not equal planned size
    ``native_byte_count`` FAIL.  The capture is incomplete or the cfg and the plan
    disagree.  Re-capture and compare ``plan.byte_plan`` with ``config_summary.json``.

Unsupported configuration (``ValueError`` from ``plan``)
    The message names the problem: unknown or advanced commands (``advFrameCfg``,
    ``subFrameCfg``, ``advChirpCfg``, ``LUTDataCfg``), multiple ``profileCfg``,
    ambiguous or undefined chirps, complex ADC, a 7-argument ``frameCfg``, or
    ``numFrames=0`` without ``frames=``.  See
    :doc:`../reference/configuration_support`.

``Invalid profile name`` when passing a cfg path
    Pass ``Path(r"...")``, not a plain string.

Plan resolves but ``can_execute_live`` is False
    ``plan.live_block_reason`` explains it (for example missing
    ``lvdsStreamCfg``, 1-RX/3-RX mask, interleaved ADC buffer, odd sample count,
    hardware trigger).  Edit the cfg; ``p.capture.run`` will refuse before touching
    hardware.

``ScheduleAwareDspRequiredError``
    The capture is a valid raw capture from a multi-chirp/TDM schedule; the legacy
    DSP/viewer cannot process it.  Use ``cap.raw.to_cube()`` and your own processing
    (see :doc:`dsp`).

Viewer unavailable / MATLAB not installed
    The viewer is optional and Windows+MATLAB only.  Install MATLAB and the ``viewer``
    extra, or ignore it: capture, ``verify()`` and ``to_cube()`` do not need MATLAB.
