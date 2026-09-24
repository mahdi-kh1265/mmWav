# mmWav

[![CI](https://github.com/mahdi-kh1265/awr2944-fmcw-radar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mahdi-kh1265/awr2944-fmcw-radar/actions/workflows/ci.yml) [![PyPI version](https://img.shields.io/pypi/v/awr2944-dca-lab.svg)](https://pypi.org/project/awr2944-dca-lab/) [![Python versions](https://img.shields.io/pypi/pyversions/awr2944-dca-lab.svg)](https://pypi.org/project/awr2944-dca-lab/) [![License](https://img.shields.io/github/license/mahdi-kh1265/awr2944-fmcw-radar.svg)](LICENSE)

A Python research toolkit for TI AWR2944EVM + DCA1000EVM raw ADC radar captures.

This package provides a robust, native direct-capture pipeline that is free from mmWave Studio GUI automation and Lua scripts.

## Installation

Install the base package via pip:

```bash
pip install awr2944-dca-lab
```

To include the optional MATLAB viewer bridge dependencies (Windows only), install with the `viewer` extra:

```bash
pip install "awr2944-dca-lab[viewer]"
```

## Prerequisites

This Python package controls and orchestrates external hardware and software. The following are external prerequisites and must be installed separately:
- **MATLAB** (Required for the `viewer` component)
- **TI mmWave SDK tools**
- **DCA1000 CLI software**

## Architecture

The production capture chain uses:
1. SDK Demo UART CLI for radar configuration
2. TI DCA1000 CLI utilities (external dependency) for FPGA initialization
3. Native direct UDP capture with zero-copy stream processing and metadata logging
4. Sequence/counter validation and DCA depadding
5. Canonical ADC cube extraction
6. Python DSP and standalone MATLAB viewer `buildMmwsCompatibleShell.m`

Historical mmWave Studio GUI automation is available as an optional `legacy-mmws` dependency extra for compatibility and debugging.

## Reference documents

TI PDFs in `reference_docs/`:
- AWR2944EVM user guide (SPRUJ22C)
- DCA1000 + mmWave Studio raw capture training
- SWRA581B ADC raw data capture app report
- mmwaveSensing FMCW offline viewing deck (radar formulas)
