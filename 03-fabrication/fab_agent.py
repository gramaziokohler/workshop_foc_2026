"""Standalone Antikythera fabrication agent with PyQt6 UI."""

import base64
import logging
import os
import sys
import threading
from typing import Any
from typing import Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from antikythera.models import Task
from antikythera_agents import Agent
from antikythera_agents import agent
from antikythera_agents import tool
from antikythera_agents.launcher import AgentLauncher
from easyhops_lib import EasyHops
from PyQt6 import QtCore
from PyQt6.QtCore import QObject
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QFrame
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
from PyQt6.QtWidgets import QListWidget
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import QToolButton
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

LOG = logging.getLogger(__name__)

# Module-level launcher — created when the user clicks Connect.
_launcher: "AgentLauncher | None" = None

# Bridge must exist before AgentLauncher is constructed because the
# launcher instantiates FabricationAgent in __init__ and the agent reads
# the module-level _UI_BRIDGE at that point.
_UI_BRIDGE: "AgentUIBridge | None" = None


# ---------------------------------------------------------------------------
# UI / agent bridge
# ---------------------------------------------------------------------------


class AgentUIBridge(QObject):
    """Thread-safe bridge between MQTT worker threads and the Qt UI."""

    connected = pyqtSignal()
    connection_error = pyqtSignal(str)
    model_received = pyqtSignal(object)  # list of beams to fabricate
    task_completed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._confirm_event = threading.Event()
        self._execute_result: "tuple[bytes, str] | None" = None

    def wait_for_user_confirmation(self) -> None:
        """Block the calling agent thread until the user clicks 'Execute'."""
        self._execute_result = None
        self._confirm_event.clear()
        self._confirm_event.wait()

    def execute_selected(self, hops_file: bytes, filename: str, beam_guid: str) -> None:
        """Called from the UI thread when the user clicks 'Execute'."""
        self._execute_result = (hops_file, filename, beam_guid)
        self._confirm_event.set()
        self.task_completed.emit()

    def user_confirmed(self) -> None:
        """Called from the UI thread when the user clicks 'Finished'."""
        self._confirm_event.set()
        self.task_completed.emit()


# ---------------------------------------------------------------------------
# Styles
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

