from PySide6.QtWidgets import QApplication
from bokeh.server.server import Server

from threading import Thread
from tornado.ioloop import IOLoop

from app.rp_plot.plot_setup import BokehPlot
import serial

from bokeh.plotting import figure as bk_figure
from bokeh.models import Range1d
from app.ui_pyside.oscilloscope import Oscilloscope

from assets.dark_theme import pyside6_simple_dark_theme

from queue import Queue
import sys

dark_theme = pyside6_simple_dark_theme

serial_rp = serial.Serial(baudrate=115200)

p = bk_figure(title="Signal", sizing_mode='stretch_both', x_axis_label='Time', y_axis_label='Voltage (V)', y_range=Range1d(start=-1, end=1), x_range=Range1d(start=-30, end=30)) # type: ignore
    
bokeh_plot = BokehPlot(plot_b=p,
                        n_plots=2,
                        roll_over=1000,
                        colors=['green', 'purple', 'blue'],
                        update_time=10,
                        scatter_plot=True,
                        oscilloscope_mode=True,
)

def modify_doc(doc, bokeh_plot):
  bokeh_plot.attach_doc(doc)

def start_bokeh_server(port_queue):
    port = 5006
    while True:
        try:
            loop = IOLoop()
            loop.make_current()
            server = Server(
                {'/': lambda doc: modify_doc(doc, bokeh_plot=bokeh_plot)},
                io_loop=loop,
                allow_websocket_origin=[f"localhost:{port}"],
                port=port
            )
            server.start()
            print(f"Bokeh server started at http://localhost:{port}")

            # Send the port to the main thread *before* blocking the loop
            port_queue.put(port)

            loop.start()  # This blocks forever
            break
        except OSError:
            print(f"Port {port} in use, trying {port + 1}...")
            port += 1
        except Exception as e:
            print(f"Unexpected error: {e}")
            port += 1


if __name__ == '__main__':
    port_queue = Queue()
    thread = Thread(target=start_bokeh_server, args=(port_queue,), daemon=True)
    thread.start()

    # Wait until the Bokeh server thread sends back the port
    port = port_queue.get()

    app = QApplication(sys.argv)
    app.setStyleSheet(dark_theme)

    window = Oscilloscope(app, rp_plot=bokeh_plot, url=f'http://localhost:{port}')
    window.show()

    sys.exit(app.exec())
