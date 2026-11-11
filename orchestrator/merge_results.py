"""
orchestrator/merge_results.py
Data Aggregation and Tidy Data Harmonization Script (v2.1).
Recursively collates fragmented micro-benchmarks from 'results/raw_micro/'
and host telemetry from 'results/raw_macro/' into unified Master CSV datasets
for downstream statistical analysis and visualization.
Includes detailed latency decomposition: auth_login_ms, secret_gen_ms, and target_call_time_ms.
"""

import os
import sys
import glob
import csv
import argparse
from collections import defaultdict

def merge_csv_directory(source_dir: str, target_file: str, id_column: str = "test_run_id") -> int:
    """
    Recursively discovers all CSV files under source_dir, deduplicates headers,
    and consolidates all rows into target_file.
    """
    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    csv_files = glob.glob(os.path.join(source_dir, "**", "*.csv"), recursive=True)

    # Exclude target file if within search path
    csv_files = [f for f in csv_files if os.path.abspath(f) != os.path.abspath(target_file)]

    if not csv_files:
        print(f"[merge_results] No CSV files located under {source_dir}")
        return 0

    # Scan all files to collect the complete union of headers (schema evolution)
    all_headers = []
    for fp in sorted(csv_files):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                r = csv.reader(f)
                h = next(r, None)
                if h:
                    for col in h:
                        if col not in all_headers:
                            all_headers.append(col)
        except Exception:
            pass

    for req_col in ["auth_http_requests", "target_http_requests", "total_http_requests"]:
        if req_col not in all_headers:
            all_headers.append(req_col)

    master_headers = all_headers
    total_records = 0
    seen_rows = set()

    with open(target_file, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=master_headers)
        writer.writeheader()

        for file_path in sorted(csv_files):
            try:
                with open(file_path, "r", encoding="utf-8") as in_f:
                    reader = csv.DictReader(in_f)
                    if not reader.fieldnames:
                        continue

                    for row in reader:
                        # Backfill HTTP request counts if missing from older runs
                        if "auth_http_requests" not in row or not row.get("auth_http_requests"):
                            strat = row.get("strategy", "")
                            if strat == "STATIC_BASELINE" or (strat.startswith("STATIC") and "VAULT" not in strat):
                                row["auth_http_requests"] = "0"
                            elif "OIDC" in strat and "VAULT" not in strat:
                                row["auth_http_requests"] = "1"
                            else:
                                row["auth_http_requests"] = "2"
                        if "target_http_requests" not in row or not row.get("target_http_requests"):
                            row["target_http_requests"] = "1"
                        if "total_http_requests" not in row or not row.get("total_http_requests"):
                            row["total_http_requests"] = str(int(row["auth_http_requests"]) + int(row["target_http_requests"]))

                        # Construct a unique fingerprint for deduplication
                        row_key = tuple(row.get(h, "") for h in master_headers)
                        if row_key not in seen_rows:
                            seen_rows.add(row_key)
                            writer.writerow(row)
                            total_records += 1
            except Exception as e:
                print(f"[merge_results] Error parsing {file_path}: {e}", file=sys.stderr)

    print(f"[merge_results] Merged {len(csv_files)} files -> {target_file} ({total_records} rows)")
    return total_records

