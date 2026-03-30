#!/usr/bin/env bash
# PreToolUse hook: validate auth and check credits before tl commands.
# Only runs when the Bash command starts with "tl ".

COMMAND="${TOOL_INPUT_command:-}"

# Only act on tl commands
if [[ ! "$COMMAND" =~ ^tl[[:space:]] ]]; then
  exit 0
fi

# Skip for system commands that don't need auth
if [[ "$COMMAND" =~ ^tl[[:space:]]+(auth|doctor|--help|--version|describe) ]]; then
  exit 0
fi

# Check auth
if ! tl auth status --quiet 2>/dev/null; then
  echo "WARN: Not authenticated. Run 'tl auth login' first." >&2
  exit 0  # Don't block, just warn
fi

# For list commands without explicit limit, suggest adding one
if [[ "$COMMAND" =~ ^tl[[:space:]]+(deals|uploads|channels|brands)[[:space:]] ]]; then
  if [[ ! "$COMMAND" =~ limit: ]] && [[ ! "$COMMAND" =~ --limit ]]; then
    echo "HINT: Consider adding a limit to control credit usage (e.g., limit:50)" >&2
  fi
fi

exit 0
