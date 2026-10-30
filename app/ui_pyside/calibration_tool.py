from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

# 14-bit signed ADC: 8192 counts correspond to the input full scale.
FULL_SCALE_COUNTS = 8192

# Input range selected by the LV/HV jumper next to each Red Pitaya input.
INPUT_RANGES = (
    ("LV jumper (\u00b11 V)", 1.0),
    ("HV jumper (\u00b120 V)", 20.0),
)
CUSTOM_RANGE_LABEL = "Custom"

# Largest sample-to-sample spread (sigma / mean) accepted without a warning.
MAX_RELATIVE_STD = 0.02

# Below this fraction of the full scale the ADC resolution is poorly used.
LOW_SIGNAL_FRACTION = 0.05


@dataclass
class CalibrationResult:
    r_mean: float
    r_std: float
    n_samples: int
    peak_voltage: float
    full_scale_voltage: float
    b2v: float  # volts (peak) per count of R
    r_at_full_scale: float  # R value that corresponds to 8192 counts

    @property
    def relative_std(self) -> float:
        return self.r_std / self.r_mean if self.r_mean else float("inf")

    @property
    def full_scale_fraction(self) -> float:
        return self.peak_voltage / self.full_scale_voltage


def amplitude_error(peak_voltage: float, full_scale_voltage: float) -> str | None:
    """Return why this amplitude cannot be used for a calibration, if so."""
    if peak_voltage <= 0.0:
        return "The signal amplitude must be greater than zero"
    if full_scale_voltage <= 0.0:
        return "The full-scale voltage must be greater than zero"
    if peak_voltage > full_scale_voltage:
        return (
            f"The signal ({peak_voltage:g} V peak) exceeds the input full "
            f"scale ({full_scale_voltage:g} V), so the ADC would clip. Lower "
            "the amplitude or select a larger input range."
        )
    return None


def compute_calibration(
    r_samples: list[float],
    peak_voltage: float,
    full_scale_voltage: float,
) -> CalibrationResult:
    """B2V = known peak amplitude / measured R.

    r_at_full_scale is the R the LIA would report for an input whose
    amplitude equals the ADC full scale (the value that maps to 8192).
    """
    if not r_samples:
        raise ValueError("No R samples were collected")

    error = amplitude_error(peak_voltage, full_scale_voltage)
    if error:
        raise ValueError(error)

    r_mean = statistics.fmean(r_samples)
    if r_mean <= 0.0:
        raise ValueError(
            "Measured R is zero - check the signal and reference connections"
        )

    r_std = statistics.stdev(r_samples) if len(r_samples) > 1 else 0.0
    b2v = peak_voltage / r_mean

    return CalibrationResult(
        r_mean=r_mean,
        r_std=r_std,
        n_samples=len(r_samples),
        peak_voltage=peak_voltage,
        full_scale_voltage=full_scale_voltage,
        b2v=b2v,
        r_at_full_scale=full_scale_voltage / b2v,
    )


