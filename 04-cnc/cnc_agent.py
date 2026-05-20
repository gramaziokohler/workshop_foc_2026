"""Standalone Antikythera CNC agent with PyQt6 UI."""

import base64
import logging
import os
import sys
import threading
from typing import Any
from typing import Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from antikythera.models import Task
from antikythera_agents.base_agent import Agent
from antikythera_agents.decorators import agent
from antikythera_agents.decorators import tool
from antikythera_agents.launcher import AgentLauncher
from cnc_client import HolzherCncClient
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

_HOPS_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "hops_output")

# Module-level launcher — created when the user clicks Connect.
_launcher: "AgentLauncher | None" = None

# Bridge must exist before AgentLauncher is constructed because the
# launcher instantiates CncAgent in __init__ and the agent reads
# the module-level _UI_BRIDGE at that point.
_UI_BRIDGE: "AgentUIBridge | None" = None


# ---------------------------------------------------------------------------
# UI / agent bridge
# ---------------------------------------------------------------------------


class AgentUIBridge(QObject):
    """Thread-safe bridge between MQTT worker threads and the Qt UI."""

    connected = pyqtSignal()
    connection_error = pyqtSignal(str)
    task_received = pyqtSignal(str)  # filename of the beam being milled
    task_completed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._confirm_event = threading.Event()

    def wait_for_user_confirmation(self) -> None:
        """Block the calling agent thread until the user clicks 'Finished Beam'."""
        self._confirm_event.clear()
        self._confirm_event.wait()

    def user_confirmed(self) -> None:
        """Called from the UI thread when the user clicks 'Finished Beam'."""
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
    background-color: #fefce8;
    border: 1px solid #fde047;
    border-radius: 8px;
}
"""


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class CncWindow(QMainWindow):
    def __init__(self, bridge: AgentUIBridge):
        super().__init__()
        self.bridge = bridge
        self._build_ui()
        bridge.connected.connect(self._on_connected)
        bridge.connection_error.connect(self._on_connection_error)
        bridge.task_received.connect(self._on_task_received)
        bridge.task_completed.connect(self._on_task_completed)

    def _build_ui(self) -> None:
        self.setWindowTitle("CNC Agent")
        self.setMinimumSize(420, 360)

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

        # ── current job card (hidden until a task arrives) ───────────────────
        self._job_card = QFrame()
        self._job_card.setStyleSheet(_INFO_STYLE)
        self._job_card.hide()
        card_layout = QVBoxLayout(self._job_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)
        card_title = QLabel("Milling job loaded")
        card_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #854d0e;")
        card_layout.addWidget(card_title)
        self._job_name_lbl = QLabel()
        self._job_name_lbl.setStyleSheet("font-size: 12px; color: #92400e;")
        card_layout.addWidget(self._job_name_lbl)
        layout.addWidget(self._job_card)

        layout.addStretch()

        # ── finished beam button ─────────────────────────────────────────────
        self._finish_btn = QPushButton("Finished Beam")
        self._finish_btn.setEnabled(False)
        self._finish_btn.setStyleSheet(_BTN_STYLE)
        self._finish_btn.clicked.connect(self._on_finish_clicked)
        layout.addWidget(self._finish_btn)

    # ── slots ────────────────────────────────────────────────────────────────

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
            _launcher = AgentLauncher(broker_host=host, broker_port=443, mqtt_transport="websockets", tls=True)
            _launcher.start()
            self.bridge.connected.emit()
        except Exception as exc:
            self.bridge.connection_error.emit(str(exc))

    def _on_connected(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online — waiting for job…")
        self._toggle_btn.setChecked(False)

    def _on_connection_error(self, message: str) -> None:
        self._dot.setStyleSheet("color: #ef4444; font-size: 20px;")
        self._status_lbl.setText("Connection failed")
        self._conn_error_lbl.setText(f"Error: {message}")
        self._conn_error_lbl.show()
        self._connect_btn.setEnabled(True)

    def _on_task_received(self, filename: str) -> None:
        self._dot.setStyleSheet("color: #f59e0b; font-size: 20px;")
        self._status_lbl.setText("Milling — press Finished Beam when done")
        self._job_name_lbl.setText(filename)
        self._job_card.show()
        self._finish_btn.setEnabled(True)

    def _on_task_completed(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online — waiting for job…")
        self._job_card.hide()
        self._finish_btn.setEnabled(False)

    def _on_finish_clicked(self) -> None:
        self._finish_btn.setEnabled(False)
        self.bridge.user_confirmed()


# ---------------------------------------------------------------------------
# Antikythera agent
# ---------------------------------------------------------------------------


@agent(type="foc")
class CncAgent(Agent):
    """Receives a HOP file, writes it to disk, loads it on the CNC, and
    waits for the operator to click 'Finished Beam'."""

    def __init__(self):
        super().__init__()
        host = os.environ.get("CNC_IP_ADDRESS", "localhost")
        self.zero_point = os.environ.get("CNC_ZERO_POINT", "DV")

        self.client = HolzherCncClient(host=host)
        try:
            self.client.connect()
            LOG.info("CncAgent connected to %s", host)
        except Exception as exc:
            LOG.warning("CncAgent could not connect to %s: %s", host, exc)

    def dispose(self):
        self.client.close()
        super().dispose()

    @property
    def _bridge(self) -> "AgentUIBridge | None":
        return _UI_BRIDGE

    @tool(name="cnc")
    def cnc(self, task: Task) -> Dict[str, Any]:
        """Receive a base64-encoded HOP file, write it to disk, load it on
        the CNC, then wait for the operator to confirm the beam is finished.

        Inputs
        ------
        hops_file : str
            Base64-encoded contents of the .hop file.
        filename : str, optional
            Filename to use when saving to disk. Defaults to "job.hop".
        """
        hops_file = task.get_input_value("hops_file")
        if hops_file is None:
            raise ValueError("Missing required input 'hops_file'.")

        filename = task.get_input_value("filename") or "job.hop"

        # Decode and write locally
        os.makedirs(_HOPS_OUTPUT_DIR, exist_ok=True)
        local_path = os.path.join(_HOPS_OUTPUT_DIR, filename)
        if isinstance(hops_file, str):
            hops_file = base64.b64decode(hops_file)
        with open(local_path, "wb") as fh:
            fh.write(hops_file)
        LOG.info("Wrote HOP file to %s", local_path)

        # Load on the CNC
        LOG.info("Loading HOP file on CNC: %s (zero_point=%s)", local_path, self.zero_point)
        self.client.add_hop_file(self.zero_point, local_path, count=1)

        # Notify the UI and wait for the operator
        bridge = self._bridge
        if bridge:
            bridge.task_received.emit(filename)
            bridge.wait_for_user_confirmation()

        return {"done": True, "local_path": local_path}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("CNC Agent")

    _UI_BRIDGE = AgentUIBridge()

    window = CncWindow(_UI_BRIDGE)
    window.show()

    sys.exit(app.exec())
