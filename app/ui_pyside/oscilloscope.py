from PySide6.QtCore import QUrl, Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QTabWidget, QDoubleSpinBox, QSpinBox,
    QComboBox, QPushButton, QSizePolicy, QFormLayout,
    QRadioButton, QStatusBar, QLabel, QGridLayout,
    QFileDialog, QMessageBox, QLineEdit
)
from PySide6.QtGui import QAction, QActionGroup, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.rp_plot.plot_setup import BokehPlot
from app.ui_pyside.generator import GeneratorSettingsWidget


class Oscilloscope(QMainWindow):
    """Control panel window for the Red Pitaya oscilloscope / real-time plot."""

    STATUS_CONNECTED_STYLE = "background-color: green; border-radius: 6px;"
    STATUS_DISCONNECTED_STYLE = "background-color: red; border-radius: 6px;"

    TRIGGER_SOURCES = [
        'DISABLED', 'NOW', 'CH1_PE', 'CH1_NE', 'CH2_PE',
        'CH2_NE', 'EXT_PE', 'EXT_NE', 'AWG_PE', 'AWG_NE',
    ]
    DEFAULT_TRIGGER_SOURCE_INDEX = 2  # 'CH1_PE'

    CONNECTION_POLL_INTERVAL_MS = 500
    RANGE_SYNC_INTERVAL_MS = 25

    def __init__(self, app, rp_plot: BokehPlot, url='http://localhost:5006/main'):
        super().__init__()
        self.app = app
        self.rp_plot = rp_plot
        self.setWindowTitle("Oscilloscope Control Panel")

        # Default values
        self.default_port = "None"
        self.default_baudrate = 115200
        self.default_y_min = 0.0
        self.default_y_max = 3.5
        self.default_roll_over = 1000

        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl(url))

        self.init_ui()
        self.create_menu_bar()
        self._start_background_timers()

        if self.rp_plot.osci:
            self.change_osci_mode()
        else:
            self.change_to_real_time_mode()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def init_ui(self):
        self.central_widget = QWidget()
        main_layout = QHBoxLayout(self.central_widget)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Waiting...")

        sidebar_layout = QVBoxLayout()
        sidebar_layout.setSpacing(15)
        sidebar_layout.addWidget(self._build_generator_group())
        sidebar_layout.addWidget(self._build_acquisition_group())
        sidebar_layout.addWidget(self._build_serial_group())
        sidebar_layout.addWidget(self._build_plot_options_group())
        sidebar_layout.addStretch()
        sidebar_layout.addLayout(self._build_logo_row())

        main_layout.addLayout(sidebar_layout, stretch=0)
        main_layout.addWidget(self.browser, stretch=1)

        self.setCentralWidget(self.central_widget)

    def _build_generator_group(self) -> QGroupBox:
        generator_tabs = QTabWidget()
        generator_tabs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        for ch in (1, 2):
            gen_widget = GeneratorSettingsWidget(channel=ch, on_change_callback=self.rp_plot.generate_signal)
            generator_tabs.addTab(gen_widget, f"CH{ch}")

        redpitaya_ip = QLineEdit(self.rp_plot.rp_ip)
        redpitaya_ip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        redpitaya_ip.textChanged.connect(self.rp_plot.update_rp_ip)

        self.status_label = QLabel()
        self.status_label.setFixedSize(12, 12)
        self.status_label.setStyleSheet(self.STATUS_DISCONNECTED_STYLE)

        ip_layout = QHBoxLayout()
        ip_layout.addWidget(redpitaya_ip)
        ip_layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        group = QGroupBox("Generator Settings")
        layout = QVBoxLayout(group)
        layout.addWidget(generator_tabs)
        layout.addLayout(ip_layout)
        return group

    def _build_acquisition_group(self) -> QGroupBox:
        self.acquiring_group = QGroupBox("Acquisition Settings")
        layout = QFormLayout(self.acquiring_group)

        trigger_level_spin = QDoubleSpinBox()
        trigger_level_spin.setRange(-100, 100)
        trigger_level_spin.setValue(0.0)
        trigger_level_spin.setSingleStep(0.01)
        trigger_level_spin.valueChanged.connect(self.rp_plot.update_trigger_level)
        layout.addRow("Trigger Level (V):", trigger_level_spin)

        trigger_delay_spin = QSpinBox()
        trigger_delay_spin.setRange(-8192, 8192)
        trigger_delay_spin.setValue(0)
        trigger_delay_spin.valueChanged.connect(self.rp_plot.update_trigger_delay)
        layout.addRow("Trigger Delay (ms):", trigger_delay_spin)

        trigger_source_combo = QComboBox()
        trigger_source_combo.addItems(self.TRIGGER_SOURCES)
        trigger_source_combo.setCurrentIndex(self.DEFAULT_TRIGGER_SOURCE_INDEX)
        trigger_source_combo.currentTextChanged.connect(self.rp_plot.update_trigger_source)
        layout.addRow("Trigger Source:", trigger_source_combo)

        decimation_spin = QSpinBox()
        decimation_spin.setRange(1, 16)  # power-of-two levels
        decimation_spin.setValue(3)
        decimation_spin.valueChanged.connect(self.rp_plot.update_decimation)
        layout.addRow("Decimation:", decimation_spin)

        return self.acquiring_group

    def _build_serial_group(self) -> QGroupBox:
        self.ports_list = QComboBox()
        self.ports_list.addItem("None")
        self.ports_list.addItems(self.rp_plot.sr_data.search())
        self.ports_list.setCurrentText(self.default_port)
        self.ports_list.textActivated.connect(self.select_port)

        self.baud_rate_spin = QSpinBox()
        self.baud_rate_spin.setRange(0, int(1e7))
        self.baud_rate_spin.setValue(self.rp_plot.sr_data.sr.baudrate)  # type: ignore
        self.baud_rate_spin.valueChanged.connect(self.rp_plot.sr_data.update_baud_rate)

        self.roll_over_spin = QSpinBox()
        self.roll_over_spin.setRange(1, int(1e7))
        self.roll_over_spin.setValue(self.default_roll_over)
        self.roll_over_spin.valueChanged.connect(self.rp_plot.update_roll_over)

        update_ports_btn = QPushButton("Update Ports")
        update_ports_btn.clicked.connect(self.update_port_list)

        self.serial_group = QGroupBox("Serial Settings")
        layout = QFormLayout(self.serial_group)
        layout.addRow("Available Ports:", self.ports_list)
        layout.addRow("Baud Rate:", self.baud_rate_spin)
        layout.addRow("Roll Over:", self.roll_over_spin)
        layout.addRow(update_ports_btn)
        return self.serial_group

    def _build_plot_options_group(self) -> QGroupBox:
        self.max_y_spin = self._make_range_spin(decimals=4, min_=-100, max_=100,
                                                  value=self.rp_plot.plot_b.y_range.end, step=0.01)
        self.min_y_spin = self._make_range_spin(decimals=4, min_=-100, max_=100,
                                                  value=self.rp_plot.plot_b.y_range.start, step=0.01)
        self.max_x_spin = self._make_range_spin(min_=-int(1e6), max_=int(1e6),
                                                  value=self.rp_plot.plot_b.x_range.end, step=10)
        self.min_x_spin = self._make_range_spin(min_=-int(1e6), max_=int(1e6),
                                                  value=self.rp_plot.plot_b.x_range.start, step=10)

        for spin, update_func in (
            (self.min_y_spin, self.update_y_range), (self.max_y_spin, self.update_y_range),
            (self.min_x_spin, self.update_x_range), (self.max_x_spin, self.update_x_range),
        ):
            spin.editingFinished.connect(update_func)

        self.scatter_radio = QRadioButton("Scatter Plot")
        self.scatter_radio.setChecked(self.rp_plot.scatter_plot)
        self.scatter_radio.toggled.connect(self.rp_plot.change_scatter)

        export_button = QPushButton("Export CSV")
        export_button.clicked.connect(self.export_csv)

        auto_scale_button = QPushButton("Auto-Scale")
        auto_scale_button.clicked.connect(self.rp_plot.auto_scale)

        group = QGroupBox("Voltage / Time : Range")
        layout = QVBoxLayout(group)
        layout.addWidget(self.scatter_radio)

        spin_layout = QFormLayout()
        spin_layout.addRow("Max V:", self.max_y_spin)
        spin_layout.addRow("Min V:", self.min_y_spin)
        spin_layout.addRow("Max t:", self.max_x_spin)
        spin_layout.addRow("Min t:", self.min_x_spin)
        layout.addLayout(spin_layout)

        layout.addWidget(export_button)
        layout.addWidget(auto_scale_button)
        layout.addLayout(self._build_scale_dpad())

        return group

    @staticmethod
    def _make_range_spin(min_, max_, value, step, decimals=None) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        if decimals is not None:
            spin.setDecimals(decimals)
        spin.setRange(min_, max_)
        spin.setValue(value)
        spin.setSingleStep(step)
        return spin

    def _build_scale_dpad(self) -> QGridLayout:
        """4-way voltage/time zoom control. Each button nudges the current
        range in/out by 10% around its midpoint."""
        dpad_layout = QGridLayout()
        dpad_layout.addWidget(
            QLabel("Scale Control"), 0, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter
        )

        up_btn, down_btn = QPushButton("+V"), QPushButton("-V")
        left_btn, right_btn = QPushButton("-T"), QPushButton("+T")

        dpad_layout.addWidget(up_btn, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(down_btn, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(left_btn, 1, 0, 2, 1,
                               alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        dpad_layout.addWidget(right_btn, 1, 2, 2, 1,
                               alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        dpad_layout.setVerticalSpacing(2)
        dpad_layout.setHorizontalSpacing(10)

        up_btn.clicked.connect(lambda: self._nudge_range(
            self.min_y_spin, self.max_y_spin, factor=1.1, update_func=self.rp_plot.update_y_range))
        down_btn.clicked.connect(lambda: self._nudge_range(
            self.min_y_spin, self.max_y_spin, factor=0.9, update_func=self.rp_plot.update_y_range))
        left_btn.clicked.connect(lambda: self._nudge_range(
            self.min_x_spin, self.max_x_spin, factor=0.9, update_func=self.rp_plot.update_x_range))
        right_btn.clicked.connect(lambda: self._nudge_range(
            self.min_x_spin, self.max_x_spin, factor=1.1, update_func=self.rp_plot.update_x_range))

        return dpad_layout

    @staticmethod
    def _nudge_range(min_spin: QDoubleSpinBox, max_spin: QDoubleSpinBox, factor: float, update_func):
        """Scale the [min_spin, max_spin] interval by `factor` around its midpoint."""
        start, end = min_spin.value(), max_spin.value()
        mid = (start + end) / 2
        new_range = (end - start) * factor
        update_func(min_val=mid - new_range / 2, max_val=mid + new_range / 2)

    def _build_logo_row(self) -> QHBoxLayout:
        logo_layout = QHBoxLayout()
        self.uni_logo = QLabel()
        self.uni_logo.setPixmap(QPixmap("assets/ua.png").scaledToWidth(75))
        self.group_logo = QLabel()
        self.group_logo.setPixmap(QPixmap("assets/geoel.png").scaledToWidth(100))
        logo_layout.addWidget(self.uni_logo)
        logo_layout.addWidget(self.group_logo)
        return logo_layout

    def _start_background_timers(self):
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.timer_multiprocess)
        self.check_timer.start(self.CONNECTION_POLL_INTERVAL_MS)

        self.update_scales_timer = QTimer(self)
        self.update_scales_timer.timeout.connect(self.update_scales_spins)
        self.update_scales_timer.start(self.RANGE_SYNC_INTERVAL_MS)

    # ------------------------------------------------------------------ #
    # Status / polling
    # ------------------------------------------------------------------ #
    def show_status_bar_msg(self, msg, time=3000):
        self.status_bar.showMessage(msg, time)

    def timer_multiprocess(self):
        style = self.STATUS_CONNECTED_STYLE if self.rp_plot.rp_connected else self.STATUS_DISCONNECTED_STYLE
        self.status_label.setStyleSheet(style)

    def update_scales_spins(self):
        editing = (
            self.max_x_spin.hasFocus() or self.min_x_spin.hasFocus()
            or self.max_y_spin.hasFocus() or self.min_y_spin.hasFocus()
        )
        if editing:
            return  # Don't clobber a value the user is mid-typing
        self.max_x_spin.setValue(self.rp_plot.plot_b.x_range.end)
        self.min_x_spin.setValue(self.rp_plot.plot_b.x_range.start)
        self.max_y_spin.setValue(self.rp_plot.plot_b.y_range.end)
        self.min_y_spin.setValue(self.rp_plot.plot_b.y_range.start)

    # ------------------------------------------------------------------ #
    # Serial port handling
    # ------------------------------------------------------------------ #
    def select_port(self, port_selected):
        self.rp_plot.sr_data.select_port(port_selected)
        if port_selected != "None":
            self.rp_plot.reading = True

    def update_port_list(self):
        self.ports_list.clear()
        self.ports_list.addItem("None")
        self.ports_list.addItems(self.rp_plot.sr_data.search())

    # ------------------------------------------------------------------ #
    # Range control
    # ------------------------------------------------------------------ #
    def update_y_range(self):
        self.rp_plot.update_y_range(min_val=self.min_y_spin.value(), max_val=self.max_y_spin.value())

    def update_x_range(self):
        self.rp_plot.update_x_range(min_val=self.min_x_spin.value(), max_val=self.max_x_spin.value())

    # ------------------------------------------------------------------ #
    # Mode switching
    # ------------------------------------------------------------------ #
    def change_osci_mode(self):
        self.ports_list.setCurrentText(self.default_port)
        self.rp_plot.change_to_oscilloscope_mode()
        self.rp_plot.reading = False
        self.max_x_spin.setEnabled(True)
        self.min_x_spin.setEnabled(True)
        self.max_y_spin.setValue(1.00)
        self.min_y_spin.setValue(-1.00)
        self.update_y_range()
        self.serial_group.hide()
        self.acquiring_group.show()
        self.status_bar.showMessage("Changed to oscilloscope mode", 3000)

    def change_to_real_time_mode(self):
        self.ports_list.setCurrentText(self.default_port)
        self.rp_plot.change_to_real_time_mode()
        self.rp_plot.reading = False
        self.max_x_spin.setEnabled(False)
        self.min_x_spin.setEnabled(False)
        self.max_y_spin.setValue(3.33)
        self.min_y_spin.setValue(0)
        self.update_y_range()
        self.serial_group.show()
        self.acquiring_group.hide()
        self.status_bar.showMessage("Changed to real-time mode", 3000)

    def reset_all(self):
        self.rp_plot.sr_data.close()
        self.ports_list.setCurrentText("None")
        self.baud_rate_spin.setValue(self.default_baudrate)
        self.max_y_spin.setValue(self.default_y_max)
        self.min_y_spin.setValue(self.default_y_min)

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def export_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV File", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        try:
            self.rp_plot.save_current_data(file_path)
            QMessageBox.information(self, "Success", f"CSV file saved:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    # ------------------------------------------------------------------ #
    # Menu bar
    # ------------------------------------------------------------------ #
    def create_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        menu_bar.addMenu("Edit")  # placeholder, no actions yet

        tools_menu = menu_bar.addMenu("Tools")

        osci_mode_action = QAction("Change to oscilloscope mode", self, checkable=True)
        osci_mode_action.triggered.connect(self.change_osci_mode)

        real_time_mode_action = QAction("Change to real time mode", self, checkable=True)
        real_time_mode_action.triggered.connect(self.change_to_real_time_mode)

        mode_group = QActionGroup(self)
        mode_group.addAction(osci_mode_action)
        mode_group.addAction(real_time_mode_action)
        mode_group.setExclusive(True)
        osci_mode_action.setChecked(self.rp_plot.osci)

        mode_menu = tools_menu.addMenu("Acquisition mode")
        mode_menu.addActions([osci_mode_action, real_time_mode_action])

        update_ports_action = QAction("Update Ports", self)
        update_ports_action.triggered.connect(self.update_port_list)

        reset_all_action = QAction("Reset application", self)
        reset_all_action.triggered.connect(self.reset_all)

        tools_menu.addActions([update_ports_action, reset_all_action])

        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(lambda: print("Oscilloscope app v1.0"))
        help_menu.addAction(about_action)