# Transformer Digital Twin: Closed-Loop Thermal Simulation

A closed-loop digital twin that stress-tests industrial PLC cooling logic before deploying to physical hardware. A Python physics engine simulates a ~630 kVA oil-immersed distribution transformer, communicating in real-time with a CODESYS SoftPLC over Modbus TCP/IP.

## System Architecture

```
Python Digital Twin              Modbus TCP/IP (port 502)             CODESYS SoftPLC
+--------------------------+     Hold Regs (x100 scaled)     +---------------------------+
| First-order thermal model| --- Oil Temp, Load%, Ambient -> | IEC 61131-3 Structured Text|
| dT = Q_net/C_th * dt    | <-- Fan1, Fan2, Speed, Alarms - | PID controller (Kp=4, Ki=  |
|                          |                                 | 0.15, Kd=1, SP=72C)       |
| Heat in:                 |                                 | Watchdog timer (10 cycles) |
|   Core loss (1.3 kW)    |                                 | Rate-of-rise alarm (>3C/s) |
|   I2R copper loss (load2)|                                 | Staged fan control         |
| Heat out:                |                                 | Critical alarm + manual    |
|   ONAN + Fan1 PID + Fan2|                                 |   reset (latch >= 105C)    |
+-----------+--------------+                                 +---------------------------+
            |
            v
   +--------------------+
   | Live Dashboard     |
   | (matplotlib 4-panel)|
   | + Post-run analysis |
   | + Power BI push API |
   +--------------------+
```

**The Brain (CODESYS V3 SoftPLC):** Runs a PID cooling controller in IEC 61131-3 Structured Text. Fan Stage 1 speed is modulated 0-100% by PID output. Fan Stage 2 is a backup ON/OFF layer at 90C. Watchdog detects if the Python twin stops sending data and forces safe state (all cooling ON). Rate-of-rise alarm triggers if dT/dt > 3C/s.

**The Physical World (Python):** Simulates transformer thermodynamics with separate I2R copper losses (load-dependent, scales with current squared) and constant core/iron losses. Multi-mode cooling: natural ONAN convection + PID-modulated fan + backup fan. Includes winding hot-spot estimation and day/night ambient temperature variation.

**Dashboard:** Real-time 4-panel matplotlib dashboard (temperatures, load, cooling status, alarms) that auto-refreshes from telemetry.csv. Post-run analysis script calculates thermal time constants, energy balance, peak metrics, and generates a 6-panel report.

## Repository Structure

```text
transformer_digital_twin/
|
|-- plc/
|   |-- Thermal_Controller.project          # CODESYS SoftPLC project file
|   +-- codesys-code-in-textbook/
|       +-- plc-code.txt                    # PLC source (readable ST text)
|-- python/
|   +-- digital_twin.py                     # Physics engine & Modbus client
|-- data/
|   +-- telemetry.csv                       # Live/latest telemetry output
+-- dashboard/
    |-- thermal_validation.pbix             # Power BI report
    |-- live_dashboard.py                   # Real-time 4-panel dashboard
    +-- analyze_run.py                      # Post-run analysis & report
```

## Quick Start

**With CODESYS SoftPLC running:**
```bash
python transformer_digital_twin/python/digital_twin.py normal
```

**Without PLC (offline demo):**
```bash
python transformer_digital_twin/python/digital_twin.py runaway --offline --fast
```

**Open live dashboard (in a second terminal):**
```bash
python transformer_digital_twin/dashboard/live_dashboard.py
```

**Generate post-run analysis:**
```bash
python transformer_digital_twin/dashboard/analyze_run.py
```

## Test Scenarios

| Scenario | Load Profile | Duration | What It Tests |
|---|---|---|---|
| `normal` | 40-85% sinusoidal daily cycle | 600s | Steady-state PID behavior |
| `overload` | Ramps to 130% then recovers | 400s | Transient response, I2R spike |
| `runaway` | 120%+ sustained with no relief | 300s | PID limits, fan saturation |
| `coldstart` | Starts at 10C, steps to 80% | 300s | Cold oil viscosity, ramp-up |

## PLC Control Logic

The PLC program implements 5 steps per scan cycle:

1. **Decode Modbus** -- WORD / 100.0 to get REAL values (0.01C resolution)
2. **Watchdog** -- If temperature register unchanged for 10 cycles, trip to safe state
3. **Rate-of-rise** -- If dT/dt > 3.0C/s, fire early warning alarm
4. **PID controller** -- Error = Temp - 72C setpoint, anti-windup integral clamp 0-500, output clamped 0-100%
5. **Staged fans** -- Fan 1 speed = PID output. Fan 2 backup ON >= 90C / OFF <= 85C. Critical alarm latches at 105C, manual reset only below 80C.

## The Proof: PID Cooling Control

In the `runaway` scenario (sustained 120%+ load), the PID controller ramps Fan 1 from 0 to ~55% speed and stabilizes oil temperature at ~77C -- well below the 105C critical threshold. The old ON/OFF hysteresis would have produced oscillating sawtooth waves; the PID gives smooth, proportional control.

<img width="1737" height="784" alt="image_2026-03-19_17-32-04" src="https://github.com/user-attachments/assets/5713587e-1e70-49df-b5a4-4897bac2aa63" />

## Tech Stack

- **PLC:** CODESYS V3 SoftPLC, IEC 61131-3 Structured Text
- **Communication:** Modbus TCP/IP (pymodbus), x100 integer scaling for 16-bit registers
- **Physics:** Python (first-order thermal ODE, I2R + core losses, multi-mode cooling)
- **Dashboard:** matplotlib (live + analysis), Power BI (streaming push API)
- **Protocols:** IEC 60076 transformer temperature limits

