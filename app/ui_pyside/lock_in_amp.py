from __future__ import annotations
import csv
import math
import re
import sys
import time
from collections import deque
import numpy as np
from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QPixmap, QValidator
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from docs.dark_theme import pyside6_simple_dark_theme
from app.paths import resource_path
from app.rp_lia.redpitaya_lia import RedPitayaLIA
from app.ui_pyside.calibration_tool import CalibrationDialog


class ScientificDoubleSpinBox(QDoubleSpinBox):
    """Spin box that shows and accepts scientific notation (e.g. 1.220703e-04).

    B2V is typically ~1e-4 V per count, which a fixed-decimals
    QDoubleSpinBox cannot show with useful precision. Stepping is
    multiplicative (1 % per step) because an additive step makes no sense
    for a quantity that can span several orders of magnitude.
    """

    _COMPLETE = re.compile(r"^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
    _PARTIAL = re.compile(r"^(\d*\.?\d*)([eE][+-]?\d*)?$")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDecimals(15)  # internal precision; display is set by textFromValue
        self.setRange(1e-12, 1e3)
        self.setKeyboardTracking(False)  # emit valueChanged on Enter / focus-out only

    def textFromValue(self, value: float) -> str:
        return f"{value:.6e}"

    def valueFromText(self, text: str) -> float:
        try:
            return float(text.strip())
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        stripped = text.strip()
        if self._COMPLETE.match(stripped):
            return (QValidator.State.Acceptable, text, pos)
        if self._PARTIAL.match(stripped):  # "", "1e", "1e-", "." ... still being typed
            return (QValidator.State.Intermediate, text, pos)
        return (QValidator.State.Invalid, text, pos)

    def stepBy(self, steps: int) -> None:
        self.setValue(self.value() * (1.01 ** steps))


def make_selectable(*labels: QLabel) -> None:
    """Let the user select text with the mouse (then Ctrl+C or right-click > Copy)."""
    for label in labels:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


def set_label_text(label: QLabel, text: str) -> None:
    """setText that does not wipe a selection the user is in the middle of
    copying: while text is selected the label keeps its content, and it
    resumes updating as soon as the selection is cleared (click elsewhere)."""
    if label.hasSelectedText():
        return
    label.setText(text)


# Fixed width (px) of the left sidebar, so it never resizes as readings change.
# Raise it if any control looks cramped.
SIDEBAR_WIDTH = 400

# --- Display precision -------------------------------------------------------
# With a heavy filter (e.g. K=31, 8 stages) the readings are stable to many more
# digits than before, so show plenty. Raise/lower these to taste.
COUNT_DECIMALS = 6      # decimals in the Counts tab I / Q / R readout and stats means
PHASE_DECIMALS = 6      # decimals (degrees) in the phase readout and phase mean
VOLT_DECIMALS = 10      # decimals in the Voltage tab I / Q / R readout (V)
STATS_SIG = 6           # significant digits for Std and Std % (they get tiny when filtered)
STATS_VOLT_MEAN_SIG = 9  # significant digits for the voltage mean
STATS_VOLT_SIG = STATS_SIG  # significant digits for the voltage Std


def format_volts(value: float | None, sig: int = STATS_VOLT_SIG) -> str:
    """Volts with an automatic V / mV / uV / nV prefix and ~`sig` significant
    digits, so tiny standard deviations stay readable and compact."""
    if value is None:
        return "\u2014"
    magnitude = abs(value)
    if magnitude == 0:
        return "0 V"
    for scale, unit in ((1.0, "V"), (1e3, "mV"), (1e6, "\u00b5V"), (1e9, "nV")):
        if magnitude * scale >= 1 or unit == "nV":
            break
    scaled = value * scale
    integer_digits = len(str(int(abs(scaled)))) if abs(scaled) >= 1 else 1
    decimals = max(sig - integer_digits, 0)
    return f"{scaled:.{decimals}f} {unit}"


def format_sig(value: float | None, sig: int = STATS_SIG, suffix: str = "") -> str:
    """`value` with `sig` significant digits, however small it is.

    Fixed decimals would print a tiny standard deviation (say 0.00042) as
    0.00, so the number of decimals follows the magnitude instead. Very
    small / very large values fall back to scientific notation.
    """
    if value is None:
        return "\u2014"
    if value == 0:
        return f"0{suffix}"
    magnitude = math.floor(math.log10(abs(value)))
    if magnitude < -6 or magnitude >= 9:
        return f"{value:.{sig - 1}e}{suffix}"
    decimals = max(sig - 1 - magnitude, 0)
    return f"{value:.{decimals}f}{suffix}"


def compute_stats(samples: np.ndarray, circular: bool = False) -> tuple[float, float | None]:
    """Mean and sample standard deviation (ddof=1) of `samples`.

    With circular=True the samples are angles in radians and the circular
    mean / deviation are used, so a phase hovering around +/-180 deg does
    not produce a bogus huge spread. Returns std=None for fewer than 2
    samples.
    """
    n = len(samples)
    if circular:
        mean = float(np.arctan2(np.mean(np.sin(samples)), np.mean(np.cos(samples))))
        deviations = (samples - mean + np.pi) % (2 * np.pi) - np.pi
        std = float(np.sqrt(np.sum(deviations ** 2) / (n - 1))) if n > 1 else None
    else:
        mean = float(np.mean(samples))
        std = float(np.std(samples, ddof=1)) if n > 1 else None
    return mean, std


class StatsPanel(QWidget):
    """Small grid: one row per channel, columns Mean / Std / Std %."""

    ROWS = ("I", "Q", "R", "Phase")
    HEADERS = ("", "Mean", "Std", "Std %")
    _STYLE = "font-family: monospace;font-size: 11px;"

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)

        for col, text in enumerate(self.HEADERS):
            header = QLabel(text)
            header.setStyleSheet(self._STYLE + "font-weight: bold;")
            header.setAlignment(Qt.AlignmentFlag.AlignRight if col else Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(header, 0, col)

        self._cells: dict[str, list[QLabel]] = {}
        for row, name in enumerate(self.ROWS, start=1):
            name_label = QLabel(name)
            name_label.setStyleSheet(self._STYLE)
            grid.addWidget(name_label, row, 0)
            cells = []
            for col in range(1, 4):
                cell = QLabel("\u2014")
                cell.setStyleSheet(self._STYLE)
                cell.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(cell, row, col)
                make_selectable(cell)
                cells.append(cell)
            self._cells[name] = cells

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)

        self.n_label = QLabel("n = 0")
        self.n_label.setStyleSheet("font-size: 11px; font-style: italic;")
        make_selectable(self.n_label)
        grid.addWidget(self.n_label, len(self.ROWS) + 1, 0, 1, 2)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.status_label, len(self.ROWS) + 1, 2, 1, 2)
        self.set_running(True)

    def set_row(self, name: str, mean: str, std: str, pct: str) -> None:
        for cell, text in zip(self._cells[name], (mean, std, pct)):
            set_label_text(cell, text)

    def as_text(self) -> str:
        """The table as tab-separated text (pastes cleanly into Excel / Sheets)."""
        lines = ["\t".join(self.HEADERS)]
        for name in self.ROWS:
            lines.append("\t".join([name] + [cell.text() for cell in self._cells[name]]))
        lines.append(self.n_label.text())
        return "\n".join(lines)

    def set_running(self, running: bool) -> None:
        if running:
            self.status_label.setText("\u25cf Running")
            self.status_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #4caf50;")
        else:
            self.status_label.setText("\u25cf Stopped")
            self.status_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #e05555;")

    def set_count(self, n: int) -> None:
        set_label_text(self.n_label, f"n = {n}")

    def clear(self) -> None:
        for cells in self._cells.values():
            for cell in cells:
                set_label_text(cell, "\u2014")
        self.set_count(0)


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
        self.ext_ref = False
        self.zc_threshold = 200
        self._quit_app_on_close = True
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
        self._calibration_dialog = None
        # Volts (peak) per count of I/Q/R. Starts at the nominal value
        # (full-scale voltage / 8192) until calibrated or set manually.
        self.b2v: float = RedPitayaLIA.volts_per_count
        self.b2v_locked = False
        self._last_reading: tuple[float, float, float, float] | None = None
        # Rolling buffers (raw counts; phase in rad) the statistics are
        # computed over. Voltage stats are derived from the counts buffers
        # with the *current* B2V, so they follow B2V changes immediately.
        self.stats_window = 100
        self.stats_running = True  # False = statistics frozen (Stop)
        self._stat_buffers: dict[str, deque] = {
            key: deque(maxlen=self.stats_window) for key in ("I", "Q", "R", "phase")
        }
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

        if self._calibration_dialog is not None: self._calibration_dialog.close()

        if self.lia.connected:
            try:
                self.lia.soft_reset()
            except Exception:
                pass
            self.lia.disconnect()
        event.accept()

        if self._quit_app_on_close:
            app = QApplication.instance()
            if app is not None:
                app.quit()
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
        # Scrollable, so adding content (e.g. the statistics) never makes Qt
        # squeeze/overlap widgets when the window is shorter than the sidebar.
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidget(sidebar_widget)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setFixedWidth(SIDEBAR_WIDTH)
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
        main_layout.addWidget(sidebar_scroll, stretch=0)
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
        self.in2_frequency_label = QLabel("Frequency: 0.00 Hz")
        for label in (self.in2_period_label, self.in2_frequency_label):
            label.setStyleSheet("font-family: monospace;font-size: 11px;")
            make_selectable(label)
        form.addRow(self.ext_ref_checkbox)
        form.addRow(QLabel('ZC Threshold:'), self.zc_threshold_spinbox)
        in2_row = QHBoxLayout()
        in2_row.addWidget(self.in2_period_label)
        in2_row.addStretch(1)
        in2_row.addWidget(self.in2_frequency_label)
        form.addRow(in2_row)
        form.addRow(QLabel('Frequency:'), self.frequency_spinbox)
        form.addRow(QLabel('Phase Offset:'), self.phase_offset_spinbox)
        # Filter parameters side by side
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel('K:'))
        filter_row.addWidget(self.k_spinbox, 1)
        filter_row.addSpacing(8)
        filter_row.addWidget(QLabel('IIR Stages:'))
        filter_row.addWidget(self.stages_spinbox, 1)
        form.addRow(filter_row)

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
            make_selectable(label)
            counts_layout.addWidget(label)
        counts_layout.addSpacing(6)
        counts_layout.addWidget(self._build_stats_title())
        self.counts_stats = StatsPanel()
        counts_layout.addWidget(self.counts_stats)
        volts_tab = QWidget()
        volts_layout = QVBoxLayout(volts_tab)
        self.i_volts_label = QLabel(f"I: {0:.{VOLT_DECIMALS}f} V")
        self.q_volts_label = QLabel(f"Q: {0:.{VOLT_DECIMALS}f} V")
        self.r_volts_label = QLabel(f"R: {0:.{VOLT_DECIMALS}f} V")
        self.phase_volts_label = QLabel("Phase: 0.00°")
        for label in (self.i_volts_label, self.q_volts_label, self.r_volts_label, self.phase_volts_label):
            label.setStyleSheet("font-family: monospace;font-size: 13px;")
            make_selectable(label)
            volts_layout.addWidget(label)

        volts_layout.addSpacing(6)
        volts_layout.addWidget(self._build_stats_title())
        self.volts_stats = StatsPanel()
        volts_layout.addWidget(self.volts_stats)

        volts_layout.addSpacing(6)
        b2v_row = QHBoxLayout()
        b2v_label = QLabel("B2V:")
        b2v_label.setToolTip("Volts (peak) per count")
        b2v_row.addWidget(b2v_label)
        self.b2v_spinbox = ScientificDoubleSpinBox()
        self.b2v_spinbox.setValue(self.b2v)
        self.b2v_spinbox.setToolTip(
            "Conversion constant from raw counts to volts (peak) for I, Q and R.\n"
            "It is set and locked automatically by the Calibration tool."
        )
        self.b2v_spinbox.valueChanged.connect(self.on_b2v_spin_changed)
        self.b2v_unlock_button = QPushButton("Unlock")
        self.b2v_unlock_button.setMinimumWidth(84)
        self.b2v_unlock_button.setMinimumHeight(28)
        self.b2v_unlock_button.setEnabled(False)  # only meaningful while locked
        self.b2v_unlock_button.setToolTip("Allow manual editing of the calibrated B2V")
        self.b2v_unlock_button.clicked.connect(self.on_unlock_b2v)
        b2v_row.addWidget(self.b2v_spinbox, 1)
        b2v_row.addWidget(self.b2v_unlock_button)
        volts_layout.addLayout(b2v_row)
        self.b2v_source_label = QLabel("Source: nominal default")
        self.b2v_source_label.setStyleSheet("font-size: 11px; font-style: italic;")
        volts_layout.addWidget(self.b2v_source_label)
        volts_layout.addStretch(1)

        self.readings_tabs.addTab(volts_tab, "Voltage")
        self.readings_tabs.addTab(counts_tab, "Counts")

        layout.addWidget(self.readings_tabs)
        run_stop_row = QHBoxLayout()
        self.stats_run_button = QPushButton("\u25b6 Run")
        self.stats_stop_button = QPushButton("\u25a0 Stop")
        for button, colour, hover in (
            (self.stats_run_button, "#2e7d32", "#4caf50"),
            (self.stats_stop_button, "#c62828", "#e05555"),
        ):
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.setStyleSheet(
                f"QPushButton {{ border: 1px solid {colour}; border-radius: 4px;"
                f" padding: 3px 10px; color: {hover}; }}"
                f"QPushButton:checked {{ background-color: {colour};"
                f" color: white; font-weight: bold; }}"
            )
        self.stats_run_button.setChecked(True)
        self.stats_run_button.setToolTip("Resume updating the statistics")
        self.stats_stop_button.setToolTip(
            "Freeze the statistics so the values can be written down\n"
            "(the live I/Q/R readout and the recording keep going)"
        )
        self.stats_run_group = QButtonGroup(self)
        self.stats_run_group.setExclusive(True)
        self.stats_run_group.addButton(self.stats_run_button)
        self.stats_run_group.addButton(self.stats_stop_button)
        self.stats_run_button.clicked.connect(lambda: self.on_stats_running_changed(True))
        self.stats_stop_button.clicked.connect(lambda: self.on_stats_running_changed(False))
        self.stats_reset_button = QPushButton("Reset")
        self.stats_reset_button.setMinimumHeight(28)
        self.stats_reset_button.setToolTip("Discard the samples used for the statistics")
        self.stats_reset_button.clicked.connect(self.reset_stats)
        run_stop_row.addWidget(self.stats_run_button, 1)
        run_stop_row.addWidget(self.stats_stop_button, 1)
        run_stop_row.addWidget(self.stats_reset_button, 1)
        self.stats_copy_button = QPushButton("Copy")
        self.stats_copy_button.setMinimumHeight(28)
        self.stats_copy_button.setToolTip(
            "Copy the statistics table of the current tab to the clipboard\n"
            "(tab-separated, pastes into Excel / Sheets)"
        )
        self.stats_copy_button.clicked.connect(self.on_copy_stats)
        run_stop_row.addWidget(self.stats_copy_button, 1)
        layout.addLayout(run_stop_row)
        stats_row = QHBoxLayout()
        stats_row.addWidget(QLabel("Stats window:"))
        self.stats_window_spinbox = QSpinBox()
        self.stats_window_spinbox.setRange(2, 100000)
        self.stats_window_spinbox.setValue(self.stats_window)
        self.stats_window_spinbox.setSuffix(" samples")
        self.stats_window_spinbox.setToolTip(
            "Mean / Std are computed over the most recent N readings."
        )
        self.stats_window_spinbox.valueChanged.connect(self.on_stats_window_changed)
        stats_row.addWidget(self.stats_window_spinbox, 1)
        layout.addLayout(stats_row)
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

    def _build_stats_title(self) -> QLabel:
        title = QLabel("Statistics")
        title.setStyleSheet("font-size: 12px;font-weight: bold;")
        title.setToolTip(
            "Std is the sample standard deviation (n-1).\n"
            "Std % = Std / |Mean| x 100 for I, Q and R (shown as \u2014 if the mean is 0).\n"
            "Phase is averaged circularly; its Std % is relative to 360\u00b0."
        )
        return title

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def on_stats_window_changed(self, n: int) -> None:
        self.stats_window = int(n)
        # Re-create the deques with the new maxlen, keeping the newest samples.
        for key, buf in self._stat_buffers.items():
            self._stat_buffers[key] = deque(buf, maxlen=self.stats_window)
        self._refresh_stats()

    def on_stats_running_changed(self, running: bool) -> None:
        """Run = keep adding readings to the statistics; Stop = freeze them."""
        self.stats_running = running
        self.counts_stats.set_running(running)
        self.volts_stats.set_running(running)
        self.statusBar().showMessage(
            "Statistics running" if running else "Statistics stopped (frozen)"
        )

    def on_copy_stats(self) -> None:
        panel = self.counts_stats if self.readings_tabs.currentIndex() == 0 else self.volts_stats
        QApplication.clipboard().setText(panel.as_text())
        self.statusBar().showMessage("Statistics copied to the clipboard", 3000)

    def reset_stats(self) -> None:
        for buf in self._stat_buffers.values():
            buf.clear()
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        """Recompute Mean / Std / Std % for the Counts and Voltage tabs."""
        n = len(self._stat_buffers["I"])
        if n == 0:
            self.counts_stats.clear()
            self.volts_stats.clear()
            return

        def pct_of_mean(mean: float, std: float | None) -> float | None:
            if std is None or mean == 0:
                return None
            return std / abs(mean) * 100.0

        def fmt(value: float | None, decimals: int, suffix: str = "") -> str:
            return "\u2014" if value is None else f"{value:.{decimals}f}{suffix}"

        for key, name in (("I", "I"), ("Q", "Q"), ("R", "R")):
            samples = np.fromiter(self._stat_buffers[key], dtype=float, count=n)
            mean, std = compute_stats(samples)
            pct = pct_of_mean(mean, std)
            pct_text = format_sig(pct, 4, " %")

            self.counts_stats.set_row(
                name, fmt(mean, COUNT_DECIMALS), format_sig(std, STATS_SIG), pct_text
            )

            # Same relative spread in volts; absolute values scale by B2V.
            self.volts_stats.set_row(
                name,
                format_volts(mean * self.b2v, STATS_VOLT_MEAN_SIG),
                format_volts(None if std is None else std * self.b2v, STATS_VOLT_SIG),
                pct_text,
            )

        phase = np.fromiter(self._stat_buffers["phase"], dtype=float, count=n)
        mean_rad, std_rad = compute_stats(phase, circular=True)
        mean_deg = float(np.degrees(mean_rad))
        std_deg = None if std_rad is None else float(np.degrees(std_rad))
        phase_pct = None if std_deg is None else std_deg / 360.0 * 100.0
        for panel in (self.counts_stats, self.volts_stats):
            panel.set_row(
                "Phase",
                fmt(mean_deg, PHASE_DECIMALS, "\u00b0"),
                format_sig(std_deg, STATS_SIG, "\u00b0"),
                format_sig(phase_pct, 4, " %"),
            )

        self.counts_stats.set_count(n)
        self.volts_stats.set_count(n)

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
        self.calibration_button = QPushButton("Calibration")
        self.calibration_button.clicked.connect(self.on_open_calibration)
        layout.addWidget(self.clear_plot_button)
        layout.addWidget(self.save_csv_button)
        layout.addWidget(self.calibration_button)
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
        logo_layout.setContentsMargins(10, 6, 10, 6)
        logo_layout.setSpacing(7)

        self.uni_logo = QLabel()
        self.uni_logo.setPixmap(
            QPixmap(resource_path('docs/ua.png')).scaledToWidth(75, Qt.TransformationMode.SmoothTransformation)
        )
        self.uni_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.group_logo = QLabel()
        self.group_logo.setPixmap(
            QPixmap(resource_path('docs/geoel.png')).scaledToWidth(100, Qt.TransformationMode.SmoothTransformation)
        )
        self.group_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
            set_label_text(self.in2_period_label, f"IN2 Period: {period} cycles")
            set_label_text(self.in2_frequency_label, f"Frequency: {external_frequency:.2f} Hz")
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
        set_label_text(self.i_value_label, f"I: {i_value:.{COUNT_DECIMALS}f}")
        set_label_text(self.q_value_label, f"Q: {q_value:.{COUNT_DECIMALS}f}")
        set_label_text(self.r_value_label, f"R: {r_value:.{COUNT_DECIMALS}f}")
        set_label_text(self.phase_value_label, f"Phase: {np.degrees(phase_value):.{PHASE_DECIMALS}f}°")
        self._last_reading = (i_value, q_value, r_value, phase_value)
        if self.stats_running:
            for key, value in zip(("I", "Q", "R", "phase"), self._last_reading):
                self._stat_buffers[key].append(value)
        self._refresh_volts_labels()  # also refreshes the statistics

    def _refresh_volts_labels(self) -> None:
        """Convert the last I/Q/R reading to volts with the current B2V.

        Also called when B2V changes, so the Voltage tab updates
        immediately even if live polling is off.
        """
        if self._last_reading is None:
            return
        self._refresh_stats()
        i_value, q_value, r_value, phase_value = self._last_reading
        set_label_text(self.i_volts_label, f"I: {i_value * self.b2v:.{VOLT_DECIMALS}f} V")
        set_label_text(self.q_volts_label, f"Q: {q_value * self.b2v:.{VOLT_DECIMALS}f} V")
        set_label_text(self.r_volts_label, f"R: {r_value * self.b2v:.{VOLT_DECIMALS}f} V")
        set_label_text(self.phase_volts_label, f"Phase: {np.degrees(phase_value):.{PHASE_DECIMALS}f}°")

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
    
    def on_open_calibration(self) -> None:
        if self._calibration_dialog is None:
            self._calibration_dialog = CalibrationDialog(self.lia, parent=self)
            self._calibration_dialog.b2v_calculated.connect(
                self._on_b2v_calculated
            )
        self._calibration_dialog.show()
        self._calibration_dialog.raise_()
        self._calibration_dialog.activateWindow()

    # ------------------------------------------------------------------
    # B2V (counts -> volts) handling
    # ------------------------------------------------------------------

    def _apply_b2v(self, value: float) -> None:
        """Single place that changes the active B2V."""
        self.b2v = float(value)
        # Keep the client's own conversion helpers consistent with the GUI.
        RedPitayaLIA.volts_per_count = self.b2v
        self._refresh_volts_labels()

    def _set_b2v_locked(self, locked: bool) -> None:
        self.b2v_locked = locked
        self.b2v_spinbox.setEnabled(not locked)
        self.b2v_unlock_button.setEnabled(locked)

    def _on_b2v_calculated(self, b2v: float) -> None:
        """Calibration finished: use its B2V and lock the manual control."""
        self.b2v_spinbox.blockSignals(True)  # don't treat this as a manual edit
        self.b2v_spinbox.setValue(b2v)
        self.b2v_spinbox.blockSignals(False)
        self._apply_b2v(b2v)  # use the exact value, not the spinbox-rounded one
        self._set_b2v_locked(True)
        self.b2v_source_label.setText("Source: calibration (locked)")
        self.statusBar().showMessage(f"B2V = {b2v:.6e} V per count (from calibration)")

    def on_b2v_spin_changed(self, value: float) -> None:
        """Manual edit of the B2V spinbox (only reachable while unlocked)."""
        self._apply_b2v(value)
        self.b2v_source_label.setText("Source: manual")
        self.statusBar().showMessage(f"B2V set manually: {value:.6e} V per count")

    def on_unlock_b2v(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Change calibrated B2V?",
            "The B2V constant was set by the calibration tool.\n\n"
            "Editing it manually will override the calibrated value and the "
            "Voltage readings will no longer reflect your calibration.\n\n"
            "Do you want to unlock it for manual editing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_b2v_locked(False)
        self.b2v_source_label.setText("Source: calibration (unlocked, editable)")
        self.statusBar().showMessage("B2V unlocked for manual editing")

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
        self.reset_stats()
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