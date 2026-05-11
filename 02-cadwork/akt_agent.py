"""Antikythera agent for Cadwork integration with PyQt6 UI."""

import logging
import os
import sys
import threading
from typing import Any
from typing import Dict

from antikythera.models import Task
from antikythera_agents import Agent
from antikythera_agents import agent
from antikythera_agents import tool
from antikythera_agents.launcher import AgentLauncher
from PyQt6.QtCore import QObject
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QFrame
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

LOG = logging.getLogger(__name__)

# Shared bridge – must be set before AgentLauncher is constructed so that
# CadworkAgent (instantiated inside the launcher) can reference it.
_UI_BRIDGE: "AgentUIBridge | None" = None


# ---------------------------------------------------------------------------
# UI / agent bridge
# ---------------------------------------------------------------------------


class AgentUIBridge(QObject):
    """Thread-safe bridge between MQTT worker threads and the Qt UI.

    Signals are emitted from agent threads; Qt delivers them safely on the
    main thread via its queued-connection mechanism.

    Workflow phases
    ---------------
    1. ``import_started``       - ct_to_cw import running
    2. ``ready_for_interaction`` - model is in Cadwork, user can work freely
    3. ``export_started``       - cw_to_ct export running after user confirms
    4. ``task_completed``       - back to idle / online
    """

    import_started = pyqtSignal(str, str)  # task_id, task_type
    ready_for_interaction = pyqtSignal()
    export_started = pyqtSignal()
    task_completed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._confirm_event = threading.Event()

    # -- called from agent threads -------------------------------------------

    def notify_import_started(self, task_id: str, task_type: str) -> None:
        self.import_started.emit(task_id, task_type)

    def notify_ready_for_interaction(self) -> None:
        """Called after ct_to_cw import finishes - unlocks the UI."""
        self.ready_for_interaction.emit()

    def wait_for_user_confirmation(self) -> None:
        """Block the agent thread until the user clicks 'Finished'."""
        self._confirm_event.clear()
        self._confirm_event.wait()

    def notify_export_started(self) -> None:
        """Called right before cw_to_ct export begins."""
        self.export_started.emit()

    # -- called from the UI thread -------------------------------------------

    def user_confirmed(self) -> None:
        self._confirm_event.set()


# ---------------------------------------------------------------------------
# Qt window
# ---------------------------------------------------------------------------

_BTN_STYLE = """
QPushButton {
    background-color: #3b82f6;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton:hover   { background-color: #2563eb; }
QPushButton:pressed { background-color: #1d4ed8; }
QPushButton:disabled {
    background-color: #d1d5db;
    color: #9ca3af;
}
"""


