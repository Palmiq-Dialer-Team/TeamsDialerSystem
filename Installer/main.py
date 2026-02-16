"""
TeamsPowerDialer - Client Application
PyQt6 GUI Entry point with dark theme
"""

# COM init before other imports (avoids 0x80010106)
import sys
sys.coinit_flags = 2

import qdarktheme
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QGridLayout, QFrame, QStatusBar,
    QFormLayout, QScrollArea, QDialog, QComboBox, QDialogButtonBox, QMessageBox,
    QDateTimeEdit, QLineEdit, QListWidget, QListWidgetItem, QTabWidget, QColorDialog,
    QSlider, QAbstractItemView, QCheckBox, QSpinBox, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QUrl, QTimer, QDateTime, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSlot, QMetaObject, QSize
from PyQt6.QtGui import QIcon, QFont, QDesktopServices, QColor, QPainter, QPen, QClipboard, QPixmap, QMovie, QShortcut, QKeySequence
from typing import List, Dict, Any, Optional
import requests
import queue
import urllib.request
import threading
import numpy as np
import os
import json

from modules.active_data_reader import ActiveExcelReader
from modules.teams_auto import TeamsController
from modules.audio_core import AudioCapture
from modules.streamer import AudioStreamer
from modules.disposition_manager import DispositionSaver
from modules.settings_manager import SettingsManager
from modules.session_manager import SessionManager
from modules.filter_manager import FilterManager
import config


class OverlayHighlight(QWidget):
    """Pulsing border overlay for a target widget."""
    def __init__(self, target_widget, parent=None):
        super().__init__(parent)
        self.target = target_widget
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()
        
        # animation state
        self.opacity = 0.0
        self.anim = QPropertyAnimation(self, b"opacity_prop")
        self.anim.setDuration(1000)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setKeyValueAt(0.5, 1.0)  # Peak at middle
        self.anim.setKeyValueAt(1.0, 0.0)
        self.anim.setLoopCount(2)  # Pulse twice by default
        
        # track target movement
        self.target.installEventFilter(self)
        self.update_position()
    
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj == self.target and (event.type() == QEvent.Type.Move or event.type() == QEvent.Type.Resize):
            self.update_position()
        return False
    
    def update_position(self):
        if not self.target.isVisible():
            self.hide()
            return
        if self.parent():
            target_pos = self.target.mapTo(self.parent(), self.target.rect().topLeft())
            target_size = self.target.size()
            self.setGeometry(
                target_pos.x() - 5,
                target_pos.y() - 5,
                target_size.width() + 10,
                target_size.height() + 10
            )
        else:
            geo = self.target.geometry()
            self.setGeometry(geo.adjusted(-5, -5, 5, 5))
    
    def get_opacity(self): 
        return self.opacity
    
    def set_opacity(self, val):
        self.opacity = val
        self.update()
    
    opacity_prop = pyqtProperty(float, get_opacity, set_opacity)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        color = QColor("#0078d4")
        color.setAlphaF(self.opacity)
        pen = QPen(color)
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawRoundedRect(rect, 5, 5)
    
    def start(self, loop_count=2):
        self.show()
        self.update_position()
        self.anim.setLoopCount(loop_count)
        self.anim.start()


class AudioWorker(QThread):
    """Audio capture + timeout detection in background thread."""
    timeout_signal = pyqtSignal()
    finished_signal = pyqtSignal()
    
    def __init__(self, audio_capture: AudioCapture, audio_queue: queue.Queue):
        super().__init__()
        self.audio_capture = audio_capture
        self.audio_queue = audio_queue
        self.is_running = False
    
    def run(self):
        if not self.audio_capture.start_capture(
            self.audio_capture.mic_device_index, 
            self.audio_capture.loopback_device_index
        ):
            print("AudioWorker: Failed to start audio capture.")
            self.finished_signal.emit()
            return
        
        self.is_running = True
        
        while self.is_running and self.audio_capture.is_capturing:
            if self.audio_capture.timeout_detected:
                self.timeout_signal.emit()
                self.audio_capture.reset_timeout_flag()
            
            mono_chunk = self.audio_capture.get_mono_mixed_frame_from_queue()
            
            if mono_chunk is not None:
                try:
                    self.audio_queue.put_nowait(mono_chunk)
                except queue.Full:
                    # queue full, drop oldest
                    try:
                        self.audio_queue.get_nowait()
                        self.audio_queue.put_nowait(mono_chunk)
                    except queue.Empty:
                        pass # Should not happen
            else:
                self.msleep(10)
        
        self.audio_capture.stop_capture()
        self.finished_signal.emit()
    
    def stop(self):
        self.is_running = False
        if self.audio_capture:
            self.audio_capture.stop_capture()


class StreamerWorker(QThread):
    """Streams audio to server in background."""
    
    def __init__(self, streamer: AudioStreamer, audio_queue: queue.Queue):
        super().__init__()
        self.streamer = streamer
        self.audio_queue = audio_queue
        self.is_running = False
    
    def run(self):
        if not self.streamer.loop or self.streamer.loop.is_closed():
            print("[StreamerWorker] WARNING: Streamer event loop not running, starting it...")
            self.streamer.start()
            self.msleep(500)
        
        self.msleep(200)
        self.streamer.start_audio_streaming(self.audio_queue)
        self.is_running = True
        while self.is_running:
            self.msleep(100)
    
    def stop(self):
        self.is_running = False
        if self.streamer:
            self.streamer.stop_audio_streaming()


class DialingWorker(QThread):
    """Teams dialing in background so UI stays responsive."""

    finished_signal = pyqtSignal(bool)

    def __init__(self, controller: TeamsController, phone_number: str):
        super().__init__()
        self.controller = controller
        self.phone_number = phone_number

    def run(self):
        success = self.controller.start_call(self.phone_number)
        self.finished_signal.emit(success)


class DebugQueueDialog(QDialog):
    """Shows queue sizes, clear buttons for when things get stuck."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setWindowTitle("Debug / Queue Status")
        self.setModal(False)
        self.main = main_window
        self.resize(420, 380)

        layout = QVBoxLayout(self)

        # LiveKit toggle
        livekit_row = QHBoxLayout()
        self.livekit_cb = QCheckBox("Use LiveKit (WebRTC) for audio")
        use_lk = main_window.settings_manager.settings.get("livekit", {}).get("use_livekit", getattr(config, "USE_LIVEKIT", False))
        self.livekit_cb.setChecked(use_lk)
        self.livekit_cb.stateChanged.connect(self._on_livekit_toggled)
        self.livekit_cb.setToolTip("Restart app for change to take effect")
        livekit_row.addWidget(self.livekit_cb)
        livekit_row.addStretch()
        layout.addLayout(livekit_row)
        livekit_note = QLabel("(Restart app for change to take effect)")
        livekit_note.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(livekit_note)

        info = QLabel(
            "Shows pending items in client queues. High counts may indicate clogging.\n"
            "Clear queues if audio/streaming appears stuck (e.g. after a failed call)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(info)

        self.labels = {}
        rows = (
            ("audio_queue", "Audio (capture → streamer):"),
            ("metadata_queue", "Metadata (status updates):"),
            ("message_queue", "Messages (from server):"),
            ("capture_queue", "Capture internal:"),
            ("streamer", "Streamer connected:"),
            ("workers", "Workers (audio/streamer):"),
        )
        form = QFormLayout()
        for key, label in rows:
            lbl = QLabel("-")
            lbl.setStyleSheet("font-family: monospace;")
            self.labels[key] = lbl
            form.addRow(label, lbl)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(refresh_btn)
        clear_audio_btn = QPushButton("Clear audio queue")
        clear_audio_btn.clicked.connect(self._clear_audio_queue)
        clear_audio_btn.setToolTip("Drain audio chunks waiting to be sent")
        btn_row.addWidget(clear_audio_btn)
        clear_meta_btn = QPushButton("Clear metadata queue")
        clear_meta_btn.clicked.connect(self._clear_metadata_queue)
        clear_meta_btn.setToolTip("Drain pending status updates")
        btn_row.addWidget(clear_meta_btn)
        clear_msg_btn = QPushButton("Clear message queue")
        clear_msg_btn.clicked.connect(self._clear_message_queue)
        clear_msg_btn.setToolTip("Drain pending server messages")
        btn_row.addWidget(clear_msg_btn)
        clear_all_btn = QPushButton("Clear all")
        clear_all_btn.clicked.connect(self._clear_all)
        clear_all_btn.setStyleSheet("QPushButton { background-color: #5a3; }")
        btn_row.addWidget(clear_all_btn)
        layout.addLayout(btn_row)

        auto_refresh_btn = QPushButton("Auto-refresh (1s)")
        self._auto_timer = None
        auto_refresh_btn.clicked.connect(self._toggle_auto_refresh)
        layout.addWidget(auto_refresh_btn)

        self._refresh()

    def _on_livekit_toggled(self, _state):
        checked = self.livekit_cb.isChecked()
        self.main.settings_manager.settings.setdefault("livekit", {})["use_livekit"] = checked
        self.main.settings_manager.save_settings()
        import config
        config.USE_LIVEKIT = checked
        self.main.statusBar().showMessage(
            f"LiveKit {'enabled' if checked else 'disabled'}. Restart app to apply.",
            4000
        )

    def _get_stats(self):
        m = self.main
        stats = {}
        try:
            stats["audio_queue"] = m.audio_queue.qsize()
        except Exception:
            stats["audio_queue"] = "?"
        try:
            stats["metadata_queue"] = m.streamer.metadata_queue.qsize()
        except Exception:
            stats["metadata_queue"] = "?"
        try:
            stats["message_queue"] = m.streamer.message_queue.qsize()
        except Exception:
            stats["message_queue"] = "?"
        try:
            stats["capture_queue"] = m.audio_capture.audio_queue.qsize()
        except Exception:
            stats["capture_queue"] = "?"
        try:
            stats["streamer"] = "Yes" if m.streamer.is_connected else "No"
        except Exception:
            stats["streamer"] = "?"
        aw = "running" if (getattr(m, "audio_worker", None) and getattr(m.audio_worker, "is_running", False)) else "idle"
        sw = "running" if (getattr(m, "streamer_worker", None) and getattr(m.streamer_worker, "is_running", False)) else "idle"
        stats["workers"] = f"audio={aw}, streamer={sw}"
        return stats

    def _refresh(self):
        for k, lbl in self.labels.items():
            s = self._get_stats()
            lbl.setText(str(s.get(k, "-")))

    def _clear_audio_queue(self):
        n = 0
        try:
            while True:
                self.main.audio_queue.get_nowait()
                n += 1
        except queue.Empty:
            pass
        self._refresh()
        self.main.statusBar().showMessage(f"Cleared {n} items from audio queue", 3000)

    def _clear_metadata_queue(self):
        n = 0
        try:
            while True:
                self.main.streamer.metadata_queue.get_nowait()
                n += 1
        except queue.Empty:
            pass
        self._refresh()
        self.main.statusBar().showMessage(f"Cleared {n} items from metadata queue", 3000)

    def _clear_message_queue(self):
        n = 0
        try:
            while True:
                self.main.streamer.message_queue.get_nowait()
                n += 1
        except queue.Empty:
            pass
        self._refresh()
        self.main.statusBar().showMessage(f"Cleared {n} items from message queue", 3000)

    def _clear_all(self):
        if QMessageBox.question(
            self, "Clear all queues",
            "Clear all client queues? Use if streaming/audio appears stuck.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        total = 0
        for q in (self.main.audio_queue, self.main.streamer.metadata_queue, self.main.streamer.message_queue):
            while True:
                try:
                    q.get_nowait()
                    total += 1
                except queue.Empty:
                    break
        self._refresh()
        self.main.statusBar().showMessage(f"Cleared {total} items from all queues", 3000)

    def _toggle_auto_refresh(self):
        if self._auto_timer:
            self._auto_timer.stop()
            self._auto_timer = None
            self.main.statusBar().showMessage("Auto-refresh stopped", 2000)
            return
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._refresh)
        self._auto_timer.start(1000)
        self.main.statusBar().showMessage("Auto-refreshing every 1s", 2000)


class SettingsDialog(QDialog):
    """Settings dialog with tabs."""
    def __init__(self, settings_manager, audio_capture, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(600, 400)
        self.settings_manager = settings_manager
        self.audio_capture = audio_capture

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Audio
        self.audio_tab = QWidget()
        self.setup_audio_tab()
        self.tabs.addTab(self.audio_tab, "Audio")

        # Dispositions
        self.dispo_tab = QWidget()
        self.setup_dispo_tab()
        self.tabs.addTab(self.dispo_tab, "Dispositions")

        # Display
        self.display_tab = QWidget()
        self.setup_display_tab()
        self.tabs.addTab(self.display_tab, "Display")

        # Calling
        self.calling_tab = QWidget()
        self.setup_calling_tab()
        self.tabs.addTab(self.calling_tab, "Calling")

        # Reminders
        self.reminders_tab = QWidget()
        self.setup_reminders_tab()
        self.tabs.addTab(self.reminders_tab, "Reminders")

        # Excel
        self.excel_tab = QWidget()
        self.setup_excel_tab()
        self.tabs.addTab(self.excel_tab, "Excel")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_all_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def setup_audio_tab(self):
        layout = QFormLayout(self.audio_tab)

        self.mic_combo = QComboBox()
        self.loopback_combo = QComboBox()

        # mic: input devices
        mic_devices = self.audio_capture.get_input_devices()
        if not mic_devices:
            mic_devices = self.audio_capture.get_all_recording_devices()
        for idx, info in mic_devices:
            name = info.get("name", f"Device {idx}")
            self.mic_combo.addItem(name, idx)

        # system audio: loopbacks + Stereo Mix
        system_devices = self.audio_capture.get_system_audio_devices()
        if not system_devices:
            system_devices = self.audio_capture.get_all_recording_devices()
        for idx, info in system_devices:
            name = info.get("name", f"Device {idx}")
            self.loopback_combo.addItem(name, idx)

        audio_cfg = self.settings_manager.get_audio_settings()
        remember = audio_cfg.get("remember_audio_devices", False)
        current_mic = audio_cfg.get("mic_index") if remember else None
        current_loop = audio_cfg.get("loopback_index") if remember else None

        self.set_combo_index(self.mic_combo, current_mic or self.audio_capture.mic_device_index)
        self.set_combo_index(self.loopback_combo, current_loop or self.audio_capture.loopback_device_index)

        layout.addRow("Microphone:", self.mic_combo)
        layout.addRow("System Audio (Stereo Mix):", self.loopback_combo)

        self.remember_audio_checkbox = QCheckBox("Remember preferred audio devices")
        self.remember_audio_checkbox.setChecked(remember)
        self.remember_audio_checkbox.setToolTip("When checked, your selections will be used on next launch. When unchecked, system defaults are used each time.")
        layout.addRow("", self.remember_audio_checkbox)
        
        volume_container = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMinimum(10)  # 1.0x
        self.volume_slider.setMaximum(100)  # 10.0x
        self.volume_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.volume_slider.setTickInterval(10)
        
        current_volume = audio_cfg.get("system_audio_volume", 6.0)
        slider_value = int(current_volume * 10)
        self.volume_slider.setValue(slider_value)
        
        self.volume_label = QLabel(f"System Volume: {current_volume:.1f}x")
        
        self.volume_slider.valueChanged.connect(lambda v: self.volume_label.setText(f"System Volume: {v/10.0:.1f}x"))
        
        volume_container.addWidget(self.volume_slider)
        volume_container.addWidget(self.volume_label)
        volume_container_widget = QWidget()
        volume_container_widget.setLayout(volume_container)
        layout.addRow("System Audio Volume:", volume_container_widget)

    def setup_dispo_tab(self):
        """Disposition color pickers."""
        layout = QVBoxLayout(self.dispo_tab)

        layout.addWidget(QLabel("Click a button to change its color:"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")

        container = QWidget()
        self.dispo_form = QFormLayout(container)

        self.current_colors = self.settings_manager.get_colors().copy()
        self.color_buttons = {}  # To keep references to buttons

        for dispo_name, hex_color in self.current_colors.items():
            btn = QPushButton()
            btn.setFixedSize(50, 25)
            self.update_color_button_style(btn, hex_color)

            # default arg captures loop variable
            btn.clicked.connect(lambda checked, n=dispo_name, b=btn: self.pick_color(n, b))

            self.color_buttons[dispo_name] = btn
            self.dispo_form.addRow(dispo_name, btn)

        scroll.setWidget(container)
        layout.addWidget(scroll)

    def update_color_button_style(self, btn, hex_color):
        """Set button background color."""
        btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #555; border-radius: 4px;")

    def pick_color(self, dispo_name, btn):
        """Open color picker, update."""
        current_hex = self.current_colors.get(dispo_name, "#FFFFFF")
        color = QColorDialog.getColor(QColor(current_hex), self, f"Choose color for {dispo_name}")

        if color.isValid():
            new_hex = color.name().upper()  # Returns #RRGGBB
            self.current_colors[dispo_name] = new_hex
            self.update_color_button_style(btn, new_hex)

    def setup_display_tab(self):
        """Which fields to show for contacts."""
        layout = QVBoxLayout(self.display_tab)

        layout.addWidget(QLabel("Drag items to reorder. Uncheck to hide."))

        self.display_list = QListWidget()
        self.display_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

        saved_order = self.settings_manager.get_display_order()
        hidden_fields = self.settings_manager.get_hidden_fields()

        all_known_fields = [
            "Email", "Title", "Company", "Website",
            "Industry", "Revenue", "State", "Timezone", "LinkedIn"
        ]

        final_order = []
        seen = set()

        for field in saved_order:
            if field in all_known_fields:
                final_order.append(field)
                seen.add(field)

        for field in all_known_fields:
            if field not in seen:
                final_order.append(field)

        for field in final_order:
            item = QListWidgetItem(field)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

            if field in hidden_fields:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)

            self.display_list.addItem(item)

        layout.addWidget(self.display_list)

    def setup_calling_tab(self):
        """Phone priority + auto-call options."""
        layout = QVBoxLayout(self.calling_tab)
        
        auto_call_group = QFrame()
        auto_call_group.setFrameStyle(QFrame.Shape.StyledPanel)
        auto_call_layout = QVBoxLayout(auto_call_group)
        auto_call_layout.addWidget(QLabel("<b>Auto-Call Features</b>"))
        
        calling_settings = self.settings_manager.get_calling_settings()
        self.auto_call_checkbox = QCheckBox("Enable Call Monitoring & Auto-Advance")
        self.auto_call_checkbox.setToolTip(
            "When enabled: Server monitors for human/voicemail and sends hangup signal; "
            "client hangs up when server says to, logs disposition, and advances.\n"
            "When disabled: Manual mode—no server monitoring, no automatic hangup or advancement."
        )
        self.auto_call_checkbox.setChecked(calling_settings.get("auto_call_enabled", True))
        auto_call_layout.addWidget(self.auto_call_checkbox)

        self.auto_call_on_disposition_checkbox = QCheckBox("Auto-call next number after No Response")
        self.auto_call_on_disposition_checkbox.setToolTip(
            "When enabled: after clicking No Response, automatically dials the next number—"
            "either the next phone for the same contact, or the first number of the next contact (new row)."
        )
        self.auto_call_on_disposition_checkbox.setChecked(calling_settings.get("auto_call_on_disposition", False))
        auto_call_layout.addWidget(self.auto_call_on_disposition_checkbox)
        
        layout.addWidget(auto_call_group)
        layout.addSpacing(20)
        
        layout.addWidget(QLabel("<b>Phone Number Priority</b>"))
        layout.addWidget(QLabel("Drag to prioritize. Uncheck to never call."))

        self.priority_list = QListWidget()
        self.priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

        priority_settings = self.settings_manager.get_phone_priority()
        saved_order = priority_settings.get("order", ["Mobile", "Direct", "Company", "Other"])
        disabled_types = priority_settings.get("disabled", [])

        all_types = ["Mobile", "Direct", "Company", "Other"]
        final_order = []
        seen = set()

        for t in saved_order:
            if t in all_types:
                final_order.append(t)
                seen.add(t)
        for t in all_types:
            if t not in seen:
                final_order.append(t)

        for p_type in final_order:
            item = QListWidgetItem(p_type)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)

            if p_type in disabled_types:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)

            self.priority_list.addItem(item)

        layout.addWidget(self.priority_list)

    def setup_reminders_tab(self):
        """Outlook reminder settings."""
        layout = QFormLayout(self.reminders_tab)
        
        reminder_cfg = self.settings_manager.get_reminder_settings()
        
        self.reminders_enabled = QCheckBox("Enable Outlook Calendar Invites")
        self.reminders_enabled.setChecked(reminder_cfg.get("enabled", False))
        
        self.agent_email_input = QLineEdit()
        self.agent_email_input.setPlaceholderText("your.name@palmiq.com")
        self.agent_email_input.setText(reminder_cfg.get("agent_email", ""))
        
        layout.addRow(self.reminders_enabled)
        layout.addRow("Your Email:", self.agent_email_input)
        
        info_label = QLabel("Invites will be sent from the system to this email.\nMake sure it is correct.")
        info_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addRow(info_label)

    def setup_excel_tab(self):
        """Excel autosave interval."""
        layout = QFormLayout(self.excel_tab)
        excel_cfg = self.settings_manager.get_excel_settings()
        self.autosave_combo = QComboBox()
        for opt in [(0, "Disabled"), (5, "Every 5 minutes"), (10, "Every 10 minutes"), (15, "Every 15 minutes")]:
            self.autosave_combo.addItem(opt[1], opt[0])
        interval = excel_cfg.get("autosave_interval_minutes", 10)
        idx = self.autosave_combo.findData(interval)
        self.autosave_combo.setCurrentIndex(idx if idx >= 0 else 1)  # Default to 10 min
        layout.addRow("Autosave workbook:", self.autosave_combo)
        layout.addRow("", QLabel("Saves the active Excel file periodically to prevent data loss."))

    def set_combo_index(self, combo, item_data):
        for i in range(combo.count()):
            if combo.itemData(i) == item_data:
                combo.setCurrentIndex(i)
                break

    def save_all_settings(self):
        mic_idx = self.mic_combo.currentData()
        loop_idx = self.loopback_combo.currentData()
        volume_value = self.volume_slider.value() / 10.0
        remember = self.remember_audio_checkbox.isChecked()

        self.settings_manager.set_audio_settings(mic_idx, loop_idx, volume_value, remember=remember)

        self.audio_capture.mic_device_index = mic_idx
        self.audio_capture.loopback_device_index = loop_idx
        self.audio_capture.system_audio_multiplier = volume_value

        self.settings_manager.set_colors(self.current_colors)

        if self.parent():
            self.parent().disposition_saver.update_colors(self.current_colors)

        new_order = []
        new_hidden = []

        for i in range(self.display_list.count()):
            item = self.display_list.item(i)
            field_name = item.text()
            new_order.append(field_name)

            if item.checkState() == Qt.CheckState.Unchecked:
                new_hidden.append(field_name)

        self.settings_manager.set_display_preferences(new_order, new_hidden)

        new_order_phones = []
        new_disabled_phones = []

        for i in range(self.priority_list.count()):
            item = self.priority_list.item(i)
            p_type = item.text()
            new_order_phones.append(p_type)

            if item.checkState() == Qt.CheckState.Unchecked:
                new_disabled_phones.append(p_type)

        self.settings_manager.set_phone_priority(new_order_phones, new_disabled_phones)
        
        auto_call_enabled = self.auto_call_checkbox.isChecked()
        auto_call_on_disposition = self.auto_call_on_disposition_checkbox.isChecked()
        self.settings_manager.set_calling_settings(auto_call_enabled, auto_call_on_disposition)

        r_enabled = self.reminders_enabled.isChecked()
        r_email = self.agent_email_input.text()
        self.settings_manager.set_reminder_settings(r_enabled, r_email)

        autosave_interval = self.autosave_combo.currentData()
        self.settings_manager.set_excel_settings(autosave_interval)

        if self.parent():
            self.parent().rebuild_details_panel()
            if hasattr(self.parent(), '_start_autosave_timer'):
                self.parent()._start_autosave_timer()
            if self.parent().current_contact:
                sorted_numbers = self.parent().sort_phones(self.parent().current_contact)
                self.parent().current_contact['Phones'] = sorted_numbers
                self.parent().update_phone_display()

        self.accept()


class CallbackDateTimeDialog(QDialog):
    """Schedule a callback."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Schedule Callback")
        self.setModal(True)
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Scheduled Date & Time:"))
        self.datetime_edit = QDateTimeEdit(self)
        self.datetime_edit.setDateTime(QDateTime.currentDateTime().addDays(1))  # Default to tomorrow
        self.datetime_edit.setCalendarPopup(True)
        self.datetime_edit.setDisplayFormat("MM/dd/yy hh:mm AP")
        layout.addWidget(self.datetime_edit)
        
        layout.addWidget(QLabel("Notes:"))
        self.notes_edit = QTextEdit(self)
        self.notes_edit.setMaximumHeight(100)
        layout.addWidget(self.notes_edit)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_datetime_iso(self) -> str:
        """Selected datetime as ISO string."""
        return self.datetime_edit.dateTime().toPyDateTime().isoformat()
    
    def get_notes(self) -> str:
        """Notes text."""
        return self.notes_edit.toPlainText()


