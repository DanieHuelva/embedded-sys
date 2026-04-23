"""
kill_servos.py
Run this anytime to immediately stop all servo movement on the Maestro.
Usage: python3 kill_servos.py
"""

import serial
import time
import os

PORT = os.getenv("MAESTRO_PORT", "/dev/ttyACM0")
BAUD = int(os.getenv("MAESTRO_BAUD", "9600"))

print(f"Connecting to Maestro on {PORT}...")
try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(0.2)

    # Set target to 0 for all 18 channels — stops all pulses
    for ch in range(18):
        ser.write(bytes([0x84, ch, 0, 0]))
        time.sleep(0.01)

    print("All servos stopped.")
    ser.close()
except Exception as e:
    print(f"Error: {e}")