class CalibrationDialog(QDialog):
    """Small, non-modal window that measures R for a known signal and
    derives the binary-to-volts constant (B2V).

    `lia` is the already-connected RedPitayaLIA client of the LIA window.
    Sampling runs on the GUI thread through a QTimer, exactly like the main
    window's own polling, so the two never touch the socket concurrently.

    B2V is only valid for the LV/HV jumper position it was measured with:
    the jumper changes how many ADC counts a given voltage produces.
    """

    b2v_calculated = Signal(float)

    SAMPLE_INTERVAL_MS = 20

    # (label, factor that converts the entered value to peak volts)
    AMPLITUDE_UNITS = (
        ("V peak", 1.0),
        ("Vpp", 0.5),
        ("V rms", math.sqrt(2.0)),
    )

    def __init__(self, lia, parent=None):
        super().__init__(parent)
        self.lia = lia

        self.setWindowTitle("Calibration Tool")
        self.setMinimumWidth(400)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._samples: list[float] = []
        self._target_samples = 0
        self._settle_deadline = 0.0
        self._running = False
        self._peak_voltage = 0.0
        self._full_scale_voltage = 1.0
        self._range_text = ""
        self._saved_filter: tuple[int, int] | None = None
        self._b2v: float | None = None

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        help_label = QLabel(
            "Feed the same signal from the generator to both the reference "
            "and the signal input (same frequency and phase), select the "
            "input range that matches the jumpers, enter the amplitude, "
            "then press Measure."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        # --- Inputs ---------------------------------------------------- #
        inputs = QGroupBox("Input signal")
        form = QFormLayout(inputs)

        amplitude_row = QHBoxLayout()
        self.amplitude_spin = QDoubleSpinBox()
        self.amplitude_spin.setDecimals(5)
        self.amplitude_spin.setRange(0.00001, 100.0)
        self.amplitude_spin.setValue(1.0)
        self.amplitude_spin.setSuffix(" V")
        amplitude_row.addWidget(self.amplitude_spin, 1)

        self.unit_combo = QComboBox()
        for label, _factor in self.AMPLITUDE_UNITS:
            self.unit_combo.addItem(label)
        amplitude_row.addWidget(self.unit_combo)
        form.addRow("Amplitude:", amplitude_row)

        self.range_combo = QComboBox()
        for label, volts in INPUT_RANGES:
            self.range_combo.addItem(label, volts)
        self.range_combo.addItem(CUSTOM_RANGE_LABEL, None)
        self.range_combo.setToolTip(
            "Must match the LV/HV jumper of the input being calibrated."
        )
        self.range_combo.currentIndexChanged.connect(self._on_range_changed)
        form.addRow("Input range:", self.range_combo)

        self.full_scale_spin = QDoubleSpinBox()
        self.full_scale_spin.setDecimals(3)
        self.full_scale_spin.setRange(0.001, 100.0)
        self.full_scale_spin.setSuffix(" V")
        self.full_scale_spin.setToolTip(
            f"Input voltage that corresponds to {FULL_SCALE_COUNTS} counts."
        )
        form.addRow(f"Full scale ({FULL_SCALE_COUNTS} counts):", self.full_scale_spin)

        self.settle_spin = QDoubleSpinBox()
        self.settle_spin.setDecimals(1)
        self.settle_spin.setRange(0.0, 600.0)
        self.settle_spin.setValue(10.0)
        self.settle_spin.setSuffix(" s")
        self.settle_spin.setToolTip(
            "Wait before sampling so the IIR filters can settle. Stronger "
            "filtering needs a longer wait."
        )
        form.addRow("Settling time:", self.settle_spin)

        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(10, 5000)
        self.samples_spin.setValue(100)
        form.addRow("Samples:", self.samples_spin)

        # Strongest filtering the FPGA supports, read from the LIA client
        # instead of being hardcoded here.
        self.max_filter_checkbox = QCheckBox(
            f"Use maximum filtering while measuring "
            f"(K = {self.lia.MAX_K}, {self.lia.MAX_STAGES} stages)"
        )
        self.max_filter_checkbox.setChecked(True)
        self.max_filter_checkbox.setToolTip(
            "Temporarily sets the IIR filter to its maximum strength for "
            "the measurement and restores your settings afterwards."
        )
        form.addRow(self.max_filter_checkbox)

        layout.addWidget(inputs)
        self._on_range_changed(self.range_combo.currentIndex())

        # --- Measure --------------------------------------------------- #
        self.measure_button = QPushButton("Measure")
        self.measure_button.clicked.connect(self._on_measure_clicked)
        layout.addWidget(self.measure_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # --- Results --------------------------------------------------- #
        results = QGroupBox("Result")
        results_form = QFormLayout(results)

        self.range_result_label = QLabel("-")
        self.r_mean_label = QLabel("-")
        self.r_std_label = QLabel("-")
        self.b2v_label = QLabel("-")
        self.r_full_scale_label = QLabel("-")
        self.b2v_label.setStyleSheet("font-weight: bold;")

        results_form.addRow("Calibrated for:", self.range_result_label)
        results_form.addRow("R (mean):", self.r_mean_label)
        results_form.addRow("R (std dev):", self.r_std_label)
        results_form.addRow("B2V (V / count of R):", self.b2v_label)
        results_form.addRow(
            f"R at {FULL_SCALE_COUNTS} counts:", self.r_full_scale_label
        )
        layout.addWidget(results)

        # --- Footer ---------------------------------------------------- #
        footer = QHBoxLayout()
        self.copy_button = QPushButton("Copy B2V")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._on_copy_clicked)
        footer.addWidget(self.copy_button)
        footer.addStretch(1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @property
    def b2v(self) -> float | None:
        """Last computed B2V, or None if no measurement has finished yet."""
        return self._b2v

    def _on_range_changed(self, index: int) -> None:
        volts = self.range_combo.itemData(index)
        is_custom = volts is None
        if not is_custom:
            self.full_scale_spin.setValue(volts)
        self.full_scale_spin.setEnabled(is_custom)

    def _range_description(self) -> str:
        label = self.range_combo.currentText()
        if self.range_combo.currentData() is None:
            return f"{label} ({self.full_scale_spin.value():g} V full scale)"
        return label

    def _peak_amplitude(self) -> float:
        _label, factor = self.AMPLITUDE_UNITS[self.unit_combo.currentIndex()]
        return self.amplitude_spin.value() * factor

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.amplitude_spin,
            self.unit_combo,
            self.range_combo,
            self.settle_spin,
            self.samples_spin,
            self.max_filter_checkbox,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._on_range_changed(self.range_combo.currentIndex())
        else:
            self.full_scale_spin.setEnabled(False)

    # ------------------------------------------------------------------ #
    # Filter strength
    # ------------------------------------------------------------------ #
    def _apply_max_filter(self) -> None:
        """Save the current IIR settings and switch to the strongest ones."""
        self._saved_filter = (self.lia.get_k(), self.lia.get_n_stages())
        self.lia.set_k(self.lia.MAX_K)
        self.lia.set_n_stages(self.lia.MAX_STAGES)

    def _restore_filter(self) -> str | None:
        """Put the IIR settings back; returns a message if that failed."""
        saved = self._saved_filter
        if saved is None:
            return None
        self._saved_filter = None
        try:
            self.lia.set_k(saved[0])
            self.lia.set_n_stages(saved[1])
        except Exception as exc:
            return f"could not restore the previous filter settings ({exc})"
        return None

    # ------------------------------------------------------------------ #
    # Measurement
    # ------------------------------------------------------------------ #
    def _on_measure_clicked(self) -> None:
        if self._running:
            self._stop("Measurement cancelled.")
            return

        if not self.lia.connected:
            self.status_label.setText(
                "The LIA is not connected - connect to the board first."
            )
            return

        peak = self._peak_amplitude()
        full_scale = self.full_scale_spin.value()
        error = amplitude_error(peak, full_scale)
        if error:
            self.status_label.setText(error)
            return

        if self.max_filter_checkbox.isChecked():
            try:
                self._apply_max_filter()
            except Exception as exc:
                restore_error = self._restore_filter()
                message = f"Could not set the maximum filter: {exc}"
                if restore_error:
                    message += f" Warning: {restore_error}."
                self.status_label.setText(message)
                return

        self._peak_voltage = peak
        self._full_scale_voltage = full_scale
        self._range_text = self._range_description()
        self._target_samples = self.samples_spin.value()
        self._samples = []
        self._settle_deadline = time.monotonic() + self.settle_spin.value()

        self._running = True
        self._set_inputs_enabled(False)
        self.copy_button.setEnabled(False)
        self.measure_button.setText("Cancel")
        self.progress.setValue(0)
        self._timer.start(self.SAMPLE_INTERVAL_MS)

    def _stop(self, message: str) -> None:
        self._timer.stop()
        self._running = False

        restore_error = self._restore_filter()
        if restore_error:
            message = f"{message} Warning: {restore_error}."

        self._set_inputs_enabled(True)
        self.measure_button.setText("Measure")
        self.status_label.setText(message)

    def _on_tick(self) -> None:
        remaining = self._settle_deadline - time.monotonic()
        if remaining > 0.0:
            self.status_label.setText(f"Settling... {remaining:.1f} s")
            return

        try:
            measurement = self.lia.get_measurement()
        except Exception as exc:
            self._stop(f"Read error: {exc}")
            return

        self._samples.append(float(measurement["R"]))
        done = len(self._samples)
        self.progress.setValue(round(100 * done / self._target_samples))
        self.status_label.setText(
            f"Sampling... {done} / {self._target_samples}"
        )

        if done >= self._target_samples:
            self._finish()

    def _finish(self) -> None:
        try:
            result = compute_calibration(
                self._samples,
                self._peak_voltage,
                self._full_scale_voltage,
            )
        except ValueError as exc:
            self._stop(str(exc))
            return

        self._b2v = result.b2v
        self._show_result(result)
        self.copy_button.setEnabled(True)

        notes = []
        if result.relative_std > MAX_RELATIVE_STD:
            notes.append(
                f"R is fluctuating (sigma/mean = "
                f"{100 * result.relative_std:.1f} %). Increase the settling "
                "time or check that the reference and the signal are locked."
            )
        if result.full_scale_fraction < LOW_SIGNAL_FRACTION:
            notes.append(
                f"The signal is only {100 * result.full_scale_fraction:.1f} % "
                "of the full scale, so the ADC resolution is poorly used. A "
                "larger amplitude (or the LV range) gives a better calibration."
            )

        self._stop("Done." if not notes else "Done, but: " + " ".join(notes))
        self.b2v_calculated.emit(result.b2v)

    def _show_result(self, result: CalibrationResult) -> None:
        self.range_result_label.setText(self._range_text)
        self.r_mean_label.setText(f"{result.r_mean:,.2f}")
        self.r_std_label.setText(
            f"{result.r_std:,.2f}  ({100 * result.relative_std:.2f} %)"
        )
        self.b2v_label.setText(f"{result.b2v:.6e}")
        self.r_full_scale_label.setText(f"{result.r_at_full_scale:,.1f}")

    def _on_copy_clicked(self) -> None:
        if self._b2v is None:
            return
        QApplication.clipboard().setText(f"{self._b2v:.9e}")
        self.status_label.setText("B2V copied to the clipboard.")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._running = False
        self._restore_filter()
        event.accept()