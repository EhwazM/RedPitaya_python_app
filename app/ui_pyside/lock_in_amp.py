from __future__ import annotations
import csv
import sys
import time
import numpy as np
from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
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
    back_to_menu = Signal()

    def __init__(
        self,
        host: str = "10.42.0.100",
        port: int = 5001,
        plot_url: str = "http://localhost:5006/",
    ):
        super().__init__()
        self.setWindowTitle("Red Pitaya Lock-In Amplifier")
        self.host = host
        self.port = port
        self.frequency = 1000.0
        self.phase_offset = 0.0
        self.k_value = 17
        self.n_stages = 2
        self.noise = 0
        self.ext_ref = False
        self.zc_threshold = 0
        self._quit_app_on_close = False
        self.record_start_time = time.time()
        self.recorded_data: dict[str, list[float]] = {
            "time": [],
            "I": [],
            "Q": [],
            "R": [],
            "phase": [],
        }
        self.lia = RedPitayaLIA(
            host=self.host,
            port=self.port,
        )
        self.iq_timer = QTimer(self)
        self.iq_timer.timeout.connect(self.poll_iq)
        self.plot_url = plot_url
        self.init_ui()
        self.create_menu_bar()
        self.connect_redpitaya()

    def connect_redpitaya(self) -> None:
        try:
            self.lia.connect()
            self.lia.configure(
                frequency_hz=self.frequency,
                phase_offset_rad=self.phase_offset,
                k=self.k_value,
                n_stages=self.n_stages,
                noise=self.noise,
                ext_ref=self.ext_ref,
                zc_threshold=self.zc_threshold,
            )
            self.statusBar().showMessage(f"Connected to LIA at {self.host}:{self.port}")
            if self.iq_poll_checkbox.isChecked():
                self.iq_timer.start(self.iq_poll_interval_spinbox.value())
        except Exception as exc:
            self.statusBar().showMessage(f"LIA connection failed: {exc}")

    def closeEvent(self, event) -> None:
        self.iq_timer.stop()
        if self.lia.connected:
            try:
                self.lia.soft_reset()
            except Exception:
                pass
            self.lia.disconnect()
        event.accept()
        if self._quit_app_on_close:
            QApplication.instance().quit()
        else:
            self.back_to_menu.emit()

    def go_back_to_menu(self) -> None:
        """Close this window and return to the application selector."""
        self._quit_app_on_close = False
        self.close()

    def exit_application(self) -> None:
        """Close this window and quit the whole application."""
        self._quit_app_on_close = True
        self.close()

    def init_ui(self) -> None:
        # ------------------------------------------------------------------
        # UI construction
        # ------------------------------------------------------------------

        central = QWidget()
        central.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(10)
        # ------------------------------------------------------------------
        # Left sidebar
        # ------------------------------------------------------------------

        sidebar = QVBoxLayout()
        sidebar.setSpacing(15)
        sidebar.addWidget(self.build_lia_settings_group())
        sidebar.addWidget(self.build_iq_values_group())
        sidebar.addWidget(self.build_plot_options_group())
        sidebar.addStretch(1)
        sidebar.addLayout(self.build_logo_row())
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setMinimumWidth(280)
        sidebar_widget.setMaximumWidth(350)
        # ------------------------------------------------------------------
        # Plot area
        # ------------------------------------------------------------------

        plot_container = QWidget()
        plot_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        plot_layout = QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(0)
        plot_title = QLabel("Lock-In Amplifier Output")
        plot_title.setStyleSheet("font-size: 16px;font-weight: bold;padding: 5px;")
        plot_layout.addWidget(plot_title)
        self.browser = QWebEngineView()
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.browser.setUrl(QUrl(self.plot_url))
        plot_layout.addWidget(self.browser, 1)
        main_layout.addWidget(sidebar_widget, stretch=0)
        main_layout.addWidget(plot_container, stretch=1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    # LIA settings
    # ------------------------------------------------------------------

    def build_lia_settings_group(self) -> QGroupBox:
        group = QGroupBox("LIA Settings")
        form = QFormLayout(group)
        self.frequency_spinbox = QDoubleSpinBox()
        self.frequency_spinbox.setRange(0.0, RedPitayaLIA.CLK_HZ / 2)
        self.frequency_spinbox.setDecimals(2)
        self.frequency_spinbox.setSingleStep(10.0)
        self.frequency_spinbox.setSuffix(" Hz")
        self.frequency_spinbox.setValue(self.frequency)
        self.frequency_spinbox.valueChanged.connect(self.on_frequency_changed)
        self.phase_offset_spinbox = QDoubleSpinBox()
        self.phase_offset_spinbox.setRange(-np.pi, np.pi)
        self.phase_offset_spinbox.setDecimals(4)
        self.phase_offset_spinbox.setSingleStep(0.01)
        self.phase_offset_spinbox.setSuffix(" rad")
        self.phase_offset_spinbox.setValue(self.phase_offset)
        self.phase_offset_spinbox.valueChanged.connect(self.on_phase_offset_changed)
        self.k_spinbox = QSpinBox()
        self.k_spinbox.setRange(RedPitayaLIA.MIN_K, RedPitayaLIA.MAX_K)
        self.k_spinbox.setValue(self.k_value)
        self.k_spinbox.valueChanged.connect(self.on_k_changed)
        self.stages_spinbox = QSpinBox()
        self.stages_spinbox.setRange(RedPitayaLIA.MIN_STAGES, RedPitayaLIA.MAX_STAGES)
        self.stages_spinbox.setValue(self.n_stages)
        self.stages_spinbox.valueChanged.connect(self.on_iir_stages_changed)
        self.noise_spinbox = QSpinBox()
        self.noise_spinbox.setRange(RedPitayaLIA.MIN_NOISE, RedPitayaLIA.MAX_NOISE)
        self.noise_spinbox.setValue(self.noise)
        self.noise_spinbox.valueChanged.connect(self.on_noise_changed)
        # External reference (IN2)
        self.ext_ref_checkbox = QCheckBox(
            "Use IN2 as external reference"
        )
        self.ext_ref_checkbox.setChecked(self.ext_ref)
        self.ext_ref_checkbox.toggled.connect(self.on_ext_ref_changed)
        self.zc_threshold_spinbox = QSpinBox()
        self.zc_threshold_spinbox.setRange(RedPitayaLIA.MIN_ZC_THRESH, RedPitayaLIA.MAX_ZC_THRESH)
        self.zc_threshold_spinbox.setValue(self.zc_threshold)
        self.zc_threshold_spinbox.setSingleStep(1)
        self.zc_threshold_spinbox.valueChanged.connect(self.on_zc_threshold_changed)
        self.in2_period_label = QLabel("IN2 Period: 0 cycles")
        self.in2_frequency_label = QLabel("IN2 Frequency: 0.00 Hz")
        for label in (self.in2_period_label, self.in2_frequency_label):
            label.setStyleSheet("font-family: monospace;font-size: 12px;")
        form.addRow(self.ext_ref_checkbox)
        form.addRow(QLabel('ZC Threshold:'), self.zc_threshold_spinbox)
        form.addRow(self.in2_period_label)
        form.addRow(self.in2_frequency_label)
        form.addRow(QLabel('Frequency:'), self.frequency_spinbox)
        form.addRow(QLabel('Phase Offset:'), self.phase_offset_spinbox)
        form.addRow(QLabel('K:'), self.k_spinbox)
        form.addRow(QLabel('IIR Stages:'), self.stages_spinbox)
        form.addRow(QLabel('Noise:'), self.noise_spinbox)
        return group

    # ------------------------------------------------------------------
    # I/Q display
    # ------------------------------------------------------------------

    def build_iq_values_group(self) -> QGroupBox:
        group = QGroupBox("LIA Output")
        layout = QVBoxLayout(group)
        self.readings_tabs = QTabWidget()
        counts_tab = QWidget()
        counts_layout = QVBoxLayout(counts_tab)
        self.i_value_label = QLabel("I: 0")
        self.q_value_label = QLabel("Q: 0")
        self.r_value_label = QLabel("R: 0")
        self.phase_value_label = QLabel("Phase: 0.00°")
        for label in (self.i_value_label, self.q_value_label, self.r_value_label, self.phase_value_label):
            label.setStyleSheet("font-family: monospace;font-size: 13px;")
            counts_layout.addWidget(label)
        counts_layout.addStretch(1)
        self.readings_tabs.addTab(counts_tab, "Counts")
        volts_tab = QWidget()
        volts_layout = QVBoxLayout(volts_tab)
        self.i_volts_label = QLabel("I: 0.0000 V")
        self.q_volts_label = QLabel("Q: 0.0000 V")
        self.r_volts_label = QLabel("R: 0.0000 V")
        self.phase_volts_label = QLabel("Phase: 0.00°")
        for label in (self.i_volts_label, self.q_volts_label, self.r_volts_label, self.phase_volts_label):
            label.setStyleSheet("font-family: monospace;font-size: 13px;")
            volts_layout.addWidget(label)
        volts_layout.addStretch(1)
        self.readings_tabs.addTab(volts_tab, "Voltage")
        layout.addWidget(self.readings_tabs)
        poll_row = QHBoxLayout()
        self.iq_poll_checkbox = QCheckBox("Live update")
        self.iq_poll_checkbox.setChecked(True)
        self.iq_poll_checkbox.toggled.connect(self.on_poll_toggled)
        self.iq_poll_interval_spinbox = QSpinBox()
        self.iq_poll_interval_spinbox.setRange(10, 5000)
        self.iq_poll_interval_spinbox.setSingleStep(10)
        self.iq_poll_interval_spinbox.setValue(500)
        self.iq_poll_interval_spinbox.setSuffix(" ms")
        self.iq_poll_interval_spinbox.valueChanged.connect(self.on_poll_interval_changed)
        poll_row.addWidget(self.iq_poll_checkbox)
        poll_row.addWidget(self.iq_poll_interval_spinbox)
        layout.addLayout(poll_row)
        return group

    # ------------------------------------------------------------------
    # Plot options
    # ------------------------------------------------------------------

    def build_plot_options_group(self) -> QGroupBox:
        group = QGroupBox("Plot Options")
        layout = QHBoxLayout(group)
        self.clear_plot_button = QPushButton("Clear Plot")
        self.clear_plot_button.clicked.connect(self.on_clear_plot)
        self.save_csv_button = QPushButton("Save CSV")
        self.save_csv_button.clicked.connect(self.on_save_csv)
        layout.addWidget(self.clear_plot_button)
        layout.addWidget(self.save_csv_button)
        return group

    # ------------------------------------------------------------------
    # Logos
    # ------------------------------------------------------------------

    def build_logo_row(self) -> QHBoxLayout:

        layout = QHBoxLayout()

        logo_frame = QFrame()
        logo_frame.setStyleSheet("""
            QFrame {
                background-color: #6b6b6b;
                border: 1px solid #858585;
                border-radius: 12px;
            }

            QLabel {
                border: none;
                background: transparent;
            }
        """)

        logo_layout = QHBoxLayout(logo_frame)
        logo_layout.setContentsMargins(10, 8, 10, 8)
        logo_layout.setSpacing(8)

        self.uni_logo = QLabel()
        self.uni_logo.setPixmap(
            QPixmap('assets/ua.png').scaledToWidth(75)
        )
        self.uni_logo.setAlignment(Qt.AlignCenter)

        self.group_logo = QLabel()
        self.group_logo.setPixmap(
            QPixmap('assets/geoel.png').scaledToWidth(100)
        )
        self.group_logo.setAlignment(Qt.AlignCenter)

        logo_layout.addWidget(self.uni_logo, 1)
        logo_layout.addWidget(self.group_logo, 1)

        layout.addWidget(logo_frame)

        return layout
    
    # ------------------------------------------------------------------
    # UI -> FPGA
    # ------------------------------------------------------------------

    def _update_frequency_control_state(self) -> None:
        """Enable manual frequency only when internal DDS mode is selected."""
        if hasattr(self, 'frequency_spinbox'):
            self.frequency_spinbox.setEnabled(not self.ext_ref)

    def on_ext_ref_changed(self, enabled: bool) -> None:
        self.ext_ref = bool(enabled)
        self._update_frequency_control_state()
        if not self.lia.connected:
            return
        try:
            self.lia.set_ext_ref(self.ext_ref)
            if self.ext_ref:
                self.statusBar().showMessage("External reference enabled: IN2 controls DDS frequency/phase")
            else:
                self.statusBar().showMessage("External reference disabled: internal DDS frequency active")
        except Exception as exc:
            self.statusBar().showMessage(f"External reference error: {exc}")

    def on_zc_threshold_changed(self, threshold: int) -> None:
        self.zc_threshold = int(threshold)
        if not self.lia.connected:
            return
        try:
            self.lia.set_zc_threshold(self.zc_threshold)
            self.statusBar().showMessage(f"ZC threshold: {self.zc_threshold}")
        except Exception as exc:
            self.statusBar().showMessage(f"ZC threshold error: {exc}")

    def on_frequency_changed(self, frequency: float) -> None:
        self.frequency = frequency
        if self.ext_ref:
            return
        if not self.lia.connected:
            return
        try:
            self.lia.set_frequency(frequency)
            self.statusBar().showMessage(f"Frequency: {frequency:.2f} Hz")
        except Exception as exc:
            self.statusBar().showMessage(f"Frequency error: {exc}")

    def on_phase_offset_changed(self, phase_offset: float) -> None:
        self.phase_offset = phase_offset
        if not self.lia.connected:
            return
        try:
            self.lia.set_phase_offset(phase_offset)
            self.statusBar().showMessage(f"Phase offset: {phase_offset:.4f} rad")
        except Exception as exc:
            self.statusBar().showMessage(f"Phase error: {exc}")

    def on_k_changed(self, k: int) -> None:
        self.k_value = k
        if not self.lia.connected:
            return
        try:
            self.lia.set_k(k)
            self.statusBar().showMessage(f"IIR K: {k}")
        except Exception as exc:
            self.statusBar().showMessage(f"K error: {exc}")

    def on_iir_stages_changed(self, stages: int) -> None:
        self.n_stages = stages
        if not self.lia.connected:
            return
        try:
            self.lia.set_n_stages(stages)
            self.statusBar().showMessage(f"IIR stages: {stages}")
        except Exception as exc:
            self.statusBar().showMessage(f"IIR stage error: {exc}")

    def on_noise_changed(self, noise: int) -> None:
        self.noise = noise
        if not self.lia.connected:
            return
        try:
            self.lia.set_noise(noise)
            self.statusBar().showMessage(f"Noise level: {noise}")
        except Exception as exc:
            self.statusBar().showMessage(f"Noise level error: {exc}")

    # ------------------------------------------------------------------
    # FPGA -> UI
    # ------------------------------------------------------------------

    def poll_iq(self) -> None:
        if not self.lia.connected:
            return
        try:
            measurement = self.lia.get_measurement()
            self.recorded_data['time'].append(time.time() - self.record_start_time)
            self.recorded_data['I'].append(measurement['I'])
            self.recorded_data['Q'].append(measurement['Q'])
            self.recorded_data['R'].append(measurement['R'])
            self.recorded_data['phase'].append(measurement['phase'])
            self.update_iq_values(
                measurement["I"],
                measurement["Q"],
                measurement["R"],
                measurement["phase"],
            )
            period = self.lia.get_period()
            external_frequency = self.lia.period_to_frequency(period)
            self.in2_period_label.setText(f"IN2 Period: {period} cycles")
            self.in2_frequency_label.setText(f"IN2 Frequency: {external_frequency:.2f} Hz")
            if self.ext_ref and period > 0:
                self.frequency_spinbox.blockSignals(True)
                self.frequency_spinbox.setValue(external_frequency)
                self.frequency_spinbox.blockSignals(False)
        except Exception as exc:
            self.statusBar().showMessage(f"I/Q read error: {exc}")

    def update_iq_values(
        self,
        i_value: float,
        q_value: float,
        r_value: float | None = None,
        phase_value: float | None = None,
    ) -> None:
        if r_value is None or phase_value is None:
            r_value, phase_value = self.lia.calculate_r_phase(i_value, q_value)
        self.i_value_label.setText(f"I: {i_value:.2f}")
        self.q_value_label.setText(f"Q: {q_value:.2f}")
        self.r_value_label.setText(f"R: {r_value:.2f}")
        self.phase_value_label.setText(f"Phase: {np.degrees(phase_value):.2f}°")
        try:
            i_volts = self.lia.raw_to_volts(i_value)
            q_volts = self.lia.raw_to_volts(q_value)
            r_volts = self.lia.raw_to_volts(r_value)
        except RuntimeError:
            return
        self.i_volts_label.setText(f"I: {i_volts:.4f} V")
        self.q_volts_label.setText(f"Q: {q_volts:.4f} V")
        self.r_volts_label.setText(f"R: {r_volts:.4f} V")
        self.phase_volts_label.setText(f"Phase: {np.degrees(phase_value):.2f}°")

    # ------------------------------------------------------------------
    # Live polling
    # ------------------------------------------------------------------

    def on_poll_toggled(self, enabled: bool) -> None:
        if enabled and self.lia.connected:
            self.iq_timer.start(self.iq_poll_interval_spinbox.value())
        else:
            self.iq_timer.stop()

    def on_poll_interval_changed(self, interval_ms: int) -> None:
        if self.iq_timer.isActive():
            self.iq_timer.start(interval_ms)

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def create_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        back_action = QAction('◀ Main Menu', self)
        back_action.setStatusTip("Return to the Red Pitaya application selector")
        back_action.triggered.connect(self.go_back_to_menu)
        menu_bar.addAction(back_action)
        file_menu = menu_bar.addMenu("File")
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(self.exit_application)
        file_menu.addAction(exit_action)
        menu_bar.addMenu("Edit")
        tools_menu = menu_bar.addMenu("Tools")
        tools_back_action = QAction('Back to Main Menu', self)
        tools_back_action.triggered.connect(self.go_back_to_menu)
        tools_menu.addAction(tools_back_action)
        help_menu = menu_bar.addMenu("Help")
        about_action = QAction('About', self)
        about_action.triggered.connect(lambda: self.statusBar().showMessage('Red Pitaya Lock-In Amplifier'))
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def on_clear_plot(self) -> None:
        """Clear recorded I/Q history and the live plot, in place.

        The Bokeh plot runs in a separate server process, so there is

        no direct Python handle to its LIABokehPlot instance here.

        Rather than reloading the embedded page (which would drop the

        session and briefly interrupt streaming), this "clicks" a

        hidden Bokeh Button already wired to that instance's

        clear_data() via a normal server round-trip, so the plot

        keeps running the whole time. The local recording buffer used

        for CSV export is cleared at the same moment so the two stay

        in sync.

        """
        self.recorded_data = {'time': [], 'I': [], 'Q': [], 'R': [], 'phase': []}
        self.record_start_time = time.time()
        js_code = """
        (function() {
            function findClearButton(root) {
                if (!root) { return null; }

                var buttons = root.querySelectorAll
                    ? root.querySelectorAll("button")
                    : [];

                for (var i = 0; i < buttons.length; i++) {
                    if (buttons[i].textContent.trim() === 'ClearTrigger') {
                        return buttons[i];
                    }
                }

                var all = root.querySelectorAll
                    ? root.querySelectorAll("*")
                    : [];

                for (var j = 0; j < all.length; j++) {
                    if (all[j].shadowRoot) {
                        var found = findClearButton(all[j].shadowRoot);
                        if (found) { return found; }
                    }
                }

                return null;
            }

            var btn = findClearButton(document);
            if (btn) {
                btn.click();
                return true;
            }

            return false;
        })();
        """

        def _report(found: bool) -> None:
            if found:
                self.statusBar().showMessage("Plot and recorded data cleared")
            else:
                self.statusBar().showMessage("Recorded data cleared; plot clear trigger not found (page still loading?)")
        self.browser.page().runJavaScript(js_code, _report)

    def on_save_csv(self) -> None:
        if not self.recorded_data['time']:
            QMessageBox.information(
                self,
                "No Data",
                "There is no recorded data to save yet.",
            )
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV File",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith('.csv'):
            file_path += '.csv'
        try:
            with open(file_path, 'w', newline='') as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(['time_s', 'I', 'Q', 'R', 'phase_rad'])
                writer.writerows(zip(self.recorded_data['time'], self.recorded_data['I'], self.recorded_data['Q'], self.recorded_data['R'], self.recorded_data['phase']))
            QMessageBox.information(
                self,
                "Success",
                f"CSV file saved:\n{file_path}",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to save file:\n{exc}",
            )

# ======================================================================
# Main
# ======================================================================

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(pyside6_simple_dark_theme)
    window = LockInAmp(
        host="10.42.0.100",
        port=5001,
        plot_url="http://localhost:5006/",
    )
    window.resize(1200, 700)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
