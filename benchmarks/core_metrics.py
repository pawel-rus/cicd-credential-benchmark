"""
benchmarks/core_metrics.py
Universal Micro-Benchmark Engine for CI/CD Credential Evaluation (v2.3).
Captures nanosecond timestamps, kernel CPU times (user & sys), RAM High-Water Mark (ru_maxrss),
exact L7 wire byte volumes (with Connection: close), AWS SigV4 signatures,
and RAM-based barrier synchronization (/dev/shm) for true Thundering Herd concurrency.
Supports symmetric 4-strategy matrix:
  1. STATIC_BASELINE (static)
  2. FEDERATED_OIDC (oidc)
  3. VAULT_APPROLE (vault_approle)
  4. VAULT_OIDC (vault_oidc)
"""

import time
import resource
import os
import csv
import hmac
import hashlib
import urllib.parse
from datetime import datetime, timezone
import http.client
from dataclasses import dataclass, asdict
import fcntl

# =====================================================================
# DATA STRUCTURES
# =====================================================================
@dataclass
class HttpExchangeResult:
    status_code: int
    headers: dict
    body: bytes
    latency_ms: float
    bytes_sent: int
    bytes_received: int
    error_flag: bool

@dataclass
class TargetCallResult:
    latency_ms: float
    http_status: int
    bytes_sent: int
    bytes_received: int
    requests_count: int
    error_flag: bool

@dataclass
class MicroMetricRecord:
    timestamp: str
    test_run_id: str
    strategy: str
    scenario: str
    concurrency_level: int
    pipeline_id: str
    job_id: str
    iteration: int
    auth_login_ms: float
    secret_gen_ms: float
    auth_time_ms: float
    target_call_time_ms: float
    total_op_time_ms: float
    cpu_user_ms: float
    cpu_sys_ms: float
    ram_peak_kb: int
    auth_heap_kb: float
    bytes_sent: int
    bytes_received: int
    http_status: int
    auth_http_requests: int
    target_http_requests: int
    total_http_requests: int
    is_cold_start: bool
    error_flag: bool

# =====================================================================
# THUNDERING HERD SYNCHRONIZATION BARRIER (/dev/shm)
# =====================================================================
def synchronize_thundering_herd_barrier(pipeline_id: str, concurrency_level: int, timeout_sec: float = 25.0) -> None:
    """
    RAM-based synchronization barrier (/dev/shm) for the Thundering Herd scenario.
    Guarantees that all N concurrent worker processes wait until all workers are spawned
    and reach this checkpoint before any worker begins timed execution and sends network packets.
    Time spent waiting at the barrier is NOT included in benchmark latency.
    """
    if concurrency_level <= 1:
        return

    shm_dir = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else "/tmp"
    barrier_file = os.path.join(shm_dir, f"barrier_{pipeline_id}_{concurrency_level}.txt")
    lock_file = os.path.join(shm_dir, f"barrier_{pipeline_id}_{concurrency_level}.lock")

    # Step 1: Atomically increment ready worker counter
    with open(lock_file, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            current_count = 0
            if os.path.exists(barrier_file):
                with open(barrier_file, "r", encoding="utf-8") as bf:
                    content = bf.read().strip()
                    if content.isdigit():
                        current_count = int(content)
            current_count += 1
            with open(barrier_file, "w", encoding="utf-8") as bf:
                bf.write(str(current_count))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

    # Step 2: Spin-wait until ready_count reaches concurrency_level
    t_wait_start = time.monotonic()
    while True:
        try:
            with open(barrier_file, "r", encoding="utf-8") as bf:
                count = int(bf.read().strip())
                if count >= concurrency_level:
                    break
        except Exception:
            pass

        if time.monotonic() - t_wait_start > timeout_sec:
            print(f"[Barrier] Warning: Timeout reached ({timeout_sec}s). Releasing worker.")
            break

        time.sleep(0.002)

# =====================================================================
# LOW-LEVEL HTTP CLIENT (Connection: close, Accurate Wire Byte Counter)
# =====================================================================
def execute_raw_http_request(method: str, url: str, headers: dict = None, body: bytes = None, timeout: float = 10.0) -> HttpExchangeResult:
    """
    Executes an HTTP request with explicit 'Connection: close',
    measuring exact nanosecond latency and wire bytes (L7 headers + payload).
    """
    if headers is None:
        headers = {}
    if body is None:
        body = b""
    elif isinstance(body, str):
        body = body.encode("utf-8")

    parsed_url = urllib.parse.urlsplit(url)
    is_https = parsed_url.scheme == "https"
    host = parsed_url.hostname
    port = parsed_url.port or (443 if is_https else 80)
    path = parsed_url.path or "/"
    if parsed_url.query:
        path = f"{path}?{parsed_url.query}"

    req_headers = dict(headers)
    req_headers["Host"] = f"{host}:{port}" if parsed_url.port else host
    req_headers["Connection"] = "close"
    if body and "Content-Length" not in req_headers:
        req_headers["Content-Length"] = str(len(body))

    # Calculate L7 bytes sent: Request Line + Headers + CRLF + Body
    req_line = f"{method.upper()} {path} HTTP/1.1\r\n"
    headers_str = "".join(f"{k}: {v}\r\n" for k, v in req_headers.items())
    bytes_sent = len(req_line.encode("latin1")) + len(headers_str.encode("latin1")) + 2 + len(body)

    t_start = time.perf_counter_ns()
    status_code = 0
    resp_headers = {}
    resp_body = b""
    bytes_received = 0
    error_flag = False

    conn = None
    try:
        if is_https:
            import ssl
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, context=context, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)

        conn.request(method.upper(), path, body=body, headers=req_headers)
        response = conn.getresponse()

        status_code = response.status
        resp_headers = dict(response.getheaders())
        resp_body = response.read()
        t_end = time.perf_counter_ns()

        # Calculate L7 bytes received: Status Line + Headers + CRLF + Body
        status_line = f"HTTP/1.1 {response.status} {response.reason}\r\n"
        resp_headers_str = "".join(f"{k}: {v}\r\n" for k, v in resp_headers.items())
        bytes_received = len(status_line.encode("latin1")) + len(resp_headers_str.encode("latin1")) + 2 + len(resp_body)

        latency_ms = (t_end - t_start) / 1_000_000.0
        if status_code >= 400:
            error_flag = True

    except Exception:
        t_end = time.perf_counter_ns()
        latency_ms = (t_end - t_start) / 1_000_000.0
        error_flag = True
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    return HttpExchangeResult(
        status_code=status_code,
        headers=resp_headers,
        body=resp_body,
        latency_ms=latency_ms,
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        error_flag=error_flag,
    )

