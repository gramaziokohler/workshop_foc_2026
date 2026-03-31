import os
import sys
from pathlib import Path

# Project directory (where this file is located)
base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
src_dir = base_dir / "src"  # if using a src/ structure

# Path to UV's venv site-packages
# UV creates the venv in .venv by default
venv_site_packages = base_dir / ".venv" / "Lib" / "site-packages"  # Windows
# For Linux/Mac: base_dir / ".venv" / "lib" / "python3.10" / "site-packages"

# Add paths to sys.path
paths_to_add = []

if venv_site_packages.exists():
    paths_to_add.append(str(venv_site_packages))

paths_to_add.extend([str(base_dir), str(src_dir)])

for path in paths_to_add:
    if path and path not in sys.path and os.path.exists(path):
        sys.path.insert(0, path)

# Update PYTHONPATH for submodules
existing_pythonpath = os.environ.get('PYTHONPATH', '')
os.environ['PYTHONPATH'] = os.pathsep.join([*[p for p in paths_to_add if p], existing_pythonpath])

# ============================================
# NOW you can import your external libraries
# ============================================
# import pandas as pd
# import numpy as np
#
# # And cwapi3d modules
# import cadwork as cw
# import element_controller as ec
# import attribute_controller as ac
#
# # Your code...
# element_ids = ec.get_active_identifiable_element_ids()
# df = pd.DataFrame({"element_id": element_ids})
# print(df)

from compas_timber.connections import LMiterJoint

print("this is a test")
print(LMiterJoint)