"""tl comments — List and add comments on sponsorships."""

import typer

from tl_cli.client.errors import ApiError, handle_api_error
from tl_cli.client.http import get_client
from tl_cli.output.formatter import detect_format, output, output_single

app = typer.Typer(help="Comments on sponsorships (free, no credits)")


@app.command("list")
def list_cmd(
    adlink_id: int = typer.Argument(..., help="Sponsorship (adlink) ID"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List comments on a sponsorship (free, no credits).

    Examples:
        tl comments list 12345
    """
    fmt = detect_format(json_output, False, False)

    client = get_client()
    try:
        data = client.get(f"/comments/{adlink_id}")
        for r in data.get("results", []):
            r["comment_id"] = r.pop("id", None)
        output(
            data,
            fmt,
            columns=["comment_id", "author", "text", "created_at"],
            title=f"Comments on Sponsorship #{adlink_id}",
        )
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()


@app.command("add")
def add_comment(
    adlink_id: int = typer.Argument(..., help="Sponsorship (adlink) ID"),
    message: str = typer.Argument(..., help="Comment text"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Add a comment to a sponsorship (free, no credits).

    Examples:
        tl comments add 12345 "Looks good, ready to send"
    """
    fmt = detect_format(json_output, False, False)

    client = get_client()
    try:
        data = client.post(f"/comments/{adlink_id}", json_body={"text": message})
        output_single(data, fmt)
    except ApiError as e:
        handle_api_error(e)
    finally:
        client.close()
