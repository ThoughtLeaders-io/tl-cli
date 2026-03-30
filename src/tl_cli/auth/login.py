"""Auth0 PKCE login flow with localhost callback server."""

import http.server
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import httpx
from rich.console import Console

from tl_cli.auth.pkce import generate_pkce_pair
from tl_cli.auth.token_store import StoredTokens, save_tokens
from tl_cli.config import get_config

err = Console(stderr=True)


@dataclass
class _CallbackResult:
    """Captured from the OAuth callback."""

    code: str | None = None
    error: str | None = None
    state: str | None = None


def login() -> StoredTokens:
    """Run the full Auth0 PKCE login flow.

    1. Generate PKCE pair + state
    2. Start localhost callback server
    3. Open browser to Auth0 /authorize
    4. Wait for callback with authorization code
    5. Exchange code for tokens
    6. Store tokens
    """
    config = get_config()
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    result = _CallbackResult()

    # Start callback server on a random port
    server, port = _start_callback_server(result, state)

    redirect_uri = f"http://localhost:{port}/callback"

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id": config.auth0_client_id,
        "redirect_uri": redirect_uri,
        "audience": config.auth0_audience,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"https://{config.auth0_domain}/authorize?{urllib.parse.urlencode(params)}"

    err.print("[bold]Opening browser for login...[/bold]")
    err.print(f"[dim]If the browser doesn't open, visit:[/dim]\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback (timeout after 120 seconds)
    deadline = time.time() + 120
    while result.code is None and result.error is None:
        if time.time() > deadline:
            server.shutdown()
            err.print("[red]Login timed out. Please try again.[/red]")
            raise SystemExit(1)
        time.sleep(0.1)

    server.shutdown()

    if result.error:
        err.print(f"[red]Login failed: {result.error}[/red]")
        raise SystemExit(1)

    # Exchange code for tokens
    err.print("[dim]Exchanging authorization code...[/dim]")
    tokens = _exchange_code(
        code=result.code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        config=config,
    )

    save_tokens(tokens)
    err.print(f"[green]Logged in as {tokens.email or 'unknown'}[/green]")
    return tokens


def refresh_access_token(refresh_token: str) -> StoredTokens:
    """Use a refresh token to get a new access token."""
    config = get_config()

    response = httpx.post(
        f"https://{config.auth0_domain}/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": config.auth0_client_id,
            "refresh_token": refresh_token,
        },
    )

    if response.status_code != 200:
        err.print("[red]Token refresh failed. Please run: tl auth login[/red]")
        raise SystemExit(2)

    data = response.json()
    tokens = StoredTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", refresh_token),
        expires_at=time.time() + data.get("expires_in", 3600),
        email=None,  # Not returned on refresh
    )
    save_tokens(tokens)
    return tokens


def _exchange_code(
    code: str,
    code_verifier: str,
    redirect_uri: str,
    config,
) -> StoredTokens:
    """Exchange authorization code for tokens."""
    response = httpx.post(
        f"https://{config.auth0_domain}/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": config.auth0_client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        },
    )

    if response.status_code != 200:
        err.print(f"[red]Token exchange failed: {response.text}[/red]")
        raise SystemExit(1)

    data = response.json()

    # Decode email from ID token if present
    email = None
    id_token = data.get("id_token")
    if id_token:
        email = _extract_email_from_jwt(id_token)

    return StoredTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + data.get("expires_in", 3600),
        email=email,
    )


def _extract_email_from_jwt(token: str) -> str | None:
    """Extract email from JWT payload without full verification (already trusted from Auth0)."""
    import base64
    import json

    try:
        payload_part = token.split(".")[1]
        # Add padding
        padding = 4 - len(payload_part) % 4
        payload_part += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_part))
        return payload.get("email")
    except Exception:
        return None


def _start_callback_server(
    result: _CallbackResult, expected_state: str
) -> tuple[http.server.HTTPServer, int]:
    """Start a temporary HTTP server to receive the OAuth callback."""

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            # Check state
            received_state = params.get("state", [None])[0]
            if received_state != expected_state:
                result.error = "State mismatch — possible CSRF attack"
                self._respond("Login failed: state mismatch.")
                return

            if "error" in params:
                result.error = params["error"][0]
                desc = params.get("error_description", [""])[0]
                self._respond(f"Login failed: {desc or result.error}")
                return

            code = params.get("code", [None])[0]
            if not code:
                result.error = "No authorization code received"
                self._respond("Login failed: no code received.")
                return

            result.code = code
            self._respond(
                "Login successful! You can close this tab and return to the terminal."
            )

        def _respond(self, message: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = f"""<!DOCTYPE html>
<html><head><title>TL CLI Login</title></head>
<body style="font-family: system-ui; text-align: center; padding: 60px;">
<h2>{message}</h2>
</body></html>"""
            self.wfile.write(html.encode())

        def log_message(self, format, *args):
            pass  # Suppress HTTP logs

    # Find a free port
    server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server, port
