# Gemini CLI — Vertex AI via AIGW using Google ADC

Configures the **Gemini CLI** to route requests through the AIGW gateway to **Gemini models on Google Vertex AI**, authenticated with a short-lived Google Cloud access token from your local **Application Default Credentials (ADC)**. No service account JSON key is required — the gateway forwards your own ADC token to Vertex on each request, and the Google GenAI SDK refreshes that token automatically.

## How it works — and why it differs from Claude Code

This is **not** a mirror of the [Claude Code Vertex ADC guide](claude-code-cli-vertex-adc-auth.md). Claude Code speaks the Anthropic `/v1/messages` protocol, which the gateway exposes natively, and uses an `apiKeyHelper` to mint the ADC bearer token. Gemini CLI has **neither**:

- Gemini CLI only ever emits **Google GenAI native protocol** (`…/models/{model}:generateContent`), never OpenAI or Anthropic format.
- It has no `apiKeyHelper` hook.

The gateway does **not** expose Google's native protocol on its normal handlers (`/v1/messages`, `/v1/chat/completions`) — a request to `…/v1beta/models/{model}:generateContent` returns **404**. The bridge is the gateway's **pass-through proxy** at `/v1/proxy/*`, combined with Gemini CLI's **native Vertex mode**:

```
Gemini CLI  (GOOGLE_GENAI_USE_VERTEXAI=true)
    │
    ├── Google GenAI SDK obtains + auto-refreshes an ADC token
    │
    └── POST https://aigw.portkey.ai/v1/proxy/v1beta1/projects/<proj>/locations/<loc>/publishers/google/models/<model>:streamGenerateContent
            Authorization: Bearer <adc-token>          ← added by the SDK
            x-portkey-virtual-key: vertex-mg           ← from GEMINI_CLI_CUSTOM_HEADERS
            x-portkey-api-key:     <portkey-key>       ← from GEMINI_CLI_CUSTOM_HEADERS
            │
            └── gateway pass-through forwards ADC token → Vertex AI (Gemini)
```

Because the SDK builds the full Vertex path itself (`…/projects/<proj>/locations/<loc>/…`) and appends it to `GOOGLE_VERTEX_BASE_URL`, pointing that variable at the gateway's `/v1/proxy` endpoint lands the request on the pass-through handler. The `Authorization: Bearer` ADC token is forwarded to Vertex; `x-portkey-api-key` authenticates your account to the gateway.

> **The `/v1/proxy` prefix is mandatory.** Pointing `GOOGLE_VERTEX_BASE_URL` at the bare gateway host (`https://aigw.portkey.ai`) returns **404** — the request never reaches a handler.

## Prerequisites

- Gemini CLI installed (verified with **v0.46.0**)
- Google Cloud SDK (`gcloud`) installed
- **ADC** established for a principal with Vertex AI access:
  ```sh
  gcloud auth application-default login
  ```
  The Google GenAI SDK reads ADC from `~/.config/gcloud/application_default_credentials.json` (or `GOOGLE_APPLICATION_CREDENTIALS`) and refreshes it automatically — no `apiKeyHelper`, no TTL to tune.
- A Portkey **virtual key** (e.g. `vertex-mg`) configured on the AIGW with your GCP **project ID + region** and *no* service account JSON, so it accepts the caller-supplied ADC token
- The **Gemini model(s)** you intend to use provisioned/allowed on that integration (e.g. `gemini-3.6-flash`) **and** available in the chosen Vertex region

## 1. Confirm the Vertex provider in the AIGW catalog

Use the same ADC-forwarding integration as the Claude setup: in the Portkey control plane, the Vertex provider is configured with only the **Project ID** and **Region** — no service account JSON key. With no stored credential, the gateway falls back to the OAuth2 access token on the incoming `Authorization` header, which is exactly the ADC token the Gemini CLI SDK sends.

Ensure the Gemini model you want is in the integration's allowed-model list and enabled on your Vertex project in the target region (this guide was verified with `gemini-3.6-flash` in the `global` region).

## 2. Confirm the token flow with curl

Before touching Gemini CLI, prove the end-to-end path with a **Vertex-native** request through the pass-through proxy:

```sh
TOKEN=$(gcloud auth print-access-token) && \
curl "https://aigw.portkey.ai/v1/proxy/v1beta1/projects/<your-project>/locations/global/publishers/google/models/gemini-3.6-flash:generateContent" \
  -H "x-portkey-virtual-key: vertex-mg" \
  -H "x-portkey-api-key: <your-portkey-key>" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"say hi in 3 words"}]}],"generationConfig":{"maxOutputTokens":100}}'
```

