#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

// --- KONFIGURASI PIN & PARAMETER SENSOR ---
#define LED_PIN 25
#define ACTUATOR_PIN 14
#define BAT_PIN 35

float batVoltage = 0.0;
int batPercent = 0;

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_SDA 21
#define OLED_SCL 22

#define DHTPIN 13
#define DHTTYPE DHT22

#define SOIL_MOISTURE_PIN 34
const int AirValue = 3500;
const int WaterValue = 1500;

#define RX_PIN 4 
#define TX_PIN 2
#define MODBUS_BAUDRATE 4800

// --- KONFIGURASI PIN LORA (T3 LoRa32 V1.6.1) ---
#define LORA_SCK  5
#define LORA_MISO 19
#define LORA_MOSI 27
#define LORA_CS   18
#define LORA_RST  23
#define LORA_IRQ  26
#define LORA_FREQ 433E6 // Sesuaikan dengan modul: 433E6, 868E6, atau 915E6

// --- INISIALISASI OBJEK ---
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
DHT dht(DHTPIN, DHTTYPE);

// --- VARIABEL GLOBAL ---
const byte npk_query[] = {0x01, 0x03, 0x00, 0x1E, 0x00, 0x03, 0x65, 0xCD};
byte npk_response[11];

float temp = 0.0, hum = 0.0;
int soilMoisturePercent = 0;
int val_N = 0, val_P = 0, val_K = 0;

// Variabel untuk menyimpan status aktuator & RSSI
bool actuatorState = false; 
int currentRssi = 0; 

unsigned long previousMillis = 0;
const long interval = 3000;

void setup() {
  Serial.begin(115200);
  Serial2.begin(MODBUS_BAUDRATE, SERIAL_8N1, RX_PIN, TX_PIN);
  
  // Inisialisasi LED Bawaan & Aktuator
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  
  pinMode(ACTUATOR_PIN, OUTPUT);
  digitalWrite(ACTUATOR_PIN, HIGH);

  analogReadResolution(12);
  
  Serial.println("Inisialisasi Sistem Sensor...");
  dht.begin();
  
  // Inisialisasi OLED
  Wire.begin(OLED_SDA, OLED_SCL);
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println(F("OLED allocation failed"));
    for(;;);
  }
  
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);
  display.setCursor(15, 25);
  display.println("System Starting...");
  display.display();
  
  // Inisialisasi LoRa
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_CS);
  LoRa.setPins(LORA_CS, LORA_RST, LORA_IRQ);
  
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("LoRa initialization failed!");
    display.clearDisplay();
    display.setCursor(0, 0);
    display.println("LoRa Failed!");
    display.display();
    while (1);
  }
  Serial.println("LoRa Initialized OK!");
  
  delay(2000);
  
  // PENTING: Set LoRa ke mode receive secara default
  LoRa.receive(); 
}

void loop() {
  // --- 1. JADWAL PENGIRIMAN DATA SENSOR ---
  unsigned long currentMillis = millis();
  
  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    readDHT();
    readSoilMoisture();
    readNPK();
    readBattery();
    updateOLED();
    sendDataLoRa();
  }

  // --- 2. LOGIKA HALF-DUPLEX: MENERIMA PERINTAH ---
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    // Ambil nilai RSSI dari paket yang baru saja diterima
    currentRssi = LoRa.packetRssi();
    
    String incoming = "";
    while (LoRa.available()) {
      incoming += (char)LoRa.read();
    }
    
    Serial.println("Menerima pesan: " + incoming + " dengan RSSI: " + String(currentRssi));
    
    // Eksekusi kontrol aktuator & Update OLED secara realtime
    if (incoming == "CMD:ON") {
      digitalWrite(ACTUATOR_PIN, LOW);
      actuatorState = true;
      updateOLED(); 
      Serial.println(">>> Aktuator NYALA <<<");
    } 
    else if (incoming == "CMD:OFF") {
      digitalWrite(ACTUATOR_PIN, HIGH);
      actuatorState = false;
      updateOLED(); 
      Serial.println(">>> Aktuator MATI <<<");
    }
  }
}

// --- FUNGSI PEMBACAAN SENSOR ---

