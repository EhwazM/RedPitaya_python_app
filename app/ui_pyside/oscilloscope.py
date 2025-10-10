from PySide6.QtCore import QUrl, Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QTabWidget, QDoubleSpinBox, QSpinBox,
    QComboBox, QPushButton, QSizePolicy, QFormLayout,
    QRadioButton, QStatusBar, QLabel, QCheckBox, QGridLayout,
    QFileDialog,QMessageBox, QLineEdit
)
from PySide6.QtGui import QAction, QActionGroup, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.rp_plot.plot_setup import BokehPlot
from app.rp_data_acquisition.serial_data import SerialData

import csv

class GeneratorSettingsWidget(QWidget):
    def __init__(self, channel: int, on_change_callback):
        super().__init__()
        self.channel = channel
        self.on_change_callback = on_change_callback

        self.default_vpp = 1.5
        self.default_freq = int(1e4)
        self.default_waveform = "sine"

        layout = QFormLayout()

        # --- Vpp ---
        self.vpp_spin = QDoubleSpinBox()
        self.vpp_spin.setRange(0, 2)
        self.vpp_spin.setDecimals(1)
        self.vpp_spin.setSingleStep(0.1)
        self.vpp_spin.setValue(self.default_vpp)
        self.vpp_spin.valueChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Vpp:", self.vpp_spin)

        # --- Frequency ---
        self.freq_spin = QSpinBox()
        self.freq_spin.setRange(0, int(6.25e7))
        self.freq_spin.setSingleStep(100)
        self.freq_spin.setValue(self.default_freq)
        self.freq_spin.valueChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Frequency:", self.freq_spin)

        # --- Waveform ---
        self.waveform_combo = QComboBox()
        self.waveform_combo.addItems(['sine','square','triangle','sawu','sawd','pwm','arbitrary','dc','dc_neg'])
        self.waveform_combo.currentTextChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Waveform:", self.waveform_combo)

        # --- Channel ON/OFF ---
        self.channel_on = QCheckBox("Channel ON")
        self.channel_on.setChecked(False)
        self.channel_on.stateChanged.connect(self.emit_values)
        layout.addRow(self.channel_on)

        # --- Show/Hide Plot ---
        self.show_plot = QCheckBox("Show Plot")
        self.show_plot.setChecked(False)
        self.show_plot.stateChanged.connect(self.emit_values)
        layout.addRow(self.show_plot)

        # # --- Buttons ---
        # self.generate_button = QPushButton("Generate Signal")
        # self.generate_button.pressed.connect(self.emit_values)
        # layout.addRow(self.generate_button)

        # self.default_button = QPushButton("Default Values")
        # self.default_button.pressed.connect(self.default_values)
        # layout.addRow(self.default_button)

        self.setLayout(layout)
        self.emit_values()

    def emit_values(self):
        # Solo emitir si el canal está activo
        values = {
            'channel': self.channel,
            'vpp': self.vpp_spin.value(),
            'freq': self.freq_spin.value(),
            'waveform': self.waveform_combo.currentText(),
            'active': self.channel_on.isChecked(),
            'show_plot': self.show_plot.isChecked()
        }
        self.on_change_callback(values)

    def default_values(self):
        self.vpp_spin.setValue(self.default_vpp)
        self.freq_spin.setValue(self.default_freq)
        self.waveform_combo.setCurrentText(self.default_waveform)
        self.channel_on.setChecked(True)
        self.show_plot.setChecked(True)
        self.emit_values()

