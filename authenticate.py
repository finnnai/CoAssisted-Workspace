#!/usr/bin/env python3
"""Standalone OAuth flow — saves token.json and exits.

Used by `install.sh --oauth`. Does NOT start the MCP server, so stdout is free
to print OAuth URLs and status messages without corrupting MCP protocol.

Normal flow:
    1. Reads credentials.json
    2. Starts a local HTTP server on a random port
    3. Opens your default browser to Google's OAuth consent screen
    4. Receives the redirect after you approve
    5. Writes token.json
    6. Exits 0
"""

from __future__ import annotations

import sys

from auth import AuthError, get_credentials


def main() -> int:
    print("━━━ Google OAuth ━━━")
    print("A browser window will open in a moment. Sign in with the account you")
    print("want the MCP to act as, click 'Advanced' → 'Go to Claude Cowork MCP")
    print("(unsafe)' on the warning page, and grant all the requested scopes.")
    print()
    print("If the browser doesn't open automatically, copy the URL that appears")
    print("below and paste it into your browser MANUALLY. Do NOT paste it back")
    print("into this terminal.")
    print()

    try:
        creds = get_credentials()
    except AuthError as e:
        print(f"\n✗ Auth error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n✗ Cancelled by user.", file=sys.stderr)
        return 130

    if creds and creds.valid:
        print()
        print("✓ Auth successful. token.json has been saved.")
        print("  You can now add the MCP to Claude Cowork's config and restart Cowork.")
        return 0

    print("✗ Auth did not complete cleanly — no valid credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
