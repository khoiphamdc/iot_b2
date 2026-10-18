#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <math.h>
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";
const char* MQTT_SERVER = "thingsboard.cloud";
const int MQTT_PORT = 1883;
const char* TOKEN = ""; // dán access token của thiết bị ThingsBoard, KHÔNG commit token thật
const int DHT_PIN = 15;
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;
const int LED_PIN = 2;
const unsigned long SEND_INTERVAL = 5000;
const unsigned long RETRY_INTERVAL = 3000;
WiFiClient wifiClient;
PubSubClient mqttClient(wifiClient);
DHTesp dht;
unsigned long lastSend = 0;
unsigned long lastMqttAttempt = 0;
unsigned long sequenceNo = 0;
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.println("[WiFi] Dang ket noi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[WiFi] OK");
    Serial.print("[WiFi] IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("[WiFi] TIMEOUT");
  }
}
bool connectMQTT() {
  if (mqttClient.connected()) return true;
  Serial.print("[MQTT] Connecting to ");
  Serial.println(MQTT_SERVER);
  String clientId = "ESP32-IoT-" +
                    String((uint32_t)ESP.getEfuseMac(), HEX);
  bool connected = mqttClient.connect(
    clientId.c_str(),
    TOKEN,
    ""
  );
  if (connected) {
    Serial.println("[MQTT] CONNECTED");
    return true;
  }
  Serial.print("[MQTT] FAILED, state = ");
  Serial.println(mqttClient.state());
  return false;
}
float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return NAN;
  return duration * 0.0343f / 2.0f;
}
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println();
  Serial.println("===== ESP32 IOT START =====");
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
  dht.setup(DHT_PIN, DHTesp::DHT22);
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setBufferSize(256);
  mqttClient.setSocketTimeout(5);
  connectWiFi();
}
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    delay(100);
    return;
  }
  mqttClient.loop();
  if (!mqttClient.connected()) {
    if (millis() - lastMqttAttempt >= RETRY_INTERVAL) {
      lastMqttAttempt = millis();
      connectMQTT();
    }
    delay(10);
    return;
  }
  if (millis() - lastSend < SEND_INTERVAL) return;
  lastSend = millis();
  TempAndHumidity data = dht.getTempAndHumidity();
  float distance = readDistanceCm();
  Serial.println();
  Serial.println("===== SENSOR READINGS =====");
  bool validDht = isfinite(data.temperature) &&
                  isfinite(data.humidity);
  if (validDht) {
    Serial.printf("Temperature: %.2f C\n", data.temperature);
    Serial.printf("Humidity: %.2f %%\n", data.humidity);
  } else {
    Serial.println("[DHT22] READ ERROR");
  }
  if (isfinite(distance)) {
    Serial.printf("Distance: %.2f cm\n", distance);
  } else {
    Serial.println("[HC-SR04] READ ERROR");
  }
  if (!validDht && !isfinite(distance)) {
    Serial.println("[Sensor] Khong co du lieu hop le");
    return;
  }
  char payload[256];
  if (validDht && isfinite(distance)) {
    snprintf(
      payload, sizeof(payload),
      "{\"temperature\":%.2f,\"humidity\":%.2f,"
      "\"distance_cm\":%.2f,\"rssi\":%d,"
      "\"sequence\":%lu,\"uptime_s\":%lu}",
      data.temperature, data.humidity, distance,
      WiFi.RSSI(), ++sequenceNo, millis() / 1000UL
    );
  } else if (validDht) {
    snprintf(
      payload, sizeof(payload),
      "{\"temperature\":%.2f,\"humidity\":%.2f,"
      "\"rssi\":%d,\"sequence\":%lu,\"uptime_s\":%lu}",
      data.temperature, data.humidity,
      WiFi.RSSI(), ++sequenceNo, millis() / 1000UL
    );
  } else {
    snprintf(
      payload, sizeof(payload),
      "{\"distance_cm\":%.2f,\"rssi\":%d,"
      "\"sequence\":%lu,\"uptime_s\":%lu}",
      distance, WiFi.RSSI(), ++sequenceNo, millis() / 1000UL
    );
  }
  Serial.print("[MQTT] Payload: ");
  Serial.println(payload);
  bool ok = mqttClient.publish(
    "v1/devices/me/telemetry",
    payload
  );
  if (ok) {
    Serial.println("[MQTT] PUBLISH OK");
    digitalWrite(LED_PIN, HIGH);
    delay(100);
    digitalWrite(LED_PIN, LOW);
  } else {
    Serial.println("[MQTT] PUBLISH FAILED");
  }
}
