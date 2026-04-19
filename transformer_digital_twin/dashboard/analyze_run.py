"""
Post-Run Analysis — Transformer Digital Twin
=============================================
Generates a summary report and multi-panel figure from a completed run.
Calculates key metrics: peak temps, thermal time constants, cooling
effectiveness, energy balance.

Usage:
  python analyze_run.py                    # analyze latest telemetry.csv
  python analyze_run.py --file <path>      # analyze specific log file
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', 'data')
DEFAULT_CSV = os.path.join(DATA_DIR, 'telemetry.csv')

# Thermal model parameters (must match digital_twin.py)
C_THERMAL = 45.0            # kJ/°C
P_CORE_LOSS = 1.3           # kW
P_LOAD_LOSS_RATED = 6.5     # kW
K_NATURAL = 0.08            # kW/°C
K_FAN1_MAX = 0.25           # kW/°C
K_FAN2 = 0.18               # kW/°C


def load_and_validate(csv_path):
    """Load CSV and normalize columns."""
    df = pd.read_csv(csv_path)

    # Handle old format
    if 'Core_Temp_C' in df.columns and 'Oil_Temp_C' not in df.columns:
        df = df.rename(columns={'Core_Temp_C': 'Oil_Temp_C'})
    for col, default in [('Winding_Temp_C', None), ('Ambient_C', 25.0),
                         ('Fan_1_Speed_Pct', None), ('Rate_Alarm', 0),
                         ('Cooling_Effort_Pct', None), ('Scenario', 'unknown')]:
        if col not in df.columns:
            if col == 'Winding_Temp_C':
                df[col] = df['Oil_Temp_C'] + 5.0
            elif col == 'Fan_1_Speed_Pct':
                df[col] = df.get('Fan_1_Active', 0) * 100
            elif col == 'Cooling_Effort_Pct':
                df[col] = df.get('Fan_1_Speed_Pct', 0)
            else:
                df[col] = default

    df['t'] = range(len(df))
    return df


def compute_metrics(df):
    """Calculate key performance metrics from telemetry."""
    m = {}
    n = len(df)
    m['duration_s'] = n
    m['scenario'] = df['Scenario'].iloc[-1] if 'Scenario' in df.columns else 'unknown'

    # Temperature metrics
    m['oil_peak'] = df['Oil_Temp_C'].max()
    m['oil_peak_time'] = df['Oil_Temp_C'].idxmax()
    m['oil_final'] = df['Oil_Temp_C'].iloc[-1]
    m['oil_mean'] = df['Oil_Temp_C'].mean()
    m['winding_peak'] = df['Winding_Temp_C'].max()
    m['winding_final'] = df['Winding_Temp_C'].iloc[-1]

    # Load metrics
    m['load_peak'] = df['Load_Percent'].max()
    m['load_mean'] = df['Load_Percent'].mean()
    m['overload_seconds'] = (df['Load_Percent'] > 100).sum()

    # Cooling metrics
    m['fan1_on_pct'] = (df['Fan_1_Active'] > 0).mean() * 100
    m['fan2_on_pct'] = (df['Fan_2_Active'] > 0).mean() * 100
    m['avg_fan_speed'] = df['Fan_1_Speed_Pct'].mean()
    m['max_fan_speed'] = df['Fan_1_Speed_Pct'].max()

    # Alarm metrics
    m['critical_alarm_seconds'] = (df['Critical_Alarm'] > 0).sum()
    m['rate_alarm_seconds'] = (df['Rate_Alarm'] > 0).sum()

    # Rate of rise
    rate = df['Oil_Temp_C'].diff().fillna(0)
    m['max_rate_rise'] = rate.max()
    m['max_rate_fall'] = rate.min()

    # Energy estimate (simplified)
    loads = df['Load_Percent'] / 100.0
    q_gen = (P_CORE_LOSS + P_LOAD_LOSS_RATED * loads ** 2).sum()  # kJ total
    m['total_heat_generated_kJ'] = q_gen

    # Thermal time constant estimate (63.2% of step response)
    # Find the longest monotonic rise and estimate tau
    rises = rate > 0
    if rises.any():
        groups = (rises != rises.shift()).cumsum()
        rise_groups = df[rises].groupby(groups[rises])
        if len(rise_groups) > 0:
            longest = max(rise_groups, key=lambda g: len(g[1]))
            group_df = longest[1]
            if len(group_df) > 5:
                start_temp = group_df['Oil_Temp_C'].iloc[0]
                end_temp = group_df['Oil_Temp_C'].iloc[-1]
                target = start_temp + 0.632 * (end_temp - start_temp)
                crossed = group_df[group_df['Oil_Temp_C'] >= target]
                if len(crossed) > 0:
                    m['est_thermal_tau_s'] = crossed.index[0] - group_df.index[0]
                else:
                    m['est_thermal_tau_s'] = None
            else:
                m['est_thermal_tau_s'] = None
        else:
            m['est_thermal_tau_s'] = None
    else:
        m['est_thermal_tau_s'] = None

    return m


def print_report(m):
    """Print formatted text report."""
    print("\n" + "=" * 60)
    print("  TRANSFORMER DIGITAL TWIN — RUN ANALYSIS REPORT")
    print("=" * 60)
    print(f"  Scenario:  {m['scenario']}")
    print(f"  Duration:  {m['duration_s']}s")
    print()
    print("  TEMPERATURES")
    print(f"    Oil peak:     {m['oil_peak']:.1f}°C  (at t={m['oil_peak_time']}s)")
    print(f"    Oil final:    {m['oil_final']:.1f}°C")
    print(f"    Oil mean:     {m['oil_mean']:.1f}°C")
    print(f"    Winding peak: {m['winding_peak']:.1f}°C")
    print(f"    Winding final:{m['winding_final']:.1f}°C")
    print(f"    Max dT/dt:    +{m['max_rate_rise']:.2f}°C/s  / {m['max_rate_fall']:.2f}°C/s")
    if m.get('est_thermal_tau_s') is not None:
        print(f"    Est. tau:     ~{m['est_thermal_tau_s']}s")
    print()
    print("  LOAD")
    print(f"    Peak load:    {m['load_peak']:.1f}%")
    print(f"    Mean load:    {m['load_mean']:.1f}%")
    print(f"    Overload:     {m['overload_seconds']}s above 100%")
    print()
    print("  COOLING")
    print(f"    Fan 1 duty:   {m['fan1_on_pct']:.1f}% of run")
    print(f"    Fan 2 duty:   {m['fan2_on_pct']:.1f}% of run")
    print(f"    Avg fan spd:  {m['avg_fan_speed']:.1f}%")
    print()
    print("  ALARMS")
    print(f"    Critical:     {m['critical_alarm_seconds']}s")
    print(f"    Rate-of-rise: {m['rate_alarm_seconds']}s")
    print()
    print(f"  Total heat generated: ~{m['total_heat_generated_kJ']:.0f} kJ")
    print("=" * 60)


def plot_report(df, m, save_path=None):
    """Generate 6-panel analysis figure."""
    fig, axes = plt.subplots(3, 2, figsize=(15, 10), facecolor='#1a1a2e')
    fig.suptitle(
        f"TRANSFORMER DIGITAL TWIN — POST-RUN ANALYSIS  "
        f"[{m['scenario']}  |  {m['duration_s']}s  |  Peak oil: {m['oil_peak']:.1f}°C]",
        fontsize=13, fontweight='bold', color='#e0e0e0', y=0.98
    )
    fig.subplots_adjust(hspace=0.4, wspace=0.25, top=0.93, bottom=0.06,
                        left=0.07, right=0.97)

    t = df['t']
    C_BG = '#1a1a2e'
    C_PANEL = '#16213e'
    C_TEXT = '#e0e0e0'
    C_GRID = '#2a2a4a'

    for ax in axes.flat:
        ax.set_facecolor(C_PANEL)
        ax.tick_params(colors=C_TEXT, labelsize=8)
        ax.grid(True, color=C_GRID, alpha=0.5, linewidth=0.5)
        for spine in ax.spines.values():
            spine.set_color(C_GRID)

    # 1. Temperature overview
    ax = axes[0, 0]
    ax.set_title('Temperature Curves', fontsize=10, fontweight='bold', color=C_TEXT)
    ax.plot(t, df['Oil_Temp_C'], color='#e74c3c', linewidth=1.5, label='Oil')
    ax.plot(t, df['Winding_Temp_C'], color='#e67e22', linewidth=1.5, label='Winding')
    ax.plot(t, df['Ambient_C'], color='#3498db', linewidth=1, linestyle='--', label='Ambient')
    ax.axhline(105, color='#e74c3c', linewidth=0.7, linestyle=':', alpha=0.5)
    ax.axhline(90, color='#f39c12', linewidth=0.7, linestyle=':', alpha=0.5)
    ax.axhline(72, color='#1abc9c', linewidth=0.7, linestyle=':', alpha=0.5)
    ax.set_xlabel('Time (s)', color=C_TEXT, fontsize=8)
    ax.set_ylabel('°C', color=C_TEXT, fontsize=8)
    ax.legend(fontsize=7, framealpha=0.3, facecolor=C_PANEL, edgecolor=C_GRID, labelcolor=C_TEXT)

    # 2. Load profile
    ax = axes[0, 1]
    ax.set_title('Load Profile', fontsize=10, fontweight='bold', color=C_TEXT)
    ax.fill_between(t, df['Load_Percent'], alpha=0.3, color='#2ecc71')
    ax.plot(t, df['Load_Percent'], color='#2ecc71', linewidth=1.5)
    ax.axhline(100, color='#e74c3c', linewidth=0.8, linestyle=':', alpha=0.5)
    ax.set_xlabel('Time (s)', color=C_TEXT, fontsize=8)
    ax.set_ylabel('%', color=C_TEXT, fontsize=8)

    # 3. Rate of change
    ax = axes[1, 0]
    ax.set_title('Temperature Rate of Change (dT/dt)', fontsize=10, fontweight='bold', color=C_TEXT)
    rate = df['Oil_Temp_C'].diff().fillna(0)
    colors = ['#e74c3c' if r > 0 else '#3498db' for r in rate]
    ax.bar(t, rate, color=colors, width=1.0, alpha=0.7)
    ax.axhline(3.0, color='#e67e22', linewidth=0.8, linestyle=':', alpha=0.6)
    ax.axhline(-3.0, color='#e67e22', linewidth=0.8, linestyle=':', alpha=0.6)
    ax.axhline(0, color=C_TEXT, linewidth=0.5, alpha=0.3)
    ax.set_xlabel('Time (s)', color=C_TEXT, fontsize=8)
    ax.set_ylabel('°C/s', color=C_TEXT, fontsize=8)

    # 4. Cooling effort
    ax = axes[1, 1]
    ax.set_title('Cooling Effort & Fan States', fontsize=10, fontweight='bold', color=C_TEXT)
    ax.fill_between(t, df['Fan_1_Speed_Pct'], alpha=0.3, color='#9b59b6')
    ax.plot(t, df['Fan_1_Speed_Pct'], color='#9b59b6', linewidth=1.5, label='Fan Speed')
    fan2_pct = df['Fan_2_Active'] * 100
    ax.plot(t, fan2_pct, color='#f39c12', linewidth=1, linestyle='--', label='Fan 2 (ON/OFF)')
    ax.set_xlabel('Time (s)', color=C_TEXT, fontsize=8)
    ax.set_ylabel('%', color=C_TEXT, fontsize=8)
    ax.set_ylim(0, 105)
    ax.legend(fontsize=7, framealpha=0.3, facecolor=C_PANEL, edgecolor=C_GRID, labelcolor=C_TEXT)

    # 5. Oil temp vs load scatter (heat map style)
    ax = axes[2, 0]
    ax.set_title('Oil Temp vs Load (phase space)', fontsize=10, fontweight='bold', color=C_TEXT)
    scatter = ax.scatter(df['Load_Percent'], df['Oil_Temp_C'], c=t, cmap='plasma',
                         s=3, alpha=0.7)
    ax.set_xlabel('Load %', color=C_TEXT, fontsize=8)
    ax.set_ylabel('Oil Temp °C', color=C_TEXT, fontsize=8)
    cb = fig.colorbar(scatter, ax=ax, label='Time (s)')
    cb.ax.yaxis.label.set_color(C_TEXT)
    cb.ax.tick_params(colors=C_TEXT, labelsize=7)

    # 6. Energy balance (cumulative)
    ax = axes[2, 1]
    ax.set_title('Cumulative Energy Balance', fontsize=10, fontweight='bold', color=C_TEXT)
    loads = df['Load_Percent'] / 100.0
    q_gen_cum = (P_CORE_LOSS + P_LOAD_LOSS_RATED * loads ** 2).cumsum()
    # Estimate cooling from temperature data
    delta_t_arr = df['Oil_Temp_C'] - df['Ambient_C']
    q_cool_est = (K_NATURAL * delta_t_arr +
                  K_FAN1_MAX * (df['Fan_1_Speed_Pct'] / 100.0) * df['Fan_1_Active'] * delta_t_arr +
                  K_FAN2 * df['Fan_2_Active'] * delta_t_arr).cumsum()
    ax.plot(t, q_gen_cum, color='#e74c3c', linewidth=1.5, label='Heat In')
    ax.plot(t, q_cool_est, color='#3498db', linewidth=1.5, label='Heat Out (est.)')
    ax.fill_between(t, q_gen_cum, q_cool_est, alpha=0.15,
                    color='#e74c3c' if q_gen_cum.iloc[-1] > q_cool_est.iloc[-1] else '#3498db')
    ax.set_xlabel('Time (s)', color=C_TEXT, fontsize=8)
    ax.set_ylabel('kJ', color=C_TEXT, fontsize=8)
    ax.legend(fontsize=7, framealpha=0.3, facecolor=C_PANEL, edgecolor=C_GRID, labelcolor=C_TEXT)

    if save_path:
        fig.savefig(save_path, dpi=150, facecolor=C_BG, bbox_inches='tight')
        print(f"[*] Report saved: {save_path}")

    plt.show()


def main():
    parser = argparse.ArgumentParser(description='Transformer Digital Twin — Post-Run Analysis')
    parser.add_argument('--file', default=DEFAULT_CSV, help='Path to telemetry CSV')
    parser.add_argument('--no-plot', action='store_true', help='Text report only, no figure')
    args = parser.parse_args()

    print(f"[*] Analyzing: {args.file}")
    df = load_and_validate(args.file)
    m = compute_metrics(df)
    print_report(m)

    if not args.no_plot:
        save_path = os.path.splitext(args.file)[0] + '_report.png'
        plot_report(df, m, save_path)


if __name__ == "__main__":
    main()
