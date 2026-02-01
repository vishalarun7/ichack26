#!/usr/bin/env python3
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
from elevenlabs.client import ElevenLabs
import time
import os

# =========================
# 1) CONFIGURATION
# =========================

# --- API KEY SETUP ---
# user explicitly requested reading from ~/KEY.txt using absolute path
key_file_path = os.path.expanduser("~/KEY1.txt")

try:
    with open(key_file_path, "r") as f:
        ELEVENLABS_API_KEY = f.read().strip()
except FileNotFoundError:
    print(f"[ERROR] Could not find key file at: {key_file_path}")
    print("Please ensure ~/KEY.txt exists and contains your ElevenLabs API key.")
    exit()

# Initialize ElevenLabs Client with the read key
client = ElevenLabs(
    api_key=ELEVENLABS_API_KEY
)

# Initialize NFC Reader
try:
    reader = SimpleMFRC522()
except Exception as e:
    print(f"[ERROR] NFC Init Failed: {e}")
    exit()

# =========================
# 2) FUNCTIONS
# =========================

def speak_text(text):
    """
    Sends text to ElevenLabs, saves the MP3, and plays it.
    """
    if not text:
        return

    print(f"Generating audio for: '{text}'...")
    
    try:
        # 1. Generate Audio Stream
        audio_generator = client.text_to_speech.convert(
            voice_id="hpp4J3VqNfWAUOO0d1Us", 
            text=text,
            model_id="eleven_monolingual_v1"
        )

        # 2. Convert Stream to Bytes
        audio_bytes = b"".join(audio_generator)

        # 3. Save to File
        filename = "/home/pi/ichack/output.mp3"
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        
        # 4. Play the File (using mpg123)
        print("Playing audio...")
        os.system(f"mpg123 -q {filename}")

    except Exception as e:
        print(f"[TTS ERROR] {e}")

def get_nfc_text():
    """
    Waits for a tag, reads the data, and returns cleaned text.
    """
    try:
        print("\nWaiting for NFC tag...")
        
        # This blocks until a card is detected
        id, text = reader.read()
        
        # Clean whitespace
        return text.strip()
        
    except Exception as e:
        print(f"Error reading NFC: {e}")
        return None

# =========================
# 3) MAIN LOOP
# =========================
if __name__ == "__main__":
    print("--------------------------------")
    print("NFC Speaker Active (Ctrl+C to quit)")
    print("--------------------------------")

    try:
        while True:
            # 1. Read the Tag
            tag_content = get_nfc_text()
            
            # 2. Process Result
            if tag_content:
                print(f"[READ] Content: {tag_content}")
                
                # 3. Speak the Content
                speak_text(tag_content)
                
                # Small delay to prevent repeating the same tag instantly
                time.sleep(2)
            else:
                print("[READ] Empty tag or error.")
                time.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting...")
        GPIO.cleanup()