class Oscilloscope(QMainWindow):
    def __init__(self, app, rp_plot: BokehPlot, url='http://localhost:5006/main'):
        super().__init__()
        self.app = app
        self.rp_plot = rp_plot
        self.setWindowTitle("Oscilloscope Control Panel")

        # Default Values
        self.default_port = "None"
        self.default_baudrate = 115200
        self.default_y_min = 0.0
        self.default_y_max = 3.5
        self.default_roll_over = 1000

        # Init
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl(url))

        self.init_ui()
        self.create_menu_bar()

        if self.rp_plot.osci:
            self.change_osci_mode()
        else:
            self.change_to_real_time_mode()

    def init_ui(self):
        self.central_widget = QWidget()
        main_layout = QHBoxLayout(self.central_widget)

        sidebar_layout = QVBoxLayout()
        sidebar_layout.setSpacing(15)

        generator_tab = QTabWidget()
        generator_tab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        for ch in [1, 2]:
            gen_widget = GeneratorSettingsWidget(channel=ch, on_change_callback=self.rp_plot.generate_signal)
            generator_tab.addTab(gen_widget, f"CH{ch}")

        generator_group = QGroupBox("Generator Settings")
        generator_layout = QVBoxLayout(generator_group)
        generator_layout.addWidget(generator_tab)

        ip_layout = QHBoxLayout()

        redpitaya_ip = QLineEdit("Red Pitaya IP:")
        redpitaya_ip.setText(self.rp_plot.rp_ip)
        redpitaya_ip.textChanged.connect(self.rp_plot.update_rp_ip)
        redpitaya_ip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ip_layout.addWidget(redpitaya_ip)

        # Small color box
        self.status_label = QLabel()
        self.status_label.setFixedSize(12, 12)
        self.status_label.setStyleSheet("""
            background-color: red;
            border-radius: 6px;
        """)

        ip_layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        generator_layout.addLayout(ip_layout)

        # Create a timer
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.timer_multiprocess)
        self.check_timer.start(50)  # every 1000 ms = 1 second

        # Add to layout
        ip_layout.addWidget(redpitaya_ip)
        ip_layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.acquiring_group = QGroupBox("Acquisition Settings")
        self.acquiring_layout = QFormLayout(self.acquiring_group)

        # Trigger Level
        trigger_level_spin = QDoubleSpinBox()
        trigger_level_spin.setRange(-100, 100)
        trigger_level_spin.setValue(0.0)
        trigger_level_spin.setSingleStep(0.01)
        trigger_level_spin.valueChanged.connect(self.rp_plot.update_trigger_level)
        self.acquiring_layout.addRow("Trigger Level (V):", trigger_level_spin)

        # Trigger Delay
        trigger_delay_spin = QSpinBox()
        trigger_delay_spin.setRange(-8192, 8192)
        trigger_delay_spin.setValue(0)
        trigger_delay_spin.valueChanged.connect(self.rp_plot.update_trigger_delay)
        self.acquiring_layout.addRow("Trigger Delay (ms):", trigger_delay_spin)

        # Trigger Source
        trigger_source_combo = QComboBox()
        trigger_source_combo.addItems(['DISABLED', 'NOW', 'CH1_PE', 'CH1_NE', 'CH2_PE', 'CH2_NE', 'EXT_PE', 'EXT_NE', 'AWG_PE', 'AWG_NE'])
        trigger_source_combo.setCurrentIndex(2)
        trigger_source_combo.currentTextChanged.connect(self.rp_plot.update_trigger_source)
        self.acquiring_layout.addRow("Trigger Source:", trigger_source_combo)

        # Decimation
        decimation_spin = QSpinBox()
        decimation_spin.setRange(1, 16)  # niveles en potencias de 2
        decimation_spin.setValue(3)      # valor por defecto
        decimation_spin.valueChanged.connect(self.rp_plot.update_decimation)

        self.acquiring_layout.addRow("Decimation:", decimation_spin)

        # Crear barra de estado
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Waiting...")

        self.ports_list = QComboBox()
        self.ports_list.addItem("None")
        self.ports_list.addItems(self.rp_plot.sr_data.search())
        self.ports_list.setCurrentText(self.default_port)
        self.ports_list.textActivated.connect(self.select_port)

        self.baud_rate_spin = QSpinBox()
        self.baud_rate_spin.setRange(0, int(1e7))
        self.baud_rate_spin.setValue(self.rp_plot.sr_data.sr.baudrate) # type: ignore
        self.baud_rate_spin.valueChanged.connect(self.rp_plot.sr_data.update_baud_rate)

        self.roll_over_spin = QSpinBox()
        self.roll_over_spin.setRange(1, int(1e7))
        self.roll_over_spin.setValue(self.default_roll_over)
        self.roll_over_spin.valueChanged.connect(self.rp_plot.update_roll_over)

        update_ports_btn = QPushButton("Update Ports")
        update_ports_btn.clicked.connect(self.update_port_list)

        self.serial_group = QGroupBox("Serial Settings")
        serial_layout = QFormLayout(self.serial_group)
        serial_layout.addRow("Available Ports:", self.ports_list)
        serial_layout.addRow("Baud Rate:", self.baud_rate_spin)
        serial_layout.addRow("Roll Over:", self.roll_over_spin)
        serial_layout.addRow(update_ports_btn)

        # --- Plot Options ---
        self.max_y_spin = QDoubleSpinBox()
        self.max_y_spin.setDecimals(4)
        self.max_y_spin.setRange(-100, 100)
        self.max_y_spin.setValue(self.rp_plot.plot_b.y_range.end)
        self.max_y_spin.setSingleStep(0.01)

        self.min_y_spin = QDoubleSpinBox()
        self.min_y_spin.setDecimals(4)
        self.min_y_spin.setRange(-100, 100)
        self.min_y_spin.setValue(self.rp_plot.plot_b.y_range.start)
        self.min_y_spin.setSingleStep(0.01)

        self.max_x_spin = QDoubleSpinBox()
        self.max_x_spin.setRange(-int(1e6), int(1e6))
        self.max_x_spin.setValue(self.rp_plot.plot_b.x_range.end)
        self.max_x_spin.setSingleStep(10)

        self.min_x_spin = QDoubleSpinBox()
        self.min_x_spin.setRange(-int(1e6), int(1e6))
        self.min_x_spin.setValue(self.rp_plot.plot_b.x_range.start)
        self.min_x_spin.setSingleStep(10)

        self.scatter_radio = QRadioButton("Scatter Plot")
        self.scatter_radio.setChecked(self.rp_plot.scatter_plot)
        self.scatter_radio.toggled.connect(self.rp_plot.change_scatter)

        # Export button
        export_button = QPushButton("Export CSV")
        export_button.clicked.connect(self.export_csv)

        # Testing button
        testing_button = QPushButton("Test Button")
        testing_button.clicked.connect(self.rp_plot.test_function)

        # Auto-Scale button
        auto_scale_button = QPushButton("Auto-Scale")
        auto_scale_button.clicked.connect(self.rp_plot.auto_scale)

        plot_options_group = QGroupBox("Voltage / Time : Range")
        plot_options_layout = QVBoxLayout(plot_options_group)

        plot_options_layout.addWidget(self.scatter_radio)

        spin_layout = QFormLayout()
        spin_layout.addRow("Max V:", self.max_y_spin)
        spin_layout.addRow("Min V:", self.min_y_spin)
        spin_layout.addRow("Max t:", self.max_x_spin)
        spin_layout.addRow("Min t:", self.min_x_spin)
        plot_options_layout.addLayout(spin_layout)
        plot_options_layout.addWidget(export_button)
        plot_options_layout.addWidget(auto_scale_button)
        # plot_options_layout.addWidget(testing_button)

        dpad_layout = QGridLayout()
        dpad_layout.addWidget(QLabel("Scale Control"), 0, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        up_btn = QPushButton("+V")
        down_btn = QPushButton("-V")
        left_btn = QPushButton("-T")
        right_btn = QPushButton("+T")

        dpad_layout.addWidget(up_btn, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(down_btn, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(left_btn, 1, 0, 2, 1, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        dpad_layout.addWidget(right_btn, 1, 2, 2, 1, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        # Espaciado ajustado
        dpad_layout.setVerticalSpacing(2)
        dpad_layout.setHorizontalSpacing(10)

        plot_options_layout.addLayout(dpad_layout)

        def increase_voltage_scale():
            start, end = self.min_y_spin.value(), self.max_y_spin.value()
            mid = (start + end)/2
            new_range = (end-start)*1.1
            self.rp_plot.update_y_range(min_val=mid - new_range/2, max_val=mid + new_range/2)
            # self.min_y_spin.setValue(mid - new_range/2)
            # self.max_y_spin.setValue(mid + new_range/2)

        def decrease_voltage_scale():
            start, end = self.min_y_spin.value(), self.max_y_spin.value()
            mid = (start + end)/2
            new_range = (end-start)*0.9
            self.rp_plot.update_y_range(min_val=mid - new_range/2, max_val=mid + new_range/2)
            # self.min_y_spin.setValue(mid - new_range/2)
            # self.max_y_spin.setValue(mid + new_range/2)

        def increase_time_scale():
            start, end = self.min_x_spin.value(), self.max_x_spin.value()
            mid = (start + end)/2
            new_range = (end-start)*1.1
            self.rp_plot.update_x_range(min_val=mid - new_range/2, max_val=mid + new_range/2)
            # self.min_x_spin.setValue(mid - new_range/2)
            # self.max_x_spin.setValue(mid + new_range/2)

        def decrease_time_scale():
            start, end = self.min_x_spin.value(), self.max_x_spin.value()
            mid = (start + end)/2
            new_range = (end-start)*0.9
            self.rp_plot.update_x_range(min_val=mid - new_range/2, max_val=mid + new_range/2)
            # self.min_x_spin.setValue(mid - new_range/2)
            # self.max_x_spin.setValue(mid + new_range/2)

        # Conectar D-pad
        up_btn.clicked.connect(increase_voltage_scale)
        down_btn.clicked.connect(decrease_voltage_scale)
        left_btn.clicked.connect(decrease_time_scale)
        right_btn.clicked.connect(increase_time_scale)

        # Sincronización Spinboxes ↔ Plot
        self.min_y_spin.editingFinished.connect(lambda: self.rp_plot.update_y_range(min_val=self.min_y_spin.value(), max_val=self.max_y_spin.value()))
        self.max_y_spin.editingFinished.connect(lambda: self.rp_plot.update_y_range(min_val=self.min_y_spin.value(), max_val=self.max_y_spin.value()))
        self.min_x_spin.editingFinished.connect(lambda: self.rp_plot.update_x_range(min_val=self.min_x_spin.value(), max_val=self.max_x_spin.value()))
        self.max_x_spin.editingFinished.connect(lambda: self.rp_plot.update_x_range(min_val=self.min_x_spin.value(), max_val=self.max_x_spin.value()))

        # --- Logos ---
        logo_layout = QHBoxLayout()
        self.uni_logo = QLabel()
        self.uni_logo.setPixmap(QPixmap("assets/ua.png").scaledToWidth(75))
        self.group_logo = QLabel()
        self.group_logo.setPixmap(QPixmap("assets/geoel.png").scaledToWidth(100))
        logo_layout.addWidget(self.uni_logo)
        logo_layout.addWidget(self.group_logo)

        sidebar_layout.addWidget(generator_group)
        sidebar_layout.addWidget(self.acquiring_group)
        sidebar_layout.addWidget(self.serial_group)
        sidebar_layout.addWidget(plot_options_group)
        sidebar_layout.addStretch()
        sidebar_layout.addLayout(logo_layout)

        main_layout.addLayout(sidebar_layout, stretch=0)
        main_layout.addWidget(self.browser, stretch=1)

        self.setCentralWidget(self.central_widget)

    def select_port(self, port_selected):
        self.rp_plot.sr_data.select_port(port_selected)
        if port_selected != "None":
            self.rp_plot.reading = True

    def update_port_list(self):
        self.ports_list.clear()
        self.ports_list.addItem("None")
        self.ports_list.addItems(self.rp_plot.sr_data.search()) 

    def update_y_range(self):
        self.rp_plot.update_y_range(
            min_val=self.min_y_spin.value(),
            max_val=self.max_y_spin.value()
        )
    
    def update_x_range(self):
        self.rp_plot.update_x_range(
            min_val=self.min_x_spin.value(),
            max_val=self.max_x_spin.value()
        )

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
        
    def show_status_bar_msg(self, msg, time=3000):
        self.status_bar.showMessage(msg, time)

    def timer_multiprocess(self):
        if self.rp_plot.rp_connected:
            self.status_label.setStyleSheet("background-color: green; border-radius: 8px;")
        else:
            self.status_label.setStyleSheet("background-color: red; border-radius: 8px;")

        if self.max_x_spin.hasFocus() or self.min_x_spin.hasFocus() or self.max_y_spin.hasFocus() or self.min_y_spin.hasFocus():
            return  # No actualizar si alguno tiene el foco de edición
        self.max_x_spin.setValue(self.rp_plot.plot_b.x_range.end)
        self.min_x_spin.setValue(self.rp_plot.plot_b.x_range.start)
        self.max_y_spin.setValue(self.rp_plot.plot_b.y_range.end)
        self.min_y_spin.setValue(self.rp_plot.plot_b.y_range.start)

    def reset_all(self):
        self.rp_plot.sr_data.close() 
        self.ports_list.setCurrentText("None")
        self.baud_rate_spin.setValue(self.default_baudrate)
        self.max_y_spin.setValue(self.default_y_max)
        self.min_y_spin.setValue(self.default_y_min)

    def export_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV File",
            "",
            "CSV Files (*.csv);;All Files (*)"
        )

        if not file_path.lower().endswith(".csv"):
            file_path += ".csv"

        if not file_path:
            return

        try:
            data = self.rp_plot.save_current_data(file_path)
            QMessageBox.information(self, "Success", f"CSV file saved:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def create_menu_bar(self):
        menu_bar = self.menuBar()
        # File menu
        file_menu = menu_bar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Edit menu (empty for now)
        edit_menu = menu_bar.addMenu("Edit")

        # Tools menu
        tools_menu = menu_bar.addMenu("Tools")

        update_ports_action = QAction("Update Ports", self)
        update_ports_action.triggered.connect(self.update_port_list)

        reset_all_action = QAction("Reset application", self)
        reset_all_action.triggered.connect(self.reset_all)

        osci_mode_action = QAction("Change to oscillocope mode", self, checkable=True)
        osci_mode_action.triggered.connect(self.change_osci_mode)

        real_time_mode_action = QAction("Change to real time mode", self, checkable=True)
        real_time_mode_action.triggered.connect(self.change_to_real_time_mode)

        mode_group = QActionGroup(self)
        mode_group.addAction(osci_mode_action)
        mode_group.addAction(real_time_mode_action)
        mode_group.setExclusive(True)

        mode_menu = tools_menu.addMenu("Adquisition mode")
        mode_menu.addActions([osci_mode_action, real_time_mode_action])

        osci_mode_action.setChecked(self.rp_plot.osci)

        tools_menu.addActions([
            update_ports_action,
            reset_all_action,
            ])

        # Help menu
        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(lambda: print("Oscilloscope app v1.0"))
        help_menu.addAction(about_action)
