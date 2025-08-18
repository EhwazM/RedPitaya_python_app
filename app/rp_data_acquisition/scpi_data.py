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
            one of 'sine','square','triangle',...
        """
        if channel not in (1,2):
            raise ValueError(f"channel must be 1 or 2, got {channel}")
        wf = waveform.lower()
        valid = {'sine','square','triangle','ramp','noise'}
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
        self.decimation = decimation
        self.rp.tx_txt('ACQ:RST')
        self.rp.tx_txt(f'ACQ:DEC {int(decimation)}')
        self.rp.tx_txt(f'ACQ:DATA:UNITS {data_units.upper()}')
        self.rp.tx_txt(f'ACQ:DATA:FORMAT {data_format.upper()}')
        self.rp.tx_txt(f'ACQ:TRIG:DLY {center}')
        self.rp.tx_txt(f'ACQ:TRIG:LEV {float(trigger_level)}')
        self.rp.tx_txt(f'ACQ:TRIG {trigger_source}')
        self.rp.tx_txt('ACQ:START')

        start_time = time.time()

        if data_format == 'ascii':
            while True:
                self.rp.tx_txt('ACQ:TRIG:FILL?')
                resp = (self.rp.rx_txt() or '').strip()
                if resp == '1':
                    break
                if time.time() - start_time > timeout:
                    try: self.rp.tx_txt('ACQ:STOP')
                    except: pass
                    raise TimeoutError("Trigger timeout waiting for fill=1")
                
        if data_format == 'bin':
            while True:
                self.rp.tx_txt('ACQ:TRIG:FILL?')
                resp = (self.rp.rx_txt() or '')
                if resp == '1':
                    break
                if time.time() - start_time > timeout:
                    try: self.rp.tx_txt('ACQ:STOP')
                    except: pass
                    raise TimeoutError("Trigger timeout waiting for fill=1")

        def _read_channel(cmd):
            self.rp.tx_txt(cmd)
            raw = (self.rp.rx_txt() or '').strip()
            if not raw:
                raise ValueError(f"Empty response for {cmd}")
            # remove braces, newlines, whitespace:
            raw = raw.strip('{} \r\n')
            parts = [p for p in raw.split(',') if p.strip()!='']
            if not parts:
                raise ValueError(f"No numeric data in response for {cmd}: {raw!r}")
            try:
                return np.array([float(s) for s in parts], dtype=float)
            except Exception as e:
                raise ValueError(f"Failed to parse channel data: {e}")
            
        def _read_channel_bin(cmd):
            self.rp.tx_txt(cmd)
            raw = self.rp.rx_arb()
            if not raw:
                raise ValueError(f"Empty response for {cmd}")

            buff = [struct.unpack('!f',bytearray(raw[i:i+4]))[0] for i in range(0, len(raw), 4)]
            if not buff:
                raise ValueError(f"No numeric data in response for {cmd}: {raw!r}")
            try:
                return np.array(buff, dtype=float)
            except Exception as e:
                raise ValueError(f"Failed to parse channel data: {e}")

        if data_format == 'ascii':
            y1 = _read_channel('ACQ:SOUR1:DATA?')
            y2 = _read_channel('ACQ:SOUR2:DATA?')
        elif data_format == 'bin':
            y1 = _read_channel_bin('ACQ:SOUR1:DATA?')
            y2 = _read_channel_bin('ACQ:SOUR2:DATA?')
        else:
            raise ValueError(f"Unknown data format: {data_format}")
        try:
            self.rp.tx_txt('ACQ:STOP')
        except:
            pass
        return y1, y2

    def close(self):
        self.rp.close()