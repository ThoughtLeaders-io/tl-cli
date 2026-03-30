"""Auth0 login flows: browser-based PKCE and headless device code."""

import http.server
import secrets
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

console = Console(stderr=True)


@dataclass
class _CallbackResult:
    """Captured from the OAuth callback."""

    code: str | None = None
    error: str | None = None
    state: str | None = None


def login_browser() -> StoredTokens:
    """Run the Auth0 PKCE login flow with a local browser.

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

    console.print("[bold]Opening browser for login...[/bold]")
    console.print(f"[dim]If the browser doesn't open, visit:[/dim]\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback (timeout after 120 seconds)
    deadline = time.time() + 120
    while result.code is None and result.error is None:
        if time.time() > deadline:
            server.shutdown()
            console.print("[red]Login timed out. Please try again.[/red]")
            raise SystemExit(1)
        time.sleep(0.1)

    server.shutdown()

    if result.error:
        console.print(f"[red]Login failed: {result.error}[/red]")
        raise SystemExit(1)

    # Exchange code for tokens
    console.print("[dim]Exchanging authorization code...[/dim]")
    tokens = _exchange_code(
        code=result.code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        config=config,
    )

    save_tokens(tokens)
    console.print(f"[green]Logged in as {tokens.email or 'unknown'}[/green]")
    return tokens


def login_device_code() -> StoredTokens:
    """Run the Auth0 Device Authorization Flow (RFC 8628).

    Works on headless machines — the user authenticates via any browser on any device.
    """
    config = get_config()

    # Request a device code
    response = httpx.post(
        f"https://{config.auth0_domain}/oauth/device/code",
        data={
            "client_id": config.auth0_client_id,
            "scope": "openid profile email offline_access",
            "audience": config.auth0_audience,
        },
    )

    if response.status_code != 200:
        console.print(f"[red]Failed to start device login: {response.text}[/red]")
        raise SystemExit(1)

    data = response.json()
    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data["verification_uri"]
    verification_uri_complete = data.get("verification_uri_complete", verification_uri)
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)

    console.print()
    console.print("[bold]To log in, open this URL on any device:[/bold]")
    console.print(f"  {verification_uri_complete}")
    console.print()
    console.print(f"[bold]And enter the code:[/bold]  [cyan bold]{user_code}[/cyan bold]")
    console.print()
    console.print(f"[dim]The code expires in {expires_in // 60} minutes.[/dim]")

    # Poll for token
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)

        token_response = httpx.post(
            f"https://{config.auth0_domain}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": config.auth0_client_id,
            },
        )

        token_data = token_response.json()

        if token_response.status_code == 200:
            # Extract email from ID token if present
            email = None
            id_token = token_data.get("id_token")
            if id_token:
                email = _extract_email_from_jwt(id_token)

            tokens = StoredTokens(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                expires_at=time.time() + token_data.get("expires_in", 3600),
                email=email,
            )
            save_tokens(tokens)
            console.print(f"\n[green]Logged in as {tokens.email or 'unknown'}[/green]")
            return tokens

        error = token_data.get("error")
        if error == "authorization_pending":
            continue
        elif error == "slow_down":
            interval += 5
            continue
        elif error == "expired_token":
            console.print("[red]Device code expired. Please try again.[/red]")
            raise SystemExit(1)
        elif error == "access_denied":
            console.print("[red]Login was denied.[/red]")
            raise SystemExit(1)
        else:
            console.print(f"[red]Login failed: {token_data.get('error_description', error)}[/red]")
            raise SystemExit(1)

    console.print("[red]Login timed out. Please try again.[/red]")
    raise SystemExit(1)


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
        console.print("[red]Token refresh failed. Please run: tl auth login[/red]")
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
        console.print(f"[red]Token exchange failed: {response.text}[/red]")
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
