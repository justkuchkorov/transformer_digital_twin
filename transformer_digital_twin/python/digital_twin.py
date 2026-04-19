"""
Transformer Digital Twin — Thermal Simulation Engine
=====================================================
Simulates a distribution transformer's thermal behavior and communicates
with a CODESYS SoftPLC via Modbus TCP/IP.

Upgrades from v1:
  - First-order thermal model with oil thermal mass (dT/dt = Q_net / C_th)
  - I²R load-dependent losses (copper) + constant core losses (iron)
  - Separate oil and winding temperature estimation
  - Float precision via ×100 Modbus scaling (0.01°C resolution)
  - PID-aware cooling model (fan speed 0-100% from PLC)
  - Multiple test scenarios (normal, overload, thermal runaway)
  - Watchdog detection (flags if PLC stops responding)
  - Ambient temperature variation (day/night cycle)
  - Auto-rotated CSV logs per session
"""

import time
import csv
import os
import math
import sys
import logging
from datetime import datetime
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

# Optional: Power BI push
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# --- Configuration ---
PLC_IP = '127.0.0.1'
PLC_PORT = 502
CYCLE_TIME = 1.0  # seconds per simulation step

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', 'data')

# Power BI Streaming Dataset endpoint (leave empty to disable)
PBI_ENDPOINT = ""

# =====================================================================
# TRANSFORMER THERMAL MODEL PARAMETERS
# Based on a ~630 kVA oil-immersed distribution transformer (ONAN/ONAF)
# =====================================================================

# Ambient
T_AMBIENT_BASE = 25.0      # Base ambient temperature (°C)
T_AMBIENT_SWING = 5.0      # Day/night variation amplitude (°C)
AMBIENT_PERIOD = 600.0      # Full day/night cycle in simulation seconds

# Thermal capacity (simplified lumped model)
# C_th represents the total thermal mass: oil + core + windings
# Units: kJ/°C — higher = slower temperature response
C_THERMAL = 45.0            # Thermal capacity (kJ/°C)

# Losses
P_CORE_LOSS = 1.3           # Constant core/iron losses (kW) — always present when energized
P_LOAD_LOSS_RATED = 6.5     # Copper losses at 100% load (kW) — scales with I²
# Actual copper loss = P_LOAD_LOSS_RATED × (load/100)²

# Cooling
# ONAN (natural): base cooling always present
# ONAF Stage 1: fan at variable speed (PID-controlled)
# ONAF Stage 2: second fan bank, full speed (backup)
K_NATURAL = 0.08            # Natural convection coefficient (kW/°C above ambient)
K_FAN1_MAX = 0.25           # Fan Stage 1 at 100% speed (kW/°C above ambient)
K_FAN2 = 0.18               # Fan Stage 2 full on (kW/°C above ambient)

# Winding hot-spot estimation
# Winding is hotter than oil due to I²R in the conductor
# Hot-spot rise above oil depends on load
WINDING_GRADIENT_RATED = 13.0  # °C above oil temp at rated load

# =====================================================================
# SIMULATION STATE
# =====================================================================
oil_temp = T_AMBIENT_BASE       # Oil temperature (°C) — main controlled variable
load_percent = 60.0             # Current load (%)
iteration = 0

# Watchdog: detect PLC not responding
plc_watchdog_fails = 0
PLC_WATCHDOG_LIMIT = 5

# =====================================================================
# TEST SCENARIOS
# =====================================================================
SCENARIOS = {
    "normal": {
        "desc": "Normal daily load cycle — 40-85% sinusoidal",
        "load_fn": lambda t: 62.5 + 22.5 * math.sin(t / 120.0) + 2.0 * math.sin(t / 17.0),
        "duration": 600,
    },
    "overload": {
        "desc": "Sudden overload event — ramps to 130% then recovers",
        "load_fn": lambda t: (
            70.0 if t < 60
            else min(70.0 + (t - 60) * 2.0, 130.0) if t < 120
            else max(130.0 - (t - 120) * 1.5, 75.0)
        ),
        "duration": 400,
    },
    "runaway": {
        "desc": "Thermal runaway test — sustained 120% load, cooling limited",
        "load_fn": lambda t: 120.0 + 3.0 * math.sin(t / 30.0),
        "duration": 300,
    },
    "coldstart": {
        "desc": "Cold start at 10°C ambient with rapid load pickup",
        "load_fn": lambda t: min(10.0 + t * 1.2, 95.0),
        "duration": 300,
    },
}


