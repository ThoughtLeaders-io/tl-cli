"""tl comments — List and add comments on deals."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Comments on deals (free, no credits)")


@app.callback(invoke_without_command=True)
def comments(
    ctx: typer.Context,
    adlink_id: int = typer.Argument(..., help="Deal (adlink) ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON data only"),
) -> None:
    """List comments on a deal (free, no credits).

    Examples:
        tl comments 12345
    """
    if ctx.invoked_subcommand is not None:
        return

    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        data = client.get(f"/comments/{adlink_id}")
        output(
            data,
            fmt,
            columns=["id", "author", "text", "created_at"],
            title=f"Comments on Deal #{adlink_id}",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("add")
def add_comment(
    adlink_id: int = typer.Argument(..., help="Deal (adlink) ID"),
    message: str = typer.Argument(..., help="Comment text"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Raw JSON only"),
) -> None:
    """Add a comment to a deal (free, no credits).

    Examples:
        tl comments add 12345 "Looks good, ready to send"
    """
    fmt = detect_format(json_output, False, False, quiet)

    client = get_client()
    try:
        data = client.post(f"/comments/{adlink_id}", json_body={"text": message})
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
