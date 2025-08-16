import serial
from serial.tools import list_ports
import numpy as np

class SerialData:
    def __init__(self, port='/dev/ttyUSB0', baudrate=115200, n_plots=1):
        self.port = port
        self.baudrate = baudrate
        self.n_plots = n_plots

        try:
            self.sr = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=1)
        except serial.SerialException as e:
            print(f"Error opening serial port {self.port}: {e}, try opening a different port")
            self.sr = serial.Serial(baudrate=self.baudrate, timeout=1)

    def search(self):
        ports = list_ports.comports()
        available_ports = [f"{port.device}" for port in ports if not port.device.startswith('/dev/ttyS')]

        return available_ports
       
    def open(self):
        if self.sr is None:
            self.sr = serial.Serial(self.port, self.baudrate)

    def close(self):
        if self.sr is not None:
            self.sr.close()
            self.sr = None

    def read(self):
        if self.sr is not None:
            return self.sr.readline()
        return None
    
    def collect_data(self):   
        if self.sr is not None and self.sr.is_open:
            last_data = None
            while self.sr.in_waiting:
                raw = self.extract_data()
                try:
                    last_data = np.array(raw, dtype=np.float32)
                except ValueError:
                    continue
            return last_data
    
    def collect_data_bunch(self):
        if self.sr is not None and self.sr.is_open:
            if self.sr.is_open:
                print('Starting data recollection.\n')
                data = self.extract_bunch()
                try:
                    data = np.array(data, dtype=np.float32)
                    return data
                except:
                    raise ValueError("Data not recognized.")
                    

    def extract_data(self):
        if self.sr is not None and self.sr.is_open:
            serialRecieving = self.sr.readline()
            data = serialRecieving.decode('utf-8').rstrip('\n')
            # print("data: " + data)
            data_serial = data.split(',')
            # print("data: " + data_serial[0] + ", " + data_serial[1])
            return data_serial

    def extract_bunch(self):
        active = False
        data_bunch = []
        if self.sr is not None and self.sr.is_open:
            while self.sr.in_waiting:
                serialRecieving = self.sr.readline()
                data = serialRecieving.decode('utf-8').rstrip('\n')

                if data.startswith('stop') and active:
                    active = False
                    break
                elif data.startswith('start') and not active:
                    active = True
                    continue
                
                if active:
                    # print(data)
                    data_serial = data.split(',')
                    data_bunch.append(data_serial)

        return data_bunch
    
    def select_port(self, port_selected): #serial - full
        if self.sr is not None:
            if self.sr.is_open:
                self.sr.close()
            print(port_selected + " was selected")
            self.sr.baudrate=self.baudrate
            self.sr.port = port_selected

            if port_selected != "None":
                self.sr.open()

    def update_baud_rate(self, bd): #serial - full
        if self.sr is not None and self.sr.is_open:
            if self.sr.is_open:
                self.sr.close()
            self.sr.baudrate = bd
            self.sr.open()
