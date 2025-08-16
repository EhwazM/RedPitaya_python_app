from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QTabWidget, QDoubleSpinBox, QSpinBox,
    QComboBox, QPushButton, QSizePolicy, QFormLayout, QRadioButton
)
from PySide6.QtGui import QAction
from PySide6.QtWebEngineWidgets import QWebEngineView
from app.rp_plot.plot_setup import BokehPlot
from app.rp_data_acquisition.serial_data import SerialData

class GeneratorSettingsWidget(QWidget):
    def __init__(self, channel: int, on_change_callback):
        super().__init__()
        self.channel = channel
        self.on_change_callback = on_change_callback

        self.default_vpp = 1.5
        self.default_freq = int(1e4)
        self.default_waveform = "sine"

        layout = QFormLayout()

        self.vpp_spin = QDoubleSpinBox()
        self.vpp_spin.setRange(0, 2)
        self.vpp_spin.setDecimals(1)
        self.vpp_spin.setSingleStep(0.1)
        self.vpp_spin.setValue(1.5)
        self.vpp_spin.valueChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Vpp:", self.vpp_spin)

        self.freq_spin = QSpinBox()
        self.freq_spin.setRange(0, int(6.25e7))
        self.freq_spin.setSingleStep(100)
        self.freq_spin.setValue(int(1e4))
        self.freq_spin.valueChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Frequency:", self.freq_spin)

        self.waveform_combo = QComboBox()
        # self.waveform_combo.addItems(["sine", "sqr", "tri", "sweep", "dc"]) ## Serial-Base
        self.waveform_combo.addItems(['sine','square','triangle','ramp','noise'])
        self.waveform_combo.currentTextChanged.connect(self.emit_values)
        layout.addRow(f"CH{channel} Waveform:", self.waveform_combo)

        self.generate_button = QPushButton("Generate signal")
        self.generate_button.pressed.connect(self.emit_values)
        layout.addRow(self.generate_button)
        
        self.default_button = QPushButton("Default Values")
        self.default_button.pressed.connect(self.default_values)
        layout.addRow(self.default_button)

        self.setLayout(layout)

    def emit_values(self):
        self.on_change_callback(
            ch=self.channel,
            vpp=self.vpp_spin.value(),
            fq=self.freq_spin.value(),
            wf=self.waveform_combo.currentText()
        )

    def default_values(self):
        self.vpp_spin.setValue(self.default_vpp)
        self.freq_spin.setValue(self.default_freq)
        self.waveform_combo.setCurrentText(self.default_waveform)

