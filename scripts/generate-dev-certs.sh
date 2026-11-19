#!/usr/bin/env bash
set -euo pipefail

# Generate a self-contained dev CA and per-service mTLS certs into ./certs (or TARGET_DIR).
INFRA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-${TARGET_DIR:-$INFRA_ROOT/../certs}}"
SERVER_IP="${SERVER_IP:-}"
CA_DIR="$TARGET_DIR/ca"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$CA_DIR"

if [[ ! -s "$CA_DIR/ca.key" || ! -s "$CA_DIR/ca.crt" ]]; then
  openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
    -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" \
    -subj "/CN=Studio Dev Internal CA"
  chmod 600 "$CA_DIR/ca.key"
  chmod 644 "$CA_DIR/ca.crt"
fi

for service in auth core payment utility config; do
  svc_dir="$TARGET_DIR/$service"
  mkdir -p "$svc_dir"
  cp "$CA_DIR/ca.crt" "$svc_dir/ca.crt"
  chmod 644 "$svc_dir/ca.crt"

  san="URI:spiffe://studio/service/${service},DNS:${service},DNS:studio-${service},DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
  if [[ -n "$SERVER_IP" ]]; then
    san="${san},IP:${SERVER_IP}"
  fi

  cnf="$TMP_DIR/${service}.cnf"
  csr="$TMP_DIR/${service}.csr"
  cat > "$cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = ${service}
[v3_req]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = ${san}
EOF

  openssl genrsa -out "$svc_dir/server.key" 2048
  chmod 600 "$svc_dir/server.key"
  openssl req -new -key "$svc_dir/server.key" -out "$csr" -config "$cnf"
  openssl x509 -req -in "$csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$svc_dir/server.crt" -days 3650 -extensions v3_req -extfile "$cnf"
  chmod 644 "$svc_dir/server.crt"

  cp "$svc_dir/server.crt" "$svc_dir/client.crt"
  cp "$svc_dir/server.key" "$svc_dir/client.key"
  chmod 644 "$svc_dir/client.crt"
  chmod 600 "$svc_dir/client.key"
done

echo "Generated dev mTLS PKI in $TARGET_DIR"
