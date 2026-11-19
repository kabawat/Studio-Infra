# Infra Service — Step-by-Step Setup Guide (Development & Production)

Development and Production share the same `/etc/studio/` paths and Redis port (`6379` on `redis-private`); only `.env` values (`BACKEND_ROOT`, etc.) and the start flag/compose file differ.

See [README.md](README.md) for service overview.

---

## Option 1: Automated Setup (All `be/*` Services on Same Server)

If `Auth`, `Core`, `Finance`, `Utility`, and `Brain` are all in the same `be/` root, `setup.sh` automatically creates any missing `.env` files (for `Infra` and all services from their templates), `Brain/data`, the Root CA (`/etc/studio/pki/ca/`), all service mTLS certs/keys (`/etc/studio/pki/auth/`, `core/`, `finance/`, `utility/`, `brain/`), Auth JWT keys (`/etc/studio/auth/keys/`), `/etc/studio/utility/keys/`, Redis secrets, `.env` Redis sync, and starts Redis. (Existing `.env` files, keys, and valid certs are reused.)

Navigate to the Infra directory:
```bash
cd be/Infra
```

Run automated setup in Development mode:
```bash
bash setup.sh -y
```

Or run automated setup in Production mode:
```bash
bash setup.sh --prod -y
```

*(Note: Fill in environment-specific values such as `MONGODB_URI`, payment/API keys, and `INTERNAL_SERVICE_SECRET` in any newly created `.env` files before starting the services.)*

*(Optional flags: `--ip` followed by an IPv4 address adds a server IP to service SANs, `--force-certs` re-issues service certs, `--no-start` skips starting Redis.)*

---

## Option 2: Manual Step-by-Step Setup (or Multi-Server CA)

Run **Step 1 to Step 4** sequentially when setting up manually or across separate servers.

### Step 1: Configure `.env`

Navigate to the Infra directory:
```bash
cd be/Infra
```

Create `.env` from the template (configure all environment variables according to your environment):
```bash
cp .env.example .env
```

---

### Step 2: Generate Root CA (`/etc/studio/pki/ca`)

> Skip Step 2 if valid `ca.key` and `ca.crt` already exist. Never copy `ca.key` to other servers.

Create the Root CA directory:
```bash
sudo install -d -m 0755 /etc/studio/pki/ca
```

Generate a 4096-bit RSA Root CA private key and self-signed certificate (valid 10 years):
```bash
sudo openssl req -x509 -newkey rsa:4096 -nodes -days 3650 -keyout /etc/studio/pki/ca/ca.key -out /etc/studio/pki/ca/ca.crt -subj "/CN=studio Internal CA"
```

Restrict `ca.key` permissions to owner-only:
```bash
sudo chmod 600 /etc/studio/pki/ca/ca.key
```

Set read permissions on `ca.crt` (copy this `ca.crt` to `/etc/studio/pki/auth/ca.crt`, `/etc/studio/pki/core/ca.crt`, `/etc/studio/pki/finance/ca.crt`, `/etc/studio/pki/utility/ca.crt`, and `/etc/studio/pki/brain/ca.crt`):
```bash
sudo chmod 644 /etc/studio/pki/ca/ca.crt
```

---

### Step 3: Setup Redis Secrets, `.env` Sync & Start Container

Generate Redis ACL and per-service credentials under `redis/secrets/` (skip if `redis/secrets/` already exists):
```bash
python3 redis/scripts/generate-secrets.py --host redis
```

Sync Redis credentials into sibling service `.env` files (Development):
```bash
python3 redis/scripts/configure-services.py --infra-root . --backend-root .. --host redis
```

Or sync Redis credentials in Production mode:
```bash
python3 redis/scripts/configure-services.py --infra-root . --backend-root .. --host redis --production
```

Start Redis in Development mode (`redis/docker-compose.dev.yml`):
```bash
docker compose --project-directory redis --env-file .env -f redis/docker-compose.dev.yml up -d --wait
```

