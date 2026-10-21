"""
benchmarks/bench_static.py
Benchmark Worker for Strategy 1: Static Secrets (Baseline).
Reads credentials from RAM (environment variables) and executes
an authenticated S3 ListBuckets operation against MinIO.
Supports /dev/shm barrier synchronization for Thundering Herd concurrency.
"""

import os
import sys
import time
import argparse
import resource
from datetime import datetime, timezone

from core_metrics import (
    execute_minio_s3_call,
    resolve_metric_filepath,
    save_micro_metric,
    synchronize_thundering_herd_barrier,
    MicroMetricRecord,
)

def run_benchmark(args):
    pipeline_id = args.pipeline_id or os.environ.get("CI_PIPELINE_ID", "0")
    job_id = args.job_id or os.environ.get("CI_JOB_ID", "0")
    results_dir = args.results_dir

    csv_path = resolve_metric_filepath(
        results_base_dir=results_dir,
        scenario=args.scenario,
        strategy="STATIC_BASELINE",
        concurrency_level=args.concurrency,
        pipeline_id=pipeline_id,
        job_id=job_id,
    )

    print(f"[bench_static] Run ID: {args.test_run_id} | Scenario: {args.scenario} | Concurrency: {args.concurrency} | Iterations: {args.iterations}")
    print(f"[bench_static] Target Output: {csv_path}")

    # Synchronize all concurrent workers in RAM before timed execution begins
    if args.scenario == "thundering_herd" and args.concurrency > 1:
        synchronize_thundering_herd_barrier(pipeline_id, args.concurrency)

    access_key_name = args.access_key_env
    secret_key_name = args.secret_key_env

    for iteration in range(1, args.iterations + 1):
        timestamp = datetime.now(timezone.utc).isoformat()
        usage_start = resource.getrusage(resource.RUSAGE_SELF)

        # -------------------------------------------------------------
        # STEP 1: AUTHENTICATION / CREDENTIAL RETRIEVAL (RAM / ENV)
        # -------------------------------------------------------------
        t_auth_start = time.perf_counter_ns()
        access_key = os.environ.get(access_key_name, "")
        if not access_key and access_key_name != "AWS_ACCESS_KEY_ID":
            access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")

        secret_key = os.environ.get(secret_key_name, "")
        if not secret_key and secret_key_name != "AWS_SECRET_ACCESS_KEY":
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

        t_auth_end = time.perf_counter_ns()
        auth_time_ms = (t_auth_end - t_auth_start) / 1_000_000.0

        error_flag = False
        if not access_key or not secret_key:
            error_flag = True
            print(f"[bench_static] ERROR: Missing credentials in environment ({access_key_name}/{secret_key_name})", file=sys.stderr)

        # -------------------------------------------------------------
        # STEP 2: TARGET CALL (MinIO S3 ListBuckets with AWS SigV4)
        # -------------------------------------------------------------
        target_res = execute_minio_s3_call(
            endpoint=args.minio_endpoint,
            access_key=access_key,
            secret_key=secret_key,
        )

        # -------------------------------------------------------------
        # STEP 3: KERNEL RESOURCE ACCOUNTING (Delta for CPU, Peak for RAM)
        # -------------------------------------------------------------
        usage_end = resource.getrusage(resource.RUSAGE_SELF)
        cpu_user_ms = (usage_end.ru_utime - usage_start.ru_utime) * 1000.0
        cpu_sys_ms = (usage_end.ru_stime - usage_start.ru_stime) * 1000.0
        ram_peak_kb = usage_end.ru_maxrss

        total_op_time_ms = auth_time_ms + target_res.latency_ms
        final_error = error_flag or target_res.error_flag

        record = MicroMetricRecord(
            timestamp=timestamp,
            test_run_id=args.test_run_id,
            strategy="STATIC_BASELINE",
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
            bytes_sent=target_res.bytes_sent,
            bytes_received=target_res.bytes_received,
            http_status=target_res.http_status,
            is_cold_start=False,
            error_flag=final_error,
        )

        save_micro_metric(csv_path, record)

    print(f"[bench_static] Completed {args.iterations} iteration(s) successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Micro-benchmark: Static Secrets Baseline")
    parser.add_argument("--test-run-id", default="manual_run", help="Unique identifier for the test suite run")
    parser.add_argument("--scenario", default="baseline", choices=["baseline", "high_freq", "thundering_herd", "real_world"])
    parser.add_argument("--concurrency", type=int, default=1, help="Level of job concurrency")
    parser.add_argument("--iterations", type=int, default=1, help="Number of repetitions inside this worker")
    parser.add_argument("--minio-endpoint", default=os.environ.get("MINIO_ENDPOINT", "http://minio:9000"))
    parser.add_argument("--access-key-env", default="MINIO_ACCESS_KEY")
    parser.add_argument("--secret-key-env", default="MINIO_SECRET_KEY")
    parser.add_argument("--pipeline-id", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--results-dir", default=os.environ.get("RESULTS_DIR", "/results"))

    args = parser.parse_args()
    run_benchmark(args)
