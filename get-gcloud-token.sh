#!/bin/bash
# get-gcloud-token.sh — apiKeyHelper for Claude Code CLI (Vertex AI via ADC)
#
# Outputs a Google Cloud ADC access token to stdout; Claude Code sends it as
# the Authorization: Bearer token to the AIGW gateway (see
# docs/claude-code-cli-vertex-adc-auth.md).
#
# Unlike a bare `gcloud auth print-access-token` apiKeyHelper, this script
# falls back to an interactive `gcloud auth login` when the local gcloud
# session itself has expired (not just the short-lived access token, which
# gcloud already refreshes on its own from the stored refresh token).
#
# Installation:
#   cp get-gcloud-token.sh ~/.claude/get-gcloud-token.sh
#   chmod +x ~/.claude/get-gcloud-token.sh
#
# ~/.claude/settings.json:
#   "apiKeyHelper": "~/.claude/get-gcloud-token.sh"

set -euo pipefail

# ── Try to get a token silently from the current gcloud session ──────────────
ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null) || ACCESS_TOKEN=""

# ── If the session has expired or there's no active login, trigger an
#    interactive browser login, then retry ────────────────────────────────────
if [ -z "$ACCESS_TOKEN" ]; then
    echo "gcloud session expired or not logged in. Launching browser for login..." >/dev/tty
    gcloud auth login >/dev/tty 2>&1

    ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null) || ACCESS_TOKEN=""
fi

if [ -z "$ACCESS_TOKEN" ]; then
    echo "Error: Failed to obtain a token from gcloud." >&2
    exit 1
fi

# Output ONLY the token — Claude Code reads this as the bearer token
echo "$ACCESS_TOKEN"
