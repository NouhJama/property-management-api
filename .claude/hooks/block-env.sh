#!/usr/bin/env bash
input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

if [[ "$file_path" == *".env"* ]]; then
    echo "Blocked: direct access to .env files is not permitted. Use app/core/config.py (Settings) to read environment variables." >&2
    exit 2
fi

exit 0
