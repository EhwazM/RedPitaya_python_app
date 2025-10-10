import time
import pandas as pd
import numpy as np

from serial.tools import list_ports

from bokeh.models import ColumnDataSource
from bokeh.plotting import figure as bk_figure
from bokeh.models import Range1d, DataRange1d

from app.rp_data_acquisition.scpi_data import ScpiData
from app.rp_data_acquisition.serial_data import SerialData

class BokehPlot:
    def __init__(self, plot_b, n_plots=2, baud_rate=115200 ,roll_over=5000, colors=['red', 'blue', 'green', 'yellow', 'orange', 'purple'], update_time=10, scatter_plot=False, oscilloscope_mode=False, sampling_rate=125e6, rp=None, rp_ip='rp-f0c5e4.local'):
        self.n_plots = n_plots
        self.plot_b = plot_b
        self.roll_over = roll_over
        self.colors = colors
        self.update_time = update_time
        self.scatter_plot = scatter_plot
        self.osci = oscilloscope_mode
        self.sources = []
        self.lines = []
        self.scatters = []
        self.y = [0.0 for _ in range(n_plots)]
        self.sampling_rate = sampling_rate
        self.sr_data = SerialData(n_plots=n_plots)
        self.reading = False
        self.decimation = int(2**3)
        self.trigger_level = 0.0
        self.trigger_source = "CH1_PE"  # CH1_PE, CH2_PE, EXT_PE, DISABLED
        self.trigger_delay = 0  # en muestras, -8192 a 8192
        self.rp_ip = rp_ip
        self.rp_connected = False

        self.periodic_callback = None
        
        self.baud_rate = baud_rate

        self.start = time.time()
        self.setup_plot()

        if not self.osci:
            self.plot_b.x_range = DataRange1d()

        if rp is None:
            try: 
                self.rp = ScpiData(self.rp_ip)
                self.rp_connected = self.rp.is_rp_connected()
            except Exception as e:
                print("Error initializing ScpiData:", e)    
        else:
            try:
                self.rp = rp
                self.rp_connected = self.rp.is_rp_connected()
            except Exception as e:
                print("Error setting Red Pitaya instance:", e)

    def setup_plot(self):
        for i in range(self.n_plots):
            source = ColumnDataSource(data=dict(x=[], y=[]))
            self.sources.append(source)

            if self.scatter_plot == True:
                scatter = self.plot_b.scatter('x', 'y', source=source, line_color=self.colors[i])
                self.scatters.append(scatter)

            line = self.plot_b.line('x', 'y', source=source, line_color=self.colors[i])
            self.lines.append(line)
        
        print("Setup ready!")

    def attach_doc(self, doc):
        self.doc = doc
        doc.theme = "dark_minimal"
        doc.add_root(self.plot_b)
        
        if self.osci:
            self.periodic_callback = doc.add_periodic_callback(self.update_oscilloscope_scpi, self.update_time)
        else:
            self.periodic_callback = doc.add_periodic_callback(self.update_real_time, self.update_time)
    
    def update_oscilloscope_scpi(self):

        if self.reading:
            try:
                y1, y2 = self.rp.read_data(decimation=self.decimation, trigger_level=self.trigger_level, trigger_source=self.trigger_source, center=self.trigger_delay)  # type: ignore
            except Exception as e:
                print("SCPI read_data error:", e)
                return

            if len(y1) == 0 or len(y2) == 0:
                print("SCPI returned empty arrays")
                return

            fs = float(self.sampling_rate) / self.decimation  # frecuencia de muestreo
            n = min(len(y1), len(y2))  # nos aseguramos de que ambos tienen al menos n datos

            # Si es impar, descartamos 1 para que sea par
            if n % 2 != 0:
                n -= 1
                y1 = y1[:n]
                y2 = y2[:n]

            half = n // 2

            # Cortamos al centro por seguridad
            y1 = y1[:n]
            y2 = y2[:n]

            # eje de tiempo centrado en cero, en microsegundos
            t = (np.arange(-half, half) / fs) * 1e6

        else:
            return

        try:
            self.sources[0].stream(dict(x=t, y=y1), rollover=n)
            self.sources[1].stream(dict(x=t, y=y2), rollover=n)
        except Exception as e:
            print("Bokeh stream error:", e)


    def update_real_time(self):
        if self.reading:
            result = self.sr_data.collect_data()
           
            if result is None:
                print("collect_data returned None")
                return
            
            for i in range(self.n_plots):
                elapsed_time = time.time() - self.start
                new_data = dict(x=[elapsed_time], y=[result[i]])
                self.sources[i].stream(new_data, rollover=self.roll_over)

    def update_y_range(self, min_val=None, max_val=None):
        def _update():
            try:
                if min_val is not None:
                    self.plot_b.y_range.start = min_val
                if max_val is not None:
                    self.plot_b.y_range.end = max_val
                if self.plot_b.y_range.start >= self.plot_b.y_range.end:
                    print("Invalid range: min >= max")
            except Exception as e:
                print(f"Error setting range inside update: {e}")

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def update_x_range(self, min_val=None, max_val=None):
        def _update():
            try:
                if min_val is not None:
                    self.plot_b.x_range.start = min_val
                if max_val is not None:
                    self.plot_b.x_range.end = max_val
                if self.plot_b.x_range.start >= self.plot_b.x_range.end:
                    print("Invalid range: min >= max")
            except Exception as e:
                print(f"Error setting range inside update: {e}")

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def update_roll_over(self, ro):
        def _update():
            self.roll_over=ro
            
        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def change_to_oscilloscope_mode(self):
        def _update():
            self.osci = True
            self.plot_b.x_range = Range1d(start=-30, end=30)

            if self.periodic_callback:
                self.doc.remove_periodic_callback(self.periodic_callback)
            self.periodic_callback = self.doc.add_periodic_callback(self.update_oscilloscope_scpi, self.update_time)

            if self.sr_data.sr is not None and self.sr_data.sr.is_open:
                self.sr_data.sr.close()

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def change_to_real_time_mode(self):
        def _update():
            self.osci = False
            self.plot_b.x_range = DataRange1d()

            if self.periodic_callback:
                self.doc.remove_periodic_callback(self.periodic_callback)
            self.periodic_callback = self.doc.add_periodic_callback(self.update_real_time, self.update_time)

            if self.sr_data.sr is not None and self.sr_data.sr.is_open:
                self.sr_data.sr.close()

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def change_scatter(self, checked: bool):
        def _update():
            self.scatter_plot = checked
            for ch in range(self.n_plots):
                # visible si scatter está ON, el canal está activo y debe mostrarse
                visible = checked and self.lines[ch].visible
                if ch < len(self.scatters):
                    self.scatters[ch].visible = visible
        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def update_decimation(self, decim: int):
        def _update():
            nonlocal decim
            if decim < 1:
                decim = 1
            elif decim > 16:
                decim = 16
            self.decimation = int(2**decim)

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")
    
    def update_trigger_level(self, trigger_level: float = 0.0):
        def _update():
            nonlocal trigger_level
            if trigger_level < -10.0:
                trigger_level = -10.0
            elif trigger_level > 10.0:
                trigger_level = 10.0
            self.trigger_level = float(trigger_level)

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def update_trigger_delay(self, center: int = 0):
        def _update():
            nonlocal center
            if center < -8192:
                center = -8192
            elif center > 8192:
                center = 8192
            self.trigger_delay = int(center)

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def update_trigger_source(self, trigger_source: str = "CH1_PE"):
        def _update():
            nonlocal trigger_source
            self.trigger_source = trigger_source

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def generate_signal(self, values: dict):
        ch = values['channel']
        vpp = values['vpp']
        fq = values['freq']
        wf = values['waveform']
        active = values['active']
        show = values['show_plot']

        if not active:
            self.reading = False
            def _hide():
                self.lines[ch-1].visible = False
                if ch-1 < len(self.scatters):
                    self.scatters[ch-1].visible = False
            if hasattr(self, "doc"):
                self.doc.add_next_tick_callback(_hide)
            return

        self.reading = True

        def _update_visibility():
            visible = active and show
            self.lines[ch-1].visible = visible
            if ch-1 < len(self.scatters):
                # scatter solo se muestra si scatter_plot está activado también
                self.scatters[ch-1].visible = visible and self.scatter_plot

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update_visibility)

        # Generación real de señal
        if self.osci:
            self.rp.generate_signal(channel=ch, amplitude=vpp/2, frequency=fq, waveform=wf) #type: ignore
        else:
            bash_cmd = f'generate {ch} {vpp} {fq} {wf}'
            if self.sr_data.sr is not None and self.sr_data.sr.is_open:
                self.sr_data.sr.write((bash_cmd + '\n').encode())
                time.sleep(0.1)

    def save_current_data(self, filename: str):
        # Tomamos la columna x de la primera fuente
        df = pd.DataFrame({"x": self.sources[0].data["x"]})

        # Añadimos cada y_i
        for i, src in enumerate(self.sources):
            df[f"y{i}"] = src.data["y"]
        df.to_csv(filename, index=False)

    def test_function(self):
        t = np.linspace(- int(2**5), int(2**5), int(1e3))
        for i in range(self.n_plots):
            new_data = dict(x=t, y=np.sin(0.05 * (2 * np.pi * (i + 1)) * t))

            # Schedule the update safely on Bokeh’s event loop
            self.doc.add_next_tick_callback(
                lambda i=i, new_data=new_data: self.sources[i].stream(new_data, rollover=None)
            )
    
    def update_rp_ip(self, new_ip: str):
        def _update():
            self.rp_connected = False
            self.rp_ip = new_ip
            self.rp.ip_address = new_ip

            try:
                self.rp.connect()
                self.rp_connected = self.rp.is_rp_connected()
            except Exception as e:
                print("Error updating Red Pitaya IP:", e)
                self.rp_connected = self.rp.is_rp_connected()
                return

            print(f"Updated Red Pitaya IP to: {self.rp_ip}")

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")
    
    def auto_scale(self):
        def _update():
            all_y = []
            for src in self.sources:
                all_y.extend(src.data["y"])
            if all_y:
                min_y = min(all_y)
                max_y = max(all_y)
                padding = (max_y - min_y) * 0.1 if max_y != min_y else 1.0
                self.plot_b.y_range.start = min_y - padding
                self.plot_b.y_range.end = max_y + padding
            else:
                print("No data available for auto-scaling.")

        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

