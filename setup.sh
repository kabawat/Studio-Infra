#!/usr/bin/env bash
set -euo pipefail

INFRA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REDIS_DIR="$INFRA_ROOT/redis"
DEFAULT_BACKEND_ROOT="$(cd -- "$INFRA_ROOT/.." && pwd)"

load_infra_env() {
  if [[ -f "$INFRA_ROOT/.env" ]]; then
    set -a
    source "$INFRA_ROOT/.env"
    set +a
  fi
  BACKEND_ROOT="${BACKEND_ROOT:-$DEFAULT_BACKEND_ROOT}"
  DRAGBIZZ_DIR="${DRAGBIZZ_DIR:-/etc/dragbizz}"
  PKI_ROOT="$DRAGBIZZ_DIR/pki"
  CA_DIR="$PKI_ROOT/ca"
  AUTH_KEYS_DIR="$DRAGBIZZ_DIR/auth/keys"
  UTILITY_KEYS_DIR="$DRAGBIZZ_DIR/utility/keys"
  DEFAULT_REDIS_HOST="${REDIS_HOST:-redis}"
}

load_infra_env

SERVICES=(Auth Core Payment Utility Config)
NEED_SUDO=false
TMP_DIR=""

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'HELP'
Usage: bash setup.sh [--prod] [-y|--yes] [--ip <SERVER_IP>] [--force-certs] [--no-start]

  No option      Development mode (uses DragBizz-* folders if present, then unprefixed).
  --prod         Production mode (uses Auth/Core/Finance/Utility/Brain folders).
  -y, --yes      Run non-interactively with defaults.
  --ip <IP>      Add an extra server IP to generated service certificate SANs.
  --force-certs  Re-issue service certificates (keeps existing private keys if valid).
  --no-start     Configure Redis and PKI without starting the Redis container.
  --help, -h     Show this help.

Sets up Redis credentials/container, Root CA, Auth JWT keys, mTLS certs/keys,
and missing .env/data files for every service found under BACKEND_ROOT.
Existing valid keys, certs, and .env files are reused.
HELP
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

valid_ipv4() {
  local ip="$1" part
  local -a parts
  [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a parts <<< "$ip"
  for part in "${parts[@]}"; do
    ((10#$part <= 255)) || return 1
  done
}

# Check if we need sudo to manage files under DRAGBIZZ_DIR
setup_privileges() {
  if [[ $EUID -eq 0 ]]; then
    NEED_SUDO=false
    return
  fi
  if [[ -d "$DRAGBIZZ_DIR" ]]; then
    if [[ -O "$DRAGBIZZ_DIR" && -w "$DRAGBIZZ_DIR" ]]; then
      NEED_SUDO=false
      return
    fi
  elif [[ -w "$(dirname "$DRAGBIZZ_DIR")" ]]; then
    NEED_SUDO=false
    return
  fi
  command -v sudo >/dev/null 2>&1 || fail "Run as root or install sudo to manage $DRAGBIZZ_DIR."
  NEED_SUDO=true
}

as_root() {
  if [[ "$NEED_SUDO" == true ]]; then
    sudo "$@"
  else
    "$@"
  fi
}

root_test() {
  as_root test "$@"
}

# Remove empty directories Docker may have created for missing cert/key files; reject non-empty ones
check_not_dir() {
  local path
  for path in "$@"; do
    if root_test -d "$path"; then
      if as_root rmdir "$path" 2>/dev/null; then
        echo "Removed empty directory: $path"
      else
        fail "$path is a non-empty directory. Remove it and re-run."
      fi
    fi
  done
}

run_openssl() {
  local err_file="$TMP_DIR/openssl.err"
  if ! as_root openssl "$@" 2>"$err_file"; then
    cat "$err_file" >&2
    fail "OpenSSL command failed."
  fi
}

prompt_redis_host() {
  local value
  read -r -p "Redis hostname [$DEFAULT_REDIS_HOST]: " value
  value="${value:-$DEFAULT_REDIS_HOST}"
  if [[ ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
    fail "Invalid Redis hostname: $value"
  fi
  printf '%s\n' "$value"
}

service_directory() {
  local service="$1"
  local _production="$2"
  local env_var="${service^^}_DIR"
  if [[ -n "${!env_var:-}" ]]; then
    printf '%s' "${!env_var}"
    return
  fi

  case "${service,,}" in
    auth) printf '%s/studio-auth' "$BACKEND_ROOT" ;;
    core) printf '%s/studio-core' "$BACKEND_ROOT" ;;
    payment) printf '%s/studio-payment' "$BACKEND_ROOT" ;;
    utility) printf '%s/studio-utility' "$BACKEND_ROOT" ;;
    config) printf '%s/config' "$BACKEND_ROOT" ;;
    *) printf '%s/%s' "$BACKEND_ROOT" "${service,,}" ;;
  esac
}

