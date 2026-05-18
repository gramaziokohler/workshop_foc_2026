import sys
from pathlib import Path

# Self-bootstrap sys.path so this module works whether it is imported via the
# workshop_foc_2026.py plugin entry or launched directly by Cadwork (which runs
# scripts from a temp dir, leaving 02-cadwork/ off sys.path). Guarded inserts
# make this a no-op when the path is already set up.
_HERE = Path(__file__).absolute().parent  # .../02-cadwork
_SITE_PACKAGES = _HERE.parent / ".venv" / "Lib" / "site-packages"

for _p in (_SITE_PACKAGES, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ct_to_cw import ImportController
from cw_to_ct import ExportController
import utility_controller as uc


class WorkshopController:
    """Composes ImportController and ExportController with a shared scale factor."""

    def __init__(self, scale=1000.0):
        self.scale = scale
        self.import_controller = ImportController(scale=scale)
        self.export_controller = ExportController(scale=scale)

    def run_import(self, file_path):
        uc.print_message(f"Importing {file_path} with scale {self.scale}.", 0, 0)
        return self.import_controller.load_model_from_file(file_path)

    def run_export(self, input_path, output_path):
        uc.print_message(
            f"Exporting {input_path} to {output_path} with scale {self.scale}.", 0, 0
        )
        self.export_controller.load_model(input_path)
        self.export_controller.export_model_to_file(output_path)
