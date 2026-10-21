import argparse
import time
import os
import csv
import resource
import requests

# =====================================================================
# UNIVERSAL MICRO-BENCHMARK WRAPPER
# =====================================================================
def measure_computational_overhead(auth_function, *args, **kwargs):
    """
    Executes the designated authentication strategy while capturing 
    high-resolution latency and CPU time metrics via the OS kernel.
    """
    # Capture initial kernel resource usage state
    usage_start = resource.getrusage(resource.RUSAGE_SELF)
    wall_clock_start = time.perf_counter()
    
    # Execute the requested authentication protocol
    auth_function(*args, **kwargs)
    
    wall_clock_end = time.perf_counter()
    # Capture final kernel resource usage state
    usage_end = resource.getrusage(resource.RUSAGE_SELF)
    
    # Calculate absolute wall-clock latency in milliseconds
    latency_ms = (wall_clock_end - wall_clock_start) * 1000
    
    # Calculate CPU time consumed in User mode (application logic)
    user_cpu_time_ms = (usage_end.ru_utime - usage_start.ru_utime) * 1000
    
    # Calculate CPU time consumed in System mode (kernel operations, I/O)
    sys_cpu_time_ms = (usage_end.ru_stime - usage_start.ru_stime) * 1000
    
    return latency_ms, user_cpu_time_ms, sys_cpu_time_ms

# =====================================================================
# AUTHENTICATION STRATEGY IMPLEMENTATIONS
# =====================================================================
def strategy_static_baseline(env_var_key):
    """ Simulated acquisition of a long-lived, statically injected credential. """
    return os.environ.get(env_var_key, "null_credential_payload")

def strategy_vault_approle(role_id, secret_id):
    """ Network-bound acquisition of a short-lived credential via Vault API. """
    vault_endpoint = "http://vault:8200/v1/auth/approle/login"
    payload = {"role_id": role_id, "secret_id": secret_id}
    try:
        requests.post(vault_endpoint, json=payload, timeout=5)
    except requests.exceptions.RequestException:
        pass

# =====================================================================
# MAIN EXECUTION ROUTER
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CI/CD Credential Benchmark Agent")
    parser.add_argument("--strategy", required=True, choices=["static", "vault", "oidc"])
    parser.add_argument("--task-id", required=True, help="Concurrency task identifier")
    parser.add_argument("--env-payload-key", default="DUMMY_KEY")
    parser.add_argument("--vault-role-id", default="")
    parser.add_argument("--vault-secret-id", default="")
    args = parser.parse_args()

    latency_ms, cpu_usr_ms, cpu_sys_ms = 0.0, 0.0, 0.0

    if args.strategy == "static":
        latency_ms, cpu_usr_ms, cpu_sys_ms = measure_computational_overhead(
            strategy_static_baseline, args.env_payload_key
        )
    elif args.strategy == "vault":
        latency_ms, cpu_usr_ms, cpu_sys_ms = measure_computational_overhead(
            strategy_vault_approle, args.vault_role_id, args.vault_secret_id
        )

    # Persist computational metrics to a strategy-specific, task-isolated file
    auth_strategy_dir_name = os.environ.get("AUTH_STRATEGY", args.strategy.upper())
    metrics_file_path = f"/results/{auth_strategy_dir_name}/micro_task_{args.task_id}.csv"
    
    os.makedirs(os.path.dirname(metrics_file_path), exist_ok=True)
    
    with open(metrics_file_path, "w", newline="") as csvfile:
        metrics_writer = csv.writer(csvfile)
        # We do not write headers here to simplify data concatenation later
        metrics_writer.writerow([args.strategy, args.task_id, latency_ms, cpu_usr_ms, cpu_sys_ms])