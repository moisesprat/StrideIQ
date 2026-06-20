"""
Session manager: logs into the TrainingPeaks web app with username/password
via a headless browser, captures the bearer token from network traffic, then
closes the browser so all subsequent calls are plain HTTP.

Re-authentication happens transparently when the token approaches expiry.
"""

import asyncio
import time
from typing import Optional

from playwright.async_api import async_playwright

TP_LOGIN_URL = "https://home.trainingpeaks.com/login"
_TOKEN_BUFFER_SECS = 120  # re-login this many seconds before expiry


class SessionManager:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - _TOKEN_BUFFER_SECS:
            return self._access_token
        async with self._lock:
            # Re-check inside the lock to avoid double login
            if self._access_token and time.time() < self._expires_at - _TOKEN_BUFFER_SECS:
                return self._access_token
            await self._browser_login()
        return self._access_token

    async def _browser_login(self) -> None:
        captured_token: Optional[str] = None
        captured_expiry: float = time.time() + 3600

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context()
            page = await context.new_page()

            # Strategy 1: intercept any JSON response that carries an access_token
            async def on_response(response):
                nonlocal captured_token, captured_expiry
                if captured_token:
                    return
                ct = response.headers.get("content-type", "")
                if response.status == 200 and "json" in ct:
                    try:
                        body = await response.json()
                        if isinstance(body, dict) and "access_token" in body:
                            captured_token = body["access_token"]
                            if "expires_in" in body:
                                captured_expiry = time.time() + int(body["expires_in"])
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(TP_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

            # Fill login form (try several common selector patterns)
            await (
                page.locator(
                    "input#Username, input[name='username'], input[type='email'][name*='user']"
                )
                .first.fill(self.username)
            )
            await (
                page.locator(
                    "input#Password, input[name='password'], input[type='password']"
                )
                .first.fill(self.password)
            )
            await (
                page.locator(
                    "button[type='submit'], input[type='submit'], button:has-text('Log In')"
                )
                .first.click()
            )

            # Wait for post-login navigation
            try:
                await page.wait_for_url("**/calendar**", timeout=30_000)
            except Exception:
                await page.wait_for_load_state("networkidle", timeout=20_000)

            # Strategy 2: read token from localStorage if network intercept missed it
            if not captured_token:
                js_probes = [
                    "localStorage.getItem('access_token')",
                    "localStorage.getItem('tp_access_token')",
                    # Generic: find any key whose name contains 'access_token'
                    (
                        "Object.entries(localStorage)"
                        ".find(([k]) => k.toLowerCase().includes('access_token'))"
                        "?.[1]"
                    ),
                ]
                for probe in js_probes:
                    try:
                        val = await page.evaluate(f"() => {probe}")
                        if val and isinstance(val, str):
                            captured_token = val
                            break
                    except Exception:
                        pass

            # Strategy 3: look for a cookie named like an auth token
            if not captured_token:
                for cookie in await context.cookies():
                    name = cookie.get("name", "").lower()
                    if "access" in name or "token" in name or "auth" in name:
                        captured_token = cookie.get("value")
                        if cookie.get("expires"):
                            captured_expiry = float(cookie["expires"])
                        break

            await browser.close()

        if not captured_token:
            raise RuntimeError(
                "Logged into TrainingPeaks successfully but could not capture the "
                "session token from network traffic, localStorage, or cookies. "
                "Verify your credentials and ensure no 2FA is required."
            )

        self._access_token = captured_token
        self._expires_at = captured_expiry
