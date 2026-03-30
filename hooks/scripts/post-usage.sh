#!/usr/bin/env bash
# PostToolUse hook: warn on low credits after tl commands.

COMMAND="${TOOL_INPUT_command:-}"
OUTPUT="${TOOL_OUTPUT:-}"

# Only act on tl commands
if [[ ! "$COMMAND" =~ ^tl[[:space:]] ]]; then
  exit 0
fi

# Check for 402 (insufficient credits)
if [[ "$OUTPUT" =~ "402" ]] || [[ "$OUTPUT" =~ "Insufficient credits" ]]; then
  echo "WARN: Credits exhausted. Deposit more at https://app.thoughtleaders.io/settings/billing" >&2
  exit 0
fi

# Check for low balance in JSON output
if [[ "$OUTPUT" =~ \"balance_remaining\"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
  BALANCE="${BASH_REMATCH[1]}"
  if [[ "$BALANCE" -lt 500 ]]; then
    echo "WARN: Low credit balance ($BALANCE remaining). Consider depositing more credits." >&2
  fi
fi

exit 0
