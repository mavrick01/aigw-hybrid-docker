import base64
import io
import logging
import mimetypes
import os

import httpx
from fastapi import FastAPI, Request
from markitdown import MarkItDown

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("file-to-text-webhook")

app = FastAPI()

DOCINTEL_ENDPOINT = os.environ.get("DOCINTEL_ENDPOINT")
if DOCINTEL_ENDPOINT:
    # Azure AI Document Intelligence backend -- handles scanned/embedded-font
    # PDFs that pdfminer (MarkItDown's default PDF backend) garbles into
    # "(cid:n)" glyph codes. Requires AZURE_API_KEY (or ambient Azure AD
    # credentials for DefaultAzureCredential if no key is set).
    #
    # MarkItDown's DocumentIntelligenceConverter defaults to API version
    # 2024-07-31-preview, which Azure has retired (calls fail with HTTP 410
    # Gone) -- MarkItDown then silently falls back to pdfminer with no error
    # surfaced here. Pin the current GA (v4.0) version instead.
    md = MarkItDown(docintel_endpoint=DOCINTEL_ENDPOINT, docintel_api_version="2024-11-30")
    log.info("Using Azure Document Intelligence backend at %s", DOCINTEL_ENDPOINT)
else:
    md = MarkItDown()

# Content block types the gateway may send an attached file as, depending on
# which provider/format the client used (OpenAI-style file_data, Bedrock's
# native Converse document block, or Anthropic's document content block).
FILE_BLOCK_TYPES = {"file", "document"}

# Which decoder to try first: "markitdown" (default) is cheap/local and tried
# first, falling back to "model" (ask the request's own target model to
# transcribe the attachment to Markdown -- costs a second LLM call) only when
# MarkItDown's output looks garbled or empty. Set DECODE_BACKEND=model to flip
# the default so the model is tried first instead. Either way the other one
# is used as a fallback. Overridable per-request via the DECODE_BACKEND_HEADER
# header (no redeploy needed).
DECODE_BACKEND_HEADER = "x-decode-backend"
DEFAULT_DECODE_BACKEND = os.environ.get("DECODE_BACKEND", "markitdown")

# Sentinel header stamped on our own model-transcription callback so that
# when it re-enters this same before_request_hooks webhook, we recognize it
# and skip straight through instead of recursing.
LOOP_GUARD_HEADER = "x-file-to-text-webhook"
LOOP_GUARD_VALUE = "transcribe-call"

INTERNAL_GATEWAY_URL = os.environ.get(
    "INTERNAL_GATEWAY_URL", "http://aigw-gateway:8787/v1/chat/completions"
)
# A dedicated service credential for this webhook's own callback to the
# gateway (see "model" backend). NOT the caller's own bearer token -- the
# gateway's forwardHeaders explicitly rejects forwarding "authorization" to
# webhook guardrails (blocks credential leakage to arbitrary webhook URLs),
# so this has to be a separate, purpose-scoped key configured on this service.
INTERNAL_GATEWAY_API_KEY = os.environ.get("INTERNAL_GATEWAY_API_KEY")

# If MarkItDown's output has this many (or more) "(cid:n)" glyph-code artifacts,
# or comes back implausibly short, treat it as a failed extraction.
GARBLED_CID_THRESHOLD = 5
MIN_PLAUSIBLE_TEXT_LENGTH = 20


def guess_extension(media_type: str | None, declared_format: str | None) -> str:
    if declared_format:
        return declared_format if declared_format.startswith(".") else f".{declared_format}"
    if media_type:
        ext = mimetypes.guess_extension(media_type)
        if ext:
            return ext
    return ""


def extract_attachment(block: dict) -> tuple[bytes, str, str] | None:
    """Pull (raw_bytes, display_name, extension) out of a file/document content block."""
    block_type = block.get("type")

    if block_type == "file":
        # OpenAI-style: {"type": "file", "file": {"filename": ..., "file_data": "data:<mime>;base64,..."}}
        file_obj = block.get("file") or {}
        data_url = file_obj.get("file_data", "")
        if not data_url.startswith("data:"):
            return None
        header, _, b64data = data_url.partition(",")
        media_type = header[len("data:"):].split(";")[0] if header.startswith("data:") else None
        name = file_obj.get("filename", "attachment")
        ext = guess_extension(media_type, None)
        return base64.b64decode(b64data), name, ext

    if block_type == "document":
        doc = block.get("document")
        if isinstance(doc, dict):
            # Bedrock Converse-native: {"type": "document", "document": {"format": "pdf", "name": ..., "source": {"bytes": "<base64>"}}}
            b64data = doc.get("source", {}).get("bytes")
            if not b64data:
                return None
            name = doc.get("name", "attachment")
            ext = guess_extension(None, doc.get("format"))
            return base64.b64decode(b64data), name, ext

        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            # Anthropic-native: {"type": "document", "source": {"type": "base64", "media_type": ..., "data": "<base64>"}}
            b64data = source.get("data")
            if not b64data:
                return None
            media_type = source.get("media_type")
            ext = guess_extension(media_type, None)
            return base64.b64decode(b64data), "attachment", ext

    return None


