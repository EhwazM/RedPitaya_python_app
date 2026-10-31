import time
import threading
import pandas as pd
import numpy as np

from bokeh.models import ColumnDataSource
from bokeh.models import Range1d, DataRange1d

from app.rp_data_acquisition.scpi_data import ScpiData
from app.rp_data_acquisition.serial_data import SerialData
from app.rp_lia.redpitaya_lia import RedPitayaLIA

class BokehPlot:
    def __init__(self, plot_b, n_plots=2, baud_rate=115200 ,roll_over=5000, colors=['red', 'blue', 'green', 'yellow', 'orange', 'purple'], update_time=10, scatter_plot=False, oscilloscope_mode=True, sampling_rate=125e6, rp=None, rp_ip='', figure_factory=None):
        self.n_plots = n_plots
        self.plot_b = plot_b
        # Bokeh models can belong to only ONE Document, but every time the
        # Oscilloscope window is reopened the browser opens a new session
        # (= a new Document). So each session after the first gets a fresh
        # figure built by this factory instead of re-using the old one.
        self._figure_factory = figure_factory
        self._plot_in_use = False
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
        self.active_channels = set()
        self.decimation = int(2**3)
        self.trigger_level = 0.0
        self.trigger_source = "CH1_PE"  # CH1_PE, CH2_PE, EXT_PE, DISABLED
        self.trigger_delay = 0  # en muestras, -8192 a 8192
        self.rp_ip = rp_ip
        self.rp_connected = False
        # Serialises connect_rp() so a background connect (launched from
        # main.py) and a lazy reconnect from generate_signal() can't race.
        self._connect_lock = threading.Lock()

        self.periodic_callback = None
        self.doc = None
        
        self.baud_rate = baud_rate

        self.start = time.time()
        self.setup_plot()

        if not self.osci:
            self.plot_b.x_range = DataRange1d()

        # IMPORTANT: nothing here touches the network. ScpiData is created
        # unconnected; the real connection is made later by connect_rp(),
        # which main.py calls once the Red Pitaya connection is done.
        if rp is None:
            self.rp = ScpiData(self.rp_ip)
        else:
            self.rp = rp
            try:
                self.rp_connected = self.rp.is_rp_connected()
            except Exception as e:
                print("Error setting Red Pitaya instance:", e)

    def connect_rp(self, ip: str | None = None) -> bool:
        """(Re)connect the SCPI client to the Red Pitaya at `ip` (or the last
        known address). Safe to call from any thread; never raises.
        Returns True if the board answered."""
        with self._connect_lock:
            if ip:
                self.rp_ip = ip
            self.rp_connected = False

            if not self.rp_ip:
                print("BokehPlot: no Red Pitaya address set yet; not connecting.")
                return False

            try:
                self.rp.ip_address = self.rp_ip
                self.rp.connect()
                self.rp_connected = self.rp.is_rp_connected()
            # SystemExit too: some SCPI client versions call sys.exit() on a
            # failed connect, which must not take the whole application down.
            except (Exception, SystemExit) as e:
                print(f"BokehPlot: could not connect to {self.rp_ip}:", e)
                self.rp_connected = False

            if self.rp_connected:
                print(f"BokehPlot: connected to Red Pitaya at {self.rp_ip}")
            return self.rp_connected

    def setup_plot(self):
        for i in range(self.n_plots):
            source = ColumnDataSource(data=dict(x=[], y=[]))
            self.sources.append(source)

            if self.scatter_plot == True:
                scatter = self.plot_b.scatter('x', 'y', source=source, line_color=self.colors[i], visible=False)
                self.scatters.append(scatter)

            line = self.plot_b.line('x', 'y', source=source, line_color=self.colors[i], visible=False)
            self.lines.append(line)
        
        print("Setup ready!")

    def _new_figure(self) -> None:
        """Replace self.plot_b (+ its sources/lines) with a fresh figure for a
        new Bokeh session, carrying over the view ranges and which channels
        are visible. Needed because a figure can't be moved between
        Documents, and detaching it from a still-alive old session from here
        is not allowed (we don't hold that Document's lock)."""
        assert self._figure_factory is not None
        old = self.plot_b
        old_lines = [line.visible for line in self.lines]
        old_scatters = [scatter.visible for scatter in self.scatters]

        fig = self._figure_factory()
        if isinstance(old.y_range, Range1d) and isinstance(fig.y_range, Range1d):
            fig.y_range.start, fig.y_range.end = old.y_range.start, old.y_range.end
        if self.osci:
            if isinstance(old.x_range, Range1d) and isinstance(fig.x_range, Range1d):
                fig.x_range.start, fig.x_range.end = old.x_range.start, old.x_range.end
        else:
            fig.x_range = DataRange1d()

        self.plot_b = fig
        self.sources, self.lines, self.scatters = [], [], []
        self.setup_plot()

        for line, visible in zip(self.lines, old_lines):
            line.visible = visible
        for scatter, visible in zip(self.scatters, old_scatters):
            scatter.visible = visible

    def _tick(self, doc, fn):
        """Wrap a periodic callback so it only runs while `doc` is still the
        current session. A previous session's callbacks keep firing until
        Bokeh finally destroys it; they must not touch the current figure."""
        def run():
            if self.doc is doc:
                fn()
        return run

    def attach_doc(self, doc):
        if self._plot_in_use:
            if self._figure_factory is not None:
                self._new_figure()
            else:
                # No factory: best effort, re-using the single figure.
                old_doc = self.plot_b.document
                if old_doc is not None and old_doc is not doc:
                    try:
                        old_doc.remove_root(self.plot_b)
                    except Exception as exc:
                        print(f"BokehPlot: could not detach previous root: {exc}")
        self._plot_in_use = True

        self.doc = doc
        doc.theme = "dark_minimal"
        doc.add_root(self.plot_b)

        callback = self.update_oscilloscope_scpi if self.osci else self.update_real_time
        self.periodic_callback = doc.add_periodic_callback(self._tick(doc, callback), self.update_time)

        def _on_destroyed(session_context):
            self._session_destroyed(doc)
        doc.on_session_destroyed(_on_destroyed)

    def _session_destroyed(self, doc):
        # Only forget the session if it is still the CURRENT one. When the
        # Oscilloscope window is reopened, the previous session is destroyed
        # some time AFTER the new one attached; clearing self.doc then would
        # cut off the live session. (No root removal needed any more: every
        # session owns its own figure.)
        if self.doc is doc:
            self.doc = None
            self.periodic_callback = None

    def _safe_next_tick(self, callback) -> None:
        """Schedule callback via add_next_tick_callback, but only if a
        live session is actually attached right now. Called from sidebar
        widgets that may fire during window construction, before any
        browser session has (re)attached -- or after one has already
        died -- when self.doc would otherwise be None or stale."""
        doc = self.doc
        if doc is None:
            return
        def guarded():
            # skip work queued on a session that has been replaced meanwhile
            if self.doc is doc:
                callback()

        try:
            doc.add_next_tick_callback(guarded)
        except Exception as exc:
            print(
                "BokehPlot: could not schedule update "
                f"(stale/dead session?): {exc}"
            )
    
    def update_oscilloscope_scpi(self):

        # Nothing to read until connect_rp() has opened the socket.
        if self.reading and self.rp.has_socket:
            try:
                y1, y2 = self.rp.read_data(decimation=self.decimation, trigger_level=self.trigger_level, trigger_source=self.trigger_source, center=self.trigger_delay)  # type: ignore
            except Exception as e:
                print("SCPI read_data error:", e)
                # Without this, a broken socket (peer closed, reset,
                # etc.) is never replaced: self.rp keeps wrapping the
                # same dead connection forever, so every future tick
                # hits this same except block on the same dead socket
                # instead of ever recovering.
                try:
                    self.rp.connect()
                except Exception as reconnect_exc:
                    print("SCPI reconnect error:", reconnect_exc)
                return

            if len(y1) == 0 or len(y2) == 0:
                print("SCPI returned empty arrays")
                return

            fs = float(self.sampling_rate) / self.decimation
            n = min(len(y1), len(y2))

            if n % 2 != 0:
                n -= 1
                y1 = y1[:n]
                y2 = y2[:n]

            half = n // 2
            y1 = y1[:n]
            y2 = y2[:n]
            t = (np.arange(-half, half) / fs) * 1e6   # centred on zero

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

        if self.doc is not None:
            self._safe_next_tick(_update)
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

        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")

    def update_roll_over(self, ro):
        def _update():
            self.roll_over=ro
            
        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")

    def change_to_oscilloscope_mode(self):
        def _update():
            doc = self.doc
            if doc is None:
                return

            self.osci = True
            self.plot_b.x_range = Range1d(start=-30, end=30)

            if self.periodic_callback:
                doc.remove_periodic_callback(self.periodic_callback)
            self.periodic_callback = doc.add_periodic_callback(self._tick(doc, self.update_oscilloscope_scpi), self.update_time)

            if self.sr_data.sr is not None and self.sr_data.sr.is_open:
                self.sr_data.sr.close()

        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")

    def change_to_real_time_mode(self):
        def _update():
            doc = self.doc
            if doc is None:
                return

            self.osci = False
            self.plot_b.x_range = DataRange1d()

            if self.periodic_callback:
                doc.remove_periodic_callback(self.periodic_callback)
            self.periodic_callback = doc.add_periodic_callback(self._tick(doc, self.update_real_time), self.update_time)

            if self.sr_data.sr is not None and self.sr_data.sr.is_open:
                self.sr_data.sr.close()

        if self.doc is not None:
            self._safe_next_tick(_update)
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
        if self.doc is not None:
            self._safe_next_tick(_update)
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

        if self.doc is not None:
            self._safe_next_tick(_update)
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

        if self.doc is not None:
            self._safe_next_tick(_update)
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

        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")

    def update_trigger_source(self, trigger_source: str = "CH1_PE"):
        def _update():
            nonlocal trigger_source
            self.trigger_source = trigger_source

        if self.doc is not None:
            self._safe_next_tick(_update)
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
            self.active_channels.discard(ch)
            self.reading = False
            def _hide():
                self.lines[ch-1].visible = False
                if ch-1 < len(self.scatters):
                    self.scatters[ch-1].visible = False
            if self.doc is not None:
                self._safe_next_tick(_hide)
            return

        self.active_channels.add(ch)
        self.reading = True

        def _update_visibility():
            visible = active and show
            self.lines[ch-1].visible = visible
            if ch-1 < len(self.scatters):
                self.scatters[ch-1].visible = visible and self.scatter_plot

        if self.doc is not None:
            self._safe_next_tick(_update_visibility)

        if self.osci:
            try:
                if not self.rp_connected:
                    self.connect_rp()
                if self.rp_connected:
                    self.rp.generate_signal(channel=ch, amplitude=vpp/2, frequency=fq, waveform=wf)
                else:
                    print("Not connected to Red Pitaya")
            except Exception as e:
                print("Error generating signal on Red Pitaya:", e)
                self.rp_connected = self.rp.is_rp_connected()
                return
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
            self._safe_next_tick(
                lambda i=i, new_data=new_data: self.sources[i].stream(new_data, rollover=None)
            )
    
    def update_rp_ip(self, new_ip: str):
        def _update():
            if self.connect_rp(new_ip):
                print(f"Updated Red Pitaya IP to: {self.rp_ip}")
            else:
                print(f"Red Pitaya IP set to {self.rp_ip}, but it is not reachable.")

        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")
    
    def auto_scale(self): #Only Y axis
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

        if self.doc is not None:
            self._safe_next_tick(_update)
        else:
            print("Document not attached yet.")


