"""Locate bundled resource files (images, ...) both in development and when
frozen with PyInstaller.

Plain relative paths like "assets/ua.png" only work if the current working
directory happens to be the project folder. In a PyInstaller build the data
files live in a temporary extraction folder (sys._MEIPASS for --onefile), so
every resource must be resolved through resource_path().
"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> str:
    """Absolute path of a bundled file, e.g. resource_path("assets/ua.png")."""
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base is not None:               # running from a PyInstaller build
        base = Path(frozen_base)
    else:                                     # running from source: project root
        base = Path(__file__).resolve().parent.parent
    return str(base / relative)