_INFO_STYLE = """
QFrame {
    background-color: #f0fdf4;
    border: 1px solid #86efac;
    border-radius: 8px;
}
"""


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class FabWindow(QMainWindow):
    def __init__(self, bridge: AgentUIBridge):
        super().__init__()
        self.bridge = bridge
        self.easy_hops = EasyHops()
        self._hops_filepaths: dict = {}
        self._build_ui()
        bridge.connected.connect(self._on_connected)
        bridge.connection_error.connect(self._on_connection_error)
        bridge.model_received.connect(self._on_model_received)
        bridge.task_completed.connect(self._on_task_completed)

    def _build_ui(self) -> None:
        self.setWindowTitle("Fabrication Agent")
        self.setMinimumSize(480, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── collapsible connection panel ─────────────────────────────────────
        self._toggle_btn = QToolButton()
        self._toggle_btn.setText("Connection Settings")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self._toggle_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle_btn.setStyleSheet("font-size: 12px; font-weight: bold; text-align: left;")
        self._toggle_btn.toggled.connect(self._on_panel_toggled)
        layout.addWidget(self._toggle_btn)

        self._conn_panel = QFrame()
        self._conn_panel.setFrameShape(QFrame.Shape.StyledPanel)
        conn_layout = QVBoxLayout(self._conn_panel)
        conn_layout.setContentsMargins(8, 8, 8, 8)
        conn_layout.setSpacing(8)

        ip_row = QHBoxLayout()
        ip_row.addWidget(QLabel("Broker IP:"))
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("e.g. 192.168.1.100")
        self._ip_input.setText("antikythera.ethz.ch")
        self._ip_input.returnPressed.connect(self._on_connect_clicked)
        ip_row.addWidget(self._ip_input)
        conn_layout.addLayout(ip_row)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setStyleSheet(_BTN_STYLE)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        conn_layout.addWidget(self._connect_btn)

        self._conn_error_lbl = QLabel()
        self._conn_error_lbl.setStyleSheet("color: #ef4444; font-size: 11px;")
        self._conn_error_lbl.setWordWrap(True)
        self._conn_error_lbl.hide()
        conn_layout.addWidget(self._conn_error_lbl)

        layout.addWidget(self._conn_panel)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e5e7eb;")
        layout.addWidget(sep)

        # ── status row ───────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #9ca3af; font-size: 20px;")
        self._status_lbl = QLabel("Not connected")
        self._status_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── model info card (hidden until a model arrives) ───────────────────
        self._model_card = QFrame()
        self._model_card.setStyleSheet(_INFO_STYLE)
        self._model_card.hide()
        card_layout = QVBoxLayout(self._model_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        card_title = QLabel("Timber Model received")
        card_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #15803d;")
        card_layout.addWidget(card_title)
        self._beam_count_lbl = QLabel()
        self._beam_count_lbl.setStyleSheet("font-size: 12px; color: #166534;")
        card_layout.addWidget(self._beam_count_lbl)
        layout.addWidget(self._model_card)

        # ── beams to fabricate section ───────────────────────────────────────
        self._beams_section = QFrame()
        beams_layout = QVBoxLayout(self._beams_section)
        beams_layout.setContentsMargins(0, 0, 0, 0)
        beams_layout.setSpacing(6)
        self._beams_header_lbl = QLabel("Beams to Fabricate")
        self._beams_header_lbl.setStyleSheet("font-size: 12px; font-weight: bold;")
        beams_layout.addWidget(self._beams_header_lbl)
        self._beams_list = QListWidget()
        self._beams_list.setMinimumHeight(100)
        self._beams_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        beams_layout.addWidget(self._beams_list)
        self._generate_btn = QPushButton("Generate")
        self._generate_btn.setStyleSheet(_BTN_STYLE)
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        beams_layout.addWidget(self._generate_btn)
        self._beams_section.hide()
        layout.addWidget(self._beams_section)

        # ── generated hops files section ─────────────────────────────────────
        self._hops_section = QFrame()
        hops_layout = QVBoxLayout(self._hops_section)
        hops_layout.setContentsMargins(0, 0, 0, 0)
        hops_layout.setSpacing(6)
        hops_header_lbl = QLabel("Generated HOP Files")
        hops_header_lbl.setStyleSheet("font-size: 12px; font-weight: bold;")
        hops_layout.addWidget(hops_header_lbl)
        self._hops_list = QListWidget()
        self._hops_list.setMinimumHeight(100)
        hops_layout.addWidget(self._hops_list)
        hops_btn_row = QHBoxLayout()
        self._sim_btn = QPushButton("Simulate")
        self._sim_btn.setStyleSheet(_BTN_STYLE)
        self._exec_btn = QPushButton("Execute")
        self._exec_btn.setStyleSheet(_BTN_STYLE)
        self._exec_btn.clicked.connect(self._on_execute_clicked)
        hops_btn_row.addWidget(self._sim_btn)
        hops_btn_row.addWidget(self._exec_btn)
        hops_layout.addLayout(hops_btn_row)
        self._hops_section.hide()
        layout.addWidget(self._hops_section)

        layout.addStretch()

        # ── finished button ──────────────────────────────────────────────────
        self._finish_btn = QPushButton("Finished")
        self._finish_btn.setEnabled(False)
        self._finish_btn.setStyleSheet(_BTN_STYLE)
        self._finish_btn.clicked.connect(self._on_finish_clicked)
        layout.addWidget(self._finish_btn)

    # ── connection panel slots ───────────────────────────────────────────────

    def _on_panel_toggled(self, checked: bool) -> None:
        self._conn_panel.setVisible(checked)
        arrow = QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow
        self._toggle_btn.setArrowType(arrow)

    def _on_connect_clicked(self) -> None:
        global _launcher
        host = self._ip_input.text().strip()
        if not host:
            self._conn_error_lbl.setText("Please enter a broker IP address.")
            self._conn_error_lbl.show()
            return

        self._connect_btn.setEnabled(False)
        self._conn_error_lbl.hide()
        self._dot.setStyleSheet("color: #f59e0b; font-size: 20px;")
        self._status_lbl.setText("Connecting…")

        try:
            _launcher = AgentLauncher(broker_host=host, broker_port=1883)
            _launcher.start()
            self.bridge.connected.emit()
        except Exception as exc:
            self.bridge.connection_error.emit(str(exc))

    def _on_connected(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online — waiting for task…")
        self._toggle_btn.setChecked(False)

    def _on_connection_error(self, message: str) -> None:
        self._dot.setStyleSheet("color: #ef4444; font-size: 20px;")
        self._status_lbl.setText("Connection failed")
        self._conn_error_lbl.setText(f"Error: {message}")
        self._conn_error_lbl.show()
        self._connect_btn.setEnabled(True)

    def _on_model_received(self, beams: list) -> None:
        self._beams_to_fabricate = beams
        beam_count = len(beams)
        self._dot.setStyleSheet("color: #f59e0b; font-size: 20px;")
        self._status_lbl.setText("Working — interact with the model")
        self._beam_count_lbl.setText(f"Beams: {beam_count}")
        self._model_card.show()
        self._finish_btn.setEnabled(True)
        self._beams_list.clear()
        for i, beam in enumerate(beams):
            attrs = beam.attributes.get("cadwork", {})
            label = attrs.get("name") or attrs.get("element_name") or f"Beam {i + 1}"
            self._beams_list.addItem(label)
        self._beams_section.show()

    def _on_task_completed(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online — waiting for task…")
        self._model_card.hide()
        self._finish_btn.setEnabled(False)
        self._beams_section.hide()
        self._hops_section.hide()

    def _on_finish_clicked(self) -> None:
        self._finish_btn.setEnabled(False)
        self.bridge.user_confirmed()

    def _on_generate_clicked(self):
        beams = getattr(self, "_beams_to_fabricate", [])
        self._hops_filepaths = self.easy_hops.generate_hops(beams)
        # Keep an ordered list of guids so list row index maps back to a beam
        self._hops_guids = list(self._hops_filepaths.keys())
        self._hops_list.clear()
        for filepath in self._hops_filepaths.values():
            self._hops_list.addItem(os.path.basename(filepath))
        self._hops_section.show()

    def _on_execute_clicked(self):
        row = self._hops_list.currentRow()
        if row < 0:
            return  # nothing selected

        guid = self._hops_guids[row]
        filepath = self._hops_filepaths[guid]

        # Derive filename from the beam's cadwork id
        beam = next((b for b in getattr(self, "_beams_to_fabricate", []) if str(b.guid) == str(guid)), None)
        cadwork_id = None
        if beam is not None:
            cadwork_id = beam.attributes.get("cadwork", {}).get("id")
        filename = f"beam_{cadwork_id}.hop" if cadwork_id is not None else os.path.basename(filepath)

        with open(filepath, "rb") as fh:
            hops_file = base64.b64encode(fh.read()).decode("ascii")

        self.bridge.execute_selected(hops_file, filename, guid)

    def _on_start_sim(self):
        selected_beam = ...  # geete from UI
        hops_filepath = self._hops_filepaths[selected_beam.guid]
        self.easy_hops.start_sim(hops_filepath, selected_beam)

    def _on_start_milling(self):
        selected_beam = ...  # geete from UI
        hops_filepath = self._hops_filepaths[selected_beam.guid]
        self.easy_hops.execute(hops_filepath, selected_beam)
        self.bridge.user_confirmed()


# ---------------------------------------------------------------------------
# Antikythera agent
# ---------------------------------------------------------------------------


@agent(type="foc")
class FabricationAgent(Agent):
    """Receives a COMPAS Timber model, shows it in the UI, and waits for
    the user to confirm before completing the task."""

    def __init__(self):
        super().__init__()
        self._pending_guids: set = set()
        self.logger.info("FabricationAgent initialized.")

    def dispose(self):
        self.logger.info("FabricationAgent disposed.")
        super().dispose()

    @property
    def _bridge(self) -> "AgentUIBridge | None":
        return _UI_BRIDGE

    @tool(name="fabrication")
    def process_model(self, task: Task) -> Dict[str, Any]:
        """Handle both 'fabrication' and 'fabrication_close' tasks (both have type foc.fabrication).

        fabrication: Receive a COMPAS Timber model, show the next pending beam,
        wait for the user to generate and execute its HOP file, then return the
        HOP data together with has_hops to drive the hops-loop.

        fabrication_close: Mark all to-fabricate beams as done, reset the queue,
        and return the updated model plus all_fabricated to drive the fab-loop.
        """
        if task.id == "fabrication_close":
            return self._close_batch(task)

        model = task.get_input_value("timber_model")
        if model is None:
            raise ValueError("Missing required 'timber_model' input.")

        # Populate the pending queue on the first call of a new batch.
        if not self._pending_guids:
            model.process_joinery()
            for beam in model.beams:
                cadwork_attrs = beam.attributes.get("cadwork", {})
                to_fabricate = cadwork_attrs.get("user_attributes", {}).get("1", {}).get("value")
                if to_fabricate:
                    self._pending_guids.add(str(beam.guid))
            self.logger.info("%d beams queued for fabrication.", len(self._pending_guids))

        if not self._pending_guids:
            return {"has_hops": False}

        pending_beams = [b for b in model.beams if str(b.guid) in self._pending_guids]

        bridge = self._bridge
        if bridge:
            bridge.model_received.emit(pending_beams)
            bridge.wait_for_user_confirmation()

        if bridge and bridge._execute_result:
            hops_file, filename, executed_guid = bridge._execute_result
            self._pending_guids.discard(str(executed_guid))
            has_hops = bool(self._pending_guids)
            return {"hops_file": hops_file, "filename": filename, "has_hops": has_hops}

        # User clicked 'Finished' without executing — exit the loop.
        return {"has_hops": False}

    def _close_batch(self, task: Task) -> Dict[str, Any]:
        """Mark the current batch as fabricated and signal whether all beams are done."""
        model = task.get_input_value("timber_model")
        if model is None:
            raise ValueError("Missing required 'timber_model' input.")

        for beam in model.beams:
            cadwork_attrs = beam.attributes.get("cadwork", {})
            to_fabricate = cadwork_attrs.get("user_attributes", {}).get("1", {}).get("value")
            if to_fabricate:
                beam.attributes["fabricated"] = True

        self._pending_guids.clear()

        all_fabricated = all(b.attributes.get("fabricated") for b in model.beams)
        self.logger.info("Batch closed. All fabricated: %s", all_fabricated)
        return {"timber_model": model, "all_fabricated": all_fabricated}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Fabrication Agent")

    _UI_BRIDGE = AgentUIBridge()

    window = FabWindow(_UI_BRIDGE)
    window.show()

    sys.exit(app.exec())
