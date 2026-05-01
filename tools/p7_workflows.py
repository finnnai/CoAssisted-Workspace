# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for P7 — Knowledge layer (2 workflows)."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import p7_workflows as p7
from errors import format_error
from logging_util import log


# Module-level cached index — re-built when the wiki rebuild tool is called.
_INDEX: Optional[p7.WikiIndex] = None


class WikiBuildInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    threads: list[dict] = Field(
        ...,
        description=("Threads to index. Each: {id, subject, body, "
                     "timestamp (optional), link (optional)}."),
    )


class WikiSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(...)
    limit: int = Field(default=10, ge=1, le=50)
    snippet_chars: int = Field(default=220, ge=50, le=2000)


class DocDiffInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    before: str = Field(...)
    after: str = Field(...)
    ignore_whitespace: bool = Field(default=True)


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_wiki_rebuild",
        annotations={"title": "Rebuild the personal wiki index from threads",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_wiki_rebuild(params: WikiBuildInput) -> str:
        """Build/replace the in-memory wiki index. Caller pre-fetches threads
        (via Gmail or whatever source). Index lives in process memory until
        the next rebuild."""
        try:
            global _INDEX
            _INDEX = p7.build_wiki_index(params.threads)
            log.info("wiki_rebuild: %d threads, %d terms",
                     _INDEX.total_threads, len(_INDEX.postings))
            return json.dumps(_INDEX.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_wiki_rebuild", e)

    @mcp.tool(
        name="workflow_wiki_search",
        annotations={"title": "Search the personal wiki",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_wiki_search(params: WikiSearchInput) -> str:
        """TF-IDF search over the cached wiki index. Returns ranked passages
        with citations. Run workflow_wiki_rebuild first."""
        try:
            global _INDEX
            if _INDEX is None or _INDEX.total_threads == 0:
                return json.dumps({
                    "results": [],
                    "note": "Index not built — run workflow_wiki_rebuild first.",
                })
            results = p7.search_wiki(
                _INDEX, params.query,
                limit=params.limit, snippet_chars=params.snippet_chars,
            )
            return json.dumps({
                "query": params.query,
                "result_count": len(results),
                "results": [r.to_dict() for r in results],
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_wiki_search", e)

    @mcp.tool(
        name="workflow_doc_diff",
        annotations={"title": "Plain-English diff between two doc versions",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_doc_diff(params: DocDiffInput) -> str:
        """Compute a structured diff between two doc body versions. Returns
        added/removed/modified lines + summary bullets + severity rating."""
        try:
            d = p7.diff_doc_text(
                params.before, params.after,
                ignore_whitespace=params.ignore_whitespace,
            )
            return json.dumps(d.to_dict(), indent=2, default=str)
        except Exception as e:
            return format_error("workflow_doc_diff", e)
