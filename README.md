# tl cli

ThoughtLeaders CLI — query sponsorship data, channels, brands, and intelligence from the terminal.

## Install

### As a developer

```bash
git clone https://github.com/ThoughtLeaders-io/tl-cli.git
cd tl-cli
python -m venv .venv
pip install -e .
```

### As a user

```bash
pipx install git+https://github.com/ThoughtLeaders-io/tl-cli.git
# or
uv tool install git+https://github.com/ThoughtLeaders-io/tl-cli.git
# or (but try to avoid it because just "pip" will not create a new venv for the product - only "uv" and "pipx" will do that)
pip install tl-cli
```

## Quick Start

```bash
# Login
tl auth login

# Query sponsorships
tl sponsorships list status:sold brand:"Nike" since:2026-01

# Shortcut commands for sponsorship types
tl deals list brand:"Nike"                    # Agreed-upon sponsorships
tl deals list since:today                     # Deals from today (date keywords: today, yesterday, tomorrow)
tl matches list                               # Possible brand-channel pairings
tl proposals list                             # Matches proposed to both sides

# Show a specific sponsorship
tl sponsorships show 12345

# Search videos (note: this only shows "your" videos)
tl uploads list q:code --csv

# Show upload details (supports colon-containing IDs)
tl uploads show 1174310:0BehkmVa7ak

# Search channels
tl channels list category:cooking min-subs:100k

# Show channel detail
tl channels show 12345

# Brand intelligence
tl brands show Nike

# Run a saved report
tl reports run 42

# Comments on a sponsorship
tl comments list 12345
tl comments add 12345 "Looks good"

# Show information about the logged-in user
tl whoami

# Check credits
tl balance
```

## Credits

Every data query costs credits based on the type and number of results. Use `tl describe` to see credit rates and `tl balance` to check your balance.

```bash
tl describe                           # All resources + credit costs
tl describe show sponsorships --filters    # Available filters for sponsorships
tl balance                     # Your credit balance
```

# Terminology

ThoughtLeaders has its internal terminology that's exposed throughout this tool.

* **Brands** - Usually companies, sometimes individual products. Brands are the sponsors.
* **Channels** - Usually YouTube channels, sometimes podcasts. Channels are creators, they are being sponsored.
* **Sponsorships** - Either possible or realised business deals between brands and channels. There are several specific types of sponsorships:
    * *Deals* - Contractually agreed-upon sponsorships. AKA sold sponsorships. They can be either in a production pipeline or already published / live.
    * *Matches* - Possible matches between brands and channels, i.e. all pairings that ThoughtLeaders thinks could possibly be right for each other.
    * *Proposals* - Matches that are actually proposed to both sides to consider.
* **Send date** - The expected publication date for a sponsored video

Sponsorships are the centre of attention in ThoughtLeaders - all other analytics and operations serve to produce or optimise sponsorships.
Note that the term "Sponsorship" is wide, and can encompass deals that yet need to be approved by either side. There is a funnel of
sponsorship types: the pool of Sponsorships is large, the pool of Metches (considered from either Brand or Channel side) is smaller,
the pool of Proposals is yet smaller, and the pool of Deals is the smallest.

# Integrations

## Claude Code Integration

If you use Claude Code, install the plugin for natural language access:

```bash
tl setup claude
```

This registers the ThoughtLeaders marketplace, installs the plugin, and copies skills to `~/.claude/` for short `/tl` invocation. If the `claude` binary isn't on PATH, it still installs the standalone skills and prints manual instructions for the plugin.

### Using the skills

Talk naturally in Claude Code:

```
/tl Which channels did we sponsor in Q1?
/tl sold sponsorships for Nike in Q1
/tl show me pending proposals with send dates in April
/tl what channels does Nike sponsor?
/tl check my balance
```

Resource-specific slash commands:
```
/tl-sponsorships pending with send dates in April
/tl-channels cooking channels over 100k subscribers
/tl-brands Nike
/tl-reports run my Q1 pipeline
/tl-balance
```

### Updating

```bash
tl setup claude                    # re-installs skills and updates plugin
```

## Output Formats

By default, output is a styled table in the terminal and JSON when piped.

```bash
tl sponsorships list status:sold                          # Pretty table
tl sponsorships list status:sold --json                   # JSON
tl sponsorships list status:sold --csv > sponsorships.csv # CSV
tl sponsorships list status:sold --json | jq '.results'   # Pipe to jq
```

## Documentation

- [Architecture & Design](docs/architecture.md) — full design doc covering commands, data scoping, credit metering, and server-side API
- `tl describe` — discover available resources, fields, filters, and credit costs from the CLI itself
- `tl <command> --help` — detailed help for any command
