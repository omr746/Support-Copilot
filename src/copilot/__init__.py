"""Support Copilot - RAG + agent + voice customer-support assistant."""

import sys
from pathlib import Path

# If running on Windows with WSL/Linux .venv, append pure Python packages in .venv to sys.path
_venv_sp = (
    Path(__file__).resolve().parent.parent.parent / ".venv" / "lib" / "python3.12" / "site-packages"
)
if _venv_sp.exists() and str(_venv_sp) not in sys.path:
    sys.path.append(str(_venv_sp))

__version__ = "0.1.0"
