#!/usr/bin/env python3
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import time
import cv2
import base64
import anthropic
import os
import threading
from enum import Enum

# =========================
# 1) CONFIGURATION (PHYSICAL PINS)
# =========================
# The NFC Library forces BOARD mode, so we use Physical Pin numbers.

# --- LCD PINS (BOARD Mode) ---
LCD_RS = 37
LCD_E  = 35
LCD_D4 = 33
LCD_D5 = 31
LCD_D6 = 29
LCD_D7 = 36

LCD_WIDTH = 16
LCD_LINE_1 = 0x80
LCD_LINE_2 = 0xC0
E_PULSE = 0.0005
E_DELAY = 0.0005

# --- BUTTON PINS (BOARD Mode) ---
MODE_BUTTON_PIN = 7      # Physical Pin 7
TRIGGER_BUTTON_PIN = 13  # Physical Pin 13

# --- API KEY ---
key_file_path = os.path.expanduser("~/KEY.txt")
try:
    with open(key_file_path, "r") as f:
        ANTHROPIC_API_KEY = f.read().strip()
except FileNotFoundError:
    print(f"[ERROR] Could not find key file at: {key_file_path}")
    exit()

MODEL_ID = "claude-haiku-4-5-20251001"

# --- GLOBAL STATE ---
lcd_lock = threading.Lock()   
scan_lock = threading.Lock()  
display_timer = None          

# --- NFC READER OBJECT ---
try:
    reader = SimpleMFRC522()
except Exception as e:
    print(f"[ERROR] NFC Init Failed: {e}")

class Mode(Enum):
    MODE_1 = "Text Reading"
    MODE_2 = "Object Detect"
    MODE_3 = "Braille Read"

class AppState(Enum):
    IDLE = 1
    CONFIRMING = 2

current_mode = Mode.MODE_1 
current_state = AppState.IDLE
last_result_text = ""

# =========================
# 2) NFC FUNCTIONS
# =========================
def writeNfc(text):
    print("\n[NFC] Initializing write...")
    try:
        text_to_write = text[:48] 
        print(f"[NFC] Waiting for Tag... (Writing: '{text_to_write}')")
        
        safe_lcd_clear()
        safe_lcd_write("Hold Tag Near", "Reader...")

        reader.write(text_to_write)
        
        print("[NFC] Write Successful!")
        return True
    except Exception as e:
        print(f"[NFC ERROR] {e}")
        return False

def readNfc():
    """Reads NFC tag and displays content on LCD"""
    print("\n[NFC] Initializing read...")
    try:
        print("[NFC] Waiting for Tag...")
        safe_lcd_clear()
        safe_lcd_write("Hold Tag Near", "Reader...")

        # NOTE: This blocks until a tag is present
        id, text = reader.read()
        clean_text = text.strip()
        
        print(f"\n[NFC RESULT] ID: {id} | Text: {clean_text}")
        
        # --- IMPROVED DISPLAY LOGIC ---
        safe_lcd_clear()
        safe_lcd_write("Read Success!", "Showing Data...")
        time.sleep(1.0)
        
        # Show actual text on both lines
        safe_lcd_clear()
        line1 = clean_text[:16]      
        line2 = clean_text[16:32]    
        safe_lcd_write(line1, line2)
        
        time.sleep(4) # Give user time to read
        reset_to_ready()
        
    except Exception as e:
        print(f"[NFC ERROR] {e}")
        safe_lcd_clear()
        safe_lcd_write("Read Failed")
        time.sleep(2)
        reset_to_ready()

# =========================
# 3) LCD FUNCTIONS
# =========================
def lcd_toggle_enable():
    time.sleep(E_DELAY)
    GPIO.output(LCD_E, True)
    time.sleep(E_PULSE)
    GPIO.output(LCD_E, False)
    time.sleep(E_DELAY)

def lcd_send_nibble(n):
    GPIO.output(LCD_D4, bool(n & 0x01))
    GPIO.output(LCD_D5, bool(n & 0x02))
    GPIO.output(LCD_D6, bool(n & 0x04))
    GPIO.output(LCD_D7, bool(n & 0x08))
    lcd_toggle_enable()