Or start Redis in Production mode (`redis/docker-compose.yml`):
```bash
docker compose --project-directory redis --env-file .env -f redis/docker-compose.yml up -d --wait
```

---

### Step 4: Sign Service CSRs with Infra CA *(Multi-Server)*

When services are on separate servers, copy each service's generated CSR to `/tmp/` on the Infra CA server, sign it with the Root CA (requires OpenSSL 3.x `-copy_extensions copy` to preserve SPIFFE SANs and usages), copy the signed certificate back, and clean up `/tmp/`:

Copy each service CSR from the service server to the Infra server:
- **Auth:** `/etc/studio/pki/auth/server.csr` → `/tmp/auth.csr`
- **Core:** `/etc/studio/pki/core/server.csr` → `/tmp/core.csr`
- **Finance:** `/etc/studio/pki/finance/server.csr` → `/tmp/finance.csr`
- **Utility:** `/etc/studio/pki/utility/server.csr` → `/tmp/utility.csr`
- **Brain:** `/etc/studio/pki/brain/client.csr` → `/tmp/brain.csr`

Sign each service CSR with Infra's Root CA:
```bash
sudo openssl x509 -req -in /tmp/auth.csr -CA /etc/studio/pki/ca/ca.crt -CAkey /etc/studio/pki/ca/ca.key -CAcreateserial -out /tmp/auth.crt -days 3650 -copy_extensions copy
sudo openssl x509 -req -in /tmp/core.csr -CA /etc/studio/pki/ca/ca.crt -CAkey /etc/studio/pki/ca/ca.key -CAcreateserial -out /tmp/core.crt -days 3650 -copy_extensions copy
sudo openssl x509 -req -in /tmp/finance.csr -CA /etc/studio/pki/ca/ca.crt -CAkey /etc/studio/pki/ca/ca.key -CAcreateserial -out /tmp/finance.crt -days 3650 -copy_extensions copy
sudo openssl x509 -req -in /tmp/utility.csr -CA /etc/studio/pki/ca/ca.crt -CAkey /etc/studio/pki/ca/ca.key -CAcreateserial -out /tmp/utility.crt -days 3650 -copy_extensions copy
sudo openssl x509 -req -in /tmp/brain.csr -CA /etc/studio/pki/ca/ca.crt -CAkey /etc/studio/pki/ca/ca.key -CAcreateserial -out /tmp/brain.crt -days 3650 -copy_extensions copy
```

Copy each signed certificate back into the corresponding service's PKI directory:
- **Auth:** `/tmp/auth.crt` → `/etc/studio/pki/auth/server.crt`
- **Core:** `/tmp/core.crt` → `/etc/studio/pki/core/server.crt`
- **Finance:** `/tmp/finance.crt` → `/etc/studio/pki/finance/server.crt`
- **Utility:** `/tmp/utility.crt` → `/etc/studio/pki/utility/server.crt`
- **Brain:** `/tmp/brain.crt` → `/etc/studio/pki/brain/client.crt`

Remove the temporary CSR and signed cert files from `/tmp` after copying:
```bash
rm -f /tmp/auth.csr /tmp/auth.crt /tmp/core.csr /tmp/core.crt /tmp/finance.csr /tmp/finance.crt /tmp/utility.csr /tmp/utility.crt /tmp/brain.csr /tmp/brain.crt
```

---

## Optional: Post-Setup Validation & Health Checks

*(These commands are for verification and debugging only—not required for setup.)*

Inspect the Root CA certificate subject and validity dates:
```bash
openssl x509 -in /etc/studio/pki/ca/ca.crt -noout -subject -dates
```

Re-apply Redis ACL permissions if upgrading an older `users.acl` file:
```bash
python3 redis/scripts/fix-acl.py
```

Check Redis container status:
```bash
docker ps --filter name=redis
```

Verify Redis responds to `PING`:
```bash
docker exec redis redis-cli ping
```
