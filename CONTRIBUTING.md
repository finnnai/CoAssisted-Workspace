# Contributing to CoAssisted Workspace

How development works on this project. Captures the patterns established
across the v0.7.0 → v0.7.2-dev arc (Apr 2026) so they don't get lost.

If you're a new owner or a collaborator, read this first. If you've been
building on this for a while, this is a memory aid.

---

## Spec IDs

Work is organized by **spec ID**: `P0-N`, `P1-N`, `P2-N`.

- **P0** — must do soon. Real impact, real risk.
- **P1** — should do this quarter. Net positive but not urgent.
- **P2** — nice-to-have. Polish, code quality, future-proofing.

Each spec has a design doc in `mcp-design-docs-YYYY-MM-DD.md` (or its
own file) with: problem, scope, files touched, behavior, acceptance
criteria, effort estimate, open questions. Don't start building until
the spec is written or amended.

## Audit-driven backlog

Every quarter or so, run a top-issues audit:

1. Walk the codebase (helpers, tools, tests, CHANGELOG, HANDOFF_LOG).
2. Classify findings into **Strengths**, **Gaps (P0)**, **Bugs/risks
   (P1)**, **Polish (P2)**.
3. Write the audit as `mcp-audit-YYYY-MM-DD.md` and the design docs
   as a sibling file.
4. Pick the next spec from the audit. Update HANDOFF_STATE.json's
   `open_tasks` to reflect the audit.

Audit + design doc takes ~30–45 minutes; each spec takes anywhere from
30 minutes (P2-1 config validator) to a full day (P1-1 split workflows).

## Commit messages

```
<spec-id>: <one-line summary>

<paragraph: what changed and why>

<bullet list: file-by-file impact if non-trivial>

<test count delta + suite runtime>
```

Examples from recent history:

- `P0-2: confidence-gated vendor reply parsing (full spec)`
- `P1-1: split tools/workflows.py into 5 category modules`
- `P0-3: baseline unit tests for drive, calendar, gmail (3 of 13)`

Handoff-only commits use a different prefix:

- `Handoff hygiene: P1-1 captured in CHANGELOG/HANDOFF_LOG/STATE`

## Handoff hygiene (run after every spec lands)

Three artifacts must stay current. Update them in the same session as
the spec, not later:

1. **`CHANGELOG.md`** — append under `[Unreleased] — X.Y.Z-dev`. New
   subsection per spec under `### Added` / `### Changed` / `### Fixed` /
   `### Refactored`. Tone: factual, names files, references the spec ID.

2. **`HANDOFF_LOG.md`** — append a paragraph to the active holder's
   entry under `### Update — <spec-id> done same session`. Then advance
   the `### Pick up here` block to point at the next spec.

3. **`HANDOFF_STATE.json`** — bump `release_date`, `last_handler.handed_off_at`,
   `tests.passing/total`. Remove the just-completed spec from
   `open_tasks`. Append to `recent_changes_summary`. Update
   `pick_up_here` text.

If the spec adds tools, also update tool counts in:
- `README.md` (the "All N tools" phrase in the pricing tier block)
- `INSTALL.md` (the same phrase in the Full Path row)

The authoritative count comes from `system_check_tools` against the
live MCP — verify after Cowork restart.

## Versioning

`_version.py` is the single source of truth. `pyproject.toml` is
hand-synced (pip reads it before `_version.py` is importable).

- **Stable releases** (Fridays only): `VERSION="X.Y.Z"`,
  `CHANNEL="stable"`. Tag on GitHub. Cut tarball via `make handoff`.
- **Dev builds** (between releases): `VERSION="X.Y.Z-dev"`,
  `CHANNEL="dev"`. No tag. Tarball carries the `-dev` suffix.

After a stable cut, immediately bump to the next `X.Y.Z-dev` so the
working state is clearly distinguishable from the release.

## Testing

### Default suite

```
make test-fast      # quiet + 5s per-test timeout, 3.5s total
make test           # verbose
make test-network   # only the @pytest.mark.network tests (live APIs)
```

The default suite excludes `@pytest.mark.network` via
`addopts = "-m 'not network'"` in `pyproject.toml`. Tests that hit live
Google/Maps/Anthropic APIs must be marked, otherwise they'll hang the
default run.

### New tool test scaffold

Every new `tools/<name>.py` gets a paired `tests/test_<name>_tools.py`
following this pattern:

```python
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock
import pytest
from googleapiclient.errors import HttpError
from pydantic import ValidationError

from tools import <name> as t_<name>
from tools.<name> import <Input1>, <Input2>, ...


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn

def _run(name, params):
    return asyncio.run(_resolve(name)(params))

def _http_error():
    return HttpError(MagicMock(status=500, reason="boom"),
                     b'{"error": {"message": "boom"}}')

def _err_assert(out):
    assert isinstance(out, str)
    assert ("error" in out.lower() or "failed" in out.lower()
            or "boom" in out.lower() or "http" in out.lower())


# Per-tool: input validation + happy path (mocked) + error path
def test_<tool>_input_requires_<field>():
    with pytest.raises(ValidationError):
        <Input>()
    <Input>(<field>="x")

def test_<tool>_happy(monkeypatch):
    fake = MagicMock()
    fake.<api>.return_value.<method>.return_value.execute.return_value = {...}
    monkeypatch.setattr(t_<name>, "_service", lambda: fake)
    out = _run("<tool>", <Input>(...))
    ...

def test_<tool>_error(monkeypatch):
    fake = MagicMock()
    fake.<api>.return_value.<method>.return_value.execute.side_effect = _http_error()
    monkeypatch.setattr(t_<name>, "_service", lambda: fake)
    _err_assert(_run("<tool>", <Input>(...)))


# Bottom of file: registration smoke
def test_all_<name>_tools_registered():
    from server import mcp
    expected = {<set of names>}
    actual = {n for n in mcp._tool_manager._tools if n.startswith("<prefix>_")}
    assert expected.issubset(actual)
```

