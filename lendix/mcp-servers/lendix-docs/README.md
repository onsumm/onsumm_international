# lendix-docs MCP Server

An MCP server that exposes the Lendix Nova API documentation (`https://docs.lendix.ai/nova`) as tools, so it can be searched and read directly from Claude Code instead of manually browsing the docs site.

## Why a custom server (and not a plain web fetch)

`docs.lendix.ai/nova` is a client-rendered single-page app: the sidebar entries (eKYC, eSign, PAN, Penny Drop, ...) aren't separate URLs or real DOM anchors — clicking one swaps content into a pane via client-side React state, with nothing to fetch statically. A plain HTTP fetch or curl only returns an empty page shell. This server uses a headless Chromium browser (Playwright) to load the page and click through every sidebar link, capturing each section's rendered content into an in-memory index.

## Setup

### Prerequisites
- Node.js (v18+)

### Install
```bash
cd mcp-servers/lendix-docs
npm install
```
`npm install` also runs `playwright install chromium` (via `postinstall`), which downloads a headless Chromium build (~300 MB, one-time).

### Register with Claude Code
Already configured in the project's [`.mcp.json`](../../.mcp.json):
```json
{
  "mcpServers": {
    "lendix-docs": {
      "command": "node",
      "args": ["C:\\Users\\Rishit Rastogi\\lendix\\mcp-servers\\lendix-docs\\index.js"],
      "env": { "LENDIX_DOCS_BASE_URL": "https://docs.lendix.ai/nova" }
    }
  }
}
```
MCP servers are loaded at session start, so **reload/restart Claude Code** any time this file or the server script changes, for the changes to take effect.

### Configuration
- `LENDIX_DOCS_BASE_URL` (env var, optional) — override the docs URL to crawl. Defaults to `https://docs.lendix.ai/nova`.

## Tools

### `list_doc_pages()`
Lists every page/section discovered so far (key + title). Triggers a crawl on first call (~60-90s, since it clicks through all sidebar sections one by one); cached in memory after that for the life of the process.
- **Args**: none
- **Returns**: JSON array of `{ path, title }`, e.g. `{"path": "/nova#ekyc", "title": "eKYC"}`

### `get_doc_page({ path })`
Fetches the full text content of one page or section by its key.
- **Args**: `path` (string) — e.g. `"/nova"` for the overview, `"/nova#esign"` for eSign, `"/nova#pan"` for PAN. Use `list_doc_pages` to see valid keys.
- **Returns**: section text — description, supported vendors, request parameters, request/response JSON examples, curl examples.

### `search_docs({ query })`
Full-text search across all indexed sections.
- **Args**: `query` (string) — a keyword or phrase, e.g. `"digilocker"`, `"penny drop"`, `"rate limit"`.
- **Returns**: JSON array of `{ path, title, snippet }` for every section containing a match, with surrounding context.

### `refresh_docs_index()`
Clears the cached index and re-crawls the site from scratch. Use when the docs site has been updated and you want current content.
- **Args**: none
- **Returns**: confirmation message with the number of pages/sections re-indexed.

## Typical usage

Just ask in natural language — the tools are called automatically when relevant, e.g.:
- "What does the eKYC module require?" → `search_docs` or `get_doc_page("/nova#ekyc")`
- "List everything in the Nova docs" → `list_doc_pages()`
- "The docs changed, refresh it" → `refresh_docs_index()`

## Scope note

Only works from Claude Code / Claude Desktop, since it runs as a **local stdio process**. It cannot be used from claude.ai web chat, which only supports remote MCP servers over HTTP/SSE with a public URL.
