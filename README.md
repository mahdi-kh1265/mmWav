# awr2944-dca-lab

Python research toolkit for **TI AWR2944EVM + DCA1000EVM** raw ADC radar captures:
SDK-demo UART radar control, DCA1000 raw capture over a native UDP receiver, packet
integrity checking, canonical ADC cubes, Python range/Doppler DSP and a standalone
MATLAB viewer.  No mmWave Studio GUI automation or Lua scripts are involved.

> **Documentation:** <https://awr2944-dca-lab.readthedocs.io/> 

## Install (current research use)

Install the Git checkout. The PyPI `0.1.0` release is **stale** and predates the
generic `.cfg` capture work.

```bash
git clone https://github.com/mahdi-kh1265/mmWav.git
cd mmWav

py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Requires Windows, Python 3.12, and the separately installed TI mmWave Studio /
DCA1000 CLI tools. MATLAB is needed only for the optional viewer
(`pip install -e ".[viewer]"`).

## Quick start: generic `.cfg` capture

```python
from pathlib import Path
from awr2944_dca import RadarProject

p = RadarProject.init_here()                      # or RadarProject.open_here()
p.hardware.autodetect_serial(save=True)
p.hardware.autodetect_toolchain(save=True)
p.eth.instructions().print()                      # read-only
p.doctor().print()                                # all required checks PASS

cfg = Path(r"C:\path\to\profile.cfg")             # a Path, not a str

plan = p.capture.plan(profile=cfg, frames=8, guard_frames=1)   # offline, no hardware
plan.print()

result = p.capture.run(profile=cfg, frames=8, name="experiment_001", guard_frames=1)

cap = result.capture
cap.verify(strict=True)                           # packet gaps, byte counts, hashes, shape
cube = cap.raw.to_cube()                          # [frame, physical_chirp, rx, sample]
```

See the documentation for machine setup, the support matrix, capture artifacts and
troubleshooting.

## Supported scope

* **Live raw capture:** AWR2944 SDK-demo *basic-frame* configs, a single `profileCfg`,
  one or many `chirpCfg` (including multi-chirp / TDM), 4 RX (and supported 2-RX
  masks with a warning), real int16 ADC, finite frame counts.
* **Fail closed:** `advFrameCfg`, `subFrameCfg`, `advChirpCfg`, `LUTDataCfg`, multiple
  `profileCfg`, complex ADC, 1/3 RX, odd sample counts, hardware trigger, unknown
  commands, and infinite frame mode (`numFrames 0`) unless `frames=` is given.
* **Raw capture is not DSP support.** Range/Doppler DSP and the MATLAB viewer support
  single-chirp captures. For multi-chirp/TDM captures the raw cube is valid but
  schedule-aware Doppler and AoA are **not implemented**; the package raises
  `ScheduleAwareDspRequiredError` rather than produce misleading results.

## Architecture

1. SDK Demo UART CLI for radar configuration
2. TI DCA1000 CLI utilities (external) for FPGA initialization
3. Native direct UDP capture with zero-copy stream processing and metadata logging
4. Sequence/counter validation and DCA depadding
5. Canonical ADC cube extraction
6. Python DSP and standalone MATLAB viewer

Historical mmWave Studio GUI automation remains an optional `legacy-mmws` extra for
compatibility and debugging only.

## Ethernet safety notes

* Use a **dedicated** Ethernet adapter for the DCA1000 (host `192.168.33.30/24`,
  DCA `192.168.33.180`, **no gateway, no DNS**). Never assign that address to the
  adapter carrying your Internet connection.
* The DCA1000 may not answer ping. `p.doctor()` and `p.dca.verify()` are the
  authoritative checks.
* `p.eth.status()`, `p.eth.instructions()` and `p.doctor()` are **read-only**. The
  low-level `p.eth.configure()`, `p.eth.repair()` and `p.eth.pair()` can modify NIC
  settings and are for advanced/development use only.

## Reference documents

TI PDFs in `reference_docs/`: AWR2944EVM user guide (SPRUJ22C), DCA1000 + mmWave
Studio raw capture training, SWRA581B ADC raw data capture app report, and the
mmwaveSensing FMCW offline viewing deck. Older design notes are in `docs/*.md`.
