"""
analysis/plot_benchmark_results.py
=============================================================================
Comprehensive Empirical Visualization Suite for Master's Thesis Research.
Generates publication-ready figures (300 DPI, academic style) from consolidated
datasets in 'results/master/master_micro_metrics.csv' and 'master_macro_metrics.csv'.

Key Visualizations:
  1. 01_latency_decomposition_baseline.png: Stacked latency breakdown (Login vs Gen vs Target).
  2. 02_credential_lifecycle_comparison.png: Phase 1 (Init) vs Phase 2 (Renewal/Reuse) comparison.
  3. 03_thundering_herd_scalability.png: Latency vs Concurrency (1..50) with 95% CI.
  4. 04_resource_footprint.png: Memory heap overhead (tracemalloc) and wire traffic (L7 bytes).
=============================================================================
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import seaborn as sns

# Academic styling settings
plt.rcParams['font.sans-serif'] = 'DejaVu Sans'
plt.rcParams['axes.edgecolor'] = '#333333'
plt.rcParams['axes.linewidth'] = 0.8
plt.rcParams['grid.color'] = '#e0e0e0'
plt.rcParams['grid.linestyle'] = '--'
plt.rcParams['grid.alpha'] = 0.7

STRATEGY_PALETTE = {
    "STATIC_BASELINE": "#4b5563",    # Slate Grey
    "FEDERATED_OIDC": "#2563eb",     # Royal Blue
    "VAULT_STATIC_KV": "#d97706",    # Amber / Orange
    "VAULT_DYNAMIC_STS": "#7c3aed",  # Purple
    "VAULT_APPROLE": "#059669",      # Emerald Green
    "VAULT_OIDC": "#7c3aed",
}

STRATEGY_DISPLAY = {
    "STATIC_BASELINE": "Static Secrets\n(Baseline)",
    "FEDERATED_OIDC": "Direct OIDC\n(MinIO STS)",
    "VAULT_STATIC_KV": "Vault OIDC\n(Static KV v2)",
    "VAULT_DYNAMIC_STS": "Vault OIDC\n(Dynamic STS)",
}


def plot_latency_decomposition_baseline(df: pd.DataFrame, output_dir: str):
    """
    Figure 1: Stacked latency decomposition (Login vs Secret Gen vs S3 Target Call).
    """
    df_base = df[(df["scenario"] == "baseline") & (df["concurrency_level"] == 1)].copy()
    if df_base.empty:
        print("[!] Warning: No baseline data found for Fig 1.")
        return

    # Filter recognized strategies
    strats = [s for s in ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS"] if s in df_base["strategy"].unique()]
    if not strats:
        strats = df_base["strategy"].unique().tolist()

    grouped = df_base.groupby("strategy")[["auth_login_ms", "secret_gen_ms", "target_call_time_ms"]].mean().reindex(strats).fillna(0.0)

    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
    x = np.arange(len(strats))
    width = 0.45

    p1 = ax.bar(x, grouped["auth_login_ms"], width, label="Identity Login / JWT Auth", color="#3b82f6", edgecolor="black", alpha=0.9)
    p2 = ax.bar(x, grouped["secret_gen_ms"], width, bottom=grouped["auth_login_ms"], label="Secret Generation / KV Read", color="#f59e0b", edgecolor="black", alpha=0.9)
    p3 = ax.bar(x, grouped["target_call_time_ms"], width, bottom=grouped["auth_login_ms"] + grouped["secret_gen_ms"], label="MinIO S3 Target Call (SigV4)", color="#10b981", edgecolor="black", alpha=0.9)

    ax.set_ylabel("Latency (milliseconds)", fontsize=11, fontweight="bold")
    ax.set_title("Decomposition of Pipeline Request Latency (Baseline, c=1)", fontsize=12, fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([STRATEGY_DISPLAY.get(s, s) for s in strats], fontsize=10)
    ax.grid(axis="y", zorder=0)
    ax.legend(loc="upper right", frameon=True, fontsize=9)

    # Annotate total sum on top of bars
    for idx, total in enumerate(grouped.sum(axis=1)):
        ax.text(idx, total + 0.5, f"{total:.2f} ms", ha='center', va='bottom', fontsize=9, fontweight="bold")

    out_file = os.path.join(output_dir, "01_latency_decomposition_baseline.png")
    plt.tight_layout()
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"[+] Saved Figure 1: {out_file}")


def plot_credential_lifecycle_comparison(df: pd.DataFrame, output_dir: str):
    """
    Figure 2: Credential Lifecycle - Phase 1 (Initial Handshake) vs Phase 2 (Renewal / In-Memory Reuse).
    Highlights session token caching benefits in Vault vs repeated STS roundtrips in Direct OIDC.
    """
    df_life = df[df["scenario"].isin(["credential_lifecycle", "lifecycle", "high_freq"])].copy()
    if df_life.empty:
        print("[!] Notice: No credential_lifecycle data found. Run orchestrator with '--scenario credential_lifecycle' to generate Fig 2.")
        return

    strats = [s for s in ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS"] if s in df_life["strategy"].unique()]
    if not strats:
        strats = df_life["strategy"].unique().tolist()

    p1_means, p2_means = [], []
    for s in strats:
        s_data = df_life[df_life["strategy"] == s]
        p1 = s_data[s_data["iteration"] == 1]["auth_time_ms"].mean() if not s_data[s_data["iteration"] == 1].empty else 0.0
        p2 = s_data[s_data["iteration"] == 2]["auth_time_ms"].mean() if not s_data[s_data["iteration"] == 2].empty else 0.0
        p1_means.append(p1)
        p2_means.append(p2)

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=300)
    x = np.arange(len(strats))
    width = 0.35

    bars1 = ax.bar(x - width/2, p1_means, width, label="Phase 1: Initial Acquisition ($T_0$)", color="#3b82f6", edgecolor="black", alpha=0.9)
    bars2 = ax.bar(x + width/2, p2_means, width, label="Phase 2: Mid-Job Renewal / Reuse ($T_1$)", color="#10b981", edgecolor="black", alpha=0.9)

    ax.set_ylabel("Authentication Latency (milliseconds)", fontsize=11, fontweight="bold")
    ax.set_title("Credential Lifecycle: Initial Acquisition vs Secondary Renewal / Reuse", fontsize=12, fontweight="bold", pad=12)
    ax.set_xticks(x)
    ax.set_xticklabels([STRATEGY_DISPLAY.get(s, s) for s in strats], fontsize=10)
    ax.grid(axis="y", zorder=0)
    ax.legend(loc="upper right", frameon=True, fontsize=9)

    # Annotate values and calculate speedup
    for i in range(len(strats)):
        v1 = p1_means[i]
        v2 = p2_means[i]
        ax.text(x[i] - width/2, v1 + 0.4, f"{v1:.2f} ms", ha='center', va='bottom', fontsize=8, fontweight="bold")
        ax.text(x[i] + width/2, v2 + 0.4, f"{v2:.2f} ms", ha='center', va='bottom', fontsize=8, fontweight="bold")

        if v1 > 0 and v1 > v2 and (v1 - v2) > 0.5:
            speedup = (v1 - v2) / v1 * 100.0
            ax.annotate(
                f"-{speedup:.0f}%",
                xy=(x[i] + width/2, v2),
                xytext=(x[i] + width/2 + 0.15, max(v1, v2) * 0.6),
                arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.2),
                fontsize=9,
                fontweight="bold",
                color="#b91c1c"
            )

    out_file = os.path.join(output_dir, "02_credential_lifecycle_comparison.png")
    plt.tight_layout()
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"[+] Saved Figure 2 (Credential Lifecycle): {out_file}")


def plot_thundering_herd_scalability(df: pd.DataFrame, output_dir: str):
    """
    Figure 3: Scalability and Concurrency Contention (c=1, 10, 25, 50).
    """
    df_th = df[df["scenario"].isin(["baseline", "thundering_herd"])].copy()
    concs = sorted(df_th["concurrency_level"].unique())
    if len(concs) <= 1:
        print("[!] Notice: Thundering herd data not yet available for multiple concurrency levels. Run '--scenario thundering_herd'.")
        return

    fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
    strats = [s for s in ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS"] if s in df_th["strategy"].unique()]
    if not strats:
        strats = df_th["strategy"].unique().tolist()

    for s in strats:
        s_data = df_th[df_th["strategy"] == s]
        means = s_data.groupby("concurrency_level")["auth_time_ms"].mean()
        std = s_data.groupby("concurrency_level")["auth_time_ms"].std().fillna(0.0)

        color = STRATEGY_PALETTE.get(s, "#333333")
        label = STRATEGY_DISPLAY.get(s, s).replace("\n", " ")
        ax.plot(means.index, means.values, marker="o", linewidth=2, label=label, color=color)
        ax.fill_between(means.index, means.values - std.values, means.values + std.values, color=color, alpha=0.15)

    ax.set_xlabel("Concurrency Level (Simultaneous CI/CD Pipelines)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Authentication Latency (ms)", fontsize=11, fontweight="bold")
    ax.set_title("System Scalability Under Thundering Herd Contention (/dev/shm barrier)", fontsize=12, fontweight="bold", pad=12)
    ax.set_xticks(concs)
    ax.grid(True, zorder=0)
    ax.legend(loc="upper left", frameon=True, fontsize=9)

    out_file = os.path.join(output_dir, "03_thundering_herd_scalability.png")
    plt.tight_layout()
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"[+] Saved Figure 3 (Scalability): {out_file}")


def plot_resource_footprint(df: pd.DataFrame, output_dir: str):
    """
    Figure 4: Memory heap allocation (tracemalloc) and wire bytes transferred.
    """
    df_base = df[(df["scenario"] == "baseline") & (df["concurrency_level"] == 1)].copy()
    if df_base.empty:
        return

    strats = [s for s in ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_STATIC_KV", "VAULT_DYNAMIC_STS"] if s in df_base["strategy"].unique()]
    if not strats:
        strats = df_base["strategy"].unique().tolist()

    grouped_heap = df_base.groupby("strategy")["auth_heap_kb"].mean().reindex(strats).fillna(0.0)
    grouped_bytes = df_base.groupby("strategy")["bytes_received"].mean().reindex(strats).fillna(0.0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5), dpi=300)
    x = np.arange(len(strats))
    colors = [STRATEGY_PALETTE.get(s, "#333333") for s in strats]

    # Subplot 1: Heap allocation
    bars1 = ax1.bar(x, grouped_heap.values, color=colors, edgecolor="black", alpha=0.88)
    ax1.set_ylabel("Heap Allocated (KiB)", fontsize=10, fontweight="bold")
    ax1.set_title("Runner Python Heap Usage (tracemalloc)", fontsize=11, fontweight="bold")
    ax1.set_xticks(x)
    ax1.set_xticklabels([STRATEGY_DISPLAY.get(s, s) for s in strats], fontsize=9)
    ax1.grid(axis="y")
    for bar in bars1:
        h = bar.get_height()
        ax1.text(bar.get_x() + bar.get_width()/2., h + 0.1, f"{h:.1f} KB", ha='center', va='bottom', fontsize=8)

    # Subplot 2: Wire bytes received
    bars2 = ax2.bar(x, grouped_bytes.values, color=colors, edgecolor="black", alpha=0.88)
    ax2.set_ylabel("Wire Traffic Received (Bytes)", fontsize=10, fontweight="bold")
    ax2.set_title("Total L7 Network Wire Volume (Bytes)", fontsize=11, fontweight="bold")
    ax2.set_xticks(x)
    ax2.set_xticklabels([STRATEGY_DISPLAY.get(s, s) for s in strats], fontsize=9)
    ax2.grid(axis="y")
    for bar in bars2:
        h = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width()/2., h + 10, f"{int(h)} B", ha='center', va='bottom', fontsize=8)

    out_file = os.path.join(output_dir, "04_resource_footprint.png")
    plt.tight_layout()
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"[+] Saved Figure 4 (Resource Footprint): {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Empirical Benchmark Visualizer Suite")
    parser.add_argument("--results-dir", default=os.path.abspath("./results"), help="Results directory containing master/")
    parser.add_argument("--output-dir", default=None, help="Output directory for generated plots")
    args = parser.parse_args()

    micro_path = os.path.join(args.results_dir, "master", "master_micro_metrics.csv")
    if not os.path.isfile(micro_path):
        print(f"[!] Master micro file not found at: {micro_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or os.path.join(args.results_dir, "plots")
    os.makedirs(out_dir, exist_ok=True)

    df_micro = pd.read_csv(micro_path)
    print(f"[*] Loaded N={len(df_micro)} records from {micro_path}")

    plot_latency_decomposition_baseline(df_micro, out_dir)
    plot_credential_lifecycle_comparison(df_micro, out_dir)
    plot_thundering_herd_scalability(df_micro, out_dir)
    plot_resource_footprint(df_micro, out_dir)
    print(f"[✓] Visualizations successfully written to: {out_dir}")


if __name__ == "__main__":
    main()

