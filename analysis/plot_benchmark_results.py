#!/usr/bin/env python3
"""
analysis/plot_benchmark_results.py
=============================================================================
Publication-Grade Empirical Visualization Suite for Master's Thesis Research.
Designed according to Academic/IEEE & AGH UST styling guidelines.

Produces high-precision vector (SVG, PDF) and raster (PNG, 300 DPI) graphics:
  1. plot_01_credential_lifecycle: Initial vs Mid-Job Renewal (All 4 Strategies).
  2. plot_02_concurrency_scalability: Mean latency vs Concurrency (Log-scale, error bars).
  3. plot_03_tail_latency_percentiles: Avg, P95, P99 Tail Latency at high contention.
  4. plot_04_target_s3_latency: S3 ListBuckets target latency under contention.
  5. plot_05_client_resource_footprint: Memory heap overhead & CPU usage (tracemalloc).
  6. plot_06_server_telemetry_macro: Prometheus CPU & RAM footprint across containers.
  7. plot_07_network_wire_traffic: L7 wire volume and HTTP request efficiency.
  8. plot_08_master_combined_grid: Comprehensive 2x2 Master Summary Grid.
=============================================================================
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ===========================================================================
# 1. CONFIGURATION & SCIENTIFIC COLOR PALETTE
# ===========================================================================
STRATEGIES = {
    "STATIC_BASELINE": {
        "name": "Static Secrets (Baseline)",
        "short_name": "Static",
        "color": "#4b5563",     # Slate Grey
        "marker": "o",
        "linestyle": "-",
    },
    "FEDERATED_OIDC": {
        "name": "Direct OIDC (MinIO STS)",
        "short_name": "Direct OIDC",
        "color": "#2563eb",     # Royal Blue
        "marker": "s",
        "linestyle": "-",
    },
    "VAULT_STATIC_KV": {
        "name": "Vault OIDC (Static KV v2)",
        "short_name": "Vault KV",
        "color": "#d97706",     # Warm Amber / Orange
        "marker": "^",
        "linestyle": "-",
    },
    "VAULT_DYNAMIC_STS": {
        "name": "Vault OIDC (Dynamic STS)",
        "short_name": "Vault STS",
        "color": "#7c3aed",     # Deep Purple
        "marker": "D",
        "linestyle": "-",
    },
}

# Unified styling matching plot_scenario1.py guidelines
STYLE_CONFIG = {
    'font.size': 11,
    'axes.labelsize': 12,
    'axes.titlesize': 13,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'figure.titlesize': 15,
    'font.family': 'sans-serif',
    'font.sans-serif': ['DejaVu Sans', 'Helvetica', 'Arial'],
    'axes.edgecolor': '#333333',
    'axes.linewidth': 0.9,
    'grid.alpha': 0.35,
    'grid.color': '#d1d5db',
    'grid.linestyle': '--',
}


def apply_plot_style():
    plt.rcParams.update(STYLE_CONFIG)


def save_multiformat_figure(fig, base_path: str):
    """Saves figure in SVG, PDF (for LaTeX), and 300 DPI PNG formats."""
    os.makedirs(os.path.dirname(base_path), exist_ok=True)
    fig.savefig(f"{base_path}.svg", format='svg', bbox_inches='tight')
    fig.savefig(f"{base_path}.pdf", format='pdf', bbox_inches='tight')
    fig.savefig(f"{base_path}.png", format='png', dpi=300, bbox_inches='tight')
    print(f"  [+] Generated: {os.path.basename(base_path)} (.svg, .pdf, .png)")


# ===========================================================================
# 2. PLOT GENERATORS
# ===========================================================================

def plot_01_credential_lifecycle(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 1: Credential Lifecycle - Phase 1 (Initial Handshake) vs Phase 2 (Mid-Job Renewal / In-Memory Reuse).
    Visualizes session token caching amortization in Vault vs repeated STS roundtrips in Direct OIDC.
    """
    df = df_micro[df_micro["scenario"].isin(["credential_lifecycle", "lifecycle"])].copy()
    if df.empty:
        return

    fig, ax = plt.subplots(figsize=(9.5, 5.8))
    strategies = [s for s in STRATEGIES.keys() if s in df["strategy"].unique()]
    x = np.arange(len(strategies))
    width = 0.35

    p1_means, p2_means = [], []
    p1_stds, p2_stds = [], []

    for s in strategies:
        d1 = df[(df["strategy"] == s) & (df["iteration"] == 1)]["auth_time_ms"]
        d2 = df[(df["strategy"] == s) & (df["iteration"] == 2)]["auth_time_ms"]
        p1_means.append(d1.mean() if not d1.empty else 0.0)
        p2_means.append(d2.mean() if not d2.empty else 0.0)
        p1_stds.append(d1.std() if len(d1) > 1 else 0.0)
        p2_stds.append(d2.std() if len(d2) > 1 else 0.0)

    bars1 = ax.bar(x - width/2, p1_means, width, yerr=p1_stds, capsize=4,
                   label="Phase 1: Initial Acquisition ($T_0$)", color="#3b82f6", edgecolor="#1e3a8a", alpha=0.9)
    bars2 = ax.bar(x + width/2, p2_means, width, yerr=p2_stds, capsize=4,
                   label="Phase 2: Mid-Job Renewal / Reuse ($T_1$)", color="#10b981", edgecolor="#065f46", alpha=0.9)

    ax.set_ylabel("Authentication Latency [ms]", fontweight='bold')
    ax.set_title("Credential Lifecycle: Initial Handshake vs Mid-Job Renewal / In-Memory Reuse",
                 fontweight='bold', pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([STRATEGIES[s]["name"].replace(" (", "\n(") for s in strategies], fontweight='bold')
    ax.grid(axis='y', zorder=0)
    ax.tick_params(axis='both', which='both', direction='out', length=5, width=1.0)
    ax.legend(loc="upper right", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    # Annotate values and calculate speedup / slowdown
    for i in range(len(strategies)):
        v1 = p1_means[i]
        v2 = p2_means[i]
        y_pos1 = max(v1 + (p1_stds[i] or 0) + 0.5, 1.2)
        y_pos2 = max(v2 + (p2_stds[i] or 0) + 0.5, 1.2)
        ax.text(x[i] - width/2, y_pos1, f"{v1:.2f} ms", ha='center', va='bottom', fontsize=8.5, fontweight='bold')
        ax.text(x[i] + width/2, y_pos2, f"{v2:.2f} ms", ha='center', va='bottom', fontsize=8.5, fontweight='bold')

        if v1 > 0 and (v1 - v2) > 1.0:
            speedup = (v1 - v2) / v1 * 100.0
            ax.annotate(
                f"-{speedup:.1f}%\n(Amortized)",
                xy=(x[i] + width/2, v2),
                xytext=(x[i] + width/2 + 0.22, max(v1, v2) * 0.65),
                arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.3),
                fontsize=8.5,
                fontweight='bold',
                color="#b91c1c",
                ha='left'
            )
        elif v1 > 0 and (v2 - v1) > 2.0:
            slowdown = (v2 - v1) / v1 * 100.0
            ax.annotate(
                f"+{slowdown:.1f}%\n(No Cache)",
                xy=(x[i] + width/2, v2),
                xytext=(x[i] + width/2 + 0.22, max(v1, v2) * 0.75),
                arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.3),
                fontsize=8.5,
                fontweight='bold',
                color="#b91c1c",
                ha='left'
            )

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_01_credential_lifecycle"))
    plt.close(fig)