class LIABokehPlot:
    """
    Real-time Bokeh document for the FPGA Lock-In Amplifier.

    A new LIABokehPlot instance must be created for every Bokeh document
    session. Bokeh models (figures, ColumnDataSource, ranges, etc.) cannot
    belong to more than one Document.
    """

    def __init__(
        self,
        host="10.42.0.100",
        port=5001,
        update_time=50,
        roll_over=1000,
    ):
        self.host = host
        self.port = port
        self.update_time = max(10, int(update_time))
        self.roll_over = max(1, int(roll_over))

        self.lia = RedPitayaLIA(
            host=self.host,
            port=self.port,
            timeout=5.0,
        )

        self.doc = None
        self.start = time.time()

        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

        self.source: ColumnDataSource | None = None
        self.plots = []
        self.layout = None

        self.setup_plot()

    def setup_plot(self):
        from bokeh.layouts import column
        from bokeh.models import Button, Range1d
        from bokeh.plotting import figure

        plot_info = (
            ("X / I", "I", "I"),
            ("Y / Q", "Q", "Q"),
            ("R", "R", "R"),
            ("Phase", "Phase (rad)", "Phase"),
        )

# --------------------------------------------------------------
# One shared ColumnDataSource with a column per channel, instead
# of four separate sources. Streaming to it once per tick sends a
# single patch to the browser instead of four, which cuts the
# per-tick server -> client sync overhead by 4x.
# --------------------------------------------------------------
        self.source = ColumnDataSource(
            data={"x": [], "I": [], "Q": [], "R": [], "Phase": []}
        )

        for title, y_label, column_name in plot_info:
            plot = figure(
                title=title,
                height_policy="max",
                width_policy="max",
                sizing_mode="stretch_width",
                x_axis_label="Time (s)",
                y_axis_label=y_label,
                tools=(
                    "pan,wheel_zoom,box_zoom,"
                    "reset,save"
                ),
            )

            if title == "Phase":
                plot.y_range = Range1d(
                    start=-np.pi,
                    end=np.pi,
                )

            plot.line(
                "x",
                column_name,
                source=self.source,
                line_width=2,
            )

            self.plots.append(plot)

