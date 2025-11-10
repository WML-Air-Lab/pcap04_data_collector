import sys
import time
import math
import random
import csv
from datetime import datetime
from collections import deque
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox,
                             QLabel, QComboBox, QFileDialog)
from PyQt5.QtCore import QBasicTimer
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import serial.tools.list_ports


PLOT_HISTORY = 100
MAX_PORTS = 5
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
        self.active_ports = []
        self.test_mode = False
        self.is_logging = False
        self.paused = False
        self.start_time = None
        self.time_buffer = deque(maxlen=PLOT_HISTORY)
        self.plot_buffers = []
        self.lines = []
        self.channel_map = {}
        self.test_phases = [[random.uniform(0, 2*math.pi) for _ in range(CHANNELS_PER_PORT)] for _ in range(MAX_PORTS)]

        self.plot_update_counter = 0
        self.plot_update_interval = 10  # update plot every 10 ticks (10ms * 10 = 100ms → 10Hz)


        # CSV management
        self.csv_columns = ["timestamp", "datetime"]
        self.csv_rows = []

        # GUI: COM ports
        for i in range(MAX_PORTS):
            port_layout = QHBoxLayout()
            lbl = QLabel(f"Device {i+1}:")
            port_cb = QComboBox()
            port_cb.addItem("")  # empty by default
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
            ch_boxes = []
            for c in range(CHANNELS_PER_PORT):
                ch_box = QCheckBox(str(c+1))
                ch_box.setEnabled(False)
                ch_box.stateChanged.connect(lambda state, idx=i, ch=c: self.on_channel_toggled(idx, ch))
                ch_layout.addWidget(ch_box)
                ch_boxes.append(ch_box)
                # Add CSV column for every possible channel
                self.csv_columns.append(f"{port_cb.currentText() or f'COM{i+1}'}_ch{c+1}")
            self.layout.addLayout(ch_layout)
            self.channel_checkboxes.append(ch_boxes)

        # Test mode
        self.test_cb = QCheckBox("Test Mode")
        self.test_cb.stateChanged.connect(self.on_test_mode_toggled)
        self.layout.addWidget(self.test_cb)
        
        refresh_btn = QPushButton("Refresh Ports")
        refresh_btn.clicked.connect(self.refresh_ports)
        self.layout.addWidget(refresh_btn)

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
        self.layout.addLayout(btn_layout)

        


        # Figure
        self.fig = Figure()
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.layout.addWidget(self.canvas)

        # Timer
        self.timer = QBasicTimer()

    # --- GUI callbacks ---
    def on_activate_toggled(self, idx):
        cb = self.port_selectors[idx][1]
        for ch in self.channel_checkboxes[idx]:
            ch.setEnabled(cb.isChecked())
        if cb.isChecked():
            port = self.port_selectors[idx][0].currentText()
            if port and port not in self.active_ports:
                self.active_ports.append(port)
        else:
            port = self.port_selectors[idx][0].currentText()
            if port in self.active_ports:
                self.active_ports.remove(port)

    def refresh_ports(self):
        available_ports = [p.device for p in serial.tools.list_ports.comports()]
        for port_cb, _ in self.port_selectors:
            current = port_cb.currentText()
            port_cb.clear()
            port_cb.addItem("")
            port_cb.addItems(available_ports)
            if current in available_ports:
                port_cb.setCurrentText(current)


    def on_test_mode_toggled(self):
        self.test_mode = self.test_cb.isChecked()

    def on_channel_toggled(self, idx, ch):
        ch_box = self.channel_checkboxes[idx][ch]
        port_cb, _ = self.port_selectors[idx]

        if ch_box.isChecked():
            buffer = deque(maxlen=PLOT_HISTORY)
            line, = self.ax.plot([], [], label=f'{port_cb.currentText() or "Test"}_ch{ch+1}')
            self.plot_buffers.append(buffer)
            self.lines.append(line)
            self.channel_map[(idx, ch)] = {'line': line, 'buffer': buffer}

            # First value in test mode
            if self.test_mode:
                timestamp = round(time.time() - self.start_time, 2) if self.start_time else 0
                val = 50 + 20*math.sin(2*math.pi*0.1*timestamp + self.test_phases[idx][ch]) + random.uniform(-1,1)
                buffer.append(val)
                if not self.time_buffer or self.time_buffer[-1] != timestamp:
                    self.time_buffer.append(timestamp)

            self.ax.relim()
            self.ax.autoscale_view()
            self.ax.legend()
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
                self.ax.relim()
                self.ax.autoscale_view()
                self.ax.legend()
                self.canvas.draw_idle()

    # --- Logging controls ---
    def start_logging(self):
        if not self.start_time:
            self.start_time = time.time()
        self.is_logging = True
        self.paused = False
        if not self.timer.isActive():
            self.timer.start(10, self)  # 100 Hz

    def pause_logging(self):
        self.paused = not self.paused

    def stop_logging(self):
        self.is_logging = False
        self.paused = False
        self.timer.stop()
        self.start_time = None
        self.time_buffer.clear()
        self.plot_buffers.clear()
        self.lines.clear()
        self.channel_map.clear()
        self.ax.clear()
        self.canvas.draw_idle()

    def clear_figure(self):
        self.time_buffer.clear()
        for buf in self.plot_buffers:
            buf.clear()
        self.ax.clear()
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
        if self.paused or not self.is_logging:
            return

        timestamp = round(time.time() - self.start_time, 2)
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.time_buffer.append(timestamp)

        # Build CSV row with empty values for inactive channels
        row = [timestamp, dt]
        for i in range(MAX_PORTS):
            for c in range(CHANNELS_PER_PORT):
                if (i, c) in self.channel_map:
                    buf = self.channel_map[(i,c)]['buffer']
                    if self.test_mode:
                        val = 50 + 20*math.sin(2*math.pi*0.1*timestamp + self.test_phases[i][c]) + random.uniform(-1,1)
                    else:
                        val = random.uniform(0,100)  # replace with real COM read
                    buf.append(val)
                    row.append(round(val,2))
                else:
                    row.append("")
        self.csv_rows.append(row)

        # Only update plot every plot_update_interval ticks
        self.plot_update_counter += 1
        if self.plot_update_counter >= self.plot_update_interval:
            self.plot_update_counter = 0
            for (i,c), data in self.channel_map.items():
                buf = data['buffer']
                line = data['line']
                min_len = min(len(self.time_buffer), len(buf))
                line.set_data(list(self.time_buffer)[-min_len:], list(buf)[-min_len:])
            self.ax.relim()
            self.ax.autoscale_view()
            if self.lines:
                self.ax.legend()
            self.canvas.draw_idle()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = DataCollector()
    win.show()
    sys.exit(app.exec_())
