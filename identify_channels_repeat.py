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

def wiggle(ser, ch):
    ser.write(bytes([0x84, ch, (CENTER_US*4) & 0x7F, ((CENTER_US*4) >> 7) & 0x7F]))
    time.sleep(DELAY)
    ser.write(bytes([0x84, ch, ((CENTER_US-DELTA_US)*4) & 0x7F, (((CENTER_US-DELTA_US)*4) >> 7) & 0x7F]))
    time.sleep(DELAY)
    ser.write(bytes([0x84, ch, ((CENTER_US+DELTA_US)*4) & 0x7F, (((CENTER_US+DELTA_US)*4) >> 7) & 0x7F]))
    time.sleep(DELAY)
    ser.write(bytes([0x84, ch, (CENTER_US*4) & 0x7F, ((CENTER_US*4) >> 7) & 0x7F]))
    time.sleep(DELAY)

print("\nMaestro Channel Identifier (repeatable)")
print("---------------------------------------")
print("Enter channel number (0–17)")
print("ENTER = retest same channel")
print("'n'   = new channel")
print("'q'   = quit\n")

with serial.Serial(PORT, BAUD, timeout=1) as ser:
    time.sleep(0.3)

    current_channel = None

    while True:
        if current_channel is None:
            ch_in = input("Channel #: ").strip()
            if ch_in.lower() == 'q':
                break
            if not ch_in.isdigit():
                print("Enter a number 0–17\n")
                continue

            ch = int(ch_in)
            if not (0 <= ch <= 17):
                print("Out of range\n")
                continue

            current_channel = ch
            print(f"\nTesting channel {current_channel}")

        wiggle(ser, current_channel)

        cmd = input("ENTER=retest | n=new channel | q=quit : ").strip().lower()

        if cmd == 'q':
            break
        elif cmd == 'n':
            label = input("What did this channel control? ").strip()
            if label:
                print(f"{label} = ch {current_channel}\n")
            else:
                print(f"(unlabeled) = ch {current_channel}\n")
            current_channel = None
        else:
            # ENTER or anything else → retest
            pass

print("\nDone.")
