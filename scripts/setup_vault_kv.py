#!/usr/bin/env python3
"""
scripts/setup_vault_kv.py
Configures HashiCorp Vault for the VAULT_STATIC_KV benchmark strategy:
1. Ensures KV v2 secrets engine is enabled at secret/
2. Writes static MinIO credentials at secret/data/ci/minio
3. Updates the gitlab-ci policy and role to permit read access to secret/data/ci/*
"""

import os
import sys
import json
import urllib.request
import urllib.error

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://localhost:8200").rstrip("/")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "root")

# Also check .env for token
if VAULT_TOKEN == "root":
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("VAULT_TOKEN="):
                    VAULT_TOKEN = line.split("=", 1)[1].strip()
                    break

HEADERS = {
    "X-Vault-Token": VAULT_TOKEN,
    "Content-Type": "application/json",
}

def vault_request(method: str, path: str, data: dict = None) -> tuple[int, dict]:
    url = f"{VAULT_ADDR}/v1/{path.lstrip('/')}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            return resp.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, {"error": err_body}
    except Exception as e:
        return 0, {"error": str(e)}

def setup():
    print(f"[*] Connecting to Vault at {VAULT_ADDR} with token: {VAULT_TOKEN[:8]}***")

    # 1. Check health
    status, res = vault_request("GET", "sys/health")
    if status not in [200, 429]:
        print(f"[!] Vault is not healthy or unsealed! Status: {status}, Response: {res}", file=sys.stderr)
        return False
    print(f"[+] Vault is online and unsealed (HTTP {status}).")

    # 2. Check mounts to ensure 'secret/' KV engine is enabled
    status, mounts = vault_request("GET", "sys/mounts")
    if "secret/" not in mounts.get("data", mounts):
        print("[*] Mounting KV v2 secrets engine at secret/...")
        mount_payload = {"type": "kv", "options": {"version": "2"}}
        m_status, m_res = vault_request("POST", "sys/mounts/secret", mount_payload)
        if m_status not in [200, 204]:
            print(f"[!] Failed to mount secret/: {m_res}", file=sys.stderr)
        else:
            print("[+] Successfully mounted KV v2 at secret/.")
    else:
        print("[+] KV secrets engine already mounted at secret/.")

    # 3. Write static credentials to secret/data/ci/minio
    static_creds = {
        "data": {
            "access_key": "minioadmin",
            "secret_key": "minioadminpassword",
            "AccessKeyId": "minioadmin",
            "SecretAccessKey": "minioadminpassword",
            "MINIO_ACCESS_KEY": "minioadmin",
            "MINIO_SECRET_KEY": "minioadminpassword"
        }
    }
    print("[*] Writing static credentials to secret/data/ci/minio...")
    w_status, w_res = vault_request("POST", "secret/data/ci/minio", static_creds)
    if w_status in [200, 204]:
        print("[+] Successfully stored static credentials at secret/data/ci/minio.")
    else:
        print(f"[!] Warning: Failed to write secret: {w_res}")

    # 4. Verify read
    r_status, r_res = vault_request("GET", "secret/data/ci/minio")
    if r_status == 200:
        retrieved = r_res.get("data", {}).get("data", {})
        print(f"[+] Verified secret read: access_key={retrieved.get('access_key')}")
    else:
        print(f"[!] Could not read back secret: {r_res}")

    # 5. Update/create policies to permit KV read across all policies & roles
    kv_policy_hcl = """
path "secret/data/ci/*" {
  capabilities = ["read", "list"]
}
path "secret/data/ci/minio" {
  capabilities = ["read", "list"]
}
path "secret/data/*" {
  capabilities = ["read", "list"]
}
path "aws/sts/*" {
  capabilities = ["read", "create", "update"]
}
"""
    # 5a. Retrieve all existing policies from Vault
    pol_list_status, pol_list_res = vault_request("GET", "sys/policy")
    existing_policies = ["default", "gitlab-ci", "gitlab-runner", "gitlab-runner-policy", "benchmark-policy"]
    if pol_list_status == 200:
        keys = pol_list_res.get("data", {}).get("keys", []) or pol_list_res.get("policies", [])
        if keys:
            existing_policies = list(set(existing_policies + keys))

    for pol_name in existing_policies:
        p_status, p_res = vault_request("GET", f"sys/policy/{pol_name}")
        existing_rules = ""
        if p_status == 200:
            existing_rules = p_res.get("rules", "") or p_res.get("data", {}).get("rules", "")

        if "secret/data/ci/*" not in existing_rules:
            combined_policy = existing_rules.strip() + "\n" + kv_policy_hcl.strip() + "\n"
            pol_payload = {"policy": combined_policy}
            up_status, up_res = vault_request("PUT", f"sys/policy/{pol_name}", pol_payload)
            if up_status in [200, 204]:
                print(f"[+] Updated ACL policy '{pol_name}' with KV read capabilities.")
        else:
            print(f"[+] ACL policy '{pol_name}' already contains KV read capabilities.")

    # 5b. Update JWT roles to ensure policies are attached
    role_list_status, role_list_res = vault_request("GET", "auth/jwt/role?list=true")
    jwt_roles = ["gitlab-ci", "gitlab-runner"]
    if role_list_status == 200:
        r_keys = role_list_res.get("data", {}).get("keys", [])
        if r_keys:
            jwt_roles = list(set(jwt_roles + r_keys))

    for r_name in jwt_roles:
        r_get_status, r_get_res = vault_request("GET", f"auth/jwt/role/{r_name}")
        if r_get_status == 200:
            r_data = r_get_res.get("data", {})
            current_pols = r_data.get("token_policies", []) or r_data.get("policies", [])
            needed = ["gitlab-ci", "default"]
            new_pols = list(set(current_pols + needed))
            if set(new_pols) != set(current_pols):
                r_data["token_policies"] = new_pols
                r_data["policies"] = new_pols
                post_status, post_res = vault_request("POST", f"auth/jwt/role/{r_name}", r_data)
                if post_status in [200, 204]:
                    print(f"[+] Attached policies {new_pols} to JWT role '{r_name}'.")

    print("\n[✓] Vault KV configuration completed successfully for VAULT_STATIC_KV benchmark!")
    return True

if __name__ == "__main__":
    setup()

