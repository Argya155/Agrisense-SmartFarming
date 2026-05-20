#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <math.h> // Digunakan untuk fungsi absolut float fabs()

// --- KONFIGURASI LORA (T3 LoRa32 V1.6.1) ---
#define LORA_SCK  5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_CS   18
#define LORA_RST  23
#define LORA_IRQ  26
#define LORA_FREQ 433E6

// --- KONFIGURASI OLED & LED ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_SDA 21
#define OLED_SCL 22
#define LED_PIN 25 
#define BAT_PIN 35

// --- KONFIGURASI WIFI & MQTT CLOUD ---
const char* ssid          = "GYAA";
const char* password      = "11111111";
const char* mqtt_server   = "90092c1d11c04f08bcf36f39c8f122cd.s1.eu.hivemq.cloud"; 
const int   mqtt_port     = 8883; 
const char* mqtt_user     = "admin_agrisense"; 
const char* mqtt_pass     = "Agrisense2026"; 
const char* mqtt_topic    = "agrisense/telemetry/gateway"; 
const char* mqtt_sub_topic= "agrisense/command/pump"; // TAMBAHAN: Topik Subscribe

// --- DEADBAND THRESHOLDS ---
const float THRESH_TEMP   = 1.0;  
const float THRESH_HUM    = 5.0;  
const float THRESH_SOIL   = 5.0;  
const int   THRESH_NPK    = 5;  

int pump_state = 0;

float batVoltage = 0.0;
int batPercent = 0;

// --- VARIABEL PENYIMPANAN STATE SEBELUMNYA ---
float prev_temp = -999.0, prev_hum = -999.0, prev_soilMoist = -999.0;
int prev_val_N = -999, prev_val_P = -999, prev_val_K = -999;
int prev_pump_state = -1;

// --- MANAJEMEN WAKTU ---
unsigned long lastReconnectAttempt = 0;
const unsigned long RECONNECT_INTERVAL = 5000; 

unsigned long lastPublishTime = 0;
const unsigned long FORCE_PUBLISH_INTERVAL = 3600000; 

// Objek Global
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
WiFiClientSecure espClient; 
PubSubClient mqttClient(espClient);

// Variabel Sensor String (Untuk OLED)
String temp = "--", hum = "--", soilMoist = "--";
String val_N = "--", val_P = "--", val_K = "--";
int currentRssi = 0;

// --- DEKLARASI FUNGSI ---
void setupWiFi();
void mqttCallback(char* topic, byte* payload, unsigned int length);
bool parseData(String data);
void evaluateAndPublish();
void updateOLED();
void displayStatus(const char* message);

void setup() {
  Serial.begin(115200);
  while (!Serial);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  analogReadResolution(12);

  Wire.begin(OLED_SDA, OLED_SCL);
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("OLED allocation failed"));
    for(;;);
  }
  displayStatus("Booting...");

  setupWiFi();
  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setCallback(mqttCallback); // TAMBAHAN: Daftarkan fungsi callback pembaca pesan MQTT

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_IRQ);

  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("LoRa init failed!");
    displayStatus("LoRa Failed!");
    while (1);
  }
  
  Serial.println("System Ready!");
  displayStatus("Waiting Data...");
  
  // PENTING: Set mode receive sejak awal
  LoRa.receive();
}

void loop() {
  // 1. Handle Koneksi MQTT secara Non-Blocking
  if (WiFi.status() == WL_CONNECTED) {
    if (!mqttClient.connected()) {
      unsigned long now = millis();
      if (now - lastReconnectAttempt > RECONNECT_INTERVAL) {
        lastReconnectAttempt = now;
        Serial.println("Attempting MQTT connection...");
        String clientId = "ESP32Gateway-" + String(WiFi.macAddress());
        
        if (mqttClient.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
          Serial.println("MQTT Cloud Connected Securely");
          
          // TAMBAHAN: Subscribe ke topik aktuator setelah terkoneksi
          mqttClient.subscribe(mqtt_sub_topic);
          Serial.println("Subscribed to: " + String(mqtt_sub_topic));
          
          lastReconnectAttempt = 0;
          lastPublishTime = 0; 
        }
      }
    } else {
      mqttClient.loop(); // Wajib dipanggil terus agar bisa membaca pesan masuk
    }
  }

  // 2. Cek Paket LoRa yang Masuk
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    digitalWrite(LED_PIN, HIGH);
    currentRssi = LoRa.packetRssi();
    
    String incomingData = "";
    while (LoRa.available()) {
      incomingData += (char)LoRa.read();
    }
    
    Serial.printf("\n[LORA] RX: %s | RSSI: %d\n", incomingData.c_str(), currentRssi);

    if (parseData(incomingData)) {
      readBattery();
      updateOLED(); 
      evaluateAndPublish(); 
    }
    
    delay(50);
    digitalWrite(LED_PIN, LOW);
  }
}

