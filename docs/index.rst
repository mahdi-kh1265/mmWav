awr2944-dca-lab
===============

Research toolkit for **TI AWR2944EVM + DCA1000EVM** raw ADC capture:
SDK-demo UART radar configuration, DCA1000 FPGA control, native UDP acquisition
with packet-integrity checking, canonical ADC cubes, Python range/Doppler DSP and
a standalone MATLAB viewer.

.. note::

   The **generic** ``.cfg`` workflow is the primary way to use this package: give it
   an AWR2944 SDK-demo *basic-frame* configuration file, review the offline plan,
   and capture.  Install from the Git checkout (not PyPI) for current research use.

.. warning::

   **Raw-capture support is not DSP support.**  Multi-chirp / TDM configurations are
   captured correctly as a raw ``[frame, physical_chirp, rx, sample]`` cube, but
   schedule-aware Doppler and angle-of-arrival processing are **not implemented**
   yet.  See :doc:`guides/dsp`.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   getting_started/index

.. toctree::
   :maxdepth: 2
   :caption: Guides

   guides/generic_cfg
   guides/capture_workflow
   guides/artifacts
   guides/verification
   guides/dsp
   guides/matlab_viewer
   guides/troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   reference/configuration_support
   reference/canonical_cube
   reference/api
