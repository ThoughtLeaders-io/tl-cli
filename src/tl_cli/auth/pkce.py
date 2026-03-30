"""PKCE (Proof Key for Code Exchange) utilities for OAuth 2.1."""

import base64
import hashlib
import secrets


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns:
        (code_verifier, code_challenge) tuple.
    """
    # code_verifier: 43-128 characters, URL-safe
    code_verifier = secrets.token_urlsafe(64)

    # code_challenge: SHA256 hash of verifier, base64url-encoded (no padding)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    return code_verifier, code_challenge