# --------------------------------------------------------------
# Hidden trigger button: lets an embedding client (e.g. the Qt
# sidebar's "Clear Plot" button) clear the live data in place,
# without reloading the page or dropping this session, by
# "clicking" it via injected JavaScript matched on its label.
# --------------------------------------------------------------
        self.clear_button = Button(
            label="ClearTrigger",
            visible=False,
        )
        self.clear_button.on_click(self.clear_data)

        self.layout = column(
            self.clear_button,
            *self.plots,
            sizing_mode="stretch_both",
        )

    def attach_doc(self, doc):
        """
        Attach this instance to exactly one Bokeh Document.

        This method is called on a newly-created LIABokehPlot instance
        for each /lia browser session.
        """
        if self.doc is not None:
            raise RuntimeError(
                "This LIABokehPlot instance is already attached "
                "to a Bokeh document."
            )

        self.doc = doc
        self.start = time.time()

        doc.theme = "dark_minimal"
        doc.add_root(self.layout)

        try:
            self.lia.connect()
            print(
                "LIA Bokeh connected to "
                f"{self.host}:{self.port}"
            )
        except Exception as exc:
            print(
                "LIA Bokeh connection error:",
                exc,
            )

# --------------------------------------------------------------
# Poll the FPGA from a background thread rather than a Bokeh
# periodic callback. The register read is a *blocking* socket
# call; running it directly on the Tornado IOLoop (as a periodic
# callback would) stalls that loop for the whole round trip, and
# if a round trip ever takes longer than update_time, callbacks
# queue up behind it -- including the hidden Clear button's click
# event, which is why "Clear" could appear to do nothing until the
# backlog drained. Only the quick, non-blocking add_next_tick_callback
# push below touches the IOLoop, so browser events keep being
# handled promptly regardless of FPGA round-trip time.
# --------------------------------------------------------------
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="lia-poll",
            daemon=True,
        )
        self._poll_thread.start()

        doc.on_session_destroyed(
            self._session_destroyed
        )

        print(
            "LIA Bokeh acquisition started every "
            f"{self.update_time} ms (background thread)"
        )

    def _poll_loop(self) -> None:
        """Background-thread polling loop (never touches Bokeh models
        directly -- only schedules add_next_tick_callback, which is
        the thread-safe way in and out of the document's IOLoop).
        """
        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            if not self.lia.connected:
                connect_start = time.monotonic()
                try:
                    self.lia.connect()
                    duration = time.monotonic() - connect_start
                    if duration > 0.25:
                        print(
                            f"[LIA] reconnect took {duration:.2f}s"
                        )
                except Exception as exc:
                    duration = time.monotonic() - connect_start
                    print(
                        f"[LIA] reconnect FAILED after "
                        f"{duration:.2f}s: {exc}"
                    )
                    self._stop_event.wait(
                        self.update_time / 1000.0
                    )
                    continue

            try:
                read_start = time.monotonic()
                measurement = self.lia.get_measurement()
                read_duration = time.monotonic() - read_start
                if read_duration > 0.25:
                    print(
                        f"[LIA] read took {read_duration:.2f}s"
                    )
                elapsed = time.time() - self.start

                doc = self.doc
                if doc is not None:
                    schedule_start = time.monotonic()
                    doc.add_next_tick_callback(
                        lambda elapsed=elapsed, measurement=measurement: (
                            self._push_measurement(elapsed, measurement)
                        )
                    )
                    schedule_duration = (
                        time.monotonic() - schedule_start
                    )
                    if schedule_duration > 0.25:
                        print(
                            "[LIA] add_next_tick_callback took "
                            f"{schedule_duration:.2f}s"
                        )
            except Exception as exc:
                print(
                    "LIA Bokeh acquisition error:",
                    exc,
                )
                # self.lia.connected only checks that a socket object
                # exists, not that it's actually still usable -- so
                # without this, a broken socket (peer closed, reset,
                # etc.) never gets replaced: every future iteration
                # re-enters this same except block on the same dead
                # socket, forever, instead of ever reconnecting.
                self.lia.disconnect()

