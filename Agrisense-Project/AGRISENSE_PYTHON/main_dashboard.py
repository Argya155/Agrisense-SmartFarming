import sys
import json
import csv
import os
import requests
import ssl 
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QGridLayout, 
                             QFrame, QMessageBox, QComboBox, QPlainTextEdit) # QComboBox ditambahkan
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon
import pyqtgraph as pg
import paho.mqtt.client as mqtt
import google.generativeai as genai
import joblib
import pandas as pd
import ctypes

# ==========================================
# KONFIGURASI KREDENSIAL & API
# ==========================================
MQTT_BROKER = "MQTT_BROKER"
MQTT_PORT = 8883
MQTT_TOPIC = "agrisense/telemetry/gateway"
MQTT_USER = "admin_agrisense"
MQTT_PASSWORD = "Agrisense2026"

OWM_API_KEY = "OWM_API_KEY"
OWM_LAT, OWM_LON = "-5.168022", "119.433589"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "GEMINI_API_KEY")
CSV_FILENAME = "agrisense_log.csv"

# ==========================================
# WORKER THREADS (Logic Background)
# ==========================================

class MqttWorker(QThread):
    data_received = pyqtSignal(dict)
    connection_status = pyqtSignal(bool)

    def run(self):
        self.client = mqtt.Client()
        self.client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_forever()
        except Exception as e:
            print(f"MQTT Error: {e}")
            self.connection_status.emit(False)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connection_status.emit(True)
            self.client.subscribe(MQTT_TOPIC)
        else:
            self.connection_status.emit(False)

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            self.data_received.emit(payload)
        except Exception as e:
            print(f"Payload Error: {e}")

class WeatherWorker(QThread):
    weather_updated = pyqtSignal(str, str)

    def run(self):
        url = f"http://api.openweathermap.org/data/2.5/forecast?lat={OWM_LAT}&lon={OWM_LON}&appid={OWM_API_KEY}&units=metric"
        try:
            res = requests.get(url, timeout=10)
            data = res.json()
            forecast = data['list'][0]
            pop = forecast.get('pop', 0) * 100
            desc = forecast['weather'][0]['description'].capitalize()
            self.weather_updated.emit(f"{pop:.1f}%", desc)
        except Exception as e:
            self.weather_updated.emit("--%", "Error/Offline")

class GeminiWorker(QThread):
    result_ready = pyqtSignal(str)

    # Menambahkan parameter crop_name ke inisialisasi Worker
    def __init__(self, n, p, k, crop_name):
        super().__init__()
        self.n, self.p, self.k = n, p, k
        self.crop_name = crop_name

    def run(self):
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            # Prompt dimodifikasi untuk menggunakan nama tanaman secara dinamis
            prompt = (f"Anda adalah pakar agronomi. Data sensor IoT mendeteksi kondisi tanah saat ini: "
                      f"Nitrogen (N): {self.n} mg/kg, Fosfor (P): {self.p} mg/kg, Kalium (K): {self.k} mg/kg. "
                      f"Tanaman yang sedang dibudidayakan saat ini adalah {self.crop_name}. "
                      f"Berikan analisis teknis dan spesifik (maksimal 3 kalimat) apakah nutrisi ini cukup untuk "
                      f"tanaman tersebut, dan berikan rekomendasi pemupukannya.")
            
            response = model.generate_content(prompt)
            self.result_ready.emit(response.text.strip())
        except Exception as e:
            self.result_ready.emit(f"Gagal menganalisis AI: {e}")

# ==========================================
# KOMPONEN UI (View)
# ==========================================