def get_ambient(t):
    """Simulate slow ambient temperature variation (day/night cycle)."""
    return T_AMBIENT_BASE + T_AMBIENT_SWING * math.sin(2 * math.pi * t / AMBIENT_PERIOD)


def compute_winding_temp(oil_t, load_pct):
    """Estimate winding hot-spot temperature from oil temp and load."""
    gradient = WINDING_GRADIENT_RATED * (load_pct / 100.0) ** 2
    return oil_t + gradient


def scale_to_modbus(value):
    """Convert float to ×100 integer for Modbus register (0.01 resolution)."""
    return max(0, min(65535, int(round(value * 100))))


def scale_from_modbus(raw):
    """Convert ×100 Modbus register back to float."""
    return raw / 100.0


def setup_logging():
    """Create a timestamped CSV log file for this session."""
    os.makedirs(DATA_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(DATA_DIR, f'telemetry_{timestamp}.csv')

    # Also maintain the standard telemetry.csv (overwrite each session)
    main_log = os.path.join(DATA_DIR, 'telemetry.csv')

    headers = [
        'Timestamp', 'Load_Percent', 'Oil_Temp_C', 'Winding_Temp_C',
        'Ambient_C', 'Fan_1_Active', 'Fan_1_Speed_Pct', 'Fan_2_Active',
        'Critical_Alarm', 'Rate_Alarm', 'Cooling_Effort_Pct', 'Scenario'
    ]

    for path in [log_path, main_log]:
        with open(path, 'w', newline='') as f:
            csv.writer(f).writerow(headers)

    return log_path, main_log


def push_to_powerbi(payload):
    """Push a single data point to Power BI Streaming Dataset."""
    if not PBI_ENDPOINT or not HAS_REQUESTS:
        return
    try:
        requests.post(PBI_ENDPOINT, json=[payload], timeout=1)
    except Exception:
        pass


def select_scenario():
    """Let user pick a test scenario from command line."""
    if len(sys.argv) > 1 and sys.argv[1] in SCENARIOS:
        return sys.argv[1]

    print("\n+==================================================+")
    print("|   TRANSFORMER DIGITAL TWIN -- TEST SCENARIOS     |")
    print("+==================================================+")
    for key, sc in SCENARIOS.items():
        print(f"|  {key:12s} -- {sc['desc'][:35]:35s} |")
    print("+==================================================+")
    print()

    choice = input("Select scenario [normal]: ").strip().lower()
    return choice if choice in SCENARIOS else "normal"


# =====================================================================
# OFFLINE PID EMULATOR (mirrors PLC logic for demo without CODESYS)
# =====================================================================
class OfflinePID:
    """Pure-Python replica of the PLC PID controller for offline mode."""

    def __init__(self):
        self.setpoint = 72.0
        self.kp = 4.0
        self.ki = 0.15
        self.kd = 1.0
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_temp = T_AMBIENT_BASE
        self.critical_alarm = 0
        self.rate_alarm = 0

    def update(self, core_temp):
        """Run one PID cycle. Returns (fan1, fan2, alarm, fan_speed, rate_alarm)."""
        # Rate of rise
        temp_rate = core_temp - self.prev_temp
        self.prev_temp = core_temp
        self.rate_alarm = 1 if temp_rate > 3.0 else 0

        # PID
        error = core_temp - self.setpoint
        self.integral = max(0.0, min(500.0, self.integral + error))
        derivative = error - self.prev_error
        self.prev_error = error

        pid_out = self.kp * error + self.ki * self.integral + self.kd * derivative
        effort = max(0.0, min(100.0, pid_out))

        # Fan Stage 1
        if effort > 5.0:
            fan1 = 1
            fan_speed = int(effort)
        elif effort < 2.0:
            fan1 = 0
            fan_speed = 0
        else:
            fan1 = 1
            fan_speed = int(effort)

        # Fan Stage 2 (backup)
        fan2 = 1 if core_temp >= 90.0 else (0 if core_temp <= 85.0 else 0)

        # Critical alarm (latching)
        if core_temp >= 105.0:
            self.critical_alarm = 1

        return fan1, fan2, self.critical_alarm, fan_speed, self.rate_alarm


def parse_args():
    """Parse command line arguments."""
    import argparse
    parser = argparse.ArgumentParser(description='Transformer Digital Twin')
    parser.add_argument('scenario', nargs='?', default=None,
                        help='Scenario name: normal, overload, runaway, coldstart')
    parser.add_argument('--offline', action='store_true',
                        help='Run without PLC (emulates PID controller locally)')
    parser.add_argument('--fast', action='store_true',
                        help='Run as fast as possible (no real-time delay)')
    return parser.parse_args()


def main():
    global oil_temp, load_percent, iteration, plc_watchdog_fails

    args = parse_args()

    # Select scenario
    if args.scenario and args.scenario in SCENARIOS:
        scenario_key = args.scenario
    else:
        scenario_key = select_scenario()

    scenario = SCENARIOS[scenario_key]
    load_fn = scenario["load_fn"]
    max_duration = scenario["duration"]
    offline = args.offline
    fast = args.fast

    # Cold start override
    if scenario_key == "coldstart":
        oil_temp = 10.0

    log_path, main_log = setup_logging()

    mode_str = "OFFLINE (PID emulator)" if offline else f"PLC at {PLC_IP}:{PLC_PORT}"
    print(f"\n[*] Scenario: {scenario_key} -- {scenario['desc']}")
    print(f"[*] Duration: {max_duration}s | Cycle: {CYCLE_TIME}s")
    print(f"[*] Mode: {mode_str}")
    print(f"[*] Speed: {'FAST (no delay)' if fast else 'Real-time'}")
    print(f"[*] Logging to: {os.path.basename(log_path)}")
    print(f"[*] Power BI: {'ENABLED' if PBI_ENDPOINT else 'DISABLED'}")

    client = None
    pid_emu = None

    if offline:
        pid_emu = OfflinePID()
        if scenario_key == "coldstart":
            pid_emu.prev_temp = 10.0
        print("[*] Offline mode -- PLC emulated locally.\n")
    else:
        print(f"[*] Connecting to PLC at {PLC_IP}:{PLC_PORT}...")
        client = ModbusTcpClient(PLC_IP, port=PLC_PORT)
        if not client.connect():
            print("[!] Failed to connect to PLC. Ensure CODESYS SoftPLC is running.")
            print("[*] Tip: use --offline to run without PLC.")
            return
        print("[*] Connected.\n")

    print(f"{'Time':>6s} | {'Load':>6s} | {'Oil':>7s} | {'Wind':>7s} | {'Amb':>5s} | {'F1':>3s} | {'Spd':>4s} | {'F2':>3s} | {'PID':>5s} | Status")
    print("-" * 85)

    try:
        while iteration < max_duration:
            t_start = time.time()

            # --- 1. Read PLC outputs (or emulate) ---
            fan1 = fan2 = alarm = fan_speed = rate_alarm = 0

            if offline:
                fan1, fan2, alarm, fan_speed, rate_alarm = pid_emu.update(oil_temp)
            else:
                try:
                    resp = client.read_input_registers(address=0, count=5)
                    if not resp.isError():
                        fan1       = resp.registers[0]
                        fan2       = resp.registers[1]
                        alarm      = resp.registers[2]
                        fan_speed  = resp.registers[3]
                        rate_alarm = resp.registers[4]
                        plc_watchdog_fails = 0
                    else:
                        plc_watchdog_fails += 1
                except Exception:
                    plc_watchdog_fails += 1

                if plc_watchdog_fails >= PLC_WATCHDOG_LIMIT:
                    print(f"[!] PLC WATCHDOG: No response for {plc_watchdog_fails} cycles -- safe state")
                    fan1, fan2, fan_speed = 1, 1, 100

            # --- 2. Compute load ---
            load_percent = load_fn(iteration)
            load_percent = max(0.0, min(150.0, load_percent))

            # --- 3. Thermal physics ---
            ambient = get_ambient(iteration)

            # Heat generation (kW)
            q_core = P_CORE_LOSS
            q_copper = P_LOAD_LOSS_RATED * (load_percent / 100.0) ** 2
            q_total = q_core + q_copper

            # Heat removal (kW)
            delta_t = oil_temp - ambient
            q_natural = K_NATURAL * delta_t
            q_fan1 = K_FAN1_MAX * (fan_speed / 100.0) * delta_t * fan1
            q_fan2 = K_FAN2 * delta_t * fan2
            q_cool_total = q_natural + q_fan1 + q_fan2

            # Net heat and temperature change
            q_net = q_total - q_cool_total
            dT = (q_net / C_THERMAL) * CYCLE_TIME
            oil_temp += dT
            oil_temp = max(ambient, oil_temp)

            # Winding hot-spot
            winding_temp = compute_winding_temp(oil_temp, load_percent)

            # --- 4. Write to PLC ---
            if not offline and client:
                try:
                    client.write_register(0, scale_to_modbus(oil_temp))
                    client.write_register(1, scale_to_modbus(load_percent))
                    client.write_register(2, scale_to_modbus(ambient))
                except Exception as e:
                    print(f"[!] Modbus Write Error: {e}")

            # --- 5. Log data ---
            ts_csv = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ts_iso = datetime.now().isoformat()

            cooling_effort = fan_speed if fan1 else 0
            row = [
                ts_csv,
                round(load_percent, 2),
                round(oil_temp, 2),
                round(winding_temp, 2),
                round(ambient, 2),
                fan1,
                fan_speed,
                fan2,
                alarm,
                rate_alarm,
                cooling_effort,
                scenario_key
            ]

            for path in [log_path, main_log]:
                with open(path, 'a', newline='') as f:
                    csv.writer(f).writerow(row)

            # --- 6. Power BI push ---
            push_to_powerbi({
                "Timestamp": ts_iso,
                "Load_Percent": round(load_percent, 2),
                "Oil_Temp_C": round(oil_temp, 2),
                "Winding_Temp_C": round(winding_temp, 2),
                "Ambient_C": round(ambient, 2),
                "Fan_1_Active": fan1,
                "Fan_1_Speed_Pct": fan_speed,
                "Fan_2_Active": fan2,
                "Critical_Alarm": alarm,
                "Rate_Alarm": rate_alarm,
            })

            # --- 7. Console output ---
            status_parts = []
            if alarm:
                status_parts.append("CRITICAL")
            if rate_alarm:
                status_parts.append("RATE-RISE")
            if not offline and plc_watchdog_fails >= PLC_WATCHDOG_LIMIT:
                status_parts.append("WDG-TRIP")
            status = " | ".join(status_parts) if status_parts else "OK"

            print(
                f"{iteration:5d}s | {load_percent:5.1f}% | {oil_temp:6.2f} C | "
                f"{winding_temp:6.2f} C | {ambient:4.1f} C | "
                f"{'ON' if fan1 else '--':>3s} | {fan_speed:3d}% | "
                f"{'ON' if fan2 else '--':>3s} | {cooling_effort:4d}% | {status}"
            )

            # --- 8. Sync ---
            iteration += 1
            if not fast:
                elapsed = time.time() - t_start
                time.sleep(max(0, CYCLE_TIME - elapsed))

        print(f"\n[*] Scenario '{scenario_key}' complete ({max_duration}s).")
        print(f"[*] Final oil temp: {oil_temp:.2f} C | Final winding temp: {winding_temp:.2f} C")

    except KeyboardInterrupt:
        print(f"\n[*] Stopped at iteration {iteration}. Oil temp: {oil_temp:.2f} C")
    finally:
        if client:
            client.close()
            print("[*] Modbus connection closed.")
        print(f"[*] Telemetry saved to: {os.path.basename(log_path)}")


if __name__ == "__main__":
    main()
