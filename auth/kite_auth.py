"""Daily login flow for Kite Connect.

SEBI's framework requires a fresh OAuth + 2FA login every trading day; there
is no persistent refresh token. This module gets the login URL, exchanges
the request_token the user pastes back for an access_token, and persists
that token into the .env file so the rest of the bot can pick it up for the
day via config.settings.

Run standalone to test the flow before wiring it into the scheduler:
    python -m auth.kite_auth
"""
from __future__ import annotations

import re
from pathlib import Path

from kiteconnect import KiteConnect

from config.settings import settings

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class KiteAuth:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        self.api_key = api_key or settings.kite_api_key
        self.api_secret = api_secret or settings.kite_api_secret
        if not self.api_key or not self.api_secret:
            raise ValueError("KITE_API_KEY and KITE_API_SECRET must be set")
        self.kite = KiteConnect(api_key=self.api_key)

    def login_url(self) -> str:
        return self.kite.login_url()

    def generate_access_token(self, request_token: str) -> str:
        data = self.kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        self._persist_access_token(access_token)
        return access_token

    def _persist_access_token(self, access_token: str) -> None:
        """Writes/updates KITE_ACCESS_TOKEN in .env so settings pick it up on next load."""
        line = f"KITE_ACCESS_TOKEN={access_token}\n"
        if ENV_PATH.exists():
            content = ENV_PATH.read_text()
            if re.search(r"^KITE_ACCESS_TOKEN=.*$", content, flags=re.MULTILINE):
                content = re.sub(r"^KITE_ACCESS_TOKEN=.*$", line.strip(), content, flags=re.MULTILINE)
            else:
                content = content.rstrip("\n") + "\n" + line
            ENV_PATH.write_text(content)
        else:
            ENV_PATH.write_text(line)

    def authenticated_client(self, access_token: str | None = None) -> KiteConnect:
        token = access_token or settings.kite_access_token
        if not token:
            raise ValueError("No access token available; run the daily login flow first")
        self.kite.set_access_token(token)
        return self.kite


def run_interactive_login() -> str:
    auth = KiteAuth()
    print("Visit this URL to login:")
    print(auth.login_url())
    request_token = input("Paste the request_token from the redirect URL: ").strip()
    access_token = auth.generate_access_token(request_token)
    print("Access token generated and saved to .env for today's session.")
    return access_token


if __name__ == "__main__":
    run_interactive_login()