check_prerequisites() {
  local no_start="$1"
  echo "Checking prerequisites..."
  command -v python3 >/dev/null 2>&1 || fail "Python 3 is required."
  command -v openssl >/dev/null 2>&1 || fail "OpenSSL is required."
  if [[ "$no_start" == false ]]; then
    command -v docker >/dev/null 2>&1 || fail "Docker is required."
    docker info >/dev/null 2>&1 || fail "Cannot reach Docker daemon."
  fi
  [[ -d "$BACKEND_ROOT" ]] || fail "Backend root not found: $BACKEND_ROOT (check BACKEND_ROOT in .env)"
  echo "Backend root: $BACKEND_ROOT"
}

# Reuse existing Root CA, or create one if missing
setup_root_ca() {
  local ca_key="$CA_DIR/ca.key"
  local ca_crt="$CA_DIR/ca.crt"

  check_not_dir "$ca_key" "$ca_crt"

  local has_key=false has_crt=false
  root_test -s "$ca_key" && has_key=true
  root_test -s "$ca_crt" && has_crt=true

  if [[ "$has_key" == true && "$has_crt" == true ]]; then
    echo "Root CA already exists, reusing it."
    return
  fi
  if [[ "$has_key" != "$has_crt" ]]; then
    fail "Incomplete Root CA in $CA_DIR (ca.key or ca.crt missing)."
  fi

  echo "Creating Root CA in $CA_DIR..."
  as_root install -d -m 0755 "$CA_DIR"
  run_openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
    -keyout "$ca_key" -out "$ca_crt" -subj "/CN=DragBizz Internal CA"
  as_root chmod 600 "$ca_key"
  as_root chmod 644 "$ca_crt"
}

# Set certificate details for each service
service_pki_spec() {
  local service="$1"
  case "$service" in
    Auth)
      CERT_NAME="server"
      CERT_CN="auth"
      CERT_EKU="serverAuth, clientAuth"
      CERT_SAN="URI:spiffe://dragbizz/service/auth,DNS:auth,DNS:dragbizz-auth,DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
      ;;
    Core)
      CERT_NAME="server"
      CERT_CN="core"
      CERT_EKU="serverAuth, clientAuth"
      CERT_SAN="URI:spiffe://dragbizz/service/core,DNS:core,DNS:dragbizz-core,DNS:dragbizz-core-intelligence,DNS:dragbizz-retailer,DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
      ;;
    Payment)
      CERT_NAME="server"
      CERT_CN="payment"
      CERT_EKU="serverAuth, clientAuth"
      CERT_SAN="URI:spiffe://dragbizz/service/payment,DNS:payment,DNS:studio-payment,DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
      ;;
    Utility)
      CERT_NAME="server"
      CERT_CN="utility"
      CERT_EKU="serverAuth, clientAuth"
      CERT_SAN="URI:spiffe://dragbizz/service/utility,DNS:utility,DNS:dragbizz-utility,DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
      ;;
    Config)
      CERT_NAME="server"
      CERT_CN="config"
      CERT_EKU="serverAuth, clientAuth"
      CERT_SAN="URI:spiffe://dragbizz/service/config,DNS:config,DNS:studio-config,DNS:localhost,DNS:host.docker.internal,IP:127.0.0.1"
      ;;
    *)
      fail "Unknown service: $service"
      ;;
  esac
}

cert_is_valid() {
  local ca_crt="$1" cert_file="$2" key_file="$3"
  as_root openssl verify -CAfile "$ca_crt" "$cert_file" >/dev/null 2>&1 || return 1

  local cert_pub key_pub
  cert_pub="$(as_root openssl x509 -in "$cert_file" -noout -pubkey 2>/dev/null)" || return 1
  key_pub="$(as_root openssl pkey -in "$key_file" -pubout 2>/dev/null)" || return 1
  [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]]
}