// --- TAMBAHAN: FUNGSI CALLBACK MQTT (KIRIM PERINTAH LORA) ---
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  
  Serial.println("\n[MQTT] Pesan masuk di topik: " + String(topic));
  Serial.println("[MQTT] Pesan: " + message);

  String loraCmd = "";
  message.toUpperCase(); // Antisipasi case-sensitive ("on", "ON", "On")

  // Logika pembacaan perintah (mendukung teks ON/OFF atau angka 1/0)
  if (message == "ON" || message == "1") {
    loraCmd = "CMD:ON";
  } else if (message == "OFF" || message == "0") {
    loraCmd = "CMD:OFF";
  }

  if (loraCmd != "") {
    digitalWrite(LED_PIN, HIGH); // Indikator sedang transmit
    Serial.println("[LORA] Meneruskan perintah ke Node 1: " + loraCmd);
    
    // Alihkan ke mode TX dan kirim perintah
    LoRa.beginPacket();
    LoRa.print(loraCmd);
    LoRa.endPacket();
    
    // PENTING: Segera kembalikan modul ke mode RX (mendengarkan sensor)
    LoRa.receive();
    
    delay(50);
    digitalWrite(LED_PIN, LOW);
  } else {
    Serial.println("[LORA] Pesan diabaikan. Perintah tidak dikenali.");
  }
}

// --- FUNGSI SETUP WIFI ---
void setupWiFi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  
  displayStatus("Connecting WiFi");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected. IP: ");
    Serial.println(WiFi.localIP());
    espClient.setInsecure(); 
  } else {
    Serial.println("\nWiFi failed, continuing offline mode.");
  }
}

// --- FUNGSI PARSING DATA ---
bool parseData(String data) {
  int index[6]; 
  
  index[0] = data.indexOf(',');
  index[1] = data.indexOf(',', index[0] + 1);
  index[2] = data.indexOf(',', index[1] + 1);
  index[3] = data.indexOf(',', index[2] + 1);
  index[4] = data.indexOf(',', index[3] + 1);
  index[5] = data.indexOf(',', index[4] + 1);

  if (index[0] > 0 && index[5] > 0) {
    temp       = data.substring(0, index[0]);
    hum        = data.substring(index[0] + 1, index[1]);
    soilMoist  = data.substring(index[1] + 1, index[2]);
    val_N      = data.substring(index[2] + 1, index[3]);
    val_P      = data.substring(index[3] + 1, index[4]);
    val_K      = data.substring(index[4] + 1, index[5]);
    pump_state = data.substring(index[5] + 1).toInt();
    return true;
  } else {
    Serial.println("Error: Corrupted Data.");
    return false;
  }
}

