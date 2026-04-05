"""tl setup — Install Claude Code plugin and other integrations."""

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from tl_cli import __version__

app = typer.Typer(help="Set up integrations (Claude Code plugin)")
console = Console(stderr=True)

MARKETPLACE_SOURCE = "ThoughtLeaders-io/tl-cli"
MARKETPLACE_NAME = "thoughtleaders-plugins"
PLUGIN_NAME = "tl-cli"
PLUGIN_KEY = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"

CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_PLUGINS_DIR = CLAUDE_HOME / "plugins"
CLAUDE_SKILLS_DIR = CLAUDE_HOME / "skills"
CLAUDE_COMMANDS_DIR = CLAUDE_HOME / "commands"


def _find_plugin_root() -> Path | None:
    """Locate the plugin assets directory.

    Tries two locations:
    1. _plugin/ inside the installed package (pip/pipx installs via hatch force-include)
    2. Repo root relative to this file (editable installs)
    """
    bundled = Path(__file__).resolve().parent.parent / "_plugin"
    if (bundled / ".claude-plugin" / "plugin.json").is_file():
        return bundled

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if (repo_root / ".claude-plugin" / "plugin.json").is_file():
        return repo_root

    return None


def _find_claude_binary() -> str | None:
    """Find the claude binary on PATH."""
    return shutil.which("claude")


def _run_claude(args: list[str], claude_bin: str) -> tuple[bool, str]:
    """Run a claude CLI command and return (success, output)."""
    try:
        result = subprocess.run(
            [claude_bin] + args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            output = result.stderr.strip() or output
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def _get_installed_plugin_version() -> str | None:
    """Try to read the installed plugin version from the cache."""
    version_file = CLAUDE_PLUGINS_DIR / "tl-cli" / ".version"
    if version_file.exists():
        return version_file.read_text().strip()
    return None


def check_plugin_version() -> str | None:
    """Check if installed plugin version matches CLI version.

    Returns a warning message if mismatched, None if OK or not installed.
    """
    installed = _get_installed_plugin_version()
    if installed is None:
        return None
    if installed != __version__:
        return f"Claude Code plugin is outdated (v{installed} vs CLI v{__version__}). Run 'tl setup claude' to update."
    return None


def _install_standalone_skills(plugin_root: Path) -> int:
    """Copy skills and commands to ~/.claude/ for non-namespaced invocation.

    Returns the number of items installed.
    """
    count = 0

    # Skills: skills/<name>/SKILL.md → ~/.claude/skills/<name>/SKILL.md
    skills_src = plugin_root / "skills"
    if skills_src.is_dir():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                dst = CLAUDE_SKILLS_DIR / skill_dir.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(skill_dir, dst)
                count += 1

    # Commands: commands/<name>.md → ~/.claude/commands/<name>.md
    commands_src = plugin_root / "commands"
    if commands_src.is_dir():
        CLAUDE_COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
        for cmd_file in commands_src.glob("*.md"):
            dst = CLAUDE_COMMANDS_DIR / cmd_file.name
            shutil.copy2(cmd_file, dst)
            count += 1

    return count


def _print_manual_instructions() -> None:
    """Print manual install instructions when claude binary is not found."""
    console.print()
    console.print("[yellow]Claude Code binary not found on PATH.[/yellow]")
    console.print()
    console.print("Install Claude Code first, then run these commands inside Claude Code:")
    console.print()
    console.print(f"  [cyan]/plugin marketplace add {MARKETPLACE_SOURCE}[/cyan]")
    console.print(f"  [cyan]/plugin install {PLUGIN_KEY}[/cyan]")
    console.print()
    console.print("Or start Claude Code with the plugin loaded directly:")
    console.print()
    console.print(f"  [cyan]claude --plugin-dir /path/to/tl-cli[/cyan]")


@app.command("claude")
def setup_claude(
    json_output: bool = typer.Option(False, "--json", help="JSON output (non-interactive)"),
) -> None:
    """Install the TL CLI plugin for Claude Code.

    Registers the ThoughtLeaders marketplace, installs the tl-cli plugin,
    and copies skills/commands to ~/.claude/ for short /tl invocation.
    If the claude binary is not on PATH, prints manual instructions.

    Examples:
        tl setup claude
        tl setup claude --json
    """
    if json_output:
        _setup_noninteractive()
        return

    console.print()
    console.print(f"[bold]tl-cli[/bold] v{__version__} — Claude Code Plugin Setup")
    console.print()

    # Check tl is on PATH
    tl_bin = shutil.which("tl")
    if tl_bin:
        console.print(f"  [green]✓[/green] tl CLI found: {tl_bin}")
    else:
        console.print("  [red]✗[/red] tl CLI not found on PATH")
        console.print("    Claude Code's Bash tool won't be able to run tl commands.")
        console.print("    Install with: [cyan]pipx install tl-cli[/cyan]")

    # Find plugin assets
    plugin_root = _find_plugin_root()
    if plugin_root is None:
        console.print("  [red]✗[/red] Plugin assets not found")
        console.print("    Try reinstalling: [cyan]pipx install tl-cli[/cyan]")
        raise SystemExit(1)
    console.print(f"  [green]✓[/green] Plugin assets found: {plugin_root}")

    # Check claude binary
    claude_bin = _find_claude_binary()
    if not claude_bin:
        # Still install standalone skills even without claude binary
        console.print("  [yellow]![/yellow] claude binary not found on PATH")
        _install_standalone_skills_step(plugin_root)
        console.print()
        _print_manual_instructions()
        raise SystemExit(1)

    console.print(f"  [green]✓[/green] claude binary found: {claude_bin}")
    console.print()

    # Step 1: Register marketplace
    console.print("[bold]Registering marketplace...[/bold]")
    ok, output = _run_claude(["plugin", "marketplace", "add", MARKETPLACE_SOURCE], claude_bin)
    if ok:
        console.print(f"  [green]✓[/green] Marketplace registered: {MARKETPLACE_NAME}")
    else:
        if "already" in output.lower() or "exists" in output.lower():
            console.print(f"  [green]✓[/green] Marketplace already registered: {MARKETPLACE_NAME}")
            console.print("  Updating marketplace...")
            _run_claude(["plugin", "marketplace", "update", MARKETPLACE_NAME], claude_bin)
        else:
            console.print(f"  [red]✗[/red] Marketplace registration failed: {output}")
            _print_manual_instructions()
            raise SystemExit(1)

    # Step 2: Install plugin
    console.print("[bold]Installing plugin...[/bold]")
    ok, output = _run_claude(["plugin", "install", PLUGIN_KEY], claude_bin)
    if ok:
        console.print(f"  [green]✓[/green] Plugin installed: {PLUGIN_KEY}")
    else:
        if "already" in output.lower():
            console.print(f"  [green]✓[/green] Plugin already installed: {PLUGIN_KEY}")
        else:
            console.print(f"  [red]✗[/red] Plugin installation failed: {output}")
            console.print("    Try running inside Claude Code:")
            console.print(f"    [cyan]/plugin install {PLUGIN_KEY}[/cyan]")
            raise SystemExit(1)

    # Step 3: Install standalone skills for short /tl invocation
    _install_standalone_skills_step(plugin_root)

    # Write version stamp
    version_dir = CLAUDE_PLUGINS_DIR / "tl-cli"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / ".version").write_text(__version__)

    console.print()
    console.print("[green]Setup complete![/green]")
    console.print()
    console.print("Available skills in Claude Code:")
    console.print("  [cyan]/tl[/cyan]                  — data analyst (smart query router)")
    console.print("  [cyan]/tl-sponsorships[/cyan]     — sponsorship lookup")
    console.print("  [cyan]/tl-brands[/cyan]           — brand intelligence")
    console.print("  [cyan]/tl-channels[/cyan]         — channel search")
    console.print("  [cyan]/tl-reports[/cyan]          — saved reports")
    console.print("  [cyan]/tl-balance[/cyan]          — credit balance")
    console.print()
    console.print("Try it:")
    console.print("  [cyan]/tl sold sponsorships for Nike in Q1[/cyan]")
    console.print()
    console.print("[dim]To update, run: tl setup claude[/dim]")


