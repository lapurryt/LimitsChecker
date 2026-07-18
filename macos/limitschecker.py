#!/usr/bin/env python3
"""LimitsChecker for macOS — a menu-bar app for Claude Code usage limits.

The macOS counterpart of the Linux GNOME indicator. It shares the same data
layer (Anthropic OAuth usage API) but renders into the macOS menu bar via
`rumps` instead of AppIndicator.

    GET https://api.anthropic.com/api/oauth/usage
    Authorization: Bearer <accessToken>
    anthropic-beta: oauth-2025-04-20

Token source on macOS: Claude Code usually stores credentials in the login
Keychain, not in a file. This app tries ~/.claude/.credentials.json first, then
falls back to `security find-generic-password` (service configurable via
LIMITSCHECKER_KEYCHAIN_SERVICE).

Run:
    pip3 install rumps
    python3 limitschecker.py
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import rumps
except ImportError:
    sys.stderr.write("LimitsChecker needs rumps: pip3 install rumps\n")
    sys.exit(1)

try:
    from PyObjCTools import AppHelper  # ships with rumps' pyobjc dependency
except ImportError:
    AppHelper = None


def _int_env(name: str, default: int, lo: "int | None" = None, hi: "int | None" = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


APP_TITLE = os.environ.get("LIMITSCHECKER_TITLE", "LimitsChecker")
CREDENTIALS = Path(
    os.environ.get("LIMITSCHECKER_CREDENTIALS", str(Path.home() / ".claude/.credentials.json"))
)
KEYCHAIN_SERVICE = os.environ.get("LIMITSCHECKER_KEYCHAIN_SERVICE", "Claude Code-credentials")
ENDPOINT = os.environ.get("LIMITSCHECKER_ENDPOINT", "https://api.anthropic.com/api/oauth/usage")
BETA_HEADER = os.environ.get("LIMITSCHECKER_BETA", "oauth-2025-04-20")
REFRESH_SECONDS = _int_env("LIMITSCHECKER_REFRESH_SECONDS", 60, lo=5)
# After a failed poll, retry this soon instead of waiting the full refresh cycle.
RETRY_SECONDS = _int_env("LIMITSCHECKER_RETRY_SECONDS", 30, lo=5)
# Attempts per poll to ride out a transient blip (rate-limit / network hiccup).
FETCH_ATTEMPTS = _int_env("LIMITSCHECKER_FETCH_ATTEMPTS", 3, lo=1, hi=10)
TIMEOUT = _int_env("LIMITSCHECKER_TIMEOUT", 30, lo=1)
WARN_PERCENT = _int_env("LIMITSCHECKER_WARN_PERCENT", 80, lo=0, hi=100)
BAR_WIDTH = _int_env("LIMITSCHECKER_BAR_WIDTH", 10, lo=0, hi=100)
FILL_CHAR = os.environ.get("LIMITSCHECKER_FILL_CHAR", "█")
TRACK_CHAR = os.environ.get("LIMITSCHECKER_TRACK_CHAR", "▒")
NAME_SESSION = os.environ.get("LIMITSCHECKER_NAME_SESSION", "Session")
NAME_WEEK = os.environ.get("LIMITSCHECKER_NAME_WEEK", "Week")
ROW_SLOTS = 12


class UsageError(Exception):
    """Raised when usage data cannot be obtained."""


class UsageTransient(UsageError):
    """A transient failure (rate-limit, 5xx, network) worth retrying soon.

    The usage endpoint itself is rate-limited: a burst of polls returns 429.
    Treat those — plus 5xx and network blips — as transient so one bad poll
    never blanks the menu; retry_after carries the endpoint's hint when given.
    """

    def __init__(self, message: str, retry_after: "float | None" = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _token_from_json(blob: str, where: str) -> str:
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        raise UsageError(f"{where}: not valid JSON")
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise UsageError(f"{where}: no claudeAiOauth section")
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        raise UsageError(f"{where}: no accessToken")
    return token


def _read_token() -> str:
    # 1) credentials file (Linux-style; present on some macOS setups too)
    if CREDENTIALS.is_file():
        try:
            return _token_from_json(CREDENTIALS.read_text(), str(CREDENTIALS))
        except OSError as exc:
            raise UsageError(f"cannot read {CREDENTIALS}: {exc}")

    # 2) macOS login Keychain (Claude Code's default on macOS)
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError(f"keychain lookup failed: {exc}")
    if proc.returncode == 0 and proc.stdout.strip():
        return _token_from_json(proc.stdout.strip(), f'keychain "{KEYCHAIN_SERVICE}"')

    raise UsageError(
        f"no token — {CREDENTIALS} missing and keychain service "
        f'"{KEYCHAIN_SERVICE}" not found. Set LIMITSCHECKER_KEYCHAIN_SERVICE.'
    )


def _fetch_usage_once() -> dict[str, Any]:
    token = _read_token()
    req = urllib.request.Request(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
            "User-Agent": "limitschecker-macos",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        if exc.code == 401:
            # Auth failures aren't transient — the token is dead until Claude
            # Code (or a re-login) refreshes the credentials.
            raise UsageError("401 unauthorized — token invalid/expired, re-login to Claude")
        if exc.code == 429 or exc.code >= 500:
            retry_after = None
            hdr = exc.headers.get("Retry-After") if exc.headers else None
            if hdr:
                try:
                    retry_after = float(hdr)
                except ValueError:
                    retry_after = None
            label = "429 rate-limited" if exc.code == 429 else f"HTTP {exc.code}"
            raise UsageTransient(f"{label}: {body or exc.reason}", retry_after)
        raise UsageError(f"HTTP {exc.code}: {body or exc.reason}")
    except urllib.error.URLError as exc:
        raise UsageTransient(f"network error: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        raise UsageTransient(f"network error: {exc}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        # A truncated body or a gateway HTML error page — treat as transient.
        raise UsageTransient(f"bad JSON from usage endpoint: {exc}")
    if not isinstance(payload, dict):
        raise UsageError("unexpected usage JSON shape")
    return payload


def _fetch_usage() -> dict[str, Any]:
    """Fetch usage, retrying a few times on transient failures.

    Runs in a worker thread, so the short blocking sleeps here never touch the
    UI. Longer waits are handled by the app's retry timer instead, so the
    in-fetch sleep is capped small.
    """
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return _fetch_usage_once()
        except UsageTransient as exc:
            if attempt + 1 >= FETCH_ATTEMPTS:
                raise
            delay = exc.retry_after if exc.retry_after is not None else (1 + attempt * 2)
            time.sleep(max(0.0, min(delay, 5.0)))
    raise UsageTransient("exhausted retries")  # unreachable; satisfies type-checkers


def _pct(value: Any) -> "int | None":
    if isinstance(value, (int, float)):
        return max(0, min(999, round(value)))
    return None


def _parse_dt(raw: Any) -> "datetime | None":
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reset_absolute(raw: Any) -> "str | None":
    dt = _parse_dt(raw)
    return dt.astimezone().strftime("%b %d %H:%M") if dt else None


def _reset_countdown(raw: Any) -> "str | None":
    dt = _parse_dt(raw)
    if dt is None:
        return None
    secs = (dt - datetime.now(dt.tzinfo)).total_seconds()
    if secs <= 0:
        return "now"
    minutes = int(secs // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _bar(pct: "int | None") -> str:
    p = max(0, min(100, pct or 0))
    filled = round(p / 100 * BAR_WIDTH)
    return FILL_CHAR * filled + TRACK_CHAR * (BAR_WIDTH - filled)


class View:
    def __init__(self) -> None:
        self.rows: list = []       # (name, percent, resets_at_raw)
        self.warn = False
        self.spend = None


def _build_view(payload: dict) -> View:
    view = View()
    session = (None, None)
    weekly = (None, None)
    scoped = []

    limits = payload.get("limits")
    if isinstance(limits, list):
        for lim in limits:
            if not isinstance(lim, dict):
                continue
            kind = str(lim.get("kind") or "")
            pct = _pct(lim.get("percent"))
            reset = lim.get("resets_at")
            if str(lim.get("severity") or "normal") not in ("normal", ""):
                view.warn = True
            if kind == "session":
                session = (pct, reset)
            elif kind == "weekly_all":
                weekly = (pct, reset)
            elif kind == "weekly_scoped" and pct is not None and lim.get("is_active", True):
                scope = lim.get("scope") if isinstance(lim.get("scope"), dict) else {}
                model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
                label = str(model.get("display_name") or scope.get("surface") or "model")
                scoped.append((label, pct, reset))

    if session[0] is None and isinstance(payload.get("five_hour"), dict):
        w = payload["five_hour"]
        session = (_pct(w.get("utilization")), w.get("resets_at"))
    if weekly[0] is None and isinstance(payload.get("seven_day"), dict):
        w = payload["seven_day"]
        weekly = (_pct(w.get("utilization")), w.get("resets_at"))

    view.rows.append((NAME_SESSION, session[0], session[1]))
    view.rows.append((NAME_WEEK, weekly[0], weekly[1]))
    view.rows.extend(scoped)

    for _, pct, _reset in view.rows:
        if pct is not None and pct >= WARN_PERCENT:
            view.warn = True

    extra = payload.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        u = _pct(extra.get("utilization"))
        if u is not None:
            view.spend = f"extra usage {u}%"
    return view


def _panel_label(view: View) -> str:
    cells = [
        f"{name} {'--' if pct is None else str(pct) + '%'}"
        for name, pct, _ in view.rows
    ]
    text = " · ".join(cells) if cells else "no data"
    return ("⚠ " + text) if view.warn else text


def _menu_rows(view: View) -> list:
    lines = []
    for name, pct, reset in view.rows:
        if pct is None:
            lines.append(f"{TRACK_CHAR * BAR_WIDTH}   --  {name}")
            continue
        cd = _reset_countdown(reset)
        tail = f"   ·  resets in {cd}" if cd else ""
        lines.append(f"{_bar(pct)}  {(str(pct) + '%').rjust(4)}  {name}{tail}")
    if view.spend:
        lines.append(view.spend)
    return lines


def _details_text(view: View) -> str:
    lines = []
    for name, pct, reset in view.rows:
        val = "--" if pct is None else f"{pct}%"
        at = _reset_absolute(reset)
        lines.append(f"{name}: {val}" + (f"  (resets {at})" if at else ""))
    if view.spend:
        lines.append(view.spend)
    return "\n".join(lines)


def _on_main(fn, *args) -> None:
    """Run a UI update on the AppKit main thread."""
    if AppHelper is not None:
        AppHelper.callAfter(fn, *args)
    else:  # best effort
        fn(*args)


class LimitsCheckerApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(APP_TITLE, title=f"{APP_TITLE} …", quit_button=None)
        self._rows = [rumps.MenuItem("") for _ in range(ROW_SLOTS)]
        self._details_text = f"{APP_TITLE}: starting..."
        # Last successful snapshot + pending one-shot retry, so a failed poll
        # keeps showing good data instead of blanking the menu.
        self._last_view: "View | None" = None
        self._last_error: "str | None" = None
        self._retry_timer: "rumps.Timer | None" = None
        self.menu = [
            *self._rows,
            None,
            rumps.MenuItem("Refresh", callback=self.on_refresh),
            rumps.MenuItem("Show details", callback=self.on_details),
            None,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]
        self._set_rows([f"{APP_TITLE}: starting..."])
        self._timer = rumps.Timer(self.on_refresh, REFRESH_SECONDS)
        self._timer.start()
        self.on_refresh(None)

    def _set_rows(self, lines: list) -> None:
        for i, item in enumerate(self._rows):
            if i < len(lines):
                item.title = lines[i]
                item.hidden = False
            else:
                item.title = ""
                item.hidden = True

    def on_refresh(self, _sender) -> None:
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            view = _build_view(_fetch_usage())
            _on_main(self._apply_ok, view)
        except UsageError as exc:
            _on_main(self._apply_error, str(exc), isinstance(exc, UsageTransient))
        except Exception as exc:
            _on_main(self._apply_error, f"{type(exc).__name__}: {exc}", True)

    def _apply_ok(self, view: View) -> None:
        if self._retry_timer is not None:
            self._retry_timer.stop()
            self._retry_timer = None
        self._last_view = view
        self._last_error = None
        self._details_text = _details_text(view)
        self._set_rows(_menu_rows(view))
        self.title = _panel_label(view)

    def _apply_error(self, message: str, transient: bool) -> None:
        self._last_error = message
        view = self._last_view
        if view is not None:
            # A single failed poll must not blank the menu bar. Keep the last
            # good numbers visible, note the failure at the foot of the menu,
            # and let the retry timer recover — most blips clear within one try.
            rows = _menu_rows(view)
            rows.append(f"⚠ last update failed — retrying · {message}"[:120])
            self._set_rows(rows)
            self.title = _panel_label(view)
            self._details_text = _details_text(view) + f"\n\n⚠ last update failed: {message}"
        else:
            # No good data yet (e.g. first poll on a dead token) — surface it.
            self._details_text = f"{APP_TITLE}: ERROR: {message}"
            self._set_rows([f"⚠ ERROR: {message}"[:120]])
            self.title = "⚠ error"
        # Transient failures retry soon; auth/other failures back off further so
        # we don't hammer a dead token (it recovers when Claude Code refreshes).
        self._schedule_retry(RETRY_SECONDS if transient else RETRY_SECONDS * 4)

    def _schedule_retry(self, delay: int) -> None:
        if self._retry_timer is not None:
            return  # a retry is already queued
        self._retry_timer = rumps.Timer(self._do_retry, delay)
        self._retry_timer.start()

    def _do_retry(self, _sender) -> None:
        if self._retry_timer is not None:
            self._retry_timer.stop()
            self._retry_timer = None
        self.on_refresh(None)

    def on_details(self, _sender) -> None:
        # Write to a temp file and open it in the default text editor.
        try:
            fd, path = tempfile.mkstemp(prefix="limitschecker-", suffix=".txt")
            with os.fdopen(fd, "w") as fh:
                fh.write(self._details_text)
            subprocess.Popen(["open", "-t", path])
        except OSError as exc:
            rumps.alert(APP_TITLE, f"cannot show details: {exc}")


def main() -> None:
    LimitsCheckerApp().run()


if __name__ == "__main__":
    main()