A successful response is Vertex-native JSON:

```json
{"candidates":[{"content":{"role":"model","parts":[{"text":"Hello! How can..."}]}}], ...}
```

> **Note on the path**: `gcloud auth print-access-token` here is only for the manual curl. The Gemini CLI itself does **not** use it — the SDK sources the token from ADC directly.

## 3. Configure Gemini CLI

Gemini CLI is driven entirely by environment variables. Set them in your shell profile, or persist them in `~/.gemini/.env` (Gemini CLI auto-loads the first `.env` it finds, searching up from the cwd then `~/.gemini/.env`):

```sh
# ~/.gemini/.env  — or export in ~/.zshrc
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<your-project>
GOOGLE_CLOUD_LOCATION=global
GOOGLE_VERTEX_BASE_URL=https://aigw.portkey.ai/v1/proxy
GEMINI_CLI_CUSTOM_HEADERS="x-portkey-virtual-key: vertex-mg, x-portkey-api-key: <your-portkey-key>"
```

| Variable | Value |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` — selects native Vertex mode (the SDK handles ADC + auto-refresh) |
| `GOOGLE_CLOUD_PROJECT` | Your Vertex project ID — the SDK bakes it into the request path |
| `GOOGLE_CLOUD_LOCATION` | Vertex region where the model is enabled (e.g. `global`, `us-central1`) |
| `GOOGLE_VERTEX_BASE_URL` | **Must** end in `/v1/proxy` — the gateway pass-through endpoint |
| `GEMINI_CLI_CUSTOM_HEADERS` | **Comma-separated** `key: value` pairs — the Portkey virtual key and account API key. (Note: comma-separated, unlike Claude Code's newline-separated `ANTHROPIC_CUSTOM_HEADERS`.) |

> **Unset conflicting keys**: if `GEMINI_API_KEY` or `GOOGLE_API_KEY` are set in your environment, Gemini CLI picks a different auth path. `unset GEMINI_API_KEY GOOGLE_API_KEY` before using Vertex mode.

> **Must be _exported_, not just set**: if you configure these in your shell rather than `~/.gemini/.env`, use `export` — a bare `VAR=value` assignment is a shell-local variable that child processes (like `gemini`) do **not** inherit. In that case the SDK never sees `GOOGLE_VERTEX_BASE_URL` and silently talks **directly to Vertex** via ADC — everything appears to work, but nothing reaches the gateway. Confirm the CLI will actually see them with `env` (not `set`):
> ```sh
> env | grep -E 'GOOGLE_VERTEX|GEMINI_CLI_CUSTOM'   # must list them; empty = not exported
> ```
> `~/.gemini/.env` avoids this entirely — Gemini CLI loads it into the process environment itself.

## 4. Token refresh — automatic

Unlike Claude Code (which re-runs `apiKeyHelper` on a TTL), the Google GenAI SDK's auth layer refreshes the ADC token on its own as long as your underlying ADC session is valid. There is no `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` equivalent to set. If ADC itself expires, re-run `gcloud auth application-default login`.

## 5. Start Gemini CLI

Interactive:

```sh
gemini -m gemini-3.6-flash
```

Headless / automation (bypasses the trusted-folder prompt):

```sh
gemini --skip-trust -m gemini-3.6-flash -p "say hi in exactly 3 words"
```

To confirm requests are landing in the gateway, check the AIGW control plane logs / analytics for your workspace.

## Verification summary

This flow was confirmed end-to-end against `aigw.portkey.ai`:

| Check | Result |
|---|---|
| Vertex-native `:generateContent` via `/v1/proxy/…` + ADC | **200** — real Gemini response |
| Gemini CLI native Vertex mode → `GOOGLE_VERTEX_BASE_URL=…/v1/proxy` | **Works** — model replied through the gateway |
| Base URL without `/v1/proxy` | **404** — confirms the prefix is required and the base URL is honoured |
| Wrong `x-portkey-api-key` | **401** at the gateway — confirms traffic passes through Portkey auth, not direct to Vertex |
| Native `/v1beta/models/…:generateContent` on the bare gateway (Gemini CLI "gateway mode") | **404** — unsupported; this is why native Vertex mode + `/v1/proxy` is used instead |

## Notes & side effects

- **Secret in plaintext**: `x-portkey-api-key` lives unencrypted wherever you set `GEMINI_CLI_CUSTOM_HEADERS` (shell profile or `~/.gemini/.env`). Keep it out of any synced/committed dotfiles. The GCP token stays ephemeral via ADC.
- **Model name = bare Vertex ID**: use `gemini-3.6-flash`, **not** the `anthropic.`-prefixed form used for Claude-on-Vertex. The model ID is carried in the URL path, not translated by the gateway.
- **Pass-through governance**: because this uses the raw pass-through proxy, Portkey applies account auth and logging, but provider-side request translation is minimal — the request/response are Vertex-native. Model allow-listing behaviour may differ from the OpenAI/Anthropic handlers.
- **`0 tokens (0 cents)` in the logs is expected**: the pass-through proxy does not parse the Vertex-native streaming response, so token/cost tallies read zero. This is a logging limitation of `/v1/proxy`, not a failed request — the request still shows in the log with your model, path (`Stream Generate Content`), and user. For token accounting, use the native `/v1/messages` or `/v1/chat/completions` handlers instead.
- **Gemini CLI makes auxiliary flash calls**: alongside your main turn, the CLI issues internal helper requests (e.g. conversation summarization, next-speaker checks) using a default flash model. These appear as extra log rows and may show a different model ID than the one you passed with `-m`.
- **`gemini-cli` "gateway mode" (`GOOGLE_GEMINI_BASE_URL`) does not work here** — it emits the Gemini *Developer API* path (`/v1beta/models/…`) that the AIGW 404s. Use native Vertex mode as documented above.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| CLI works but **nothing appears in the gateway logs** | Env vars set but not **exported**, so `gemini` didn't inherit them and went direct to Vertex via ADC | `export` the vars (or use `~/.gemini/.env`); verify with `env \| grep GOOGLE_VERTEX` (not `set`). While a request runs, `lsof -nP -iTCP -sTCP:ESTABLISHED \| grep node` should show a connection to the gateway IP, not `aiplatform.googleapis.com` |
| `code: 404` from the CLI | `GOOGLE_VERTEX_BASE_URL` missing the `/v1/proxy` suffix, or model/region path wrong | Ensure the base URL ends in `/v1/proxy`; confirm the model exists in the region |
| `status: 401` from the CLI | Bad/missing `x-portkey-api-key`, or ADC expired | Fix the Portkey key in `GEMINI_CLI_CUSTOM_HEADERS`; re-run `gcloud auth application-default login` |
| `Publisher model … was not found` | Model not available in the chosen region / not the right ID | Use a valid Vertex model ID (e.g. `gemini-3.6-flash`) and a region where it's enabled |
| `Model … is not allowed for this integration` (412) | Model not in the virtual key's allow-list | Add the model to the integration in the AIGW control plane |
| CLI uses the wrong auth (browser login, personal account) | `GOOGLE_GENAI_USE_VERTEXAI` not `true`, or `GEMINI_API_KEY`/`GOOGLE_API_KEY` set | Set `GOOGLE_GENAI_USE_VERTEXAI=true`; `unset GEMINI_API_KEY GOOGLE_API_KEY` |
| `not running in a trusted directory` | Headless run in an untrusted folder | Add `--skip-trust`, or set `GEMINI_CLI_TRUST_WORKSPACE=true`, or trust the folder interactively |
| `Reauthentication failed. cannot prompt…` | ADC session expired | `gcloud auth application-default login` |
| `Error: Incomplete JSON segment at the end` / gateway returns empty `{}` body | **Gateway caching enabled (`CACHE_STORE: redis`)** — the self-hosted AIGW buffers the full streaming response before writing to cache; Gemini thinking models take 2+ seconds to produce a first token, causing the buffer to flush as `{}` before Vertex responds | Disable caching for the `/v1/proxy` pass-through: in `docker-compose.yml` remove or set `CACHE_STORE: none`, or add `x-portkey-cache-force-refresh: true` to `GEMINI_CLI_CUSTOM_HEADERS` to bypass the cached entry per-request |

## Updating credentials

- **Rotate the Portkey key or virtual key**: update the `GEMINI_CLI_CUSTOM_HEADERS` value.
- **Switch GCP identity**: `gcloud auth application-default login` (and `gcloud config set project <id>` if needed); the SDK picks up the new ADC automatically.
- **Change project/region**: update `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` (and the AIGW virtual key configuration if the integration's project/region changes).
