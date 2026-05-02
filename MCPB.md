# Building a `.mcpb` desktop extension

CoAssisted Workspace ships a portable [MCPB](https://github.com/modelcontextprotocol/mcpb)
manifest at the repo root so anyone can install it as a one-click Claude
Desktop custom connector.

## Quick install (operator's own machine)

If you've already got the project running locally and just want to wire
it into Claude Desktop:

1. Build the bundle:

   ```bash
   npx @anthropic-ai/mcpb pack
   ```

   This produces `coassisted-workspace.mcpb` in the current directory.

2. Open Claude Desktop → Settings → Connectors → **Add custom desktop
   connector** → choose file → select the `.mcpb`.

3. On first launch you'll be prompted for your `anthropic_api_key`
   (required) and `google_maps_api_key` (optional). They're stored in
   Claude Desktop's secret store, not in `config.json`.

## Two distribution channels

| Channel | What it is | When to use |
|---|---|---|
| **`.mcpb` desktop extension** (this doc) | One-click install in Claude Desktop. Manifest at repo root. | Personal/operator install on a Mac with Python 3.11+ |
| **Plugin marketplace** (`.claude-plugin/`) | `/plugin marketplace add finnnai/CoAssisted-Workspace` from inside Cowork. | Org-wide rollout, version-controlled updates |

You can use both — they're separate channels and don't conflict.

## What the manifest does

`manifest.json` declares:

- The MCP server entry point (`server.py`) and Python runtime
  requirements (`>=3.11`)
- 390 tools across 14 categories (auto-discovered at runtime, so
  `tools_generated: true`)
- Two `user_config` keys collected from the user at install time:
  - `anthropic_api_key` (sensitive, required)
  - `google_maps_api_key` (sensitive, optional)
- Compatibility: macOS + Linux

`mcpb pack` reads this manifest, bundles `server.py` + the rest of the
project source, runs `pip install` against `pyproject.toml`, and ships
the result as `coassisted-workspace.mcpb`.

## Verifying

```bash
npx @anthropic-ai/mcpb validate manifest.json
```

Should print `✓ manifest is valid`.

## Local-only operator manifest

If you've already got the project installed under `~/Claude/google_workspace_mcp/`
and want a `.mcpb` that points at *your existing install path* instead
of the bundled copy (no rebuild on every code change), there's a build
script in this session's notes:
[`build_mcpb.sh`](https://github.com/finnnai/CoAssisted-Workspace/blob/main/scripts/build_mcpb.sh)
(operator-only, not portable across machines).