// --- FUNGSI EVALUASI DEADBAND & PUBLISH ---
void evaluateAndPublish() {
  if (!mqttClient.connected()) return;

  float cur_t = temp.toFloat();
  float cur_h = hum.toFloat();
  float cur_s = soilMoist.toFloat();
  int cur_n = val_N.toInt();
  int cur_p = val_P.toInt();
  int cur_k = val_K.toInt();

  bool isSignificant = false;
  String triggerReason = "";

  if (fabs(cur_t - prev_temp) >= THRESH_TEMP)      { isSignificant = true; triggerReason += "Temp "; }
  if (fabs(cur_h - prev_hum) >= THRESH_HUM)        { isSignificant = true; triggerReason += "Hum "; }
  if (fabs(cur_s - prev_soilMoist) >= THRESH_SOIL) { isSignificant = true; triggerReason += "Soil "; }
  if (abs(cur_n - prev_val_N) >= THRESH_NPK)       { isSignificant = true; triggerReason += "N "; }
  if (abs(cur_p - prev_val_P) >= THRESH_NPK)       { isSignificant = true; triggerReason += "P "; }
  if (abs(cur_k - prev_val_K) >= THRESH_NPK)       { isSignificant = true; triggerReason += "K "; }
  if (pump_state != prev_pump_state)               { isSignificant = true; triggerReason += "PumpState "; }

  if (millis() - lastPublishTime >= FORCE_PUBLISH_INTERVAL) {
    isSignificant = true;
    triggerReason = "Heartbeat Interval";
  }

  if (isSignificant) {
    Serial.println("[MQTT] Memulai Publish. Alasan: " + triggerReason);
    
    StaticJsonDocument<200> doc;
    doc["temperature"] = cur_t;
    doc["humidity"]    = cur_h;
    doc["soil_moist"]  = cur_s;
    doc["nitrogen"]    = cur_n;
    doc["phosphorus"]  = cur_p;
    doc["potassium"]   = cur_k;
    doc["pump_state"]  = pump_state;
    doc["rssi"]        = currentRssi;

    char jsonBuffer[256];
    serializeJson(doc, jsonBuffer);

    if (mqttClient.publish(mqtt_topic, jsonBuffer)) {
      Serial.println("[MQTT] Publish Sukses: " + String(jsonBuffer));
      
      prev_temp = cur_t;
      prev_hum = cur_h;
      prev_soilMoist = cur_s;
      prev_val_N = cur_n;
      prev_val_P = cur_p;
      prev_val_K = cur_k;
      prev_pump_state = pump_state;
      lastPublishTime = millis();
    } else {
      Serial.println("[MQTT] Publish Gagal");
    }
  } else {
    Serial.println("[MQTT] Publish Diabaikan (Perubahan berada di bawah threshold)");
  }
}

// --- FUNGSI TAMPILAN OLED UTAMA ---
void updateOLED() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);
  
  display.setCursor(0, 0);
  display.printf("T:%sC  H:%s%%\n", temp.c_str(), hum.c_str());
  
  display.setCursor(0, 12);
  display.printf("Soil Moist: %s%%\n", soilMoist.c_str());
  
  display.drawLine(0, 22, 128, 22, WHITE);
  
  display.setCursor(0, 27); display.printf("N: %s mg/kg\n", val_N.c_str());
  display.setCursor(0, 39); display.printf("P: %s mg/kg\n", val_P.c_str());
  display.setCursor(0, 51); display.printf("K: %s mg/kg\n", val_K.c_str());
  
  int16_t x1, y1; 
  uint16_t w, h;

  String batTxt = "BAT:" + String(batPercent) + "%";
  display.getTextBounds(batTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 27); // y=27 sejajar dengan baris N
  display.print(batTxt);

  String statusTxt = (mqttClient.connected()) ? "WIFI:ON" : "WIFI:OFF";
  display.getTextBounds(statusTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 39); 
  display.print(statusTxt);
  
  String rssiTxt = "RSSI:" + String(currentRssi);
  display.getTextBounds(rssiTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 51); 
  display.print(rssiTxt);
  
  display.display();
}

// --- FUNGSI UTILITAS OLED ---
void displayStatus(const char* message) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);
  display.setCursor(0, 25);
  display.println(message);
  display.display();
}

// --- FUNGSI PEMBACAAN BATERAI (DENGAN OVERSAMPLING) ---
void readBattery() {
  uint32_t sumAdc = 0;
  const int numSamples = 64; // Jumlah sampel oversampling
  
  // Mengambil banyak sampel secara berurutan
  for (int i = 0; i < numSamples; i++) {
    sumAdc += analogRead(BAT_PIN);
    delay(2); // Jeda sangat singkat (2ms) antar sampel agar pembacaan lebih stabil
  }
  
  // Hitung nilai rata-rata ADC
  float avgAdc = (float)sumAdc / numSamples;
  
  // Kalkulasi tegangan menggunakan nilai rata-rata
  // (Nilai Rata-rata ADC / Resolusi Maksimal 12-bit) * Tegangan Referensi * Faktor Pengali Pembagi Tegangan [cite: 335, 337]
  batVoltage = (avgAdc / 4095.0) * 3.3 * 2.0;

  // Kalkulasi persentase (Asumsi LiPo: 3.2V = 0%, 4.2V = 100%) 
  batPercent = ((batVoltage - 3.2) / (4.2 - 3.2)) * 100;
  
  // Batasi nilai persentase agar tidak kurang dari 0 atau lebih dari 100
  batPercent = constrain(batPercent, 0, 100);
  
  Serial.printf("[BATERAI] ADC Rata-rata: %.1f | Tegangan: %.2fV | Kapasitas: %d%%\n", avgAdc, batVoltage, batPercent);
}