def plot_02_concurrency_scalability(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 2: Concurrency Scalability under Thundering Herd Contention.
    Logarithmic scale with mean markers and standard deviation error bars.
    """
    df = df_micro[df_micro["scenario"].isin(["baseline", "thundering_herd"])].copy()
    concs = sorted(df["concurrency_level"].astype(int).unique())
    if len(concs) <= 1:
        return

    fig, ax = plt.subplots(figsize=(9.5, 6.0))

    for s_key, meta in STRATEGIES.items():
        s_data = df[df["strategy"] == s_key]
        if s_data.empty:
            continue

        grouped = s_data.groupby("concurrency_level")["auth_time_ms"]
        means = grouped.mean().reindex(concs)
        stds = grouped.std().fillna(0.0).reindex(concs)

        ax.errorbar(
            concs, means.values, yerr=stds.values,
            label=meta["name"], color=meta["color"],
            fmt=f"{meta['marker']}{meta['linestyle']}",
            linewidth=2.2, markersize=7, markeredgecolor='white', markeredgewidth=1.2,
            capsize=4.5, elinewidth=1.4, alpha=0.95
        )

    ax.set_xlabel("Pipeline Concurrency Level ($c$) [Simultaneous Workers]", fontweight='bold')
    ax.set_ylabel("Authentication Latency [ms] (Log Scale)", fontweight='bold')
    ax.set_title("System Scalability Under Thundering Herd Contention (/dev/shm barrier)",
                 fontweight='bold', pad=12)
    ax.set_xticks(concs)
    ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())

    # Logarithmic scaling to visualize microsecond static baseline alongside 2.7s OIDC spike
    ax.set_yscale('log')
    ax.get_yaxis().set_major_formatter(plt.ScalarFormatter())
    ax.yaxis.set_minor_locator(plt.NullLocator())

    ax.grid(True, zorder=0)
    ax.tick_params(axis='both', which='both', direction='out', length=5.5, width=1.1, colors='#333333')
    ax.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_02_concurrency_scalability"))
    plt.close(fig)


def plot_03_tail_latency_percentiles(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 3: Tail Latency Evaluation (Mean, P95, P99) under Maximum Contention (c=50).
    Demonstrates SLA boundary predictability and risk exposure.
    """
    max_c = df_micro["concurrency_level"].astype(int).max()
    df_max = df_micro[(df_micro["concurrency_level"].astype(int) == max_c) & (df_micro["scenario"] == "thundering_herd")].copy()
    if df_max.empty:
        df_max = df_micro[df_micro["scenario"] == "thundering_herd"].copy()
        if df_max.empty:
            return

    strategies = [s for s in STRATEGIES.keys() if s in df_max["strategy"].unique()]
    x = np.arange(len(strategies))
    width = 0.25

    avg_vals, p95_vals, p99_vals = [], [], []
    for s in strategies:
        vals = df_max[df_max["strategy"] == s]["auth_time_ms"].dropna()
        avg_vals.append(vals.mean() if not vals.empty else 0.0)
        p95_vals.append(np.percentile(vals, 95) if not vals.empty else 0.0)
        p99_vals.append(np.percentile(vals, 99) if not vals.empty else 0.0)

    fig, ax = plt.subplots(figsize=(9.5, 6.0))

    ax.bar(x - width, avg_vals, width, label="Average (Mean)", color="#3b82f6", edgecolor="black", alpha=0.9)
    ax.bar(x, p95_vals, width, label="95th Percentile (P95)", color="#f59e0b", edgecolor="black", alpha=0.9)
    ax.bar(x + width, p99_vals, width, label="99th Percentile (P99 Tail)", color="#ef4444", edgecolor="black", alpha=0.9)

    ax.set_ylabel(f"Authentication Latency [ms] at $c={max_c}$ (Log Scale)", fontweight='bold')
    ax.set_title(f"Tail Latency & SLA Predictability under Maximum Contention ($c={max_c}$)",
                 fontweight='bold', pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')

    ax.set_yscale('log')
    ax.get_yaxis().set_major_formatter(plt.ScalarFormatter())
    ax.yaxis.set_minor_locator(plt.NullLocator())

    ax.grid(axis='y', zorder=0)
    ax.tick_params(axis='both', which='both', direction='out', length=5, width=1.0)
    ax.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    # Annotate P99 values
    for i in range(len(strategies)):
        ax.text(x[i] + width, p99_vals[i] * 1.15, f"{p99_vals[i]:.1f} ms",
                ha='center', va='bottom', fontsize=8, fontweight='bold', color="#b91c1c")

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_03_tail_latency_percentiles"))
    plt.close(fig)


def plot_04_target_s3_latency(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 4: Impact of Credential Strategy on Target S3 ListBuckets Latency under Contention.
    Exposes cross-service interference (e.g. MinIO STS queue starvation affecting S3 operations).
    """
    df = df_micro[df_micro["scenario"].isin(["baseline", "thundering_herd"])].copy()
    concs = sorted(df["concurrency_level"].astype(int).unique())
    if len(concs) <= 1:
        return

    fig, ax = plt.subplots(figsize=(9.5, 5.8))

    for s_key, meta in STRATEGIES.items():
        s_data = df[df["strategy"] == s_key]
        if s_data.empty:
            continue

        grouped = s_data.groupby("concurrency_level")["target_call_time_ms"]
        means = grouped.mean().reindex(concs)
        stds = grouped.std().fillna(0.0).reindex(concs)

        ax.errorbar(
            concs, means.values, yerr=stds.values,
            label=meta["name"], color=meta["color"],
            fmt=f"{meta['marker']}{meta['linestyle']}",
            linewidth=2.0, markersize=6.5, markeredgecolor='white', markeredgewidth=1.1,
            capsize=4.0, elinewidth=1.3, alpha=0.9
        )

    ax.set_xlabel("Pipeline Concurrency Level ($c$)", fontweight='bold')
    ax.set_ylabel("MinIO S3 Target Latency [ms]", fontweight='bold')
    ax.set_title("Downstream Target Operation Latency (MinIO S3 SigV4 ListBuckets)",
                 fontweight='bold', pad=12)
    ax.set_xticks(concs)
    ax.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax.grid(True, zorder=0)
    ax.tick_params(axis='both', which='both', direction='out', length=5, width=1.0)
    ax.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_04_target_s3_latency"))
    plt.close(fig)


def plot_05_client_resource_footprint(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 5: Runner Process Footprint - Heap Allocation (tracemalloc) and CPU user/sys time.
    """
    df = df_micro.copy()
    strategies = [s for s in STRATEGIES.keys() if s in df["strategy"].unique()]
    if not strategies:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))
    x = np.arange(len(strategies))
    colors = [STRATEGIES[s]["color"] for s in strategies]

    # Panel 1: Heap Allocation
    heap_means = [df[df["strategy"] == s]["auth_heap_kb"].mean() for s in strategies]
    bars1 = ax1.bar(x, heap_means, color=colors, edgecolor="black", alpha=0.88, width=0.5)
    ax1.set_ylabel("Python Heap Allocated [KiB]", fontweight='bold')
    ax1.set_title("A: Runner Client Heap Memory (tracemalloc)", fontweight='bold', pad=10)
    ax1.set_xticks(x)
    ax1.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')
    ax1.grid(axis='y', zorder=0)

    for bar in bars1:
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., h + 2.0, f"{h:.1f} KiB", ha='center', va='bottom', fontsize=9, fontweight='bold')

    # Panel 2: CPU User + System Time
    cpu_user = [df[df["strategy"] == s]["cpu_user_ms"].mean() for s in strategies]
    cpu_sys = [df[df["strategy"] == s]["cpu_sys_ms"].mean() for s in strategies]

    p_u = ax2.bar(x, cpu_user, width=0.5, label="User CPU Time", color="#3b82f6", edgecolor="black", alpha=0.9)
    p_s = ax2.bar(x, cpu_sys, width=0.5, bottom=cpu_user, label="Kernel Sys CPU Time", color="#f59e0b", edgecolor="black", alpha=0.9)

    ax2.set_ylabel("CPU Time [ms]", fontweight='bold')
    ax2.set_title("B: Runner CPU Consumption (User vs Kernel Sys)", fontweight='bold', pad=10)
    ax2.set_xticks(x)
    ax2.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')
    ax2.grid(axis='y', zorder=0)
    ax2.legend(loc="upper right", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    for i in range(len(strategies)):
        tot = cpu_user[i] + cpu_sys[i]
        ax2.text(x[i], tot + 0.3, f"{tot:.1f} ms", ha='center', va='bottom', fontsize=9, fontweight='bold')

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_05_client_resource_footprint"))
    plt.close(fig)


def plot_06_server_telemetry_macro(df_macro: pd.DataFrame, out_dir: str):
    """
    Figure 6: Multi-Container Telemetry (Prometheus) - Server CPU & RAM under Contention.
    Compares MinIO, Vault, and Runner container utilization.
    """
    if df_macro.empty:
        return

    df_th = df_macro[df_macro["scenario"].isin(["baseline", "thundering_herd"])].copy()
    concs = sorted(df_th["concurrency_level"].astype(int).unique())
    if len(concs) <= 1:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    # Panel A: Peak Server CPU Usage under Contention (c=10, 25, 50)
    for s_key in ["FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS", "STATIC_BASELINE"]:
        s_data = df_th[df_th["strategy"] == s_key]
        if s_data.empty:
            continue
        cpu_peak = s_data.groupby("concurrency_level")["minio_cpu_max_pct"].mean().reindex(concs)
        ax1.plot(concs, cpu_peak.values, marker=STRATEGIES[s_key]["marker"],
                 label=f"{STRATEGIES[s_key]['short_name']} (MinIO CPU)",
                 color=STRATEGIES[s_key]["color"], linewidth=2.0)

    # Add Vault CPU for Vault strategies
    for s_key in ["VAULT_STATIC_KV", "VAULT_DYNAMIC_STS"]:
        s_data = df_th[df_th["strategy"] == s_key]
        if s_data.empty:
            continue
        v_cpu = s_data.groupby("concurrency_level")["vault_cpu_max_pct"].mean().reindex(concs)
        ax1.plot(concs, v_cpu.values, marker="x", linestyle="--",
                 label=f"{STRATEGIES[s_key]['short_name']} (Vault CPU)",
                 color=STRATEGIES[s_key]["color"], alpha=0.7)

    ax1.set_xlabel("Concurrency Level ($c$)", fontweight='bold')
    ax1.set_ylabel("Peak Container CPU Usage [%]", fontweight='bold')
    ax1.set_title("A: Peak Server CPU Contention (Prometheus cAdvisor)", fontweight='bold', pad=10)
    ax1.set_xticks(concs)
    ax1.grid(True)
    ax1.legend(loc="upper left", fontsize=8.5, frameon=True)

    # Panel B: Memory Working Set
    for s_key in ["FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS", "STATIC_BASELINE"]:
        s_data = df_th[df_th["strategy"] == s_key]
        if s_data.empty:
            continue
        ram_peak = s_data.groupby("concurrency_level")["runner_ram_max_mb"].mean().reindex(concs)
        ax2.plot(concs, ram_peak.values, marker=STRATEGIES[s_key]["marker"],
                 label=f"{STRATEGIES[s_key]['short_name']} (Runner RAM)",
                 color=STRATEGIES[s_key]["color"], linewidth=2.0)

    ax2.set_xlabel("Concurrency Level ($c$)", fontweight='bold')
    ax2.set_ylabel("Max Memory Working Set [MB]", fontweight='bold')
    ax2.set_title("B: Runner Working Set Memory (Prometheus)", fontweight='bold', pad=10)
    ax2.set_xticks(concs)
    ax2.grid(True)
    ax2.legend(loc="upper left", fontsize=8.5, frameon=True)

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_06_server_telemetry_macro"))
    plt.close(fig)


def plot_07_network_wire_traffic(df_micro: pd.DataFrame, out_dir: str):
    """
    Figure 7: Protocol Efficiency - Wire Bytes Transferred & HTTP Request Decomposition.
    Contrasts XML Form-Data vs REST JSON payloads and request roundtrips without redundant annotations.
    """
    # Use baseline & initial acquisition data for deterministic protocol comparison
    df = df_micro[df_micro["iteration"].isin([1, None])].copy()
    if df.empty:
        df = df_micro.copy()

    strategies = [s for s in STRATEGIES.keys() if s in df["strategy"].unique()]
    if not strategies:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))
    x = np.arange(len(strategies))

    # Panel A: Wire Traffic Transferred (Bytes Sent TX vs Bytes Received RX)
    rx_bytes = [df[df["strategy"] == s]["bytes_received"].mean() for s in strategies]
    tx_bytes = [df[df["strategy"] == s]["bytes_sent"].mean() for s in strategies]

    ax1.bar(x - 0.18, tx_bytes, width=0.35, label="Bytes Sent (TX)", color="#3b82f6", edgecolor="black", alpha=0.9)
    ax1.bar(x + 0.18, rx_bytes, width=0.35, label="Bytes Received (RX)", color="#10b981", edgecolor="black", alpha=0.9)

    ax1.set_ylabel("L7 Wire Traffic [Bytes]", fontweight='bold')
    ax1.set_title("A: L7 Wire Traffic Volume (Headers + Payload)", fontweight='bold', pad=10)
    ax1.set_xticks(x)
    ax1.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')
    ax1.grid(axis='y', zorder=0)
    ax1.tick_params(axis='both', which='both', direction='out', length=5, width=1.0)
    ax1.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    # Panel B: Clean Stacked HTTP Roundtrip Decomposition (Auth vs Target S3)
    auth_reqs = [round(df[df["strategy"] == s]["auth_http_requests"].mean()) for s in strategies]
    target_reqs = [round(df[df["strategy"] == s]["target_http_requests"].mean()) for s in strategies]

    ax2.bar(x, auth_reqs, width=0.45, label="Authentication Requests", color="#f59e0b", edgecolor="black", alpha=0.9)
    ax2.bar(x, target_reqs, width=0.45, bottom=auth_reqs, label="Target S3 Requests", color="#6366f1", edgecolor="black", alpha=0.9)

    ax2.set_ylabel("HTTP Request Count [Integer]", fontweight='bold')
    ax2.set_title("B: HTTP Protocol Roundtrip Count (Auth vs Target)", fontweight='bold', pad=10)
    ax2.set_xticks(x)
    ax2.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')
    ax2.set_yticks([0, 1, 2, 3, 4])
    ax2.set_ylim(0, 3.8)
    ax2.grid(axis='y', zorder=0)
    ax2.tick_params(axis='both', which='both', direction='out', length=5, width=1.0)
    ax2.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0')

    plt.tight_layout()
    save_multiformat_figure(fig, os.path.join(out_dir, "plot_07_network_wire_traffic"))
    plt.close(fig)


