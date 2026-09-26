from __future__ import annotations

import signal
import sys
from queue import Queue
from threading import Thread

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bokeh.models import Range1d
from bokeh.plotting import figure as bk_figure
from bokeh.server.server import Server
from tornado.ioloop import IOLoop

from assets.dark_theme import pyside6_simple_dark_theme
from app.rp_plot.plot_setup import BokehPlot, LIABokehPlot
from app.rp_ssh.ssh_control import RedPitayaSSH, RedPitayaSSHError
from app.ui_pyside.lock_in_amp import LockInAmp
from app.ui_pyside.oscilloscope import Oscilloscope


# ============================================================================
# Bokeh documents
# ============================================================================

# Oscilloscope document
oscilloscope_figure = bk_figure(
    title="Signal",
    sizing_mode="stretch_both",
    x_axis_label="Time",
    y_axis_label="Voltage (V)",
    y_range=Range1d(start=-1, end=1),
    x_range=Range1d(start=-30, end=30),
)  # type: ignore

bokeh_plot = BokehPlot(
    plot_b=oscilloscope_figure,
    n_plots=2,
    roll_over=1000,
    colors=["green", "purple", "blue"],
    update_time=10,
    scatter_plot=True,
    oscilloscope_mode=True,
)


# LIA document
lia_plot = LIABokehPlot(
    host="10.42.0.100",
    port=5001,
    update_time=100,
    roll_over=1000,
)

# The Red Pitaya's address as entered on the launcher screen. Read by
# modify_lia_doc() at the moment a /lia browser session starts (i.e. once
# the LockInAmp window's embedded browser loads the page), which is always
# after ModeSelector has already updated it for the board just connected to.
current_rp_host = "10.42.0.100"


def modify_oscilloscope_doc(doc):
    bokeh_plot.attach_doc(doc)


def modify_lia_doc(doc):
    # IMPORTANT: Bokeh Model objects cannot be shared between Documents.
    # Create a completely new plot/model tree for every /lia session.
    lia_plot = LIABokehPlot(
        host=current_rp_host,
        port=5001,
        update_time=100,
        roll_over=1000,
    )
    lia_plot.attach_doc(doc)


# ============================================================================
# Bokeh server
# ============================================================================

def start_bokeh_server(port_queue: Queue):
    """
    Start one Bokeh server containing both documents.

    /      -> Oscilloscope
    /lia   -> Lock-in Amplifier
    """
    port = 5006

    while True:
        try:
            loop = IOLoop()
            loop.make_current()

            server = Server(
                {
                    "/": modify_oscilloscope_doc,
                    "/lia": modify_lia_doc,
                },
                io_loop=loop,
                allow_websocket_origin=[
                    f"localhost:{port}",
                    f"127.0.0.1:{port}",
                ],
                port=port,
            )

            server.start()

            print(
                f"Bokeh server started at "
                f"http://localhost:{port}/"
            )
            print(
                f"LIA Bokeh document available at "
                f"http://localhost:{port}/lia"
            )

            port_queue.put(port)

            loop.start()
            return

        except OSError:
            print(
                f"Port {port} in use, trying {port + 1}..."
            )
            port += 1

        except Exception as exc:
            print(
                f"Unexpected Bokeh error: {exc}"
            )
            port += 1


# ============================================================================
# Application mode selector
# ============================================================================

