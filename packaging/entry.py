"""PyInstaller entry point: run the package CLI with absolute imports."""
from sme_modbus_sim.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
