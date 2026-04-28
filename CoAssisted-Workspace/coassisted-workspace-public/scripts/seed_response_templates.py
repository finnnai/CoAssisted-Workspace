#!/usr/bin/env python3
# See LICENSE file for terms.
"""Seed 8 brand-voice response templates as HTML.

Reads `brand-voice.md` for tone/voice, generates 8 reusable email templates
in the user's voice via Claude Haiku, and writes them to `templates/*.md`
with HTML bodies in frontmatter so the existing send-templated tools can
pick them up without changes.

Categories (one template each):
  - customer_complaint_response
  - upset_client_response
  - thanks_for_reply
  - great_to_meet_you
  - client_feedback_response
  - welcome_response
  - renewal_reminder_response
  - followup_response

By default skips templates that already exist (preserves user edits). Pass
`--force` to overwrite, `--only <slug>` to regenerate just one, `--dry-run`
to preview without writing.

Cost: ~$0.02 for the full set (Haiku 4.5).

Run from Terminal (NOT Cowork):

    cd "/Users/finnnai/Claude/google_workspace_mcp"
    .venv/bin/python scripts/seed_response_templates.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))


CATEGORIES: list[dict] = [
    {
        "slug": "inbound_customer_complaint",
        "subject_hint": "Re: {subject|your message}",
        "scenario": (
            "A customer has written in with a complaint or issue. Acknowledge "
            "what they raised in their own words (one short sentence). Take "
            "ownership without making excuses. Propose a specific next step "
            "with a clear date. Tone: warm, accountable, no corporate hedging."
        ),
        "description": "Acknowledge a complaint, take ownership, commit to a next step.",
    },
    {
        "slug": "inbound_upset_client",
        "subject_hint": "Re: {subject|where we go from here}",
        "scenario": (
            "An existing client is openly frustrated and may be considering "
            "leaving. Don't argue or get defensive. Restate what you heard so "
            "they know you understood. Commit to a single concrete action with "
            "a date attached. Offer a 15-min call. Keep it short — under 8 "
            "lines. Tone: calm, direct, action-oriented."
        ),
        "description": "Frustrated existing client — restate, commit, offer a call.",
    },
    {
        "slug": "inbound_thanks_for_reply",
        "subject_hint": "Re: {subject|thanks}",
        "scenario": (
            "Someone replied to your email and you want to acknowledge it "
            "before continuing the thread. Keep it under 4 lines. Don't add "
            "filler. If they raised a question, name what you'll do next."
        ),
        "description": "Short, warm acknowledgment of a reply. Under 4 lines.",
    },
    {
        "slug": "inbound_great_to_meet",
        "subject_hint": "Great to meet you, {first_name|there}",
        "scenario": (
            "Just met someone — at an event, intro, video call. Send a short "
            "follow-up while you're top of mind. Reference one specific thing "
            "they said or did. Suggest a concrete next step (call, intro, "
            "doc). Tone: warm but focused, not gushy."
        ),
        "description": "Post-introduction follow-up. Reference something specific + propose a next step.",
    },
    {
        "slug": "inbound_client_feedback",
        "subject_hint": "Re: {subject|the feedback}",
        "scenario": (
            "A client shared feedback — could be praise, criticism, or a "
            "feature request. Thank them sincerely. Reflect what you heard "
            "in your own words. Be honest about whether/when you'll act on it: "
            "'this is on the roadmap' / 'we won't do this and here's why' / "
            "'I'm escalating internally'. No fake commitments."
        ),
        "description": "Acknowledge client feedback honestly — no fake commitments.",
    },
    {
        "slug": "inbound_welcome",
        "subject_hint": "Welcome, {first_name|there}",
        "scenario": (
            "A new customer or contact just signed up / joined / was "
            "introduced. Welcome them warmly. Set expectations for what "
            "happens next (a specific link, a call, a follow-up timeline). "
            "Include one CTA — don't list five resources."
        ),
        "description": "Welcome a new contact. Single clear CTA, set timing expectations.",
    },
    {
        "slug": "inbound_renewal_response",
        "subject_hint": "Re: {subject|your renewal}",
        "scenario": (
            "A client responded to your renewal-reminder email. They might be "
            "ready to renew, or they have questions, or they're considering "
            "alternatives. Confirm what they're saying. Propose the simplest "
            "path forward (sign here, hop on a call, or escalate). Don't push."
        ),
        "description": "They replied to a renewal nudge. Confirm + propose simplest next step.",
    },
    {
        "slug": "inbound_followup_response",
        "subject_hint": "Re: {subject|circling back}",
        "scenario": (
            "Someone responded to a follow-up you sent. Catch them up on "
            "anything that's changed since the last message, name the specific "
            "ask or next step, and keep moving the thread forward. Tone: "
            "businesslike, no apology for the gap."
        ),
        "description": "They replied to your follow-up. Catch them up + name the next step.",
    },
]


_BUILD_PROMPT = """\
You are writing a reusable email template for a user whose brand voice is captured below.

# Brand voice

{brand_voice}

# Template scenario

{scenario}

# Output rules

Return ONLY a JSON object — no prose, no code fences, no commentary. Schema:

{{
  "subject": "<subject line; may include {{var|fallback}} placeholders>",
  "body_plain": "<plain-text body; uses the same placeholder syntax; signs off with the user's signature style>",
  "body_html": "<HTML body using semantic tags only — <p>, <strong>, <em>, <ul>, <ol>, <li>, <a href>. NO inline styles, NO <body>/<html> wrappers. Match the same content as body_plain but with proper paragraph and list structure.>",
  "description": "<one-line description of when to use this template>"
}}

