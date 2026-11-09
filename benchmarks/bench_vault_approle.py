"""
benchmarks/bench_vault_approle.py
Benchmark Worker for Strategy: HashiCorp Vault AppRole (VAULT_APPROLE).
Authenticates against Vault via AppRole (POST /v1/auth/approle/login),
generates dynamic ephemeral credentials via Vault MinIO/AWS STS engine,
then executes an authenticated S3 ListBuckets operation against MinIO.
Supports /dev/shm barrier synchronization for Thundering Herd concurrency.
"""

import os
import sys
import time
import json
import argparse
import resource
import tracemalloc
import urllib.parse
from datetime import datetime, timezone

from core_metrics import (
    execute_raw_http_request,
    execute_minio_s3_call,
    resolve_metric_filepath,
    save_micro_metric,
    synchronize_thundering_herd_barrier,
    MicroMetricRecord,
)

def extract_dynamic_credentials(payload: dict) -> tuple[str, str, str, bool]:
    """
    Extracts dynamic access_key, secret_key, and optional security_token from Vault dynamic STS response.
    Supports standard Vault AWS/STS engine response as well as MinIO engine and KV fallbacks.
    """
    try:
        data = payload.get("data", {})
        if "data" in data and isinstance(data["data"], dict):
            inner = data["data"]
        else:
            inner = data

        ak = inner.get("access_key") or inner.get("access_key_id") or inner.get("AccessKeyId") or ""
        sk = inner.get("secret_key") or inner.get("secret_access_key") or inner.get("SecretAccessKey") or ""
        st = inner.get("security_token") or inner.get("session_token") or inner.get("SessionToken") or ""

        if not ak or not sk:
            return ak, sk, st, True
        return ak, sk, st, False
    except Exception as e:
        print(f"[bench_vault_approle] Credential parsing error: {e}", file=sys.stderr)
        return "", "", "", True