### When tools share state

If multiple tests touch the same JSON store (`awaiting_info.json`,
`review_queue.json`, `vendor_response_history.json`, etc.), use an
isolation fixture:

```python
@pytest.fixture
def isolated_stores(tmp_path):
    vf._override_path_for_tests(tmp_path / "awaiting_info.json")
    rq._override_path_for_tests(tmp_path / "review_queue.json")
    yield vf, rq
    project_root = Path(__file__).resolve().parent.parent
    vf._override_path_for_tests(project_root / "awaiting_info.json")
    rq._override_path_for_tests(project_root / "review_queue.json")
```

The teardown matters — without it, tests leak into the user's real store.

### When LLM affects determinism

Tests that exercise code with an LLM branch should mock
`llm.is_available` to force the deterministic fallback:

```python
@pytest.fixture(autouse=True)
def _mock_llm_unavailable(monkeypatch):
    import llm as _llm
    monkeypatch.setattr(_llm, "is_available", lambda: (False, "mocked"))
    yield
```

`tests/test_project_invoices_tools.py` does this autouse for the entire
file. If you need to test the LLM branch in a specific test, override
the fixture with your own `with patch(...)` for that test only.

## Adding a new tool

1. Decide which `tools/<name>.py` it belongs to. Most additions are
   composed workflows in `workflows_{gmail,crm,calendar,chat,misc}.py`,
   not in the underlying `gmail.py`/`calendar.py`/etc.
2. Add the input model class at the top of the file (Pydantic v2,
   `ConfigDict(str_strip_whitespace=True, extra="forbid")`).
3. Register the tool inside `register(mcp)` with a `@mcp.tool(name=...,
   annotations={...})` decorator. Annotations include
   `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
4. Body: `try ... except Exception as e: return format_error("<tool>", e)`.
5. Add tests following the scaffold above.
6. If the tool joins an existing helper (`_vf`, `_rq`, `_vrh_history`,
   etc.), reuse the helpers — don't reinvent the JSON store.

## Helpers / shared modules

| Module | What it owns |
|---|---|
| `vendor_followups.py` | `awaiting_info.json` — outstanding vendor info requests |
| `review_queue.py` | `review_queue.json` — medium-confidence parsed replies |
| `vendor_response_history.py` | `vendor_response_history.json` — per-vendor reply latency rolling window |
| `project_registry.py` | `projects.json` — project codes + routing rules |
| `merchant_cache.py` | `merchants.json` — receipt extractor merchant memory |
| `tools/_workflow_helpers.py` | Cross-cutting workflow helpers (geocode cache, contact walker, address parsers) shared across the 5 `workflows_*.py` modules |

All JSON stores use the same atomic-write pattern:
- `_load()` reads → returns empty dict on missing or unparseable file
- `_save(data)` writes via `tempfile.mkstemp` + `os.replace`
- `_override_path_for_tests(path)` reroutes to a tmp file (used by fixtures)

## Type checking

`mypy` is configured with a soft baseline in `pyproject.toml`:

```
make typecheck         # soft config (current default)
make typecheck-strict  # --strict --ignore-missing-imports
```

New modules should be type-clean. Existing files have ~150 errors as of
the P2-2 baseline; the cleanup is incremental — touch as you go. To
opt a module into strict mode, uncomment the `[[tool.mypy.overrides]]`
block at the bottom of `pyproject.toml` and add the module name.

## Exception handling

After the P1-3 audit (`p1-3-exception-audit-2026-04-30.md`), the
established pattern for new code:

```python
# Tool-boundary handlers (the @mcp.tool body)
try:
    ...
except SpecificError as e:
    return format_error(e)        # narrow, expected
except Exception as e:
    log.error("<tool>: unexpected: %s", e)
    return format_error(e)        # broad safety net, with log

# Library helpers with fallback chains
try:
    ...
except (ExpectedTypeA, ExpectedTypeB) as e:
    log.debug("primary failed, falling back: %s", e)
    # fallback chain continues

# Import-time fallbacks (optional dep)
try:
    import optional_lib
except ImportError:
    optional_lib = None
```

Avoid `except Exception: pass` with no log unless the suppressed failure
is genuinely fine (e.g. a Tier 3 fallback in a chain). When in doubt,
add `log.debug(...)` so the failure is visible to anyone investigating.

## Releases

```
make handoff       # build the dist/<archive>.tar.gz with current code
make bump VERSION=X.Y.Z   # sync _version.py + pyproject.toml
```

Friday stable cut:
1. `make bump VERSION=X.Y.Z`
2. Set `CHANNEL="stable"` in `_version.py`
3. Move `[Unreleased]` content under a dated `[X.Y.Z]` heading in `CHANGELOG.md`
4. `git tag vX.Y.Z` + push
5. `make handoff`
6. Bump immediately to next `X.Y.Z-dev` so dev builds are distinguishable

## When you're stuck

- Read `HANDOFF_LOG.md` latest entry — has the previous holder's
  pick-up-here pointer.
- Read `mcp-audit-YYYY-MM-DD.md` — strategic backlog + priorities.
- Read `mcp-design-docs-YYYY-MM-DD.md` — concrete specs ready to build.
- Run `system_doctor` against the live MCP. Most install drift surfaces
  here.
- Run `make test-fast`. Should be <5s, 0 failures. Anything else is a
  regression worth investigating before adding new code.
