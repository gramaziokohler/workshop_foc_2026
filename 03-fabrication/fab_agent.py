"""Standalone Antikythera fabrication agent with PyQt6 UI."""

import logging
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
from PyQt6 import QtCore
from PyQt6.QtCore import QObject
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QFrame
from PyQt6.QtWidgets import QHBoxLayout
from PyQt6.QtWidgets import QLabel
from PyQt6.QtWidgets import QLineEdit
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
    model_received = pyqtSignal(int)  # number of beams
    task_completed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._confirm_event = threading.Event()

    def wait_for_user_confirmation(self) -> None:
        """Block the calling agent thread until the user clicks 'Finished'."""
        self._confirm_event.clear()
        self._confirm_event.wait()

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
        self._build_ui()
        bridge.connected.connect(self._on_connected)
        bridge.connection_error.connect(self._on_connection_error)
        bridge.model_received.connect(self._on_model_received)
        bridge.task_completed.connect(self._on_task_completed)

    def _build_ui(self) -> None:
        self.setWindowTitle("Fabrication Agent")
        self.setMinimumSize(420, 320)

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
        self._ip_input.setText("172.20.10.12")
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

    def _on_model_received(self, beam_count: int) -> None:
        self._dot.setStyleSheet("color: #f59e0b; font-size: 20px;")
        self._status_lbl.setText("Working — interact with the model")
        self._beam_count_lbl.setText(f"Beams: {beam_count}")
        self._model_card.show()
        self._finish_btn.setEnabled(True)

    def _on_task_completed(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online — waiting for task…")
        self._model_card.hide()
        self._finish_btn.setEnabled(False)

    def _on_finish_clicked(self) -> None:
        self._finish_btn.setEnabled(False)
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
        self.logger.info("FabricationAgent initialized.")

    def dispose(self):
        self.logger.info("FabricationAgent disposed.")
        super().dispose()

    @property
    def _bridge(self) -> "AgentUIBridge | None":
        return _UI_BRIDGE

    @tool(name="fabrication")
    def process_model(self, task: Task) -> Dict[str, Any]:
        """Receive a COMPAS Timber model object, display it, and wait for
        the user to click 'Finished' before completing the task.

        Inputs
        ------
        timber_model : TimberModel
            The COMPAS Timber model object passed directly by the orchestrator.
        """
        model = task.get_input_value("timber_model")
        if model is None:
            raise ValueError("Missing required 'timber_model' input.")

        beams = list(model.beams)
        beam_count = len(beams)
        self.logger.info("Timber model received — %d beam(s).", beam_count)

        bridge = self._bridge
        if bridge:
            bridge.model_received.emit(beam_count)
            bridge.wait_for_user_confirmation()

        return {"beam_count": beam_count}


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
