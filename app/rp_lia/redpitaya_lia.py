from __future__ import annotations

import math
import socket
import struct
from typing import Any


class RedPitayaLIA:
    """
    TCP client for the FPGA Lock-In Amplifier register server.

    The server runs on the Red Pitaya at TCP port 5001 and exposes:

        0x00  PHASE_INC       read/write
        0x04  PHASE_OFFSET    read/write
        0x08  I / X_OUT       read-only, signed int32
        0x0C  Q / Y_OUT       read-only, signed int32
        0x10  SOFT_RESET      write-only
        0x14  K               read/write
        0x18  N_STAGES        read/write
        0x1C  NOISE           read/write

    Protocol:

        READ:
            b"R" + uint32(offset)
            <- uint32(value)

        WRITE:
            b"W" + uint32(offset) + uint32(value)
            <- b"\\x00" on success
            <- b"\\x01" on error
    """

    # Register offsets
    REG_PHASE_INC = 0x00
    REG_PHASE_OFFSET = 0x04
    REG_I = 0x08
    REG_Q = 0x0C
    REG_SOFT_RESET = 0x10
    REG_K = 0x14
    REG_N_STAGES = 0x18
    REG_NOISE = 0x1C

    # Public limits used by the PySide6 LIA UI.
    MIN_K = 0
    MAX_K = 20

    MIN_STAGES = 1
    MAX_STAGES = 4

    MIN_NOISE = 0
    MAX_NOISE = 15

    # FPGA/reference constants.
    # Keep these consistent with the DDS implementation.
    PHASE_BITS = 32
    SAMPLE_RATE_HZ = 125_000_000.0
    # Compatibility name used by the existing LockInAmp UI.
    CLK_HZ = SAMPLE_RATE_HZ
    TWO_PI = 2.0 * math.pi

    def __init__(
        self,
        host: str = "10.42.0.100",
        port: int = 5001,
        timeout: float = 5.0,
    ):
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)

        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self.connected:
            return

        self._sock = socket.create_connection(
            (self.host, self.port),
            timeout=self.timeout,
        )

        # Keep a timeout on every subsequent read/write.
        self._sock.settimeout(self.timeout)

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
            raise RuntimeError(
                "RedPitayaLIA is not connected"
            )

        return self._sock

    # ------------------------------------------------------------------
    # TCP protocol
    # ------------------------------------------------------------------

    @staticmethod
    def _recv_exact(
        sock: socket.socket,
        size: int,
    ) -> bytes:
        data = bytearray()

        while len(data) < size:
            chunk = sock.recv(size - len(data))

            if not chunk:
                raise ConnectionError(
                    "Red Pitaya LIA server closed the connection"
                )

            data.extend(chunk)

        return bytes(data)

    @staticmethod
    def _signed32(value: int) -> int:
        """
        Convert an unsigned 32-bit register value into signed int32.

        Examples:
            0x00000001 ->  1
            0xFFFFFFFE -> -2
            0xFFFFFFFF -> -1
        """
        value &= 0xFFFFFFFF

        if value & 0x80000000:
            return value - 0x1_0000_0000

        return value

    @staticmethod
    def _uint32(value: int) -> int:
        return int(value) & 0xFFFFFFFF

    def _read_reg(self, offset: int) -> int:
        sock = self._require_connection()

        sock.sendall(
            b"R" + struct.pack(
                "<I",
                self._uint32(offset),
            )
        )

        raw = self._recv_exact(sock, 4)

        return struct.unpack(
            "<I",
            raw,
        )[0]

    def _write_reg(
        self,
        offset: int,
        value: int,
    ) -> None:
        sock = self._require_connection()

        sock.sendall(
            b"W"
            + struct.pack(
                "<I",
                self._uint32(offset),
            )
            + struct.pack(
                "<I",
                self._uint32(value),
            )
        )

        status = self._recv_exact(sock, 1)

        if status != b"\x00":
            raise RuntimeError(
                "LIA register write failed: "
                f"{status!r}"
            )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @classmethod
    def frequency_to_phase_inc(
        cls,
        frequency_hz: float,
    ) -> int:
        frequency_hz = float(frequency_hz)

        if not (
            0.0
            <= frequency_hz
            <= cls.SAMPLE_RATE_HZ / 2.0
        ):
            raise ValueError(
                "Frequency must be between "
                f"0 and {cls.SAMPLE_RATE_HZ / 2.0:g} Hz"
            )

        phase_inc = round(
            frequency_hz
            * (1 << cls.PHASE_BITS)
            / cls.SAMPLE_RATE_HZ
        )

        return cls._uint32(phase_inc)

    @classmethod
    def phase_inc_to_frequency(
        cls,
        phase_inc: int,
    ) -> float:
        return (
            cls._uint32(phase_inc)
            * cls.SAMPLE_RATE_HZ
            / (1 << cls.PHASE_BITS)
        )

    def set_frequency(
        self,
        frequency_hz: float,
    ) -> None:
        phase_inc = self.frequency_to_phase_inc(
            frequency_hz
        )
        self._write_reg(
            self.REG_PHASE_INC,
            phase_inc,
        )

    def get_frequency(self) -> float:
        phase_inc = self._read_reg(
            self.REG_PHASE_INC
        )

        return self.phase_inc_to_frequency(
            phase_inc
        )

    def set_phase_offset(
        self,
        phase_rad: float,
    ) -> None:
        phase_rad = float(phase_rad)

        # DDS phase accumulator is represented by a 32-bit unsigned word.
        phase_word = round(
            (phase_rad / self.TWO_PI)
            * (1 << self.PHASE_BITS)
        )

        self._write_reg(
            self.REG_PHASE_OFFSET,
            phase_word,
        )

    def get_phase_offset(self) -> float:
        phase_word = self._read_reg(
            self.REG_PHASE_OFFSET
        )

        signed_word = self._signed32(
            phase_word
        )

        return (
            signed_word
            * self.TWO_PI
            / (1 << self.PHASE_BITS)
        )

    def set_k(self, k: int) -> None:
        k = int(k)

        if not (
            self.MIN_K
            <= k
            <= self.MAX_K
        ):
            raise ValueError(
                f"K must be between "
                f"{self.MIN_K} and {self.MAX_K}"
            )

        self._write_reg(
            self.REG_K,
            k,
        )

    def get_k(self) -> int:
        return self._read_reg(
            self.REG_K
        )

    def set_n_stages(
        self,
        n_stages: int,
    ) -> None:
        n_stages = int(n_stages)

        if not (
            self.MIN_STAGES
            <= n_stages
            <= self.MAX_STAGES
        ):
            raise ValueError(
                "Number of IIR stages must be between "
                f"{self.MIN_STAGES} and "
                f"{self.MAX_STAGES}"
            )

        self._write_reg(
            self.REG_N_STAGES,
            n_stages,
        )

    def get_n_stages(self) -> int:
        return self._read_reg(
            self.REG_N_STAGES
        )

    def set_noise(
        self,
        noise: int,
    ) -> None:
        noise = int(noise)

        if not (
            self.MIN_NOISE
            <= noise
            <= self.MAX_NOISE
        ):
            raise ValueError(
                "Noise level must be between "
                f"{self.MIN_NOISE} and "
                f"{self.MAX_NOISE}"
            )

        self._write_reg(
            self.REG_NOISE,
            noise,
        )

    def get_noise(self) -> int:
        return self._read_reg(
            self.REG_NOISE
        )

    def soft_reset(self) -> None:
        self._write_reg(
            self.REG_SOFT_RESET,
            1,
        )

    def configure(
        self,
        frequency_hz: float | None = None,
        phase_offset_rad: float | None = None,
        k: int | None = None,
        n_stages: int | None = None,
        noise: int | None = None,
    ) -> None:
        if frequency_hz is not None:
            self.set_frequency(
                frequency_hz
            )

        if phase_offset_rad is not None:
            self.set_phase_offset(
                phase_offset_rad
            )

        if k is not None:
            self.set_k(k)

        if n_stages is not None:
            self.set_n_stages(
                n_stages
            )

        if noise is not None:
            self.set_noise(
                noise
            )

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def get_i(self) -> int:
        raw = self._read_reg(self.REG_I)
        return self._signed32(raw)

    def get_q(self) -> int:
        raw = self._read_reg(self.REG_Q)
        return self._signed32(raw)

    def get_measurement(self) -> dict[str, float | int]:
        """
        Read I/Q and calculate R and phase.

        I and Q are signed FPGA outputs.

        R     = sqrt(I² + Q²)
        phase = atan2(Q, I)
        """
        i = self.get_i()
        q = self.get_q()

        r = math.hypot(
            float(i),
            float(q),
        )

        phase = math.atan2(
            float(q),
            float(i),
        )

        return {
            "I": i,
            "Q": q,
            "R": r,
            "phase": phase,
        }

    def read(self) -> dict[str, float | int]:
        """Alias useful to acquisition/plotting code."""
        return self.get_measurement()

    # ------------------------------------------------------------------
    # Convenience helpers for the GUI
    # ------------------------------------------------------------------

    def get_configuration(self) -> dict[str, Any]:
        return {
            "frequency_hz": self.get_frequency(),
            "phase_offset_rad": self.get_phase_offset(),
            "k": self.get_k(),
            "n_stages": self.get_n_stages(),
            "noise": self.get_noise(),
        }