#! /bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
source "$SCRIPT_DIR/.env"
set +a

# Fetch the token
TOKEN=$(az account get-access-token --resource ${AZURE_JWT_RESOURCE} --tenant ${AZURE_JWT_DIRECTORY} --query   "accessToken" -o tsv)

# Use the first argument ($1) if provided, otherwise default to Sonnet
MODEL="${1:-@vertex/anthropic.claude-sonnet-5}"

echo "VERTEX TEST"
echo "==========="
echo "Using model: $MODEL"

curl -k https://aigw.portkey.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
      "model": "'"$MODEL"'",
      "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the tallest mountain in tasmania,Australia"}
      ],
      "max_tokens": 512
    }'


