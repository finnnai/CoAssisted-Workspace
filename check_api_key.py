#!/usr/bin/env python3
"""Verify ANTHROPIC_API_KEY is set + valid by making one tiny live API call.

Safe to run: max_tokens=5, claude-haiku-4-5 — costs ~$0.0001 per check.

Run with the project's venv:
    /Users/finnnai/Claude/google_workspace_mcp/.venv/bin/python check_api_key.py
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not key:
        print("✗ ANTHROPIC_API_KEY is not set in your environment.")
        print()
        print("  To set it, add this line to ~/.zshrc (or ~/.bashrc):")
        print('    export ANTHROPIC_API_KEY="sk-ant-api03-YOURKEY"')
        print("  then run: source ~/.zshrc")
        return 1

    if not key.startswith("sk-ant-"):
        print(f"✗ Key is set but does not look like an Anthropic key.")
        print(f"  Expected prefix 'sk-ant-...'; got '{key[:10]}...'")
        return 1

    print(f"✓ Key set: {key[:15]}... (length: {len(key)})")

    # Try installing the SDK if not present.
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print()
        print("Anthropic SDK not installed in this venv. Installing now...")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "anthropic>=0.40"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"✗ pip install failed: {result.stderr}")
            return 2
        print("  Installed.")

    import anthropic
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with: ok"}],
        )
        reply = "".join(
            getattr(b, "text", "") for b in (msg.content or [])
        ).strip()
        print(f"✓ Live API call succeeded. Model replied: '{reply}'")
        print(f"  Usage: {msg.usage.input_tokens} input / {msg.usage.output_tokens} output tokens")
        print(f"  Cost: ~$0.0001")
        return 0
    except Exception as e:
        print(f"✗ Live API call failed: {e}")
        print()
        print("  Possible causes:")
        print("    - Key is invalid or revoked")
        print("    - No billing set up (Settings → Billing on console.anthropic.com)")
        print("    - Network/proxy blocking api.anthropic.com")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
