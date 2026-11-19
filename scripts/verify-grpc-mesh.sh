#!/usr/bin/env bash
set -euo pipefail

# Verify gRPC mTLS PKI mounts, extra_hosts, and certificate chains across all 5 services.
INFRA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ROOT="$(cd -- "$INFRA_ROOT/.." && pwd)"
PKI_ROOT="${PKI_ROOT:-/etc/studio/pki}"

python3 - "$BACKEND_ROOT" <<'PY'
import sys
from pathlib import Path
import yaml

root = Path(sys.argv[1])
services = {
    "auth": "studio-auth",
    "core": "studio-core",
    "payment": "studio-payment",
    "utility": "studio-utility",
    "config": "studio-config",
}

for svc, dirname in services.items():
    for filename in ("docker-compose.yml", "docker-compose.dev.yml"):
        path = root / dirname / filename
        cfg = yaml.safe_load(path.read_text())
        app = cfg["services"][svc]
        extra_hosts = app.get("extra_hosts", [])
        assert any("host.docker.internal:host-gateway" in h for h in extra_hosts), f"{path}: missing host.docker.internal"
        volumes = app.get("volumes", [])
        expected_targets = {
            f"/run/studio/pki/{svc}/server.crt",
            f"/run/studio/pki/{svc}/server.key",
            f"/run/studio/pki/{svc}/ca.crt",
        }
        mounted_targets = {
            v["target"]
            for v in volumes
            if isinstance(v, dict)
            and v.get("type") == "bind"
            and v.get("read_only") is True
            and v.get("bind", {}).get("create_host_path") is False
        }
        missing = expected_targets - mounted_targets
        assert not missing, f"{path}: missing PKI bind mounts: {sorted(missing)}"

print("Compose mTLS bind mounts and extra_hosts OK across all 5 services.")
PY

if [[ -d "$PKI_ROOT" ]]; then
  for svc in auth core payment utility config; do
    svc_dir="$PKI_ROOT/$svc"
    if [[ -r "$svc_dir/ca.crt" && -r "$svc_dir/server.crt" ]]; then
      openssl verify -CAfile "$svc_dir/ca.crt" "$svc_dir/server.crt"
      openssl x509 -in "$svc_dir/server.crt" -noout -ext subjectAltName,extendedKeyUsage | grep -q "spiffe://studio/service/${svc}"
      echo "Verified PKI cert + SPIFFE SAN for ${svc}"
    else
      echo "Notice: skipping readable cert check for ${svc} in ${svc_dir} (not readable or missing)"
    fi
  done
fi