class FollowUpDialog(QDialog):
    """Prompt to schedule follow-up."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Follow-up")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Do you want to schedule a follow-up call for this contact?"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        buttons.accepted.connect(self.accept)  # 'Yes' will be accepted
        buttons.rejected.connect(self.reject)  # 'No' will be rejected

        layout.addWidget(buttons)


class ResumeSessionDialog(QDialog):
    def __init__(self, session_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resume Session")
        self.setModal(True)

        layout = QVBoxLayout(self)

        path = session_data.get("workbook_path", "Unknown File")
        filename = os.path.basename(path)
        index = session_data.get("current_index", 0)
        date_str = session_data.get("timestamp", "")[:10]  # Just the date

        msg = (
            f"Welcome back!\n\n"
            f"Last session: {date_str}\n"
            f"File: {filename}\n"
            f"Stopped at Contact #{index + 1}\n\n"
            f"Do you want to resume?"
        )

        layout.addWidget(QLabel(msg))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class FiltersDialog(QDialog):
    """Filters & Sort: select multiple in order (first = top). Order shown as [1], [2] in text. Opens clean each time."""

    DISPOSITION_OPTIONS = [
        "No Response", "Call Back Later", "Sent Info", "Booked Meeting",
        "Not Interested", "Wrong Number", "Voicemail (Verified)", "Invalid Contact",
    ]

    def __init__(self, contact_list: List[Dict[str, Any]], filter_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filters & Sort")
        self.setModal(True)
        self.resize(500, 400)
        self.contact_list = contact_list
        self.manager = filter_manager
        self.tab_ordered_selection: Dict[str, List[str]] = {}

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.categories = ["Disposition", "Title", "Industry", "Revenue"]
        self.list_widgets: Dict[str, QListWidget] = {}
        self.sort_az_checkboxes: Dict[str, QCheckBox] = {}
        self.clear_buttons: Dict[str, QPushButton] = {}

        for cat in self.categories:
            self.tab_ordered_selection[cat] = []

            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.addWidget(QLabel(f"Click values in order; first = top. Number in [ ] = priority (sort by {cat}):"))

            opt_row = QHBoxLayout()
            sort_az_cb = QCheckBox("Sort A to Z")
            sort_az_cb.setChecked(bool(self.manager.sort_az.get(cat, False)))
            sort_az_cb.stateChanged.connect(lambda state, c=cat: self._on_sort_az_changed(c, state))
            self.sort_az_checkboxes[cat] = sort_az_cb
            opt_row.addWidget(sort_az_cb)
            clear_btn = QPushButton("Clear selection")
            clear_btn.clicked.connect(lambda checked=False, c=cat: self._clear_tab_selection(c))
            self.clear_buttons[cat] = clear_btn
            opt_row.addStretch()
            tab_layout.addLayout(opt_row)

            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.list_widgets[cat] = list_widget

            if cat == "Disposition":
                from_data = {c.get("current_disposition") for c in self.contact_list if c.get("current_disposition")}
                unique_values = sorted(set(self.DISPOSITION_OPTIONS) | from_data)
            else:
                unique_values = sorted({str(c.get(cat, "-") or "-") for c in self.contact_list})

            for val in unique_values:
                item = QListWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, str(val))
                list_widget.addItem(item)

            list_widget.itemClicked.connect(lambda it: self._on_filter_item_clicked(it))

            tab_layout.addWidget(list_widget)
            self.tabs.addTab(tab, cat)

            if sort_az_cb.isChecked():
                list_widget.setEnabled(False)
                clear_btn.setEnabled(False)

        layout.addWidget(QLabel("Only the current tab's selection is applied. Order = priority (1 at top)."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_filters)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_tab_text(self, cat: str):
        list_widget = self.list_widgets[cat]
        ordered = self.tab_ordered_selection.get(cat, [])
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            val = item.data(Qt.ItemDataRole.UserRole) or ""
            if val in ordered:
                rank = ordered.index(val) + 1
                item.setText(f"{val}  [{rank}]")
            else:
                item.setText(val)

    def _on_filter_item_clicked(self, item: QListWidgetItem):
        list_widget = item.listWidget()
        cat = next((c for c in self.categories if self.list_widgets[c] is list_widget), None)
        if not cat:
            return
        val = item.data(Qt.ItemDataRole.UserRole) or ""
        ordered = self.tab_ordered_selection[cat]
        if val in ordered:
            ordered.remove(val)
        else:
            ordered.append(val)
        self._refresh_tab_text(cat)

    def _on_sort_az_changed(self, cat: str, state):
        checked = state == Qt.CheckState.Checked.value or state == 2
        list_widget = self.list_widgets[cat]
        clear_btn = self.clear_buttons[cat]
        list_widget.setEnabled(not checked)
        clear_btn.setEnabled(not checked)
        if checked:
            self.tab_ordered_selection[cat] = []
            self._refresh_tab_text(cat)

    def _clear_tab_selection(self, cat: str):
        if self.sort_az_checkboxes[cat].isChecked():
            return
        self.tab_ordered_selection[cat] = []
        self._refresh_tab_text(cat)

    def save_filters(self):
        parent = self.parent()
        tab_index = self.tabs.currentIndex()
        if tab_index < len(self.categories):
            cat = self.categories[tab_index]
            sort_az = self.sort_az_checkboxes[cat].isChecked()
            self.manager.set_sort_az(cat, sort_az)
            if sort_az:
                self.manager.active_sorts[cat] = []
                if parent is not None and hasattr(parent, "_apply_excel_sort_by_selection"):
                    parent._apply_excel_sort_by_selection({}, sort_az_category=cat)
                    if hasattr(parent, "refresh_from_excel"):
                        parent.refresh_from_excel()
                    if hasattr(parent, "apply_filters"):
                        parent.apply_filters()
            else:
                ordered = self.tab_ordered_selection.get(cat, [])
                self.manager.active_sorts = {cat: list(ordered)} if ordered else {}
                if parent is not None and hasattr(parent, "_apply_excel_sort_by_selection"):
                    parent._apply_excel_sort_by_selection(self.manager.active_sorts)
                    if hasattr(parent, "refresh_from_excel"):
                        parent.refresh_from_excel()
                    if hasattr(parent, "apply_filters"):
                        parent.apply_filters()
        self.accept()


ANNOUNCEMENT_IMAGE_BOX_SIZE = 64


class AnnouncementPopupDialog(QDialog):
    """Server announcement popup."""

    def __init__(self, message: str, image_filename: Optional[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Announcement")
        self.setModal(False)
        self.setMinimumWidth(350)
        self.setMaximumWidth(450)
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        if image_filename:
            img_url = f"{config.SERVER_API_URL.rstrip('/')}/static/announcements/{image_filename}"
            if image_filename.lower().endswith(".gif"):
                movie = self._load_gif(img_url, image_filename)
                if movie:
                    img_label = QLabel()
                    img_label.setFixedSize(ANNOUNCEMENT_IMAGE_BOX_SIZE, ANNOUNCEMENT_IMAGE_BOX_SIZE)
                    img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    img_label.setStyleSheet("background-color: #2d2d2d; border-radius: 4px;")
                    movie.setScaledSize(QSize(ANNOUNCEMENT_IMAGE_BOX_SIZE, ANNOUNCEMENT_IMAGE_BOX_SIZE))
                    movie.start()
                    img_label.setMovie(movie)
                    self._movie = movie
                    content_row.addWidget(img_label)
                else:
                    pixmap = self._load_image(img_url, image_filename)
                    if pixmap:
                        img_label = QLabel()
                        img_label.setFixedSize(ANNOUNCEMENT_IMAGE_BOX_SIZE, ANNOUNCEMENT_IMAGE_BOX_SIZE)
                        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        img_label.setStyleSheet("background-color: #2d2d2d; border-radius: 4px;")
                        scaled = pixmap.scaled(
                            ANNOUNCEMENT_IMAGE_BOX_SIZE,
                            ANNOUNCEMENT_IMAGE_BOX_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        img_label.setPixmap(scaled)
                        content_row.addWidget(img_label)
            else:
                pixmap = self._load_image(img_url, image_filename)
                if pixmap:
                    img_label = QLabel()
                    img_label.setFixedSize(ANNOUNCEMENT_IMAGE_BOX_SIZE, ANNOUNCEMENT_IMAGE_BOX_SIZE)
                    img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    img_label.setStyleSheet("background-color: #2d2d2d; border-radius: 4px;")
                    scaled = pixmap.scaled(
                        ANNOUNCEMENT_IMAGE_BOX_SIZE,
                        ANNOUNCEMENT_IMAGE_BOX_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    img_label.setPixmap(scaled)
                    content_row.addWidget(img_label)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 12px;")
        content_row.addWidget(msg_label, 1)

        main_layout.addLayout(content_row)

        btn = QPushButton("OK")
        btn.clicked.connect(self.accept)
        main_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)

    def _load_image(self, url: str, filename: str) -> Optional[QPixmap]:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                return pixmap
        except Exception:
            pass
        return None

    def _load_gif(self, url: str, filename: str) -> Optional[QMovie]:
        if not filename.lower().endswith(".gif"):
            return None
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            movie = QMovie()
            movie.setFormat(b"GIF")
            if movie.loadFromData(data):
                return movie
        except Exception:
            pass
        return None


class CallbackPromptDialog(QDialog):
    """Callback due - call or dismiss."""
    
    def __init__(self, callback_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.callback_data = callback_data
        self.setWindowTitle("Callback Reminder")
        self.setModal(True)
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        name = callback_data.get("contact_name", "Unknown")
        phone = callback_data.get("contact_phone", "")
        notes = callback_data.get("notes", "")
        scheduled = callback_data.get("scheduled_time", "")
        
        layout.addWidget(QLabel(f"<b>Contact:</b> {name}"))
        layout.addWidget(QLabel(f"<b>Phone:</b> {phone}"))
        if notes:
            layout.addWidget(QLabel(f"<b>Notes:</b> {notes}"))
        if scheduled:
            layout.addWidget(QLabel(f"<b>Scheduled:</b> {scheduled}"))
        
        layout.addSpacing(20)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.call_now_btn = QPushButton("Call Now")
        self.call_now_btn.setStyleSheet("background-color: #28a745; color: white; padding: 10px;")
        button_layout.addWidget(self.call_now_btn)
        
        self.remind_later_btn = QPushButton("Remind Me Later")
        self.remind_later_btn.setStyleSheet("background-color: #ffc107; color: black; padding: 10px;")
        button_layout.addWidget(self.remind_later_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("background-color: #dc3545; color: white; padding: 10px;")
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Connect buttons
        self.call_now_btn.clicked.connect(lambda: self.done(1))  # 1 = Call Now
        self.remind_later_btn.clicked.connect(lambda: self.done(2))  # 2 = Remind Later
        self.cancel_btn.clicked.connect(lambda: self.done(3))  # 3 = Cancel


class SelectDispositionDialog(QDialog):
    """Modal to pick a disposition."""
    def __init__(self, dispositions: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Disposition")
        self.setModal(True)
        self.selected_disposition = None
        
        layout = QGridLayout(self)
        row, col = 0, 0
        for disp in dispositions:
            btn = QPushButton(disp)
            btn.clicked.connect(lambda checked, d=disp: self.on_select(d))
            layout.addWidget(btn, row, col)
            col += 1
            if col > 1:
                col = 0
                row += 1
    
    def on_select(self, disposition):
        self.selected_disposition = disposition
        self.accept()


class OverdueCallbacksDialog(QDialog):
    """
    A modal dialog to display and manage overdue callbacks.
    """
    call_now_requested = pyqtSignal(dict)
    log_manual_requested = pyqtSignal(dict)
    dismiss_all_requested = pyqtSignal(list)  # All callbacks to dismiss as No Response
    
    def __init__(self, callbacks: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Overdue Callbacks")
        self.setModal(True)
        self.setMinimumSize(600, 400)
        self.callbacks = callbacks
        self.selected_callback = None
        
        main_layout = QHBoxLayout(self)
        
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("<b>Overdue Items:</b>"))
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        left_panel.addWidget(self.list_widget)
        
        right_panel = QVBoxLayout()
        details_group = QFrame()
        details_group.setFrameShape(QFrame.Shape.StyledPanel)
        self.details_layout = QFormLayout(details_group)
        
        self.name_label = QLabel("-")
        self.phone_label = QLabel("-")
        self.notes_label = QLabel("-")
        self.notes_label.setWordWrap(True)
        self.scheduled_label = QLabel("-")
        self.title_detail_label = QLabel("-")
        self.company_detail_label = QLabel("-")
        self.email_detail_label = QLabel("-")
        self.website_detail_label = QLabel("-")
        self.industry_detail_label = QLabel("-")
        self.revenue_detail_label = QLabel("-")
        self.state_detail_label = QLabel("-")
        
        self.details_layout.addRow("<b>Name:</b>", self.name_label)
        self.details_layout.addRow("<b>Phone:</b>", self.phone_label)
        self.details_layout.addRow("<b>Scheduled:</b>", self.scheduled_label)
        self.details_layout.addRow("<b>Title:</b>", self.title_detail_label)
        self.details_layout.addRow("<b>Company:</b>", self.company_detail_label)
        self.details_layout.addRow("<b>Email:</b>", self.email_detail_label)
        self.details_layout.addRow("<b>Website:</b>", self.website_detail_label)
        self.details_layout.addRow("<b>Industry:</b>", self.industry_detail_label)
        self.details_layout.addRow("<b>Revenue:</b>", self.revenue_detail_label)
        self.details_layout.addRow("<b>State:</b>", self.state_detail_label)
        self.details_layout.addRow("<b>Notes:</b>", self.notes_label)
        right_panel.addWidget(details_group)
        
        button_layout = QGridLayout()
        self.call_now_btn = QPushButton("Call Now")
        self.log_manual_btn = QPushButton("Log Manual Call")
        self.dismiss_all_btn = QPushButton("Dismiss All")
        self.dismiss_all_btn.setStyleSheet("background-color: #6c757d; color: white;")
        self.dismiss_all_btn.setToolTip("Dismiss all overdue callbacks as No Response; they will not show again.")
        
        button_layout.addWidget(self.call_now_btn, 0, 0)
        button_layout.addWidget(self.log_manual_btn, 0, 1)
        button_layout.addWidget(self.dismiss_all_btn, 1, 0, 1, 2)
        right_panel.addLayout(button_layout)
        
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 1)
        
        self.call_now_btn.clicked.connect(self.on_call_now)
        self.log_manual_btn.clicked.connect(self.on_log_manual)
        self.dismiss_all_btn.clicked.connect(self.on_dismiss_all)
        
        self.populate_list()
        self.update_ui_state()
    
    def populate_list(self):
        """Fill callback list."""
        import json
        self.list_widget.clear()
        for cb in self.callbacks:
            contact_data_str = cb.get("contact_data", "{}")
            try:
                contact_data = json.loads(contact_data_str)
                first_name = contact_data.get('First Name', '')
                last_name = contact_data.get('Last Name', '')
                name = f"{first_name} {last_name}".strip() or "Unknown"
            except:
                name = "Unknown"
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, cb)
            self.list_widget.addItem(item)
    
    def on_selection_changed(self, current_item, previous_item):
        """Update details when selection changes."""
        import json
        
        if not current_item:
            self.selected_callback = None
            self.name_label.setText("-")
            self.phone_label.setText("-")
            self.scheduled_label.setText("-")
            self.notes_label.setText("-")
            self.title_detail_label.setText("-")
            self.company_detail_label.setText("-")
            self.email_detail_label.setText("-")
            self.website_detail_label.setText("-")
            self.industry_detail_label.setText("-")
            self.revenue_detail_label.setText("-")
            self.state_detail_label.setText("-")
        else:
            self.selected_callback = current_item.data(Qt.ItemDataRole.UserRole)
            
            contact_data_str = self.selected_callback.get("contact_data", "{}")
            try:
                contact_data = json.loads(contact_data_str)
            except:
                contact_data = {}
            
            self.scheduled_label.setText(self.selected_callback.get("scheduled_time", "-"))
            self.notes_label.setText(self.selected_callback.get("notes", "-"))
            
            first_name = contact_data.get('First Name', '')
            last_name = contact_data.get('Last Name', '')
            self.name_label.setText(f"{first_name} {last_name}".strip() or "-")
            
            phones = contact_data.get('Phones', [])
            self.phone_label.setText(phones[0] if phones else "-")
            
            self.title_detail_label.setText(contact_data.get('Title', '-'))
            self.company_detail_label.setText(contact_data.get('Company', '-'))
            
            emails = contact_data.get('Emails', [])
            self.email_detail_label.setText(emails[0] if emails else "-")
            
            self.website_detail_label.setText(contact_data.get('Website', '-'))
            self.industry_detail_label.setText(contact_data.get('Industry', '-'))
            self.revenue_detail_label.setText(contact_data.get('Revenue', '-'))
            self.state_detail_label.setText(contact_data.get('State', '-'))
        
        self.update_ui_state()
    
    def update_ui_state(self):
        """Update button enabled state."""
        is_item_selected = self.selected_callback is not None
        self.call_now_btn.setEnabled(is_item_selected)
        self.log_manual_btn.setEnabled(is_item_selected)
        self.dismiss_all_btn.setEnabled(len(self.callbacks) > 0)
    
    def _remove_selected_item(self):
        """Remove selected callback."""
        current_row = self.list_widget.currentRow()
        if current_row >= 0:
            self.list_widget.takeItem(current_row)
    
    def on_call_now(self):
        self.call_now_requested.emit(self.selected_callback)
        self._remove_selected_item()
    
    def on_log_manual(self):
        self.log_manual_requested.emit(self.selected_callback)
        self._remove_selected_item()
    
    def on_dismiss_all(self):
        """Dismiss all callbacks as No Response; they will not show again on next open."""
        self.dismiss_all_requested.emit(self.callbacks)
        self.accept()


class TeamsPowerDialerApp(QMainWindow):
    """Main app window."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Teams Power Dialer")
        self.setGeometry(100, 100, 380, 850)
        
        self.excel_reader = ActiveExcelReader()
        self.teams_controller = TeamsController()
        self.audio_capture = AudioCapture()
        self.settings_manager = SettingsManager()
        self.session_manager = SessionManager()
        self.filter_manager = FilterManager()
        
        import getpass
        try:
            agent_id = getpass.getuser()
        except Exception:
            agent_id = "unknown_agent"
        
        use_livekit = self.settings_manager.settings.get("livekit", {}).get("use_livekit", config.USE_LIVEKIT)
        if use_livekit:
            from modules.livekit_streamer import LiveKitStreamer
            self.streamer = LiveKitStreamer(
                config.LIVEKIT_URL, agent_id, config.SERVER_API_URL
            )
        else:
            self.streamer = AudioStreamer(config.SERVER_URL, agent_id=agent_id)
        
        current_colors = self.settings_manager.get_colors()
        self.disposition_saver = DispositionSaver(
            config.ONEDRIVE_BASE_PATH,
            colors=current_colors
        )
        
        time_cfg = self.settings_manager.get_time_filter_settings()
        self.filter_manager.set_time_filter(
            time_cfg.get("enabled", False),
            time_cfg.get("start_hour", 9),
            time_cfg.get("end_hour", 17),
            time_cfg.get("my_timezone", "ET"),
        )
        
        self.contact_list: List[Dict[str, Any]] = []
        self.current_index: int = 0
        self.current_phone_idx: int = 0
        self.current_contact: Optional[Dict[str, Any]] = None
        self.valid_indices: List[int] = []  # All indices in display order (sorting done via Excel)
        self._last_sheet_name: Optional[str] = None  # Track last active sheet for Excel sync
        
        self.calls_today: int = 0
        
        self.audio_worker: Optional[AudioWorker] = None
        self.streamer_worker: Optional[StreamerWorker] = None
        self.audio_queue = queue.Queue(maxsize=100)
        self.audio_lock = threading.Lock()  # Thread lock for audio thread operations
        
        self.is_connected = False
        
        self.call_status = "Ready"  # Ready, Dialing, Calling, Connected, Call Ended
        self.dot_animation_timer = QTimer()
        self.dot_animation_count = 0
        self.call_timer = QTimer()  # For tracking call duration (fallback)
        self.call_start_time = None
        self.is_timer_synced = False  # Flag to track if we've synced with Teams timer
        
        self.saved_state = None  # Save state when "Call Now" from callback
        
        self.has_no_response_history = False  # Track if contact had "No Response" during session
        
        self.history_stack = []  # Stack to track disposition actions for undo
        
        self._call_record_created = False
        
        self.message_timer = QTimer()
        self.message_timer.timeout.connect(self.check_incoming_messages)
        self.message_timer.start(100)  # Check every 100ms
        
        self.filter_timer = QTimer()
        self.filter_timer.timeout.connect(self.apply_filters)
        self.filter_timer.start(15 * 60 * 1000)  # Re-evaluate every 15 minutes

        self.autosave_timer = QTimer()
        self.autosave_timer.timeout.connect(self._on_autosave_tick)
        
        self.idle_timer = QTimer()
        self.idle_timer.timeout.connect(self.trigger_reminder_pulse)
        self.idle_timer.setInterval(60000)  # 1 minute default
        self.idle_timer.setSingleShot(True)  # Only fire once per start
        
        self.current_ui_state = 'OFF'
        
        self.setup_ui()
        self.setup_connections()
    
        self.streamer.start()
        
        QTimer.singleShot(2000, self.request_overdue_callbacks)

        audio_cfg = self.settings_manager.get_audio_settings()
        if audio_cfg.get("remember_audio_devices") and audio_cfg.get("mic_index") is not None and audio_cfg.get("loopback_index") is not None:
            self.audio_capture.mic_device_index = audio_cfg["mic_index"]
            self.audio_capture.loopback_device_index = audio_cfg["loopback_index"]
        else:
            self.audio_capture.mic_device_index = None
            self.audio_capture.loopback_device_index = None
        self.audio_capture.auto_select_devices()
        
        self.audio_capture.system_audio_multiplier = audio_cfg.get("system_audio_volume", 6.0)
        
        self.statusBar().showMessage("Ready. Default audio devices selected.")
        
        QTimer.singleShot(100, lambda: self._update_nav_buttons() if hasattr(self, 'go_back_btn') else None)

        self.initialize_calls_counter()
        
        QTimer.singleShot(2500, self.check_for_resume)
        
        QTimer.singleShot(5000, self.refresh_calls_counter_from_server)
    
    def setup_ui(self):
        """Build the UI layout."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        
        central_widget = QWidget()
        self.setCentralWidget(scroll_area)
        scroll_area.setWidget(central_widget)
        
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.main_layout.setSpacing(8)
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.import_btn = QPushButton("Import from Active Excel")
        self.import_btn.setFixedHeight(30)  # Slim height (max 30px)
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #106ebe;
            }
        """)
        top_row.addWidget(self.import_btn)

        # Refresh button deactivated - use Import from Active Excel instead
        # (refresh_from_excel kept in code for internal use, e.g. after Excel sort)

        self.config_btn = QPushButton("⚙️")  # Use emoji or icon
        self.config_btn.setFixedSize(30, 30)
        self.config_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #aaaaaa;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        top_row.addWidget(self.config_btn)

        self.debug_btn = QPushButton("🐛")
        self.debug_btn.setFixedSize(30, 30)
        self.debug_btn.setToolTip("Debug / Queue Status")
        self.debug_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #aaaaaa;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        top_row.addWidget(self.debug_btn)

        self.filters_btn = QPushButton("Filters")
        self.filters_btn.setFixedHeight(30)
        self.filters_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 10px;
                padding-left: 8px;
                padding-right: 8px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
        """)
        top_row.addWidget(self.filters_btn)

        self.main_layout.addLayout(top_row)
        
        nav_row = QHBoxLayout()
        nav_row.setSpacing(5)

        self.search_jump_input = QLineEdit()
        self.search_jump_input.setPlaceholderText("Row # or search name/company...")
        self.search_jump_input.setToolTip("Enter row number (e.g. 42) or search text (name, company, phone). Press Enter or click Search.")
        self.search_jump_input.returnPressed.connect(self.perform_unified_search_or_jump)

        search_btn = QPushButton("Search")
        search_btn.setFixedWidth(60)
        search_btn.setStyleSheet("background-color: #3d3d3d; color: white; border: none; border-radius: 3px; padding: 4px;")
        search_btn.clicked.connect(self.perform_unified_search_or_jump)

        nav_row.addWidget(self.search_jump_input)
        nav_row.addWidget(search_btn)
        nav_row.addStretch()  # Push everything to the left

        self.main_layout.addLayout(nav_row)
        
        self.contact_container = QFrame()
        contact_container_layout = QVBoxLayout(self.contact_container)
        contact_container_layout.setContentsMargins(0, 0, 0, 0)
        contact_container_layout.setSpacing(8)
        
        self.name_label = QLabel("No Contact Loaded")
        self.name_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.name_label.setStyleSheet("color: #ffffff;")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.name_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.name_label)
        contact_container_layout.addWidget(self.name_label)
        
        self.phone_container = QFrame()
        phone_container_layout = QHBoxLayout(self.phone_container)
        phone_container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        phone_container_layout.setSpacing(5)
        phone_container_layout.setContentsMargins(0, 0, 0, 0)
        
        self.phone_label = QLabel("No phone number")
        self.phone_label.setFont(QFont("Segoe UI", 14))
        self.phone_label.setStyleSheet("color: #ffffff;")
        self.phone_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.phone_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.phone_label)
        
        self.phone_counter_label = QLabel("")
        self.phone_counter_label.setFont(QFont("Segoe UI", 10))
        self.phone_counter_label.setStyleSheet("color: #888888;")
        
        phone_container_layout.addWidget(self.phone_label)
        phone_container_layout.addWidget(self.phone_counter_label)
        contact_container_layout.addWidget(self.phone_container)
        
        self.details_form = QFormLayout()
        self.details_form.setSpacing(4)
        self.details_form.setContentsMargins(0, 10, 0, 10)
        
        self.init_detail_widgets()
        
        self.rebuild_details_panel()
        
        contact_container_layout.addLayout(self.details_form)
        self.main_layout.addWidget(self.contact_container)
        
        notes_header = QHBoxLayout()
        notes_label = QLabel("Notes")
        notes_label.setStyleSheet("color: #888888; font-weight: bold; font-size: 11px;")
        notes_header.addWidget(notes_label)
        
        notes_header.addStretch()  # Push Calls Today to the right
        
        self.calls_today_label = QLabel("Calls Today: 0")
        self.calls_today_label.setStyleSheet("color: #888888; font-weight: bold; font-size: 11px;")
        notes_header.addWidget(self.calls_today_label)
        
        self.main_layout.addLayout(notes_header)
        
        self.notes_text = QTextEdit()
        self.notes_text.setPlaceholderText("Type notes here...")
        self.notes_text.setFixedHeight(80)  # Max Height: 80px
        self.notes_text.setStyleSheet("""
            QTextEdit {
                background-color: #252525;
                color: #cccccc;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 5px;
                font-size: 11px;
            }
        """)
        self.main_layout.addWidget(self.notes_text)
        
        call_button_container = QHBoxLayout()
        call_button_container.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.call_btn = QPushButton("Call")
        self.call_btn.setFixedSize(120, 35)  # Fixed Width: ~120px, Height: ~35px
        self.call_btn.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        self.call_btn.setEnabled(False)  # Starts DISABLED
        call_button_container.addWidget(self.call_btn)
        self.main_layout.addLayout(call_button_container)
        
        self.call_status_label = QLabel("Ready")
        self.call_status_label.setFont(QFont("Segoe UI", 10))
        self.call_status_label.setStyleSheet("color: #888888; font-style: italic;")
        self.call_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.addWidget(self.call_status_label)
        
        disposition_label = QLabel("Disposition")
        disposition_label.setStyleSheet("color: #888888; font-weight: bold; font-size: 11px;")
        self.main_layout.addWidget(disposition_label)
        
        self.disposition_container = QFrame()
        disposition_grid = QGridLayout(self.disposition_container)
        disposition_grid.setSpacing(5)
        disposition_grid.setContentsMargins(0, 0, 0, 0)
        
        dispositions = [
            ("Booked Meeting", 0, 0),
            ("No Response", 0, 1),
            ("Sent Info", 1, 0),
            ("Not Interested", 1, 1),
            ("Call Back Later", 2, 0),
            ("Wrong Number", 2, 1),
            ("Voicemail (Verified)", 3, 0),
            ("Invalid Contact", 3, 1),
        ]
        
        self.disposition_buttons = {}
        for text, row, col in dispositions:
            btn = QPushButton(text)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #3d3d3d;
                    color: white;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                    font-size: 10px;
                }
                QPushButton:hover {
                    background-color: #4d4d4d;
                }
            """)
            btn.clicked.connect(lambda checked, t=text: self.handle_disposition(t))
            disposition_grid.addWidget(btn, row, col)
            self.disposition_buttons[text] = btn
        
        go_back_btn = QPushButton("Previous Contact")
        go_back_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        go_back_btn.clicked.connect(self.handle_go_back)
        disposition_grid.addWidget(go_back_btn, 4, 0, 1, 2)  # Span 2 columns
        self.go_back_btn = go_back_btn
        
        next_contact_btn = QPushButton("Next Contact")
        next_contact_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 8px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
            QPushButton:disabled {
                background-color: #555555;
                color: #888888;
            }
        """)
        next_contact_btn.clicked.connect(self._on_next_contact)
        disposition_grid.addWidget(next_contact_btn, 5, 0, 1, 2)  # Span 2 columns
        self.next_contact_btn = next_contact_btn
        
        self.main_layout.addWidget(self.disposition_container)
        self.main_layout.addStretch()  # Push everything to top
        
        self.statusBar().showMessage("Ready")
    
    def setup_connections(self):
        """Wire up signals."""
        self.import_btn.clicked.connect(self.import_contacts)
        self.call_btn.clicked.connect(self.start_call)
        self.config_btn.clicked.connect(self.open_settings_dialog)
        self.debug_btn.clicked.connect(self.open_debug_dialog)
        self.filters_btn.clicked.connect(self.open_filters_dialog)
        
        self.dot_animation_timer.timeout.connect(self.update_calling_animation)

        self.call_timer.timeout.connect(self.update_call_timer)

        QShortcut(QKeySequence("Ctrl+N"), self, self._on_next_contact)

        self.teams_controller.call_ended_signal.connect(
            self.on_call_ended,
            Qt.ConnectionType.QueuedConnection
        )
        self.teams_controller.call_started_signal.connect(self.on_call_started)

        self.streamer.register_message_handler("force_hangup", self.handle_force_hangup)
        self.streamer.register_message_handler("audio_ack", self.handle_audio_ack)
    
    def set_ui_state_highlight(self, state, is_reminder=False):
        """
        Applies pulsing highlight based on state.
        state: 'READY' (Contact Loaded), 'NEXT_NUMBER' (Phone Number Changed), 
               'DISPOSITION' (Call Ended), 'OFF' (Clear all)
        is_reminder: If True, this is a reminder pulse (shorter duration)
        """
        self.current_ui_state = state
        self.idle_timer.start(60000)  # Reset idle timer (1 minute)
        
        if not hasattr(self, 'overlay_contact'):
            scroll_area = self.centralWidget()
            central_widget = scroll_area.widget() if hasattr(scroll_area, 'widget') else scroll_area
            self.overlay_contact = OverlayHighlight(self.contact_container, central_widget)
            self.overlay_call = OverlayHighlight(self.call_btn, central_widget)
            self.overlay_dispo = OverlayHighlight(self.disposition_container, central_widget)
            self.overlay_phone = OverlayHighlight(self.phone_container, central_widget)
        
        self.overlay_contact.hide()
        self.overlay_call.hide()
        self.overlay_dispo.hide()
        self.overlay_phone.hide()
        
        loop_count = 2  # Pulse twice then stop
        
        if state == 'READY':
            self.overlay_contact.start(loop_count)
            self.overlay_call.start(loop_count if is_reminder else -1)  # Infinite loop for call button initially
        
        elif state == 'NEXT_NUMBER':
            self.overlay_phone.start(loop_count)
            self.overlay_call.start(loop_count)
        
        elif state == 'DISPOSITION':
            self.overlay_dispo.start(loop_count)
        
        elif state == 'OFF':
            pass
    
    def trigger_reminder_pulse(self):
        """Re-show highlight for idle reminder."""
        if self.current_ui_state and self.current_ui_state != 'OFF':
            self.set_ui_state_highlight(self.current_ui_state, is_reminder=True)
    
    def save_notes_from_ui(self):
        """Sync notes widget text into current contact."""
        if self.current_contact:
            new_notes = self.notes_text.toPlainText().strip()
            self.current_contact['Notes'] = new_notes
    

    def check_audio_setup(self) -> tuple[bool, str]:
        """
        Checks if 'Stereo Mix' is the default recording device
        Returns a tuple of (is_setup_correct, message).
        """
        try:
            default_input_device = self.audio_capture.audio.get_default_input_device_info()
            device_name = default_input_device.get('name', '').lower()

            print(f"Startup Check: Default recording device is '{default_input_device.get('name')}'")

            if 'stereo mix' in device_name:
                print("Startup Check PASSED: 'Stereo Mix' is the default recording device.")
                return (True, "Audio setup is correct.")
            else:
                error_message = (
                    "<h2>Audio Configuration Error</h2>"
                    "<p>To enable call monitoring, your system's audio needs to be configured correctly. "
                    "This is a one-time setup.</p>"
                    "<p><b>Required Action:</b></p>"
                    "<ol>"
                    "<li>In Windows Sound settings (Recording tab), find <b>'Stereo Mix'</b>.</li>"
                    "<li>If you don't see it, right-click and select 'Show Disabled Devices'.</li>"
                    "<li>Right-click on <b>'Stereo Mix'</b> and set it as the <b>'Default Device'</b>.</li>"
                    "</ol>"
                    f"<p>Your current default recording device is:<br><b>'{default_input_device.get('name')}'</b></p>"
                    "<p>Please correct the setting and restart the application.</p>"
                )
                return (False, error_message)

        except Exception as e:
            error_message = (
                "<h2>Could Not Verify Audio Setup</h2>"
                f"<p>An error occurred while checking audio devices: {e}</p>"
                "<p>Please ensure 'Stereo Mix' is enabled and a microphone is connected.</p>"
            )
            return (False, error_message)
    
    def refresh_from_excel(self):
        """Re-read visible rows from Excel (after filter/sort changes).
        Deactivated from GUI - button removed. Kept for internal use (e.g. after Excel sort)."""
        if not self.contact_list:
            self.import_contacts()
            return
        try:
            df, _ = self.excel_reader.load_data_shadow_copy(visible_only=True)
            sheet_name = None
            if self.excel_reader.active_workbook:
                try:
                    sheet_name = self.excel_reader.active_workbook.ActiveSheet.Name
                except Exception:
                    pass
            self.contact_list = self.excel_reader.process_contacts(df, sheet_name=sheet_name)
            if not self.contact_list:
                self.statusBar().showMessage("No visible contacts after filter. Try clearing Excel filter.")
                return
            self.current_index = 0
            self.current_phone_idx = 0
            self.display_current_contact()
            self.apply_filters()
            self.statusBar().showMessage(f"Refreshed: {len(self.contact_list)} visible contacts")
        except Exception as e:
            self.statusBar().showMessage(f"Refresh error: {e}")
            print(f"Refresh error: {e}")

    def import_contacts(self, target_index=None, target_sorts=None):
        """
        Import contacts from active Excel workbook.
        target_index: Index to resume at.
        target_sorts: active_sorts to restore (State, Industry, etc. selections).
        """
        if isinstance(target_index, bool):
            target_index = None

        try:
            df, _ = self.excel_reader.load_data_shadow_copy()

            sheet_name = None
            if self.excel_reader.active_workbook:
                try:
                    sheet_name = self.excel_reader.active_workbook.ActiveSheet.Name
                except Exception:
                    pass

            self.contact_list = self.excel_reader.process_contacts(df, sheet_name=sheet_name)

            if not self.contact_list:
                self.statusBar().showMessage("No valid contacts found in Excel file (check headers: email, phone, first name, company)")
                return

            workbook_path = None
            if self.excel_reader.active_workbook:
                try:
                    workbook_path = self.excel_reader.active_workbook.FullName
                except Exception:
                    pass

            current_colors = self.settings_manager.get_colors()
            self.disposition_saver = DispositionSaver(
                config.ONEDRIVE_BASE_PATH,
                active_workbook=self.excel_reader.active_workbook,
                workbook_path=workbook_path,
                colors=current_colors
            )

            start_index = 0

            if target_index is not None:
                start_index = target_index
            elif workbook_path:
                history = self.session_manager.get_workbook_history(workbook_path)
                if history:
                    saved_idx = history.get("current_index", 0)
                    saved_date = history.get("timestamp", "")
                    date_str = saved_date[:10] if saved_date else "Unknown"
                    if saved_idx > 0 and saved_idx < len(self.contact_list):
                        reply = QMessageBox.question(
                            self,
                            "Resume File?",
                            f"You previously worked on this file.\n\n"
                            f"Last saved: {date_str}\n"
                            f"Position: Contact #{saved_idx + 1} of {len(self.contact_list)}\n\n"
                            f"Resume where you left off?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.Yes
                        )
                        if reply == QMessageBox.StandardButton.Yes:
                            start_index = saved_idx
                            target_sorts = history.get("active_sorts")
                            self.statusBar().showMessage(f"Resumed from contact #{saved_idx + 1}")
                        else:
                            self.statusBar().showMessage("Starting from beginning")

            self.current_index = start_index
            self.current_phone_idx = 0
            self.display_current_contact()
            self.statusBar().showMessage(f"Loaded {len(self.contact_list)} contacts")

            if target_sorts:
                self.filter_manager.active_sorts = target_sorts

            self.apply_filters()

            self._start_autosave_timer()
        
        except Exception as e:
            self.contact_list = []
            self.clear_contact_display()
            self.statusBar().showMessage(f"Import error: {e}")
            print(f"Import error: {e}")

    def check_for_resume(self):
        """Check for resumable session, prompt if found."""
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return

        session = self.session_manager.get_last_session()
        if not session:
            return

        workbook_path = session.get("workbook_path")
        if not workbook_path or not os.path.exists(workbook_path):
            return

        dialog = ResumeSessionDialog(session, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.resume_session(session)

    def resume_session(self, session: Dict[str, Any]):
        """Resume session: reopen workbook, jump to saved index."""
        path = session.get("workbook_path")
        target_index = session.get("current_index", 0)
        target_sorts = session.get("active_sorts")

        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Error", "Saved workbook path no longer exists.")
            return

        self.statusBar().showMessage(f"Opening Excel: {os.path.basename(path)}...")
        try:
            os.startfile(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not launch Excel: {e}")
            return

        self.resume_attempt_count = 0
        self.target_resume_index = target_index
        self.target_resume_sorts = target_sorts
        self.resume_timer = QTimer()
        self.resume_timer.timeout.connect(self._check_excel_ready_for_resume)
        self.resume_timer.start(1000)  # Check every second

    def _check_excel_ready_for_resume(self):
        """Retry connecting to Excel on resume."""
        self.resume_attempt_count += 1
        
        try:
            t_idx = getattr(self, 'target_resume_index', 0)
            t_sorts = getattr(self, 'target_resume_sorts', None)
            self.import_contacts(target_index=t_idx, target_sorts=t_sorts)
            
            if self.contact_list:
                self.resume_timer.stop()
                
                
                if self.current_index >= len(self.contact_list):
                    self.current_index = 0
                    self.display_current_contact()
                
                self.statusBar().showMessage(f"Session Resumed at #{self.current_index + 1}")
                return
                
        except Exception:
            pass

        if self.resume_attempt_count > 10:
            self.resume_timer.stop()
            self.statusBar().showMessage("Failed to reconnect to Excel. Please click 'Import' manually.")
            QMessageBox.warning(self, "Timeout", "Excel took too long to open. Please click 'Import from Active Excel' manually once Excel is ready.")
    
    def display_current_contact(self):
        """Show current contact in UI."""
        if not self.contact_list or self.current_index >= len(self.contact_list):
            self.clear_contact_display()
            return
        
        self.has_no_response_history = False
        
        self.current_contact = self.contact_list[self.current_index]
        
        new_sheet_name = self.current_contact.get("Sheet Name") or self.current_contact.get("sheetName")
        if new_sheet_name and new_sheet_name != self._last_sheet_name:
            self.excel_reader.activate_sheet(new_sheet_name)
            self._last_sheet_name = new_sheet_name
        
        sorted_numbers = self.sort_phones(self.current_contact)
        self.current_contact['Phones'] = sorted_numbers
        
        first_name = self.current_contact.get('First Name', '')
        last_name = self.current_contact.get('Last Name', '')
        name = f"{first_name} {last_name}".strip() or "Unknown"
        self.name_label.setText(name)
        
        self.current_phone_idx = 0
        self.update_phone_display()
        
        # Even if a widget is hidden (not in layout), it still exists and can accept text.
        
        # Map of Data Keys -> Widget Keys
        key_map = {
            'Emails': 'Email',  # List needs joining
            'Title': 'Title',
            'Company': 'Company',
            'Website': 'Website',
            'Industry': 'Industry',
            'Revenue': 'Revenue',
            'State': 'State',
            'Timezone': 'Timezone',
            'LinkedIn': 'LinkedIn'
        }
        
        for data_key, widget_key in key_map.items():
            if widget_key in self.detail_widgets_map:
                _, widget = self.detail_widgets_map[widget_key]
                
                val = self.current_contact.get(data_key, []) if data_key == 'Emails' else self.current_contact.get(data_key, '')
                
                # Handle Lists (Emails) vs Strings
                if isinstance(val, list):
                    text = ", ".join(val) if val else "-"
                else:
                    text = str(val) if val else "-"
                
                widget.setText(text)
                
                # Handle special properties for links
                if widget_key in ['Website', 'LinkedIn']:
                    widget.setProperty('url', text if text != "-" else "")
        
        # 5. Update Notes
        notes = self.current_contact.get('Notes', '')
        self.notes_text.setText(notes if notes else "")
        
        # Save session state (use cached workbook_path to avoid COM errors when Excel is busy)
        path = None
        if hasattr(self, 'disposition_saver') and self.disposition_saver and self.disposition_saver.workbook_path:
            path = self.disposition_saver.workbook_path
        elif self.excel_reader.active_workbook:
            try:
                path = self.excel_reader.active_workbook.FullName
            except Exception as e:
                print(f"Error getting workbook path for session: {e}")
        if path:
            try:
                sheet = self.current_contact.get('Sheet Name')
                current_sorts = self.filter_manager.active_sorts
                self.session_manager.save_session(
                    workbook_path=path,
                    current_index=self.current_index,
                    sheet_name=sheet,
                    active_sorts=current_sorts
                )
            except Exception as e:
                print(f"Error saving session: {e}")
        
        # Highlight contact info and call button (new contact loaded)
        self.set_ui_state_highlight('READY')

        # Update Next Contact button state
        self._update_nav_buttons()

    def _update_nav_buttons(self):
        """Update Next Contact button enabled state."""
        if not hasattr(self, 'next_contact_btn'):
            return
        if not self.contact_list:
            self.next_contact_btn.setEnabled(False)
            return
        has_next = self.current_index < len(self.contact_list) - 1
        self.next_contact_btn.setEnabled(has_next)

    def _on_next_contact(self):
        """Handle Next Contact button click or Ctrl+N."""
        if not self.contact_list:
            return
        self.move_to_next_valid_contact()
        self._update_nav_buttons()

    def _start_autosave_timer(self):
        """Start or restart the Excel autosave timer based on settings."""
        interval_min = self.settings_manager.get_excel_settings().get("autosave_interval_minutes", 10)
        self.autosave_timer.stop()
        if interval_min > 0 and hasattr(self, 'disposition_saver') and self.disposition_saver:
            self.autosave_timer.setInterval(interval_min * 60 * 1000)
            self.autosave_timer.start()

    def _on_autosave_tick(self):
        """Called when autosave timer fires. Saves the active Excel workbook."""
        if hasattr(self, 'disposition_saver') and self.disposition_saver and self.contact_list:
            self.disposition_saver.save_workbook_async()
            from datetime import datetime
            self.statusBar().showMessage(f"Autosaved at {datetime.now().strftime('%H:%M')}")

    def apply_filters(self):
        """Refresh valid_indices to full list order (all rows visible; sorting is done via Excel)."""
        if not self.contact_list:
            self.valid_indices = []
            return
        self.valid_indices = list(range(len(self.contact_list)))
        self.statusBar().showMessage(f"{len(self.contact_list)} contacts in list.")
        self._update_nav_buttons()

    def move_to_next_valid_contact(self):
        """Move to next contact in list order."""
        if not self.contact_list:
            return
        if self.current_index < len(self.contact_list) - 1:
            self.current_index += 1
            self.current_phone_idx = 0
            self.current_contact = self.contact_list[self.current_index]
            self.display_current_contact()
        else:
            self.statusBar().showMessage("End of list.")

    def open_filters_dialog(self):
        """Open the Filters dialog. Apply triggers sort-by-selection, reload, and UI refresh."""
        if not self.contact_list:
            QMessageBox.information(self, "No Contacts", "Please import contacts before applying filters.")
            return
        dialog = FiltersDialog(self.contact_list, self.filter_manager, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_filters()

    def _apply_excel_sort_by_selection(self, active_sorts: Dict[str, List[str]], sort_az_category: Optional[str] = None):
        """Sort Excel: by selection order (priority) or A–Z for one category. Saves workbook and reloads list."""
        xlSortOnValues = 0
        xlAscending = 1
        xlYes = 1
        CATEGORY_HEADERS = {
            "State": ["state", "company state abbr"],
            "Industry": ["industry", "company industry"],
            "Title": ["title", "job title"],
            "Revenue": ["revenue", "company revenue range", "annual revenue"],
            "Disposition": ["disposition", "dispo", "_dispo_stamp", "disposition stamp"],
        }
        try:
            self.excel_reader.get_active_file_path()
            workbook = self.excel_reader.active_workbook
            if not workbook:
                raise RuntimeError("No Excel workbook")
            sheet = workbook.ActiveSheet
            rng = sheet.UsedRange
            nr, nc = rng.Rows.Count, rng.Columns.Count
            if nr < 2:
                self.statusBar().showMessage("No data rows to sort.")
                return
            headers = {}
            for c in range(1, nc + 1):
                try:
                    h = rng.Cells(1, c).Value
                    if h:
                        s = str(h).strip().lower()
                        for cat, names in CATEGORY_HEADERS.items():
                            if cat not in headers:
                                for n in names:
                                    if n in s:
                                        is_json = cat == "Disposition" and ("_dispo_stamp" in s or "disposition stamp" in s)
                                        headers[cat] = (c, is_json)
                                        break
                except Exception:
                    pass

            if sort_az_category:
                if sort_az_category not in headers:
                    self.statusBar().showMessage(f"No column found for '{sort_az_category}'.")
                    return
                col, _ = headers[sort_az_category]
                sort_rng = sheet.Range(sheet.Cells(1, 1), sheet.Cells(nr, nc))
                key_range = sort_rng.Columns(col)
                sheet.Sort.SortFields.Clear()
                sheet.Sort.SortFields.Add(Key=key_range, SortOn=xlSortOnValues, Order=xlAscending)
                sheet.Sort.SetRange(sort_rng)
                sheet.Sort.Header = xlYes
                sheet.Sort.Apply()
                if hasattr(self, "disposition_saver") and self.disposition_saver:
                    self.disposition_saver.save_workbook_async()
                self.refresh_from_excel()
                self.current_index = 0
                self.display_current_contact()
                self.statusBar().showMessage(f"Sorted A–Z by {sort_az_category}: {len(self.contact_list)} contacts.")
                return
            active = {k: v for k, v in (active_sorts or {}).items() if v}
            if not active:
                self.statusBar().showMessage("No sort criteria selected.")
                return
            for cat in active:
                if cat not in headers:
                    self.statusBar().showMessage(f"No matching column for '{cat}'.")
                    return
            helper_col = nc + 1
            sheet.Cells(1, helper_col).Value = "_sort_help"
            for r in range(2, nr + 1):
                match_index = None
                for cat, selected in active.items():
                    if cat not in headers or not selected:
                        continue
                    col, is_json = headers[cat]
                    val = sheet.Cells(r, col).Value
                    cell_val = None
                    if val is not None:
                        sv = str(val).strip()
                        if is_json:
                            try:
                                import json as _json
                                data = _json.loads(sv)
                                cell_val = data.get("disposition")
                            except Exception:
                                pass
                        else:
                            cell_val = sv
                    cell_str = (cell_val or "-").strip().lower()
                    selected_norm = [str(x).strip().lower() for x in selected]
                    for idx, sel in enumerate(selected_norm):
                        if cell_str == sel:
                            match_index = idx
                            break
                    if match_index is None:
                        match_index = len(selected)
                    break
                if match_index is None:
                    match_index = 0
                sheet.Cells(r, helper_col).Value = match_index
            sort_rng = sheet.Range(sheet.Cells(1, 1), sheet.Cells(nr, helper_col))
            key_range = sort_rng.Columns(helper_col)
            sheet.Sort.SortFields.Clear()
            sheet.Sort.SortFields.Add(Key=key_range, SortOn=xlSortOnValues, Order=xlAscending)
            sheet.Sort.SetRange(sort_rng)
            sheet.Sort.Header = xlYes
            sheet.Sort.Apply()
            sheet.Columns(helper_col).Delete()
            if hasattr(self, "disposition_saver") and self.disposition_saver:
                self.disposition_saver.save_workbook_async()
            self.refresh_from_excel()
            self.current_index = 0
            self.display_current_contact()
            self.statusBar().showMessage(f"Sorted: {len(self.contact_list)} contacts.")
        except Exception as e:
            self.statusBar().showMessage(f"Could not sort Excel: {e}")
            print(f"Excel sort error: {e}")

    def perform_unified_search_or_jump(self):
        """
        Unified handler: row number -> jump, otherwise -> search by name/company/phone.
        """
        text = self.search_jump_input.text().strip()
        if not text:
            return
        if text.isdigit():
            success = self._jump_to_row(text)
        else:
            success = self._search_contacts(text)
        # Keep search text in the bar so you can press Enter again to cycle to next match

    def _jump_to_row(self, row_text: str) -> bool:
        """Jump to a specific row index. Returns True on success."""
        if not self.contact_list:
            self.statusBar().showMessage("No contacts loaded.")
            return False
        try:
            target_row = int(row_text)
            target_index = target_row - 1
            if 0 <= target_index < len(self.contact_list):
                if self.call_status != "Ready":
                    self.statusBar().showMessage("Cannot jump while in a call.")
                    return False
                self.current_index = target_index
                self.display_current_contact()
                self.statusBar().showMessage(f"Jumped to Row {target_row}")
                return True
            self.statusBar().showMessage(f"Invalid Row. Max is {len(self.contact_list)}.")
            return False
        except ValueError:
            self.statusBar().showMessage("Invalid number format.")
            return False
        except Exception as e:
            print(f"Jump error: {e}")
            self.statusBar().showMessage("Error jumping to row.")
            return False

    def _search_contacts(self, query: str) -> bool:
        """
        Search for text in Name, Company, or Phone. Cycles through matches if repeated.
        Returns True if a match was found and we navigated to it.
        """
        if not self.contact_list:
            self.statusBar().showMessage("No contacts loaded.")
            return False
        query = query.strip().lower()
        if not query:
            return False
        try:
            start_index = self.current_index + 1
            total_contacts = len(self.contact_list)
            indices_to_check = list(range(start_index, total_contacts)) + list(range(0, start_index))
            for i in indices_to_check:
                contact = self.contact_list[i]
                first = str(contact.get('First Name', '')).lower()
                last = str(contact.get('Last Name', '')).lower()
                company = str(contact.get('Company', '')).lower()
                phones = " ".join([str(p).lower() for p in contact.get('Phones', [])])
                if (query in first or query in last or query in company or query in phones):
                    self.current_index = i
                    self.display_current_contact()
                    self.statusBar().showMessage(f"Found match at Row {i + 1}")
                    return True
            contact = self.contact_list[self.current_index]
            first = str(contact.get('First Name', '')).lower()
            last = str(contact.get('Last Name', '')).lower()
            company = str(contact.get('Company', '')).lower()
            phones = " ".join([str(p).lower() for p in contact.get('Phones', [])])
            if (query in first or query in last or query in company or query in phones):
                self.statusBar().showMessage("Current contact matches query.")
            else:
                self.statusBar().showMessage(f"No matches found for '{query}'")
            return False
        except Exception as e:
            print(f"Search error: {e}")
            self.statusBar().showMessage("Error occurred during search.")
            return False

    def update_phone_display(self):
        """Update phone number display and counter, handling empty lists."""
        phones = self.current_contact.get('Phones', []) if self.current_contact else []
        
        if not phones:
            self.phone_label.setText("No valid numbers")
            self.phone_counter_label.setText("(0 of 0)")
            self.call_btn.setEnabled(False)
            # Reset index just in case
            self.current_phone_idx = 0
            return
        
        # Clamp index
        if self.current_phone_idx >= len(phones):
            self.current_phone_idx = len(phones) - 1
        if self.current_phone_idx < 0:
            self.current_phone_idx = 0
        
        # Display current phone
        current_phone = phones[self.current_phone_idx]
        phone_count = len(phones)
        self.phone_label.setText(current_phone)
        self.phone_counter_label.setText(f"({self.current_phone_idx + 1} of {phone_count})")
        # Enable call button when valid phone number is loaded
        self.call_btn.setEnabled(True)
    
        # Only trigger specific phone highlight if we are advancing numbers (index > 0)
        if self.current_phone_idx > 0:
            self.set_ui_state_highlight('NEXT_NUMBER')
    
    def update_calling_animation(self):
        """Update the 'Calling...' animation with dots"""
        if self.call_status == "Dialing":
            # Cycle: Calling. -> Calling.. -> Calling... -> Calling.
            dots = "." * ((self.dot_animation_count % 3) + 1)
            self.call_status_label.setText(f"Calling{dots}")
            self.dot_animation_count += 1
        elif self.call_status == "Calling":
            # Same animation for "Calling" state
            dots = "." * ((self.dot_animation_count % 3) + 1)
            self.call_status_label.setText(f"Calling{dots}")
            self.dot_animation_count += 1
    

    
    def handle_call_timer_update(self, time_text: str):
        """Handle call timer updates from TeamsController monitor (timer signal)"""
        # This is called from the background thread, so we need to use QTimer.singleShot
        # to safely update the GUI from the main thread
        from PyQt6.QtCore import QTimer as QtTimer
        QtTimer.singleShot(0, lambda: self._update_call_timer_ui(time_text))
    
    def _update_call_timer_ui(self, time_text: str):
        """Update UI with timer from Teams (called from main thread)"""
        # New Slot: update_call_timer(time_text)
        if not self.is_timer_synced:
            # If self.is_timer_synced is False:
            self.is_timer_synced = True
            
            # Stop the "Calling..." animation timer
            self.dot_animation_timer.stop()
            
            # Update the Status Label text to: In Call ({time_text})
            self.call_status = "Connected"
            self.call_status_label.setText(f"In Call ({time_text})")
            self.call_status_label.setStyleSheet("color: #4caf50; font-weight: bold;")  # Green for connected
        else:
            # Timer already synced, just update the time
            self.call_status_label.setText(f"In Call ({time_text})")
    
    def _update_call_status_ui(self, status: str):
        """Update UI based on call status (called from main thread)"""
        if status == "Connected":
            # On "Connected" Signal (from window title detection fallback)
            # Only update if timer hasn't been synced yet
            if not self.is_timer_synced:
                self.dot_animation_timer.stop()  # Stop the "Calling..." animation
                self.call_status = "Connected"
                self.call_status_label.setText("In Call (00:00)")
                self.call_status_label.setStyleSheet("color: #4caf50; font-weight: bold;")  # Green for connected
                
                # Start call duration timer (fallback if Teams timer not available)
                # Note: call_start_time is already set in on_call_started when window opens
                # Only set it here if it wasn't set yet (fallback case)
                if not hasattr(self, 'call_start_time') or not self.call_start_time:
                    import time
                    self.call_start_time = time.time()
                self.call_timer.start(1000)  # Update every second
            

    
    @pyqtSlot()
    def reset_ui_state(self):
        """Resets the UI state after a call ends. Thread-safe slot."""
        
        # Stop call monitoring (redundant safety)
        self.teams_controller.stop_monitoring()
        
        # Stop timers
        try:
            if self.dot_animation_timer.isActive():
                self.dot_animation_timer.stop()
        except:
            pass  # Timer might not exist or already stopped
        
        try:
            if self.call_timer.isActive():
                self.call_timer.stop()
        except:
            pass
        
        # Update UI text and colors
        self.call_status = "Call Ended"
        self.call_status_label.setText("Call Ended")
        self.call_status_label.setStyleSheet("color: #888888; font-weight: bold;")
        
        self.is_timer_synced = False
        self.call_btn.setEnabled(True)
        
        # Trigger disposition highlight
        self.set_ui_state_highlight('DISPOSITION')
    
    def on_call_ended(self):
        """
        Slot: Handle call ended event.
        Stops animation, resets UI, and re-enables call button.
        Notifies the server and logs the call to the database.
        """
        
        # First, stop the audio and streaming threads immediately.
        self.stop_audio_threads()
        
        # Reset UI state (this will also be called from stop_audio_threads, but safe to call twice)
        self.reset_ui_state()

        # Notify server that the call has ended and log it to database
        try:
            import time
            current_phone = ""
            contact_name = ""
            
            if self.current_contact:
                phones = self.current_contact.get("Phones", [])
                if phones and 0 <= self.current_phone_idx < len(phones):
                    current_phone = phones[self.current_phone_idx]
                
                first = self.current_contact.get("First Name", "")
                last = self.current_contact.get("Last Name", "")
                contact_name = f"{first} {last}".strip()
            
            # Calculate call duration if call_start_time is available
            duration = 0
            if hasattr(self, 'call_start_time') and self.call_start_time:
                duration = int(time.time() - self.call_start_time)
            else:
                duration = 0
            
            # Get agent_id from streamer
            agent_id = getattr(self.streamer, 'agent_id', '')
            
            # Send call ended message with complete information
            self.streamer.send_json_message({
                "status": "Call Ended",
                "phone": current_phone,
                "contact_name": contact_name,
                "duration": duration,
                "agent_id": agent_id,
                "campaign_mode": "General",
            })
            if current_phone:
                disp = getattr(self, "_pending_force_hangup_disposition", None) or "No Disposition"
                payload = {
                    "type": "log_disposition",
                    "agent_id": agent_id,
                    "phone": current_phone,
                    "contact_name": contact_name,
                    "disposition": disp,
                    "duration": duration,
                    "notes": "",
                    "campaign_mode": "General",
                }
                if self.current_contact:
                    try:
                        payload["contact_data"] = self.current_contact
                    except Exception:
                        pass
                self.streamer.send_json_message(payload)
                # Mark that we created a call record - handle_disposition will only send update_disposition if this is True
                self._call_record_created = True
            
            # Reset call_start_time for next call
            self.call_start_time = None

        except Exception as e:
            print(f"Error sending Call Ended message to server: {e}")
            import traceback
            traceback.print_exc()

    def on_call_started(self):
        """
        Slot: Logs call start to server and increments counter.
        Includes guards to prevent double-logging for the same call session.
        """
        import time
        
        # Prevent double processing if we already handled this call start recently
        now = time.time()
        if hasattr(self, '_last_call_start_processed') and (now - self._last_call_start_processed < 5.0):
            return
        self._last_call_start_processed = now
        
        if not self.call_start_time:
            self.call_start_time = now
        
        # Get contact information
        current_phone = ""
        contact_name = ""
        if self.current_contact:
            phones = self.current_contact.get("Phones", [])
            if phones and 0 <= self.current_phone_idx < len(phones):
                current_phone = phones[self.current_phone_idx]
            
            first = self.current_contact.get("First Name", "")
            last = self.current_contact.get("Last Name", "")
            contact_name = f"{first} {last}".strip()
        
        # Get agent_id from streamer
        agent_id = getattr(self.streamer, 'agent_id', '')
        
        # Update local UI counter; DB is updated when the call ends via log_disposition
        self.calls_today += 1
        if hasattr(self, 'calls_today_label'):
            self.calls_today_label.setText(f"Calls Today: {self.calls_today}")
        
        # Check if auto-call is enabled
        calling_settings = self.settings_manager.get_calling_settings()
        auto_call_enabled = calling_settings.get("auto_call_enabled", True)
        
        if not auto_call_enabled:
            # Still send metadata update
            try:
                self.streamer.send_metadata({
                    'status': 'Calling',
                    'lead': current_phone,
                    'contact': contact_name
                })
            except Exception as e:
                print(f"Error sending metadata update: {e}")
            return
        
        try:
            self.streamer.send_json_message({
                "type": "call_started",   # matches server check: message.get("type") == "call_started"
                "agent_id": agent_id
            })
            
            # Send metadata update for dashboard
            self.streamer.send_metadata({
                'status': 'Calling',
                'lead': current_phone,
                'contact': contact_name
            })
        except Exception as e:
            print(f"Error sending call_started message to server: {e}")
    
    def update_call_timer(self):
        """Update the call duration timer display (fallback if Teams timer not available)"""
        # This only runs if Teams timer signal was never received
        if self.call_status == "Connected" and self.call_start_time and not self.is_timer_synced:
            import time
            elapsed = int(time.time() - self.call_start_time)
            minutes = elapsed // 60
            seconds = elapsed % 60
            self.call_status_label.setText(f"In Call ({minutes:02d}:{seconds:02d})")
    
    def start_call(self):
        """Start a call to the current phone number - non-blocking Teams automation"""
        if not self.current_contact:
            return
        
        phones = self.current_contact.get('Phones', [])
        if not phones or self.current_phone_idx >= len(phones):
            return
        
        current_phone = phones[self.current_phone_idx]

        self.call_btn.setEnabled(False)
        self.call_status = "Dialing"
        self.call_status_label.setText("Dialing...")
        self.call_status_label.setStyleSheet("color: #ffa500; font-weight: bold;")  # Orange for dialing

        # Clear highlighting (call in progress)
        self.set_ui_state_highlight('OFF')

        # Reset idle timer (user activity)
        self.idle_timer.start(60000)

        # Reset timer sync flag
        self.is_timer_synced = False

        # Start the "Calling..." animation timer (500ms interval)
        self.dot_animation_count = 0
        self.dot_animation_timer.start(500)  # Update every 500ms

        self.current_call_id = None

        self.dial_worker = DialingWorker(self.teams_controller, current_phone)
        self.dial_worker.finished_signal.connect(self.on_dialing_finished)
        self.dial_worker.start()

    def on_dialing_finished(self, success: bool):
        """
        Called when the Teams automation script finishes trying to initiate the call.
        Runs on the GUI thread.
        """
        if success:
            # Automation worked (or active call window detected)
            import time
            self.call_start_time = time.time()
            self.on_call_started()

            # Start monitoring the call window with timer callback and direct audio stop callback
            self.teams_controller.monitor_call_window(
                timer_callback=self.handle_call_timer_update,
                on_ended_callback=self.stop_audio_threads
            )

            # Start streamer worker first (needs to be ready to receive audio)
            self.streamer_worker = StreamerWorker(self.streamer, self.audio_queue)
            self.streamer_worker.start()

            # Start audio capture worker (feeds audio to queue)
            self.audio_worker = AudioWorker(self.audio_capture, self.audio_queue)
            # No local timeout: hangup is server-driven only (force_hangup). Avoids hanging up on
            # a live person when server's human_detected is delayed due to event loop blockage.
            self.audio_worker.start()

            # Update status bar
            self.statusBar().showMessage("Call initiated...")

            # Send metadata
            if self.current_contact:
                current_phone = self.current_contact.get('Phones', [None])[self.current_phone_idx] if self.current_phone_idx < len(self.current_contact.get('Phones', [])) else None
                self.streamer.send_metadata({
                    'status': 'Calling',
                    'lead': current_phone,
                    'contact': self.current_contact.get('First Name', '') + ' ' + self.current_contact.get('Last Name', '')
                })
        else:
            # Automation failed (timeout or could not detect call window/button)
            self.dot_animation_timer.stop()
            self.call_status = "Ready"
            self.call_status_label.setText("Call Failed (Teams Error)")
            self.call_status_label.setStyleSheet("color: #ff4444; font-weight: bold;")
            self.call_btn.setEnabled(True)
            self.statusBar().showMessage("Could not detect Teams call window")
    
    def handle_disposition(self, disposition: str):
        """Handle disposition button click"""
        # Save notes from UI to contact record
        self.save_notes_from_ui()
        
        if disposition == "Sent Info":
            if not self.current_contact:
                return

            # 1. Ask the user if they want to schedule a follow-up
            follow_up_dialog = FollowUpDialog(self)
            if follow_up_dialog.exec() == QDialog.DialogCode.Accepted:  # User clicked "Yes"
                # 2. Show the standard callback scheduling dialog
                callback_dialog = CallbackDateTimeDialog(self)
                if callback_dialog.exec() == QDialog.DialogCode.Accepted:
                    # 3. User confirmed a date/time. Create the callback on the server.
                    scheduled_time = callback_dialog.get_datetime_iso()
                    notes = callback_dialog.get_notes()

                    # This logic is similar to handle_callback_later, but logs as "Sent Info"
                    import json
                    
                    # Retrieve Settings
                    rem_settings = self.settings_manager.get_reminder_settings()
                    time_filter_cfg = self.settings_manager.get_time_filter_settings()
                    
                    # Ensure defaults and handle missing keys
                    is_enabled = rem_settings.get("enabled", False)
                    stored_email = rem_settings.get("agent_email", "").strip()
                    
                    agent_email = None
                    if is_enabled and stored_email:
                        agent_email = stored_email
                    
                    source_path = self.disposition_saver.workbook_path
                    source_sheet = self.current_contact.get('sheetName', 'Unknown')
                    source_row = self.current_contact.get('rowNumber')

                    self.streamer.send_json_message({
                        "type": "create_callback",
                        "scheduled_time": scheduled_time,
                        "notes": notes,
                        "source_workbook_path": source_path,
                        "source_sheet_name": source_sheet,
                        "source_row_index": source_row,
                        "contact_data": json.dumps(self.current_contact),
                        
                        # New fields
                        "agent_email": agent_email,
                        "timezone": time_filter_cfg.get("my_timezone", "ET")
                    })

                    if agent_email:
                        self.statusBar().showMessage(f"Follow-up scheduled. Invite sent to {agent_email}. Saving as 'Sent Info'.")
                    else:
                        self.statusBar().showMessage(f"Follow-up scheduled. Saving as 'Sent Info'.")

        # Check if we are in a temporary callback state (callback call flow)
        if self.saved_state:
            print("Handling disposition for a temporary callback call.")
            # We still need to save the disposition for the contact that was called
            if self.current_contact:
                self.disposition_saver.save_contact_async(self.current_contact, disposition)

            # Restore the previous state
            self.contact_list = self.saved_state.get("contact_list", [])
            self.current_index = self.saved_state.get("current_index", 0)
            self.current_phone_idx = self.saved_state.get("current_phone_idx", 0)
            self.saved_state = None  # Clear the saved state

            # Reset UI to the restored state
            self.display_current_contact()
            self.call_status = "Ready"
            self.call_status_label.setText("Ready")
            self.call_btn.setEnabled(True)
            self.statusBar().showMessage("Callback disposition saved. Resuming original list.")

            # Stop audio/monitoring threads from the callback call
            self.stop_audio_threads()
            self.teams_controller.stop_monitoring()
            self.dot_animation_timer.stop()
            self.call_timer.stop()

            return

        if disposition == "Call Back Later":
            self.handle_callback_later()
            return
        
        if disposition == "No Response":
            # Flag that this contact has at least one valid number that didn't answer
            self.has_no_response_history = True

            phones = self.current_contact.get('Phones', []) if self.current_contact else []
            if phones and self.current_phone_idx < len(phones) - 1:
                # Update the call we just had before trying next number (only if we actually made a call)
                if getattr(self, "_call_record_created", False) and self.current_contact and self.streamer:
                    try:
                        first = self.current_contact.get("First Name", "")
                        last = self.current_contact.get("Last Name", "")
                        full_name = f"{first} {last}".strip()
                        agent_id = getattr(self.streamer, 'agent_id', '')
                        duration = 0
                        if hasattr(self, 'call_start_time') and self.call_start_time:
                            import time
                            duration = int(time.time() - self.call_start_time)
                        payload = {
                            "type": "update_disposition",
                            "agent_id": agent_id,
                            "phone": self.phone_label.text(),
                            "contact_name": full_name,
                            "disposition": "No Response",
                            "duration": duration,
                            "notes": self.notes_text.toPlainText(),
                            "campaign_mode": "General",
                        }
                        if self.current_contact:
                            try:
                                payload["contact_data"] = self.current_contact
                            except Exception:
                                pass
                        self.streamer.send_json_message(payload)
                        QTimer.singleShot(1000, self.refresh_calls_counter_from_server)
                    except Exception as e:
                        print(f"Error sending update_disposition to server: {e}")
                    self._call_record_created = False
                # Try next number
                self.current_phone_idx += 1
                self.update_phone_display()
                self.statusBar().showMessage(f"No response. Trying next number...")
                self.call_btn.setEnabled(True)
                self.call_status = "Ready"
                self.call_status_label.setText("Ready")
                # Stop audio threads before trying next number
                self.stop_audio_threads()
                self.teams_controller.stop_monitoring()
                self.dot_animation_timer.stop()
                self.call_timer.stop()
                # Auto-call next number if enabled (only applies within same contact)
                calling_settings = self.settings_manager.get_calling_settings()
                if calling_settings.get("auto_call_on_disposition", False):
                    QTimer.singleShot(400, self.start_call)
                return

        # Wrong number handling
        deleted_phone_for_history = None  # Track deleted phone for history
        if disposition == "Wrong Number":
            if self.current_contact:
                phones = self.current_contact.get('Phones', [])
                if phones and self.current_phone_idx < len(phones):
                    bad_number = phones[self.current_phone_idx]
                    deleted_phone_for_history = bad_number  # Capture before deletion

                    # Delete from source file
                    self.disposition_saver.delete_phone_number_from_source(self.current_contact, bad_number)

                    # Update memory - remove the bad number
                    phones.pop(self.current_phone_idx)
                    self.current_contact['Phones'] = phones
                    self.statusBar().showMessage(f"Removed wrong number: {bad_number}")

                # Check if numbers remain (use updated phones list)
                if phones:
                    # Update the call we just had before trying next number (only if we actually made a call)
                    if getattr(self, "_call_record_created", False) and self.current_contact and self.streamer:
                        try:
                            first = self.current_contact.get("First Name", "")
                            last = self.current_contact.get("Last Name", "")
                            full_name = f"{first} {last}".strip()
                            agent_id = getattr(self.streamer, 'agent_id', '')
                            duration = 0
                            if hasattr(self, 'call_start_time') and self.call_start_time:
                                import time
                                duration = int(time.time() - self.call_start_time)
                            self.streamer.send_json_message({
                                "type": "update_disposition",
                                "agent_id": agent_id,
                                "phone": self.phone_label.text(),
                                "contact_name": full_name,
                                "disposition": "Wrong Number",
                                "duration": duration,
                                "notes": self.notes_text.toPlainText(),
                                "campaign_mode": "General",
                            })
                            QTimer.singleShot(1000, self.refresh_calls_counter_from_server)
                        except Exception as e:
                            print(f"Error sending update_disposition to server: {e}")
                        self._call_record_created = False
                    # Stay on contact, adjust index if needed
                    if self.current_phone_idx >= len(phones):
                        self.current_phone_idx = len(phones) - 1
                    if self.current_phone_idx < 0:
                        self.current_phone_idx = 0
                    self.update_phone_display()
                    self.call_btn.setEnabled(True)
                    self.call_status = "Ready"
                    # Stop audio threads before trying next number
                    self.stop_audio_threads()
                    self.teams_controller.stop_monitoring()
                    self.dot_animation_timer.stop()
                    self.call_timer.stop()
                    return
                else:
                    # Contact has no numbers left
                    if self.has_no_response_history:
                        # We had a "No Response" earlier, so this contact is still valid.
                        # Save as "No Response" so we can retry the valid numbers later.
                        disposition = "No Response"
                        self.statusBar().showMessage("All numbers failed, but valid number exists. Saving as 'No Response'.")
                    else:
                        # Every single number was a Wrong Number. The contact is dead.
                        disposition = "Invalid Contact"
                        self.statusBar().showMessage("All numbers invalid. Saving as 'Invalid Contact'.")

        # Push to history stack before saving (for undo)
        if self.current_contact:
            # For "Wrong Number", restore the deleted phone in the contact copy for history
            contact_copy = self.current_contact.copy()
            if deleted_phone_for_history and 'Phones' in contact_copy:
                # Restore the deleted phone to the contact copy for history
                phones_list = contact_copy['Phones'].copy()
                # Insert at the original position
                phones_list.insert(min(self.current_phone_idx, len(phones_list)), deleted_phone_for_history)
                contact_copy['Phones'] = phones_list
            
            history_item = {
                "index": self.current_index,
                "contact": contact_copy,  # Deep copy with restored phone if applicable
                "disposition": disposition,
                "callback_id": None,  # Will be set if callback was created
                "deleted_phone": deleted_phone_for_history  # Will be set if phone was deleted
            }
            self.history_stack.append(history_item)
            # Update Go Back button state
            self.go_back_btn.setEnabled(True)
        
        # Stop audio threads
        self.stop_audio_threads()
        
        # Stop call monitoring and animations - Forcefully stop
        self.teams_controller.stop_monitoring()
        self.dot_animation_timer.stop()  # Forcefully stop "Calling..." animation
        self.call_timer.stop()
        
        # Reset call status
        self.call_status = "Ready"
        self.call_status_label.setText("Ready")
        self.call_status_label.setStyleSheet("color: #888888; font-weight: bold;")
        self.is_timer_synced = False  # Reset sync flag
        
        # Save disposition (async, non-blocking) - always save to Excel regardless of whether we called
        if self.current_contact:
            # Mark which phone index was active so Excel/OneDrive can bold that number
            try:
                self.current_contact["ActivePhoneIndex"] = self.current_phone_idx
            except Exception:
                pass
            self.disposition_saver.save_contact_async(self.current_contact, disposition)
        
        # Only send update_disposition if we actually made a call (on_call_ended created a record)
        # If user clicked disposition without calling, no call record exists - don't create one
        if getattr(self, "_call_record_created", False) and self.current_contact and self.streamer:
            try:
                first = self.current_contact.get("First Name", "")
                last = self.current_contact.get("Last Name", "")
                full_name = f"{first} {last}".strip()
                agent_id = getattr(self.streamer, 'agent_id', '')
                duration = 0
                if hasattr(self, 'call_start_time') and self.call_start_time:
                    import time
                    duration = int(time.time() - self.call_start_time)

                payload = {
                    "type": "update_disposition",
                    "agent_id": agent_id,
                    "phone": self.phone_label.text(),
                    "contact_name": full_name,
                    "disposition": disposition,
                    "duration": duration,
                    "notes": self.notes_text.toPlainText(),
                    "campaign_mode": "General",
                }
                if self.current_contact:
                    try:
                        payload["contact_data"] = self.current_contact
                    except Exception:
                        pass
                self.streamer.send_json_message(payload)
                QTimer.singleShot(1000, self.refresh_calls_counter_from_server)
            except Exception as e:
                print(f"Error sending update_disposition to server: {e}")
            self._call_record_created = False

        # Clear highlighting (disposition saved, moving to next)
        self.set_ui_state_highlight('OFF')
        
        self.move_to_next_valid_contact()
        QApplication.processEvents()
        
        if self.current_contact:
            self.display_current_contact()
            # Auto-call next contact's first number only when disposition was No Response and setting is on
            calling_settings = self.settings_manager.get_calling_settings()
            if disposition == "No Response" and calling_settings.get("auto_call_on_disposition", False):
                QTimer.singleShot(400, self.start_call)
    
    def handle_go_back(self):
        """Handle Go Back button click - undo the last disposition action"""
        if not self.history_stack:
            self.statusBar().showMessage("No actions to undo.")
            return
        
        last_action = self.history_stack.pop()
        
        # 1. Restore UI State
        self.current_index = last_action['index']
        # Restore the contact (including any modified phones)
        if self.current_index < len(self.contact_list):
            self.contact_list[self.current_index] = last_action['contact']
        self.current_contact = last_action['contact']
        self.display_current_contact()
        
        # 2. Revert Files
        # Remove from the disposition file where it was saved
        self.disposition_saver.remove_contact_from_file(last_action['contact'], last_action['disposition'])
        
        # Clear the stamp/color from the source file
        self.disposition_saver.revert_source_file_changes(last_action['contact'])
        
        # 3. Revert Server (if Callback)
        if last_action.get('callback_id'):
            self.streamer.send_json_message({
                "type": "cancel_callback",
                "callback_id": last_action['callback_id']
            })
        
        # Update Go Back button state
        if not self.history_stack:
            self.go_back_btn.setEnabled(False)
        
        self.statusBar().showMessage("Reverted last action.")
    
    def handle_callback_later(self):
        """Handle Call Back Later disposition - show datetime picker"""
        if not self.current_contact:
            return
        
        # Save notes from UI to contact record
        self.save_notes_from_ui()
        
        # Show datetime picker dialog
        dialog = CallbackDateTimeDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            scheduled_time = dialog.get_datetime_iso()
            notes = dialog.get_notes()
            
            import json
            
            # Retrieve Reminder Settings
            rem_settings = self.settings_manager.get_reminder_settings()
            time_filter_cfg = self.settings_manager.get_time_filter_settings()
            
            # Ensure defaults and handle missing keys
            is_enabled = rem_settings.get("enabled", False)
            stored_email = rem_settings.get("agent_email", "").strip()
            
            agent_email = None
            if is_enabled and stored_email:
                agent_email = stored_email
            
            # Get the workbook path from the disposition saver
            source_path = self.disposition_saver.workbook_path
            # Get the sheet name from the contact data
            source_sheet = self.current_contact.get('sheetName', 'Unknown')
            # Get the original row index from the contact data
            source_row = self.current_contact.get('rowNumber')
            
            # Send create_callback message to server with new fields
            self.streamer.send_json_message({
                "type": "create_callback",
                "scheduled_time": scheduled_time,
                "notes": notes,
                "source_workbook_path": source_path,
                "source_sheet_name": source_sheet,
                "source_row_index": source_row,
                "contact_data": json.dumps(self.current_contact),  # Send the whole contact as a JSON string
                
                # New fields for Server-Side Outlook
                "agent_email": agent_email,
                "timezone": time_filter_cfg.get("my_timezone", "ET")
            })
            
            # Push to history stack before saving (for undo functionality)
            history_item = {
                "index": self.current_index,
                "contact": self.current_contact.copy(),  # Deep copy important!
                "disposition": "Call Back Later",
                "callback_id": None,  # Will be updated when callback_created message is received
                "deleted_phone": None
            }
            self.history_stack.append(history_item)
            # Update Go Back button state
            self.go_back_btn.setEnabled(True)
            
            # Save disposition (still save as "Call Back Later")
            self.disposition_saver.save_contact_async(self.current_contact, "Call Back Later")
            
            if agent_email:
                self.statusBar().showMessage(f"Callback scheduled. Invite sent to {agent_email}")
            else:
                self.statusBar().showMessage(f"Callback scheduled locally.")
        
        # Move to next contact
        self.current_index += 1
        self.current_phone_idx = 0
        
        if self.current_index < len(self.contact_list):
            self.display_current_contact()
        else:
            self.clear_contact_display()
            self.statusBar().showMessage("All contacts processed")
            
            # Stop audio and reset
            self.stop_audio_threads()
            self.teams_controller.stop_monitoring()
            self.dot_animation_timer.stop()
            self.call_timer.stop()
            self.call_status = "Ready"
            self.call_status_label.setText("Ready")
            self.call_btn.setEnabled(True)
    
    def initialize_calls_counter(self):
        """Initialize the calls today counter by fetching from server"""
        self.refresh_calls_counter_from_server()
    
    def refresh_calls_counter_from_server(self):
        """Refresh the calls today counter by fetching from server"""
        def fetch_in_background():
            try:
                import getpass
                import urllib.request
                import json as json_lib
                
                agent_id = getpass.getuser()
                server_url = getattr(config, 'SERVER_API_URL', config.SERVER_URL.replace('ws://', 'http://').replace('wss://', 'https://').split('/ws')[0])
                
                stats_url = f"{server_url}/api/agent_stats/{agent_id}"
                
                with urllib.request.urlopen(stats_url, timeout=5) as response:
                    data = json_lib.loads(response.read().decode())
                    new_count = data.get('calls_today', 0)
                    
                    # Update UI on main thread
                    QTimer.singleShot(0, lambda: self._update_calls_counter_ui(new_count))
            except Exception as e:
                print(f"Could not fetch call count from server: {e}")
                # Keep current value
        
        # Run in background thread to avoid blocking
        import threading
        thread = threading.Thread(target=fetch_in_background, daemon=True)
        thread.start()
    
    def _update_calls_counter_ui(self, new_count: int):
        """Update the calls counter UI (must be called from main thread)"""
        self.calls_today = new_count
        if hasattr(self, 'calls_today_label'):
            self.calls_today_label.setText(f"Calls Today: {self.calls_today}")
    
    def request_overdue_callbacks(self):
        """Request overdue callbacks from server"""
        self.streamer.send_json_message({
            "type": "request_overdue_callbacks"
        })
    
    def check_incoming_messages(self):
        """Check for incoming messages from server"""
        message = self.streamer.get_message()
        if message:
            message_type = message.get("type")
            
            if message_type == "overdue_callbacks_list":
                self.handle_overdue_callbacks(message.get("callbacks", []))
            elif message_type == "show_callback_prompt":
                self.handle_callback_prompt(message)
            elif message_type == "callback_created":
                callback_id = message.get('callback_id')
                print(f"Callback created: {callback_id}")
                # Update the most recent history item with the callback_id if it was a "Call Back Later"
                if self.history_stack:
                    last_item = self.history_stack[-1]
                    if last_item.get('disposition') == "Call Back Later" and last_item.get('callback_id') is None:
                        last_item['callback_id'] = callback_id
            elif message_type == "callback_updated":
                print(f"Callback updated: {message.get('callback_id')}")
            elif message_type == "callback_cancelled":
                print(f"Callback cancelled: {message.get('callback_id')}")
            elif message_type == "callback_logged":
                print(f"Callback logged: {message.get('callback_id')}")
            elif message_type == "force_hangup":
                self.handle_force_hangup(message)
            elif message_type == "ai_unavailable":
                self.handle_ai_unavailable(message)
            elif message_type == "human_detected":
                self.statusBar().showMessage("Human detected - call connected")
            elif message_type == "announcement":
                self.handle_announcement(message)
    
    def handle_ai_unavailable(self, message):
        """Server reports AMD unavailable - no server-driven hangup. Client works like normal calling (AI off): user manually hangs up, logs disposition."""
        reason = message.get("reason", "unknown")
        self.statusBar().showMessage("AI monitoring unavailable - handle call manually")
        print(f"Server: AI unavailable ({reason}). No server-driven hangup; handle call manually.")

    def handle_announcement(self, message: Dict[str, Any]):
        """Show announcement popup from server."""
        msg_text = message.get("message", "")
        image_filename = message.get("image")
        dlg = AnnouncementPopupDialog(msg_text, image_filename, self)
        dlg.show()

    def handle_audio_ack(self, message):
        """Server confirms it received audio packets. Log on client for real-time visibility (helps debug control-panel log delay)."""
        packets = message.get("packets_received", 0)
        print(f"[Streamer] Server received {packets} audio packets")

    def handle_force_hangup(self, message):
        """Handle forced hangup from server (timeout, voicemail, or machine)."""
        if getattr(self, "_no_answer_handling", False):
            return  # Already handled by previous force_hangup
        self._no_answer_handling = True
        reason = message.get("reason", "timeout")
        if reason == "voicemail" or reason == "machine_no_pause":
            self._pending_force_hangup_disposition = "Voicemail"
            self.statusBar().showMessage("Voicemail detected. Moving to next...")
        else:
            self._pending_force_hangup_disposition = "No Response"
            self.statusBar().showMessage("No Answer (30s). Moving to next...")

        # 1. Physically Hang Up Teams
        self.teams_controller.hang_up()
        
        # 2. Stop Audio/Monitoring
        self.stop_audio_threads()
        self.teams_controller.stop_monitoring()
        self.dot_animation_timer.stop()
        self.call_timer.stop()

        # 3. Trigger Auto-Next Logic after delay (allow Teams to fully close call window)
        QTimer.singleShot(3500, self._process_no_answer_automation)

    def _process_no_answer_automation(self):
        """
        Logic:
        1. Check if current contact has more numbers.
        2. If yes, dial next number.
        3. If no, mark disposition (No Response or Voicemail), go to next contact, dial primary number.
        """
        self._no_answer_handling = False  # Clear guard so next call can be handled
        if not self.current_contact:
            return

        disposition = getattr(self, "_pending_force_hangup_disposition", "No Response")
        if hasattr(self, "_pending_force_hangup_disposition"):
            delattr(self, "_pending_force_hangup_disposition")

        # Ensure any lingering call is hung up before starting next (safety net)
        self.teams_controller.hang_up()

        phones = self.current_contact.get('Phones', [])
        
        # Scenario A: Try Next Number for SAME Contact
        if self.current_phone_idx < len(phones) - 1:
            self.current_phone_idx += 1
            self.update_phone_display()
            print(f"Auto-dialing next number: {phones[self.current_phone_idx]}")
            self.statusBar().showMessage(f"Dialing next number...")
            
            # Start call immediately
            self.start_call()
            
        # Scenario B: No more numbers, Move to NEXT Contact
        else:
            print(f"All numbers failed. Saving '{disposition}' and moving next.")
            self.disposition_saver.save_contact_async(self.current_contact, disposition)
            
            # Move index
            self.move_to_next_valid_contact()
            
            # Check if we actually have a new contact to call
            if self.current_contact:
                print(f"Auto-dialing next lead: {self.name_label.text()}")
                self.statusBar().showMessage(f"Dialing next lead...")
                # Start call immediately
                self.start_call()
            else:
                self.statusBar().showMessage("End of list.")
    
    def handle_overdue_callbacks(self, callbacks: List[Dict[str, Any]]):
        """Handle overdue callbacks list from server by showing a modal dialog."""
        if not callbacks:
            return  # No overdue callbacks, do nothing
        
        # Create and execute the dialog
        dialog = OverdueCallbacksDialog(callbacks, self)
        dialog.call_now_requested.connect(self.handle_callback_call_now)
        dialog.log_manual_requested.connect(self.handle_callback_log_manual)
        dialog.dismiss_all_requested.connect(self.handle_callback_dismiss_all)
        dialog.exec()
    
    def handle_callback_call_now(self, callback_data: Dict[str, Any]):
        """Handles the entire flow for calling an overdue callback."""
        import json
        
        # Parse contact data from JSON string
        contact_data_str = callback_data.get("contact_data", "{}")
        try:
            contact_data = json.loads(contact_data_str)
        except:
            contact_data = {}
        
        phones = contact_data.get("Phones", [])
        phone_to_call = phones[0] if phones else ""
        
        if not phone_to_call:
            QMessageBox.warning(self, "Error", "No phone number found for this contact.")
            return
        
        # Save current state
        self.saved_state = {
            "contact_list": self.contact_list.copy() if self.contact_list else [],
            "current_index": self.current_index,
            "current_phone_idx": self.current_phone_idx
        }
        
        # Set as current contact
        self.contact_list = [contact_data]
        self.current_index = 0
        self.current_phone_idx = 0
        self.display_current_contact()
        
        # 1. Initiate the call
        self.teams_controller.start_call(phone_to_call)
        
        # 2. Show "waiting for call to end" message
        # NOTE: A robust implementation would use the call_ended_signal from TeamsController
        # to trigger the disposition dialog automatically. For now, we use a simple message box.
        first_name = contact_data.get('First Name', '')
        last_name = contact_data.get('Last Name', '')
        contact_name = f"{first_name} {last_name}".strip() or "Contact"
        QMessageBox.information(self, "Call in Progress", 
                               f"Calling {contact_name}... Click OK when the call has ended.")
        
        # 3. Force disposition selection
        all_dispositions = list(self.disposition_buttons.keys())
        dispo_dialog = SelectDispositionDialog(all_dispositions, self)
        if dispo_dialog.exec() == QDialog.DialogCode.Accepted:
            chosen_disposition = dispo_dialog.selected_disposition
            
            # 4. Log the new disposition and update files
            callback_id = callback_data.get("id")
            if callback_id:
                self.streamer.send_json_message({
                    "type": "log_manual_callback",
                    "callback_id": callback_id,
                    "disposition_id": chosen_disposition
                })
            
            # Use the new smart update function
            notes = contact_data.get('Notes', '') if contact_data else ''
            self.disposition_saver.update_source_file(
                workbook_path=callback_data.get("source_workbook_path"),
                sheet_name=callback_data.get("source_sheet_name"),
                row_index=callback_data.get("source_row_index"),
                disposition=chosen_disposition,
                notes=notes
            )
            
            # Also save to the new disposition file
            self.disposition_saver.save_contact_async(contact_data, chosen_disposition)
            
            QMessageBox.information(self, "Success", f"Call logged with disposition: {chosen_disposition}")
        
        # Restore previous state
        if hasattr(self, 'saved_state') and self.saved_state:
            self.restore_previous_state()
    
    def save_current_state(self):
        """Save the current contact list state"""
        self.saved_state = {
            "contact_list": self.contact_list.copy() if self.contact_list else [],
            "current_index": self.current_index,
            "current_phone_idx": self.current_phone_idx
        }
    
    def restore_previous_state(self):
        """Restore the previous contact list state"""
        if hasattr(self, 'saved_state') and self.saved_state:
            self.contact_list = self.saved_state.get("contact_list", [])
            self.current_index = self.saved_state.get("current_index", 0)
            self.current_phone_idx = self.saved_state.get("current_phone_idx", 0)
            if self.contact_list:
                self.display_current_contact()
            else:
                self.clear_contact_display()
            self.saved_state = None
    
    def handle_callback_log_manual(self, callback_data: Dict[str, Any]):
        """Handles the full workflow for manually logging an overdue callback."""
        import json
        
        # 1. Force the user to select a new disposition
        all_dispositions = list(self.disposition_buttons.keys())

        # We must exclude "Call Back Later" itself from the options
        if "Call Back Later" in all_dispositions:
            all_dispositions.remove("Call Back Later")

        dispo_dialog = SelectDispositionDialog(all_dispositions, self)
        if dispo_dialog.exec() != QDialog.DialogCode.Accepted:
            return  # User cancelled

        chosen_disposition = dispo_dialog.selected_disposition
        if not chosen_disposition:
            return

        # 2. Get all necessary data
        contact_data_str = callback_data.get("contact_data", "{}")
        try:
            contact_data = json.loads(contact_data_str)
        except:
            contact_data = {}

        # 3. Send message to server to mark callback as completed
        self.streamer.send_json_message({
            "type": "log_manual_callback",
            "callback_id": callback_data.get("id"),
            "disposition_id": chosen_disposition
        })

        # 4. Update the original source Excel file with the new disposition
        notes = contact_data.get('Notes', '') if contact_data else ''
        self.disposition_saver.update_source_file(
            workbook_path=callback_data.get("source_workbook_path"),
            sheet_name=callback_data.get("source_sheet_name"),
            row_index=callback_data.get("source_row_index"),
            disposition=chosen_disposition,
            notes=notes
        )

        # 5. Save the contact to the NEW disposition file
        self.disposition_saver.save_contact_async(contact_data, chosen_disposition)

        # 6. Remove the contact from the OLD "Call Back Later" disposition file
        self.disposition_saver.remove_contact_from_file(contact_data, "Call Back Later")

        self.statusBar().showMessage(f"Manual call logged as '{chosen_disposition}'.")
    
    def handle_callback_dismiss_all(self, callbacks: List[Dict[str, Any]]):
        """Dismiss all overdue callbacks as No Response; they will not show again on next open."""
        import json
        for callback_data in callbacks:
            callback_id = callback_data.get("callback_id") or callback_data.get("id")
            if not callback_id:
                continue
            self.streamer.send_json_message({
                "type": "log_manual_callback",
                "callback_id": callback_id,
                "disposition_id": "No Response"
            })
            contact_data_str = callback_data.get("contact_data", "{}")
            try:
                contact_data = json.loads(contact_data_str)
            except Exception:
                contact_data = {}
            notes = contact_data.get("Notes", "") if contact_data else ""
            self.disposition_saver.update_source_file(
                workbook_path=callback_data.get("source_workbook_path"),
                sheet_name=callback_data.get("source_sheet_name"),
                row_index=callback_data.get("source_row_index"),
                disposition="No Response",
                notes=notes
            )
            self.disposition_saver.save_contact_async(contact_data, "No Response")
            self.disposition_saver.remove_contact_from_file(contact_data, "Call Back Later")
        self.statusBar().showMessage(f"Dismissed {len(callbacks)} callback(s) as No Response.")
    
    def handle_callback_prompt(self, message: Dict[str, Any]):
        """Handle show_callback_prompt message from server"""
        self.show_callback_prompt_dialog(message)
    
    def show_callback_prompt_dialog(self, callback_data: Dict[str, Any]):
        """Show callback prompt dialog"""
        dialog = CallbackPromptDialog(callback_data, self)
        result = dialog.exec()
        
        callback_id = callback_data.get("callback_id")
        
        if result == 1:  # Call Now
            # Save the current state before handling the callback
            self.saved_state = {
                "contact_list": self.contact_list.copy() if self.contact_list else [],
                "current_index": self.current_index,
                "current_phone_idx": self.current_phone_idx,
            }
            print("State saved before handling callback.")

            contact_name = callback_data.get("contact_name", "")
            contact_phone = callback_data.get("contact_phone", "")
            
            name_parts = contact_name.split() if contact_name else []
            callback_contact = {
                "First Name": name_parts[0] if name_parts else "",
                "Last Name": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
                "Phones": [contact_phone] if contact_phone else [],
                "Notes": callback_data.get("notes", "")
            }
            
            self.contact_list = [callback_contact]
            self.current_index = 0
            self.current_phone_idx = 0
            self.display_current_contact()
            self.start_call()
            
        elif result == 2:  # Remind Me Later
            reschedule_dialog = CallbackDateTimeDialog(self)
            if reschedule_dialog.exec() == QDialog.DialogCode.Accepted:
                new_scheduled_time = reschedule_dialog.get_datetime_iso()
                self.streamer.send_json_message({
                    "type": "update_callback",
                    "callback_id": callback_id,
                    "scheduled_time": new_scheduled_time
                })
                self.statusBar().showMessage(f"Callback rescheduled to {new_scheduled_time}")
        
        elif result == 3:  # Cancel
            self.streamer.send_json_message({
                "type": "cancel_callback",
                "callback_id": callback_id
            })
            self.statusBar().showMessage("Callback cancelled")
    
    def save_disposition_and_next(self, disposition: str):
        """Save disposition and move to next contact"""
        # Stop audio threads
        self.stop_audio_threads()
        
        # Save disposition
        if self.current_contact:
            self.disposition_saver.save_contact_async(self.current_contact, disposition)
        
        # Move to next
        self.current_index += 1
        self.current_phone_idx = 0
        
        if self.current_index < len(self.contact_list):
            self.display_current_contact()
        else:
            self.clear_contact_display()
            self.statusBar().showMessage("All contacts processed")
    
    def stop_audio_threads(self):
        """Stop audio capture and streaming threads (thread-safe)"""
        with self.audio_lock:
            # Stop the worker threads
            if self.audio_worker:
                self.audio_worker.stop()
                self.audio_worker.wait(1000)
                self.audio_worker = None
            
            # Force stop the capture engine directly (handles stuck workers)
            if self.audio_capture:
                self.audio_capture.stop_capture()
            
            if self.streamer_worker:
                self.streamer_worker.stop()
                self.streamer_worker.wait(1000)
                self.streamer_worker = None
        
            # IMPORTANT: Do NOT call streamer.stop() here - that would disconnect the WebSocket
            # The streamer connection should stay alive for the entire app session
            # Only audio streaming is stopped, not the WebSocket connection itself
        
            # Do NOT hang up here, as this function is now also called when a call naturally ends.
            # self.teams_controller.hang_up()  # Comment out or remove this line.
            
            # Force UI update on Main Thread
            # This ensures the UI updates even if the Qt signal fails to deliver
            QMetaObject.invokeMethod(self, "reset_ui_state", Qt.ConnectionType.QueuedConnection)
    
    def init_detail_widgets(self):
        """Initialize all detail labels and store them in a map."""
        self.detail_widgets_map = {}

        # Email
        self.email_label = QLabel("-")
        self.email_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.email_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.email_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.email_label)
        self.detail_widgets_map["Email"] = ("Email(s):", self.email_label)

        # Title
        self.title_label = QLabel("-")
        self.title_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.title_label)
        self.detail_widgets_map["Title"] = ("Title:", self.title_label)

        # Company
        self.company_label = QLabel("-")
        self.company_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.company_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.company_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.company_label)
        self.detail_widgets_map["Company"] = ("Company:", self.company_label)

        # Website
        self.website_label = QLabel("-")
        self.website_label.setStyleSheet("color: #0078d4; font-size: 11px; text-decoration: underline;")
        self.website_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.website_label.mousePressEvent = lambda e: self.open_website()
        self.detail_widgets_map["Website"] = ("Website:", self.website_label)

        # Industry
        self.industry_label = QLabel("-")
        self.industry_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.industry_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.industry_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.industry_label)
        self.detail_widgets_map["Industry"] = ("Industry:", self.industry_label)

        # Revenue
        self.revenue_label = QLabel("-")
        self.revenue_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.revenue_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.revenue_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.revenue_label)
        self.detail_widgets_map["Revenue"] = ("Revenue:", self.revenue_label)

        # State
        self.state_label = QLabel("-")
        self.state_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.state_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.state_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.state_label)
        self.detail_widgets_map["State"] = ("State:", self.state_label)

        # Timezone
        self.timezone_label = QLabel("-")
        self.timezone_label.setStyleSheet("color: #cccccc; font-size: 11px;")
        self.timezone_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timezone_label.mousePressEvent = lambda e: self.copy_to_clipboard(self.timezone_label)
        self.detail_widgets_map["Timezone"] = ("Timezone:", self.timezone_label)

        # LinkedIn
        self.linkedin_label = QLabel("-")
        self.linkedin_label.setStyleSheet("color: #0078d4; font-size: 11px; text-decoration: underline;")
        self.linkedin_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.linkedin_label.mousePressEvent = lambda e: self.open_linkedin()
        self.detail_widgets_map["LinkedIn"] = ("LinkedIn:", self.linkedin_label)

    def rebuild_details_panel(self):
        """Clears and rebuilds the details form based on user settings."""
        
        while self.details_form.count():
            item = self.details_form.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        # Get settings
        display_order = self.settings_manager.get_display_order()
        hidden_fields = self.settings_manager.get_hidden_fields()

        # 3. Rebuild
        for field_name in display_order:
            if field_name in hidden_fields:
                continue

            if field_name in self.detail_widgets_map:
                label_text, widget = self.detail_widgets_map[field_name]

                # Make sure the widget is visible (in case it was hidden before)
                widget.setVisible(True)

                self.details_form.addRow(label_text, widget)

                # Apply generic style to the label we just added
                # (Accessing the label item we just added to the form)
                item = self.details_form.itemAt(self.details_form.rowCount() - 1, QFormLayout.ItemRole.LabelRole)
                if item and item.widget():
                    item.widget().setStyleSheet("color: #888888; font-size: 11px;")
    
    def sort_phones(self, contact):
        """Sorts and filters the contact's phones based on user settings."""
        phone_objs = contact.get('Phone_Objects', [])
        if not phone_objs:
            return []

        priority_cfg = self.settings_manager.get_phone_priority()
        order = priority_cfg.get("order", [])
        disabled = priority_cfg.get("disabled", [])

        # Filter out disabled types
        active_phones = [p for p in phone_objs if p['type'] not in disabled]

        # Sort based on index in 'order' list
        # If type not in order, put it at the end
        def sort_key(p_obj):
            p_type = p_obj['type']
            try:
                return order.index(p_type)
            except ValueError:
                return 999

        active_phones.sort(key=sort_key)

        # Return just the list of number strings
        return [p['number'] for p in active_phones]
    
    def clear_contact_display(self):
        """Clear contact display"""
        self.current_contact = None
        self.name_label.setText("No Contact Loaded")
        self.phone_label.setText("No phone number")
        self.phone_counter_label.setText("")
        self.email_label.setText("-")
        self.title_label.setText("-")
        self.company_label.setText("-")
        self.website_label.setText("-")
        self.website_label.setProperty('url', '')
        self.industry_label.setText("-")
        self.revenue_label.setText("-")
        self.state_label.setText("-")
        self.timezone_label.setText("-")
        self.linkedin_label.setText("-")
        self.linkedin_label.setProperty('url', '')
        self.notes_text.setText("")
        self.call_btn.setEnabled(False)
        self._update_nav_buttons()
    
    def open_settings_dialog(self):
        """Open the master settings dialog"""
        dialog = SettingsDialog(self.settings_manager, self.audio_capture, self)
        dialog.exec()

    def open_debug_dialog(self):
        """Open the Debug / Queue Status dialog"""
        dialog = DebugQueueDialog(self)
        dialog.exec()
    
    def open_website(self):
        """Open website URL"""
        url = self.website_label.property('url')
        if url:
            QDesktopServices.openUrl(QUrl(url))
    
    def open_linkedin(self):
        """Open LinkedIn URL"""
        url = self.linkedin_label.property('url')
        if url:
            QDesktopServices.openUrl(QUrl(url))
    
    def copy_to_clipboard(self, label: QLabel):
        """Copy label text to clipboard"""
        text = label.text()
        # Skip placeholder/default text that shouldn't be copied
        skip_texts = ["-", "No Contact Loaded", "No phone number", "No valid numbers"]
        if text and text not in skip_texts:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self.statusBar().showMessage(f"Copied '{text}' to clipboard", 2000)  # Show for 2 seconds
    
    def closeEvent(self, event):
        """Handle window close event"""
        self.stop_audio_threads()
        event.accept()


def main():
    """Main entry point"""
    # DPI awareness fix - suppress SetProcessDpiAwarenessContext error
    import os
    os.environ['QT_AUTO_SCREEN_SCALE_FACTOR'] = '1'
    
    app = QApplication(sys.argv)
    
    # Apply dark theme
    app.setStyleSheet(qdarktheme.load_stylesheet())
    
    # Create and show main window
    window = TeamsPowerDialerApp()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
