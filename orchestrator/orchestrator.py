"""
orchestrator/orchestrator.py
Master Experiment Orchestration Suite (v2.3).
Coordinates automated pipeline executions via GitLab REST API for all 4 strategies:
  1. STATIC_BASELINE
  2. FEDERATED_OIDC
  3. VAULT_APPROLE
  4. VAULT_OIDC
Implements automated JWKS cache invalidation for cold-start accuracy,
and captures high-precision multi-container telemetry from Prometheus.
"""

import os
import sys
import time
import csv
import json
import argparse
import subprocess
import requests
from datetime import datetime, timezone

def load_dotenv(env_path=".env"):
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_dotenv()

GITLAB_URL = os.environ.get("GITLAB_URL", "http://localhost:8081").rstrip("/")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9095").rstrip("/")
VAULT_URL = os.environ.get("VAULT_URL", "http://localhost:8200").rstrip("/")
PROJECT_ID = os.environ.get("GITLAB_PROJECT_ID", "1")
GITLAB_API_TOKEN = os.environ.get("GITLAB_API_TOKEN", "")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "root")

ALL_STRATEGIES = ["STATIC_BASELINE", "FEDERATED_OIDC", "VAULT_APPROLE", "VAULT_OIDC"]

# =====================================================================
# GITLAB REST API CONTROLLER
# =====================================================================
class GitLabController:
    def __init__(self, base_url: str, token: str, project_id: str):
        self.base_url = base_url
        self.project_id = project_id
        self.headers = {"PRIVATE-TOKEN": token} if token else {}

    def trigger_pipeline(self, strategy: str, scenario: str, concurrency: int,
                         iterations: int, test_run_id: str, branch: str = "main", max_retries: int = 3) -> int:
        url = f"{self.base_url}/api/v4/projects/{self.project_id}/pipeline"
        variables = [
            {"key": "AUTH_STRATEGY", "value": strategy},
            {"key": "SCENARIO", "value": scenario},
            {"key": "CONCURRENCY_LEVEL", "value": str(concurrency)},
            {"key": "ITERATIONS", "value": str(iterations)},
            {"key": "TEST_RUN_ID", "value": test_run_id},
        ]
        payload = {"ref": branch, "variables": variables}

        for attempt in range(1, max_retries + 1):
            try:
                res = requests.post(url, headers=self.headers, json=payload, timeout=60)
                res.raise_for_status()
                return res.json()["id"]
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                if attempt == max_retries:
                    raise
                print(f"    [!] Warning: Pipeline dispatch attempt {attempt} failed ({e}). Retrying in 5s...", file=sys.stderr)
                time.sleep(5)

    def wait_for_pipeline(self, pipeline_id: int, poll_interval: float = 3.0) -> tuple[str, int]:
        url = f"{self.base_url}/api/v4/projects/{self.project_id}/pipelines/{pipeline_id}"
        jobs_url = f"{self.base_url}/api/v4/projects/{self.project_id}/pipelines/{pipeline_id}/jobs"

        while True:
            res = requests.get(url, headers=self.headers, timeout=60)
            res.raise_for_status()
            status = res.json().get("status", "unknown")

            if status in ["success", "failed", "canceled", "skipped"]:
                failed_count = 0
                try:
                    jobs_res = requests.get(jobs_url, headers=self.headers, timeout=60)
                    if jobs_res.status_code == 200:
                        for job in jobs_res.json():
                            if job.get("status") == "failed":
                                failed_count += 1
                except Exception:
                    pass
                return status, failed_count

            time.sleep(poll_interval)