# Issue cert and key for a service if missing (or re-issue with --force-certs)
setup_service_pki() {
  local service="$1" server_ip="$2" force_certs="$3"
  local svc_lower="${service,,}"
  local svc_pki="$PKI_ROOT/$svc_lower"

  service_pki_spec "$service"
  local ca_crt="$svc_pki/ca.crt"
  local key_file="$svc_pki/${CERT_NAME}.key"
  local crt_file="$svc_pki/${CERT_NAME}.crt"

  check_not_dir "$ca_crt" "$key_file" "$crt_file"

  local has_key=false has_crt=false
  root_test -s "$key_file" && has_key=true
  root_test -s "$crt_file" && has_crt=true

  as_root install -d -m 0755 "$svc_pki"
  if ! root_test -s "$ca_crt" || [[ "$force_certs" == true ]]; then
    as_root cp "$CA_DIR/ca.crt" "$ca_crt"
    as_root chmod 644 "$ca_crt"
  fi

  if [[ "$force_certs" == false ]]; then
    if [[ "$has_key" == true && "$has_crt" == true ]]; then
      if cert_is_valid "$ca_crt" "$crt_file" "$key_file"; then
        echo "PKI ready for $service, reusing existing certs."
        return
      fi
      fail "Existing PKI for $service is invalid or expired. Re-run with --force-certs to re-issue."
    fi
    if [[ "$has_key" != "$has_crt" ]]; then
      fail "Incomplete PKI for $service in $svc_pki. Fix manually or run with --force-certs."
    fi
  fi

  echo "Creating PKI certs for $service..."
  if [[ "$has_key" == false ]]; then
    run_openssl genrsa -out "$key_file" 2048
    as_root chmod 600 "$key_file"
  fi

  local san_line="$CERT_SAN"
  if [[ -n "$server_ip" ]]; then
    san_line="${san_line},IP:${server_ip}"
  fi

  local cnf_file="$TMP_DIR/${svc_lower}-csr.cnf"
  local csr_file="$TMP_DIR/${svc_lower}.csr"
  cat > "$cnf_file" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = ${CERT_CN}
[v3_req]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = ${CERT_EKU}
subjectAltName = ${san_line}
EOF

  run_openssl req -new -key "$key_file" -out "$csr_file" -config "$cnf_file"
  run_openssl x509 -req -in "$csr_file" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$crt_file" -days 3650 -extensions v3_req -extfile "$cnf_file"
  as_root chmod 644 "$crt_file"
}

# Create Auth JWT RSA keys once
setup_auth_jwt_keys() {
  local priv="$AUTH_KEYS_DIR/private.pem"
  local pub="$AUTH_KEYS_DIR/public.pem"

  check_not_dir "$priv" "$pub"

  local has_priv=false has_pub=false
  root_test -s "$priv" && has_priv=true
  root_test -s "$pub" && has_pub=true

  if [[ "$has_priv" == true && "$has_pub" == true ]]; then
    echo "Auth JWT keys already exist, reusing them."
    return
  fi
  if [[ "$has_priv" != "$has_pub" ]]; then
    fail "Incomplete Auth JWT keys in $AUTH_KEYS_DIR (only one key exists)."
  fi

  echo "Creating Auth JWT keys in $AUTH_KEYS_DIR..."
  as_root install -d -m 0755 "$AUTH_KEYS_DIR"
  run_openssl genrsa -out "$priv" 2048
  run_openssl rsa -in "$priv" -pubout -out "$pub"
  as_root chmod 600 "$priv"
  as_root chmod 644 "$pub"
}

setup_pki_and_keys() {
  local production="$1" server_ip="$2" force_certs="$3"
  setup_privileges
  TMP_DIR="$(mktemp -d)"

  setup_root_ca

  local service dir
  for service in "${SERVICES[@]}"; do
    dir="$(service_directory "$service" "$production")"
    if [[ ! -d "$dir" ]]; then
      echo "Skipping PKI for $service ($dir not found)."
      continue
    fi
    setup_service_pki "$service" "$server_ip" "$force_certs"
    if [[ "$service" == "Auth" ]]; then
      setup_auth_jwt_keys
    elif [[ "$service" == "Utility" ]]; then
      as_root install -d -m 0755 "$UTILITY_KEYS_DIR"
    fi
  done
}

# Create Infra .env from .env.example if missing
ensure_infra_env() {
  if [[ -f "$INFRA_ROOT/.env" || ! -f "$INFRA_ROOT/.env.example" ]]; then
    return
  fi

  cp "$INFRA_ROOT/.env.example" "$INFRA_ROOT/.env"
  python3 - "$INFRA_ROOT/.env" "$DEFAULT_BACKEND_ROOT" <<'PY'
import sys
from pathlib import Path

env_path, backend_root = Path(sys.argv[1]), sys.argv[2]
lines = [
    f"BACKEND_ROOT={backend_root}" if line.startswith("BACKEND_ROOT=") else line
    for line in env_path.read_text().splitlines()
]
env_path.write_text("\n".join(lines) + "\n")
PY
  chmod 600 "$INFRA_ROOT/.env"
  load_infra_env
  echo "Created $INFRA_ROOT/.env from .env.example."
}

