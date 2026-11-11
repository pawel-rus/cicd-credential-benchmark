"""
benchmarks/bench_oidc.py
Benchmark Worker for Strategy 2: Direct OIDC (Federated Identity / STS).
Exchanges a GitLab-issued JWT for temporary AWS-compatible session credentials
via MinIO STS (AssumeRoleWithWebIdentity), then executes an authenticated S3 call.
Supports /dev/shm barrier synchronization for Thundering Herd concurrency.
"""

import os
import sys
import time
import argparse
import resource
import tracemalloc
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from core_metrics import (
    execute_raw_http_request,
    execute_minio_s3_call,
    resolve_metric_filepath,
    save_micro_metric,
    synchronize_thundering_herd_barrier,
    MicroMetricRecord,
)

def parse_sts_credentials(xml_bytes: bytes) -> tuple[str, str, str, bool]:
    """
    Parses STS XML response containing AccessKeyId, SecretAccessKey, and SessionToken.
    """
    try:
        root = ET.fromstring(xml_bytes)
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        credentials_elem = root.find(f".//{ns}Credentials")
        if credentials_elem is None:
            return "", "", "", True

        access_key = credentials_elem.find(f"{ns}AccessKeyId")
        secret_key = credentials_elem.find(f"{ns}SecretAccessKey")
        session_token = credentials_elem.find(f"{ns}SessionToken")

        ak = access_key.text if access_key is not None else ""
        sk = secret_key.text if secret_key is not None else ""
        st = session_token.text if session_token is not None else ""

        if not ak or not sk or not st:
            return ak, sk, st, True

        return ak, sk, st, False
    except Exception as e:
        print(f"[bench_oidc] XML Parsing Exception: {e}", file=sys.stderr)
        return "", "", "", True

