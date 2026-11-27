/*
  RoadPulse water-depth sensor: ESP32 + JSN-SR04T (waterproof ultrasonic), mounted pointing DOWN at the road.
  depth = (distance to dry road, measured once at install) - (current distance to water surface)

  Wiring (JSN-SR04T): VCC 5V, GND, TRIG -> GPIO 5, ECHO -> GPIO 18 (use a 5V->3.3V divider on ECHO).
  Board: "ESP32 Dev Module" in Arduino IDE. No extra libraries needed (WiFi, HTTPClient are built in).
*/
#include <WiFi.h>
#include <HTTPClient.h>

// ---- change these ----
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* API_URL   = "http://192.168.1.10:8000/api/sensors/readings"; // your laptop/server IP; use https in production
const char* DEVICE_KEY = "dev-sensor-key";   // must be in DEVICE_KEYS on the server
const char* SENSOR_ID  = "sensor-01";        // must match a hotspot in backend/app/config.py
float DRY_DISTANCE_CM  = 100.0;              // measured distance to the dry road surface at install time
const unsigned long INTERVAL_MS = 2000;      // demo: every 2 s; real deployment: 30 s
// ----------------------

const int TRIG = 5, ECHO = 18;
const int BUF = 20;            // readings kept while offline, sent when the network returns
float pending[BUF]; int npending = 0;

float readDistanceCm() {
  // median of 5 pings filters splashes and passing vehicles
  float v[5];
  for (int i = 0; i < 5; i++) {
    digitalWrite(TRIG, LOW); delayMicroseconds(2);
    digitalWrite(TRIG, HIGH); delayMicroseconds(20);
    digitalWrite(TRIG, LOW);
    long us = pulseIn(ECHO, HIGH, 30000);
    v[i] = us > 0 ? us / 58.0 : -1;
    delay(60);
  }
  for (int i = 0; i < 5; i++) for (int j = i + 1; j < 5; j++) if (v[j] < v[i]) { float t = v[i]; v[i] = v[j]; v[j] = t; }
  return v[2];
}

bool sendDepth(float depth) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.begin(API_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  String body = String("{\"sensor_id\":\"") + SENSOR_ID + "\",\"depth_cm\":" + String(depth, 1) + "}";
  int code = http.POST(body);
  http.end();
  Serial.printf("depth %.1f cm -> HTTP %d\n", depth, code);
  return code >= 200 && code < 300;
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG, OUTPUT); pinMode(ECHO, INPUT);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " not connected (will retry)");
}

void loop() {
  float d = readDistanceCm();
  if (d > 0) {
    float depth = DRY_DISTANCE_CM - d;
    if (depth < 0) depth = 0;
    if (depth > 300) depth = 300;
    // flush anything buffered while offline, then the new reading
    while (npending > 0 && sendDepth(pending[0])) {
      for (int i = 1; i < npending; i++) pending[i - 1] = pending[i];
      npending--;
    }
    if (!sendDepth(depth)) {
      if (npending < BUF) pending[npending++] = depth;
      if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    }
  }
  delay(INTERVAL_MS);
}
