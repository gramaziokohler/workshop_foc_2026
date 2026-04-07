from pathlib import Path

import menu_controller
import utility_controller
from scripts.controller import WorkshopController

PLUGIN_ROOT = Path(__file__).absolute().parent
DEFAULT_DIR = str(PLUGIN_ROOT / "temp")


def run_from_dialog():
    mode = menu_controller.display_simple_menu(["Import (COMPAS → Cadwork)", "Export (Cadwork → COMPAS)"])
    if not mode:
        utility_controller.print_message("Cancelled.", 0, 0)
        return

    scale = utility_controller.get_user_double_with_default_value("Scale factor (e.g. 1000 for m→mm):", 1000.0)
    if scale <= 0:
        utility_controller.print_error("Invalid scale. Aborting.")
        return

    controller = WorkshopController(scale=scale)

    if mode == "Import (COMPAS → Cadwork)":
        input_path = utility_controller.get_user_file_from_dialog_in_path("JSON files (*.json)", DEFAULT_DIR)
        if not input_path:
            utility_controller.print_message("No file selected. Cancelled.", 0, 0)
            return
        controller.run_import(Path(input_path))

    else:
        input_path = utility_controller.get_user_file_from_dialog_in_path("JSON files (*.json)", DEFAULT_DIR)
        if not input_path:
            utility_controller.print_message("No input file selected. Cancelled.", 0, 0)
            return
        output_path = utility_controller.get_new_user_file_from_dialog_in_path("JSON files (*.json)", DEFAULT_DIR)
        if not output_path:
            utility_controller.print_message("No output file selected. Cancelled.", 0, 0)
            return
        controller.run_export(Path(input_path), Path(output_path))


if __name__ == "__main__":
    run_from_dialog()
