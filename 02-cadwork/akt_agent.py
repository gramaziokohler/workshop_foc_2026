"""Antikythera agent for Cadwork integration with PyQt6 UI."""

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).absolute().parent
SITE_PACKAGES = PLUGIN_ROOT / ".venv" / "Lib" / "site-packages"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

if str(SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(SITE_PACKAGES))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

import os
import socket
import sys
import threading
import time
from typing import Any, Dict

import utility_controller as uc
from antikythera.models import Task
from antikythera_agents import Agent, agent, tool
from antikythera_agents.launcher import AgentLauncher
from PyQt6 import QtCore
from PyQt6.QtCore import (
    QEventLoop,
    QObject,
    QSocketNotifier,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QDockWidget,
)

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
    connected = pyqtSignal()
    connection_error = pyqtSignal(str)

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
# Paho ↔ Qt event-loop bridge
# ---------------------------------------------------------------------------


class MqttLoopManager(QObject):
    """Integrates a paho MQTT client into the Qt event loop properly.

    Uses QSocketNotifier to react to read/write readiness on paho's raw socket
    (fully event-driven, no polling) and a slow QTimer for keepalive/misc work.
    Call stop() before destroying the paho client.
    """

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client

        # Take over from paho's internal threading.Thread.
        client.loop_stop()

        fd = client.socket().fileno()

        self._read_notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
        self._read_notifier.activated.connect(self._on_readable)

        self._write_notifier = QSocketNotifier(fd, QSocketNotifier.Type.Write, self)
        self._write_notifier.activated.connect(self._on_writable)
        self._write_notifier.setEnabled(False)  # enabled only when paho has data to send

        # Keepalives, ping timeouts, reconnect logic — doesn't need to be frequent.
        self._misc_timer = QTimer(self)
        self._misc_timer.setInterval(1000)
        self._misc_timer.timeout.connect(self._on_misc)
        self._misc_timer.start()

    def _on_readable(self):
        self._client.loop_read()
        self._sync_write_notifier()

    def _on_writable(self):
        self._client.loop_write()
        self._sync_write_notifier()

    def _on_misc(self):
        self._client.loop_misc()
        self._sync_write_notifier()

    def _sync_write_notifier(self):
        """Enable the write notifier only when paho actually has pending writes."""
        self._write_notifier.setEnabled(self._client.want_write())

    def stop(self):
        self._read_notifier.setEnabled(False)
        self._write_notifier.setEnabled(False)
        self._misc_timer.stop()


# ---------------------------------------------------------------------------
# Connection worker
# ---------------------------------------------------------------------------


class ConnectWorker(QThread):
    """Runs the broker TCP probe + AgentLauncher construction off the main thread.

    Using QThread (instead of threading.Thread) ensures the Cadwork Python
    runtime keeps the thread scheduled even when the main thread is idle.
    """

    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, host: str, port: int = 1883):
        super().__init__()
        self.host = host
        self.port = port

    def run(self) -> None:
        global _launcher
        try:
            socket.create_connection((self.host, self.port), timeout=5).close()
        except OSError as exc:
            self.failed.emit(f"Cannot reach {self.host}:{self.port} — {exc}")
            return
        try:
            _launcher = AgentLauncher(broker_host=self.host, broker_port=self.port)
            _launcher.start()
            self.succeeded.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


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


