# LimitsChecker for macOS

A macOS menu-bar version of LimitsChecker. Same data (the Anthropic OAuth usage
API), rendered with [`rumps`](https://github.com/jaredks/rumps) instead of the
Linux AppIndicator.

The menu-bar title shows `Session 16% · Week 43% · Fable 59%`; the drop-down
lists each window with a text progress bar and reset countdown, plus **Refresh**
and **Show details** (opens the raw JSON in your text editor).

> ⚠️ Written on Linux and not yet verified on a Mac — please try it and report
> back. The one thing likely to need adjusting is the **token source** (see below).

## Requirements

- macOS with Python 3.9+
- `pip3 install rumps` (pulls in pyobjc)
- Claude Code logged in

## Run

```bash
pip3 install rumps
python3 macos/limitschecker.py
```

To keep it running in the background / at login, package it into a `.app` with
[py2app](https://py2app.readthedocs.io/) or launch it from a `launchd` agent.

## Token source (important on macOS)

On Linux the OAuth token lives in `~/.claude/.credentials.json`. On macOS Claude
Code typically stores it in the **login Keychain** instead. The app tries, in
order:

1. `~/.claude/.credentials.json` (if present)
2. the Keychain: `security find-generic-password -s "Claude Code-credentials" -w`

If your Keychain item uses a different name, find it and set the service:

```bash
# see which Claude-related items exist
security dump-keychain 2>/dev/null | grep -i claude

# then point the app at the right one
export LIMITSCHECKER_KEYCHAIN_SERVICE="<the service name>"
python3 macos/limitschecker.py
```

The Keychain item is expected to contain the same JSON as the Linux credentials
file (with a `claudeAiOauth.accessToken` field).

## Configuration

Environment variables (same idea as the Linux build, `LIMITSCHECKER_*` prefix):

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIMITSCHECKER_KEYCHAIN_SERVICE` | `Claude Code-credentials` | Keychain service name for the token |
| `LIMITSCHECKER_CREDENTIALS` | `~/.claude/.credentials.json` | Credentials file path (tried first) |
| `LIMITSCHECKER_REFRESH_SECONDS` | `300` | Refresh interval (min 5) |
| `LIMITSCHECKER_WARN_PERCENT` | `80` | Threshold (0–100) for the `⚠` badge |
| `LIMITSCHECKER_TIMEOUT` | `30` | HTTP timeout, seconds |
| `LIMITSCHECKER_BAR_WIDTH` | `10` | Progress-bar width in glyphs |
| `LIMITSCHECKER_NAME_SESSION` / `_WEEK` | `Session` / `Week` | Window labels |
| `LIMITSCHECKER_TITLE` | `LimitsChecker` | Menu-bar / app title |

## Also on macOS

If you want a polished native (Swift) menu-bar app today,
[CodexBar](https://github.com/steipete/CodexBar) supports Claude on macOS. This
`rumps` port is the lightweight, same-codebase counterpart to the Linux build.
