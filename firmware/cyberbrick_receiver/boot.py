# MOSS Forklift Receiver
# CyberBrick Core Board (ESP32-C3) - Advanced Programming Project
#
# Receives ESP-NOW control data from MOSS robot.
# Protocol: JSON {"m1":int,"m2":int,"fk":int,"ld":int}
#   m1/m2: motor speed -100..100
#   fk: fork speed -100..100 (0=stop, continuous rotation servo)
#   ld: LED effect 0=off 1=left 2=right 3=headlights 4=flash
#
# GPIO pins - verify against your CyberBrick receiver board:
#   MOTOR1 (left track):  IN1=GPIO4, IN2=GPIO5
#   MOTOR2 (right track): IN1=GPIO6, IN2=GPIO7
#   FORK servo:           GPIO0
#   LED2 (front light):   GPIO20 (WS2812 NeoPixel)

import network
import espnow
import machine
import time
import ujson

# ==================== Configuration ====================

WIFI_CHANNEL = 6        # Must match MOSS WiFi channel
FAILSAFE_MS = 500       # Stop all outputs if no data for this long

# Motor H-bridge pins (2 pins per motor: forward/reverse)
MOTOR1_PIN1 = 4         # Left track
MOTOR1_PIN2 = 5
MOTOR2_PIN1 = 6         # Right track
MOTOR2_PIN2 = 7
FORK_PIN = 0            # Fork continuous-rotation servo
LED_PIN = 20            # Front light (WS2812)
LED_COUNT = 2           # Left + Right front headlights

# ==================== WiFi + ESP-NOW ====================

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.disconnect()
wlan.config(reconnects=0)
time.sleep_ms(100)
wlan.config(channel=WIFI_CHANNEL)

e = espnow.ESPNow()
e.active(True)

# ==================== Motor (H-bridge, dual PWM) ====================

class Motor:
    def __init__(self, pin1, pin2, freq=20000):
        self.p1 = machine.PWM(machine.Pin(pin1), freq=freq, duty=0)
        self.p2 = machine.PWM(machine.Pin(pin2), freq=freq, duty=0)

    def set_speed(self, speed):
        speed = max(-100, min(100, int(speed)))
        duty = int(abs(speed) / 100 * 1023)
        if speed >= 0:
            self.p1.duty(duty)
            self.p2.duty(0)
        else:
            self.p1.duty(0)
            self.p2.duty(duty)

    def stop(self):
        self.p1.duty(0)
        self.p2.duty(0)

motor1 = Motor(MOTOR1_PIN1, MOTOR1_PIN2)
motor2 = Motor(MOTOR2_PIN1, MOTOR2_PIN2)

# ==================== Fork Servo (continuous rotation) ====================

fork_pwm = machine.PWM(machine.Pin(FORK_PIN), freq=50, duty=0)

def fork_set(speed):
    speed = max(-100, min(100, int(speed)))
    if speed == 0:
        fork_pwm.duty(0)
        return
    duty = int(75 + speed * 0.5)
    fork_pwm.duty(max(25, min(125, duty)))

# ==================== Front Light (NeoPixel) ====================

try:
    import neopixel
    np = neopixel.NeoPixel(machine.Pin(LED_PIN, machine.Pin.OUT), LED_COUNT)
    HAS_NEOPIXEL = True
except:
    HAS_NEOPIXEL = False
    led_pin = machine.Pin(LED_PIN, machine.Pin.OUT)

YELLOW = (0xd5, 0xb3, 0x00)
WHITE = (0xff, 0xff, 0xff)
OFF = (0, 0, 0)

led_fx = 0
led_timer = time.ticks_ms()
led_on = False

def led_set(fx):
    global led_fx, led_timer, led_on
    led_fx = fx
    led_on = False
    led_timer = time.ticks_ms()
    if HAS_NEOPIXEL:
        if fx == 0 or fx == 5:
            np[0] = OFF; np[1] = OFF; np.write()
        elif fx == 1:   # Left turn: left LED blinks yellow, right off
            np[0] = OFF; np[1] = OFF; np.write()
        elif fx == 2:   # Right turn: right LED blinks yellow, left off
            np[0] = OFF; np[1] = OFF; np.write()
        elif fx == 3:   # Headlights on: both white
            np[0] = WHITE; np[1] = WHITE; np.write()
        elif fx == 4:   # Both blink yellow
            np[0] = OFF; np[1] = OFF; np.write()
    else:
        led_pin.value(1 if fx == 3 else 0)

def led_update():
    global led_on, led_timer
    if led_fx in (1, 2, 4) and time.ticks_diff(time.ticks_ms(), led_timer) >= 250:
        led_timer = time.ticks_ms()
        led_on = not led_on
        if HAS_NEOPIXEL:
            if led_fx == 1:     # Left turn: only left LED blinks
                np[0] = YELLOW if led_on else OFF
                np[1] = OFF
            elif led_fx == 2:   # Right turn: only right LED blinks
                np[0] = OFF
                np[1] = YELLOW if led_on else OFF
            elif led_fx == 4:   # Both blink (hazard)
                np[0] = YELLOW if led_on else OFF
                np[1] = YELLOW if led_on else OFF
            np.write()
        else:
            led_pin.value(1 if led_on else 0)

# ==================== Main Loop ====================

my_mac = ':'.join('%02x' % b for b in wlan.config('mac'))
print("[FORKLIFT] Ready. MAC=%s CH=%d" % (my_mac, WIFI_CHANNEL))

last_recv = 0

while True:
    if e.any():
        host, msg = e.recv()
        if msg:
            text = msg.decode()

            # Pairing
            if text == "MOSS_PAIR":
                try:
                    e.add_peer(host)
                except:
                    pass
                e.send(host, b"MOSS_ACK:" + my_mac.encode())
                print("[FORKLIFT] Paired")
                continue

            # Control
            try:
                d = ujson.loads(text)
                motor1.set_speed(d.get('m1', 0))
                motor2.set_speed(d.get('m2', 0))
                fork_set(d.get('fk', 0))
                fx = d.get('ld', 0)
                if fx != led_fx:
                    led_set(fx)
                last_recv = time.ticks_ms()
            except Exception as ex:
                print("[FORKLIFT] Err: %s" % ex)

    # Failsafe
    if last_recv and time.ticks_diff(time.ticks_ms(), last_recv) > FAILSAFE_MS:
        motor1.stop()
        motor2.stop()
        fork_set(0)
        if led_fx not in (0, 3, 5):
            led_set(0)
        last_recv = 0

    led_update()
    time.sleep_ms(10)
