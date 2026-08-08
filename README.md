# awr2944-fmcw-radar

[![CI](https://github.com/mahdi-kh1265/awr2944-fmcw-radar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mahdi-kh1265/awr2944-fmcw-radar/actions/workflows/ci.yml) [![PyPI version](https://img.shields.io/pypi/v/awr2944-dca-lab.svg)](https://pypi.org/project/awr2944-dca-lab/) [![Python versions](https://img.shields.io/pypi/pyversions/awr2944-dca-lab.svg)](https://pypi.org/project/awr2944-dca-lab/) [![License](https://img.shields.io/github/license/mahdi-kh1265/awr2944-fmcw-radar.svg)](LICENSE)

A Python research toolkit for TI AWR2944EVM + DCA1000EVM raw ADC radar captures.

This package provides a robust, native direct-capture pipeline that is free from mmWave Studio GUI automation and Lua scripts.

## Installation

```bash
pip install awr2944-dca-lab
```

To include the optional MATLAB viewer bridge dependencies (Windows only):

```bash
pip install "awr2944-dca-lab[viewer]"
```

## Prerequisites

- **TI mmWave SDK tools** and **DCA1000 CLI software** (external, installed separately)
- **MATLAB** (required only for the `viewer` component)
- A **dedicated wired Ethernet adapter** (or USB-Ethernet adapter) for the DCA1000 connection

---

## One-Time Machine Setup

> **This section describes the one-time per-machine setup.**
> Most steps only need to be repeated if you change hardware or reinstall Windows.

### Step 1 — Install the package

```bash
pip install awr2944-dca-lab
```

### Step 2 — Initialize a project

```python
from awr2944_dca import RadarProject

p = RadarProject.init_here()   # creates project scaffold in current directory
# or: p = RadarProject.open("path/to/existing/project")
```

### Step 3 — Detect AWR2944 serial interfaces

```python
p.hardware.autodetect_serial(save=True)
```

Discovers the XDS110 COM ports used by the AWR2944EVM and saves them to
`.awr2944/local.toml`. Run with the AWR2944EVM powered and connected via USB.

### Step 4 — Detect TI DCA1000 toolchain

```python
p.hardware.autodetect_toolchain(save=True)
```

Scans `C:\ti\mmwave_studio_*` for a complete DCA1000 toolchain (CLI executables,
RF API DLL, `cf.json`) and saves the paths to `.awr2944/local.toml`.

### Step 5 — Inspect Ethernet adapters

```python
p.hardware.discover("network")   # shows all adapters and their metadata
p.eth.status()                   # shows DCA readiness vs. project config
p.eth.instructions()             # shows exactly how to configure a dedicated NIC
```

> These calls are **entirely read-only** — they never change any network settings.

### Step 6 — Manually configure the dedicated DCA NIC in Windows

> ### ⚠ Important — Read Before Configuring
>
> - Use a **dedicated Ethernet port or USB-Ethernet adapter** for the DCA1000.
>   Do not use your main Internet/campus Ethernet port.
>
> - **DO NOT** assign `192.168.33.30` to the NIC that carries your normal
>   Internet connection or default gateway.  Doing so will break Internet access.
>
> - The DCA1000 may **not respond to ICMP ping** — this is normal and expected.
>   Use `p.doctor()` or `p.dca.verify()` for aliveness checks, not ping.
>
> - Static NIC configuration is normally a **one-time per-machine** step.
>
> - **Package Ethernet helpers are read-only in the normal workflow.**

Open **Windows Settings → Network → Change adapter options**, right-click the
dedicated adapter → **Properties → IPv4 → Use the following IP address**:

| Setting     | Value             |
|-------------|-------------------|
| IP address  | `192.168.33.30`   |
| Subnet mask | `255.255.255.0`   |
| Gateway     | *(leave blank)*   |
| DNS         | *(leave blank)*   |

The DCA1000 board default address is `192.168.33.180`.

Run `p.eth.instructions().print()` to see the exact values for your project.

### Step 7 — Verify readiness

```python
p.doctor().print()
```

A fully-ready system shows all required checks as **PASS**.
Follow any FAIL guidance to fix configuration issues.

### Step 8 — Plan a capture (dry-run, no hardware needed)

```python
plan = p.capture.plan(
    profile="smoke_v1",
    frames=8,
    guard_frames=1,
)
plan          # Jupyter display
plan.print()  # terminal display
```

### Step 9 — Capture

```python
result = p.capture.run_smoke(
    name="demo",
    frames=8,
    guard_frames=1,
)
```

---

## Architecture

The production capture chain uses:
1. SDK Demo UART CLI for radar configuration
2. TI DCA1000 CLI utilities (external) for FPGA initialization
3. Native direct UDP capture with zero-copy stream processing and metadata logging
4. Sequence/counter validation and DCA depadding
5. Canonical ADC cube extraction
6. Python DSP and standalone MATLAB viewer `buildMmwsCompatibleShell.m`

Historical mmWave Studio GUI automation is available as an optional `legacy-mmws`
dependency extra for compatibility and debugging.

---

## Ethernet Safety Notes

- The normal user-facing Ethernet API (`p.eth.status()`, `p.eth.instructions()`,
  `p.doctor()`) is **read-only** — it never modifies Windows networking.
- Low-level helpers (`p.eth.configure()`, `p.eth.repair()`, `p.eth.pair()`) are
  preserved for advanced/development use.  They may mutate NIC settings.
  Their docstrings carry explicit **Advanced/unsafe** warnings.
- Never configure a default-gateway adapter for the DCA1000.
  The package will warn you if it detects this situation.
- The DCA1000 does not typically respond to ICMP ping; TCP/UDP connectivity
  via `query_sys_status` (`p.dca.verify()`) is the authoritative aliveness test.

---

## Reference Documents

TI PDFs in `reference_docs/`:
- AWR2944EVM user guide (SPRUJ22C)
- DCA1000 + mmWave Studio raw capture training
- SWRA581B ADC raw data capture app report
- mmwaveSensing FMCW offline viewing deck (radar formulas)