def lcd_byte(value, is_data):
    GPIO.output(LCD_RS, is_data)
    lcd_send_nibble((value >> 4) & 0x0F)
    lcd_send_nibble(value & 0x0F)

def lcd_command(cmd):
    lcd_byte(cmd, False)

def lcd_clear():
    lcd_command(0x01)
    time.sleep(0.002)

def lcd_set_cursor(addr):
    lcd_command(addr)

def lcd_write_line(addr, s):
    s = (s or "").ljust(LCD_WIDTH)[:LCD_WIDTH]
    lcd_set_cursor(addr)
    for ch in s:
        lcd_byte(ord(ch), True)

def lcd_init():
    GPIO.setwarnings(False)
    for pin in (LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7):
        GPIO.setup(pin, GPIO.OUT)
    
    time.sleep(0.05)
    lcd_send_nibble(0x03); time.sleep(0.005)
    lcd_send_nibble(0x03); time.sleep(0.001)
    lcd_send_nibble(0x03); time.sleep(0.001)
    lcd_send_nibble(0x02)
    lcd_command(0x28); lcd_command(0x0C); lcd_command(0x06); lcd_clear()

def safe_lcd_write(line1, line2=""):
    with lcd_lock:
        lcd_write_line(LCD_LINE_1, line1)
        lcd_write_line(LCD_LINE_2, line2)

def safe_lcd_clear():
    with lcd_lock:
        lcd_clear()

# =========================
# 4) AI & CAMERA LOGIC
# =========================
def analyze_current_view():
    global current_mode
    if not cam.isOpened():
        print("[ERROR] Camera not detected.")
        return None

    for _ in range(5):
        cam.grab()

    ret, frame = cam.read()
    if not ret: return None

    _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    b64_image = base64.b64encode(buffer).decode('utf-8')

    print(f"   -> Analyzing in mode: {current_mode.value}...")
    
    if current_mode == Mode.MODE_1:
        prompt = "Transcribe the text in this image exactly as it appears. Respond with ONLY the raw text string. No conversational filler."
    elif current_mode == Mode.MODE_2:
        prompt = "Identify the primary object in this image. Respond with ONLY the object name (e.g. 'Red Coffee Mug'). Do not use full sentences."
    elif current_mode == Mode.MODE_3:
        prompt = "Transcribe the Braille dots in this image into English text. Respond with ONLY the translated text string. Do not include explanations."

    try:
        response = client.messages.create(
            model=MODEL_ID,
            max_tokens=60,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_image}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[ERROR] API Call failed: {e}")
        return None

# =========================
# 5) SHARED SCAN FUNCTION
# =========================
def reset_to_ready():
    global current_state
    current_state = AppState.IDLE
    safe_lcd_clear()
    safe_lcd_write("Ready to Scan:", current_mode.value)

def perform_scan():
    global current_state, last_result_text
    
    if not scan_lock.acquire(blocking=False):
        print("...Scan already running...")
        return

    try:
        print("\n--- Starting Scan ---")
        safe_lcd_clear()
        safe_lcd_write("Thinking...", current_mode.value)
        
        prediction = analyze_current_view()
        
        if prediction:
            print(f"[RESULT]: {prediction}")
            last_result_text = prediction
            current_state = AppState.CONFIRMING
            
            safe_lcd_clear()
            safe_lcd_write(prediction[:16], "1x:Write 2x:Drop")
        else:
            print("[RESULT]: (Failed)")
            safe_lcd_clear()
            safe_lcd_write("Error / Failed")
            time.sleep(2)
            reset_to_ready()
            
    finally:
        scan_lock.release()

