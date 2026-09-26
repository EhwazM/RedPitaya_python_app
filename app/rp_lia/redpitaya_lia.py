from __future__ import annotations

import math
import socket
import struct
from typing import Any


class RedPitayaLIA:
    """TCP client for the FPGA Lock-In Amplifier register server."""

    REG_PHASE_INC = 0x00
    REG_PHASE_OFFSET = 0x04
    REG_I = 0x08
    REG_Q = 0x0C
    REG_SOFT_RESET = 0x10
    REG_K = 0x14
    REG_N_STAGES = 0x18
    REG_NOISE = 0x1C
    REG_EXT_REF = 0x20
    REG_ZC_THRESH = 0x24
    REG_PERIOD = 0x28

    MIN_K = 0
    MAX_K = 20
    MIN_STAGES = 1
    MAX_STAGES = 4
    MIN_NOISE = 0
    MAX_NOISE = 15
    MIN_ZC_THRESH = -8192
    MAX_ZC_THRESH = 8191

    # --- Raw-count -> volts conversion --------------------------------------
    # The ADC itself is 14-bit signed (this is what ZC_THRESH is masked to):
    # raw codes run -8192..8191 for the configured ADC_VOLTAGE_RANGE.
    ADC_BITS = 14
    ADC_FULL_SCALE_COUNTS = 1 << (ADC_BITS - 1)  # 8192
    ADC_VOLTAGE_RANGE = 1.0  # Volts, 0-to-peak, at full scale; 20.0 if the HV jumper is set

    # Nominal I/Q/R scale: 1 V (0-to-peak) corresponds to a full-scale
    # reading of ADC_FULL_SCALE_COUNTS (8192) raw counts. This is the
    # intended design value once the demodulator's output scaling is
    # correctly normalized; if you have empirical calibration data instead
    # (e.g. because the firmware fix hasn't been applied yet), override
    # this via calibrate_volts_per_count() or by setting it directly.
    volts_per_count: float = ADC_VOLTAGE_RANGE / ADC_FULL_SCALE_COUNTS

    PHASE_BITS = 32
    SAMPLE_RATE_HZ = 125_000_000.0
    CLK_HZ = SAMPLE_RATE_HZ
    TWO_PI = 2.0 * math.pi

    def __init__(self, host: str = "10.42.0.100", port: int = 5001, timeout: float = 5.0):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self._sock: socket.socket | None = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self.connected:
            return
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        # Small request/response messages (5-byte reads, 4-byte
        # replies) are exactly the pattern Nagle's algorithm plus
        # delayed ACKs tends to stall for tens of milliseconds at a
        # time. Disabling Nagle sends each command immediately.
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def disconnect(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    close = disconnect

    def __enter__(self) -> "RedPitayaLIA":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()

    def _require_connection(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("RedPitayaLIA is not connected")
        return self._sock

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("Red Pitaya LIA server closed the connection")
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def _signed32(value: int) -> int:
        value &= 0xFFFFFFFF
        return value - 0x1_0000_0000 if value & 0x80000000 else value

    @staticmethod
    def _uint32(value: int) -> int:
        return int(value) & 0xFFFFFFFF

    def _read_reg(self, offset: int) -> int:
        sock = self._require_connection()
        sock.sendall(b"R" + struct.pack("<I", self._uint32(offset)))
        return struct.unpack("<I", self._recv_exact(sock, 4))[0]

    def _write_reg(self, offset: int, value: int) -> None:
        sock = self._require_connection()
        sock.sendall(
            b"W"
            + struct.pack("<I", self._uint32(offset))
            + struct.pack("<I", self._uint32(value))
        )
        status = self._recv_exact(sock, 1)
        if status != b"\x00":
            raise RuntimeError(f"LIA register write failed: {status!r}")

    @classmethod
    def frequency_to_phase_inc(cls, frequency_hz: float) -> int:
        frequency_hz = float(frequency_hz)
        if not 0.0 <= frequency_hz <= cls.SAMPLE_RATE_HZ / 2.0:
            raise ValueError(
                f"Frequency must be between 0 and {cls.SAMPLE_RATE_HZ / 2.0:g} Hz"
            )
        return cls._uint32(round(frequency_hz * (1 << cls.PHASE_BITS) / cls.SAMPLE_RATE_HZ))

    @classmethod
    def phase_inc_to_frequency(cls, phase_inc: int) -> float:
        return cls._uint32(phase_inc) * cls.SAMPLE_RATE_HZ / (1 << cls.PHASE_BITS)

    def set_frequency(self, frequency_hz: float) -> None:
        self._write_reg(self.REG_PHASE_INC, self.frequency_to_phase_inc(frequency_hz))

    def get_frequency(self) -> float:
        return self.phase_inc_to_frequency(self._read_reg(self.REG_PHASE_INC))

    def set_phase_offset(self, phase_rad: float) -> None:
        phase_word = round((float(phase_rad) / self.TWO_PI) * (1 << self.PHASE_BITS))
        self._write_reg(self.REG_PHASE_OFFSET, phase_word)

    def get_phase_offset(self) -> float:
        word = self._signed32(self._read_reg(self.REG_PHASE_OFFSET))
        return word * self.TWO_PI / (1 << self.PHASE_BITS)

    def set_k(self, k: int) -> None:
        k = int(k)
        if not self.MIN_K <= k <= self.MAX_K:
            raise ValueError(f"K must be between {self.MIN_K} and {self.MAX_K}")
        self._write_reg(self.REG_K, k)

    def get_k(self) -> int:
        return self._read_reg(self.REG_K)

    def set_n_stages(self, n_stages: int) -> None:
        n_stages = int(n_stages)
        if not self.MIN_STAGES <= n_stages <= self.MAX_STAGES:
            raise ValueError(f"Number of IIR stages must be between {self.MIN_STAGES} and {self.MAX_STAGES}")
        self._write_reg(self.REG_N_STAGES, n_stages)

    def get_n_stages(self) -> int:
        return self._read_reg(self.REG_N_STAGES)

    def set_noise(self, noise: int) -> None:
        noise = int(noise)
        if not self.MIN_NOISE <= noise <= self.MAX_NOISE:
            raise ValueError(f"Noise level must be between {self.MIN_NOISE} and {self.MAX_NOISE}")
        self._write_reg(self.REG_NOISE, noise)

    def get_noise(self) -> int:
        return self._read_reg(self.REG_NOISE)

    def set_ext_ref(self, enabled: bool) -> None:
        self._write_reg(self.REG_EXT_REF, 1 if enabled else 0)

    def get_ext_ref(self) -> bool:
        return bool(self._read_reg(self.REG_EXT_REF) & 0x1)

    def set_zc_threshold(self, threshold: int) -> None:
        threshold = int(threshold)
        if not self.MIN_ZC_THRESH <= threshold <= self.MAX_ZC_THRESH:
            raise ValueError(
                f"Zero-crossing threshold must be between {self.MIN_ZC_THRESH} and {self.MAX_ZC_THRESH}"
            )
        self._write_reg(self.REG_ZC_THRESH, threshold & 0x3FFF)

    def get_zc_threshold(self) -> int:
        raw = self._read_reg(self.REG_ZC_THRESH) & 0x3FFF
        return raw - 0x4000 if raw & 0x2000 else raw

    @classmethod
    def period_to_frequency(cls, period: int) -> float:
        period = int(period)
        return 0.0 if period <= 0 else cls.SAMPLE_RATE_HZ / period

    def get_period(self) -> int:
        return self._read_reg(self.REG_PERIOD) & 0xFFFFFF

    def get_external_frequency(self) -> float:
        return self.period_to_frequency(self.get_period())

    def soft_reset(self) -> None:
        self._write_reg(self.REG_SOFT_RESET, 1)

    def configure(
        self,
        frequency_hz: float | None = None,
        phase_offset_rad: float | None = None,
        k: int | None = None,
        n_stages: int | None = None,
        noise: int | None = None,
        ext_ref: bool | None = None,
        zc_threshold: int | None = None,
    ) -> None:
        if frequency_hz is not None:
            self.set_frequency(frequency_hz)
        if phase_offset_rad is not None:
            self.set_phase_offset(phase_offset_rad)
        if k is not None:
            self.set_k(k)
        if n_stages is not None:
            self.set_n_stages(n_stages)
        if noise is not None:
            self.set_noise(noise)
        if ext_ref is not None:
            self.set_ext_ref(ext_ref)
        if zc_threshold is not None:
            self.set_zc_threshold(zc_threshold)

    def get_i(self) -> int:
        return self._signed32(self._read_reg(self.REG_I))

    def get_q(self) -> int:
        return self._signed32(self._read_reg(self.REG_Q))

    def get_iq(self) -> tuple[int, int]:
        """Read I and Q in a single network round trip.

        Both read commands are sent back-to-back without waiting for
        a reply in between, then both 4-byte replies are read off in
        order. This halves the round trips compared to calling
        get_i() and get_q() separately, assuming the server processes
        commands strictly in the order they arrive and replies to
        each in turn (true for the simple sequential request/response
        protocol used here). If the server ever becomes concurrent or
        reordering, fall back to sequential get_i()/get_q() calls.
        """
        sock = self._require_connection()
        sock.sendall(
            b"R" + struct.pack("<I", self._uint32(self.REG_I))
            + b"R" + struct.pack("<I", self._uint32(self.REG_Q))
        )
        i_raw = struct.unpack("<I", self._recv_exact(sock, 4))[0]
        q_raw = struct.unpack("<I", self._recv_exact(sock, 4))[0]
        return self._signed32(i_raw), self._signed32(q_raw)

    @staticmethod
    def calculate_r_phase(i: float, q: float) -> tuple[float, float]:
        return math.hypot(float(i), float(q)), math.atan2(float(q), float(i))

    def get_measurement(self) -> dict[str, float | int]:
        i, q = self.get_iq()
        r, phase = self.calculate_r_phase(i, q)
        return {"I": i, "Q": q, "R": r, "phase": phase}

    def read(self) -> dict[str, float | int]:
        return self.get_measurement()

    @classmethod
    def raw_to_volts(cls, raw_count: float) -> float:
        """Convert a raw I/Q/R count to volts using the calibrated scale."""
        if cls.volts_per_count is None:
            raise RuntimeError(
                "volts_per_count is not set. Run calibrate_volts_per_count() "
                "with a known input amplitude, or set RedPitayaLIA.volts_per_count "
                "directly if the gain is known analytically."
            )
        return raw_count * cls.volts_per_count

    @classmethod
    def calibrate_volts_per_count(cls, known_amplitude_volts: float, measured_raw_r: float) -> float:
        """Derive and store volts_per_count from one calibration point.

        Drive the input with a sine wave of known peak amplitude at the
        reference frequency, let the filter settle, read R, then call:
            RedPitayaLIA.calibrate_volts_per_count(known_amplitude_volts, measured_r)
        For a more robust constant, take several (amplitude, R) pairs and
        fit a line instead of using a single point.
        """
        if measured_raw_r == 0:
            raise ValueError("measured_raw_r must be nonzero")
        cls.volts_per_count = known_amplitude_volts / measured_raw_r
        return cls.volts_per_count

    def get_measurement_volts(self) -> dict[str, float]:
        """Same as get_measurement(), but I/Q/R are converted to volts.
        Requires volts_per_count to be calibrated first."""
        m = self.get_measurement()
        return {
            "I": self.raw_to_volts(m["I"]),
            "Q": self.raw_to_volts(m["Q"]),
            "R": self.raw_to_volts(m["R"]),
            "phase": m["phase"],
        }

    def get_configuration(self) -> dict[str, Any]:
        period = self.get_period()
        return {
            "frequency_hz": self.get_frequency(),
            "phase_offset_rad": self.get_phase_offset(),
            "k": self.get_k(),
            "n_stages": self.get_n_stages(),
            "noise": self.get_noise(),
            "ext_ref": self.get_ext_ref(),
            "zc_threshold": self.get_zc_threshold(),
            "period": period,
            "external_frequency_hz": self.period_to_frequency(period),
        }