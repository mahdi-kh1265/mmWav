Installation
============

Requirements
------------

* Windows (the capture chain controls Windows-hosted TI tools and a Windows NIC).
* **Python 3.12** (``requires-python >= 3.12``; CI runs 3.12).
* External, separately installed TI software: **mmWave Studio / DCA1000 CLI tools**
  (the package uses the DCA1000 command-line utilities, ``cf.json`` and the RF API
  DLL; it does *not* drive the mmWave Studio GUI).
* MATLAB only for the optional standalone viewer.

Install from the Git checkout
-----------------------------

For current research use install the repository, **not** the PyPI ``0.1.0``
package (which predates the generic ``.cfg`` capture work)::

    git clone https://github.com/mahdi-kh1265/mmWav.git
    cd mmWav

    py -3.12 -m venv .venv
    .venv\Scripts\activate
    python -m pip install --upgrade pip
    python -m pip install -e .

Optional extras (see ``pyproject.toml``): ``viewer`` (MATLAB bridge, Windows only),
``dev`` (pytest, ruff, mypy, build), ``storage``, ``dashboard``, ``tables``.
For example ``python -m pip install -e ".[dev,viewer]"``.

Check the install
-----------------

::

    python -c "from awr2944_dca import RadarProject; print(RadarProject)"

The offline test suite (no hardware needed)::

    python -m pip install -e ".[dev]"
    python -m pytest -m "not hardware_live and not matlab_live"

Building these docs locally
---------------------------

::

    python -m pip install -r docs/requirements.txt
    python -m pip install -e .
    sphinx-build -W -b html docs docs/_build/html
