"""
Test Client - Exercise server functions (audio streaming, AMD, call simulation).
Run with console visible for log output. Does NOT send log_disposition (no DB pollution).
"""
import sys
import queue
import threading
from pathlib import Path
from datetime import datetime

# Add parent for Installer.modules when running from project
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

try:
    from Installer.modules.streamer import AudioStreamer
    from Installer.modules.livekit_streamer import LiveKitStreamer
    from Installer.modules.audio_core import AudioCapture
except ImportError:
    from modules.streamer import AudioStreamer
    from modules.livekit_streamer import LiveKitStreamer
    from modules.audio_core import AudioCapture

import config

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QRadioButton, QCheckBox, QComboBox, QLabel,
    QGroupBox, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import QTimer, Qt


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class AudioWorker(threading.Thread):
    """Capture audio from mic+loopback and put into queue."""
    def __init__(self, capture: AudioCapture, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.capture = capture
        self.out_queue = out_queue
        self._stop = threading.Event()

    def run(self):
        if not self.capture.start_capture(
            self.capture.mic_device_index,
            self.capture.loopback_device_index,
        ):
            _log("AudioWorker: Failed to start capture")
            return
        while not self._stop.is_set():
            chunk = self.capture.get_mono_mixed_frame_from_queue()
            if chunk is not None:
                try:
                    self.out_queue.put_nowait(chunk)
                except queue.Full:
                    try:
                        self.out_queue.get_nowait()
                        self.out_queue.put_nowait(chunk)
                    except queue.Empty:
                        pass
            else:
                self._stop.wait(0.01)
        self.capture.stop_capture()

    def stop(self):
        self._stop.set()


class TestClientWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Test Client")
        self.setMinimumWidth(450)

        self.streamer = None
        self.audio_capture = None
        self.audio_worker = None
        self.audio_queue = queue.Queue(maxsize=100)
        self._call_active = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Connection
        conn_g = QGroupBox("Connection")
        conn_l = QVBoxLayout(conn_g)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Server URL:"))
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("ws://192.168.1.46/ws")
        self.server_edit.setText(getattr(config, "SERVER_WS_URL", "ws://192.168.1.46/ws"))
        row1.addWidget(self.server_edit)
        conn_l.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("API Base:"))
        self.api_edit = QLineEdit()
        self.api_edit.setPlaceholderText("http://192.168.1.46")
        self.api_edit.setText(getattr(config, "SERVER_API_URL", "http://192.168.1.46"))
        row2.addWidget(self.api_edit)
        conn_l.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Agent ID:"))
        self.agent_edit = QLineEdit()
        self.agent_edit.setText(getattr(config, "DEFAULT_AGENT_ID", "test_agent"))
        row3.addWidget(self.agent_edit)
        conn_l.addLayout(row3)

        self.ws_radio = QRadioButton("WebSocket")
        self.lk_radio = QRadioButton("LiveKit")
        self.ws_radio.setChecked(True)
        conn_l.addWidget(self.ws_radio)
        conn_l.addWidget(self.lk_radio)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        conn_l.addLayout(btn_row)
        layout.addWidget(conn_g)

        # Audio
        audio_g = QGroupBox("Audio Source")
        audio_l = QVBoxLayout(audio_g)
        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("Mic:"))
        self.mic_combo = QComboBox()
        dev_row.addWidget(self.mic_combo)
        dev_row.addWidget(QLabel("System:"))
        self.loopback_combo = QComboBox()
        dev_row.addWidget(self.loopback_combo)
        audio_l.addLayout(dev_row)
        self._populate_audio_devices()
        layout.addWidget(audio_g)

        # AMD
        self.amd_cb = QCheckBox("Enable AMD (send call_started when simulating)")
        self.amd_cb.setChecked(True)
        layout.addWidget(self.amd_cb)

        # Simulate Call
        call_g = QGroupBox("Simulate Call (Start = connect + stream + call_started)")
        call_l = QVBoxLayout(call_g)
        btn_row2 = QHBoxLayout()
        self.start_call_btn = QPushButton("▶ Start Call")
        self.end_call_btn = QPushButton("■ End Call")
        self.end_call_btn.setEnabled(False)
        self.start_call_btn.clicked.connect(self._on_start_call)
        self.end_call_btn.clicked.connect(self._on_end_call)
        btn_row2.addWidget(self.start_call_btn)
        btn_row2.addWidget(self.end_call_btn)
        call_l.addLayout(btn_row2)
        layout.addWidget(call_g)

        # File Playback (optional)
        file_g = QGroupBox("File Playback (optional)")
        file_l = QHBoxLayout(file_g)
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText("Select WAV file...")
        self.browse_btn = QPushButton("Browse...")
        self.play_file_btn = QPushButton("Play File → Stream")
        self.play_file_btn.setEnabled(False)
        self.browse_btn.clicked.connect(self._on_browse)
        self.play_file_btn.clicked.connect(self._on_play_file)
        file_l.addWidget(self.file_edit)
        file_l.addWidget(self.browse_btn)
        file_l.addWidget(self.play_file_btn)
        layout.addWidget(file_g)

        layout.addStretch()

        # Message poll timer
        self.msg_timer = QTimer()
        self.msg_timer.timeout.connect(self._poll_messages)
        self.msg_timer.start(100)

    def _populate_audio_devices(self):
        try:
            cap = AudioCapture()
            cap.auto_select_devices()
            mics = cap.get_input_devices()
            systems = cap.get_system_audio_devices()
            if not systems:
                systems = cap.get_all_recording_devices()
            for idx, info in mics:
                self.mic_combo.addItem(info.get("name", str(idx)), idx)
            for idx, info in systems:
                self.loopback_combo.addItem(info.get("name", str(idx)), idx)
            if cap.mic_device_index is not None:
                for i in range(self.mic_combo.count()):
                    if self.mic_combo.itemData(i) == cap.mic_device_index:
                        self.mic_combo.setCurrentIndex(i)
                        break
            if cap.loopback_device_index is not None:
                for i in range(self.loopback_combo.count()):
                    if self.loopback_combo.itemData(i) == cap.loopback_device_index:
                        self.loopback_combo.setCurrentIndex(i)
                        break
        except Exception as e:
            _log(f"Could not list audio devices: {e}")

    def _get_streamer(self):
        if self.streamer is None:
            return None
        return self.streamer

    def _on_connect(self):
        agent_id = self.agent_edit.text().strip() or "test_agent"
        if self.lk_radio.isChecked():
            api_base = self.api_edit.text().strip() or "http://192.168.1.46"
            lk_url = getattr(config, "LIVEKIT_URL", "ws://192.168.1.46:7880")
            self.streamer = LiveKitStreamer(lk_url, agent_id, api_base)
        else:
            ws_url = self.server_edit.text().strip() or "ws://192.168.1.46/ws"
            self.streamer = AudioStreamer(ws_url, agent_id=agent_id)
        self.streamer.start()
        _log("Connecting...")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)

    def _on_disconnect(self):
        s = self._get_streamer()
        if s:
            s.stop()
            self.streamer = None
            _log("Disconnected")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.start_call_btn.setEnabled(True)
        self.end_call_btn.setEnabled(False)
        self._call_active = False

    def _on_start_call(self):
        s = self._get_streamer()
        if not s:
            QMessageBox.warning(self, "Not Connected", "Connect first.")
            return
        agent_id = s.agent_id
        if self.amd_cb.isChecked():
            s.send_json_message({"type": "call_started", "agent_id": agent_id})
            _log("Sent call_started (AMD enabled)")
        s.send_metadata({"status": "Calling", "lead": "555-TEST", "contact": "Test Contact"})
        # Start audio capture and streaming
        try:
            self.audio_capture = AudioCapture()
            midx = self.mic_combo.currentData()
            lidx = self.loopback_combo.currentData()
            self.audio_capture.mic_device_index = midx if midx is not None else self.audio_capture.mic_device_index
            self.audio_capture.loopback_device_index = lidx if lidx is not None else self.audio_capture.loopback_device_index
            if self.audio_capture.mic_device_index is None or self.audio_capture.loopback_device_index is None:
                self.audio_capture.auto_select_devices()
            self.audio_worker = AudioWorker(self.audio_capture, self.audio_queue)
            self.audio_worker.start()
            s.start_audio_streaming(self.audio_queue)
            _log("Audio streaming started")
            self._call_active = True
            self.start_call_btn.setEnabled(False)
            self.end_call_btn.setEnabled(True)
        except Exception as e:
            _log(f"Failed to start audio: {e}")
            QMessageBox.critical(self, "Audio Error", str(e))

    def _on_end_call(self):
        s = self._get_streamer()
        if s:
            s.send_json_message({"status": "Call Ended"})
            s.send_metadata({"status": "Call Ended"})  # Update dashboard display
            _log("Sent Call Ended")
        if self.audio_worker:
            self.audio_worker.stop()
            self.audio_worker.join(timeout=2.0)
            self.audio_worker = None
        if self.audio_capture:
            self.audio_capture = None
        if s:
            s.stop_audio_streaming()
        self._call_active = False
        self.start_call_btn.setEnabled(True)
        self.end_call_btn.setEnabled(False)
        _log("Call ended, streaming stopped")

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select WAV", "", "WAV (*.wav);;All (*)")
        if path:
            self.file_edit.setText(path)
            self.play_file_btn.setEnabled(True)

    def _on_play_file(self):
        path = self.file_edit.text().strip()
        if not path:
            return
        s = self._get_streamer()
        if not s:
            QMessageBox.warning(self, "Not Connected", "Connect and Start Call first.")
            return
        try:
            import wave
            with wave.open(path, "rb") as wf:
                # Expect 48k, 2ch, 16bit
                while True:
                    data = wf.readframes(4800)  # 50ms at 48k stereo
                    if not data:
                        break
                    import numpy as np
                    arr = np.frombuffer(data, dtype=np.int16)
                    try:
                        self.audio_queue.put_nowait(arr)
                    except queue.Full:
                        pass
            _log("File play finished")
        except Exception as e:
            _log(f"File play error: {e}")

    def _poll_messages(self):
        s = self._get_streamer()
        if not s:
            return
        while True:
            msg = s.get_message(timeout=0)
            if msg is None:
                break
            _log(f"RECV: {msg}")

    def closeEvent(self, event):
        self._on_end_call()
        self._on_disconnect()
        self.msg_timer.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = TestClientWindow()
    win.show()
    _log("Test Client started. Output appears here.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