def is_garbled(text: str | None) -> bool:
    if not text or len(text.strip()) < MIN_PLAUSIBLE_TEXT_LENGTH:
        return True
    return text.count("(cid:") >= GARBLED_CID_THRESHOLD


def extract_via_markitdown(raw_bytes: bytes, ext: str) -> str | None:
    try:
        result = md.convert_stream(io.BytesIO(raw_bytes), file_extension=ext or None)
    except Exception:
        log.exception("MarkItDown failed to convert attachment")
        return None
    return result.text_content


async def transcribe_via_model(model: str, block: dict) -> str | None:
    """Ask the original target model to transcribe its own attachment to Markdown.

    Calls back into this same gateway, stamped with LOOP_GUARD_HEADER so that
    when this before_request_hooks webhook fires again for the inner call, it
    short-circuits instead of recursing. Authenticates with its own service
    credential (INTERNAL_GATEWAY_API_KEY), not the caller's token.
    """
    if not INTERNAL_GATEWAY_API_KEY:
        log.warning("INTERNAL_GATEWAY_API_KEY not set; skipping model transcription")
        return None

    headers = {
        "Authorization": f"Bearer {INTERNAL_GATEWAY_API_KEY}",
        "Content-Type": "application/json",
        LOOP_GUARD_HEADER: LOOP_GUARD_VALUE,
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Transcribe this document into clean, well-structured Markdown. "
                        "Preserve tables and headings. Output only the Markdown, no commentary.",
                    },
                    block,
                ],
            }
        ],
        "max_tokens": 2048,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(INTERNAL_GATEWAY_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content if isinstance(content, str) else None
    except Exception:
        log.exception("Model transcription fallback failed")
        return None


async def decode_attachment(
    block: dict, raw_bytes: bytes, ext: str, model: str, backend: str
) -> str | None:
    """Try the preferred backend first, falling back to the other one if it comes back unusable."""
    if backend == "markitdown":
        text = extract_via_markitdown(raw_bytes, ext)
        if is_garbled(text):
            log.info("MarkItDown output looked garbled/empty, falling back to model transcription")
            text = await transcribe_via_model(model, block)
    else:
        text = await transcribe_via_model(model, block)
        if is_garbled(text):
            log.info("Model transcription came back empty, falling back to MarkItDown")
            text = extract_via_markitdown(raw_bytes, ext)
    return text


async def convert_block_to_text(block: dict, model: str, backend: str) -> dict | None:
    extracted = extract_attachment(block)
    if not extracted:
        return None
    raw_bytes, name, ext = extracted

    text = await decode_attachment(block, raw_bytes, ext, model, backend)
    if not text:
        # Couldn't get usable text out of it either way -- leave the original
        # attachment for the model to handle natively.
        return None

    label = f"{name}{ext}" if ext and not name.endswith(ext) else name
    return {
        "type": "text",
        "text": f"[Extracted content from attached file: {label}]\n\n{text}",
    }


async def rewrite_messages(request_json: dict, model: str, backend: str) -> bool:
    """Replace file/document content blocks in-place with extracted text. Returns True if anything changed."""
    changed = False
    for message in request_json.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in FILE_BLOCK_TYPES:
                text_block = await convert_block_to_text(block, model, backend)
                if text_block is not None:
                    new_content.append(text_block)
                    changed = True
                    continue
            new_content.append(block)
        message["content"] = new_content
    return changed


@app.post("/webhook")
async def webhook(req: Request):
    if req.headers.get(LOOP_GUARD_HEADER) == LOOP_GUARD_VALUE:
        return {"verdict": True}

    payload = await req.json()

    if payload.get("eventType") != "beforeRequestHook":
        return {"verdict": True}

    request_json = payload.get("request", {}).get("json") or {}
    model = request_json.get("model", "")

    backend = req.headers.get(DECODE_BACKEND_HEADER, DEFAULT_DECODE_BACKEND).lower()

    changed = await rewrite_messages(request_json, model, backend)

    if changed:
        log.info("Rewrote file/document attachment(s) into text content (backend=%s)", backend)
        # The gateway only applies this when it finds transformedData.request.json
        # (the same wrapped shape it sent us) -- a bare "request" key is ignored.
        return {"verdict": True, "transformedData": {"request": {"json": request_json}}}

    return {"verdict": True}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
