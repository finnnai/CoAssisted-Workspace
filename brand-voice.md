# Brand Voice Guidelines

_Auto-generated from your last 90 days of sent mail. Do not hand-edit
above the divider — re-run `refresh_brand_voice.py` to regenerate. Hand-edits
below the divider are preserved._

**Generated:** 2026-04-24 19:50 PDT
**Source:** 14 sent emails (scanned 21, kept 14)
**Date range:** 2026-04-23T15:44:04Z → 2026-04-25T01:37:07Z
**Mode:** LLM analysis (claude-sonnet-4-6)

---

# Brand-Voice Style Guide

---

## 1. Voice in One Paragraph

This is the voice of a technically fluent builder who respects the reader's time above all else. Messages are dense with useful information and almost entirely free of social padding — no "Hope you're doing well," no "Please don't hesitate to reach out." The tone is collegial and direct, like a trusted colleague dropping something on your desk with a sticky note rather than a formal memo. There's a quiet confidence: instructions are given without over-explaining, caveats are precise rather than apologetic, and the occasional dry aside ("Delete at will," "Ping me with anything weird") signals warmth without performing it.

---

## 2. Tone & Formality

**Spectrum position:** Casual-professional. Closer to Slack than to a business letter, but never sloppy.

- Greetings exist but are minimal: `Hi there`, `Hi Josh`, `Josh —`
- No titles, no last names, no "Dear"
- Technical terms are used without apology or definition: `MCP server`, `OAuth credentials`, `tarball`, `threading + headers logic`
- Apologies are brief and functional: *"Apologies for the spam"* — one clause, not a paragraph
- Warmth appears in small doses via word choice (`Ping me`, `anything weird`, `Delete at will`) rather than explicit friendliness
- The register stays consistent whether writing to one person or a group

---

## 3. Sentence Rhythm

**Typical pattern:** Short declarative opener → compact supporting detail → one-line action item or closer.

- Opening sentences are often a single clause that names the thing being shared: *"Sharing the Google Workspace MCP."* / *"Latest build with all the fixes from this week's testing."*
- Lists do heavy lifting. When there are multiple items, they go into a bulleted or indented list rather than a run-on sentence.
- Sentences rarely exceed 30 words. When they do, an em-dash breaks the load: *"…including actual email send, not just drafts."*
- Closing sentences are short imperatives or open invitations: *"Ping me if anything breaks or you want a walk-through."* / *"Restart Cowork after install to pick up the patches."*
- Some messages are a single sentence or even a single phrase (e.g., *"Regression: testing the old `note` param name (was `comment`)"*) — no padding added just to reach a "proper" length

---

## 4. Sign-offs & Openers

**Openers actually used:**
- `Hi there,` — default for semi-anonymous or group sends
- `Hi Josh,` / `Hi Joshua,` — first name when addressing someone directly
- `Josh —` — em-dash after name for quick, informal notes (no "Hi")
- No opener at all — several messages begin directly with the content

**Sign-offs actually used:**
- `Thanks,` — appears once, in the most complete/formal of the handoff emails
- `— Finnn` — used in the short, informal tool-test note
- Most messages have **no sign-off at all** — content ends and the message ends

**Pattern to follow:** Use `Hi [first name],` for direct messages with real content. Use no opener for one-liners, test notes, or forwarded items. Sign off only when the message is substantive enough to warrant it; otherwise, let it end on the closing action line.

---

## 5. Vocabulary & Phrasing

**Recurring phrases (use these):**
- *"Ping me"* — preferred over "let me know," "feel free to contact," "reach out"
- *"anything weird"* / *"anything breaks"* — casual but specific invitation for feedback
- *"walk-through"* — hyphenated, used consistently
- *"hands-on time"* — distinguishes active work from passive waiting
- *"No action needed"* / *"No response needed"* — explicit permission to ignore, used for test/automated messages
- *"Delete at will"* — breezy dismissal, signals low stakes
- *"supersedes"* — precise word choice over "replaces" or "updates"
- *"baked in"* — informal but vivid: *"now with a new one-call handoff tool baked in"*
- *"one-time"* — used to flag setup steps that only happen once
- *"one-call"* — technical shorthand, used without explanation

