import numpy as np
import app.redpitaya_scpi.redpitaya_scpi as scpi
import time
import struct

class ScpiData:
    def __init__(self, ip_address, port=5000):
        self.ip_address = ip_address
        self.port = port
        self.rp = scpi.scpi(ip_address, port)
        self.decimation = int(2**3)

    def connect(self):
        self.rp = scpi.scpi(self.ip_address, self.port)

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
        self.rp.tx_txt(f'SOUR{str(channel)}:FUNC:RESET')

        # Set the waveform type, frequency, amplitude, and offset
        self.rp.tx_txt(f'SOUR{str(channel)}:FUNC {str(waveform.upper())}')
        self.rp.tx_txt(f'SOUR{str(channel)}:FREQ:FIX {str(frequency)}')
        self.rp.tx_txt(f'SOUR{str(channel)}:VOLT {str(amplitude)}')
        self.rp.tx_txt(f'SOUR{str(channel)}:VOLT:OFFS {str(offset)}')
        self.rp.tx_txt(f"SOUR{channel}:TRIG:INT")

        # Enable the output
        self.rp.tx_txt(f'OUTPUT{str(channel)}:STATE ON')

    def trigger_generation(self):
        self.rp.tx_txt(f'SOUR:TRIG:INT')

    def stop_signal(self, channel=1):
        """
        Stop the signal generation on the specified channel.
        """
        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        if not self.rp._socket:
            print("Not connected to Red Pitaya")
            return
        self.rp.tx_txt(f'OUTPUT{channel}:STATE OFF')

    def reset(self, channel=1):
        """
        Reset the specified channel.
        """

        if channel not in (1, 2):
            raise ValueError(f"Channel must be 1 or 2, got {channel}")
        self.rp.tx_txt(f'SOUR{str(channel)}:FUNC:RESET')

    def configure_acquisition(self, decimation, trigger_level, data_units, data_format, trigger_source):
        self.decimation = decimation
        self.rp.tx_txt(f"ACQ:DEC {str(decimation)}")
        self.rp.tx_txt(f"ACQ:DATA:UNITS {str(data_units).upper()}")
        self.rp.tx_txt(f"ACQ:DATA:FORMAT {str(data_format).upper()}")
        self.rp.tx_txt(f"ACQ:TRig:LEV {str(trigger_level)}")
        self.rp.tx_txt(f"ACQ:TRig {str(trigger_source)}")

    def stop_acquisition(self):
        self.rp.tx_txt('ACQ:STOP')

    def read_data(self, decimation=8, trigger_level=0.1, data_units='Volts', data_format='bin', trigger_source='CH1_PE', timeout=5.0, center=0):
        """
        Read acquired data from Red Pitaya.

        Parameters
        ----------
        decimation : int
        trigger_level : float
        data_units : str
            "Volts" or "Raw"
        data_format : str
            "ascii" or "bin"
        trigger_source : str
            e.g. "CH1_PE", "CH2_PE", "EXT_PE", "NOW"
        timeout : float
            seconds
        center : int
            trigger delay in samples (0–16384)

        Returns
        -------
        y1, y2 : np.ndarray
            acquired data from channels 1 and 2
        """

        self.decimation = decimation

        # 1. Reset first
        self.rp.tx_txt("ACQ:RST")

        # 2. Configure all acquisition parameters
        self.rp.tx_txt(f"ACQ:DATA:FORMAT {data_format.upper()}")
        self.rp.tx_txt(f"ACQ:DATA:UNITS {data_units.upper()}")
        self.rp.tx_txt(f"ACQ:DEC {int(decimation)}")
        self.rp.tx_txt(f"ACQ:TRIG:DLY {int(center)}")
        self.rp.tx_txt(f"ACQ:TRIG:LEV {float(trigger_level)}")

        # 3. Start acquisition
        self.rp.tx_txt("ACQ:START")

        # 4. Arm trigger last
        self.rp.tx_txt(f"ACQ:TRIG {trigger_source}")

        start_time = time.time()

        # Esperar trigger válido y buffer lleno
        while True:
            # self.rp.tx_txt("ACQ:TRIG:STAT?")
            # trig_stat = (self.rp.rx_txt() or "").strip()
            self.rp.tx_txt("ACQ:TRIG:FILL?")
            fill_stat = (self.rp.rx_txt() or "").strip()

            # if trig_stat == "TD" and fill_stat == "1":  # Trigger Detected and Buffer Full
            if fill_stat == "1":
                break
            if time.time() - start_time > timeout:
                try:
                    self.rp.tx_txt("ACQ:STOP")
                except:
                    pass
                raise TimeoutError("Timeout esperando trigger y llenado de buffer")

        def _read_channel_ascii(cmd):
            self.rp.tx_txt(cmd)
            raw = (self.rp.rx_txt() or "").strip()
            if not raw:
                raise ValueError(f"Respuesta vacía para {cmd}")
            raw = raw.strip("{} \r\n")
            parts = [p for p in raw.split(",") if p.strip() != ""]
            if not parts:
                raise ValueError(f"Sin datos numéricos en {cmd}: {raw!r}")
            return np.array([float(s) for s in parts], dtype=float)

        def _read_channel_bin(cmd):
            self.rp.tx_txt(cmd)
            raw = self.rp.rx_arb()
            if not raw:
                raise ValueError(f"Respuesta vacía para {cmd}")
            # asegurar múltiplo de 4 bytes
            n = (len(raw) // 4) * 4
            buff = [
                struct.unpack("!f", bytearray(raw[i:i+4]))[0]
                for i in range(0, n, 4)
            ]
            if not buff:
                raise ValueError(f"Sin datos numéricos en {cmd}: {raw!r}")
            return np.array(buff, dtype=float)

        if data_format.lower() == "ascii":
            y1 = _read_channel_ascii("ACQ:SOUR1:DATA?")
            y2 = _read_channel_ascii("ACQ:SOUR2:DATA?")
        elif data_format.lower() == "bin":
            y1 = _read_channel_bin("ACQ:SOUR1:DATA?")
            y2 = _read_channel_bin("ACQ:SOUR2:DATA?")
        else:
            raise ValueError(f"Formato desconocido: {data_format}")

        try:
            self.rp.tx_txt("ACQ:STOP")
        except:
            pass

        return y1, y2

        # y1_post = y1[8192:]
        # y2_post = y2[8192:]
        # return y1_post, y2_post

        # lag = compute_lag(y1, y2)

        # if lag > 0:
        #     y2_aligned = y2[lag:]
        #     y1_aligned = y1[:len(y2_aligned)]
        # elif lag < 0:
        #     y1_aligned = y1[-lag:]
        #     y2_aligned = y2[:len(y1_aligned)]
        # else:
        #     y1_aligned, y2_aligned = y1, y2

        # return y1_aligned, y2_aligned

    def acq_setDecimation(self, value: int):
        self.rp.tx_txt(f"ACQ:DEC {value}")

    def acq_getDecimation(self) -> str:
        self.rp.tx_txt("ACQ:DEC?")
        return self.rp.rx_txt() or ""

    def close(self):
        self.rp.close()

def compute_lag(signal1, signal2):
        corr = np.correlate(signal1, signal2, mode='full')
        lag = corr.argmax() - (len(signal1) - 1)
        return lag