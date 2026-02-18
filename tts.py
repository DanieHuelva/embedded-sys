import pyttsx3

engine = pyttsx3.init()

# Optional tuning (safe defaults)
engine.setProperty('rate', 170)
engine.setProperty('volume', 1.0)

print("Type text to speak. Type 'exit' to quit.")

while True:
    text = input("> ").strip()

    if text == "":
        continue

    engine.say(text)
    engine.runAndWait()

    if text.lower() == "exit":
        break

print("Program ended.")
