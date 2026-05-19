# requires: compas_timber==2.1.1-rc0
# This is the entry file in Cadwork, it needs to be named workshop_foc_2026.py to be recognized by Cadwork as a plugin
# or be executed as a script in Cadwork with the right python environment.
import sys
from pathlib import Path

# Cadwork copies the executed script into a temp file (Temp\cw_script.py) and
# runs it from there, so __file__ does NOT point to the real plugin folder.
# Resolve the plugin root via Cadwork's API instead, falling back to __file__
# only when running outside Cadwork (e.g. local tooling/tests).
_PLUGIN_FOLDER_NAME = "workshop_foc_2026"


def _resolve_plugin_root():
    candidates = []
    try:
        import utility_controller as _uc

        plugin_path = _uc.get_plugin_path()
        if plugin_path:
            candidates.append(Path(plugin_path))
        userprofil = _uc.get_3d_userprofil_path()
        if userprofil:
            candidates.append(Path(userprofil) / "API.x64" / _PLUGIN_FOLDER_NAME)
    except Exception:
        pass  # not running inside Cadwork

    candidates.append(Path(__file__).absolute().parent)

    for c in candidates:
        if (c / "workshop_foc_2026_2.py").is_file():
            return c
    return candidates[0]


PLUGIN_ROOT = _resolve_plugin_root()
SITE_PACKAGES = PLUGIN_ROOT / ".venv" / "Lib" / "site-packages"
CADWORK_DIR = PLUGIN_ROOT / "02-cadwork"

for p in (SITE_PACKAGES, PLUGIN_ROOT, CADWORK_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Force-reload all local modules under PLUGIN_ROOT so Cadwork's
# persistent Python interpreter picks up code changes between runs.
# SITE_PACKAGES are not reloaded since they are expected to be stable between runs, and reloading them can cause issues with some packages.
import importlib

_stale = [name for name, mod in sys.modules.items() if hasattr(mod, "__file__") and mod.__file__ and str(PLUGIN_ROOT) in mod.__file__ and str(SITE_PACKAGES) not in mod.__file__]
for name in _stale:
    importlib.reload(sys.modules[name])

# Traditional imports from here ..
# akt_agent builds and shows the "Cadwork Agent" dock widget at *module
# import time*. Its window/launcher references must live as module-level
# globals — see the comment in akt_agent.py: wrapping them in a function
# lets Qt garbage-collect the window. So importing the module IS the entry
# point; there is no function to call afterwards.
import akt_agent  # noqa: F401
