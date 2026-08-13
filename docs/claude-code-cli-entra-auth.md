# Claude Code CLI — EntraID JWT Authentication via AIGW

Configures the Claude Code CLI to send requests through this self-hosted AIGW gateway, authenticated using a Microsoft EntraID JWT bearer token obtained automatically from the local Azure CLI session.

## How it works

Claude Code CLI supports an `apiKeyHelper` — a script it calls on each session start whose stdout becomes the bearer token sent to the gateway. `get-az-token.sh` in this repo fills that role: it fetches an EntraID access token from the local Azure CLI cache, triggering an interactive browser login if the session has expired.

```
Claude Code CLI
    │
    ├── calls get-az-token.sh → outputs EntraID access token
    │
    └── sends requests to AIGW gateway (http://localhost:8787)
            Authorization: Bearer <token>
            │
            └── gateway validates JWT, maps claims → Portkey workspace
```

## Prerequisites

- The gateway stack running (`docker compose up -d`)
- The EntraID app registration configured for this gateway — see [entraid-jwt-auth guide](aigw-entraid-claude-jwt-auth.md) or run `Setup-EntraID-App.sh`
- `ENTRAID_CLIENT_ID` and `ENTRAID_TENANT_ID` in your `.env` (added automatically by `Setup-EntraID-App.sh`)
- Azure CLI installed (`brew install azure-cli`) and logged in at least once

## 1. Install the token helper script

Copy `get-az-token.sh` from this repo to your Claude config directory:

```sh
cp get-az-token.sh ~/.claude/get-az-token.sh
chmod +x ~/.claude/get-az-token.sh
```

The script reads `ENTRAID_CLIENT_ID` and `ENTRAID_TENANT_ID` from the environment. The cleanest way to supply them is via Claude Code's settings (step 2), but if you keep the script in the repo directory it will also fall back to loading them from the local `.env`.

## 2. Configure Claude Code CLI

Edit (or create) `~/.claude/settings.json`:

```json
{
  "apiKeyHelper": "/Users/<you>/.claude/get-az-token.sh",
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8787",
    "ENTRAID_CLIENT_ID": "<your-app-client-id>",
    "ENTRAID_TENANT_ID": "<your-tenant-id>"
  }
}
```

| Field | Value |
|---|---|
| `apiKeyHelper` | Absolute path to `get-az-token.sh` |
| `ANTHROPIC_BASE_URL` | Your gateway URL — use `http://localhost:8787` for local Docker |
| `ENTRAID_CLIENT_ID` | The Application (client) ID from the EntraID app registration |
| `ENTRAID_TENANT_ID` | Your Entra tenant ID |

> **Tip:** If you have `ENTRAID_CLIENT_ID` and `ENTRAID_TENANT_ID` in your shell environment already (e.g. via `direnv`), you can omit the `env` block and the script will pick them up directly.

## 3. Set the Portkey config header (optional)

If your gateway requires an explicit routing config (the `x-portkey-config` header), the recommended approach is to configure a **default config** in the AIGW control plane for your workspace — the gateway then applies it to all requests that don't carry the header explicitly.

Alternatively, you can set it per-project in `.claude/settings.json` at the repo level if Claude Code CLI adds support for custom request headers in your version.

## 4. Verify

Start Claude Code in a terminal:

```sh
claude
```

On the first run (or after your Azure CLI session expires) a browser window opens to sign in to EntraID. After authentication the session is cached by the Azure CLI — subsequent invocations are silent until the token expires.

To confirm requests are landing in the gateway, check the control plane logs or AIGW analytics for your workspace.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ENTRAID_CLIENT_ID not set` | Variable not in env or `.env` | Add it to the `env` block in `settings.json` or run `Setup-EntraID-App.sh` to populate `.env` |
| `401 Unauthorized` from gateway | JWT invalid or missing claims | Decode the token at [jwt.ms](https://jwt.ms) and verify `portkey_oid` and `email_id` are present; check gateway logs |
| Browser opens on every run | Azure CLI token cache expired | Normal — sign in; the cache is then reused for the token lifetime (~1 h) |
| `az: command not found` | Azure CLI not installed | `brew install azure-cli` |
| Gateway returns `502` / no route | No Portkey config header and no default config set | Set a default config in the AIGW control plane for your workspace |

## Updating credentials

If you rotate the EntraID app registration or move to a different tenant, update `ENTRAID_CLIENT_ID` / `ENTRAID_TENANT_ID` in:

1. `~/.claude/settings.json` (`env` block)
2. The repo `.env` (if you use it as a fallback)
3. Re-run `az login --tenant <new-tenant-id>` to refresh the CLI session
