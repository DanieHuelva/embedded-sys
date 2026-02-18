#!/usr/bin/env python3
import time
import serial

PORT = "/dev/ttyACM0"
BAUD = 9600
CHANNELS = 18
MIN_US = 1200
MAX_US = 1800
CENTER_US = 1500

def us_to_target(us):
    return us * 4

def set_target(ser, ch, us):
    target = us_to_target(us)
    ser.write(bytes([
        0x84,
        ch,
        target & 0x7F,
        (target >> 7) & 0x7F
    ]))

print("Opening Maestro...")
with serial.Serial(PORT, BAUD, timeout=1) as ser:
    time.sleep(0.2)

    for ch in range(CHANNELS):
        print(f"Testing channel {ch}")
        for us in range(MIN_US, MAX_US + 1, 25):
            set_target(ser, ch, us)
            time.sleep(0.02)
        for us in range(MAX_US, MIN_US - 1, -25):
            set_target(ser, ch, us)
            time.sleep(0.02)

        set_target(ser, ch, CENTER_US)
        time.sleep(0.5)

print("Done.")