def plot_08_master_combined_grid(df_micro: pd.DataFrame, df_macro: pd.DataFrame, out_dir: str):
    """
    Figure 8: Master Combined 2x2 Publication Figure.
    Modeled directly after plot_scenario1_combined.svg for comprehensive paper presentation.
    """
    fig, axs = plt.subplots(2, 2, figsize=(18, 12))
    apply_plot_style()

    # -----------------------------------------------------------------------
    # SUBPLOT A: Concurrency Scalability (Log Scale)
    # -----------------------------------------------------------------------
    ax_a = axs[0, 0]
    df_th = df_micro[df_micro["scenario"].isin(["baseline", "thundering_herd"])].copy()
    concs = sorted(df_th["concurrency_level"].astype(int).unique())

    for s_key, meta in STRATEGIES.items():
        s_data = df_th[df_th["strategy"] == s_key]
        if s_data.empty:
            continue
        grouped = s_data.groupby("concurrency_level")["auth_time_ms"]
        means = grouped.mean().reindex(concs)
        stds = grouped.std().fillna(0.0).reindex(concs)

        ax_a.errorbar(
            concs, means.values, yerr=stds.values,
            label=meta["name"], color=meta["color"],
            fmt=f"{meta['marker']}{meta['linestyle']}",
            linewidth=2.0, markersize=5.5, markeredgecolor='white',
            capsize=3.5, elinewidth=1.2
        )

    ax_a.set_xlabel("Concurrency Level ($c$)")
    ax_a.set_ylabel("Authentication Latency [ms] (Log Scale)")
    ax_a.set_title("A: Authentication Scalability under Contention", fontsize=12, fontweight='bold', pad=10)
    ax_a.set_xticks(concs)
    ax_a.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax_a.set_yscale('log')
    ax_a.get_yaxis().set_major_formatter(plt.ScalarFormatter())
    ax_a.yaxis.set_minor_locator(plt.NullLocator())
    ax_a.grid(True)
    ax_a.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0', fontsize=9)

    # -----------------------------------------------------------------------
    # SUBPLOT B: Credential Lifecycle (Phase 1 vs Phase 2)
    # -----------------------------------------------------------------------
    ax_b = axs[0, 1]
    df_life = df_micro[df_micro["scenario"].isin(["credential_lifecycle", "lifecycle"])].copy()
    strategies = [s for s in STRATEGIES.keys() if s in df_life["strategy"].unique()]
    x_b = np.arange(len(strategies))
    w_b = 0.35

    p1 = [df_life[(df_life["strategy"] == s) & (df_life["iteration"] == 1)]["auth_time_ms"].mean() for s in strategies]
    p2 = [df_life[(df_life["strategy"] == s) & (df_life["iteration"] == 2)]["auth_time_ms"].mean() for s in strategies]

    ax_b.bar(x_b - w_b/2, p1, w_b, label="Phase 1: Initial Acquisition", color="#3b82f6", edgecolor="black", alpha=0.9)
    ax_b.bar(x_b + w_b/2, p2, w_b, label="Phase 2: Mid-Job Renewal / Reuse", color="#10b981", edgecolor="black", alpha=0.9)
    ax_b.set_ylabel("Authentication Latency [ms]")
    ax_b.set_title("B: Credential Lifecycle Amortization", fontsize=12, fontweight='bold', pad=10)
    ax_b.set_xticks(x_b)
    ax_b.set_xticklabels([STRATEGIES[s]["short_name"] for s in strategies], fontweight='bold')
    ax_b.grid(axis='y')
    ax_b.legend(loc="upper right", frameon=True, facecolor='white', edgecolor='#e0e0e0', fontsize=9)

    for i in range(len(strategies)):
        if p1[i] > 0 and (p1[i] - p2[i]) > 1.0:
            pct = (p1[i] - p2[i]) / p1[i] * 100.0
            ax_b.text(x_b[i] + w_b/2, p2[i] + 0.8, f"-{pct:.0f}%", ha='center', va='bottom', fontsize=8.5, fontweight='bold', color="#b91c1c")

    # -----------------------------------------------------------------------
    # SUBPLOT C: Downstream Target Call (S3 SigV4 ListBuckets)
    # -----------------------------------------------------------------------
    ax_c = axs[1, 0]
    for s_key, meta in STRATEGIES.items():
        s_data = df_th[df_th["strategy"] == s_key]
        if s_data.empty:
            continue
        means_s3 = s_data.groupby("concurrency_level")["target_call_time_ms"].mean().reindex(concs)
        ax_c.plot(concs, means_s3.values, marker=meta["marker"], color=meta["color"], linewidth=1.8, label=meta["name"])

    ax_c.set_xlabel("Concurrency Level ($c$)")
    ax_c.set_ylabel("S3 ListBuckets Latency [ms]")
    ax_c.set_title("C: Target S3 Operation Latency under Contention", fontsize=12, fontweight='bold', pad=10)
    ax_c.set_xticks(concs)
    ax_c.get_xaxis().set_major_formatter(plt.ScalarFormatter())
    ax_c.grid(True)
    ax_c.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0', fontsize=9)

    # -----------------------------------------------------------------------
    # SUBPLOT D: Total Pipeline Duration (Prometheus Telemetry)
    # -----------------------------------------------------------------------
    ax_d = axs[1, 1]
    if not df_macro.empty:
        df_m_th = df_macro[df_macro["scenario"].isin(["baseline", "thundering_herd"])].copy()
        concs_m = sorted(df_m_th["concurrency_level"].astype(int).unique())
        for s_key, meta in STRATEGIES.items():
            s_data = df_m_th[df_m_th["strategy"] == s_key]
            if s_data.empty:
                continue
            durations = s_data.groupby("concurrency_level")["total_pipeline_duration_s"].mean().reindex(concs_m)
            ax_d.plot(concs_m, durations.values, marker=meta["marker"], color=meta["color"], linewidth=1.8, label=meta["name"])

        ax_d.set_xlabel("Concurrency Level ($c$)")
        ax_d.set_ylabel("Total Pipeline Duration [seconds]")
        ax_d.set_title("D: Macro Pipeline Duration (Prometheus)", fontsize=12, fontweight='bold', pad=10)
        ax_d.set_xticks(concs_m)
        ax_d.get_xaxis().set_major_formatter(plt.ScalarFormatter())
        ax_d.grid(True)
        ax_d.legend(loc="upper left", frameon=True, facecolor='white', edgecolor='#e0e0e0', fontsize=9)

    fig.suptitle("Master Benchmark: Empirical Performance, Lifecycle Dynamics, and Concurrency Scalability",
                 fontsize=16, fontweight='bold', y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    save_multiformat_figure(fig, os.path.join(out_dir, "plot_08_master_combined_grid"))
    plt.close(fig)


# ===========================================================================
# 3. MAIN RUNNER
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="Publication-Grade Benchmark Plotting Suite")
    parser.add_argument("--results-dir", default=os.path.abspath("./results"), help="Results directory containing master/")
    parser.add_argument("--output-dir", default=None, help="Output directory for generated plots")
    args = parser.parse_args()

    apply_plot_style()

    micro_path = os.path.join(args.results_dir, "master", "master_micro_metrics.csv")
    macro_path = os.path.join(args.results_dir, "master", "master_macro_metrics.csv")

    if not os.path.isfile(micro_path):
        print(f"[!] Error: {micro_path} not found. Run orchestrator first!", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or os.path.join(args.results_dir, "plots")
    os.makedirs(out_dir, exist_ok=True)

    df_micro = pd.read_csv(micro_path)
    df_macro = pd.read_csv(macro_path) if os.path.isfile(macro_path) else pd.DataFrame()

    print("=" * 80)
    print(f"[*] EMPIRICAL BENCHMARK VISUALIZATION SUITE (Publication Vector Graphics)")
    print(f"    Micro-Telemetry: {len(df_micro)} rows from {micro_path}")
    print(f"    Macro-Telemetry: {len(df_macro)} rows from {macro_path}")
    print(f"    Output Formats:  .svg (Vector), .pdf (LaTeX Vector), .png (300 DPI Raster)")
    print(f"    Target Folder:   {out_dir}")
    print("=" * 80)

    plot_01_credential_lifecycle(df_micro, out_dir)
    plot_02_concurrency_scalability(df_micro, out_dir)
    plot_03_tail_latency_percentiles(df_micro, out_dir)
    plot_04_target_s3_latency(df_micro, out_dir)
    plot_05_client_resource_footprint(df_micro, out_dir)
    plot_06_server_telemetry_macro(df_macro, out_dir)
    plot_07_network_wire_traffic(df_micro, out_dir)
    plot_08_master_combined_grid(df_micro, df_macro, out_dir)

    print("=" * 80)
    print(f"[✓] Successfully generated complete suite of 8 academic publication figures!")
    print(f"    Ready for inclusion into LaTeX master's thesis ({out_dir}/*.pdf)")
    print("=" * 80)


if __name__ == "__main__":
    main()