**Vocabulary character:**
- Prefers concrete nouns and active verbs over nominalizations ("install" not "installation process")
- Uses backtick code formatting inline: `` `note` ``, `` `./install.sh` `` — even in plain email
- Numbers are written as numerals: `90 tools`, `16 caught + fixed bugs`, `~15-min setup`, `800KB`
- Approximations use `~` rather than "approximately" or "about" (in technical contexts)

---

## 6. Punctuation Habits

| Mark | Usage |
|---|---|
| **Em-dash ( — )** | Primary tool for asides, clarifications, and name-to-content transitions. *"…including actual email send, not just drafts."* / *"Josh — this is an end-to-end test…"* Used liberally, always with spaces on both sides. |
| **Bullet / indented lists** | Preferred over semicolon-separated lists for anything with 3+ items. Uses `  -` (two-space indent + hyphen) in plain-text contexts. |
| **Parentheses** | Used for brief clarifications: `(was 'comment')`, `(under 500KB threshold)`, `(shared with you on Drive, download from here)` |
| **Exclamation points** | Essentially absent from body copy. Zero instances across 14 samples. |
| **Semicolons** | Not used. Lists or new sentences instead. |
| **Ellipsis** | Not used. |
| **Commas** | Minimal. Short sentences reduce the need for them. |
| **`+` as conjunction** | Used in technical/list contexts: `16 caught + fixed bugs`, `Source code + install script` |
| **Colon** | Used to introduce lists and label sections: `What's inside the tarball:`, `Merged data captured for you:` |

---

## 7. What I Avoid

- **Pleasantries and throat-clearing:** No "Hope this finds you well," "As per my last email," "I wanted to reach out," or "Just following up"
- **Exclamation points:** Completely absent — enthusiasm is conveyed through word choice, not punctuation
- **Passive voice:** Instructions are direct imperatives ("Restart Cowork," "Ping me," "start with HANDOFF.md")
- **Hedging language:** No "I think," "it might be worth," "you may want to consider"
- **Over-apologizing:** Apologies appear once, briefly, and only when genuinely warranted (*"Apologies for the spam"*)
- **Filler transitions:** No "Additionally," "Furthermore," "In conclusion," "As mentioned"
- **Formal closings:** No "Best regards," "Sincerely," "Kind regards," "Warm wishes"
- **Redundant context:** Doesn't re-explain what the recipient already knows; jumps to the new information
- **Emoji:** None present across all samples

---

## 8. Three Do/Don't Pairs

### 1. Opening a message

✅ **Do:** Start with the thing itself.
> *"Sharing the Google Workspace MCP. It's a local MCP server that gives Claude Cowork about 90 tools…"*

❌ **Don't:** Warm up with social filler.
> *"Hi Josh, hope you're having a great week! I wanted to share something I've been working on that I think you'll find really useful…"*

---

### 2. Giving instructions

✅ **Do:** Short imperative, then the reason in the same breath.
> *"Restart Cowork after install to pick up the patches."*
> *"Start with HANDOFF.md; it walks you through the ~15-min setup."*

❌ **Don't:** Nominalize and over-explain.
> *"Please ensure that a restart of the Cowork application is performed following installation in order to ensure that all patches are properly applied."*

---

### 3. Closing a message

✅ **Do:** End on a concrete, low-friction invitation.
> *"Ping me if anything breaks or you want a walk-through."*
> *"Ping me with anything weird."*

❌ **Don't:** Close with a formal sign-off or a vague open door.
> *"Please do not hesitate to contact me should you have any questions or require further assistance. Best regards."*

---

*Note on sample limitations: The 14 samples are heavily weighted toward technical handoff and test emails, with almost no examples of relationship-building, negotiation, or conflict messages. The patterns above are reliable for this category of communication. Signals for how this voice handles sensitive topics, persuasion, or longer narrative emails are weak in this dataset and should not be extrapolated from this guide.*

---

## Hand-edits (preserved across regeneration)

_Add overrides, exceptions, or context here. The `brand-voice:enforce-voice`
skill reads everything in this file._