# Create missing service .env files from env.example (never overwrites existing .env)
ensure_service_env_files() {
  local production="$1"
  local service dir env_file template
  for service in "${SERVICES[@]}"; do
    dir="$(service_directory "$service" "$production")"
    [[ -d "$dir" ]] || continue

    env_file="$dir/.env"
    if [[ -f "$env_file" ]]; then
      continue
    fi

    if [[ -f "$dir/env.example" ]]; then
      template="$dir/env.example"
    elif [[ -f "$dir/.env.example" ]]; then
      template="$dir/.env.example"
    else
      echo "No env.example found for $service ($dir), skipping .env creation."
      continue
    fi

    cp "$template" "$env_file"
    if [[ "$production" == true ]]; then
      python3 - "$env_file" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
overrides = {
    "ENVIRONMENT": "production",
    "NODE_ENV": "production",
    "AI_ENVIRONMENT": "production",
    "OTEL_DEPLOYMENT_ENVIRONMENT": "production",
}
lines = []
for line in env_path.read_text().splitlines():
    key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
    lines.append(f"{key}={overrides[key]}" if key in overrides else line)
env_path.write_text("\n".join(lines) + "\n")
PY
    fi
    chmod 600 "$env_file"
    echo "Created $env_file from $(basename "$template")."
  done
}

# Generate Redis secrets if missing, or verify existing ones are complete
prepare_redis_secrets() {
  local redis_host="$1"
  local secrets_dir="$REDIS_DIR/secrets"
  local required=(
    users.acl monitor_password operator.env
    auth.env core.env payment.env utility.env config.env
  )
  local file missing=0

  if [[ ! -e "$secrets_dir" ]]; then
    echo "Creating Redis secrets..."
    python3 "$REDIS_DIR/scripts/generate-secrets.py" --host "$redis_host"
    return
  fi

  [[ -d "$secrets_dir" ]] || fail "$secrets_dir exists but is not a directory."
  for file in "${required[@]}"; do
    if [[ ! -s "$secrets_dir/$file" ]]; then
      echo "Missing Redis secret: $secrets_dir/$file" >&2
      missing=1
    fi
  done
  ((missing == 0)) || fail "Redis secrets are incomplete. Fix them manually."
  echo "Redis secrets already exist, reusing them."
}

sync_redis_env() {
  local production="$1" redis_host="$2"
  local args=(--infra-root "$INFRA_ROOT" --backend-root "$BACKEND_ROOT" --host "$redis_host")
  [[ "$production" == true ]] && args+=(--production)

  echo "Syncing Redis config to service .env files..."
  python3 "$REDIS_DIR/scripts/configure-services.py" "${args[@]}"
}

start_redis() {
  local production="$1"
  local compose_file="$REDIS_DIR/docker-compose.dev.yml"
  local mode="development"
  if [[ "$production" == true ]]; then
    compose_file="$REDIS_DIR/docker-compose.yml"
    mode="production"
  fi

  local env_args=()
  [[ -f "$INFRA_ROOT/.env" ]] && env_args=(--env-file "$INFRA_ROOT/.env")

  echo "Starting Redis ($mode)..."
  docker compose --project-directory "$REDIS_DIR" \
    "${env_args[@]}" -f "$compose_file" up -d --wait
}

main() {
  local automatic=false
  local production=false
  local force_certs=false
  local no_start=false
  local server_ip="${SERVER_IP:-}"

  # Parse CLI flags
  while (($#)); do
    case "$1" in
      -y|--yes) automatic=true ;;
      --prod) production=true ;;
      --force-certs) force_certs=true ;;
      --no-start) no_start=true ;;
      --ip)
        shift
        (($#)) || fail "--ip requires an IPv4 address."
        server_ip="$1"
        ;;
      --help|-h) usage; return 0 ;;
      *) usage >&2; return 2 ;;
    esac
    shift
  done

  if [[ -n "$server_ip" ]] && ! valid_ipv4 "$server_ip"; then
    fail "Invalid IPv4 address: $server_ip"
  fi

  local redis_host="$DEFAULT_REDIS_HOST"
  if [[ "$automatic" == false ]]; then
    redis_host="$(prompt_redis_host)"
  fi

  check_prerequisites "$no_start"
  ensure_infra_env
  if [[ "$automatic" == true ]]; then
    redis_host="$DEFAULT_REDIS_HOST"
  fi
  setup_pki_and_keys "$production" "$server_ip" "$force_certs"
  ensure_service_env_files "$production"
  prepare_redis_secrets "$redis_host"
  sync_redis_env "$production" "$redis_host"

  if [[ "$no_start" == true ]]; then
    echo "Infra config and PKI ready (Redis start skipped)."
    return 0
  fi

  if [[ "$automatic" == false ]]; then
    local answer mode="development"
    [[ "$production" == true ]] && mode="production"
    read -r -p "Start Redis ($mode) now? [y/N]: " answer
    [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]] || {
      echo "Infra config and PKI ready (Redis not started)."
      return 0
    }
  fi

  start_redis "$production"
  echo "Infra setup complete."
}

main "$@"
