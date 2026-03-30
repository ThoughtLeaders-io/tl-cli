# tl-cli

ThoughtLeaders CLI — query sponsorship data, channels, brands, and intelligence from the terminal.

## Install

```bash
pipx install tl-cli
# or
pip install tl-cli
# or
uv tool install tl-cli
```

## Quick Start

```bash
# Login
tl auth login

# Query deals
tl deals status:sold brand:"Nike" since:2026-01

# Search channels
tl channels category:cooking min-subs:100k

# Brand intelligence
tl brands Nike

# Run a saved report
tl reports run 42

# Check credits
tl balance
```

## Claude Code Integration

If you use Claude Code, install the plugin for natural language access:

```bash
tl setup claude
```

Then just talk naturally:
```
/tl sold deals for Nike in Q1
/tl-channels cooking channels over 100k subscribers
/tl-brands Nike
```

## Output Formats

By default, output is a styled table in the terminal and JSON when piped.

```bash
tl deals status:sold                          # Pretty table
tl deals status:sold --json                   # JSON
tl deals status:sold --csv > deals.csv        # CSV
tl deals status:sold --json | jq '.results'   # Pipe to jq
```

## Credits

Every data query costs credits based on the type and number of results. Use `tl describe` to see credit rates and `tl balance` to check your balance.

```bash
tl describe                    # All resources + credit costs
tl describe deals --filters    # Available filters for deals
tl balance                     # Your credit balance
```

## Documentation

Full docs: https://docs.thoughtleaders.io/cli
