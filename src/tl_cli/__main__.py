"""Module entry point for the CLI.

Enables `python -m tl_cli` and serves as the single entry script for the
PyInstaller-frozen standalone build (see packaging/tl.spec). Invoking the CLI
through the signed `python.exe` / signed frozen exe is what keeps it runnable
under Windows Smart App Control, which blocks the unsigned launcher stub that
pipx/uv generate per install.
"""

from tl_cli.main import cli

if __name__ == "__main__":
    cli()