# =====================================================================
# AWS SIGNATURE V4 (Standard AWS S3 SigV4 Generator)
# =====================================================================
def sign_aws_s3_sigv4(method: str, endpoint: str, access_key: str, secret_key: str,
                       session_token: str = None, region: str = "us-east-1") -> tuple[str, dict]:
    """
    Computes standard AWS Signature Version 4 for S3 ListBuckets (GET /).
    Returns (target_url, headers_dict)
    """
    now = datetime.now(timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = now.strftime('%Y%m%d')

    parsed = urllib.parse.urlsplit(endpoint)
    host = parsed.netloc

    canonical_uri = "/"
    canonical_querystring = ""
    payload_hash = hashlib.sha256(b"").hexdigest()

    # Headers for signing (lowercase, sorted)
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"

    if session_token:
        canonical_headers += f"x-amz-security-token:{session_token}\n"
        signed_headers += ";x-amz-security-token"

    canonical_request = (
        f"{method.upper()}\n"
        f"{canonical_uri}\n"
        f"{canonical_querystring}\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n"
        f"{amz_date}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    def _sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    k_date = _sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, "s3")
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    authorization_header = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": authorization_header,
    }
    if session_token:
        headers["x-amz-security-token"] = session_token

    target_url = urllib.parse.urljoin(endpoint, "/")
    return target_url, headers

# =====================================================================
# S3 TARGET CALL IMPLEMENTATION
# =====================================================================
def execute_minio_s3_call(endpoint: str, access_key: str, secret_key: str,
                          session_token: str = None, region: str = "us-east-1") -> TargetCallResult:
    """
    Executes authenticated S3 ListBuckets against MinIO.
    """
    target_url, auth_headers = sign_aws_s3_sigv4("GET", endpoint, access_key, secret_key, session_token, region)
    res = execute_raw_http_request("GET", target_url, headers=auth_headers)

    return TargetCallResult(
        latency_ms=res.latency_ms,
        http_status=res.status_code,
        bytes_sent=res.bytes_sent,
        bytes_received=res.bytes_received,
        requests_count=1,
        error_flag=(res.status_code != 200 or res.error_flag),
    )

# =====================================================================
# PERSISTENCE (Tidy Data CSV Writer)
# =====================================================================
def resolve_metric_filepath(results_base_dir: str, scenario: str, strategy: str,
                            concurrency_level: int, pipeline_id: str, job_id: str) -> str:
    """
    Resolves the standardized hierarchical path for micro metrics to prevent race conditions.
    Maps:
      STATIC_BASELINE -> static
      FEDERATED_OIDC  -> oidc
      VAULT_APPROLE   -> vault_approle
      VAULT_OIDC      -> vault_oidc
    """
    strategy_slug_map = {
        "STATIC_BASELINE": "static",
        "FEDERATED_OIDC": "oidc",
        "VAULT_STATIC_KV": "vault_kv",
        "VAULT_KV": "vault_kv",
        "VAULT_STATIC": "vault_kv",
        "VAULT_DYNAMIC_STS": "vault_dynamic",
        "VAULT_DYNAMIC": "vault_dynamic",
        "VAULT_OIDC": "vault_dynamic",
        "VAULT_APPROLE": "vault_approle",
    }
    strategy_dir = strategy_slug_map.get(strategy.upper(), strategy.lower())

    if scenario == "thundering_herd":
        dest_dir = os.path.join(
            results_base_dir, "raw_micro", scenario, f"conc_{concurrency_level}", strategy_dir
        )
    else:
        dest_dir = os.path.join(
            results_base_dir, "raw_micro", scenario, strategy_dir
        )

    os.makedirs(dest_dir, exist_ok=True)
    return os.path.join(dest_dir, f"job_{pipeline_id}_{job_id}.csv")

def save_micro_metric(file_path: str, record: MicroMetricRecord):
    """
    Appends a single measurement record in CSV format.
    Writes header if the file does not exist.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    file_exists = os.path.isfile(file_path)
    record_dict = asdict(record)

    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(record_dict.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(record_dict)