def run_benchmark(args):
    pipeline_id = args.pipeline_id or os.environ.get("CI_PIPELINE_ID", "0")
    job_id = args.job_id or os.environ.get("CI_JOB_ID", "0")
    results_dir = args.results_dir

    csv_path = resolve_metric_filepath(
        results_base_dir=results_dir,
        scenario=args.scenario,
        strategy="FEDERATED_OIDC",
        concurrency_level=args.concurrency,
        pipeline_id=pipeline_id,
        job_id=job_id,
    )

    print(f"[bench_oidc] Run ID: {args.test_run_id} | Scenario: {args.scenario} | Concurrency: {args.concurrency} | Iterations: {args.iterations}")
    print(f"[bench_oidc] Target Output: {csv_path}")

    # Synchronize all concurrent workers in RAM before timed execution begins
    if args.scenario == "thundering_herd" and args.concurrency > 1:
        synchronize_thundering_herd_barrier(pipeline_id, args.concurrency)

    jwt_token = os.environ.get(args.jwt_env, "")
    if not jwt_token:
        print(f"[bench_oidc] WARNING: Environment variable {args.jwt_env} is empty!", file=sys.stderr)

    for iteration in range(1, args.iterations + 1):
        if iteration > 1 and args.lifecycle_delay > 0 and args.scenario in ["credential_lifecycle", "lifecycle", "high_freq"]:
            print(f"[bench_oidc] Simulating workload delay between phases ({args.lifecycle_delay}s)...")
            time.sleep(args.lifecycle_delay)

        timestamp = datetime.now(timezone.utc).isoformat()
        usage_start = resource.getrusage(resource.RUSAGE_SELF)

        is_cold_start = (os.environ.get("IS_COLD_START", "false").lower() in ["true", "1"]) and (iteration == 1)

        # -------------------------------------------------------------
        # STEP 1: STS AssumeRoleWithWebIdentity (OIDC Token Exchange)
        # -------------------------------------------------------------
        tracemalloc.start()
        t_auth_start = time.perf_counter_ns()

        sts_form_data = {
            "Action": "AssumeRoleWithWebIdentity",
            "Version": "2011-06-15",
            "DurationSeconds": str(args.duration_seconds),
            "WebIdentityToken": jwt_token,
        }
        role_arn = args.role_arn or os.environ.get("MINIO_ROLE_ARN", "arn:minio:iam:::role/1mAsqpUGox8eewepDQ1dmtwpdY8")
        if role_arn:
            sts_form_data["RoleArn"] = role_arn
        if args.role_session_name:
            sts_form_data["RoleSessionName"] = args.role_session_name

        sts_body = urllib.parse.urlencode(sts_form_data).encode("utf-8")
        sts_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        auth_http_requests = 0
        sts_res = execute_raw_http_request(
            method="POST",
            url=args.sts_endpoint,
            headers=sts_headers,
            body=sts_body,
            timeout=10.0,
        )
        auth_http_requests += 1

        access_key, secret_key, session_token, parse_error = parse_sts_credentials(sts_res.body)
        t_auth_end = time.perf_counter_ns()
        auth_time_ms = (t_auth_end - t_auth_start) / 1_000_000.0
        _, peak_heap_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        auth_heap_kb = round(peak_heap_bytes / 1024.0, 3)

        auth_error = sts_res.error_flag or parse_error
        if auth_error:
            print(f"[bench_oidc] AUTH ERROR! HTTP {sts_res.status_code} | Body: {sts_res.body.decode('utf-8', errors='replace')[:300]}", file=sys.stderr)

        # -------------------------------------------------------------
        # STEP 2: TARGET CALL (MinIO S3 ListBuckets with Session Token)
        # -------------------------------------------------------------
        target_res = execute_minio_s3_call(
            endpoint=args.minio_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            session_token=session_token,
        )

        # -------------------------------------------------------------
        # STEP 3: KERNEL RESOURCE ACCOUNTING (Delta for CPU, Peak for RAM)
        # -------------------------------------------------------------
        usage_end = resource.getrusage(resource.RUSAGE_SELF)
        cpu_user_ms = (usage_end.ru_utime - usage_start.ru_utime) * 1000.0
        cpu_sys_ms = (usage_end.ru_stime - usage_start.ru_stime) * 1000.0
        ram_peak_kb = usage_end.ru_maxrss

        total_op_time_ms = auth_time_ms + target_res.latency_ms
        total_bytes_sent = sts_res.bytes_sent + target_res.bytes_sent
        total_bytes_received = sts_res.bytes_received + target_res.bytes_received
        final_error = auth_error or target_res.error_flag

        target_http_requests = target_res.requests_count
        total_http_requests = auth_http_requests + target_http_requests

        record = MicroMetricRecord(
            timestamp=timestamp,
            test_run_id=args.test_run_id,
            strategy="FEDERATED_OIDC",
            scenario=args.scenario,
            concurrency_level=args.concurrency,
            pipeline_id=pipeline_id,
            job_id=job_id,
            iteration=iteration,
            auth_login_ms=round(auth_time_ms, 4),
            secret_gen_ms=0.0,
            auth_time_ms=round(auth_time_ms, 4),
            target_call_time_ms=round(target_res.latency_ms, 4),
            total_op_time_ms=round(total_op_time_ms, 4),
            cpu_user_ms=round(cpu_user_ms, 4),
            cpu_sys_ms=round(cpu_sys_ms, 4),
            ram_peak_kb=ram_peak_kb,
            auth_heap_kb=auth_heap_kb,
            bytes_sent=total_bytes_sent,
            bytes_received=total_bytes_received,
            http_status=target_res.http_status if not auth_error else sts_res.status_code,
            auth_http_requests=auth_http_requests,
            target_http_requests=target_http_requests,
            total_http_requests=total_http_requests,
            is_cold_start=is_cold_start,
            error_flag=final_error,
        )

        save_micro_metric(csv_path, record)

    print(f"[bench_oidc] Completed {args.iterations} iteration(s) successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Micro-benchmark: Federated OIDC / STS")
    parser.add_argument("--test-run-id", default="manual_run")
    parser.add_argument("--scenario", default="baseline", choices=["baseline", "credential_lifecycle", "lifecycle", "high_freq", "thundering_herd", "real_world"])
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--lifecycle-delay", type=float, default=5.0, help="Delay in seconds between phases in lifecycle scenario")
    parser.add_argument("--minio-endpoint", default=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"))
    parser.add_argument("--sts-endpoint", default=os.environ.get("STS_ENDPOINT", "http://minio:9000"))
    parser.add_argument("--jwt-env", default="GITLAB_OIDC_TOKEN")
    parser.add_argument("--role-arn", default="")
    parser.add_argument("--role-session-name", default="gitlab-runner-session")
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--pipeline-id", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--results-dir", default=os.environ.get("RESULTS_DIR", "/results"))

    args = parser.parse_args()
    run_benchmark(args)
