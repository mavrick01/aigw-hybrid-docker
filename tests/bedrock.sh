#! /bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a
source "$SCRIPT_DIR/.env"
set +a

TOKEN=${AIGW_TEST_API_KEY}

# Use the first argument ($1) as the model if provided, otherwise (or if "-") default to gpt-5.6-sol
if [ -z "$1" ] || [ "$1" = "-" ]; then
  MODEL="global.openai.gpt-5.6-sol"
else
  MODEL="$1"
fi
# Remaining arguments ($2 onward) are joined as the prompt, otherwise default to `hello`
shift || true
PROMPT="${*:-hello}"

echo "BEDROCK TEST"
echo "==========="
echo "Using model: $MODEL"

curl https://aigw.portkey.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
      "model": "'"$MODEL"'",
      "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "'"$PROMPT"'"}
      ],
      "max_tokens": 512
    }'


