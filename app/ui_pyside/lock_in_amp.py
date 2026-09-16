from __future__ import annotations

import sys
import numpy as np

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from assets.dark_theme import pyside6_simple_dark_theme
from app.rp_lia.redpitaya_lia import RedPitayaLIA


class LockInAmp(QMainWindow):
    """
    PySide6 interface for the FPGA Lock-In Amplifier.

    The GUI talks only to RedPitayaLIA. It does not use the oscilloscope
    acquisition code or the standard Red Pitaya SCPI acquisition API.
    """

    def __init__(
        self,
        host: str = "10.42.0.100",
        port: int = 5001,
        plot_url: str = "http://localhost:5006/",
    ):
        super().__init__()

        self.setWindowTitle("Red Pitaya Lock-In Amplifier")

        # --------------------------------------------------------------
        # Local UI state
        # --------------------------------------------------------------

        self.host = host
        self.port = port

        self.frequency = 1000.0
        self.phase_offset = 0.0
        self.k_value = 17
        self.n_stages = 2
        self.noise = 0

        # --------------------------------------------------------------
        # LIA communication client
        # --------------------------------------------------------------

        self.lia = RedPitayaLIA(
            host=self.host,
            port=self.port,
        )

        # --------------------------------------------------------------
        # I/Q polling timer
        # --------------------------------------------------------------

        self.iq_timer = QTimer(self)
        self.iq_timer.timeout.connect(
            self.poll_iq
        )

        # --------------------------------------------------------------
        # Bokeh URL
        # --------------------------------------------------------------

        self.plot_url = plot_url

        # --------------------------------------------------------------
        # Build UI
        # --------------------------------------------------------------

        self.init_ui()
        self.create_menu_bar()

        # --------------------------------------------------------------
        # Connect to FPGA
        # --------------------------------------------------------------

        self.connect_redpitaya()

    # ==================================================================
    # Red Pitaya connection
    # ==================================================================

    def connect_redpitaya(self) -> None:
        try:
            self.lia.connect()

            # Synchronize FPGA with values shown in GUI.
            self.lia.configure(
                frequency_hz=self.frequency,
                phase_offset_rad=self.phase_offset,
                k=self.k_value,
                n_stages=self.n_stages,
                noise=self.noise,
            )

            self.statusBar().showMessage(
                f"Connected to LIA at "
                f"{self.host}:{self.port}"
            )

            # Start live I/Q acquisition.
            if self.iq_poll_checkbox.isChecked():
                self.iq_timer.start(
                    self.iq_poll_interval_spinbox.value()
                )

        except Exception as exc:
            self.statusBar().showMessage(
                f"LIA connection failed: {exc}"
            )

    def closeEvent(self, event) -> None:
        self.iq_timer.stop()
        self.lia.disconnect()
        event.accept()

    # ==================================================================
    # UI
    # ==================================================================

    def init_ui(self) -> None:
        central = QWidget()
        central.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        main_layout = QHBoxLayout(central)

        main_layout.setContentsMargins(
            8,
            8,
            8,
            8,
        )

        main_layout.setSpacing(10)

        # ==============================================================
        # LEFT SIDEBAR
        # ==============================================================

        sidebar = QVBoxLayout()
        sidebar.setSpacing(15)

        sidebar.addWidget(
            self.build_lia_settings_group()
        )

        sidebar.addWidget(
            self.build_iq_values_group()
        )

        sidebar.addStretch(1)

        sidebar.addLayout(
            self.build_logo_row()
        )

        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)

        sidebar_widget.setMinimumWidth(280)
        sidebar_widget.setMaximumWidth(350)

        # ==============================================================
        # RIGHT PLOT AREA
        # ==============================================================

        plot_container = QWidget()
        plot_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        plot_layout = QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(0)

        # Plot title
        plot_title = QLabel(
            "Lock-In Amplifier Output"
        )

        plot_title.setStyleSheet(
            "font-size: 16px;"
            "font-weight: bold;"
            "padding: 5px;"
        )

        plot_layout.addWidget(
            plot_title
        )

        # Bokeh browser
        self.browser = QWebEngineView()
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.browser.setUrl(
            QUrl(self.plot_url)
        )

        plot_layout.addWidget(
            self.browser,
            1,
        )

        # ==============================================================
        # MAIN WINDOW LAYOUT
        # ==============================================================

        main_layout.addWidget(
            sidebar_widget,
            stretch=0,
        )

        main_layout.addWidget(
            plot_container,
            stretch=1,
        )

        self.setCentralWidget(
            central
        )

    # ==================================================================
    # LIA settings
    # ==================================================================

    def build_lia_settings_group(self) -> QGroupBox:
        group = QGroupBox(
            "LIA Settings"
        )

        form = QFormLayout(group)

        # Frequency
        self.frequency_spinbox = QDoubleSpinBox()

        self.frequency_spinbox.setRange(
            0.0,
            RedPitayaLIA.CLK_HZ / 2,
        )

        self.frequency_spinbox.setDecimals(
            2
        )

        self.frequency_spinbox.setSingleStep(
            10.0
        )

        self.frequency_spinbox.setSuffix(
            " Hz"
        )

        self.frequency_spinbox.setValue(
            self.frequency
        )

        self.frequency_spinbox.valueChanged.connect(
            self.on_frequency_changed
        )

        # Phase
        self.phase_offset_spinbox = QDoubleSpinBox()

        self.phase_offset_spinbox.setRange(
            -np.pi,
            np.pi,
        )

        self.phase_offset_spinbox.setDecimals(
            4
        )

        self.phase_offset_spinbox.setSingleStep(
            0.01
        )

        self.phase_offset_spinbox.setSuffix(
            " rad"
        )

        self.phase_offset_spinbox.setValue(
            self.phase_offset
        )

        self.phase_offset_spinbox.valueChanged.connect(
            self.on_phase_offset_changed
        )

        # K
        self.k_spinbox = QSpinBox()

        self.k_spinbox.setRange(
            RedPitayaLIA.MIN_K,
            RedPitayaLIA.MAX_K,
        )

        self.k_spinbox.setValue(
            self.k_value
        )

        self.k_spinbox.valueChanged.connect(
            self.on_k_changed
        )

        # IIR stages
        self.stages_spinbox = QSpinBox()

        self.stages_spinbox.setRange(
            RedPitayaLIA.MIN_STAGES,
            RedPitayaLIA.MAX_STAGES,
        )

        self.stages_spinbox.setValue(
            self.n_stages
        )

        self.stages_spinbox.valueChanged.connect(
            self.on_iir_stages_changed
        )

        # noise config
        self.noise_spinbox = QSpinBox()

        self.noise_spinbox.setRange(
            RedPitayaLIA.MIN_NOISE,
            RedPitayaLIA.MAX_NOISE,
        )

        self.noise_spinbox.setValue(
            self.noise
        )

        self.noise_spinbox.valueChanged.connect(
            self.on_noise_changed
        )
        
        # Add controls
        form.addRow(
            QLabel("Frequency:"),
            self.frequency_spinbox,
        )

        form.addRow(
            QLabel("Phase Offset:"),
            self.phase_offset_spinbox,
        )

        form.addRow(
            QLabel("K:"),
            self.k_spinbox,
        )

        form.addRow(
            QLabel("IIR Stages:"),
            self.stages_spinbox,
        )

        form.addRow(
            QLabel("Noise:"),
            self.noise_spinbox,
        )

        return group

    # ==================================================================
    # I/Q display
    # ==================================================================

    def build_iq_values_group(self) -> QGroupBox:
        group = QGroupBox(
            "LIA Output"
        )

        layout = QVBoxLayout(group)

        self.i_value_label = QLabel(
            "I: 0"
        )

        self.q_value_label = QLabel(
            "Q: 0"
        )

        self.r_value_label = QLabel(
            "R: 0"
        )

        self.phase_value_label = QLabel(
            "Phase: 0.00°"
        )

        for label in (
            self.i_value_label,
            self.q_value_label,
            self.r_value_label,
            self.phase_value_label,
        ):
            label.setStyleSheet(
                "font-family: monospace;"
                "font-size: 13px;"
            )

            layout.addWidget(
                label
            )

        # --------------------------------------------------------------
        # Live polling
        # --------------------------------------------------------------

        poll_row = QHBoxLayout()

        self.iq_poll_checkbox = QCheckBox(
            "Live update"
        )

        self.iq_poll_checkbox.setChecked(
            True
        )

        self.iq_poll_checkbox.toggled.connect(
            self.on_poll_toggled
        )

        self.iq_poll_interval_spinbox = QSpinBox()

        self.iq_poll_interval_spinbox.setRange(
            10,
            5000,
        )

        self.iq_poll_interval_spinbox.setSingleStep(
            10
        )

        self.iq_poll_interval_spinbox.setValue(
            50
        )

        self.iq_poll_interval_spinbox.setSuffix(
            " ms"
        )

        self.iq_poll_interval_spinbox.valueChanged.connect(
            self.on_poll_interval_changed
        )

        poll_row.addWidget(
            self.iq_poll_checkbox
        )

        poll_row.addWidget(
            self.iq_poll_interval_spinbox
        )

        layout.addLayout(
            poll_row
        )

        return group

    # ==================================================================
    # Logos
    # ==================================================================

    def build_logo_row(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        self.uni_logo = QLabel()

        self.uni_logo.setPixmap(
            QPixmap(
                "assets/ua.png"
            ).scaledToWidth(
                75
            )
        )

        self.group_logo = QLabel()

        self.group_logo.setPixmap(
            QPixmap(
                "assets/geoel.png"
            ).scaledToWidth(
                100
            )
        )

        layout.addWidget(
            self.uni_logo
        )

        layout.addWidget(
            self.group_logo
        )

        return layout

    # ==================================================================
    # UI -> FPGA
    # ==================================================================

    def on_frequency_changed(
        self,
        frequency: float,
    ) -> None:

        self.frequency = frequency

        if not self.lia.connected:
            return

        try:
            self.lia.set_frequency(
                frequency
            )

            self.statusBar().showMessage(
                f"Frequency: {frequency:.2f} Hz"
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"Frequency error: {exc}"
            )

    def on_phase_offset_changed(
        self,
        phase_offset: float,
    ) -> None:

        self.phase_offset = phase_offset

        if not self.lia.connected:
            return

        try:
            self.lia.set_phase_offset(
                phase_offset
            )

            self.statusBar().showMessage(
                f"Phase offset: "
                f"{phase_offset:.4f} rad"
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"Phase error: {exc}"
            )

    def on_k_changed(
        self,
        k: int,
    ) -> None:

        self.k_value = k

        if not self.lia.connected:
            return

        try:
            self.lia.set_k(
                k
            )

            self.statusBar().showMessage(
                f"IIR K: {k}"
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"K error: {exc}"
            )

    def on_iir_stages_changed(
        self,
        stages: int,
    ) -> None:

        self.n_stages = stages

        if not self.lia.connected:
            return

        try:
            self.lia.set_n_stages(
                stages
            )

            self.statusBar().showMessage(
                f"IIR stages: {stages}"
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"IIR stage error: {exc}"
            )

    def on_noise_changed(
        self,
        noise: int,
    ) -> None:

        self.noise = noise

        if not self.lia.connected:
            return

        try:
            self.lia.set_noise(
                noise
            )

            self.statusBar().showMessage(
                f"Noise level: {noise}"
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"Noise level error: {exc}"
            )

    # ==================================================================
    # FPGA -> UI
    # ==================================================================

    def poll_iq(self) -> None:

        if not self.lia.connected:
            return

        try:
            measurement = (
                self.lia.get_measurement()
            )

            self.update_iq_values(
                measurement["I"],
                measurement["Q"],
                measurement["R"],
                measurement["phase"],
            )

        except Exception as exc:
            self.statusBar().showMessage(
                f"I/Q read error: {exc}"
            )

    def update_iq_values(
        self,
        i_value: float,
        q_value: float,
        r_value: float | None = None,
        phase_value: float | None = None,
    ) -> None:

        if (
            r_value is None
            or phase_value is None
        ):
            r_value, phase_value = (
                self.lia.calculate_r_phase(
                    i_value,
                    q_value,
                )
            )

        self.i_value_label.setText(
            f"I: {i_value:.2f}"
        )

        self.q_value_label.setText(
            f"Q: {q_value:.2f}"
        )

        self.r_value_label.setText(
            f"R: {r_value:.2f}"
        )

        self.phase_value_label.setText(
            f"Phase: "
            f"{np.degrees(phase_value):.2f}°"
        )

    # ==================================================================
    # Live polling
    # ==================================================================

    def on_poll_toggled(
        self,
        enabled: bool,
    ) -> None:

        if (
            enabled
            and self.lia.connected
        ):
            self.iq_timer.start(
                self.iq_poll_interval_spinbox.value()
            )

        else:
            self.iq_timer.stop()

    def on_poll_interval_changed(
        self,
        interval_ms: int,
    ) -> None:

        if self.iq_timer.isActive():
            self.iq_timer.start(
                interval_ms
            )

    # ==================================================================
    # Menu
    # ==================================================================

    def create_menu_bar(self) -> None:

        menu_bar = self.menuBar()

        # File
        file_menu = menu_bar.addMenu(
            "File"
        )

        exit_action = QAction(
            "Exit",
            self,
        )

        exit_action.triggered.connect(
            self.close
        )

        file_menu.addAction(
            exit_action
        )

        # Empty menus
        menu_bar.addMenu(
            "Edit"
        )

        menu_bar.addMenu(
            "Tools"
        )

        # Help
        help_menu = menu_bar.addMenu(
            "Help"
        )

        about_action = QAction(
            "About",
            self,
        )

        about_action.triggered.connect(
            lambda: self.statusBar().showMessage(
                "Red Pitaya Lock-In Amplifier"
            )
        )

        help_menu.addAction(
            about_action
        )


# ======================================================================
# Main
# ======================================================================

def main() -> int:

    app = QApplication(
        sys.argv
    )

    app.setStyleSheet(
        pyside6_simple_dark_theme
    )

    window = LockInAmp(
        host="10.42.0.100",
        port=5001,
        plot_url="http://localhost:5006/",
    )

    window.resize(
        1200,
        700,
    )

    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )