# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

**tl-cli** is a Python CLI for querying ThoughtLeaders sponsorship data (deals, channels, brands, uploads, snapshots, reports). Built with Typer + Rich + httpx. Designed as an "agent-first dumb tool" — the CLI handles structured commands and output, while the user's AI agent (Claude) provides intelligence.

## Development Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # Editable install (registers `tl` command)

# Test
pytest                              # All tests
pytest tests/test_auth.py           # Single file
pytest tests/test_filters.py -k "test_quoted"  # Single test by name

# Lint
ruff check src/ tests/              # Lint (rules: E, F, I, W)
ruff format --check src/ tests/     # Format check
ruff check --fix src/ tests/        # Auto-fix

# Run
tl --help                           # CLI entry point
tl describe --json                  # Schema discovery (free, no auth needed for structure)
```

Ruff config: Python 3.10 target, 100-char line length.

## Architecture

### Entry Point & Command Registration

`src/tl_cli/main.py` creates the root Typer app and registers all subcommands via `app.add_typer()`. The console script `tl` maps to `tl_cli.main:app`.

### Command Pattern (all data commands follow this)

Every command in `src/tl_cli/commands/` follows the same structure:
1. Accept positional args (optional ID + `key:value` filters)
2. `split_id_and_filters(args)` separates ID from filter dict
3. `get_client()` gets an authenticated `TLClient`
4. If ID: `GET /endpoint/{id}` → `output_single()`. If filters: `GET /endpoint?params` → `output()`
5. `handle_api_error(e)` on failure (maps HTTP status to exit codes)

When adding a new command, copy an existing one (e.g., `deals.py`) and follow the pattern.

### Filter Parsing (`filters.py`)

`parse_filters()` handles `key:value` and `key:"quoted value"` syntax. Returns `dict[str, str]` passed as query params.

### Auth Flow (`auth/`)

- **PKCE + Auth0**: Browser-based login with localhost callback server (`login.py`)
- **Token Storage** (`token_store.py`): OS keyring primary, `~/.config/tl/credentials.json` fallback (0o600)
- **Env override**: `TL_API_KEY` env var takes priority over keyring (for CI)
- **Auto-refresh**: `TLClient` refreshes expired tokens on 401

### HTTP Client (`client/http.py`)

`TLClient` wraps httpx with auth header injection and automatic token refresh on 401. All API calls go through `_request()`.

### Error Handling (`client/errors.py`)

Exit codes: 1 (forbidden/not-found), 2 (auth required), 3 (rate-limit/server-error), 4 (insufficient credits).

### Output (`output/formatter.py`)

TTY-aware: Rich tables in terminal, JSON when piped. Flags: `--json`, `--csv`, `--md`, `--quiet`. Usage footer (credits charged + balance) goes to stderr. Breadcrumbs suggest next commands.

### Claude Code Plugin Integration

The CLI doubles as a Claude Code plugin:
- `.claude-plugin/plugin.json` — plugin manifest
- `commands/*.md` — slash commands (`/tl`, `/tl-deals`, `/tl-channels`, `/tl-brands`, `/tl-reports`, `/tl-balance`)
- `skills/tl/SKILL.md` — skill definition teaching Claude the CLI workflow
- `agents/tl-analyst.md` — autonomous multi-step analysis agent
- `hooks/` — PreToolUse (auth check, limit guard) and PostToolUse (low balance warning) on `tl` commands

Install with `tl setup claude`.

### API Response Envelope

All list endpoints return: `{ results, total, limit, offset, usage: { credits_charged, credit_rate, balance_remaining }, _breadcrumbs }`.

### Key Environment Variables

- `TL_API_URL` — API base (default: `https://app.thoughtleaders.io`)
- `TL_API_KEY` — Bearer token override for CI/scripts
- `TL_AUTH0_DOMAIN`, `TL_AUTH0_CLIENT_ID`, `TL_AUTH0_AUDIENCE` — Auth0 config
- `TL_LLM_KEY` — User's own LLM key for `tl ask` (avoids surcharge)

### Credit System

Every data query costs credits (rates vary by resource). `tl describe` shows rates, `tl balance` shows remaining. The `402` status means insufficient credits. Hooks automatically warn when balance drops below 500.

### Important Constraint

`tl snapshots video` requires `--channel` flag — Firebolt queries without a channel partition are unbounded.
