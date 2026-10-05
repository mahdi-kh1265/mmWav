One-time machine setup
======================

Most of this is a one-time, per-machine step.

Project, serial ports and toolchain
-----------------------------------

::

    from awr2944_dca import RadarProject

    p = RadarProject.init_here()      # scaffold in the current directory
    # or: p = RadarProject.open(r"path\to\existing\project")

    p.hardware.autodetect_serial(save=True)      # XDS110 COM ports of the AWR2944EVM
    p.hardware.autodetect_toolchain(save=True)   # TI DCA1000 CLI tools, RF API DLL, cf.json

Run the serial step with the AWR2944EVM powered and connected over USB.  Results
are saved to ``.awr2944/local.toml``.  The toolchain step scans
``C:\ti\mmwave_studio_*``.

Dedicated DCA1000 network adapter
---------------------------------

The DCA1000 needs its **own** wired adapter (a dedicated Ethernet port or a
USB-Ethernet dongle).

.. list-table::
   :header-rows: 1

   * - Setting
     - Value
   * - Host IPv4 address
     - ``192.168.33.30``
   * - Subnet mask
     - ``255.255.255.0`` (/24)
   * - DCA1000 address
     - ``192.168.33.180``
   * - Default gateway
     - *(blank)*
   * - DNS
     - *(blank)*

In Windows: *Settings -> Network -> Change adapter options -> (dedicated adapter)
-> Properties -> Internet Protocol Version 4 -> Use the following IP address*.

.. warning::

   Do **not** put ``192.168.33.30`` on the adapter that carries your normal
   Internet / campus connection or default gateway.  Do not reconfigure your normal
   Internet adapter for this.

Read-only helpers::

    p.hardware.discover("network")      # list adapters (read-only)
    p.eth.status()                      # DCA readiness vs. project config
    p.eth.instructions().print()        # exact values for your project

The normal Ethernet helpers (``p.eth.status()``, ``p.eth.instructions()``,
``p.doctor()``) are **read-only**.  ``p.eth.configure()``, ``p.eth.repair()`` and
``p.eth.pair()`` are low-level advanced helpers that can change NIC settings; do
not use them in the normal workflow.

.. note::

   The DCA1000 frequently does **not answer ICMP ping**.  That is expected.
   ``p.doctor()`` and ``p.dca.verify()`` (a TCP/UDP ``query_sys_status``) are the
   authoritative aliveness checks.

Verify readiness
----------------

::

    p.doctor().print()

All required checks should be **PASS** before the first capture.