def _install_standalone_skills_step(plugin_root: Path) -> None:
    """Install standalone skills and print status."""
    console.print("[bold]Installing skills for /tl shortcut...[/bold]")
    count = _install_standalone_skills(plugin_root)
    if count > 0:
        console.print(f"  [green]✓[/green] Installed {count} skills/commands to ~/.claude/")
    else:
        console.print("  [yellow]![/yellow] No skills found to install")


def _setup_noninteractive() -> None:
    """Non-interactive setup for --json/agent usage."""
    result = {
        "cli_version": __version__,
        "marketplace_source": MARKETPLACE_SOURCE,
        "marketplace_name": MARKETPLACE_NAME,
        "plugin_key": PLUGIN_KEY,
    }

    plugin_root = _find_plugin_root()
    if plugin_root is None:
        result["status"] = "error"
        result["error"] = "Plugin assets not found"
        print(json.dumps(result, indent=2))
        raise SystemExit(1)

    claude_bin = _find_claude_binary()

    # Register marketplace + install plugin (if claude binary available)
    if claude_bin:
        ok, output = _run_claude(["plugin", "marketplace", "add", MARKETPLACE_SOURCE], claude_bin)
        if not ok and "already" not in output.lower() and "exists" not in output.lower():
            result["marketplace_registered"] = False
        else:
            result["marketplace_registered"] = True
            _run_claude(["plugin", "marketplace", "update", MARKETPLACE_NAME], claude_bin)

        ok, output = _run_claude(["plugin", "install", PLUGIN_KEY], claude_bin)
        result["plugin_installed"] = ok or "already" in output.lower()
    else:
        result["marketplace_registered"] = False
        result["plugin_installed"] = False

    # Always install standalone skills
    count = _install_standalone_skills(plugin_root)
    result["standalone_skills_installed"] = count

    # Write version stamp
    version_dir = CLAUDE_PLUGINS_DIR / "tl-cli"
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / ".version").write_text(__version__)

    result["status"] = "ok"
    print(json.dumps(result, indent=2))
