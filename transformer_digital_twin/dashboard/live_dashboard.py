"""
Live Telemetry Dashboard — Transformer Digital Twin
====================================================
Reads telemetry.csv in real-time and displays a 4-panel dashboard:
  1. Temperature curves (oil, winding, ambient)
  2. Load profile (%)
  3. Cooling status (fan speed, fan states)
  4. Alarms & rate-of-rise indicator

Usage:
  python live_dashboard.py              # auto-refresh from telemetry.csv
  python live_dashboard.py --file <path> # specify a specific log file
  python live_dashboard.py --static     # one-shot render (no auto-refresh)
"""

import os
import sys
import argparse
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.animation import FuncAnimation
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', 'data')
DEFAULT_CSV = os.path.join(DATA_DIR, 'telemetry.csv')

# New column headers (from upgraded digital_twin.py)
EXPECTED_COLS = [
    'Timestamp', 'Load_Percent', 'Oil_Temp_C', 'Winding_Temp_C',
    'Ambient_C', 'Fan_1_Active', 'Fan_1_Speed_Pct', 'Fan_2_Active',
    'Critical_Alarm', 'Rate_Alarm', 'Cooling_Effort_Pct', 'Scenario'
]

# Old column headers (from v1 digital_twin.py)
OLD_COLS = [
    'Timestamp', 'Load_Percent', 'Core_Temp_C', 'Fan_1_Active',
    'Fan_2_Active', 'Critical_Alarm'
]

# Colors
C_OIL = '#e74c3c'
C_WINDING = '#e67e22'
C_AMBIENT = '#3498db'
C_LOAD = '#2ecc71'
C_FAN_SPEED = '#9b59b6'
C_FAN1 = '#1abc9c'
C_FAN2 = '#f39c12'
C_ALARM = '#e74c3c'
C_RATE = '#e67e22'
C_BG = '#1a1a2e'
C_PANEL = '#16213e'
C_TEXT = '#e0e0e0'
C_GRID = '#2a2a4a'

# Thresholds (visual reference lines)
T_FAN1_ON = 72.0       # PID setpoint
T_FAN2_ON = 90.0       # Fan Stage 2 activation
T_CRITICAL = 105.0     # Critical alarm