# =====================================================================
# PROMETHEUS TELEMETRY COLLECTOR
# =====================================================================
class PrometheusCollector:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def query_instant(self, query: str, timestamp: float) -> float:
        url = f"{self.base_url}/api/v1/query"
        try:
            res = requests.get(url, params={"query": query, "time": timestamp}, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    results = data.get("data", {}).get("result", [])
                    if results and "value" in results[0]:
                        return float(results[0]["value"][1])
        except Exception as e:
            print(f"[Prometheus] Instant query error ({query}): {e}", file=sys.stderr)
        return 0.0

    def query_range(self, query: str, start_ts: float, end_ts: float, step: str = "250ms") -> list:
        url = f"{self.base_url}/api/v1/query_range"
        params = {"query": query, "start": start_ts, "end": end_ts, "step": step}
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    return data.get("data", {}).get("result", [])
        except Exception as e:
            print(f"[Prometheus] Range query error: {e}", file=sys.stderr)
        return []

    def get_container_cpu_stats(self, container_pattern: str, start_ts: float, end_ts: float) -> tuple[float, float]:
        duration = max(end_ts - start_ts, 0.001)

        counter_query = f'sum(container_cpu_usage_seconds_total{{name=~".*{container_pattern}.*"}})'
        cpu_start_sec = self.query_instant(counter_query, start_ts)
        cpu_end_sec = self.query_instant(counter_query, end_ts)
        cpu_delta = max(cpu_end_sec - cpu_start_sec, 0.0)
        avg_cpu_pct = round((cpu_delta / duration) * 100.0, 2)

        irate_query = f'sum(irate(container_cpu_usage_seconds_total{{name=~".*{container_pattern}.*"}}[2s])) * 100'
        range_results = self.query_range(irate_query, start_ts, end_ts, step="250ms")
        peak_values = []
        for series in range_results:
            for pair in series.get("values", []):
                try:
                    val = float(pair[1])
                    if not (val != val):
                        peak_values.append(val)
                except (ValueError, TypeError):
                    pass

        max_cpu_pct = round(max(peak_values), 2) if peak_values else avg_cpu_pct
        return avg_cpu_pct, max_cpu_pct

    def get_container_ram_peak(self, container_pattern: str, start_ts: float, end_ts: float) -> float:
        query = f'max(container_memory_working_set_bytes{{name=~".*{container_pattern}.*"}}) / 1048576'
        range_results = self.query_range(query, start_ts, end_ts, step="500ms")
        values = []
        for series in range_results:
            for pair in series.get("values", []):
                try:
                    val = float(pair[1])
                    if not (val != val):
                        values.append(val)
                except (ValueError, TypeError):
                    pass
        if values:
            return round(max(values), 2)
        return round(self.query_instant(query, end_ts), 2)

    def get_network_delta_kb(self, metric_name: str, start_ts: float, end_ts: float) -> float:
        query = f'sum({metric_name}{{name=~".*(gitlab-runner|vault|minio).*"}})'
        b_start = self.query_instant(query, start_ts)
        b_end = self.query_instant(query, end_ts)
        delta_bytes = max(b_end - b_start, 0.0)
        return round(delta_bytes / 1024.0, 2)

# =====================================================================
# CACHE INVALIDATION CONTROLLER (Cold-Start Accuracy)
# =====================================================================
def invalidate_jwks_cache(strategy: str, vault_url: str = VAULT_URL, vault_token: str = VAULT_TOKEN):
    if strategy == "FEDERATED_OIDC":
        print("[Orchestrator] Invalidating MinIO JWKS cache via container restart...")
        try:
            subprocess.run(["docker", "restart", "minio"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            t_wait = time.time()
            while time.time() - t_wait < 10.0:
                try:
                    r = requests.get("http://localhost:9000/minio/health/live", timeout=1)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.3)
            print("[Orchestrator] MinIO restarted and healthy.")
        except Exception as e:
            print(f"[Orchestrator] Warning: Failed to restart MinIO: {e}", file=sys.stderr)

    elif strategy in ["VAULT_OIDC", "VAULT_DYNAMIC"]:
        print("[Orchestrator] Invalidating Vault JWKS cache via API configuration reload...")
        try:
            url = f"{vault_url}/v1/auth/jwt/config"
            headers = {"X-Vault-Token": vault_token}
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                config_data = res.json().get("data", {})
                requests.post(url, headers=headers, json=config_data, timeout=5)
            print("[Orchestrator] Vault JWKS cache invalidated.")
        except Exception as e:
            print(f"[Orchestrator] Warning: Failed to invalidate Vault JWKS: {e}", file=sys.stderr)

# =====================================================================
# MACRO METRIC PERSISTENCE
# =====================================================================
def save_macro_metric(file_path: str, record: dict):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    file_exists = os.path.isfile(file_path)
    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(record)

# =====================================================================
# EXPERIMENT RUNNER
# =====================================================================
def run_experiment(gitlab: GitLabController, prom: PrometheusCollector,
                   strategy: str, scenario: str, concurrency: int,
                   iterations: int, results_dir: str, branch: str = "main", invalidate_cache: bool = True):
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    strategy_slug_map = {
        "STATIC_BASELINE": "static",
        "FEDERATED_OIDC": "oidc",
        "VAULT_APPROLE": "vault_approle",
        "VAULT_OIDC": "vault_oidc",
        "VAULT_DYNAMIC": "vault_oidc",
    }
    strategy_slug = strategy_slug_map.get(strategy.upper(), strategy.lower())
    test_run_id = f"run_{strategy_slug}_{scenario}_c{concurrency}_{timestamp_str}"

    print(f"\n=======================================================================")
    print(f"[*] Starting Experiment: {test_run_id}")
    print(f"    Strategy: {strategy} | Scenario: {scenario} | Concurrency: {concurrency} | Iterations: {iterations}")
    print(f"=======================================================================")

    if invalidate_cache and scenario in ["baseline", "high_freq"]:
        invalidate_jwks_cache(strategy)

    t_start = time.time()
    try:
        pipeline_id = gitlab.trigger_pipeline(
            strategy=strategy,
            scenario=scenario,
            concurrency=concurrency,
            iterations=iterations,
            test_run_id=test_run_id,
            branch=branch,
        )
        print(f"    -> Pipeline #{pipeline_id} dispatched. Awaiting completion...")
        status, failed_jobs = gitlab.wait_for_pipeline(pipeline_id)
        t_end = time.time()
        print(f"    -> Pipeline #{pipeline_id} finished: {status} (Failed jobs: {failed_jobs})")
    except Exception as e:
        print(f"    [!] Error during pipeline execution: {e}", file=sys.stderr)
        return

    duration_s = round(t_end - t_start, 2)
    time.sleep(2.0)

    print("    -> Extracting high-resolution container telemetry from Prometheus...")
    runner_cpu_avg, runner_cpu_max = prom.get_container_cpu_stats("gitlab-runner", t_start, t_end)
    runner_ram_max = prom.get_container_ram_peak("gitlab-runner", t_start, t_end)

    vault_cpu_avg, vault_cpu_max = prom.get_container_cpu_stats("vault", t_start, t_end)
    vault_ram_max = prom.get_container_ram_peak("vault", t_start, t_end)

    minio_cpu_avg, minio_cpu_max = prom.get_container_cpu_stats("minio", t_start, t_end)
    minio_ram_max = prom.get_container_ram_peak("minio", t_start, t_end)

    rx_kb = prom.get_network_delta_kb("container_network_receive_bytes_total", t_start, t_end)
    tx_kb = prom.get_network_delta_kb("container_network_transmit_bytes_total", t_start, t_end)

    macro_record = {
        "test_run_id": test_run_id,
        "strategy": strategy,
        "scenario": scenario,
        "concurrency_level": concurrency,
        "total_pipeline_duration_s": duration_s,
        "runner_cpu_avg_pct": runner_cpu_avg,
        "runner_cpu_max_pct": runner_cpu_max,
        "runner_ram_max_mb": runner_ram_max,
        "vault_cpu_avg_pct": vault_cpu_avg,
        "vault_cpu_max_pct": vault_cpu_max,
        "vault_ram_max_mb": vault_ram_max,
        "minio_cpu_avg_pct": minio_cpu_avg,
        "minio_cpu_max_pct": minio_cpu_max,
        "minio_ram_max_mb": minio_ram_max,
        "network_rx_total_kb": rx_kb,
        "network_tx_total_kb": tx_kb,
        "failed_jobs_count": failed_jobs,
    }

    macro_path = os.path.join(results_dir, "raw_macro", f"macro_{test_run_id}.csv")
    save_macro_metric(macro_path, macro_record)
    print(f"    -> Macro telemetry saved: {macro_path}")

# =====================================================================
# CLI & BATCH DISPATCHER
# =====================================================================
def main():
    parser = argparse.ArgumentParser(description="CI/CD Credential Benchmark Orchestrator (v2.3)")
    parser.add_argument("--scenario", default="baseline", choices=["baseline", "high_freq", "thundering_herd", "all"])
    parser.add_argument("--strategy", default="all", choices=ALL_STRATEGIES + ["all"])
    parser.add_argument("--concurrencies", nargs="+", type=int, default=[10, 25, 50], help="Concurrency levels for thundering herd")
    parser.add_argument("--gitlab-url", default=GITLAB_URL)
    parser.add_argument("--prometheus-url", default=PROMETHEUS_URL)
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--results-dir", default=os.path.abspath("./results"))
    parser.add_argument("--branch", default=os.environ.get("GITLAB_BRANCH", "main"), help="GitLab target branch")
    parser.add_argument("--runs", type=int, default=1, help="Number of repetitions per experiment for statistical significance")
    parser.add_argument("--cooldown", type=int, default=3, help="Cool-down duration in seconds between pipeline runs")

    args = parser.parse_args()

    token = GITLAB_API_TOKEN or os.environ.get("GITLAB_API_TOKEN", "")
    if not token:
        print("[!] Warning: GITLAB_API_TOKEN is not set in environment or .env!", file=sys.stderr)

    gitlab = GitLabController(args.gitlab_url, token, args.project_id)
    prom = PrometheusCollector(args.prometheus_url)

    strategies = [args.strategy] if args.strategy != "all" else ALL_STRATEGIES

    if args.scenario in ["baseline", "all"]:
        for strat in strategies:
            for run_idx in range(1, args.runs + 1):
                if args.runs > 1:
                    print(f"\n>>> [Batch Progress] Repeat {run_idx}/{args.runs} for {strat} (baseline) <<<")
                # Only invalidate cache on the first run of the strategy (Cold Start), subsequent runs measure Steady State
                run_experiment(gitlab, prom, strategy=strat, scenario="baseline", concurrency=1, iterations=1, results_dir=args.results_dir, branch=args.branch, invalidate_cache=(run_idx == 1))
                if args.cooldown > 0:
                    time.sleep(args.cooldown)

    if args.scenario in ["high_freq", "all"]:
        for strat in strategies:
            for run_idx in range(1, args.runs + 1):
                if args.runs > 1:
                    print(f"\n>>> [Batch Progress] Repeat {run_idx}/{args.runs} for {strat} (high_freq) <<<")
                run_experiment(gitlab, prom, strategy=strat, scenario="high_freq", concurrency=1, iterations=100, results_dir=args.results_dir, branch=args.branch, invalidate_cache=(run_idx == 1))
                if args.cooldown > 0:
                    time.sleep(args.cooldown)

    if args.scenario in ["thundering_herd", "all"]:
        for conc in args.concurrencies:
            for strat in strategies:
                for run_idx in range(1, args.runs + 1):
                    if args.runs > 1:
                        print(f"\n>>> [Batch Progress] Repeat {run_idx}/{args.runs} for {strat} (thundering_herd, c={conc}) <<<")
                    run_experiment(gitlab, prom, strategy=strat, scenario="thundering_herd", concurrency=conc, iterations=1, results_dir=args.results_dir, branch=args.branch, invalidate_cache=(run_idx == 1))
                    if args.cooldown > 0:
                        time.sleep(args.cooldown)

    print("\n" + "=" * 75)
    print("[*] All scheduled experiments completed.")
    print("    Running merge_results.py to consolidate Master datasets...")
    print("=" * 75)
    try:
        merge_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "merge_results.py")
        subprocess.run([sys.executable, merge_script, "--results-dir", args.results_dir], check=False)
    except Exception as e:
        print(f"[!] Could not run merge_results automatically: {e}")

if __name__ == "__main__":
    main()
