import time
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

        self.periodic_callback = None
        
        self.baud_rate = baud_rate

        self.start = time.time()
        self.setup_plot()

        if not self.osci:
            self.plot_b.x_range = DataRange1d()

        if rp is None:
            self.rp = ScpiData(rp_ip)
        else:
            self.rp = rp

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
        decimation = self.rp.get_decimation()
        if self.reading:
            try:
                y1, y2 = self.rp.read_data(decimation=decimation)
            except Exception as e:
                print("SCPI read_data error:", e)
                return

            if len(y1) == 0 or len(y2) == 0:
                print("SCPI returned empty arrays")
                return

            fs = float(self.sampling_rate) / decimation
            t = np.arange(len(y1)) / fs  * 1e6

        else:
            return

        try:
            self.sources[0].stream(dict(x=t, y=y1), rollover=y1.shape[0])
            self.sources[1].stream(dict(x=t, y=y2), rollover=y2.shape[0])
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

    def change_scatter(self, checked : bool):
        def _update():
            self.scatter_plot = checked
            for scatter in self.scatters:
                scatter.visible = checked
        
        if hasattr(self, "doc"):
            self.doc.add_next_tick_callback(_update)
        else:
            print("Document not attached yet.")

    def generate_signal(self, ch=1, vpp=1.5, fq=1e4, wf='sine'):
        bash_cmd = f'generate {ch} {vpp} {fq} {wf}'
        if self.sr_data.sr is not None and self.sr_data.sr.is_open:
            self.sr_data.sr.write((bash_cmd + '\n').encode())
            time.sleep(0.1)

    def generate_signal_scpi(self, ch=1, vpp=1.5, fq:int=int(1e4), wf='SINE'):
        self.rp.generate_signal(channel=ch, amplitude=vpp/2, frequency=fq, waveform=wf)

        