# Pace to roughly update_time, minus however long the blocking
# read itself just took, so a slow FPGA response doesn't pile up
# a backlog of pending updates.
            spent = time.monotonic() - loop_start
            self._stop_event.wait(
                max(0.0, (self.update_time / 1000.0) - spent)
            )

    def _push_measurement(self, elapsed, measurement) -> None:
        source = self.source
        if source is None:
            return

        source.stream(
            {
                "x": [elapsed],
                "I": [measurement["I"]],
                "Q": [measurement["Q"]],
                "R": [measurement["R"]],
                "Phase": [measurement["phase"]],
            },
            rollover=self.roll_over,
        )

# Diagnostic: confirm rollover is actually capping the dataset.
# If this ever prints, the source is growing unbounded, which
# would explain both an increasingly laggy plot and an
# increasingly slow Clear (more data/DOM = more work competing
# for the browser's single UI thread on every redraw).
        n_points = len(source.data["x"])
        if n_points > self.roll_over:
            print(
                f"[LIA] WARNING: source has {n_points} points, "
                f"expected <= roll_over ({self.roll_over})"
            )
        elif n_points % 200 == 0:
            print(
                f"[LIA] source size OK: {n_points} points "
                f"(roll_over={self.roll_over})"
            )

    def _session_destroyed(self, session_context):
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None

        self.lia.disconnect()

        print("LIA Bokeh session disconnected.")

    def update_roll_over(self, rollover):
        self.roll_over = max(
            1,
            int(rollover),
        )

    def update_interval(self, interval_ms):
        interval_ms = max(
            10,
            int(interval_ms),
        )
        self.update_time = interval_ms
# No Bokeh periodic callback to restart: the background poll
# thread reads self.update_time fresh on every loop iteration, so
# the new pace takes effect on its next cycle automatically.

    def clear_data(self):
        print(
            f"[LIA] clear_data() invoked at "
            f"{time.strftime('%H:%M:%S')}"
        )
        if self.doc is None:
            return

        source = self.source
        if source is None:
            return

        def clear():
            print(
                f"[LIA] clear() actually ran at "
                f"{time.strftime('%H:%M:%S')}"
            )
            source.data = {
                "x": np.array([], dtype=float),
                "I": np.array([], dtype=float),
                "Q": np.array([], dtype=float),
                "R": np.array([], dtype=float),
                "Phase": np.array([], dtype=float),
            }
            self.start = time.time()

        self.doc.add_next_tick_callback(clear)

    def save_current_data(self, filename):
        source = self.source
        if source is None:
            return

        data = {
            "time": source.data["x"],
            "X": source.data["I"],
            "Y": source.data["Q"],
            "R": source.data["R"],
            "Phase": source.data["Phase"],
        }

        pd.DataFrame(data).to_csv(
            filename,
            index=False,
        )