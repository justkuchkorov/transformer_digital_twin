import time
import csv
import os
import math
import logging
import requests  # Required for Power BI API
from datetime import datetime
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# --- Configuration ---
PLC_IP = '127.0.0.1'
PLC_PORT = 502
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, '..', 'data', 'telemetry.csv')

# Power BI Configuration (Paste your Push URL here)
# To get this: Power BI Service > My Workspace > Create > Streaming Dataset > API
PBI_ENDPOINT = "" 

# Physics Constants (Tuned for faster testing)
T_AMBIENT = 25.0
K_HEAT = 0.02     # Heating rate per load unit
K_COOL1 = 1.5     # Cooling capacity of Fan Stage 1
K_COOL2 = 2.5     # Cooling capacity of Fan Stage 2
K_LOSS = 0.002    # Natural thermal dissipation factor

# Simulation State
current_temp = 25.0
load_percent = 60.0
iteration = 0

# Ensure data directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Initialize CSV file with headers if it doesn't exist
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Timestamp', 'Load_Percent', 'Core_Temp_C', 'Fan_1_Active', 'Fan_2_Active', 'Critical_Alarm'])

def push_to_powerbi(payload):
    """Pushes a single data point to the Power BI Streaming Dataset."""
    if not PBI_ENDPOINT:
        return
    try:
        # Power BI expects an array of objects
        response = requests.post(PBI_ENDPOINT, json=[payload], timeout=1)
        if response.status_code != 200:
            print(f"[!] Power BI Error: {response.status_code}")
    except Exception as e:
        print(f"[!] Power BI Connection Failed: {e}")

def get_simulated_load(t):
    """Generates a realistic grid load using a sine wave with random jitter."""
    base_load = 67.5  # Midpoint of 40% and 95%
    amplitude = 27.5
    jitter = (math.sin(t / 10.0) * 2.0) + (0.5 - (0.5 * (t % 5))) # Small oscillations
    return base_load + amplitude * math.sin(t / 120.0) + jitter

# Initialize Modbus Client
client = ModbusTcpClient(PLC_IP, port=PLC_PORT)

print(f"[*] Starting Transformer Digital Twin Simulation...")
print(f"[*] Power BI Push: {'ENABLED' if PBI_ENDPOINT else 'DISABLED (Set PBI_ENDPOINT)'}")
print(f"[*] Connecting to PLC at {PLC_IP}:{PLC_PORT}...")

try:
    if not client.connect():
        print("[!] Failed to connect to PLC. Ensure CODESYS SoftPLC is running.")
        exit(1)

    print("[*] Connection successful. Beginning physics loop. Press Ctrl+C to stop.")

    while True:
        start_time = time.time()
        
        # 1. Read Control State from PLC (Input Registers 0, 1, 2)
        try:
            response = client.read_input_registers(address=0, count=3)
            if response.isError():
                raise ModbusException("Failed to read input registers from PLC")
            
            fan1 = response.registers[0]  # Input Register 0 (Fan 1)
            fan2 = response.registers[1]  # Input Register 1 (Fan 2)
            alarm = response.registers[2] # Input Register 2 (Critical Alarm)
        except Exception as e:
            print(f"[!] Modbus Read Error: {e}")
            fan1, fan2, alarm = 0, 0, 0

        # 2. Update Physics Engine
        load_percent = get_simulated_load(iteration)
        load_percent = max(40.0, min(95.0, load_percent)) # Bound load
        
        # Physics Formula
        heat_gain = load_percent * K_HEAT
        cool_loss = (fan1 * K_COOL1) + (fan2 * K_COOL2)
        ambient_loss = (current_temp - T_AMBIENT) * K_LOSS
        
        current_temp = current_temp + heat_gain - cool_loss - ambient_loss
        
        # 3. Write Simulated State to PLC (Holding Registers 0, 1)
        try:
            client.write_register(address=0, value=int(current_temp))
            client.write_register(address=1, value=int(load_percent))
        except Exception as e:
            print(f"[!] Modbus Write Error: {e}")

        # 4. Prepare Payload
        timestamp_iso = datetime.now().isoformat()
        timestamp_csv = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        payload = {
            "Timestamp": timestamp_iso,
            "Load_Percent": round(load_percent, 2),
            "Core_Temp_C": round(current_temp, 2),
            "Fan_1_Active": int(fan1),
            "Fan_2_Active": int(fan2),
            "Critical_Alarm": int(alarm)
        }

        # 5. Data Logging (CSV)
        log_row = [timestamp_csv, payload["Load_Percent"], payload["Core_Temp_C"], fan1, fan2, alarm]
        with open(LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(log_row)

        # 6. Push to Power BI
        push_to_powerbi(payload)

        # 7. Console Output for Monitoring
        status = "ALARM" if alarm else "NORMAL"
        print(f"[{timestamp_csv}] T: {payload['Core_Temp_C']:.2f}°C | L: {payload['Load_Percent']:.1f}% | F1: {fan1} | F2: {fan2} | Status: {status}")

        # 8. Synchronization
        iteration += 1
        elapsed = time.time() - start_time
        time.sleep(max(0, 1.0 - elapsed))

except KeyboardInterrupt:
    print("\n[*] Simulation stopped by user.")
finally:
    client.close()
    print("[*] Modbus connection closed.")
