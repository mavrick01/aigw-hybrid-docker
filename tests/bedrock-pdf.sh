#! /bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
source "$SCRIPT_DIR/.env"
set +a

TOKEN=${AIGW_TEST_API_KEY}

# Use the first argument ($1) as the model if provided, otherwise (or if "-") default to nova-2-lite
if [ -z "$1" ] || [ "$1" = "-" ]; then
  MODEL="@test-bedrock/global.amazon.nova-2-lite-v1:0"
else
  MODEL="$1"
fi

# Second argument is the file to attach, defaulting to a sample PDF if omitted/"-"
if [ -z "$2" ] || [ "$2" = "-" ]; then
  FILE_PATH="/Users/mgollop/Downloads/Palo Alto Networks Inc Mail - Booking Confirmation.pdf"
else
  FILE_PATH="$2"
fi

if [ ! -f "$FILE_PATH" ]; then
  echo "File not found: $FILE_PATH" >&2
  exit 1
fi

EXT="${FILE_PATH##*.}"
EXT_LOWER=$(printf '%s' "$EXT" | tr '[:upper:]' '[:lower:]')
case "$EXT_LOWER" in
  pdf|csv|doc|docx|xls|xlsx|html|txt|md) FORMAT="$EXT_LOWER" ;;
  *)
    echo "Unsupported file extension for Bedrock document input: .$EXT" >&2
    echo "Supported: pdf, csv, doc, docx, xls, xlsx, html, txt, md" >&2
    exit 1
    ;;
esac

FILE_B64=$(base64 -i "$FILE_PATH" | tr -d '\n')

# Remaining arguments ($3 onward) are joined as the prompt, otherwise default to a summary request
shift 2 || true
PROMPT="${*:-Please summarize this document.}"

echo "BEDROCK PDF TEST"
echo "================"
echo "Using model: $MODEL"
echo "Attaching: $FILE_PATH"

jq -n \
  --arg model "$MODEL" \
  --arg prompt "$PROMPT" \
  --arg format "$FORMAT" \
  --arg filename "$(basename "$FILE_PATH" ".$EXT")" \
  --arg data "$FILE_B64" \
  '{
    model: $model,
    messages: [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": [
        {"type": "text", "text": $prompt},
        {"type": "document", "document": {"format": $format, "name": $filename, "source": {"bytes": $data}}}
      ]}
    ],
    max_tokens: 512
  }' | curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d @-
