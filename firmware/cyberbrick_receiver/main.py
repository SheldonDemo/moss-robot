# CyberBrick Forklift Receiver - main.py
# ESP32-C3 Core Board
#
# GPIO mapping:
#   Motor 1 (left track):  GPIO 4 (fwd), GPIO 5 (rev)
#   Motor 2 (right track): GPIO 6 (fwd), GPIO 7 (rev)
#   Servo 1 (fork):        GPIO 0
#   LED 2 (front light):   GPIO 20 (NeoPixel)
#
# ESP-NOW data format (CSV): "L1,L2,L3,R1,R2,R3,K1,K2,K3,K4"
#   ADC values 0-4095 (midpoint 2048), buttons 0/1
#
# Channel mapping (from rc_config):
#   [2] L3 → MOTOR1(+) + MOTOR2(-) = steering
#   [3] R1 → PWM1(-) = fork lift/lower
#   [4] R2 → MOTOR1(-) + MOTOR2(-) = throttle
#   [6] K1 → LED2 effect cycling

import machine
import time
import espnow
import network

# ---- Motor Controller (H-bridge, dual PWM) ----

class Motor:
    def __init__(self, fwd_pin, rev_pin, freq=20000):
        self.fwd = machine.PWM(machine.Pin(fwd_pin), freq=freq, duty=0)
        self.rev = machine.PWM(machine.Pin(rev_pin), freq=freq, duty=0)
        self.max_duty = 1023  # 10-bit

    def set_speed(self, speed):
        """speed: -1.0 to 1.0"""
        speed = max(-1.0, min(1.0, speed))
        duty = int(abs(speed) * self.max_duty)
        if speed >= 0:
            self.fwd.duty(duty)
            self.rev.duty(0)
        else:
            self.fwd.duty(0)
            self.rev.duty(duty)

    def stop(self):
        self.fwd.duty(0)
        self.rev.duty(0)


# ---- Servo Controller ----

class Servo:
    def __init__(self, pin, freq=50):
        self.pwm = machine.PWM(machine.Pin(pin), freq=freq, duty=0)
        self.current_duty = 0

    def set_position(self, value):
        """value: -1.0 to 1.0, maps to servo duty 25-125"""
        value = max(-1.0, min(1.0, value))
        # Map -1.0..1.0 to duty 25..125 (standard servo range)
        # 0 = center (duty 75)
        duty = int(75 + value * 50)
        duty = max(25, min(125, duty))
        if duty != self.current_duty:
            self.pwm.duty(duty)
            self.current_duty = duty

    def stop(self):
        self.pwm.duty(0)
        self.current_duty = 0


# ---- LED Controller (NeoPixel on GPIO 20) ----

class FrontLight:
    """Controls front light via simple on/off/blink on GPIO 20.
    Uses machine.Pin for basic control (not NeoPixel to keep it simple)."""

    def __init__(self, pin=20):
        self.pin = machine.Pin(pin, machine.Pin.OUT)
        self.pin.value(0)
        self.effect = 0  # 0=off
        self.last_toggle = 0
        self.blink_state = False

    def set_effect(self, fx):
        """fx: 0=off, 1=on, 2=blink (turn signal)"""
        self.effect = fx
        if fx == 0:
            self.pin.value(0)
        elif fx == 1:
            self.pin.value(1)

    def update(self):
        """Call in main loop for blink effects."""
        if self.effect == 2:
            now = time.ticks_ms()
            if time.ticks_diff(now, self.last_toggle) > 250:
                self.blink_state = not self.blink_state
                self.pin.value(1 if self.blink_state else 0)
                self.last_toggle = now


# ---- Forklift Receiver ----

MID = 2048
DEADZONE = 200  # Match rc_config deadzone