def print_micro_summary(master_micro_file: str):
    """
    Generates a concise statistical summary of the consolidated micro-metrics,
    showing login latency, secret generation latency, and target S3 latency.
    """
    if not os.path.isfile(master_micro_file):
        return

    stats = defaultdict(lambda: {
        "count": 0,
        "auth_login_sum": 0.0,
        "secret_gen_sum": 0.0,
        "auth_sum": 0.0,
        "target_sum": 0.0,
        "total_sum": 0.0,
        "errors": 0,
    })

    with open(master_micro_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            key = (r.get("strategy", "unknown"), r.get("scenario", "unknown"), r.get("concurrency_level", "1"))
            s = stats[key]
            s["count"] += 1
            try:
                s["auth_login_sum"] += float(r.get("auth_login_ms", 0.0) or 0.0)
                s["secret_gen_sum"] += float(r.get("secret_gen_ms", 0.0) or 0.0)
                s["auth_sum"] += float(r.get("auth_time_ms", 0.0) or 0.0)
                s["target_sum"] += float(r.get("target_call_time_ms", 0.0) or 0.0)
                s["total_sum"] += float(r.get("total_op_time_ms", 0.0) or 0.0)
                if r.get("error_flag", "").lower() in ["true", "1"]:
                    s["errors"] += 1
            except (ValueError, TypeError):
                pass

    print("\n" + "=" * 115)
    print(f"{'Strategy':<16} | {'Scenario':<16} | {'Conc':<5} | {'Samples':<8} | {'Login (ms)':<11} | {'Gen (ms)':<10} | {'Auth (ms)':<11} | {'S3 Target':<11} | {'Errors':<6}")
    print("=" * 115)
    for (strat, scen, conc), d in sorted(stats.items()):
        cnt = d["count"]
        avg_login = round(d["auth_login_sum"] / cnt, 3) if cnt > 0 else 0.0
        avg_gen = round(d["secret_gen_sum"] / cnt, 3) if cnt > 0 else 0.0
        avg_auth = round(d["auth_sum"] / cnt, 3) if cnt > 0 else 0.0
        avg_target = round(d["target_sum"] / cnt, 3) if cnt > 0 else 0.0
        print(f"{strat:<16} | {scen:<16} | {conc:<5} | {cnt:<8} | {avg_login:<11} | {avg_gen:<10} | {avg_auth:<11} | {avg_target:<11} | {d['errors']:<6}")
    print("=" * 115 + "\n")

def print_lifecycle_breakdown(master_micro_file: str):
    """
    Prints a targeted comparison of Phase 1 (Initial Acquisition) vs
    Phase 2 (Lifecycle Renewal / Secondary Reuse) for the credential_lifecycle scenario.
    """
    if not os.path.isfile(master_micro_file):
        return

    lifecycle_stats = defaultdict(lambda: {
        "p1_auth": [],
        "p2_auth": [],
        "p1_target": [],
        "p2_target": [],
    })

    with open(master_micro_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            scen = r.get("scenario", "")
            if scen in ["credential_lifecycle", "lifecycle", "high_freq"]:
                strat = r.get("strategy", "unknown")
                it = r.get("iteration", "1")
                try:
                    auth_ms = float(r.get("auth_time_ms", 0.0) or 0.0)
                    target_ms = float(r.get("target_call_time_ms", 0.0) or 0.0)
                    if it == "1":
                        lifecycle_stats[strat]["p1_auth"].append(auth_ms)
                        lifecycle_stats[strat]["p1_target"].append(target_ms)
                    elif it == "2":
                        lifecycle_stats[strat]["p2_auth"].append(auth_ms)
                        lifecycle_stats[strat]["p2_target"].append(target_ms)
                except (ValueError, TypeError):
                    pass

    has_data = any(d["p1_auth"] and d["p2_auth"] for d in lifecycle_stats.values())
    if has_data:
        print("=" * 115)
        print("CREDENTIAL LIFECYCLE BREAKDOWN: Phase 1 (Initial Handshake) vs Phase 2 (Mid-Job Renewal / Reuse)")
        print("=" * 115)
        print(f"{'Strategy':<18} | {'Phase 1 Auth (ms)':<18} | {'Phase 2 Auth (ms)':<18} | {'Delta Auth (ms)':<16} | {'Speedup / Amortization':<22}")
        print("-" * 115)
        for strat, d in sorted(lifecycle_stats.items()):
            if d["p1_auth"] and d["p2_auth"]:
                avg_p1 = sum(d["p1_auth"]) / len(d["p1_auth"])
                avg_p2 = sum(d["p2_auth"]) / len(d["p2_auth"])
                delta = avg_p1 - avg_p2
                speedup_pct = ((avg_p1 - avg_p2) / avg_p1 * 100.0) if avg_p1 > 0 else 0.0
                if max(avg_p1, avg_p2) < 0.15:
                    ratio_str = "Parity (<0.1 ms RAM)"
                elif delta > 0:
                    ratio_str = f"{round(speedup_pct, 1)}% faster"
                else:
                    ratio_str = f"{round(abs(speedup_pct), 1)}% slower"
                print(f"{strat:<18} | {round(avg_p1, 3):<18} | {round(avg_p2, 3):<18} | {round(delta, 3):<16} | {ratio_str:<22}")
        print("=" * 115 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Merge Fragmented Benchmarking CSVs into Master Tidy Tables (v2.1)")
    parser.add_argument("--results-dir", default=os.path.abspath("./results"), help="Base results directory")
    args = parser.parse_args()

    micro_dir = os.path.join(args.results_dir, "raw_micro")
    macro_dir = os.path.join(args.results_dir, "raw_macro")
    master_dir = os.path.join(args.results_dir, "master")

    master_micro_csv = os.path.join(master_dir, "master_micro_metrics.csv")
    master_macro_csv = os.path.join(master_dir, "master_macro_metrics.csv")

    print("[*] Consolidating micro-benchmarks...")
    merge_csv_directory(micro_dir, master_micro_csv)
    print_micro_summary(master_micro_csv)
    print_lifecycle_breakdown(master_micro_csv)

    print("[*] Consolidating macro-telemetry...")
    merge_csv_directory(macro_dir, master_macro_csv)

if __name__ == "__main__":
    main()
