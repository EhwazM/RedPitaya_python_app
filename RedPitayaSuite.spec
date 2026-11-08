# -*- mode: python ; coding: utf-8 -*-
#
# Build (run on the OS you are targeting -- PyInstaller cannot cross-compile):
#     pyinstaller RedPitayaSuite.spec
# Output: dist/RedPitayaSuite  (Linux)   or   dist/RedPitayaSuite.exe  (Windows)

from PyInstaller.utils.hooks import collect_all

# Everything in docs/ (logos, ...) is copied next to the code inside the
# bundle. Code finds it through app.paths.resource_path("docs/...").
datas = [("docs", "docs")]
binaries = []
hiddenimports = []

# Bokeh loads its JS bundles / templates from its own package folder at
# runtime, which PyInstaller's import scan can't see.
bokeh_datas, bokeh_binaries, bokeh_hidden = collect_all("bokeh")
datas += bokeh_datas
binaries += bokeh_binaries
hiddenimports += bokeh_hidden

# If other code reads local files (e.g. FPGA bitstreams or scripts that
# ssh_control.py uploads to the Red Pitaya), add them here too, for example:
# datas += [("app/rp_ssh/bitstreams", "app/rp_ssh/bitstreams")]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RedPitayaSuite",
    debug=False,
    strip=False,
    upx=False,
    # Keep the console while testing so errors are visible; once it works,
    # set console=False to hide the terminal window.
    console=True,
)
