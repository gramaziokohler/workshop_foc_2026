# requires: compas_timber==2.1.1-rc0
# This is the entry file in Cadwork, it needs to be named workshop_foc_2026.py to be recognized by Cadwork as a plugin
# or be executed as a script in Cadwork with the right python environment.
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).absolute().parent
SITE_PACKAGES = PLUGIN_ROOT / ".venv" / "Lib" / "site-packages"

for p in (SITE_PACKAGES, PLUGIN_ROOT):
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
from workshop_foc_2026_2 import run_from_dialog

if __name__ == "__main__":
    run_from_dialog()
