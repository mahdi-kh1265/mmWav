"""Sphinx configuration for the awr2944-dca-lab documentation."""

from __future__ import annotations

project = "awr2944-dca-lab"
author = "Mahdi Khamseh"
copyright = "2026, Mahdi Khamseh"

try:
    from importlib.metadata import version as _pkg_version

    release = _pkg_version("awr2944-dca-lab")
except Exception:  # pragma: no cover - docs build without installed metadata
    release = "0.1.0"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

# The legacy design notes under docs/ are Markdown and are NOT part of the
# Sphinx site (they remain browsable on GitHub).
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "*.md"]

root_doc = "index"
autosummary_generate = False
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "show-inheritance": True}
# Importing the package must never need hardware, serial ports, MATLAB or
# Windows networking.  Optional / Windows-only modules are mocked if absent.
autodoc_mock_imports = ["win32com", "pythoncom", "pywintypes", "matlab", "matlab.engine"]
napoleon_google_docstring = True
napoleon_numpy_docstring = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}
# Do not fail an offline build just because an inventory is unreachable.
intersphinx_timeout = 10

html_theme = "sphinx_rtd_theme"
html_theme_options = {"navigation_depth": 3, "collapse_navigation": False}
html_title = "awr2944-dca-lab"
pygments_style = "sphinx"