def run_benchmark(args):
    pipeline_id = args.pipeline_id or os.environ.get("CI_PIPELINE_ID", "0")
    job_id = args.job_id or os.environ.get("CI_JOB_ID", "0")
    results_dir = args.results_dir

    csv_path = resolve_metric_filepath(
        results_base_dir=results_dir,
        scenario=args.scenario,
        strategy="VAULT_APPROLE",
        concurrency_level=args.concurrency,
        pipeline_id=pipeline_id,
        job_id=job_id,
    )

    print(f"[bench_vault_approle] Run ID: {args.test_run_id} | Scenario: {args.scenario} | Concurrency: {args.concurrency} | Iterations: {args.iterations}")
    print(f"[bench_vault_approle] Target Output: {csv_path}")

    # Synchronize all concurrent workers in RAM before timed execution begins
    if args.scenario == "thundering_herd" and args.concurrency > 1:
        synchronize_thundering_herd_barrier(pipeline_id, args.concurrency)

    role_id = os.environ.get(args.role_id_env, "")
    secret_id = os.environ.get(args.secret_id_env, "")
    if not role_id or not secret_id:
        print(f"[bench_vault_approle] WARNING: Missing {args.role_id_env} or {args.secret_id_env} in environment!", file=sys.stderr)

    login_endpoint = urllib.parse.urljoin(args.vault_url, "/v1/auth/approle/login")
    dynamic_path = args.vault_dynamic_path.lstrip("/")
    if not dynamic_path.startswith("v1/"):
        dynamic_path = f"v1/{dynamic_path}"
    dynamic_endpoint = urllib.parse.urljoin(args.vault_url, f"/{dynamic_path}")

    for iteration in range(1, args.iterations + 1):
        timestamp = datetime.now(timezone.utc).isoformat()
        usage_start = resource.getrusage(resource.RUSAGE_SELF)

        # -------------------------------------------------------------
        # STEP 1A: APPROLE LOGIN (POST /v1/auth/approle/login)
        # -------------------------------------------------------------
        auth_http_requests = 0
        tracemalloc.start()
        t_login_start = time.perf_counter_ns()
        login_payload = json.dumps({"role_id": role_id, "secret_id": secret_id}).encode("utf-8")
        login_headers = {"Content-Type": "application/json"}
        login_res = execute_raw_http_request("POST", login_endpoint, headers=login_headers, body=login_payload)
        auth_http_requests += 1
        t_login_end = time.perf_counter_ns()
        auth_login_ms = (t_login_end - t_login_start) / 1_000_000.0

        client_token = ""
        login_error = login_res.error_flag
        try:
            login_data = json.loads(login_res.body.decode("utf-8"))
            client_token = login_data.get("auth", {}).get("client_token", "")
            if not client_token:
                login_error = True
        except Exception:
            login_error = True

        if login_error:
            print(f"[bench_vault_approle] WARNING: Vault authentication failed (HTTP {login_res.status_code}): {login_res.body.decode('utf-8', errors='replace')[:150]}", file=sys.stderr)

        # -------------------------------------------------------------
        # STEP 1B: DYNAMIC CREDENTIAL GENERATION (GET /v1/aws/sts/...)
        # -------------------------------------------------------------
        t_gen_start = time.perf_counter_ns()
        secret_headers = {"X-Vault-Token": client_token}
        secret_res = execute_raw_http_request("GET", dynamic_endpoint, headers=secret_headers)
        auth_http_requests += 1
        t_gen_end = time.perf_counter_ns()
        secret_gen_ms = (t_gen_end - t_gen_start) / 1_000_000.0

        access_key, secret_key, session_token, parse_error = "", "", "", True
        if not secret_res.error_flag:
            try:
                secret_json = json.loads(secret_res.body.decode("utf-8"))
                access_key, secret_key, session_token, parse_error = extract_dynamic_credentials(secret_json)
            except Exception:
                parse_error = True

        auth_time_ms = auth_login_ms + secret_gen_ms
        auth_error = login_error or secret_res.error_flag or parse_error
        _, peak_heap_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        auth_heap_kb = round(peak_heap_bytes / 1024.0, 3)

        # -------------------------------------------------------------
        # STEP 2: TARGET CALL (MinIO S3 ListBuckets with Dynamic Keys)
        # -------------------------------------------------------------
        target_res = execute_minio_s3_call(
            endpoint=args.minio_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token if session_token else None,
        )

        # -------------------------------------------------------------
        # STEP 3: KERNEL RESOURCE ACCOUNTING (Delta for CPU, Peak for RAM)
        # -------------------------------------------------------------
        usage_end = resource.getrusage(resource.RUSAGE_SELF)
        cpu_user_ms = (usage_end.ru_utime - usage_start.ru_utime) * 1000.0
        cpu_sys_ms = (usage_end.ru_stime - usage_start.ru_stime) * 1000.0
        ram_peak_kb = usage_end.ru_maxrss

        total_op_time_ms = auth_time_ms + target_res.latency_ms
        total_bytes_sent = login_res.bytes_sent + secret_res.bytes_sent + target_res.bytes_sent
        total_bytes_received = login_res.bytes_received + secret_res.bytes_received + target_res.bytes_received
        final_error = auth_error or target_res.error_flag

        target_http_requests = target_res.requests_count
        total_http_requests = auth_http_requests + target_http_requests

        record = MicroMetricRecord(
            timestamp=timestamp,
            test_run_id=args.test_run_id,
            strategy="VAULT_APPROLE",
            scenario=args.scenario,
            concurrency_level=args.concurrency,
            pipeline_id=pipeline_id,
            job_id=job_id,
            iteration=iteration,
            auth_login_ms=round(auth_login_ms, 4),
            secret_gen_ms=round(secret_gen_ms, 4),
            auth_time_ms=round(auth_time_ms, 4),
            target_call_time_ms=round(target_res.latency_ms, 4),
            total_op_time_ms=round(total_op_time_ms, 4),
            cpu_user_ms=round(cpu_user_ms, 4),
            cpu_sys_ms=round(cpu_sys_ms, 4),
            ram_peak_kb=ram_peak_kb,
            auth_heap_kb=auth_heap_kb,
            bytes_sent=total_bytes_sent,
            bytes_received=total_bytes_received,
            http_status=target_res.http_status if not auth_error else (secret_res.status_code or login_res.status_code),
            auth_http_requests=auth_http_requests,
            target_http_requests=target_http_requests,
            total_http_requests=total_http_requests,
            is_cold_start=(os.environ.get("IS_COLD_START", "false").lower() in ["true", "1"]) and (iteration == 1),
            error_flag=final_error,
        )

        save_micro_metric(csv_path, record)

    print(f"[bench_vault_approle] Completed {args.iterations} iteration(s) successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Micro-benchmark: HashiCorp Vault AppRole (AppRole + STS)")
    parser.add_argument("--test-run-id", default="manual_run")
    parser.add_argument("--scenario", default="baseline", choices=["baseline", "high_freq", "thundering_herd", "real_world"])
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--vault-url", default=os.environ.get("VAULT_ADDR", "http://vault:8200"))
    parser.add_argument("--role-id-env", default="VAULT_ROLE_ID")
    parser.add_argument("--secret-id-env", default="VAULT_SECRET_ID")
    parser.add_argument("--vault-dynamic-path", default="v1/aws/sts/minio-dynamic-role")
    parser.add_argument("--minio-endpoint", default=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"))
    parser.add_argument("--pipeline-id", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--results-dir", default=os.environ.get("RESULTS_DIR", "/results"))

    args = parser.parse_args()
    run_benchmark(args)