def load_data(csv_path):
    """Load telemetry CSV, handling both old and new column formats."""
    try:
        df = pd.read_csv(csv_path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return None

    if len(df) == 0:
        return None

    # Detect format and normalize columns
    if 'Oil_Temp_C' in df.columns:
        # New format — all good
        pass
    elif 'Core_Temp_C' in df.columns:
        # Old format — adapt
        df = df.rename(columns={'Core_Temp_C': 'Oil_Temp_C'})
        if 'Winding_Temp_C' not in df.columns:
            df['Winding_Temp_C'] = df['Oil_Temp_C'] + 5.0  # rough estimate
        if 'Ambient_C' not in df.columns:
            df['Ambient_C'] = 25.0
        if 'Fan_1_Speed_Pct' not in df.columns:
            df['Fan_1_Speed_Pct'] = df['Fan_1_Active'] * 100
        if 'Rate_Alarm' not in df.columns:
            df['Rate_Alarm'] = 0
        if 'Cooling_Effort_Pct' not in df.columns:
            df['Cooling_Effort_Pct'] = df['Fan_1_Active'] * 100
        if 'Scenario' not in df.columns:
            df['Scenario'] = 'unknown'

    df['t'] = range(len(df))  # time axis in seconds
    return df


def setup_figure():
    """Create the 4-panel dark-theme figure."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 8), facecolor=C_BG)
    fig.suptitle('TRANSFORMER DIGITAL TWIN — LIVE TELEMETRY',
                 fontsize=14, fontweight='bold', color=C_TEXT, y=0.98)
    fig.subplots_adjust(hspace=0.35, wspace=0.25, top=0.92, bottom=0.08,
                        left=0.07, right=0.97)

    for ax in axes.flat:
        ax.set_facecolor(C_PANEL)
        ax.tick_params(colors=C_TEXT, labelsize=8)
        ax.grid(True, color=C_GRID, alpha=0.5, linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color(C_GRID)
        ax.xaxis.label.set_color(C_TEXT)
        ax.yaxis.label.set_color(C_TEXT)
        ax.title.set_color(C_TEXT)

    return fig, axes


def draw_frame(df, axes):
    """Draw all 4 panels from dataframe."""
    ax_temp, ax_load, ax_cool, ax_alarm = axes.flat

    for ax in axes.flat:
        ax.clear()
        ax.set_facecolor(C_PANEL)
        ax.tick_params(colors=C_TEXT, labelsize=8)
        ax.grid(True, color=C_GRID, alpha=0.5, linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color(C_GRID)

    t = df['t']

    # --- Panel 1: Temperatures ---
    ax_temp.set_title('Temperatures (°C)', fontsize=11, fontweight='bold', color=C_TEXT)
    ax_temp.plot(t, df['Oil_Temp_C'], color=C_OIL, linewidth=1.5, label='Oil')
    ax_temp.plot(t, df['Winding_Temp_C'], color=C_WINDING, linewidth=1.5, label='Winding')
    ax_temp.plot(t, df['Ambient_C'], color=C_AMBIENT, linewidth=1, linestyle='--', label='Ambient')

    # Reference lines
    ax_temp.axhline(T_FAN1_ON, color=C_FAN1, linewidth=0.8, linestyle=':', alpha=0.6)
    ax_temp.axhline(T_FAN2_ON, color=C_FAN2, linewidth=0.8, linestyle=':', alpha=0.6)
    ax_temp.axhline(T_CRITICAL, color=C_ALARM, linewidth=0.8, linestyle=':', alpha=0.6)

    # Annotate thresholds on right side
    xlim = ax_temp.get_xlim()
    ax_temp.text(xlim[1], T_FAN1_ON, ' PID SP', fontsize=7, color=C_FAN1, va='center')
    ax_temp.text(xlim[1], T_FAN2_ON, ' Fan2', fontsize=7, color=C_FAN2, va='center')
    ax_temp.text(xlim[1], T_CRITICAL, ' CRIT', fontsize=7, color=C_ALARM, va='center')

    ax_temp.set_xlabel('Time (s)', fontsize=9, color=C_TEXT)
    ax_temp.set_ylabel('°C', fontsize=9, color=C_TEXT)
    ax_temp.legend(loc='upper left', fontsize=8, framealpha=0.3,
                   facecolor=C_PANEL, edgecolor=C_GRID, labelcolor=C_TEXT)

    # Current values box
    if len(df) > 0:
        last = df.iloc[-1]
        info = (f"Oil: {last['Oil_Temp_C']:.1f}°C\n"
                f"Wind: {last['Winding_Temp_C']:.1f}°C\n"
                f"Amb: {last['Ambient_C']:.1f}°C")
        ax_temp.text(0.98, 0.95, info, transform=ax_temp.transAxes,
                     fontsize=8, color=C_TEXT, va='top', ha='right',
                     bbox=dict(boxstyle='round,pad=0.4', facecolor=C_BG, alpha=0.8,
                               edgecolor=C_GRID))

    # --- Panel 2: Load Profile ---
    ax_load.set_title('Load Profile (%)', fontsize=11, fontweight='bold', color=C_TEXT)
    ax_load.fill_between(t, df['Load_Percent'], alpha=0.3, color=C_LOAD)
    ax_load.plot(t, df['Load_Percent'], color=C_LOAD, linewidth=1.5)
    ax_load.axhline(100, color='#e74c3c', linewidth=0.8, linestyle=':', alpha=0.6)
    ax_load.text(xlim[1] if len(t) > 0 else 1, 100, ' RATED', fontsize=7,
                 color='#e74c3c', va='center')
    ax_load.set_xlabel('Time (s)', fontsize=9, color=C_TEXT)
    ax_load.set_ylabel('%', fontsize=9, color=C_TEXT)
    ax_load.set_ylim(0, max(df['Load_Percent'].max() * 1.1, 110))

    if len(df) > 0:
        last = df.iloc[-1]
        ax_load.text(0.98, 0.95, f"Load: {last['Load_Percent']:.1f}%",
                     transform=ax_load.transAxes, fontsize=9, color=C_LOAD,
                     va='top', ha='right', fontweight='bold',
                     bbox=dict(boxstyle='round,pad=0.3', facecolor=C_BG, alpha=0.8,
                               edgecolor=C_GRID))

    # --- Panel 3: Cooling Status ---
    ax_cool.set_title('Cooling System', fontsize=11, fontweight='bold', color=C_TEXT)

    # Fan speed as area
    ax_cool.fill_between(t, df['Fan_1_Speed_Pct'], alpha=0.25, color=C_FAN_SPEED)
    ax_cool.plot(t, df['Fan_1_Speed_Pct'], color=C_FAN_SPEED, linewidth=1.5,
                 label='Fan 1 Speed')

    # Fan states as colored bands at bottom
    fan1_on = df['Fan_1_Active'].astype(bool)
    fan2_on = df['Fan_2_Active'].astype(bool)

    if fan1_on.any():
        ax_cool.fill_between(t, 0, 5, where=fan1_on, color=C_FAN1, alpha=0.6)
    if fan2_on.any():
        ax_cool.fill_between(t, 5, 10, where=fan2_on, color=C_FAN2, alpha=0.6)

    ax_cool.set_xlabel('Time (s)', fontsize=9, color=C_TEXT)
    ax_cool.set_ylabel('Speed %', fontsize=9, color=C_TEXT)
    ax_cool.set_ylim(0, 105)

    # Legend patches
    p1 = mpatches.Patch(color=C_FAN1, alpha=0.6, label='Fan 1 ON')
    p2 = mpatches.Patch(color=C_FAN2, alpha=0.6, label='Fan 2 ON')
    p3 = mpatches.Patch(color=C_FAN_SPEED, alpha=0.4, label='Fan Speed')
    ax_cool.legend(handles=[p3, p1, p2], loc='upper left', fontsize=7,
                   framealpha=0.3, facecolor=C_PANEL, edgecolor=C_GRID,
                   labelcolor=C_TEXT)

    # --- Panel 4: Alarms ---
    ax_alarm.set_title('Alarms & Protection', fontsize=11, fontweight='bold', color=C_TEXT)

    # Rate of temperature change
    if len(df) > 1:
        rate = df['Oil_Temp_C'].diff().fillna(0)
        ax_alarm.plot(t, rate, color=C_RATE, linewidth=1, label='dT/dt (°C/s)')
        ax_alarm.axhline(3.0, color=C_RATE, linewidth=0.8, linestyle=':', alpha=0.5)
        ax_alarm.text(xlim[1] if len(t) > 0 else 1, 3.0, ' RATE LIM',
                      fontsize=7, color=C_RATE, va='center')

    # Alarm bands
    crit = df['Critical_Alarm'].astype(bool)
    rate_al = df['Rate_Alarm'].astype(bool)

    if crit.any():
        ax_alarm.fill_between(t, ax_alarm.get_ylim()[0], ax_alarm.get_ylim()[1],
                              where=crit, color=C_ALARM, alpha=0.15)
    if rate_al.any():
        ax_alarm.fill_between(t, ax_alarm.get_ylim()[0], ax_alarm.get_ylim()[1],
                              where=rate_al, color=C_RATE, alpha=0.1)

    ax_alarm.set_xlabel('Time (s)', fontsize=9, color=C_TEXT)
    ax_alarm.set_ylabel('°C/s', fontsize=9, color=C_TEXT)

    # Status indicator
    if len(df) > 0:
        last = df.iloc[-1]
        if last.get('Critical_Alarm', 0):
            status_text, status_color = 'CRITICAL ALARM', C_ALARM
        elif last.get('Rate_Alarm', 0):
            status_text, status_color = 'RATE-OF-RISE', C_RATE
        else:
            status_text, status_color = 'NORMAL', '#2ecc71'

        ax_alarm.text(0.98, 0.95, status_text, transform=ax_alarm.transAxes,
                      fontsize=10, color=status_color, va='top', ha='right',
                      fontweight='bold',
                      bbox=dict(boxstyle='round,pad=0.4', facecolor=C_BG, alpha=0.9,
                                edgecolor=status_color, linewidth=1.5))

    # Scenario label
    scenario = df['Scenario'].iloc[-1] if 'Scenario' in df.columns and len(df) > 0 else '?'
    ax_alarm.text(0.02, 0.95, f'Scenario: {scenario}', transform=ax_alarm.transAxes,
                  fontsize=8, color=C_TEXT, va='top', ha='left',
                  bbox=dict(boxstyle='round,pad=0.3', facecolor=C_BG, alpha=0.7,
                            edgecolor=C_GRID))

    al_patches = [
        mpatches.Patch(color=C_ALARM, alpha=0.3, label='Critical Alarm'),
        mpatches.Patch(color=C_RATE, alpha=0.3, label='Rate Alarm'),
    ]
    ax_alarm.legend(handles=al_patches, loc='lower left', fontsize=7,
                    framealpha=0.3, facecolor=C_PANEL, edgecolor=C_GRID,
                    labelcolor=C_TEXT)


def run_live(csv_path, interval_ms=1000):
    """Run live-updating dashboard."""
    fig, axes = setup_figure()

    def update(frame):
        df = load_data(csv_path)
        if df is not None and len(df) > 1:
            draw_frame(df, axes)
            n = len(df)
            fig.suptitle(
                f'TRANSFORMER DIGITAL TWIN — LIVE TELEMETRY  [{n} samples]',
                fontsize=14, fontweight='bold', color=C_TEXT, y=0.98
            )

    # Initial draw
    df = load_data(csv_path)
    if df is not None and len(df) > 1:
        draw_frame(df, axes)

    ani = FuncAnimation(fig, update, interval=interval_ms, cache_frame_data=False)
    plt.show()


def run_static(csv_path):
    """Single render — useful for screenshots or post-run analysis."""
    df = load_data(csv_path)
    if df is None or len(df) < 2:
        print(f"[!] No data or too few rows in {csv_path}")
        return

    fig, axes = setup_figure()
    draw_frame(df, axes)
    n = len(df)
    fig.suptitle(
        f'TRANSFORMER DIGITAL TWIN — TELEMETRY REPORT  [{n} samples]',
        fontsize=14, fontweight='bold', color=C_TEXT, y=0.98
    )

    # Save PNG
    out_path = os.path.join(os.path.dirname(csv_path), 'telemetry_report.png')
    fig.savefig(out_path, dpi=150, facecolor=C_BG, bbox_inches='tight')
    print(f"[*] Saved: {out_path}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(description='Transformer Digital Twin — Live Dashboard')
    parser.add_argument('--file', default=DEFAULT_CSV, help='Path to telemetry CSV')
    parser.add_argument('--static', action='store_true', help='One-shot render (no auto-refresh)')
    parser.add_argument('--interval', type=int, default=1000, help='Refresh interval in ms (live mode)')
    args = parser.parse_args()

    csv_path = args.file
    print(f"[*] Dashboard reading from: {csv_path}")

    if args.static:
        run_static(csv_path)
    else:
        print(f"[*] Live mode — refreshing every {args.interval}ms. Close window to stop.")
        run_live(csv_path, args.interval)


if __name__ == "__main__":
    main()
