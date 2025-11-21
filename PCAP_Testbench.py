import sys
import time
import math
import random
import csv
from datetime import datetime
from collections import deque
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox,
                             QLabel, QComboBox, QLineEdit, QFileDialog)
from PyQt5.QtCore import QBasicTimer, Qt
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import serial.tools.list_ports
import re


PLOT_HISTORY = 5000
MAX_PORTS = 1
CHANNELS_PER_PORT = 6

#todo MOVE Legend to outside


class DataCollector(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Data Collector")
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.port_selectors = []
        self.channel_checkboxes = []
        self.channel_status_labels = []
        self.channel_zero_buttons = []
        self.active_ports = []
        self.status_labels = []
        # per-sensor zero offsets keyed by (port_name, ch)
        self.zero_offsets = {}
        self.test_mode = False
        self.is_logging = False
        self.paused = False
        self.start_time = None
        self.time_buffer = deque(maxlen=PLOT_HISTORY)
        self.plot_buffers = []
        self.lines = []
        self.channel_map = {}
        # Serial connections and latest parsed values per port
        self.serial_conns = {}       # port_name -> serial.Serial
        self.serial_latest = {}      # port_name -> [val_ch0, val_ch1, ...]
        self.test_phases = [[random.uniform(0, 2*math.pi) for _ in range(CHANNELS_PER_PORT)] for _ in range(MAX_PORTS)]
        self.resize(1200, 800)      # initial size
        self.setMinimumSize(800, 600)  # optional lower limit



        self.plot_update_counter = 0
        self.plot_update_interval = 10  # update plot every 10 ticks (10ms * 10 = 100ms → 10Hz)


        # CSV management
        self.csv_columns = ["timestamp", "datetime"]
        self.csv_rows = []
        # Calibration storage: per-port temporary points and final curve (slope, intercept)
        self.cal_points_temp = {}  # port_str -> [None, None, None] each element (load_value, measured_value)
        self.cal_curves = {}       # port_str -> (slope, intercept)

        # GUI: COM ports
        for i in range(MAX_PORTS):
            port_layout = QHBoxLayout()
            lbl = QLabel(f"Device {i+1}:")
            port_cb = QComboBox()
            port_cb.addItem("Select Device...")
            # 🔍 Populate only active COM ports
            available_ports = serial.tools.list_ports.comports()
            for port in available_ports:
                port_cb.addItem(port.device)

            activate_cb = QCheckBox("Activate")
            activate_cb.stateChanged.connect(lambda state, idx=i: self.on_activate_toggled(idx))

            port_layout.addWidget(lbl)
            port_layout.addWidget(port_cb)
            port_layout.addWidget(activate_cb)

            self.layout.addLayout(port_layout)
            self.port_selectors.append((port_cb, activate_cb))

            # Channels
            ch_layout = QHBoxLayout()
            ch_layout.setAlignment(Qt.AlignLeft)
            ch_boxes = []
            ch_statuses = []
            ch_zero_buttons = []
            for c in range(CHANNELS_PER_PORT):
                ch_box = QCheckBox(str(c+1))
                ch_box.setEnabled(False)
                ch_box.stateChanged.connect(lambda state, idx=i, ch=c: self.on_channel_toggled(idx, ch))
                ch_layout.addWidget(ch_box)

                # Status label per sensor (yellow=not calibrated, green=calibrated)
                ch_status = QLabel()
                ch_status.setFixedSize(12, 12)
                ch_status.setStyleSheet("background-color: yellow; border: 1px solid black;")
                ch_layout.addWidget(ch_status)

                ch_boxes.append(ch_box)
                ch_statuses.append(ch_status)

                # Zero button for this sensor
                zbtn = QPushButton("Zero")
                zbtn.setFixedWidth(40)
                zbtn.clicked.connect(lambda _, idx=i, ch=c: self.zero_sensor(idx, ch))
                ch_layout.addWidget(zbtn)
                ch_zero_buttons.append(zbtn)
                # small gap between sensors
                ch_layout.addSpacing(520)

                # Add CSV column for every possible channel
                self.csv_columns.append(f"{port_cb.currentText() or f'Device{i+1}'}_ch{c+1}")
            self.layout.addLayout(ch_layout)
            self.channel_checkboxes.append(ch_boxes)
            self.channel_status_labels.append(ch_statuses)
            self.channel_zero_buttons.append(ch_zero_buttons)

        # Test mode
        self.test_cb = QCheckBox("Test Mode")
        self.test_cb.stateChanged.connect(self.on_test_mode_toggled)
        self.layout.addWidget(self.test_cb)
        
        refresh_btn = QPushButton("Refresh Ports")
        refresh_btn.clicked.connect(self.refresh_ports)
        self.layout.addWidget(refresh_btn)

        # --- Calibration UI ---
        cal_layout = QHBoxLayout()
        cal_layout.addWidget(QLabel("Active Sensor:"))
        self.sensor_dropdown = QComboBox()
        self.sensor_dropdown.addItem("")
        cal_layout.addWidget(self.sensor_dropdown)

        # Three load input boxes
        self.load_inputs = []
        for i in range(3):
            le = QLineEdit()
            le.setPlaceholderText(f"Load {i+1}")
            le.setFixedWidth(80)
            self.load_inputs.append(le)
            cal_layout.addWidget(le)

        # Calibration point buttons
        self.cal_buttons = []
        for i in range(1, 4):
            b = QPushButton(f"Cal Point {i}")
            b.clicked.connect(lambda _, n=i: self.cal_point(n))
            self.cal_buttons.append(b)
            cal_layout.addWidget(b)

        # Confirm calibration
        self.confirm_cal_btn = QPushButton("Confirm Calibration")
        self.confirm_cal_btn.clicked.connect(self.confirm_calibration)
        cal_layout.addWidget(self.confirm_cal_btn)

        self.layout.addLayout(cal_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_logging)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_logging)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_logging)
        self.clear_btn = QPushButton("Clear Figure")
        self.clear_btn.clicked.connect(self.clear_figure)
        self.save_btn = QPushButton("Save CSV")
        self.save_btn.clicked.connect(self.save_csv)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.pause_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addWidget(self.save_btn)
        # Recording status indicator
        self.record_status_label = QLabel("Not recording")
        self.record_status_label.setStyleSheet("font-weight: bold; color: red;")
        btn_layout.addWidget(self.record_status_label)
        self.layout.addLayout(btn_layout)

        


        # Figure
        self.fig = Figure()
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.layout.addWidget(self.canvas)

        # Timer
        self.timer = QBasicTimer()
        # Start a timer for live plotting always (plot updates continue even when not recording)
        self.plot_start_time = time.time()
        if not self.timer.isActive():
            self.timer.start(self.plot_update_interval, self)  # 100 Hz

    # --- GUI callbacks ---
    def on_activate_toggled(self, idx):
        cb = self.port_selectors[idx][1]
        for ch in self.channel_checkboxes[idx]:
            ch.setEnabled(cb.isChecked())
        if cb.isChecked():
            port = self.port_selectors[idx][0].currentText()
            if port and port not in self.active_ports:
                self.active_ports.append(port)
            # open serial for this port
            if port:
                self.open_serial_port(port)
        else:
            port = self.port_selectors[idx][0].currentText()
            if port in self.active_ports:
                self.active_ports.remove(port)
            # close serial for this port
            if port:
                self.close_serial_port(port)
        # Keep the active sensor dropdown in sync
        try:
            self.update_active_sensors_dropdown()
        except Exception:
            pass

    def refresh_ports(self):
        available_ports = [p.device for p in serial.tools.list_ports.comports()]
        for idx, (port_cb, act_cb) in enumerate(self.port_selectors):
            current = port_cb.currentText()
            port_cb.clear()
            port_cb.addItem("")
            port_cb.addItems(available_ports)
            if current in available_ports:
                port_cb.setCurrentText(current)
            # if the port selection changed while active, reopen the serial connection
            new_port = port_cb.currentText()
            if act_cb.isChecked():
                # if different from tracked serial connections, close old and open new
                for p in list(self.serial_conns.keys()):
                    if p != new_port and p not in [pb.currentText() for pb, _ in self.port_selectors if _.isChecked()]:
                        self.close_serial_port(p)
                if new_port and new_port not in self.serial_conns:
                    self.open_serial_port(new_port)
        # Update active sensors dropdown after refreshing
        try:
            self.update_active_sensors_dropdown()
        except Exception:
            pass


    def on_test_mode_toggled(self):
        self.test_mode = self.test_cb.isChecked()

    # --- Calibration helpers ---
    def update_active_sensors_dropdown(self):
        # Repopulate dropdown with currently active ports
        self.sensor_dropdown.clear()
        self.sensor_dropdown.addItem("")
        # Add each active sensor as PORT_chN
        for idx, (port_cb, act_cb) in enumerate(self.port_selectors):
            port = port_cb.currentText()
            if port and act_cb.isChecked():
                for c in range(CHANNELS_PER_PORT):
                    self.sensor_dropdown.addItem(f"{port}_ch{c+1}")

    # --- Serial helpers ---
    def open_serial_port(self, port_name):
        if not port_name:
            return
        if port_name in self.serial_conns:
            return
        try:
            ser = serial.Serial(port_name, baudrate=115200, timeout=0)
            self.serial_conns[port_name] = ser
            # initialize latest values array
            self.serial_latest[port_name] = [None] * CHANNELS_PER_PORT
            # send a simple "OK" notification to the board on selection
            try:
                ser.write(b"OK")
                ser.flush()
            except Exception:
                pass
            print(f"Opened serial {port_name}")
        except Exception as e:
            print(f"Failed to open {port_name}: {e}")

    def close_serial_port(self, port_name):
        ser = self.serial_conns.get(port_name)
        if ser:
            try:
                ser.close()
            except Exception:
                pass
        self.serial_conns.pop(port_name, None)
        self.serial_latest.pop(port_name, None)
        print(f"Closed serial {port_name}")

    def read_serial_from_port(self, port_name, max_lines=20):
        """Read up to max_lines from serial port and update self.serial_latest[port_name].
        Attempts to parse numeric values from lines; supports comma-separated values or any floats found.
        """
        ser = self.serial_conns.get(port_name)
        if not ser:
            return
        try:
            lines_read = 0
            while ser.in_waiting and lines_read < max_lines:
                raw = ser.readline()
                lines_read += 1
                if not raw:
                    continue
                try:
                    s = raw.decode('utf-8', errors='ignore').strip()
                except Exception:
                    s = str(raw)
                if not s:
                    continue
                # extract floats from the line
                nums = re.findall(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", s)
                if not nums:
                    continue
                vals = [float(x) for x in nums]
                if len(vals) >= CHANNELS_PER_PORT:
                    # take first CHANNELS_PER_PORT values
                    self.serial_latest[port_name] = vals[:CHANNELS_PER_PORT]
                else:
                    # if single value, assign to channel 0; if multiple but fewer than channels, map to first N
                    for i, v in enumerate(vals):
                        if i < CHANNELS_PER_PORT:
                            self.serial_latest[port_name][i] = v
        except Exception as e:
            # on serial errors, close the connection to avoid repeated exceptions
            print(f"Serial read error on {port_name}: {e}")
            self.close_serial_port(port_name)

    def get_current_reading_for_device(self, idx):
        # Return the most recent measurement for the device index by averaging
        # the latest values from any active channels for that device.
        vals = []
        for c in range(CHANNELS_PER_PORT):
            key = (idx, c)
            if key in self.channel_map:
                buf = self.channel_map[key]['buffer']
                if buf:
                    vals.append(buf[-1])
        if not vals:
            return None
        return sum(vals) / len(vals)

    def get_current_reading_for_sensor(self, port_name, ch):
        # Find port index
        idx = None
        for i, (port_cb, _) in enumerate(self.port_selectors):
            if port_cb.currentText() == port_name:
                idx = i
                break
        if idx is None:
            return None
        key = (idx, ch)
        if key in self.channel_map:
            buf = self.channel_map[key]['buffer']
            if buf:
                return buf[-1]
        return None

    def cal_point(self, n):
        # Capture calibration point n (1..3): read load textbox and current measured value
        sel = self.sensor_dropdown.currentText()
        if not sel:
            return
        # parse selection like 'COM3_ch2'
        if "_ch" not in sel:
            return
        port_name, ch_str = sel.rsplit("_ch", 1)
        try:
            ch = int(ch_str) - 1
        except Exception:
            return
        # parse load value
        try:
            load_val = float(self.load_inputs[n-1].text())
        except Exception:
            load_val = None
        meas = self.get_current_reading_for_sensor(port_name, ch)
        key = (port_name, ch)
        pts = self.cal_points_temp.get(key, [None, None, None])
        pts[n-1] = (load_val, meas)
        self.cal_points_temp[key] = pts
        # visually mark button
        try:
            self.cal_buttons[n-1].setStyleSheet("background-color: lightgreen")
        except Exception:
            pass

    def confirm_calibration(self):
        sel = self.sensor_dropdown.currentText()
        if not sel or "_ch" not in sel:
            return
        port_name, ch_str = sel.rsplit("_ch", 1)
        try:
            ch = int(ch_str) - 1
        except Exception:
            return
        key = (port_name, ch)
        pts = self.cal_points_temp.get(key, [None, None, None])
        pairs = [(p[0], p[1]) for p in pts if p and p[0] is not None and p[1] is not None]
        if len(pairs) < 2:
            return
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        x_mean = sum(xs) / len(xs)
        y_mean = sum(ys) / len(ys)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
        den = sum((x - x_mean) ** 2 for x in xs)
        slope = num / den if den != 0 else 0.0
        intercept = y_mean - slope * x_mean
        # Save curve for that sensor
        self.cal_curves[key] = (slope, intercept)
        # update status label for that sensor to green
        for i, (port_cb, _) in enumerate(self.port_selectors):
            if port_cb.currentText() == port_name:
                try:
                    self.channel_status_labels[i][ch].setStyleSheet("background-color: green; border: 1px solid black;")
                except Exception:
                    pass
                break
        # reset cal button visuals
        for b in self.cal_buttons:
            try:
                b.setStyleSheet("")
            except Exception:
                pass

    def zero_sensor(self, idx, ch):
        # Capture the current reading for sensor (port_name, ch) and store as offset
        port_name = self.port_selectors[idx][0].currentText()
        cur = self.get_current_reading_for_sensor(port_name, ch)
        if cur is None:
            # fallback to latest serial value
            if port_name and port_name in self.serial_latest:
                vlist = self.serial_latest.get(port_name)
                if vlist and len(vlist) > ch:
                    cur = vlist[ch]
        if cur is None:
            cur = 0.0

        if(port_name, ch) not in self.zero_offsets:
            self.zero_offsets[(port_name, ch)] = 0.0
        
        # store offset so incoming values will be shifted by this amount
        self.zero_offsets[(port_name, ch)] = self.zero_offsets[(port_name, ch)] + cur        



    def on_channel_toggled(self, idx, ch):
        ch_box = self.channel_checkboxes[idx][ch]
        port_cb, _ = self.port_selectors[idx]

        if ch_box.isChecked():
            buffer = deque(maxlen=PLOT_HISTORY)
            line, = self.ax.plot([], [], label=f'{port_cb.currentText() or "Test"}_ch{ch+1}')
            self.plot_buffers.append(buffer)
            self.lines.append(line)
            self.channel_map[(idx, ch)] = {'line': line, 'buffer': buffer}

            # First value in test mode (use plot_start_time so plotting is continuous even when not recording)
            if self.test_mode:
                timestamp = round(time.time() - self.plot_start_time, 2)
                val = 50 + 20*math.sin(2*math.pi*0.1*timestamp + self.test_phases[idx][ch]) + random.uniform(-1,1)
                buffer.append(val)
                if not self.time_buffer or self.time_buffer[-1] != timestamp:
                    self.time_buffer.append(timestamp)

            self.ax.relim()
            self.ax.autoscale_view()
            handles, labels = self.ax.get_legend_handles_labels()
            if labels:  # Only draw legend if there are valid labels
                self.ax.legend(loc='center left', bbox_to_anchor=(1.05, 0.5), borderaxespad=0)
                self.fig.subplots_adjust(right=0.8)

            self.canvas.draw_idle()
        else:
            # Remove line and buffer
            if (idx, ch) in self.channel_map:
                line = self.channel_map[(idx,ch)]['line']
                buf = self.channel_map[(idx,ch)]['buffer']
                line.remove()
                if buf in self.plot_buffers:
                    self.plot_buffers.remove(buf)
                del self.channel_map[(idx,ch)]
                # Reset sensor status colour to yellow when channel removed (not calibrated)
                try:
                    self.channel_status_labels[idx][ch].setStyleSheet("background-color: yellow; border: 1px solid black;")
                except Exception:
                    pass
                self.ax.relim()
                self.ax.autoscale_view()
                handles, labels = self.ax.get_legend_handles_labels()
                if labels:  # Only draw legend if there are valid labels
                    self.ax.legend(loc='center left', bbox_to_anchor=(1.05, 0.5), borderaxespad=0)
                    self.fig.subplots_adjust(right=0.8)

                self.canvas.draw_idle()

    # --- Logging controls ---
    def start_logging(self):
        if not self.start_time:
            self.start_time = time.time()
        self.is_logging = True
        self.paused = False
        try:
            self.record_status_label.setText("Recording")
            self.record_status_label.setStyleSheet("font-weight: bold; color: green;")
        except Exception:
            pass

    def pause_logging(self):
        self.paused = not self.paused
        try:
            if self.is_logging and self.paused:
                self.record_status_label.setText("Recording (paused)")
                self.record_status_label.setStyleSheet("font-weight: bold; color: orange;")
            elif self.is_logging and not self.paused:
                self.record_status_label.setText("Recording")
                self.record_status_label.setStyleSheet("font-weight: bold; color: green;")
            else:
                self.record_status_label.setText("Not recording")
                self.record_status_label.setStyleSheet("font-weight: bold; color: red;")
        except Exception:
            pass

    def stop_logging(self):
        # Stop recording but keep live plotting active
        self.is_logging = False
        self.paused = False
        self.start_time = None
        try:
            self.record_status_label.setText("Not recording")
            self.record_status_label.setStyleSheet("font-weight: bold; color: red;")
        except Exception:
            pass

    def clear_figure(self):
        # Clear the shared time buffer
        self.time_buffer.clear()
        # Clear in-memory saved CSV rows so "saved data" is reset when the user
        # clicks Clear Figure. We keep csv_columns (headers) intact.
        self.csv_rows.clear()

        # Remove lines and buffers for channels that are currently unchecked in the UI.
        # For channels that remain checked, simply clear their data buffer so the
        # plotted line (and its legend entry) stays but with no points.
        for (i, c) in list(self.channel_map.keys()):
            try:
                ch_box = self.channel_checkboxes[i][c]
            except Exception:
                # If the checkbox structure changed unexpectedly, remove the channel
                entry = self.channel_map.pop((i, c), None)
                if entry:
                    try:
                        entry['line'].remove()
                    except Exception:
                        pass
                    buf = entry.get('buffer')
                    if buf in self.plot_buffers:
                        self.plot_buffers.remove(buf)
                continue

            if not ch_box.isChecked():
                # Channel is unchecked: remove its line and buffer entirely
                entry = self.channel_map.pop((i, c), None)
                if entry:
                    try:
                        entry['line'].remove()
                    except Exception:
                        pass
                    buf = entry.get('buffer')
                    if buf in self.plot_buffers:
                        self.plot_buffers.remove(buf)
            else:
                # Channel is checked: clear its data buffer but keep the plot/legend
                self.channel_map[(i, c)]['buffer'].clear()

        # Recompute limits and update legend to reflect removed channels only
        self.ax.relim()
        self.ax.autoscale_view()
        handles, labels = self.ax.get_legend_handles_labels()
        if labels:
            self.ax.legend(loc='center left', bbox_to_anchor=(1.05, 0.5), borderaxespad=0)
            self.fig.subplots_adjust(right=0.8)
        else:
            # No labels -> remove any existing legend and reset subplot spacing
            leg = self.ax.get_legend()
            if leg:
                try:
                    leg.remove()
                except Exception:
                    pass
            self.fig.subplots_adjust(right=1.0)

        # Redraw canvas
        self.canvas.draw_idle()

    def save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV files (*.csv)")
        if path:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(self.csv_columns)
                for row in self.csv_rows:
                    writer.writerow(row)

    def timerEvent(self, event):
        # Always update plotted buffers so the graph shows live data even when not recording.
        plot_timestamp = round(time.time() - self.plot_start_time, 2)
        self.time_buffer.append(plot_timestamp)

        # Attempt to read serial data for all open ports so serial_latest is up to date
        for port_name in list(self.serial_conns.keys()):
            try:
                self.read_serial_from_port(port_name)
            except Exception:
                pass

        # For each active channel, append a new sample (prefer serial data if available)
        for i in range(MAX_PORTS):
            port_name = self.port_selectors[i][0].currentText()
            for c in range(CHANNELS_PER_PORT):
                if (i, c) in self.channel_map:
                    buf = self.channel_map[(i, c)]['buffer']
                    val = None
                    # prefer live serial parsed value if available
                    if port_name and port_name in self.serial_latest:
                        vlist = self.serial_latest.get(port_name)
                        if vlist and len(vlist) > c and vlist[c] is not None:
                            val = vlist[c]
                    # fallback to test mode or placeholder
                    if val is None:
                        if self.test_mode:
                            val = 50 + 20*math.sin(2*math.pi*0.1*plot_timestamp + self.test_phases[i][c]) + random.uniform(-1, 1)
                        else:
                            val = None
                    # apply zero offset if present for this port & channel
                    if val is not None and port_name:
                        offs = self.zero_offsets.get((port_name, c))
                        if offs is not None:
                            try:
                                val = val - offs
                            except Exception:
                                pass
                    buf.append(val)

        # If currently recording (and not paused), build and save a CSV row using recording start_time
        if self.is_logging and not self.paused and self.start_time:
            csv_ts = round(time.time() - self.start_time, 2)
            dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            row = [csv_ts, dt]
            for i in range(MAX_PORTS):
                for c in range(CHANNELS_PER_PORT):
                    if (i, c) in self.channel_map:
                        buf = self.channel_map[(i, c)]['buffer']
                        val = buf[-1] if buf else ""
                        row.append(round(val, 2) if isinstance(val, (int, float)) else "")
                    else:
                        row.append("")
            self.csv_rows.append(row)

        # Only update plot every plot_update_interval ticks
        self.plot_update_counter += 1
        if self.plot_update_counter >= self.plot_update_interval:
            self.plot_update_counter = 0
            for (i, c), data in self.channel_map.items():
                buf = data['buffer']
                line = data['line']
                min_len = min(len(self.time_buffer), len(buf))
                line.set_data(list(self.time_buffer)[-min_len:], list(buf)[-min_len:])
            self.ax.relim()
            self.ax.autoscale_view()
            handles, labels = self.ax.get_legend_handles_labels()
            if labels:  # Only draw legend if there are valid labels
                self.ax.legend(loc='center left', bbox_to_anchor=(1.05, 0.5), borderaxespad=0)
                self.fig.subplots_adjust(right=0.8)
            self.canvas.draw_idle()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = DataCollector()
    win.show()
    sys.exit(app.exec_())
