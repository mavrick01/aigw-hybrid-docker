# file-to-text-webhook

A `before_request_hooks` webhook guardrail for the AI Gateway. Whenever an
inbound chat request has a file/document content block attached (OpenAI-style
`file` + `file_data`, Bedrock's native Converse `document` block, or
Anthropic's `document` content block), this decodes it to plain text/Markdown
and swaps it into the user's prompt as a `text` content block — before the
request is routed to the model. This lets a non-vision/non-document model
receive file content as text, and for models that *do* support documents
natively, still gets the content into a form other hooks further down the
pipeline (DLP scanning, logging, etc.) can inspect as plain text.

This service is decode-only — it doesn't scan, mask, or block content. If you
want that, add it as another `before_request_hooks` entry after this one (see
"Extending this" below).

## Pipeline, per attachment

1. Pick a decoder based on `DECODE_BACKEND` (env var, default `markitdown`) or
   the per-request `x-decode-backend` header if present:
   - **`markitdown`** (default): try [MarkItDown](https://github.com/microsoft/markitdown)
     first — cheap, local, no extra LLM call.
   - **`model`**: ask the request's own target model to transcribe the
     attachment to Markdown instead, by calling back into this same gateway
     with the unmodified attachment and a "transcribe to Markdown" prompt.
     This reuses whatever native document/vision support the model already
     has — e.g. Nova reads a PDF with embedded/subsetted fonts natively and
     fast, where `pdfminer`-based extraction garbles the same PDF into
     `(cid:n)` glyph-code noise (see "PDF extraction quality" below) — but it
     costs a second LLM call on every attachment.
2. Whichever runs first, if its output looks garbled or empty, falls back to
   the other one. With the default ordering (`DECODE_BACKEND=markitdown`)
   this means: MarkItDown runs for free on every attachment, and the model is
   only asked when that output looks unusable. "Unusable" (`is_garbled()` in
   `app.py`) means the text is empty/whitespace, under
   `MIN_PLAUSIBLE_TEXT_LENGTH` (20 chars), or contains `GARBLED_CID_THRESHOLD`
   (5) or more `(cid:n)` glyph-code artifacts — the pdfminer failure mode
   described in "PDF extraction quality" below.
3. If both come back empty (e.g. transient errors), fails open — leaves the
   original attachment untouched rather than breaking the request over our
   own infra hiccups.

## Choosing the backend

```sh
# deployment-wide default
DECODE_BACKEND=model   # in docker/.env, to try the model first instead

# or per-request, no redeploy needed
curl ... -H "x-decode-backend: model" ...
```

## Loop prevention

The `model` backend's callback hits the exact same gateway endpoint this
guardrail is attached to, with the same attachment still present — which
would re-trigger this webhook recursively. The callback is stamped with
`x-file-to-text-webhook: transcribe-call`; on receiving a request with that
header, the webhook returns `{"verdict": true}` immediately without
processing anything.

**This only works if `x-file-to-text-webhook` is itself in the guardrail's
`forwardHeaders` list** (see below) — it's *our own outgoing* call that sets
this header, but the gateway won't relay it back to us on the recursive
incoming webhook call unless it's explicitly allowlisted there. Without it,
the inner call re-runs the full decode (MarkItDown/DocIntel can take several
seconds), blows through the guardrail's `timeout`, and the whole inner
transcription call fails — which silently falls back to MarkItDown for the
outer request instead of actually testing the model backend. Confirmed this
the hard way: omitting it from `forwardHeaders` produces a `400`/`401` from
the inner call in the logs, caused by a `TimeoutError` on the *same* guardrail
firing again on itself, not an actual auth problem.

**The callback does *not* use the caller's own `Authorization` header** —
`forwardHeaders` rejects `"authorization"` outright (confirmed: the gateway's
guardrail-update API returns a validation error on any attempt to list it,
regardless of value; this is a deliberate block on leaking a caller's bearer
token to an arbitrary webhook URL). Instead `transcribe_via_model()`
authenticates with its own service credential, `INTERNAL_GATEWAY_API_KEY`
(see `.env.example`) — a key dedicated to this webhook, not tied to whoever
happens to be calling the gateway. If it's unset, the `model` backend logs a
warning and returns nothing, which just falls through to the `markitdown`
fallback.

## Extending this

To add scanning/masking/blocking on top of the decoded text (e.g. a DLP
scan), do it in `convert_block_to_text()` in `app.py`, right after
`decode_attachment()` returns `text` and before it's wrapped into the
replacement content block — that's the one place every attachment's plain
text passes through regardless of which backend produced it. To actually
deny a request from there, return `{"verdict": false}` from the `/webhook`
handler; whether the gateway turns that into a hard 446 or a soft 200 deny is
controlled entirely by the guardrail's own config in the Strata UI, not by
this service.

## Run it

It's defined as the `file-to-text-webhook` service in
`../docker/docker-compose.file-to-text.yml`, a separate optional stack that
joins the main stack on the shared `aigw-net` network (start the main stack
first, see `../docker/README.md` / repo root `README.md`):

```sh
cd ../docker
docker compose -f docker-compose.file-to-text.yml up -d --build
```

Health check: `curl http://127.0.0.1:3060/healthz`

(It can also be run standalone with `docker build` + `docker run`, but it must
be attached to the `aigw-net` network — see "Wire it into the gateway" below
for why `host.docker.internal` / public hostnames don't work here.)

## Wire it into the gateway

**Reach it by its Docker service/container name (`file-to-text-webhook`), not
`host.docker.internal` or a public hostname.** The gateway's webhook-guardrail
SSRF check does its own DNS resolution rather than honoring `extra_hosts`
overrides, and hard-blocks `host.docker.internal` regardless of the
`TRUSTED_CUSTOM_HOSTS` allowlist. Putting the webhook on `aigw-net` and
referencing it by service name uses Docker's internal DNS directly — the same
pattern already used for `aigw-redis` and `otel-collector`. The service name
is already added to `TRUSTED_CUSTOM_HOSTS` in `docker-compose.yml`.

Add a `before_request_hooks` entry (inline on an `x-portkey-config`, or as a
workspace/org default) pointing at this service:

```jsonc
{
  "before_request_hooks": [
    {
      "id": "file-to-text-mutator",
      "type": "guardrail",
      "deny": false,
      "checks": [
        {
          "id": "default.webhook",
          "parameters": {
            "webhookURL": "http://file-to-text-webhook:3060/webhook",
            "timeout": 15000,
            "failOnError": false,
            "forwardHeaders": ["x-decode-backend", "x-file-to-text-webhook"]
          }
        }
      ]
    }
  ]
}
```

- `deny: false` — this hook only transforms the request, it never blocks it
  (unless you extend it per "Extending this" above, in which case flip this
  to `true`).
- `failOnError: false` — if the webhook times out or errors, the gateway
  proceeds with the original (untouched) request rather than failing the call.
- `timeout: 15000` — a model-transcription fallback call can take longer than
  the 3s default; bump this further if you're seeing timeouts on larger files.

Alternatively, create it as a **Webhook Guardrail** in the Strata UI
(Add Guardrail → Webhook Guardrail → `http://file-to-text-webhook:3060/webhook`)
and reference its `pg-...` slug in `before_request_hooks` instead of the
inline block above — same pattern used in `../smart-routing/`.

### Updating an existing guardrail via the `airs` CLI

The deployed guardrail (`pg-webhoo-1dd51b` / UUID `e4f956eb-cf1c-438a-84f2-dd16ba36d66d`,
workspace `Col_Workspace`) can be read and updated from the command line instead
of the Strata UI, via the `airs` CLI's `gateway guardrails` commands:

```sh
airs gateway guardrails get <uuid> --output json
airs gateway guardrails update <uuid> --checks '<full checks array as JSON>' --output json
```

Two non-obvious gotchas hit while doing this:

- **`--checks` replaces the whole array**, and each check object must include
  `"is_enabled": true` — omitting it (even though it's not documented as part
  of the write schema, and doesn't appear in Portkey's own create-guardrail
  examples for other check types) makes the server reject the request with
  `AB01: Request Validation Error`, with no indication of which field was
  wrong. Easiest fix: `get` the current resource first and edit that JSON
  in place rather than hand-rolling it from docs examples.
- **`forwardHeaders` containing `"authorization"` is rejected** the same way,
  for any value — this is what led to the `INTERNAL_GATEWAY_API_KEY` design
  above instead of forwarding the caller's token.

After changing `TRUSTED_CUSTOM_HOSTS`, recreate `aigw-gateway` (it's read at
startup only):

```sh
cd ../docker
docker compose up -d aigw-gateway
```

## Response shape the gateway actually requires

Portkey's public docs say a `beforeRequestHook` transform is applied from
`transformedData.request` (the bare request body). That's not what the
deployed gateway binary does — pulled straight from
`/app/build/start-server.js` inside the `aigw-gateway` container:

```js
u.transformedData?.request?.json && "beforeRequestHook" === r && !l &&
  (s.request.json = u.transformedData.request.json, c = !0)
```

It only applies the transform if `transformedData.request.json` is present —
i.e. the response must wrap the modified body in the **same `{json: ...}`
shape** the gateway sent as `request.json` in the payload, not place it
directly under `request`. `app.py` returns:

```json
{"verdict": true, "transformedData": {"request": {"json": <modified request body>}}}
```

A response shaped as `{"transformedData": {"request": <modified request body>}}`
(matching the public docs) is silently ignored — `verdict` still comes back
`true` and `transformed` is left `false` in `hook_results`, with no error, so
this is easy to miss.

## PDF extraction quality: pdfminer vs. Azure Document Intelligence

By default MarkItDown extracts PDF text with `pdfminer`. On PDFs with
embedded/subsetted fonts and non-standard character encodings (common with
some hotel/invoice/booking-system PDF generators), `pdfminer` can't map glyph
IDs back to characters and the extracted text comes out as `(cid:123)` noise
instead of readable text — this isn't a bug in this service, it's a limitation
of the font's encoding not being recoverable without OCR/layout analysis.

To fix this, point MarkItDown at an **Azure AI Document Intelligence**
resource instead (OCR/layout-based extraction, handles these PDFs correctly):

1. Create the resource (Azure Portal → "Document Intelligence", or):
   ```sh
   az cognitiveservices account create \
     --name <resource-name> \
     --resource-group <rg> \
     --kind FormRecognizer \
     --sku S0 \
     --location <region>
   ```
2. Note its endpoint (`https://<resource-name>.cognitiveservices.azure.com/`)
   and a key:
   ```sh
   az cognitiveservices account keys list --name <resource-name> --resource-group <rg>
   ```
3. Set `DOCINTEL_ENDPOINT` and `AZURE_API_KEY` in `../docker/.env` (see
   `.env.example`), then recreate the service:
   ```sh
   cd ../docker
   docker compose -f docker-compose.file-to-text.yml up -d --build
   ```
   Check the logs for `Using Azure Document Intelligence backend at ...` to
   confirm it picked up the config.

Leaving both env vars unset keeps the default `pdfminer` backend — no code
change needed either way, `app.py` switches backends based on whether
`DOCINTEL_ENDPOINT` is set. The required Python deps (`azure-ai-documentintelligence`,
`azure-identity`) are already pulled in by `markitdown[all]` in
`requirements.txt`, via MarkItDown's `[az-doc-intel]` extra.

If `AZURE_API_KEY` is left unset but `DOCINTEL_ENDPOINT` is set, MarkItDown
falls back to `DefaultAzureCredential()` — which won't find any ambient Azure
AD credentials inside this plain container, so set the key unless you've
separately wired in a managed identity/service principal.
