import sys
from pathlib import Path

import utility_controller

# Cadwork copies the executed script into Temp\cw_script.py and runs it from
# there, so __file__ is unreliable. Resolve the plugin root via Cadwork's API.
# This block is intentionally duplicated in workshop_foc_2026.py: a bootstrap
# that fixes sys.path cannot itself live in a shared importable module (that
# would need sys.path already fixed). It is idempotent and guarded, so it is
# safe whether this file is the entry point or imported by workshop_foc_2026.py.
_PLUGIN_FOLDER_NAME = "workshop_foc_2026"


def _resolve_plugin_root():
    candidates = []
    try:
        plugin_path = utility_controller.get_plugin_path()
        if plugin_path:
            candidates.append(Path(plugin_path))
        userprofil = utility_controller.get_3d_userprofil_path()
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
for _p in (PLUGIN_ROOT / ".venv" / "Lib" / "site-packages", PLUGIN_ROOT, PLUGIN_ROOT / "02-cadwork"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import menu_controller
from controller import WorkshopController

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