class AcquisitionSettingsWidget(QWidget):
    def __init__(self, on_change_callback):
        super().__init__()
        self.on_change_callback = on_change_callback

        self.trigger_level = 0.0
        self.trigger_source = "CH1_PE"
        self.decimation_level = 8

        layout = QFormLayout()

        self.trigger_level_spin = QDoubleSpinBox()
        self.trigger_level_spin.setRange(-100, 100)
        self.trigger_level_spin.setValue(self.trigger_level)
        self.trigger_level_spin.valueChanged.connect(self.emit_trigger_settings)
        layout.addRow("Trigger Level:", self.trigger_level_spin)

        self.trigger_source_combo = QComboBox()
        self.trigger_source_combo.addItems(["CH1_PE", "CH2_PE", "NOW"])
        self.trigger_source_combo.setCurrentText(self.trigger_source)
        self.trigger_source_combo.currentTextChanged.connect(self.emit_trigger_settings)
        layout.addRow("Trigger Source:", self.trigger_source_combo)

        self.decimation_level_spin = QSpinBox()
        self.decimation_level_spin.setRange(1, 16)
        self.decimation_level_spin.setValue(self.decimation_level)
        self.decimation_level_spin.valueChanged.connect(self.emit_trigger_settings)
        layout.addRow("Decimation Level:", self.decimation_level_spin)

        self.setLayout(layout)

    def emit_trigger_settings(self):
        self.on_change_callback(
            trigger_level=self.trigger_level_spin.value(),
            trigger_source=self.trigger_source_combo.currentText(),
            decimation_level=self.decimation_level_spin.value()
        )

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

    def init_ui(self):
        self.central_widget = QWidget()
        main_layout = QHBoxLayout(self.central_widget)

        # Sidebar
        sidebar_layout = QVBoxLayout()

        # Serial Settings
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
        self.roll_over_spin.setRange(1,int(1e7))
        self.roll_over_spin.setValue(self.default_roll_over)
        self.roll_over_spin.valueChanged.connect(self.rp_plot.update_roll_over)

        update_ports_btn = QPushButton("Update Ports")
        update_ports_btn.clicked.connect(self.update_port_list)

        serial_group = QGroupBox("Serial Settings")
        serial_layout = QFormLayout(serial_group)
        serial_layout.addRow("Available Ports:", self.ports_list)
        serial_layout.addRow("Baud Rate:", self.baud_rate_spin)
        serial_layout.addRow("Roll_Over:", self.roll_over_spin)
        serial_layout.addRow(update_ports_btn)

        # Generator Tabs
        generator_tab = QTabWidget()
        generator_tab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)


        for ch in [1, 2]:
            gen_widget = GeneratorSettingsWidget(channel=ch, on_change_callback=self.rp_plot.generate_signal_scpi)
            generator_tab.addTab(gen_widget, f"CH{ch}")

        generator_group = QGroupBox("Generator Settings")
        generator_layout = QVBoxLayout(generator_group)
        generator_layout.addWidget(generator_tab)

        # Y Range Settings
        self.max_y_spin = QDoubleSpinBox()
        self.max_y_spin.setRange(-100, 100)
        self.max_y_spin.setValue(self.rp_plot.plot_b.y_range.end)
        self.max_y_spin.valueChanged.connect(self.update_y_range)

        self.min_y_spin = QDoubleSpinBox()
        self.min_y_spin.setRange(-100, 100)
        self.min_y_spin.setValue(self.rp_plot.plot_b.y_range.start)
        self.min_y_spin.valueChanged.connect(self.update_y_range)

        # X Range Settings
        self.max_x_spin = QDoubleSpinBox()
        self.max_x_spin.setRange(-10000, 10000)
        self.max_x_spin.setValue(self.rp_plot.plot_b.x_range.end)
        self.max_x_spin.valueChanged.connect(self.update_x_range)

        self.min_x_spin = QDoubleSpinBox()
        self.min_x_spin.setRange(-10000, 10000)
        self.min_x_spin.setValue(self.rp_plot.plot_b.x_range.start)
        self.min_x_spin.valueChanged.connect(self.update_x_range)

        self.scatter_radio = QRadioButton()
        self.scatter_radio.setChecked(self.rp_plot.scatter_plot)
        self.scatter_radio.toggled.connect(self.rp_plot.change_scatter)

        plot_options_group = QGroupBox("Voltage Range")
        plot_options_layout = QFormLayout(plot_options_group)
        plot_options_layout.addRow("Max V:", self.max_y_spin)
        plot_options_layout.addRow("Min V:", self.min_y_spin)
        plot_options_layout.addRow("Max t:", self.max_x_spin)
        plot_options_layout.addRow("Min t:", self.min_x_spin)
        plot_options_layout.addRow("Scatter:", self.scatter_radio)

        # Sidebar assembly
        sidebar_layout.addWidget(generator_group)
        sidebar_layout.addWidget(serial_group)
        sidebar_layout.addWidget(plot_options_group)

        # Add to main layout
        main_layout.addLayout(sidebar_layout)
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

    def change_to_real_time_mode(self):
        self.ports_list.setCurrentText(self.default_port)
        self.rp_plot.change_to_real_time_mode()
        self.rp_plot.reading = False
        self.max_x_spin.setEnabled(False)
        self.min_x_spin.setEnabled(False)
        self.max_y_spin.setValue(3.33)
        self.min_y_spin.setValue(0)
        self.update_y_range()
        

    def reset_all(self):
        self.rp_plot.sr_data.close() 
        self.ports_list.setCurrentText("None")
        self.baud_rate_spin.setValue(self.default_baudrate)
        self.max_y_spin.setValue(self.default_y_max)
        self.min_y_spin.setValue(self.default_y_min)

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

        osci_mode_action = QAction("Change to oscillocope mode", self)
        osci_mode_action.triggered.connect(self.change_osci_mode)

        real_time_mode_action = QAction("Change to real time mode", self)
        real_time_mode_action.triggered.connect(self.change_to_real_time_mode)

        tools_menu.addActions([
            update_ports_action,
            reset_all_action,
            osci_mode_action,
            real_time_mode_action
            ])

        # Help menu
        help_menu = menu_bar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(lambda: print("Oscilloscope app v1.0"))
        help_menu.addAction(about_action)