void readDHT() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  if (!isnan(h) && !isnan(t)) {
    hum = h;
    temp = t;
  }
}

void readSoilMoisture() {
  int rawValue = analogRead(SOIL_MOISTURE_PIN);
  soilMoisturePercent = map(rawValue, AirValue, WaterValue, 0, 100);
  soilMoisturePercent = constrain(soilMoisturePercent, 0, 100);
}

void readNPK() {
  while (Serial2.available()) Serial2.read();
  Serial2.write(npk_query, sizeof(npk_query));

  unsigned long startTime = millis();
  int bytesReceived = 0;

  while (millis() - startTime < 1000) {
    if (Serial2.available()) {
      npk_response[bytesReceived++] = Serial2.read();
      if (bytesReceived >= 11) break;
    }
  }

  if (bytesReceived == 11 && npk_response[0] == 0x01 && npk_response[1] == 0x03) {
    val_N = (npk_response[3] << 8) | npk_response[4];
    val_P = (npk_response[5] << 8) | npk_response[6];
    val_K = (npk_response[7] << 8) | npk_response[8];
    Serial.printf("NPK Berhasil: N=%d, P=%d, K=%d\n", val_N, val_P, val_K);
  } else {
    Serial.println("Peringatan: Gagal membaca NPK (Timeout / Invalid).");
  }
}

// --- FUNGSI PENGIRIMAN DATA LORA ---

void sendDataLoRa() {
  digitalWrite(LED_PIN, HIGH); 
  
  String payload = String(temp, 1) + "," + 
                   String(hum, 1) + "," + 
                   String(soilMoisturePercent) + "," + 
                   String(val_N) + "," + 
                   String(val_P) + "," + 
                   String(val_K) + "," + 
                   String(actuatorState);

  // Mulai transmisi
  LoRa.beginPacket();
  LoRa.print(payload);
  LoRa.endPacket();

  Serial.println("Data LoRa Terkirim: " + payload);
  
  delay(100); 
  digitalWrite(LED_PIN, LOW); 
  
  // PENTING: Kembalikan modul ke mode mendengarkan (Rx) setelah mengirim
  LoRa.receive(); 
}

// --- FUNGSI TAMPILAN OLED ---

void updateOLED() {
  display.clearDisplay();
  
  display.setTextSize(1);
  display.setTextColor(WHITE);
  
  // Baris 1: Suhu & Kelembapan
  display.setCursor(0, 0);
  display.print("T:"); display.print(temp, 1); display.print("C  ");
  display.print("H:"); display.print(hum, 1); display.println("%");
  
  // Baris 2: Kelembapan Tanah
  display.setCursor(0, 12);
  display.print("Soil Moist : "); display.print(soilMoisturePercent); display.println("%");
  
  // Garis Pemisah horizontal
  display.drawLine(0, 22, 128, 22, WHITE);
  
  // Baris 3, 4, 5: Sensor NPK di sisi kiri
  display.setCursor(0, 27); display.print("N: "); display.print(val_N); display.println(" mg/kg");
  display.setCursor(0, 39); display.print("P: "); display.print(val_P); display.println(" mg/kg");
  display.setCursor(0, 51); display.print("K: "); display.print(val_K); display.println(" mg/kg");
  
  // --- Posisi Rata Kanan (Right-Aligned) ---
  int16_t x1, y1; 
  uint16_t w, h;

  // 1. Indikator BATERAI (Berada di sebelah N)
  String batTxt = "BAT:" + String(batPercent) + "%";
  display.getTextBounds(batTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 27); // y=27 sejajar dengan baris N
  display.print(batTxt);
  
  // 2. Indikator CMD (Berada di sebelah P)
  String cmdTxt = actuatorState ? "CMD:ON" : "CMD:OFF";
  display.getTextBounds(cmdTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 39); // 128 - w memastikan teks sejajar di kanan
  display.print(cmdTxt);
  
  // 2. Indikator RSSI (Berada di sebelah K, di bawah CMD)
  String rssiTxt = "RSSI:" + String(currentRssi);
  display.getTextBounds(rssiTxt, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(128 - w, 51); 
  display.print(rssiTxt);
  
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