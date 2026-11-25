# PyInstaller spec (onefile). Build from the repo root:
#   pyinstaller packaging/sme-modbus-sim.spec --noconfirm
# SPECPATH is the directory of this spec file (packaging/).
import os
from PyInstaller.utils.hooks import collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(root, "sme_modbus_sim", "static"), "sme_modbus_sim/static"),
    (os.path.join(root, "sme_modbus_sim", "data", "sunspec"), "sme_modbus_sim/data/sunspec"),
    # __main__ resolves configs/ relative to the package parent directory
    (os.path.join(root, "configs", "demo-site.yaml"), "configs"),
]

hiddenimports = (
    collect_submodules("uvicorn")
    + ["multipart", "yaml", "h11", "websockets", "httptools", "serial"]
)

a = Analysis(
    [os.path.join(root, "packaging", "entry.py")],
    pathex=[root],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sme-modbus-sim",
    console=True,
    upx=False,
)