class AgentWindow(QMainWindow):
    def __init__(self, bridge: AgentUIBridge):
        super().__init__()
        self.bridge = bridge
        self._build_ui()
        bridge.import_started.connect(self._on_import_started)
        bridge.ready_for_interaction.connect(self._on_ready_for_interaction)
        bridge.export_started.connect(self._on_export_started)
        bridge.task_completed.connect(self._on_task_completed)

    def _build_ui(self) -> None:
        self.setWindowTitle("Cadwork Agent")
        self.setMinimumSize(380, 240)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── status row ──────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl = QLabel("Online")
        self._status_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e5e7eb;")
        layout.addWidget(sep)

        # ── task / file info ─────────────────────────────────────────────────
        self._task_lbl = QLabel("Waiting for tasks…")
        self._task_lbl.setStyleSheet("font-size: 12px; color: #6b7280;")
        self._task_lbl.setWordWrap(True)
        layout.addWidget(self._task_lbl)

        layout.addStretch()

        # ── finished button ──────────────────────────────────────────────────
        self._finish_btn = QPushButton("Finished")
        self._finish_btn.setEnabled(False)
        self._finish_btn.setStyleSheet(_BTN_STYLE)
        self._finish_btn.clicked.connect(self._on_finish_clicked)
        layout.addWidget(self._finish_btn)

    # ── slots ────────────────────────────────────────────────────────────────

    def _on_import_started(self, task_id: str, task_type: str) -> None:
        self._dot.setStyleSheet("color: #f59e0b; font-size: 20px;")
        self._status_lbl.setText("Importing…")
        self._task_lbl.setText(f"<b>Type:</b> {task_type}<br><b>ID:</b> {task_id}")
        self._finish_btn.setEnabled(False)

    def _on_ready_for_interaction(self) -> None:
        self._status_lbl.setText("Interact in Cadwork")
        self._finish_btn.setEnabled(True)

    def _on_export_started(self) -> None:
        self._status_lbl.setText("Exporting…")
        self._finish_btn.setEnabled(False)

    def _on_task_completed(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online")
        self._task_lbl.setText("Waiting for tasks…")
        self._finish_btn.setEnabled(False)

    def _on_finish_clicked(self) -> None:
        self._finish_btn.setEnabled(False)
        self.bridge.user_confirmed()


# ---------------------------------------------------------------------------
# Antikythera agent
# ---------------------------------------------------------------------------


@agent(type="cadwork")
class CadworkAgent(Agent):
    """Agent that imports a COMPAS Timber model into Cadwork, waits for the
    user to finish manual work, then exports the modified model back to CT."""

    def __init__(self):
        super().__init__()
        self.logger.info("CadworkAgent initialized.")

    def dispose(self):
        self.logger.info("CadworkAgent disposed.")
        super().dispose()

    @property
    def _bridge(self) -> "AgentUIBridge | None":
        return _UI_BRIDGE

    @tool(name="process_model")
    def process_model(self, task: Task) -> Dict[str, Any]:
        """Import a COMPAS Timber model into Cadwork, let the user work on it,
        then export the modified model back to COMPAS Timber format.

        Inputs / params
        ---------------
        timber_model : str
            Path to the input COMPAS Timber JSON file.
        output_model : str, optional
            Path for the exported JSON.  Defaults to
            ``<input_stem>_modified<ext>``.
        """
        timber_model = task.get_input_value("timber_model") or task.get_param_value("timber_model")
        if not timber_model:
            raise ValueError("Missing required 'timber_model' input or param.")

        output_model = task.get_input_value("output_model") or task.get_param_value("output_model")
        if not output_model:
            base, ext = os.path.splitext(timber_model)
            output_model = f"{base}_modified{ext}"

        bridge = self._bridge

        # ── Phase 1: import into Cadwork ─────────────────────────────────────
        # Cadwork Python API modules are only available inside the Cadwork
        # runtime, so we import them lazily here rather than at module level.
        self.logger.info(f"Importing model into Cadwork: {timber_model}")
        if bridge:
            bridge.notify_import_started(task.id, task.type)

        from ct_to_cw import ImportController

        controller = ImportController()
        controller.load_model_from_file(timber_model)
        self.logger.info("Model imported. Waiting for user to finish in Cadwork.")

        # ── Phase 2: hand control to the user ────────────────────────────────
        if bridge:
            bridge.notify_ready_for_interaction()
            bridge.wait_for_user_confirmation()
        # (headless / no-UI fallback: skip waiting and export immediately)

        # ── Phase 3: export modified model back to CT ─────────────────────────
        self.logger.info(f"Exporting modified model to: {output_model}")
        if bridge:
            bridge.notify_export_started()

        from cw_to_ct import ExportController

        exporter = ExportController()
        exporter.load_model(timber_model)
        exporter.export_model_to_file(output_model)
        self.logger.info("Export complete.")

        # ── Phase 4: signal task done to UI ───────────────────────────────────
        if bridge:
            bridge.task_completed.emit()

        return {"timber_model": output_model}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    app = QApplication(sys.argv)

    # Bridge must exist before AgentLauncher is constructed because the
    # launcher instantiates CadworkAgent in __init__ and the agent reads
    # the module-level _UI_BRIDGE at that point.
    _UI_BRIDGE = AgentUIBridge()

    window = AgentWindow(_UI_BRIDGE)
    window.show()

    launcher = AgentLauncher(broker_host="127.0.0.1", broker_port=1883)
    launcher.start()  # non-blocking: just subscribes to MQTT topics

    exit_code = app.exec()  # Qt event loop - MQTT callbacks run in their own threads

    launcher.stop()
    sys.exit(exit_code)
