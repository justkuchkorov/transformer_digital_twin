# Transformer Digital Twin: Closed-Loop Thermal Simulation

This is a closed-loop digital twin built to stress-test industrial PLC safety logic before deploying it to physical hardware. 

Instead of burning out a real transformer to see if the cooling fans turn on, this project uses a Python physics engine to simulate grid load and thermodynamics. The Python script communicates in real-time with a CODESYS SoftPLC over Modbus TCP, allowing the PLC to react to virtual temperature spikes and trigger cooling stages based on industrial hysteresis logic.

## 🏗️ System Architecture

* **The Brain (CODESYS V3 SoftPLC):** Acts as the Modbus TCP Server. It runs an IEC 61131-3 Structured Text (ST) program with built-in deadband/hysteresis logic to prevent hardware chattering.
* **The Physical World (Python):** Acts as the Modbus TCP Client. It simulates a dynamic city grid load, calculates thermal gain/loss, and writes the telemetry to the PLC every 1.0 seconds via `pymodbus`.
* **The SCADA / HMI (Power BI):** Reads the generated `telemetry.csv` file to visualize the closed-loop control, proving that the PLC successfully intervened to prevent a thermal runaway.

## 📂 Repository Structure

```text
transformer_digital_twin/
│
├── plc/
│   └── Thermal_Controller.project  # CODESYS SoftPLC program
├── python/
│   └── digital_twin.py             # Physics engine & Modbus client
├── data/
│   └── telemetry.csv               # Live data output
└── dashboard/
    └── thermal_validation.pbix     # Power BI validation report
```
**Visualize the Data:** Let the simulation run for 5-10 minutes to generate a full thermal cycle. Open thermal_validation.pbix in Power BI and click Refresh to see the hysteresis control in action.

**📊 The Proof: Hysteresis in Action**
As seen in the telemetry data, when the simulated core temperature crosses the 75°C threshold, the PLC successfully triggers Fan Stage 1. The hysteresis logic holds the fan ON until the temperature safely drops to 70°C, creating a perfect industrial control sawtooth wave.

<img width="652" height="437" alt="image_2026-03-19_17-14-48" src="https://github.com/user-attachments/assets/fb6c3bbe-7f2f-45e3-9fef-4d391f6d5b66" />
