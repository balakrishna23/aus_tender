"""Shared paths, independent of the shell's working directory."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def database_directory():
    configured = Path(os.environ.get("AUSTENDER_DB_DIR", "databases"))
    return configured if configured.is_absolute() else PROJECT_DIR / configured
