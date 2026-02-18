#!/usr/bin/env python3
import time
import serial

PORT = "/dev/ttyACM0"   # change to ttyACM1 if needed
BAUD = 9600

CENTER_US = 1500
DELTA_US = 250
DELAY = 0.4

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

print("\nMaestro Channel Identifier")
print("--------------------------")
print("Type channel number (0–17)")
print("Watch what moves")
print("Then type what that servo controls")
print("Ctrl+C to quit\n")

with serial.Serial(PORT, BAUD, timeout=1) as ser:
    time.sleep(0.3)

    while True:
        ch_in = input("Channel #: ").strip()
        if not ch_in.isdigit():
            print("Enter a number 0–17\n")
            continue

        ch = int(ch_in)
        if not (0 <= ch <= 17):
            print("Out of range\n")
            continue

        # Move servo
        set_target(ser, ch, CENTER_US)
        time.sleep(DELAY)
        set_target(ser, ch, CENTER_US - DELTA_US)
        time.sleep(DELAY)
        set_target(ser, ch, CENTER_US + DELTA_US)
        time.sleep(DELAY)
        set_target(ser, ch, CENTER_US)
        time.sleep(DELAY)

        label = input("What did that control? ").strip()
        if label:
            print(f"{label} = ch {ch}\n")
        else:
            print(f"(unlabeled) = ch {ch}\n")
