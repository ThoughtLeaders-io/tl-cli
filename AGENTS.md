# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

**tl-cli** is a Python CLI for querying ThoughtLeaders sponsorship data (sponsorships, channels, brands, uploads, snapshots, reports). Built with Typer + Rich + httpx. Designed as an "agent-first tool" — the CLI handles structured commands and output, while the user's AI agent (Claude) provides intelligence.

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

`src/tl_cli/main.py` creates the root Typer app and registers all subcommands via `app.add_typer()`. The console script `tl` maps to `tl_cli.main:cli`, which wraps the Typer app with top-level error handling (respects `--debug`). System commands (`auth`, `setup`, `balance`, `doctor`, `whoami`) are free and don't cost credits.

### Command Pattern (all data commands follow this)

Every data command in `src/tl_cli/commands/` uses explicit Typer subcommands:
- `list` — list/search with `key:value` filters as positional args
- `show` — detail view by ID
- `create` / `add` — create new records (where applicable)

Running a command with no subcommand defaults to `list`. Shared logic for sponsorship commands lives in `sponsorships.py` (`do_list`, `do_show`, `do_create`).

When adding a new data command, follow this pattern. See `sponsorships.py` for the reference implementation.

`deals`, `matches`, and `proposals` are shortcut commands that delegate to sponsorships' `do_list`/`do_show`/`do_create` with a pre-set status filter. They reject explicit `status:` filters — users should use `tl sponsorships list` for finer-grained status filtering.

### Filter Parsing (`filters.py`)

`parse_filters()` handles `key:value` and `key:"quoted value"` syntax. Returns `dict[str, str]` passed as query params. Date filter keys (listed in `DATE_FILTER_KEYS` — e.g. `since`, `created-at`, `created-at-start`, `publish-date-end`) accept keywords `today`, `yesterday`, `tomorrow`. Sponsorship date fields (`created-at`, `publish-date`, `purchase-date`, `send-date`) each expose three filter shapes: bare `<field>:<date>` matches within that date/period, and `<field>-start:` / `<field>-end:` give inclusive lower/upper bounds (both sides inclusive; partial dates expand to the whole period).

### Auth Flow (`auth/`)

- **PKCE + Auth0**: Browser-based login with localhost callback server (`login.py`)
- **Token Storage** (`token_store.py`): OS keyring primary, `~/.config/tl/credentials.json` fallback (0o600)
- **Env override**: `TL_API_KEY` env var takes priority over keyring (for CI)
- **Auto-refresh**: `TLClient` refreshes expired tokens on 401

### HTTP Client (`client/http.py`)

`TLClient` wraps httpx with auth header injection and automatic token refresh on 401. All API calls go through `_request()`.

Every request includes an `X-TL-Client: cli/<version>` header. This header is used server-side in a Cloudflare WAF rule to skip managed challenges (JS/CAPTCHA) for CLI traffic on `/api/cli/*` paths. The header is not a secret — Cloudflare bypass is safe because the API enforces its own auth via Bearer tokens. If Cloudflare starts blocking CLI requests again, verify the WAF rule matches the current header value.

### Error Handling (`client/errors.py`)

Exit codes: 1 (forbidden/not-found), 2 (auth required), 3 (rate-limit/server-error), 4 (insufficient credits).

### Output (`output/formatter.py`)

TTY-aware: Rich tables in terminal, JSON when piped. Flags: `--json`, `--csv`, `--md`, `--quiet`. Usage footer (credits charged + balance) goes to stderr. Breadcrumbs suggest next commands.

### AI Agent Integration

The CLI integrates with AI coding agents via skills, commands, agents, and hooks.

**Claude Code** (`tl setup claude`):
- `.claude-plugin/plugin.json` — plugin manifest + marketplace
- `commands/*.md` — slash commands (`/tl`, `/tl-sponsorships`, `/tl-channels`, `/tl-brands`, `/tl-reports`, `/tl-balance`)
- `skills/tl/SKILL.md` — skill definition teaching Claude the CLI workflow
- `agents/tl-analyst.md` — autonomous multi-step analysis agent
- `hooks/` — PreToolUse (auth check, limit guard) and PostToolUse (low balance warning) on `tl` commands

**OpenCode** (`tl setup opencode`):
- `skills/tl/SKILL.md` — same skill file, copied to `~/.config/opencode/skills/tl/`
- OpenCode's agent discovers and invokes it automatically for data questions

### API Response Envelope

All list endpoints return: `{ results, total, limit, offset, usage: { credits_charged, credit_rate, balance_remaining }, _breadcrumbs }`.

### Key Environment Variables

- `TL_API_URL` — API base (default: `https://app.thoughtleaders.io`)
- `TL_API_KEY` — Bearer token override for CI/scripts
- `TL_AUTH0_DOMAIN`, `TL_AUTH0_CLIENT_ID`, `TL_AUTH0_AUDIENCE` — Auth0 config
- `TL_LLM_KEY` — User's own LLM key for `tl ask` (avoids surcharge)

### Credit System

Every data query costs credits (rates vary by resource). `tl describe` shows rates, `tl balance` shows remaining. The `402` status means insufficient credits. Hooks automatically warn when balance drops below 500.

### Version Bumps

The version string is defined in three files and all three must be updated together:
- `pyproject.toml` — `version = "x.y.z"`
- `.claude-plugin/plugin.json` — `"version": "x.y.z"`
- `src/tl_cli/__init__.py` — `__version__ = "x.y.z"`

### Important Constraint

`tl snapshots video` requires `--channel` flag — Firebolt queries without a channel partition are unbounded.
