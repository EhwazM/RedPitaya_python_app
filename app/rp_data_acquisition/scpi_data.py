from __future__ import annotations

import numpy as np
import app.redpitaya_scpi.redpitaya_scpi as scpi
import time

RING_SIZE = 16384          # samples per channel in the Red Pitaya acquisition buffer
ADC_CLOCK = 125e6


class ScpiData:
    def __init__(self, ip_address, port=5000, timeout=5.0):
        self.ip_address = ip_address
        self.port = port
        # No socket is opened here. The connection is made explicitly with
        # connect(), once the Red Pitaya's address is actually known and the
        # board is ready (constructing this object must never touch the network).
        self.timeout = timeout
        self.rp: scpi.scpi | None = None
        self.decimation = int(2**3)
        self.window = 8192            # samples returned per frame (centred on the trigger)
        self.last_triggered = True    # False if a software trigger found no edge (free-run frame)

    @property
    def has_socket(self) -> bool:
        """True once connect() has created an SCPI client (does not ping the board)."""
        return self.rp is not None

    def _client(self) -> scpi.scpi:
        """The live SCPI client, or ConnectionError if connect() hasn't run.
        Returning a non-Optional type here is what lets every method below use
        a plain local `rp` without Optional-member-access warnings."""
        rp = self.rp
        if rp is None:
            raise ConnectionError("Not connected to the Red Pitaya (call connect() first)")
        return rp

    def connect(self):
        self.close()  # drop any previous socket instead of leaking it
        # NOTE: scpi.scpi(host, timeout, port) -> pass the port by keyword
        self.rp = scpi.scpi(self.ip_address, timeout=self.timeout, port=self.port)

    def generate_signal(self, channel=1, frequency=15000, amplitude=0.75, offset=0.0, waveform='sine'):
        """
        Generate a waveform on channel {1|2}.

        Parameters
        ----------
        channel : 1 or 2
        frequency : int
            in Hz, 0 < frequency <= 62.5e6
        amplitude : float
            in Vpp, 0 <= amplitude <= 1.0
        offset : float
            in V, must satisfy |offset| + amplitude <= 1.0
        waveform : str
            one of 'sine','square','triangle', 'sawu', 'sawd', 'pwm', 'arbitrary', 'dc', 'dc_neg'
        """
        if channel not in (1,2):
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        rp = self._client()
        wf = waveform.lower()
        valid = {'sine','square','triangle','sawu','sawd','pwm','arbitrary','dc','dc_neg'}
        if wf not in valid:
            raise ValueError(f"waveform must be one of {valid}")
        if not (0 < frequency <= 62.5e6):
            raise ValueError("frequency out of range")
        if not (0 <= amplitude <= 1.0):
            raise ValueError("amplitude out of range")
        if abs(offset) + amplitude > 1.0:
            raise ValueError("offset+amplitude exceeds supply rails")

        print(f"Generating {waveform} signal on channel {channel} with frequency {frequency} Hz, amplitude {amplitude} Vpp, and offset {offset} V.")

        # Reset the channel and set the waveform parameters
        rp.tx_txt(f'SOUR{str(channel)}:FUNC:RESET')

        # Set the waveform type, frequency, amplitude, and offset
        rp.tx_txt(f'SOUR{str(channel)}:FUNC {str(waveform.upper())}')
        rp.tx_txt(f'SOUR{str(channel)}:FREQ:FIX {str(frequency)}')
        rp.tx_txt(f'SOUR{str(channel)}:VOLT {str(amplitude)}')
        rp.tx_txt(f'SOUR{str(channel)}:VOLT:OFFS {str(offset)}')
        rp.tx_txt(f"SOUR{channel}:TRIG:INT")

        # Enable the output
        rp.tx_txt(f'OUTPUT{str(channel)}:STATE ON')

    def trigger_generation(self):
        rp = self._client()
        rp.tx_txt(f'SOUR:TRIG:INT')

    def stop_signal(self, channel=1):
        """
        Stop the signal generation on the specified channel.
        """
        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        rp = self.rp
        if rp is None or not rp._socket:
            print("Not connected to Red Pitaya")
            return
        rp.tx_txt(f'OUTPUT{channel}:STATE OFF')

    def reset(self, channel=1):
        """
        Reset the specified channel.
        """

        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        rp = self._client()
        rp.tx_txt(f'SOUR{str(channel)}:FUNC:RESET')

    def configure_acquisition(self, decimation, trigger_level, data_units, data_format, trigger_source):
        rp = self._client()
        self.decimation = decimation
        rp.tx_txt(f"ACQ:DEC {str(decimation)}")
        rp.tx_txt(f"ACQ:DATA:UNITS {str(data_units).upper()}")
        rp.tx_txt(f"ACQ:DATA:FORMAT {str(data_format).upper()}")
        rp.tx_txt(f"ACQ:TRig:LEV {str(trigger_level)}")
        rp.tx_txt(f"ACQ:TRig {str(trigger_source)}")

    def stop_acquisition(self):
        rp = self._client()
        rp.tx_txt('ACQ:STOP')

    def is_rp_connected(self):
        rp = self.rp
        if rp is None:
            return False
        try:
            rp.tx_txt("*IDN?")
            _ = rp.rx_txt()
            return True
        except Exception:
            return False

    def _read_block(self, cmd):
        """Send a query that returns a binary block of big-endian float32 and parse it."""
        rp = self._client()
        rp.tx_txt(cmd)
        raw = rp.rx_arb()
        if not raw:
            raise ValueError(f"Empty response for {cmd}")
        n = (len(raw) // 4) * 4
        return np.frombuffer(bytes(raw[:n]), dtype=">f4").astype(float)

    # CHx edge triggers are applied in software on a free-running capture
    _SW_TRIGGERS = {'CH1_PE': (0, True), 'CH1_NE': (0, False),
                    'CH2_PE': (1, True), 'CH2_NE': (1, False)}

    @staticmethod
    def _window(y, trig_idx, pre, post, delay=0):
        """Cut pre+post samples around trig_idx (shifted by delay); anything outside the data is NaN."""
        start = trig_idx - pre + delay
        out = np.full(pre + post, np.nan)
        lo, hi = max(start, 0), min(start + pre + post, len(y))
        if hi > lo:
            out[lo - start:hi - start] = y[lo:hi]
        return out

    @staticmethod
    def _find_edge(y, level, rising, lo, hi):
        """First index in [lo, hi) where y crosses `level` in the given direction, or None."""
        a, b = y[lo - 1:hi - 1], y[lo:hi]
        cross = ((a < level) & (b >= level)) if rising else ((a > level) & (b <= level))
        idx = np.flatnonzero(cross)
        return lo + int(idx[0]) if idx.size else None

    def read_data(self, decimation=8, trigger_level=0.1, data_units='Volts', data_format='bin',
                  trigger_source='CH1_PE', timeout=5.0, center=0):
        """
        Acquire one frame from both channels.

        Returns
        -------
        y1, y2 : np.ndarray, `self.window` samples each (default 8192)
            The trigger sits at index window//2 - center, so a time axis of
            arange(-half, half)/fs has t = 0 at the trigger (for center = 0).

        How it works (found by testing on a STEMlab 125-14, OS 2.0):
          * ACQ:SOUR2:DATA? returned the raw circular buffer without unrolling it,
            so we read the raw ring of both channels and unroll it ourselves.
          * The hardware pre-trigger half was sometimes partly stale (left over
            from earlier frames). So we always capture with trigger delay 8192
            (every sample written after the trigger, fresh and contiguous) and
            apply CH1/CH2 edge triggers in software on that capture. Pre- and
            post-trigger data are then both fresh and locked.
          * EXT_*/AWG_* use the hardware trigger; their pre-trigger part is NaN.
          * If no edge is found, the frame is free-run (centred, unlocked) and
            self.last_triggered is set to False.
        `center` is a trigger delay in samples (clamped to +-4096); `data_format`
        is ignored (always binary).
        """
        rp = self._client()
        self.decimation = decimation
        src = str(trigger_source).upper()
        sw = self._SW_TRIGGERS.get(src)
        hw_source = 'NOW' if (sw is not None or src == 'DISABLED') else src

        # 1. Reset first, then configure
        rp.tx_txt("ACQ:RST")
        rp.tx_txt("ACQ:DATA:FORMAT BIN")
        rp.tx_txt(f"ACQ:DATA:UNITS {data_units.upper()}")
        rp.tx_txt(f"ACQ:DEC {int(decimation)}")
        rp.tx_txt("ACQ:TRIG:DLY 8192")
        rp.tx_txt(f"ACQ:TRIG:LEV {float(trigger_level)}")

        # 2. Start, give the buffer a moment (kept from the tested sequence), then arm
        rp.tx_txt("ACQ:START")
        time.sleep(min(RING_SIZE * int(decimation) / ADC_CLOCK * 1.5 + 0.01, 0.1))
        rp.tx_txt(f"ACQ:TRIG {hw_source}")

        # 3. Wait for trigger detected AND buffer full
        start_time = time.time()
        while True:
            rp.tx_txt("ACQ:TRIG:STAT?")
            trig_stat = (rp.rx_txt() or "").strip()
            rp.tx_txt("ACQ:TRIG:FILL?")
            fill_stat = (rp.rx_txt() or "").strip()
            if trig_stat == "TD" and fill_stat == "1":
                break
            if time.time() - start_time > timeout:
                try:
                    rp.tx_txt("ACQ:STOP")
                except Exception:
                    pass
                raise TimeoutError("Timeout waiting for trigger and full buffer")
            time.sleep(0.0005)

        # 4. Read the raw rings plus the write pointer (index of the newest sample)
        wpos = int(rp.txrx_txt("ACQ:WPOS?"))
        ring1 = self._read_block(f"ACQ:SOUR1:DATA:START:END? 0,{RING_SIZE - 1}")
        ring2 = self._read_block(f"ACQ:SOUR2:DATA:START:END? 0,{RING_SIZE - 1}")
        wpos_after = int(rp.txrx_txt("ACQ:WPOS?"))

        if wpos_after != wpos:
            raise RuntimeError("Write pointer moved while reading; frame discarded")
        if len(ring1) != RING_SIZE or len(ring2) != RING_SIZE:
            raise ValueError(f"Unexpected ring length: {len(ring1)}, {len(ring2)}")

        # 5. Unroll: oldest sample first (= the trigger sample, since DLY = 8192)
        y = (np.roll(ring1, -(wpos + 1)), np.roll(ring2, -(wpos + 1)))

        # 6. Window around the trigger
        pre = post = self.window // 2
        max_delay = (RING_SIZE - self.window) // 2
        delay = int(np.clip(center, -max_delay, max_delay))
        self.last_triggered = True

        if sw is not None:                                   # CHx_PE / CHx_NE in software
            ch, rising = sw
            lo = max(pre - delay, 1)
            hi = RING_SIZE - post - delay
            idx = self._find_edge(y[ch], float(trigger_level), rising, lo, hi)
            if idx is None:
                self.last_triggered = False                  # auto mode: free-run, centred
                idx = RING_SIZE // 2
        elif hw_source == 'NOW':                             # NOW / DISABLED: free-run, centred
            idx = RING_SIZE // 2
        else:                                                # EXT_* / AWG_*: trigger is sample 0
            idx = 0

        return (self._window(y[0], idx, pre, post, delay),
                self._window(y[1], idx, pre, post, delay))

    def acq_setDecimation(self, value: int):
        rp = self._client()
        rp.tx_txt(f"ACQ:DEC {value}")

    def acq_getDecimation(self) -> str:
        rp = self._client()
        rp.tx_txt("ACQ:DEC?")
        return rp.rx_txt() or ""

    def close(self):
        rp = self.rp
        if rp is None:
            return
        self.rp = None
        try:
            rp.close()
        except Exception as exc:
            print("Error closing SCPI connection:", exc)