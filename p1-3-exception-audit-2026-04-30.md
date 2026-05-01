# P1-3 — broad-exception audit (2026-04-30)

## Method
Sampled every 18th occurrence of `except Exception(?:\s+as\s+e)?:` across
production code (excluding `tests/`, `.venv/`, `__pycache__/`, backup files).
Total population: **558 broad-except handlers**. Sample: **31** occurrences.

For each, classified as:
- **Legitimate** — wraps a network/API/file/parsing call AND either re-raises,
  returns an error sentinel through `format_error()`, or logs the exception
  with context.
- **Swallow-ish** — catches `Exception` with no log AND a more specific type
  would suffice OR the failure should be surfaced (even at debug level).

## Result

**25 legitimate / 6 swallow-ish (19.4%).** Above the 10% threshold from
`mcp-audit-2026-04-29.md`, so a fix pass landed.

| # | Location | Pattern before | Fix |
|---|---|---|---|
| S12 | `tools/morning_brief.py:74` | `getProfile()` failure → silent `None` | `log.warning` |
| S14 | `tools/workflows_calendar.py:2979` | `gmaps.geocode(addr)` per-iter failure → silent `continue` | `log.debug` |
| S21 | `tools/project_invoices.py:828` | `_pr.list_all()` failure → silent `""` | `log.warning` |
| S26 | `tools/project_invoices.py:5212` | `import llm` → catches all of `Exception` | `except ImportError` |
| S28 | `receipts.py:603` | `from PIL import Image` → catches all | `except ImportError` |
| S29 | `ap_drive_layout.py:267` | name-resolution fall-through → silent `pass` | `log.debug` (also added missing `log` import) |

## What the audit confirms

The remaining **25 of 31 (81%)** broad-except handlers are warranted:
- 13 are **tool-boundary handlers** that wrap an entire `@mcp.tool` body and
  delegate to `format_error()` (returns a JSON error dict). This is the
  established pattern for the FastMCP surface and shouldn't be tightened.
- 7 are **API failure handlers** (Anthropic, Google APIs, Drive, gmaps)
  that log + return a structured error to the caller.
- 3 are **defensive fallbacks** in chained-resolution code (geolocation
  IP→GPS→Google API; display-name resolution Tier 1→2→3).
- 2 are **deliberate swallows with context** (e.g. `setup_wizard` falling
  back to a fresh config when `config.json` is unparseable, with a `warn(...)`
  to the user).

## Extrapolating to the population

If the sample's 19.4% swallow rate generalizes, the codebase has roughly
**108 swallow-ish handlers** (558 × 0.194). The 6 fixed in this pass are
~6% of the projected total. The cleanup is incremental — pick them up
opportunistically as you touch each file.

## Recommended pattern for new code

For new tool implementations:
```python
try:
    ... # API/network call
except SpecificError as e:
    return format_error(e)  # narrow, expected error
except Exception as e:
    log.error("tool_name unexpected: %s", e)  # broad safety net
    return format_error(e)
```

For library helpers that fall through to a fallback:
```python
try:
    ...
except (ExpectedTypeA, ExpectedTypeB) as e:
    log.debug("primary failed, falling back: %s", e)
    # fallback chain continues
```

## Tests
All 1293 tests still pass after the 6 fixes (including 1 import addition
to `ap_drive_layout.py`). Suite runtime: 3.56s.