class PumpIndicator(QLabel):
    def __init__(self):
        super().__init__()
        self.setObjectName("PumpIndicator")
        self.setAlignment(Qt.AlignCenter)
        
        # Setup Timer untuk Animasi
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.frame = 0
        self.is_running = False
        
        # State awal
        self.set_state(0)

    def set_state(self, state):
        if state == 1 and not self.is_running:
            self.is_running = True
            self.timer.start(500) # Ubah frame animasi setiap 500 milidetik
        elif state == 0 and self.is_running:
            self.is_running = False
            self.timer.stop()
            self.set_off_style()

        if state == 0:
            self.set_off_style()

    def set_off_style(self):
        self.setText("🛑 Status Pompa: OFF")
        self.setStyleSheet("""
            color: #f44336; 
            font-weight: bold; 
            padding: 12px; 
            border: 2px solid #f44336; 
            border-radius: 8px; 
            margin: 5px 15px;
            background-color: transparent;
        """)

    def animate(self):
        # Frame teks bergerak
        frames = ["💦 Pompa Aktif.  ", "💦 Pompa Aktif.. ", "💦 Pompa Aktif..."]
        # Frame warna berkedip (pulsing effect)
        colors = ["#2196f3", "#1976d2"] 
        
        self.setText(frames[self.frame % 3])
        current_color = colors[self.frame % 2]
        
        self.setStyleSheet(f"""
            color: white; 
            background-color: {current_color}; 
            font-weight: bold; 
            padding: 12px; 
            border-radius: 8px; 
            margin: 5px 15px;
            border: none;
        """)
        self.frame += 1

