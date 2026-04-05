"""tl setup — Install Claude Code plugin and other integrations."""

import shutil
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Set up integrations (Claude Code plugin)")
console = Console()

# The plugin source is relative to this package's installed location
PLUGIN_SOURCE = Path(__file__).resolve().parent.parent.parent.parent
CLAUDE_PLUGINS_DIR = Path.home() / ".claude" / "plugins" / "tl-cli"


@app.command("claude")
def setup_claude() -> None:
    """Install the TL CLI plugin for Claude Code.

    This copies the plugin manifest, skill file, agent, hooks, and slash commands
    into ~/.claude/plugins/tl-cli/ so Claude Code can discover them.

    Examples:
        tl setup claude
    """
    # Directories to copy from the package
    components = [".claude-plugin", "commands", "skills", "agents", "hooks"]

    # Find the package root (where plugin.json lives)
    package_root = PLUGIN_SOURCE
    plugin_json = package_root / ".claude-plugin" / "plugin.json"

    if not plugin_json.exists():
        console.print("[red]Plugin files not found.[/red]")
        console.print("This usually means the CLI was installed without plugin assets.")
        console.print("Try reinstalling: pipx install tl-cli")
        raise SystemExit(1)

    # Create plugin directory
    CLAUDE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

    for component in components:
        src = package_root / component
        dst = CLAUDE_PLUGINS_DIR / component
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    console.print("[green]Claude Code plugin installed![/green]")
    console.print(f"  Location: {CLAUDE_PLUGINS_DIR}")
    console.print()
    console.print("Restart Claude Code to activate. Then try:")
    console.print("  [cyan]/tl sold sponsorships for Nike in Q1[/cyan]")
