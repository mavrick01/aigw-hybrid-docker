# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A minimal deployment configuration for running the **Portkey Gateway Enterprise** self-hosted container alongside Redis. Two deployment targets are covered:

- **Docker Compose** (`docker-compose.yml`) — local or VM-based deployment
- **Helm/Kubernetes** (`values.yml`) — Kubernetes deployment via Portkey's Helm chart

A third, optional file (`docker-compose.otel.yml`) adds an observability stack (Prometheus, OpenTelemetry Collector, Jaeger) that attaches to the main stack via a shared external network.

## Common commands

```sh
# Pull latest images and start services
docker compose pull && docker compose up -d

# View logs
docker compose logs -f portkey-gateway

# Stop services
docker compose down

# Restart gateway only (e.g. after env changes)
docker compose restart portkey-gateway

# Start observability stack (after the main stack is up)
docker compose -f docker-compose.otel.yml up -d
```

## Architecture

```
portkey-gateway (port 8787)
    └── depends on portkey-redis (port 6379, healthcheck gated)
```

The gateway is stateless; Redis is used solely as a cache (`CACHE_STORE: redis`). Analytics and logs are shipped to Portkey's control plane (`control_plane`) — nothing is stored locally.

The gateway calls out to three Portkey endpoints:

| Env var | Purpose |
|---|---|
| `ALBUS_BASEPATH` | Management plane (AIRS/Strata) |
| `CONTROL_PLANE_BASEPATH` | AI gateway API |
| `CONFIG_READER_PATH` | Model config sync |

## Environment variables

Required at runtime (supply via `.env` or shell export):

| Variable | Description |
|---|---|
| `PORTKEY_CLIENT_AUTH` | Client auth token issued by Portkey |
| `ORGANISATIONS_TO_SYNC` | Organisation UUID to sync from the control plane |
| `SERVICE_NAME` | Identifier for this gateway instance |

`values.yml` duplicates these for Kubernetes — keep them out of version control (use a secrets manager or `.gitignore` the file).