class SensorCard(QFrame):
    def __init__(self, title, unit, icon=""):
        super().__init__()
        self.setObjectName("SensorCard")
        self.unit = unit
        layout = QVBoxLayout(self)
        
        header_layout = QHBoxLayout()
        title_label = QLabel(f"{icon} {title}")
        title_label.setObjectName("CardTitle")
        
        self.time_label = QLabel("--:--:--")
        self.time_label.setObjectName("CardTime")
        self.time_label.setAlignment(Qt.AlignRight)
        
        header_layout.addWidget(title_label)
        header_layout.addWidget(self.time_label)
        
        self.value_label = QLabel("--")
        self.value_label.setObjectName("CardValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        
        layout.addLayout(header_layout)
        layout.addWidget(self.value_label)
        layout.addStretch()

    def update_value(self, value):
        self.value_label.setText(f"{value} {self.unit}")
        self.time_label.setText(datetime.now().strftime("%H:%M:%S"))

class AgrisenseDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agrisense - Smart Farming Dashboard")
        self.setWindowIcon(QIcon('logo_agrisense.png'))
        self.setGeometry(100, 100, 1200, 800)

        try:
            hwnd = int(self.winId())
            # Atribut untuk Windows 11 dan Windows 10 versi terbaru
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20 
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int(2))
            )
            # Atribut fallback untuk versi Windows 10 yang lebih lawas
            DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int(2))
            )
        except Exception:
            pass
        
        self.time_data = []
        self.temp_data = []
        self.soil_data = []
        self.max_points = 50 
        self.counter = 0

        self.current_rain_prob_val = 0.0 # Menyimpan nilai float cuaca
        self.RAIN_PROB_THRESHOLD = 65.0  # Batas threshold (bisa diubah)
        
        # --- MEMUAT MODEL ML ---
        try:
            self.rf_model = joblib.load('rf_irigasi_model_1500.pkl')
        except Exception as e:
            print(f"[!] Gagal memuat model ML: {e}")
            self.rf_model = None

        # --- VARIABEL STATE MACHINE & FAIL-SAFE IRIGASI ---
        self.SOIL_DRY_LIMIT = 30.0    # Batas bawah: Tanah dianggap kritis/kering
        self.SOIL_TARGET_LIMIT = 80.0 # Batas atas: Penyiraman dihentikan
        
        self.auto_pump_active = False # Status Pompa otomatis
        self.status_hari_ini = -1     # Tracker penggantian hari
        
        # Memori penyimpan prediksi 3 jam sebelumnya
        self.ml_pred_morning = None   
        self.ml_pred_afternoon = None
        
        # Flag agar eksekusi jadwal hanya ter-trigger 1 kali per sesi
        self.done_morning = False     
        self.done_afternoon = False
        
        self.init_csv()
        self.init_ui()
        self.start_workers()

    def init_csv(self):
        if not os.path.exists(CSV_FILENAME):
            with open(CSV_FILENAME, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                # Tambahkan kolom Komoditas dan Log_AI
                writer.writerow(["Timestamp", "Suhu", "Kelembaban", "SoilMoist", "N", "P", "K", "RSSI", "Komoditas", "Log_AI"])

    def add_log(self, message):
        """Menambahkan pesan ke dalam kotak Log Console UI"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_console.appendPlainText(f"[{timestamp}] {message}")

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(220) 
        sidebar.setMaximumWidth(350)
        sidebar_layout = QVBoxLayout(sidebar)
        
        logo = QLabel("🌱 AGRISENSE")
        logo.setObjectName("Logo")
        logo.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(logo)
        
        # --- FITUR BARU: Input Target Komoditas ---
        self.crop_label = QLabel("Komoditas Tanaman:")
        self.crop_label.setObjectName("CropLabel")
        sidebar_layout.addWidget(self.crop_label)
        
        self.crop_combo = QComboBox()
        self.crop_combo.setObjectName("CropCombo")
        # Set list dropdown bawaan
        self.crop_combo.addItems(["Jagung", "Padi", "Bawang Merah", "Cabai", "Tomat", "Kedelai"])
        # Mengizinkan pengguna mengetik tanaman selain yang ada di daftar
        self.crop_combo.setEditable(True) 
        sidebar_layout.addWidget(self.crop_combo)
        
        # Spacer ringan sebelum tombol
        sidebar_layout.addSpacing(20)
        
        # Sidebar Buttons
        self.btn_auto = QPushButton("🔄 Mode: OTOMATIS")
        self.btn_pump = QPushButton("💧 Pompa: OFF")
        self.btn_refresh = QPushButton("☁️ Refresh Cuaca")
        self.btn_ai = QPushButton("🤖 Analisis NPK (AI)")

        self.btn_auto.setStyleSheet("background-color: #2e7d32;")
        self.btn_pump.setStyleSheet("background-color: #2a2a3e;")
        self.btn_pump.setEnabled(False)
        
        self.btn_auto.clicked.connect(self.toggle_mode)
        self.btn_pump.clicked.connect(self.toggle_pump)
        self.btn_refresh.clicked.connect(self.refresh_weather)
        self.btn_ai.clicked.connect(self.analyze_ai)
        
        for btn in [self.btn_auto, self.btn_pump, self.btn_refresh, self.btn_ai]:
            btn.setObjectName("SidebarBtn")
            sidebar_layout.addWidget(btn)

        sidebar_layout.addSpacing(50)

        self.pump_indicator = PumpIndicator()
        sidebar_layout.addWidget(self.pump_indicator)
            
        sidebar_layout.addStretch()

        self.footer_label = QLabel("Agrisense v1.0.0\nCreated by: Bengkel IT 2026")
        self.footer_label.setObjectName("FooterLabel")
        self.footer_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(self.footer_label)

        # 2. Main Content Area
        content_wrapper = QWidget()
        content_layout = QVBoxLayout(content_wrapper)
        content_layout.setContentsMargins(20, 20, 20, 20)

        # Header
        header_layout = QHBoxLayout()
        header_title = QLabel("Real-time Telemetry")
        header_title.setObjectName("HeaderTitle")
        
        self.status_label = QLabel("🔴 Menghubungkan ke MQTT Cloud...")
        self.status_label.setStyleSheet("color: white;")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAlignment(Qt.AlignRight)
        
        header_layout.addWidget(header_title)
        header_layout.addWidget(self.status_label)
        content_layout.addLayout(header_layout)

        # Grid Sensor Cards
        grid_layout = QGridLayout()
        self.cards = {
            'temp': SensorCard("Suhu", "°C", "🌡️"),
            'hum': SensorCard("Kelembaban Udara", "%", "💨"),
            'soil': SensorCard("Kelembaban Tanah", "%", "🪴"),
            'weather': SensorCard("Prob. Hujan", "", "🌤️"),
            'n': SensorCard("Nitrogen (N)", "mg/kg", "🍀"),
            'p': SensorCard("Fosfor (P)", "mg/kg", "🌴"),
            'k': SensorCard("Kalium (K)", "mg/kg", "🌾"),
            'rssi': SensorCard("Sinyal LoRa", "dBm", "📡")
        }
        
        positions = [(i, j) for i in range(2) for j in range(4)]
        for position, (key, card) in zip(positions, self.cards.items()):
            grid_layout.addWidget(card, *position)
            
        content_layout.addLayout(grid_layout)

        # Pyqtgraph Area
        self.graph = pg.PlotWidget()
        self.graph.setBackground('#1e1e2e')
        self.graph.setTitle("Tren Suhu & Kelembaban Tanah", color="#ffffff", size="12pt")
        self.graph.showGrid(x=True, y=True, alpha=0.3)
        self.graph.addLegend()
        
        self.temp_line = self.graph.plot(pen=pg.mkPen('#ff5252', width=2), name="Suhu (°C)")
        self.soil_line = self.graph.plot(pen=pg.mkPen('#448aff', width=2), name="Soil Moist (%)")
        
        content_layout.addWidget(self.graph)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setObjectName("LogConsole")
        self.log_console.setMaximumHeight(200) # Dibatasi agar tidak memakan layar grafik
        content_layout.addWidget(self.log_console)

        main_layout.addWidget(sidebar, 1)
        main_layout.addWidget(content_wrapper, 4)

        self.apply_stylesheet()

    def start_workers(self):
        self.mqtt_thread = MqttWorker()
        self.mqtt_thread.connection_status.connect(self.update_mqtt_status)
        self.mqtt_thread.data_received.connect(self.update_telemetry)
        self.mqtt_thread.start()
        
        self.weather_thread = WeatherWorker()
        self.weather_thread.weather_updated.connect(self.update_weather_ui)
        self.refresh_weather()

    def update_mqtt_status(self, connected):
        if connected:
            self.status_label.setText("🟢 MQTT Cloud Terhubung")
            self.status_label.setStyleSheet("color: #4caf50;")
            self.add_log("Sistem terhubung ke MQTT Cloud Serverless.")
        else:
            self.status_label.setText("🔴 MQTT Cloud Terputus")
            self.status_label.setStyleSheet("color: #f44336;")
            self.add_log("Koneksi MQTT terputus. Mencoba menghubungkan kembali...")

    def update_telemetry(self, data):
        t = data.get('temperature', 0)
        h = data.get('humidity', 0)
        s = data.get('soil_moist', 0)
        n = data.get('nitrogen', 0)
        p = data.get('phosphorus', 0)
        k = data.get('potassium', 0)
        rssi = data.get('rssi', 0)

        pump_state = data.get('pump_state', 0) 
        self.pump_indicator.set_state(pump_state)

        self.add_log(f"[DATA DITERIMA LORA] T={t}°C | H={h}% | S={s}% | NPK=({n},{p},{k}) | RSSI={rssi} dBm | PUMP={pump_state}")

        # --- Simpan semua state sensor terakhir untuk dipakai oleh AI ---
        self.last_t, self.last_h, self.last_s = t, h, s
        self.last_n, self.last_p, self.last_k = n, p, k
        self.last_rssi = rssi

        # --- Update UI Cards ---
        self.cards['temp'].update_value(t)
        self.cards['hum'].update_value(h)
        self.cards['soil'].update_value(s)
        self.cards['n'].update_value(n)
        self.cards['p'].update_value(p)
        self.cards['k'].update_value(k)
        self.cards['rssi'].update_value(rssi)

        # --- Penulisan CSV Telemetri Reguler ---
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CSV_FILENAME, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            # Tambahkan tanda "-" untuk kolom Komoditas dan Log_AI
            writer.writerow([timestamp, t, h, s, n, p, k, rssi, " ", " "])

        # --- Update Grafik ---
        self.counter += 1
        self.time_data.append(self.counter)
        self.temp_data.append(t)
        self.soil_data.append(s)

        if len(self.time_data) > self.max_points:
            self.time_data = self.time_data[-self.max_points:]
            self.temp_data = self.temp_data[-self.max_points:]
            self.soil_data = self.soil_data[-self.max_points:]

        self.temp_line.setData(self.time_data, self.temp_data)
        self.soil_line.setData(self.time_data, self.soil_data)

        # ==========================================
        # LOGIKA INFERENSI ML & FAIL-SAFE PENJADWALAN
        # ==========================================
        if self.rf_model is not None and "OTOMATIS" in self.btn_auto.text():
            try:
                now = datetime.now()
                jam = now.hour
                hari = now.day

                # 1. Reset State Harian (Tepat pada saat pergantian hari)
                if getattr(self, 'status_hari_ini', -1) != hari:
                    self.ml_pred_morning = None
                    self.ml_pred_afternoon = None
                    self.done_morning = False
                    self.done_afternoon = False
                    self.auto_pump_active = False
                    self.status_hari_ini = hari

                # Format input array 2D untuk Model Random Forest
                hujan_biner = 1 if self.current_rain_prob_val >= self.RAIN_PROB_THRESHOLD else 0
                fitur_input = pd.DataFrame([[t, h, s, hujan_biner]], columns=['Suhu', 'Kelembaban_Udara', 'Kelembaban_Tanah', 'Prediksi_Hujan'])
                
                # 2. FASE PRE-CHECK: 3 Jam sebelum jadwal (07:00 dan 13:00)
                if jam == 7 and self.ml_pred_morning is None:
                    self.ml_pred_morning = self.rf_model.predict(fitur_input)[0]
                    kep_teks = "SIRAM" if self.ml_pred_morning == 1 else "TUNDA"
                    self.add_log(f"[Pre-check 07:00] ML: {kep_teks} (Cuaca: {self.current_rain_prob_val}%)")

                if jam == 13 and self.ml_pred_afternoon is None:
                    self.ml_pred_afternoon = self.rf_model.predict(fitur_input)[0]
                    kep_teks = "SIRAM" if self.ml_pred_afternoon == 1 else "TUNDA"
                    self.add_log(f"[Pre-check 13:00] ML: {kep_teks} (Cuaca: {self.current_rain_prob_val}%)")

                # 3. FASE PENGHENTIAN: Memotong pompa jika target tercapai
                if getattr(self, 'auto_pump_active', False):
                    if s >= self.SOIL_TARGET_LIMIT:
                        self.auto_pump_active = False
                        self.btn_pump.setText("💧 Pompa: OFF (Auto)")
                        self.btn_pump.setStyleSheet("background-color: #424242;")
                        self.add_log(f"[Auto Stop] Kelembapan mencapai {s}%. POMPA DIMATIKAN.")
                        self.mqtt_thread.client.publish("agrisense/command/pump", "0")

                # 4. FASE EKSEKUSI JADWAL (Pukul 10:00 Pagi)
                if jam == 10 and not self.done_morning:
                    # Jika alat baru dihidupkan lewat jam 7 (prediksi kosong), jalankan ML secara instan
                    pred_pagi = self.ml_pred_morning if self.ml_pred_morning is not None else self.rf_model.predict(fitur_input)[0]
                    
                    if pred_pagi == 0 and s < self.SOIL_DRY_LIMIT:
                        self.add_log(f"[Koreksi Pagi] ML TUNDA, tapi aktual kering ({s}%). FAIL-SAFE: POMPA NYALA.")
                        self.auto_pump_active = True
                    elif pred_pagi == 1 and s < self.SOIL_TARGET_LIMIT:
                        self.add_log(f"[Jadwal Pagi] Eksekusi SIRAM sesuai prediksi ML. POMPA NYALA.")
                        self.auto_pump_active = True
                    else:
                        self.add_log(f"[Jadwal Pagi] Kelembapan aman ({s}%). Skip penyiraman.")
                    
                    self.done_morning = True # Kunci agar tidak trigger berulang kali selama jam 10
                    
                    if getattr(self, 'auto_pump_active', False):
                        self.btn_pump.setText("💧 Pompa: ON (Auto)")
                        self.btn_pump.setStyleSheet("background-color: #2196f3;")
                        self.mqtt_thread.client.publish("agrisense/command/pump", "1")

                # 5. FASE EKSEKUSI JADWAL (Pukul 16:00 Sore)
                if jam == 16 and not self.done_afternoon:
                    pred_sore = self.ml_pred_afternoon if self.ml_pred_afternoon is not None else self.rf_model.predict(fitur_input)[0]
                    
                    if pred_sore == 0 and s < self.SOIL_DRY_LIMIT:
                        self.add_log(f"[Koreksi Sore] ML TUNDA, tapi aktual kering ({s}%). FAIL-SAFE: POMPA NYALA.")
                        self.auto_pump_active = True
                    elif pred_sore == 1 and s < self.SOIL_TARGET_LIMIT:
                        self.add_log(f"[Jadwal Sore] Eksekusi SIRAM sesuai prediksi ML. POMPA NYALA.")
                        self.auto_pump_active = True
                    else:
                        self.add_log(f"[Jadwal Sore] Kelembapan aman ({s}%). Skip penyiraman.")
                    
                    self.done_afternoon = True
                    
                    if getattr(self, 'auto_pump_active', False):
                        self.btn_pump.setText("💧 Pompa: ON (Auto)")
                        self.btn_pump.setStyleSheet("background-color: #2196f3;")
                        self.mqtt_thread.client.publish("agrisense/command/pump", "1")

            except Exception as e:
                self.add_log(f"[!] Error Logika Auto: {e}")

    def update_weather_ui(self, pop, desc):
        self.cards['weather'].update_value(f"{pop} ({desc})")
        try:
            angka_pop = pop.replace('%', '')
            self.current_rain_prob_val = float(angka_pop)
        except ValueError:
            self.current_rain_prob_val = 0.0

    def toggle_mode(self):
        text = self.btn_auto.text()
        if "OTOMATIS" in text:
            self.btn_auto.setText("🛠️ Mode: MANUAL")
            self.btn_auto.setStyleSheet("background-color: #ff9800;")
        
            self.btn_pump.setEnabled(True)
        else:
            self.btn_auto.setText("🔄 Mode: OTOMATIS")
            self.btn_auto.setStyleSheet("background-color: #2e7d32;")
            self.btn_pump.setStyleSheet("background-color: #2a2a3e;")

            self.btn_pump.setEnabled(False)

    def toggle_pump(self):
        text = self.btn_pump.text()
        if "OFF" in text:
            self.btn_pump.setText("💧 Pompa: ON")
            self.btn_pump.setStyleSheet("background-color: #2196f3;")
            self.add_log("Perintah manual: POMPA DIAKTIFKAN.")
            self.mqtt_thread.client.publish("agrisense/command/pump", "1")
        else:
            self.btn_pump.setText("💧 Pompa: OFF")
            self.btn_pump.setStyleSheet("background-color: #424242;")
            self.add_log("Perintah manual: POMPA DIMATIKAN.")
            self.mqtt_thread.client.publish("agrisense/command/pump", "0")

    def refresh_weather(self):
        self.cards['weather'].update_value("Memuat...")
        self.weather_thread.start()

    def analyze_ai(self):
        if not hasattr(self, 'last_n'):
            QMessageBox.warning(self, "Tunggu", "Belum ada data NPK masuk dari sensor!")
            return
            
        # Ambil nilai teks yang sedang aktif/ditulis di Dropdown
        current_crop = self.crop_combo.currentText()
        if not current_crop.strip():
            QMessageBox.warning(self, "Tunggu", "Harap pilih atau ketik nama komoditas tanaman terlebih dahulu!")
            return

        self.btn_ai.setText("🤖 Menganalisis...")
        self.btn_ai.setEnabled(False)
        
        # Kirim data NPK beserta Nama Komoditas ke Worker
        self.gemini_thread = GeminiWorker(self.last_n, self.last_p, self.last_k, current_crop)
        self.gemini_thread.result_ready.connect(self.show_ai_result)
        self.gemini_thread.start()

    def show_ai_result(self, result):
        self.btn_ai.setText("🤖 Analisis NPK (AI)")
        self.btn_ai.setEnabled(True)
        
        current_crop = self.crop_combo.currentText()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # --- PROSES LOGGING KE CSV UTAMA ---
        try:
            with open(CSV_FILENAME, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                # Tulis baris baru berisi data sensor terakhir + Komoditas + Hasil AI
                writer.writerow([
                    timestamp, 
                    self.last_t, self.last_h, self.last_s, 
                    self.last_n, self.last_p, self.last_k, 
                    self.last_rssi, 
                    current_crop, 
                    result
                ])
            print("[*] Log Analisis AI berhasil disatukan ke CSV Utama.")
            self.add_log(f"Analisis AI {current_crop} Selesai dan disimpan ke CSV.")
        except Exception as e:
            print(f"[!] Gagal menyimpan log AI: {e}")
            
        # Menampilkan hasil di UI
        QMessageBox.information(self, f"Rekomendasi Gemini AI - {current_crop}", result)

    def apply_stylesheet(self):
        qss = """
        QMainWindow {
            background-color: #12121e;
        }
        #Sidebar {
            background-color: #1a1a27;
            border-right: 1px solid #2a2a3e;
        }
        #Logo {
            color: #4caf50;
            font-size: 24px;
            font-weight: bold;
            padding: 20px 0;
        }
        #CropLabel {
            color: #a0a0b0;
            font-size: 13px;
            font-weight: bold;
            margin-left: 15px;
            margin-top: 10px;
        }
        #CropCombo {
            background-color: #2a2a3e;
            color: white;
            border: 1px solid #3f3f5a;
            border-radius: 6px;
            padding: 8px;
            margin: 5px 15px 10px 15px;
            font-size: 14px;
        }
        #CropCombo:drop-down {
            border: none;
        }
        #SidebarBtn {
            background-color: #2a2a3e;
            color: white;
            font-weight: bold;
            padding: 15px;
            border: none;
            border-radius: 8px;
            margin: 5px 15px;
            text-align: left;
        }
        #SidebarBtn:hover { background-color: #3f3f5a; }
        
        #HeaderTitle {
            color: white;
            font-size: 24px;
            font-weight: bold;
        }
        #StatusLabel {
            font-size: 14px;
            font-weight: bold;
        }
        #SensorCard {
            background-color: #1a1a27;
            border-radius: 12px;
            border: 1px solid #2a2a3e;
            padding: 15px;
        }
        #CardTitle {
            color: #a0a0b0;
            font-size: 14px;
            font-weight: bold;
        }
        #CardTime {
            color: #606070;
            font-size: 12px;
        }
        #CardValue {
            color: #ffffff;
            font-size: 32px;
            font-weight: bold;
            margin-top: 10px;
        }
        #FooterLabel {
            color: #606070; /* Warna teks abu-abu redup */
            font-size: 11px;
            margin-bottom: 10px; /* Jarak dari batas bawah layar */
        }
        #LogConsole {
            background-color: #12121e;
            color: #4caf50; /* Warna hijau khas terminal */
            border: 1px solid #2a2a3e;
            border-radius: 8px;
            padding: 10px;
            font-family: Consolas, monospace;
            font-size: 13px;
        }
        QMessageBox {
            background-color: #1a1a27;
            color: white;
        }
        QMessageBox QLabel {
            color: white;
            font-size: 14px;
        }
        QMessageBox QPushButton {
            background-color: #4caf50;
            color: white;
            padding: 5px 15px;
            border-radius: 5px;
        }
        """
        self.setStyleSheet(qss)

if __name__ == '__main__':
    try:
        # Buat string ID unik bebas (jangan pakai spasi)
        myappid = 'agrisense.smartfarming.dashboard.v1' 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)
    
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    app.setWindowIcon(QIcon('logo_agrisense.png'))
    
    window = AgrisenseDashboard()
    window.show()
    sys.exit(app.exec_())