# =========================
# 6) BUTTON THREADS
# =========================
def monitor_mode_button():
    """Cycles Mode (Physical Pin 7)"""
    GPIO.setup(MODE_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    global current_mode, current_state
    last_state = GPIO.HIGH
    
    while True:
        current_state_gpio = GPIO.input(MODE_BUTTON_PIN)
        if last_state == GPIO.HIGH and current_state_gpio == GPIO.LOW:
            
            if current_mode == Mode.MODE_1: new_mode = Mode.MODE_2
            elif current_mode == Mode.MODE_2: new_mode = Mode.MODE_3
            else: new_mode = Mode.MODE_1
            
            current_mode = new_mode
            current_state = AppState.IDLE
            print(f"\n[MODE] Changed to: {new_mode.value}")
            safe_lcd_clear()
            safe_lcd_write("Mode Changed:", new_mode.value)
            time.sleep(0.3) 

        last_state = current_state_gpio
        time.sleep(0.05)

def monitor_trigger_button():
    """Trigger Scan (Physical Pin 13)"""
    GPIO.setup(TRIGGER_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    global current_state, last_result_text
    last_gpio_state = GPIO.HIGH
    
    while True:
        current_gpio_state = GPIO.input(TRIGGER_BUTTON_PIN)
        
        if last_gpio_state == GPIO.HIGH and current_gpio_state == GPIO.LOW:
            
            # === LOGIC START ===
            
            # --- STATE: IDLE (Modified for Double Tap) ---
            if current_state == AppState.IDLE:
                print("\n[IDLE] Button pressed... Waiting for double tap...")
                
                # 1. Wait for release of 1st press
                while GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW:
                    time.sleep(0.01)
                
                # 2. Check for 2nd press within 400ms
                start_time = time.time()
                double_tap = False
                while (time.time() - start_time) < 0.4:
                    if GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW:
                        double_tap = True
                        break
                    time.sleep(0.01)
                
                # 3. Action
                if double_tap:
                    print("[IDLE] Double Tap Detected -> READ NFC")
                    # Wait for 2nd release
                    while GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW: time.sleep(0.1)
                    readNfc()
                else:
                    print("[IDLE] Single Tap Detected -> SCAN CAMERA")
                    perform_scan()

            # --- STATE: CONFIRMING ---
            elif current_state == AppState.CONFIRMING:
                print("\n[CONFIRM] Tap detected... Waiting for second...")
                
                while GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW:
                    time.sleep(0.01)
                
                start_time = time.time()
                double_tap = False
                while (time.time() - start_time) < 0.4:
                    if GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW:
                        double_tap = True
                        break
                    time.sleep(0.01)
                
                if double_tap:
                    print("[CONFIRM] Double Tap -> CANCELLED")
                    safe_lcd_clear()
                    safe_lcd_write("Cancelled.")
                    time.sleep(1.5)
                    reset_to_ready()
                    while GPIO.input(TRIGGER_BUTTON_PIN) == GPIO.LOW: time.sleep(0.1)
                else:
                    print("[CONFIRM] Single Tap -> WRITING NFC")
                    success = writeNfc(last_result_text)
                    safe_lcd_clear()
                    if success: safe_lcd_write("Write Success!")
                    else: safe_lcd_write("Write Failed.")
                    time.sleep(1.5)
                    reset_to_ready()

        last_gpio_state = current_gpio_state
        time.sleep(0.05)

# =========================
# 7) MAIN LOOP
# =========================
if __name__ == "__main__":
    lcd_init()
    safe_lcd_write("System Booting...")
    
    print("Initializing Claude Client...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    
    print("Initializing Camera...")
    cam = cv2.VideoCapture(0)
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # --- START THREADS ---
    t1 = threading.Thread(target=monitor_mode_button, daemon=True)
    t2 = threading.Thread(target=monitor_trigger_button, daemon=True)
    t1.start()
    t2.start()

    time.sleep(1)
    reset_to_ready()

    print("-----------------------------------")
    print("System Ready (BOARD MODE).")
    print("--- IDLE STATE ---")
    print("1 Tap  -> Take Picture (Scan)")
    print("2 Taps -> Read NFC Tag (Displays on Screen)")
    print("--- CONFIRM STATE (After Scan) ---")
    print("1 Tap  -> Write Result to NFC")
    print("2 Taps -> Cancel / Drop Result")
    print("-----------------------------------")

    try:
        while True:
            # Main thread monitors keyboard as backup
            user_input = input("> ").strip().lower()
            if user_input == 'read' or user_input == '':
                perform_scan()
            elif user_input == 'nfc':
                readNfc()
            elif user_input == 'q':
                print("Exiting...")
                safe_lcd_clear()
                safe_lcd_write("Goodbye!")
                time.sleep(1)
                break

    except KeyboardInterrupt:
        print("\nExiting...")

    finally:
        cam.release()
        safe_lcd_clear()
        GPIO.cleanup()