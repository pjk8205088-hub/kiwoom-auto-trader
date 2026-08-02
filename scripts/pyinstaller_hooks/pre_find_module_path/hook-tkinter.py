from pathlib import Path
import sys


def pre_find_module_path(hook_api):
    """Keep tkinter discoverable when PyInstaller cannot initialize host Tcl."""
    stdlib = Path(sys.base_prefix) / "Lib"
    hook_api.search_dirs = [str(stdlib)]
