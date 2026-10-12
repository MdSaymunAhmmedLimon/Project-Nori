"""Small standard-library helpers for launching the project on a new machine."""

import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PROJECT_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def use_project_python(*, create=False):
    """Use the project interpreter instead of requiring shell activation."""
    if create and not PROJECT_PYTHON.is_file():
        print("Creating the project's Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    if PROJECT_PYTHON.is_file() and Path(sys.prefix).resolve() != VENV.resolve():
        command = [str(PROJECT_PYTHON), str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
        try:
            raise SystemExit(subprocess.call(command, cwd=ROOT))
        except KeyboardInterrupt:
            raise SystemExit(130) from None
