# Portkey Gateway Enterprise — Self-Hosted

Deployment configuration for running the Portkey Gateway Enterprise container with Redis, targeting either Docker Compose or Kubernetes (via Helm).

## Prerequisites

- Docker + Docker Compose, **or** a Kubernetes cluster with Helm installed
- Credentials issued by Portkey (client auth token, org UUID, registry credentials)

## Setup

Copy the example env file and fill in your values:

```sh
cp .env.example .env
```

| Variable | Description |
|---|---|
| `PORTKEY_CLIENT_AUTH` | Client auth token issued by Portkey |
| `ORGANISATIONS_TO_SYNC` | Organisation UUID to sync from the control plane |
| `SERVICE_NAME` | Identifier for this gateway instance |
| `PORTKEY_REGISTRY_USERNAME` | Registry username for pulling the enterprise image |
| `PORTKEY_REGISTRY_PASSWORD` | Registry password for pulling the enterprise image |

---

## Docker Compose

```sh
# Pull latest images and start
docker compose pull && docker compose up -d

# View logs
docker compose logs -f aigw-gateway

# Restart gateway only (e.g. after env changes)
docker compose restart aigw-gateway

# Stop
docker compose down
```

The gateway is available at `http://localhost:8787`.

---

## Observability (Prometheus + OpenTelemetry + Jaeger)

`docker-compose.otel.yml` is a separate, optional stack that adds:

- **Prometheus** — scrapes gateway metrics
- **OpenTelemetry Collector** — receives OTLP traces from the gateway and forwards them to Jaeger
- **Jaeger** — all-in-one, in-memory trace storage + UI

It connects to the main stack via the shared `aigw-net` Docker network, so the main stack must be started first.

```sh
# Start the main stack first (creates the shared network)
docker compose up -d

# Then start observability
docker compose -f docker-compose.otel.yml up -d

# Stop observability
docker compose -f docker-compose.otel.yml down
```

| Service | URL |
|---|---|
| Prometheus | `http://localhost:9090` |
| Jaeger UI | `http://localhost:16686` |
| OTLP (gRPC) | `http://localhost:4317` |
| OTLP (HTTP) | `http://localhost:4318` |

Config files live under `otel/`:
- `otel/prometheus.yml` — scrape config
- `otel/otel-collector-config.yaml` — OTLP receiver → Jaeger exporter pipeline

---

## Kubernetes (Helm)

`values.yml` uses `${VAR}` placeholders that must be substituted from your `.env` before passing to Helm. Use `envsubst` to do this inline:

```sh
# Add the Portkey Helm repo (first time only)
helm repo add portkey https://helm.portkey.ai
helm repo update

# Install
export $(grep -v '^#' .env | xargs) && \
  envsubst < values.yml | helm install portkey-gateway portkey/portkey-gateway -f -

# Upgrade (after changing values.yml or .env)
export $(grep -v '^#' .env | xargs) && \
  envsubst < values.yml | helm upgrade portkey-gateway portkey/portkey-gateway -f -

# Uninstall
helm uninstall portkey-gateway
```

The `export $(grep -v '^#' .env | xargs)` step loads your `.env` into the current shell so `envsubst` can substitute them into `values.yml`.

---

## Architecture

```
aigw-gateway (port 8787)
    └── depends on aigw-redis (port 6379, healthcheck gated)

aigw-net (shared Docker network — also used by the OTEL stack)
```

The gateway is stateless. Redis is used only as a cache. Analytics and logs are shipped to Portkey's control plane — nothing is stored locally.

| Env var | Purpose |
|---|---|
| `ALBUS_BASEPATH` | AIRS/Strata management plane |
| `CONTROL_PLANE_BASEPATH` | AI gateway API |
| `CONFIG_READER_PATH` | Model config sync |

---

## Guides

- [EntraID (OIDC/JWT) authentication for Claude Desktop](docs/aigw-entraid-claude-jwt-auth.md)
- [Claude Code CLI → Vertex AI via ADC](docs/claude-code-cli-vertex-adc-auth.md)