class ModeCard(QFrame):
    """A large, clickable "card" used on the launcher screen to pick a mode."""

    def __init__(self, emoji: str, title: str, description: str, on_click):
        super().__init__()
        self.setObjectName("ModeCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(240, 200)
        self.setFrameShape(QFrame.NoFrame)
        self._on_click = on_click

        self.setStyleSheet(
            """
            QFrame#ModeCard {
                background-color: #3c3f41;
                border: 1px solid #5c5c5c;
                border-radius: 8px;
            }
            QFrame#ModeCard:hover {
                background-color: #444444;
                border: 1px solid #007acc;
            }
            QLabel#CardEmoji { font-size: 44px; }
            QLabel#CardTitle { font-size: 17px; font-weight: 600; color: #ffffff; }
            QLabel#CardDesc { font-size: 12px; color: #b1b1b1; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)

        emoji_label = QLabel(emoji)
        emoji_label.setObjectName("CardEmoji")
        emoji_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        title_label.setAlignment(Qt.AlignCenter)

        desc_label = QLabel(description)
        desc_label.setObjectName("CardDesc")
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)

        layout.addWidget(emoji_label)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self._on_click()
        super().mouseReleaseEvent(event)


class ConnectWorker(QThread):
    """Opens the persistent SSH session to the Red Pitaya, off the GUI
    thread (the handshake + auth negotiation can take a few seconds)."""

    finished_ok = Signal(object)  # passes the connected RedPitayaSSH
    finished_error = Signal(str)

    def __init__(self, host: str, password: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.password = password

    def run(self):
        ssh = RedPitayaSSH(self.host, self.password)
        try:
            ssh.connect()
        except RedPitayaSSHError as exc:
            self.finished_error.emit(str(exc))
            return
        except Exception as exc:
            self.finished_error.emit(f"Unexpected error: {exc}")
            return

        self.finished_ok.emit(ssh)


class SwitchWorker(QThread):
    """Runs the FPGA-switch commands over an *already-connected*
    RedPitayaSSH session, off the GUI thread. Does not connect or
    disconnect -- the session is shared and kept alive across mode
    switches by ModeSelector."""

    finished_ok = Signal()
    finished_error = Signal(str)

    def __init__(self, ssh: RedPitayaSSH, mode: str, parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.mode = mode

    def run(self):
        try:
            if self.mode == "Lock-in Amplifier":
                self.ssh.switch_to_lia()
            else:
                self.ssh.switch_to_oscilloscope()
        except RedPitayaSSHError as exc:
            self.finished_error.emit(str(exc))
            return
        except Exception as exc:
            self.finished_error.emit(f"Unexpected error: {exc}")
            return

        self.finished_ok.emit()


class ModeSelector(QMainWindow):
    """
    Main application launcher window.

    Only ONE acquisition UI is opened at a time:
        - Oscilloscope
        - Lock-in Amplifier

    The Bokeh server contains both documents, but the user only opens the
    document belonging to the selected mode. Both application windows can
    hand control back to this selector (see `return_to_menu`), which fully
    tears down the previous window so the next time it is opened it starts
    from a clean, default state.
    """

    def __init__(self, bokeh_port: int):
        super().__init__()

        self.bokeh_port = bokeh_port

        self.current_window = None
        self.current_mode = None
        self._switch_worker = None
        self._connect_worker = None
        self.rp_ssh: RedPitayaSSH | None = None

        self.setWindowTitle("Red Pitaya Control Suite")
        self.setMinimumSize(560, 480)

        self._build_menu_bar()
        self._build_ui()

        # Covers every quit path (File > Exit, the window's close button
        # with nothing else open, etc.) in one place, rather than
        # duplicating the disconnect call at each of them.
        QApplication.instance().aboutToQuit.connect(self._cleanup_ssh)

    def _cleanup_ssh(self):
        if self.rp_ssh is not None:
            self.rp_ssh.disconnect()
            self.rp_ssh = None

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._quit_application)
        file_menu.addAction(exit_action)

        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(
            lambda: self.statusBar().showMessage(
                "Red Pitaya Control Suite \u2014 choose an application to launch", 4000
            )
        )
        help_menu.addAction(about_action)

    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(48, 40, 48, 32)
        outer.setSpacing(6)

        title = QLabel("Red Pitaya Control Suite")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: #ffffff;"
        )

        subtitle = QLabel("Choose an application to launch")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #b1b1b1;")

        outer.addWidget(title)
        outer.addWidget(subtitle)
        outer.addSpacing(16)

        outer.addWidget(self._build_connection_group())
        outer.addSpacing(16)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(24)

        self.osc_card = ModeCard(
            "\U0001F4C8",
            "Oscilloscope",
            "Real-time signal acquisition\nand waveform generator",
            lambda: self.open_mode("Oscilloscope"),
        )
        self.lia_card = ModeCard(
            "\U0001F39B\uFE0F",
            "Lock-in Amplifier",
            "Phase-sensitive I/Q detection\nwith live FPGA readout",
            lambda: self.open_mode("Lock-in Amplifier"),
        )

        cards_row.addWidget(self.osc_card)
        cards_row.addWidget(self.lia_card)
        outer.addLayout(cards_row)
        outer.addSpacing(20)

        self.status_label = QLabel("No application open.")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #b1b1b1; font-size: 12px;")
        outer.addWidget(self.status_label)
        outer.addStretch(1)

        self.setCentralWidget(central)

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("Red Pitaya Connection")

        form = QFormLayout(group)
        form.setSpacing(8)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("rp-f0XXXXX.local")
        form.addRow("Hostname:", self.host_edit)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.100")
        form.addRow("IPv4 address:", self.ip_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        connect_row = QHBoxLayout()

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)
        connect_row.addWidget(self.connect_button)

        self.connection_status_label = QLabel("Not connected")
        self.connection_status_label.setStyleSheet("color: #b1b1b1;")
        connect_row.addWidget(self.connection_status_label)
        connect_row.addStretch(1)

        form.addRow(connect_row)

        self.debug_override_checkbox = QCheckBox(
            "Skip SSH connection / FPGA switching (debug)"
        )
        self.debug_override_checkbox.setToolTip(
            "For local UI development without real Red Pitaya "
            "hardware attached. When checked, selecting a mode "
            "opens its window directly without touching the board, "
            "and the Connect button above is not needed."
        )
        form.addRow(self.debug_override_checkbox)

        return group

    # ------------------------------------------------------------------ #
    # Mode switching
    # ------------------------------------------------------------------ #
    def open_mode(self, mode: str):
        # If the selected application is already open, simply bring it
        # to the foreground.
        if self.current_window is not None and self.current_mode == mode:
            self.current_window.show()
            self.current_window.raise_()
            self.current_window.activateWindow()
            return

        # Never keep both application windows alive at the same time. If
        # something is already open, tear it down first (this also resets
        # it, since it is destroyed rather than merely hidden).
        if self.current_window is not None:
            self._discard_current_window()

        host = self.ip_edit.text().strip() or self.host_edit.text().strip()

        if self.debug_override_checkbox.isChecked():
            # Debug path: skip SSH/FPGA switching entirely. Still honor
            # whatever host was typed in (so the UI can be pointed at a
            # real board without touching its FPGA image), falling back
            # to the original hardcoded default if the fields are empty.
            self._launch_window(mode, host or "10.42.0.100")
            return

        if self.rp_ssh is None or not self.rp_ssh.connected:
            QMessageBox.warning(
                self,
                "Not Connected",
                "Connect to the Red Pitaya first (or check the "
                "debug override).",
            )
            return

        # Reuses the already-open SSH session: only the FPGA-switch
        # commands run here, no new connection/handshake.
        self._set_launcher_enabled(False)
        self.status_label.setText(f"Switching to {mode} \u2026")

        self._switch_worker = SwitchWorker(self.rp_ssh, mode)
        self._switch_worker.finished_ok.connect(
            lambda: self._on_switch_ok(mode, self.rp_ssh.host)
        )
        self._switch_worker.finished_error.connect(
            self._on_switch_error
        )
        self._switch_worker.start()

    def _on_switch_ok(self, mode: str, host: str):
        self._set_launcher_enabled(True)
        self.status_label.setText(
            f"Switched \u2014 launching {mode}\u2026"
        )
        self._launch_window(mode, host)

    def _on_switch_error(self, message: str):
        self._set_launcher_enabled(True)
        self.status_label.setText("Switch failed.")
        QMessageBox.critical(
            self,
            "Red Pitaya Switch Failed",
            message,
        )

    def _toggle_connection(self):
        if self.rp_ssh is not None and self.rp_ssh.connected:
            self.rp_ssh.disconnect()
            self.rp_ssh = None
            self.connect_button.setText("Connect")
            self.connection_status_label.setText("Not connected")
            self.connection_status_label.setStyleSheet(
                "color: #b1b1b1;"
            )
            return

        host = self.ip_edit.text().strip() or self.host_edit.text().strip()
        password = self.password_edit.text()

        if not host:
            QMessageBox.warning(
                self,
                "Missing Host",
                "Enter the Red Pitaya's hostname or IPv4 address.",
            )
            return

        if not password:
            QMessageBox.warning(
                self,
                "Missing Password",
                "Enter the Red Pitaya's password.",
            )
            return

        self._set_launcher_enabled(False)
        self.connection_status_label.setText(f"Connecting to {host} \u2026")
        self.connection_status_label.setStyleSheet("color: #b1b1b1;")

        self._connect_worker = ConnectWorker(host, password)
        self._connect_worker.finished_ok.connect(self._on_connect_ok)
        self._connect_worker.finished_error.connect(self._on_connect_error)
        self._connect_worker.start()

    def _on_connect_ok(self, ssh: RedPitayaSSH):
        self.rp_ssh = ssh
        self._set_launcher_enabled(True)
        self.connect_button.setText("Disconnect")
        self.connection_status_label.setText(f"Connected to {ssh.host}")
        self.connection_status_label.setStyleSheet("color: #4caf50;")

    def _on_connect_error(self, message: str):
        self.rp_ssh = None
        self._set_launcher_enabled(True)
        self.connect_button.setText("Connect")
        self.connection_status_label.setText("Connection failed.")
        self.connection_status_label.setStyleSheet("color: #e05555;")
        QMessageBox.critical(
            self,
            "Red Pitaya Connection Failed",
            message,
        )

    def _set_launcher_enabled(self, enabled: bool):
        self.osc_card.setEnabled(enabled)
        self.lia_card.setEnabled(enabled)
        self.host_edit.setEnabled(enabled)
        self.ip_edit.setEnabled(enabled)
        self.password_edit.setEnabled(enabled)
        self.debug_override_checkbox.setEnabled(enabled)
        self.connect_button.setEnabled(enabled)

    def _launch_window(self, mode: str, host: str):
        global current_rp_host

        if mode == "Oscilloscope":
            window = Oscilloscope(
                QApplication.instance(),
                rp_plot=bokeh_plot,
                url=f"http://localhost:{self.bokeh_port}/",
            )

        elif mode == "Lock-in Amplifier":
            # Read by modify_lia_doc() the moment this window's embedded
            # browser opens /lia, which happens after this assignment.
            current_rp_host = host

            window = LockInAmp(
                host=host,
                port=5001,
                plot_url=(
                    f"http://localhost:"
                    f"{self.bokeh_port}/lia"
                ),
            )

        else:
            raise RuntimeError(
                f"Unknown application mode: {mode}"
            )

        # Ensure the underlying window object is actually destroyed (not
        # just hidden) when it closes, so reopening it always starts from
        # a fresh, default state.
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.back_to_menu.connect(self.return_to_menu)

        self.current_window = window
        self.current_mode = mode

        self.current_window.show()
        self.current_window.raise_()
        self.current_window.activateWindow()

        self.status_label.setText(f"Running: {mode}")

        # The selector is only a launcher. Once an application is open,
        # hide it.
        self.hide()

    def _discard_current_window(self):
        """Close whichever application window is currently open.

        Disconnects `back_to_menu` first so closing it here doesn't also
        trigger `return_to_menu` and race with the caller's own bookkeeping.
        """
        old_window = self.current_window
        self.current_window = None
        self.current_mode = None
        try:
            old_window.back_to_menu.disconnect(self.return_to_menu)
        except (TypeError, RuntimeError):
            pass
        try:
            old_window._quit_app_on_close = False
        except AttributeError:
            pass
        try:
            old_window.close()
        except RuntimeError:
            pass

    def return_to_menu(self):
        """Called when the currently open application asks to go back.

        The application window has already reset its own state and is in
        the process of closing (and will be deleted, per WA_DeleteOnClose),
        so all that's left to do here is update our bookkeeping and show
        the launcher again.
        """
        self.current_window = None
        self.current_mode = None
        self.status_label.setText("No application open.")
        self.show_selector()

    def _quit_application(self):
        if self.current_window is not None:
            self._discard_current_window()
        QApplication.instance().quit()

    def show_selector(self):
        """Show the application selector without closing the current mode."""
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # If an application is running, closing the selector should only
        # hide the selector. The selected application remains open.
        if self.current_window is not None:
            self.hide()
            event.ignore()
            return

        event.accept()
        # We disabled quitOnLastWindowClosed (see main()) so that closing
        # an application window while the selector is hidden doesn't kill
        # the whole app. That means closing the selector itself when no
        # application is open must quit explicitly, or nothing is left to
        # tell Qt the app should exit.
        QApplication.instance().quit()


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    # PySide's C++ event loop doesn't return control to the Python
    # interpreter between events, so Python's SIGINT handler (which raises
    # KeyboardInterrupt) may never actually run and Ctrl+C in the terminal
    # appears to do nothing. Restoring the OS default handler makes Ctrl+C
    # terminate the process immediately, no matter what Qt is doing.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    port_queue = Queue()

    bokeh_thread = Thread(
        target=start_bokeh_server,
        args=(port_queue,),
        daemon=True,
    )
    bokeh_thread.start()

    # Do not create the Qt UI until Bokeh is actually listening.
    port = port_queue.get()

    app = QApplication(sys.argv)
    app.setStyleSheet(
        pyside6_simple_dark_theme
    )
    # Life-cycle is managed explicitly by ModeSelector (it may briefly have
    # zero visible windows while swapping between the launcher and an
    # application window), so don't let Qt quit on our behalf.
    app.setQuitOnLastWindowClosed(False)

    selector = ModeSelector(
        bokeh_port=port
    )
    selector.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())