class ForkliftReceiver:
    def __init__(self):
        # Hardware
        self.motor_left = Motor(4, 5)   # MOTOR1 = left track
        self.motor_right = Motor(6, 7)  # MOTOR2 = right track
        self.fork = Servo(0)            # PWM1 = fork servo
        self.light = FrontLight(20)     # LED2 = front light

        # ESP-NOW
        self.wlan = network.WLAN(network.STA_IF)
        self.e = espnow.ESPNow()
        self.e.active(True)

        # State
        self.last_recv_time = 0
        self.prev_k1 = 0
        self.current_light_fx = 0
        self.paired_mac = None
        self.running = True

        print("[RX] Forklift receiver initialized")

    def parse_frame(self, data):
        """Parse CSV data into 10 ints."""
        try:
            text = data.decode() if isinstance(data, bytes) else data
            values = [int(x.strip()) for x in text.split(',')]
            if len(values) == 10:
                return values
        except:
            pass
        return None

    def apply_controls(self, ch):
        """Map 10-channel data to actuators."""
        # Apply deadzone
        def apply_deadzone(val, mid, dz):
            diff = val - mid
            if abs(diff) < dz:
                return 0
            return diff

        # Steering: ch[2] (L3) → MOTOR1(+) + MOTOR2(-)
        steer = apply_deadzone(ch[2], MID, DEADZONE)
        # Throttle: ch[4] (R2) → MOTOR1(-) + MOTOR2(-)
        throttle = apply_deadzone(ch[4], MID, DEADZONE)

        # Differential drive:
        #   MOTOR1 = (steer) + (-throttle) = steer - throttle
        #   MOTOR2 = (-steer) + (-throttle) = -steer - throttle
        # Normalize to -1.0..1.0
        left = (steer - throttle) / 4096.0
        right = (-steer - throttle) / 4096.0
        left = max(-1.0, min(1.0, left))
        right = max(-1.0, min(1.0, right))

        self.motor_left.set_speed(left)
        self.motor_right.set_speed(right)

        # Fork: ch[3] (R1) → PWM1(-)
        fork_val = apply_deadzone(ch[3], MID, DEADZONE)
        fork_pos = -fork_val / 2048.0  # Negative direction
        fork_pos = max(-1.0, min(1.0, fork_pos))
        self.fork.set_position(fork_pos)

        # Light: K1 button rising edge cycles effects
        k1 = ch[6]
        if k1 == 1 and self.prev_k1 == 0:
            self.current_light_fx = (self.current_light_fx + 1) % 3
            self.light.set_effect(self.current_light_fx)
        self.prev_k1 = k1

    def failsafe_check(self):
        """Stop all outputs if no data received for 500ms."""
        if self.last_recv_time > 0:
            elapsed = time.ticks_diff(time.ticks_ms(), self.last_recv_time)
            if elapsed > 500:
                self.motor_left.stop()
                self.motor_right.stop()
                self.fork.stop()
                self.last_recv_time = 0
                print("[RX] Failsafe: no data, stopped")

    def handle_pairing(self, mac, data):
        """Respond to pairing requests."""
        try:
            text = data.decode() if isinstance(data, bytes) else data
            if text == "PAIR_REQ":
                print("[RX] Pairing request from", self.mac_str(mac))
                self.paired_mac = mac
                # Send pairing acknowledgment back
                self.e.send(mac, b"PAIR_ACK")
                print("[RX] Sent PAIR_ACK")
        except:
            pass

    def mac_str(self, mac):
        return ':'.join('{:02X}'.format(b) for b in mac)

    def run(self):
        """Main control loop."""
        print("[RX] Waiting for data...")
        while self.running:
            # Check for ESP-NOW data
            try:
                mac, msg = self.e.recv(0)  # Non-blocking
                if mac:
                    text = msg.decode() if isinstance(msg, bytes) else msg

                    # Handle pairing
                    if text == "PAIR_REQ":
                        self.handle_pairing(mac, msg)
                        continue

                    # Handle control data
                    if self.paired_mac and bytes(mac) == bytes(self.paired_mac):
                        ch = self.parse_frame(msg)
                        if ch:
                            self.last_recv_time = time.ticks_ms()
                            self.apply_controls(ch)
                    elif not self.paired_mac:
                        # Not paired yet, accept any data (for initial pairing via data)
                        ch = self.parse_frame(msg)
                        if ch:
                            self.paired_mac = mac
                            self.last_recv_time = time.ticks_ms()
                            self.apply_controls(ch)
                            print("[RX] Auto-paired with", self.mac_str(mac))
            except OSError:
                pass  # No data available

            # Update blink effects
            self.light.update()

            # Failsafe check
            self.failsafe_check()

            time.sleep_ms(5)  # ~200Hz check rate


# ---- Entry Point ----

if __name__ == "__main__":
    rx = ForkliftReceiver()
    rx.run()
