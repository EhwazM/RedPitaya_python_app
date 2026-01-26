from PySide6.QtWidgets import (
    QWidget, QDoubleSpinBox, QSpinBox,
    QComboBox, QFormLayout, QCheckBox
)
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
        self.channel_on.setChecked(False)
        self.show_plot.setChecked(False)
        self.emit_values()