class AgentWindow(QDockWidget):
    def __init__(self, bridge: AgentUIBridge):
        super().__init__()
        self.bridge = bridge
        self._build_ui()
        bridge.connected.connect(self._on_connected)
        bridge.connection_error.connect(self._on_connection_error)
        bridge.import_started.connect(self._on_import_started)
        bridge.ready_for_interaction.connect(self._on_ready_for_interaction)
        bridge.export_started.connect(self._on_export_started)
        bridge.task_completed.connect(self._on_task_completed)

    def _build_ui(self) -> None:
        self.setWindowTitle("Cadwork Agent")

        root = QWidget()
        root.setMinimumSize(380, 280)
        self.setWidget(root)
        layout = QVBoxLayout(root)
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

        # ── status row ──────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet("color: #9ca3af; font-size: 20px;")
        self._status_lbl = QLabel("Not connected")
        self._status_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── task / file info ─────────────────────────────────────────────────
        self._task_lbl = QLabel("")
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

    # ── connection panel slots ───────────────────────────────────────────────

    def _on_panel_toggled(self, checked: bool) -> None:
        self._conn_panel.setVisible(checked)
        self._toggle_btn.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow)

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

        LOG.info(f"Attempting to connect to broker: {host}:1883")
        self._connect_worker = ConnectWorker(host, port=1883)

        loop = QEventLoop()
        self._connect_worker.succeeded.connect(loop.quit)
        self._connect_worker.failed.connect(lambda _: loop.quit())
        self._connect_worker.succeeded.connect(self.bridge.connected)
        self._connect_worker.failed.connect(self.bridge.connection_error)
        self._connect_worker.finished.connect(self._connect_worker.deleteLater)
        self._connect_worker.start()

        # Block the main thread with a local event loop so Qt keeps processing
        # events (and the QThread keeps making progress) until connection
        # succeeds or fails.
        loop.exec()

    def _on_connected(self) -> None:
        self._dot.setStyleSheet("color: #22c55e; font-size: 20px;")
        self._status_lbl.setText("Online")
        self._task_lbl.setText("Waiting for tasks…")
        # Collapse the connection panel
        self._toggle_btn.setChecked(False)

        # Hand paho's network I/O to Qt's event loop via socket notifiers.
        self._mqtt_loop = MqttLoopManager(_launcher.transport.client, parent=self)

        # AgentLauncher executes tasks in threading.Threads which only get the
        # GIL when the main thread yields it.  A periodic sleep(0) from a
        # QTimer is the correct way to yield the GIL without busy-spinning.
        self._gil_yield_timer = QTimer(self)
        self._gil_yield_timer.setInterval(10)  # 100 Hz
        self._gil_yield_timer.timeout.connect(lambda: time.sleep(0))
        self._gil_yield_timer.start()

    def _on_connection_error(self, message: str) -> None:
        self._dot.setStyleSheet("color: #ef4444; font-size: 20px;")
        self._status_lbl.setText("Connection failed")
        self._conn_error_lbl.setText(f"Error: {message}")
        self._conn_error_lbl.show()
        self._connect_btn.setEnabled(True)

    # ── agent status slots ───────────────────────────────────────────────────

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


@agent(type="foc")
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

    @tool(name="planning")
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
        timber_model = task.get_input_value("timber_model")
        if not timber_model:
            for input in task.inputs:
                print(f"Input '{input.name}' value: {input.value}")
            raise ValueError("Missing required 'timber_model' input or param.")

        bridge = self._bridge

        # ── Phase 1: import into Cadwork ─────────────────────────────────────
        # Cadwork Python API modules are only available inside the Cadwork
        # runtime, so we import them lazily here rather than at module level.
        if bridge:
            bridge.notify_import_started(task.id, task.type)

        from ct_to_cw import ImportController

        self.logger.info(f"Importing model into Cadwork: {timber_model}")
        controller = ImportController()
        controller.load_model(timber_model)
        self.logger.info("Model imported. Waiting for user to finish in Cadwork.")

        # ── Phase 2: hand control to the user ────────────────────────────────
        if bridge:
            bridge.notify_ready_for_interaction()
            bridge.wait_for_user_confirmation()
        # (headless / no-UI fallback: skip waiting and export immediately)

        # ── Phase 3: export modified model back to CT ─────────────────────────
        self.logger.info("Exporting modified model...")
        if bridge:
            bridge.notify_export_started()

        from cw_to_ct import ExportController

        exporter = ExportController()
        exporter.load_model(timber_model)
        output_model = exporter.export_model()
        self.logger.info("Export complete.")

        # ── Phase 4: signal task done to UI ───────────────────────────────────
        if bridge:
            bridge.task_completed.emit()

        return {"timber_model": output_model}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def show_error_popup_and_exit(title: str, message: str):
    _ = QMessageBox.critical(None, title, message, defaultButton=QMessageBox.StandardButton.Ok)
    sys.exit(1)


cadworkMainWindow = uc.get_3d_hwnd()
print("get_3d_hwnd", cadworkMainWindow)

if cadworkMainWindow < 0:
    show_error_popup_and_exit("Error", "No window handle found")

lParentWidget = QWidget.find(cadworkMainWindow)
print("lParentWidget", lParentWidget)

if not lParentWidget:
    show_error_popup_and_exit("Error", "Could not find cadwork main window.")

# This must be at the module's root, otherwise the window briefly loads then disappears!!!

# _launcher is created on demand when the user clicks Connect in the UI.
_launcher: "AgentLauncher | None" = None

# Bridge must exist before AgentLauncher is constructed because the
# launcher instantiates CadworkAgent in __init__ and the agent reads
# the module-level _UI_BRIDGE at that point.
_UI_BRIDGE = AgentUIBridge()

window = AgentWindow(_UI_BRIDGE)

lParentWidget.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, window)
window.show()
