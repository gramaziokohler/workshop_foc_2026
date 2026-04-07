from scripts.ct_to_cw import ImportController
from scripts.cw_to_ct import ExportController
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
