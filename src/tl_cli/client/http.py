"""Authenticated HTTP client for the TL CLI API."""

import httpx

from tl_cli import __version__
from tl_cli.auth.login import refresh_access_token
from tl_cli.auth.token_store import load_tokens
from tl_cli import config as tl_config
from tl_cli.client.errors import ApiError
from tl_cli.config import get_config


class TLClient:
    """HTTP client that handles auth injection, token refresh, and error mapping."""

    def __init__(self) -> None:
        self._config = get_config()
        self._client = httpx.Client(
            base_url=self._config.cli_api_base,
            timeout=30.0,
            headers={
                "User-Agent": f"tl-cli/{__version__}",
                "X-TL-Client": f"cli/{__version__}",
            },
        )

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_body: dict | None = None) -> dict:
        return self._request("POST", path, json_body=json_body)

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        headers = self._auth_headers()

        if tl_config.full_access:
            params = dict(params) if params else {}
            params['full_access'] = '1'

        response = self._client.request(
            method, path, params=params, json=json_body, headers=headers
        )

        # On 401, try refreshing the token once
        if response.status_code == 401:
            headers = self._refresh_and_get_headers()
            if headers:
                response = self._client.request(
                    method, path, params=params, json=json_body, headers=headers
                )

        if response.status_code >= 400:
            detail = self._extract_detail(response)
            try:
                raw = response.json() if response.text else None
            except Exception:
                raw = None
            raise ApiError(
                response.status_code, detail, raw=raw,
                url=str(response.url), response_text=response.text,
            )

        return response.json()

    def _auth_headers(self) -> dict[str, str]:
        """Get authorization headers from API key or stored tokens."""
        # API key takes priority (for CI/scripts)
        if self._config.api_key:
            return {"Authorization": f"Bearer {self._config.api_key}"}

        tokens = load_tokens()
        if not tokens:
            raise ApiError(401, "Not authenticated. Run: tl auth login")

        if tokens.is_expired and tokens.refresh_token:
            tokens = refresh_access_token(tokens.refresh_token)

        return {"Authorization": f"Bearer {tokens.access_token}"}

    def _refresh_and_get_headers(self) -> dict[str, str] | None:
        """Try to refresh the token. Returns new headers or None."""
        tokens = load_tokens()
        if not tokens or not tokens.refresh_token:
            return None
        try:
            new_tokens = refresh_access_token(tokens.refresh_token)
            return {"Authorization": f"Bearer {new_tokens.access_token}"}
        except SystemExit:
            return None

    def _extract_detail(self, response: httpx.Response) -> str:
        """Extract error detail from response body."""
        try:
            data = response.json()
            return data.get("detail", data.get("error", str(data)))
        except Exception:
            text = response.text or ""
            if text.lstrip().startswith("<!") or text.lstrip().startswith("<html"):
                return f"HTTP {response.status_code} (non-JSON response from server)"
            return text or f"HTTP {response.status_code}"

    def close(self) -> None:
        self._client.close()


def get_client() -> TLClient:
    """Get a configured TL API client."""
    return TLClient()
