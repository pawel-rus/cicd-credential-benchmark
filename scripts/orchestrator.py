import requests
import time
import csv
import os
from datetime import datetime

# =====================================================================
# CONFIGURATION
# =====================================================================
GITLAB_URL = "http://localhost:8081"
PROMETHEUS_URL = "http://localhost:9095"

PROJECT_ID = "1"  
GITLAB_API_TOKEN = os.getenv("GITLAB_API_TOKEN")

if not GITLAB_API_TOKEN:
    raise ValueError("[!] Missing GITLAB_API_TOKEN in .env file")

STRATEGIES_TO_TEST = ["STATIC_BASELINE"]

HEADERS = {"PRIVATE-TOKEN": GITLAB_API_TOKEN}

def trigger_pipeline(strategy):
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/pipeline"
    payload = {
        "ref": "main",
        "variables": [{"key": "AUTH_STRATEGY", "value": strategy}]
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()["id"]

def wait_for_pipeline(pipeline_id):
    url = f"{GITLAB_URL}/api/v4/projects/{PROJECT_ID}/pipelines/{pipeline_id}"
    while True:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        status = response.json()["status"]
        if status in ["success", "failed", "canceled"]:
            return status
        time.sleep(5)

def fetch_prometheus_metrics(start_timestamp, end_timestamp, strategy):
    url = f"{PROMETHEUS_URL}/api/v1/query_range"
    promql_query = 'sum(rate(container_cpu_usage_seconds_total{name=~"runner-.*"}[1s]))'
    
    params = {
        "query": promql_query,
        "start": start_timestamp,
        "end": end_timestamp,
        "step": "1s" 
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    # Save to the isolated strategy directory
    results_dir = f"/home/pawel/cicd-credential-benchmark/results/{strategy}"
    os.makedirs(results_dir, exist_ok=True)
    results_path = f"{results_dir}/prometheus_metrics.csv"
    
    with open(results_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["strategy", "unix_timestamp", "cpu_usage_rate"])
        if data["data"]["result"]:
            for value_pair in data["data"]["result"][0]["values"]:
                writer.writerow([strategy, value_pair[0], value_pair[1]])

# =====================================================================
# MAIN ORCHESTRATION LOOP
# =====================================================================
if __name__ == "__main__":
    print("Starting automated benchmark orchestration...")
    
    for strategy in STRATEGIES_TO_TEST:
        print(f"\n[+] Initiating benchmark for strategy: {strategy}")
        start_time = time.time()
        
        try:
            pipeline_id = trigger_pipeline(strategy)
            print(f"    -> Pipeline {pipeline_id} triggered. Waiting...")
            final_status = wait_for_pipeline(pipeline_id)
            print(f"    -> Pipeline {pipeline_id} finished: {final_status}")
        except requests.exceptions.RequestException as e:
            print(f"    [!] GitLab API Error: {e}")
            continue
            
        end_time = time.time()
        time.sleep(2) 
        
        print("    -> Fetching Prometheus telemetry...")
        try:
            fetch_prometheus_metrics(start_time, end_time, strategy)
            print("    -> Metrics persisted successfully.")
        except requests.exceptions.RequestException as e:
            print(f"    [!] Prometheus API Error: {e}")