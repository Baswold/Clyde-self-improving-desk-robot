// blink_and_echo — Clyde's first-contact firmware.
//
// Blinks the on-board LED so Basil can see the board is alive, and
// echoes any line received on serial back with "ok: <line>" so Clyde
// can verify two-way comms.
//
// Override pin via the PIN_LED macro at compile time if your board
// uses something other than the standard LED_BUILTIN.

#ifndef PIN_LED
#define PIN_LED LED_BUILTIN
#endif

const unsigned long BLINK_MS = 500;
unsigned long last_blink = 0;
int led_state = LOW;

void setup() {
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  Serial.begin(115200);
  while (!Serial && millis() < 2000) { /* wait briefly for USB */ }
  Serial.println("clyde-firmware: blink_and_echo ready");
}

void loop() {
  unsigned long now = millis();
  if (now - last_blink >= BLINK_MS) {
    led_state = (led_state == LOW) ? HIGH : LOW;
    digitalWrite(PIN_LED, led_state);
    last_blink = now;
  }

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      Serial.print("ok: ");
      Serial.println(line);
    }
  }
}
