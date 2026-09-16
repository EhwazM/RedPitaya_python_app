from __future__ import annotations

import sys
from queue import Queue
from threading import Thread

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
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
    update_time=50,
    roll_over=1000,
)


def modify_oscilloscope_doc(doc):
    bokeh_plot.attach_doc(doc)


def modify_lia_doc(doc):
    # IMPORTANT: Bokeh Model objects cannot be shared between Documents.
    # Create a completely new plot/model tree for every /lia session.
    lia_plot = LIABokehPlot(
        host="10.42.0.100",
        port=5001,
        update_time=50,
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

class ModeSelector(QMainWindow):
    """
    Main application window.

    Only ONE acquisition UI is opened at a time:
        - Oscilloscope
        - Lock-in Amplifier

    The Bokeh server contains both documents, but the user only opens the
    document belonging to the selected mode.
    """

    def __init__(self, bokeh_port: int):
        super().__init__()

        self.bokeh_port = bokeh_port

        self.current_window = None
        self.current_mode = None

        self.setWindowTitle("Red Pitaya")
        self.setMinimumWidth(420)
        self.setMinimumHeight(180)

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        title = QLabel("Red Pitaya")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size: 22px; font-weight: bold;"
        )

        subtitle = QLabel("Select application")
        subtitle.setAlignment(Qt.AlignCenter)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(
            [
                "Oscilloscope",
                "Lock-in Amplifier",
            ]
        )

        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(
            self.open_selected_mode
        )

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        layout.addWidget(self.mode_combo)
        layout.addWidget(self.open_button)

        self.status_label = QLabel(
            "No application open."
        )
        self.status_label.setAlignment(
            Qt.AlignCenter
        )
        layout.addWidget(self.status_label)

        self.setCentralWidget(central)

    def open_selected_mode(self):
        mode = self.mode_combo.currentText()

        # If the selected application is already open, simply bring it
        # to the foreground.
        if (
            self.current_window is not None
            and self.current_mode == mode
        ):
            self.current_window.show()
            self.current_window.raise_()
            self.current_window.activateWindow()
            return

        # Never keep both application windows alive at the same time.
        self.close_current_mode()

        if mode == "Oscilloscope":
            self.current_window = Oscilloscope(
                QApplication.instance(),
                rp_plot=bokeh_plot,
                url=f"http://localhost:{self.bokeh_port}/",
            )

        elif mode == "Lock-in Amplifier":
            self.current_window = LockInAmp(
                host="10.42.0.100",
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

        self.current_mode = mode

        # If the existing classes provide their own closeEvent, keep the
        # selected window as a normal top-level application window.
        self.current_window.show()
        self.current_window.raise_()
        self.current_window.activateWindow()

        self.status_label.setText(
            f"Running: {mode}"
        )

        # The selector is only a launcher. Once an application is open,
        # hide/close the selector window.
        self.hide()

    def _quit_application(self):
        if self.current_window is not None:
            try:
                self.current_window.close()
            except RuntimeError:
                pass

            self.current_window = None
            self.current_mode = None

        QApplication.instance().quit()

    def show_selector(self):
        """Show the application selector without closing the current mode."""
        self.show()
        self.raise_()
        self.activateWindow()

    def close_current_mode(self):
        if self.current_window is not None:
            try:
                self.current_window.close()
            except RuntimeError:
                pass

            self.current_window = None
            self.current_mode = None

            self.status_label.setText(
                "No application open."
            )

    def closeEvent(self, event):
        # If an application is running, closing the selector should only
        # hide the selector. The selected application remains open.
        if self.current_window is not None:
            self.hide()
            event.ignore()
            return

        event.accept()


# ============================================================================
# Main
# ============================================================================

def main() -> int:
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

    selector = ModeSelector(
        bokeh_port=port
    )
    selector.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())