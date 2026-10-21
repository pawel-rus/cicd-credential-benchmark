"""
analysis/generate_plots.py
Automated Statistical Analysis and Publication-Grade Plot Generator (v1.0).
Parses consolidated Master CSV datasets from 'results/master/' and generates:
  1. Baseline Latency Decomposition (Stacked Bar Chart: Login vs Secret Gen vs S3 Target)
  2. Baseline Total Operation Latency (Bar Chart with StdDev Error Bars & Data Points)
  3. Kernel Resource Accounting (Process CPU User/Sys Time & RAM High-Water Mark)
  4. Container Telemetry Overview (Prometheus cAdvisor CPU & RAM per Container)
  5. Concurrency Scalability Curves (Line Plot for Thundering Herd c=1..50)
  6. Consolidated Statistical Summary Table (CSV with Mean, Std, Median, P95, P99)
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Academic styling palette
STRATEGY_ORDER = ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_APPROLE", "VAULT_OIDC"]
STRATEGY_LABELS = {
    "STATIC_BASELINE": "Static Baseline\n(RAM / os.environ)",
    "FEDERATED_OIDC": "Federated OIDC\n(GitLab -> MinIO STS)",
    "VAULT_APPROLE": "Vault AppRole\n(Static Role/Secret ID)",
    "VAULT_OIDC": "Vault OIDC\n(JWT -> Vault -> MinIO)",
}
STRATEGY_COLORS = {
    "STATIC_BASELINE": "#6c757d",   # Sleek Slate Gray
    "FEDERATED_OIDC": "#0d6efd",    # Vibrant Tech Blue
    "VAULT_APPROLE": "#fd7e14",     # Energetic Amber/Orange
    "VAULT_OIDC": "#6f42c1",        # Royal Purple
}

def apply_plot_style():
    sns.set_theme(style="whitegrid")
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.labelsize": 11,
        "axes.labelweight": "semibold",
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.titlesize": 14,
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
    })

def plot_baseline_latency_breakdown(df: pd.DataFrame, output_dir: str):
    """
    Stacked Bar Chart decomposing total latency into:
      - auth_login_ms (Identity verification / initial handshake)
      - secret_gen_ms (Dynamic credential creation / retrieval)
      - target_call_time_ms (MinIO S3 ListBuckets via SigV4)
    """
    baseline_df = df[df["scenario"] == "baseline"]
    if baseline_df.empty:
        return

    grouped = baseline_df.groupby("strategy")[["auth_login_ms", "secret_gen_ms", "target_call_time_ms"]].mean()
    # Reindex according to defined order
    existing_strats = [s for s in STRATEGY_ORDER if s in grouped.index]
    grouped = grouped.loc[existing_strats]

    fig, ax = plt.subplots(figsize=(9, 6))

    labels = [STRATEGY_LABELS.get(s, s) for s in existing_strats]
    x = np.arange(len(labels))
    width = 0.55

    login_vals = grouped["auth_login_ms"].values
    gen_vals = grouped["secret_gen_ms"].values
    target_vals = grouped["target_call_time_ms"].values

    p1 = ax.bar(x, login_vals, width, label="1. Auth Login / Exchange (ms)", color="#3b82f6", edgecolor="black", linewidth=0.7)
    p2 = ax.bar(x, gen_vals, width, bottom=login_vals, label="2. Secret Gen / Retrieval (ms)", color="#f59e0b", edgecolor="black", linewidth=0.7)
    p3 = ax.bar(x, target_vals, width, bottom=login_vals + gen_vals, label="3. Target S3 ListBuckets (ms)", color="#10b981", edgecolor="black", linewidth=0.7)

    # Annotate total values on top of bars
    total_vals = login_vals + gen_vals + target_vals
    for i, tot in enumerate(total_vals):
        ax.annotate(f"{tot:.1f} ms",
                    xy=(i, tot),
                    xytext=(0, 4),
                    textcoords="offset points",
                    ha="center", va="bottom",
                    fontweight="bold")

    ax.set_ylabel("Latency (ms)")
    ax.set_title("Decomposition of Latency: Authentication vs Secret vs S3 Target (Baseline)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc="upper left", frameon=True, framealpha=0.9)
    plt.tight_layout()

    out_file = os.path.join(output_dir, "01_baseline_latency_breakdown.png")
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")

def plot_baseline_total_latency(df: pd.DataFrame, output_dir: str):
    """
    Bar Chart with standard deviation error bars and jittered scatter points.
    """
    baseline_df = df[df["scenario"] == "baseline"]
    if baseline_df.empty:
        return

    existing_strats = [s for s in STRATEGY_ORDER if s in baseline_df["strategy"].unique()]
    
    stats = baseline_df.groupby("strategy")["total_op_time_ms"].agg(["mean", "std", "count"]).loc[existing_strats]
    stats["std"] = stats["std"].fillna(0.0)

    fig, ax = plt.subplots(figsize=(8.5, 5.5))

    x = np.arange(len(existing_strats))
    width = 0.5
    colors = [STRATEGY_COLORS.get(s, "#6c757d") for s in existing_strats]

    bars = ax.bar(
        x,
        stats["mean"].values,
        width,
        yerr=stats["std"].values,
        capsize=6,
        color=colors,
        edgecolor="black",
        linewidth=0.8,
        alpha=0.85,
        error_kw={"elinewidth": 1.5, "capthick": 1.5}
    )

    # Overlay scatter points for actual observations
    for idx, strat in enumerate(existing_strats):
        y_pts = baseline_df[baseline_df["strategy"] == strat]["total_op_time_ms"]
        x_jitter = np.random.normal(idx, 0.04, size=len(y_pts))
        ax.scatter(x_jitter, y_pts, color="black", s=25, alpha=0.7, zorder=3)

    for i, row in enumerate(stats.itertuples()):
        mean_val = row.mean
        std_val = row.std
        ax.annotate(f"{mean_val:.1f} ± {std_val:.1f} ms\n(N={row.count})",
                    xy=(i, mean_val + std_val),
                    xytext=(0, 6),
                    textcoords="offset points",
                    ha="center", va="bottom",
                    fontsize=9,
                    fontweight="bold")

    ax.set_ylabel("Total Operation Latency (ms)")
    ax.set_title("Total Latency Comparison with Error Bars (StdDev, N Samples)")
    ax.set_xticks(x)
    ax.set_xticklabels([STRATEGY_LABELS.get(s, s) for s in existing_strats])
    plt.tight_layout()

    out_file = os.path.join(output_dir, "02_baseline_total_latency_errorbars.png")
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")

def plot_process_kernel_resources(df: pd.DataFrame, output_dir: str):
    """
    Subplots comparing CPU Time (ru_utime + ru_stime) and Peak RAM (ru_maxrss).
    """
    baseline_df = df[df["scenario"] == "baseline"]
    if baseline_df.empty:
        return

    existing_strats = [s for s in STRATEGY_ORDER if s in baseline_df["strategy"].unique()]
    baseline_df["cpu_total_ms"] = baseline_df["cpu_user_ms"] + baseline_df["cpu_sys_ms"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # CPU Subplot
    cpu_stats = baseline_df.groupby("strategy")["cpu_total_ms"].mean().loc[existing_strats]
    colors = [STRATEGY_COLORS.get(s, "#6c757d") for s in existing_strats]
    ax1.bar(range(len(existing_strats)), cpu_stats.values, color=colors, edgecolor="black", alpha=0.85, width=0.5)
    for i, v in enumerate(cpu_stats.values):
        ax1.annotate(f"{v:.2f} ms", (i, v), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontweight="bold")
    ax1.set_title("Process CPU Consumption (Kernel + User)")
    ax1.set_ylabel("CPU Time Delta (ms)")
    ax1.set_xticks(range(len(existing_strats)))
    ax1.set_xticklabels([s.replace("_", "\n") for s in existing_strats], fontsize=9)

    # RAM Peak Subplot
    ram_stats = (baseline_df.groupby("strategy")["ram_peak_kb"].mean() / 1024.0).loc[existing_strats]
    ax2.bar(range(len(existing_strats)), ram_stats.values, color=colors, edgecolor="black", alpha=0.85, width=0.5)
    for i, v in enumerate(ram_stats.values):
        ax2.annotate(f"{v:.1f} MB", (i, v), xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontweight="bold")
    ax2.set_title("Runner Peak Memory Footprint (ru_maxrss)")
    ax2.set_ylabel("Peak RAM (MB)")
    ax2.set_xticks(range(len(existing_strats)))
    ax2.set_xticklabels([s.replace("_", "\n") for s in existing_strats], fontsize=9)

    plt.tight_layout()
    out_file = os.path.join(output_dir, "03_kernel_resources_cpu_ram.png")
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")

def plot_concurrency_curves(df: pd.DataFrame, output_dir: str):
    """
    Line plots showing Latency vs Concurrency Level (1, 10, 25, 50).
    Only renders if multiple concurrency levels exist in the dataset.
    """
    conc_df = df[df["concurrency_level"].astype(int) > 0]
    unique_concs = sorted(conc_df["concurrency_level"].astype(int).unique())

    if len(unique_concs) < 2:
        return

    fig, ax = plt.subplots(figsize=(9, 6))

    for strat in STRATEGY_ORDER:
        strat_df = conc_df[conc_df["strategy"] == strat]
        if strat_df.empty:
            continue

        stats = strat_df.groupby("concurrency_level")["total_op_time_ms"].agg(["mean", "std"]).sort_index()
        x = stats.index.astype(int)
        y = stats["mean"].values
        err = stats["std"].fillna(0).values

        color = STRATEGY_COLORS.get(strat, "#333333")
        ax.plot(x, y, marker="o", linewidth=2.2, label=strat, color=color)
        ax.fill_between(x, y - err, y + err, alpha=0.15, color=color)

    ax.set_xlabel("Concurrency Level (Concurrent Shell Workers)")
    ax.set_ylabel("Total Latency (ms)")
    ax.set_title("Thundering Herd: Scalability & Contention Curve (c=1..50)")
    ax.set_xticks(unique_concs)
    ax.legend(loc="upper left", frameon=True)
    plt.tight_layout()

    out_file = os.path.join(output_dir, "04_thundering_herd_scalability.png")
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")

def plot_container_telemetry_macro(df_macro: pd.DataFrame, output_dir: str):
    """
    Visualizes multi-container telemetry from Prometheus:
    CPU and RAM metrics across runner, minio, and vault.
    """
    if df_macro.empty:
        return

    baseline_macro = df_macro[df_macro["scenario"] == "baseline"]
    if baseline_macro.empty:
        baseline_macro = df_macro

    existing_strats = [s for s in STRATEGY_ORDER if s in baseline_macro["strategy"].unique()]
    if not existing_strats:
        return

    grouped = baseline_macro.groupby("strategy").mean(numeric_only=True).loc[existing_strats]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # CPU Max%
    cpu_cols = ["runner_cpu_max_pct", "minio_cpu_max_pct", "vault_cpu_max_pct"]
    avail_cpu_cols = [c for c in cpu_cols if c in grouped.columns]
    if avail_cpu_cols:
        grouped[avail_cpu_cols].plot(kind="bar", ax=ax1, edgecolor="black", width=0.6, colormap="viridis", alpha=0.85)
        ax1.set_title("Peak Container CPU Load (%) - Prometheus")
        ax1.set_ylabel("CPU Utilization (%)")
        ax1.set_xticklabels([s.replace("_", "\n") for s in existing_strats], rotation=0, fontsize=9)
        ax1.legend(["Runner", "MinIO", "Vault"], frameon=True)

    # RAM Max MB
    ram_cols = ["runner_ram_max_mb", "minio_ram_max_mb", "vault_ram_max_mb"]
    avail_ram_cols = [c for c in ram_cols if c in grouped.columns]
    if avail_ram_cols:
        grouped[avail_ram_cols].plot(kind="bar", ax=ax2, edgecolor="black", width=0.6, colormap="magma", alpha=0.85)
        ax2.set_title("Peak Container Memory Usage (MB) - Prometheus")
        ax2.set_ylabel("RAM (MB)")
        ax2.set_xticklabels([s.replace("_", "\n") for s in existing_strats], rotation=0, fontsize=9)
        ax2.legend(["Runner", "MinIO", "Vault"], frameon=True)

    plt.tight_layout()
    out_file = os.path.join(output_dir, "05_container_telemetry_macro.png")
    plt.savefig(out_file)
    plt.close()
    print(f"[+] Saved: {out_file}")

def export_statistical_summary_table(df_micro: pd.DataFrame, target_path: str):
    """
    Computes a comprehensive statistical breakdown (Mean, Std, Median, P95, P99, Min, Max, Count, Errors)
    and saves it to CSV for direct inclusion in thesis tables.
    """
    records = []
    grouped = df_micro.groupby(["strategy", "scenario", "concurrency_level"])

    for (strat, scen, conc), group in grouped:
        n = len(group)
        tot = group["total_op_time_ms"].dropna()
        auth = group["auth_time_ms"].dropna()
        target = group["target_call_time_ms"].dropna()

        errors = int(group["error_flag"].astype(str).str.lower().isin(["true", "1"]).sum())
        err_rate = round((errors / n) * 100.0, 2) if n > 0 else 0.0

        records.append({
            "strategy": strat,
            "scenario": scen,
            "concurrency_level": conc,
            "samples_count": n,
            "total_mean_ms": round(tot.mean(), 3),
            "total_std_ms": round(tot.std(ddof=1), 3) if n > 1 else 0.0,
            "total_median_ms": round(tot.median(), 3),
            "total_p95_ms": round(tot.quantile(0.95), 3),
            "total_p99_ms": round(tot.quantile(0.99), 3),
            "auth_mean_ms": round(auth.mean(), 3),
            "auth_std_ms": round(auth.std(ddof=1), 3) if n > 1 else 0.0,
            "target_mean_ms": round(target.mean(), 3),
            "target_std_ms": round(target.std(ddof=1), 3) if n > 1 else 0.0,
            "error_count": errors,
            "error_rate_pct": err_rate,
        })

    summary_df = pd.DataFrame(records)
    summary_df.sort_values(by=["scenario", "concurrency_level", "strategy"], inplace=True)
    summary_df.to_csv(target_path, index=False)
    print(f"[+] Statistical summary table exported: {target_path}")

def main():
    parser = argparse.ArgumentParser(description="Publication-Grade Benchmark Plotting Suite (v1.0)")
    parser.add_argument("--results-dir", default=os.path.abspath("./results"), help="Results directory containing master/")
    parser.add_argument("--output-dir", default=None, help="Directory to save generated plots (default: results/plots)")
    args = parser.parse_args()

    results_dir = args.results_dir
    plots_dir = args.output_dir or os.path.join(results_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)

    micro_path = os.path.join(results_dir, "master", "master_micro_metrics.csv")
    macro_path = os.path.join(results_dir, "master", "master_macro_metrics.csv")
    summary_path = os.path.join(results_dir, "master", "statistical_summary.csv")

    if not os.path.isfile(micro_path):
        print(f"[!] Error: {micro_path} does not exist. Run merge_results.py first!", file=sys.stderr)
        sys.exit(1)

    apply_plot_style()

    df_micro = pd.read_csv(micro_path)
    df_macro = pd.read_csv(macro_path) if os.path.isfile(macro_path) else pd.DataFrame()

    print(f"\n=======================================================")
    print(f"[*] Processing Benchmark Telemetry for Visualization")
    print(f"    Master Micro Records: {len(df_micro)}")
    print(f"    Master Macro Records: {len(df_macro)}")
    print(f"    Target Output Directory: {plots_dir}")
    print(f"=======================================================")

    plot_baseline_latency_breakdown(df_micro, plots_dir)
    plot_baseline_total_latency(df_micro, plots_dir)
    plot_process_kernel_resources(df_micro, plots_dir)
    plot_concurrency_curves(df_micro, plots_dir)
    plot_container_telemetry_macro(df_macro, plots_dir)
    export_statistical_summary_table(df_micro, summary_path)

    print("\n[✓] All visualization artifacts generated successfully.")

if __name__ == "__main__":
    main()