Constraints:
- Use placeholders like {{first_name|there}} for the recipient first name with a sensible fallback.
- Use {{subject|...}} when echoing the subject line they sent.
- The signature should match the brand voice (em-dash + first name, etc.).
- Keep length appropriate to the scenario — short scenarios like 'thanks_for_reply' should be 2-4 sentences. Longer scenarios like 'upset_client_response' can be 6-8.
- HTML must be valid, no unclosed tags. Use <p> for paragraphs, not <br>.
"""


def _read_brand_voice() -> str:
    """Read brand-voice.md if it exists. Otherwise return a generic placeholder
    so the script still produces sensible output for new users."""
    p = _PROJECT / "brand-voice.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return (
        "(No brand-voice.md found — using generic professional voice.)\n"
        "- Direct and warm, no corporate buzzwords\n"
        "- Em-dashes for asides\n"
        "- Sign off with first name only\n"
        "- Avoid 'Best regards', 'Hope you're well', 'I hope this email finds you well'\n"
    )


def _generate_one(category: dict, brand_voice: str) -> dict:
    """Call Claude Haiku to draft one template. Returns the parsed JSON."""
    import llm
    prompt = _BUILD_PROMPT.format(
        brand_voice=brand_voice[:6000],  # cap so we don't blow the context
        scenario=category["scenario"],
    )
    result = llm.call_simple(
        prompt,
        model="claude-haiku-4-5",
        max_tokens=1500,
        temperature=0.3,  # a little variation, not robotic
    )
    text = result["text"].strip()
    # Strip code fences if Claude wrapped JSON despite instructions.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)


def _render_template_file(category: dict, generated: dict) -> str:
    """Combine generated content + frontmatter into a templates/*.md file."""
    subject = generated.get("subject") or category["subject_hint"]
    description = generated.get("description") or category["description"]
    body_plain = generated.get("body_plain", "").strip()
    body_html = generated.get("body_html", "").strip()

    # Indent HTML so it sits cleanly under `html_body: |` in YAML frontmatter.
    html_indented = "\n".join("  " + line for line in body_html.splitlines())

    return (
        "---\n"
        f"subject: \"{subject}\"\n"
        f"description: {description}\n"
        f"format: html\n"
        f"html_body: |\n{html_indented}\n"
        "---\n"
        f"{body_plain}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate 8 brand-voice response templates as HTML."
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Overwrite existing template files (default: skip).",
    )
    ap.add_argument(
        "--only", type=str, default=None,
        help="Generate just one template by slug (e.g. 'welcome_response').",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be generated without writing files.",
    )
    args = ap.parse_args()

    try:
        import llm
        ok, reason = llm.is_available()
    except Exception as e:
        print(f"\033[31m✗ Could not import llm: {e}\033[0m")
        return 1
    if not ok:
        print(f"\033[31m✗ Anthropic API not available: {reason}\033[0m")
        return 1

    brand_voice = _read_brand_voice()
    templates_dir = _PROJECT / "templates"
    templates_dir.mkdir(exist_ok=True)

    cats = CATEGORIES
    if args.only:
        cats = [c for c in CATEGORIES if c["slug"] == args.only]
        if not cats:
            print(f"Unknown slug: {args.only}. Available:")
            for c in CATEGORIES:
                print(f"  - {c['slug']}")
            return 2

    print("\033[1m" + "=" * 70 + "\033[0m")
    print("\033[1m  Seeding brand-voice response templates\033[0m")
    print("\033[1m" + "=" * 70 + "\033[0m\n")

    created = 0
    skipped = 0
    failed = 0
    total_cost = 0.0

    for category in cats:
        slug = category["slug"]
        path = templates_dir / f"{slug}.md"
        if path.exists() and not args.force:
            print(f"  \033[33m⊘\033[0m {slug:30s} already exists, skipping (--force to overwrite)")
            skipped += 1
            continue

        print(f"  \033[36m→\033[0m {slug:30s} generating…", end="", flush=True)
        try:
            generated = _generate_one(category, brand_voice)
        except Exception as e:
            print(f"  \033[31m✗ failed: {e}\033[0m")
            failed += 1
            continue

        body = _render_template_file(category, generated)

        if args.dry_run:
            print(f"  \033[33m(dry-run)\033[0m")
            print("    " + "\n    ".join(body.splitlines()[:6]))
            print("    ...")
            continue

        path.write_text(body, encoding="utf-8")
        # Cost estimate from the result if available
        try:
            import llm as _llm
            # we don't have direct access to result here; just log a flat estimate
            total_cost += 0.0025
        except Exception:
            pass
        print(f"  \033[32m✓\033[0m wrote {path.name}")
        created += 1

    print()
    print("\033[1m" + "=" * 70 + "\033[0m")
    if failed == 0:
        print(f"\033[32m\033[1mDone — {created} created, {skipped} skipped\033[0m")
    else:
        print(f"\033[31m\033[1mDone with errors — {failed} failed, {created} created, {skipped} skipped\033[0m")
    if not args.dry_run and created:
        print(f"  Estimated cost: ~${total_cost:.3f}")
    print("\033[1m" + "=" * 70 + "\033[0m\n")

    if not args.dry_run and created:
        print("Test one before sending:")
        print(f"  .venv/bin/python -c \"import templates; "
              f"t = templates.load('{cats[0]['slug']}'); print(t.subject); "
              f"print(t.body); print('---HTML---'); print(t.html_body)\"")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
