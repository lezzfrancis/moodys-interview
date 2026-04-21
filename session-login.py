import os
import re
import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


DEFAULT_LOGIN_URL = os.getenv("MOODYS_LOGIN_URL", "https://login.moodys.com")
DEFAULT_SESSION_FILE = os.getenv("MOODYS_SESSION_FILE", "moodys_auth_state.json")
DEFAULT_HEADLESS = os.getenv("MOODYS_HEADLESS", "true").lower() == "true"
DEFAULT_USERNAME = os.getenv("MOODYS_USERNAME", "")  # Only for testing. Save your credentials in a .env file or set them as environment variables.
DEFAULT_PASSWORD = os.getenv("MOODYS_PASSWORD", "")  # Only for testing. Save your credentials in a .env file or set them as environment variables.


def manual_login_and_save_session(
    login_url: str = DEFAULT_LOGIN_URL,
    session_file: str = DEFAULT_SESSION_FILE,
    headless: bool = False,
) -> None:
    """
    Opens a visible browser so the user can log in manually.
    After successful login, stores the browser auth state to a file.
    Best for SSO / MFA / bot-protected sites.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 2000},
        )
        page = context.new_page()

        print(f"Opening login page: {login_url}")
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)

        print("\nLog in manually in the opened browser.")
        print("Complete username/password, SSO, MFA, captcha, etc.")
        input("\nAfter login is complete and you can access Moody's pages, press Enter here to save the session... ")

        context.storage_state(path=session_file)
        print(f"Saved authenticated session to: {session_file}")

        context.close()
        browser.close()


print(f'default password: {DEFAULT_PASSWORD} , {DEFAULT_USERNAME}')

manual_login_and_save_session(
                login_url=DEFAULT_LOGIN_URL,
                session_file=DEFAULT_SESSION_FILE,
                headless=False,
            )

  

