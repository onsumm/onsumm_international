#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { chromium } from 'playwright';

const BASE_URL = process.env.LENDIX_DOCS_BASE_URL || 'https://docs.lendix.ai/nova';
const ORIGIN = new URL(BASE_URL).origin;
const MAX_PAGES = 200;

/** In-memory index: key (e.g. "/nova" or "/nova#ekyc") -> { title, text, url } */
const pageIndex = new Map();
let browserPromise = null;

function getBrowser() {
  if (!browserPromise) {
    browserPromise = chromium.launch({ headless: true });
  }
  return browserPromise;
}

/**
 * Lendix docs are a single-page app: the sidebar's #anchor links don't point to
 * real DOM ids and don't navigate — clicking one swaps the content pane's React
 * state client-side. So a plain render-and-slice-by-id approach finds nothing;
 * we have to actually click through each sidebar link and read the content pane
 * after each click.
 */
function extractContentPane(page) {
  return page.evaluate(() => {
    const nav = document.querySelector('nav');
    if (!nav) return document.body.innerText;
    const navWrapper = nav.parentElement;
    const container = navWrapper?.parentElement;
    const contentEl = container ? Array.from(container.children).find((c) => c !== navWrapper) : null;
    return (contentEl || document.body).innerText;
  });
}

async function crawl(startUrl, maxPages = MAX_PAGES) {
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    await page.goto(startUrl, { waitUntil: 'networkidle', timeout: 30000 });
    const title = await page.title();
    const basePath = new URL(startUrl).pathname.replace(/\/+$/, '') || '/';

    const hashLinks = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('nav a[href^="#"]')).map((a) => ({
        id: a.getAttribute('href').slice(1),
        title: a.innerText.trim(),
      }));
    });

    // Whatever section is showing on initial load (Overview) counts as the base page.
    const overviewText = await extractContentPane(page);
    pageIndex.set(basePath, { title, text: overviewText, url: startUrl });

    for (const { id, title: sectionTitle } of hashLinks) {
      if (pageIndex.size >= maxPages) break;
      const key = `${basePath}#${id}`;
      if (pageIndex.has(key)) continue;
      try {
        await page.click(`nav a[href="#${id}"]`, { timeout: 5000 });
        await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(300);
        const text = await extractContentPane(page);
        pageIndex.set(key, { title: sectionTitle || id, text, url: `${startUrl.split('#')[0]}#${id}` });
      } catch (err) {
        pageIndex.set(key, { title: sectionTitle || id, text: `Failed to load: ${err.message}`, url: `${startUrl.split('#')[0]}#${id}` });
      }
    }
  } finally {
    await page.close();
  }
}

async function ensureIndex() {
  if (pageIndex.size === 0) {
    await crawl(BASE_URL);
  }
}

const server = new Server(
  { name: 'lendix-docs-mcp', version: '1.0.0' },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'search_docs',
      description: `Search the Lendix Nova documentation (${BASE_URL}) for a keyword or phrase. Returns matching sections/pages with short snippets.`,
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Keyword or phrase to search for' },
        },
        required: ['query'],
      },
    },
    {
      name: 'get_doc_page',
      description: 'Fetch the full text content of a specific Lendix Nova docs page or section by key (e.g. "/nova" for the overview, or "/nova#ekyc" for the eKYC section). Use list_doc_pages to see valid keys.',
      inputSchema: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Page or section key, e.g. /nova or /nova#esign' },
        },
        required: ['path'],
      },
    },
    {
      name: 'list_doc_pages',
      description: 'List all Lendix Nova documentation pages/sections discovered so far (key + title).',
      inputSchema: { type: 'object', properties: {} },
    },
    {
      name: 'refresh_docs_index',
      description: 'Re-crawl the Lendix Nova documentation site to refresh the cached page/section index.',
      inputSchema: { type: 'object', properties: {} },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  if (name === 'search_docs') {
    await ensureIndex();
    const query = String(args.query || '').toLowerCase();
    const results = [];
    for (const [path, { title, text }] of pageIndex) {
      const lower = text.toLowerCase();
      const idx = lower.indexOf(query);
      if (idx !== -1) {
        const start = Math.max(0, idx - 100);
        const end = Math.min(text.length, idx + query.length + 100);
        results.push({ path, title, snippet: `...${text.slice(start, end).trim()}...` });
      }
    }
    return {
      content: [
        {
          type: 'text',
          text: results.length
            ? JSON.stringify(results, null, 2)
            : `No matches found for "${args.query}".`,
        },
      ],
    };
  }

  if (name === 'get_doc_page') {
    await ensureIndex();
    const key = args.path.startsWith('/') ? args.path : `/${args.path}`;
    if (!pageIndex.has(key)) {
      return {
        content: [
          {
            type: 'text',
            text: `No page/section found for "${key}". Call list_doc_pages to see valid keys.`,
          },
        ],
      };
    }
    const entry = pageIndex.get(key);
    return { content: [{ type: 'text', text: `# ${entry.title}\n\n${entry.text}` }] };
  }

  if (name === 'list_doc_pages') {
    await ensureIndex();
    const list = Array.from(pageIndex.entries()).map(([path, { title }]) => ({ path, title }));
    return { content: [{ type: 'text', text: JSON.stringify(list, null, 2) }] };
  }

  if (name === 'refresh_docs_index') {
    pageIndex.clear();
    await crawl(BASE_URL);
    return { content: [{ type: 'text', text: `Re-crawled ${pageIndex.size} pages/sections from ${BASE_URL}.` }] };
  }

  throw new Error(`Unknown tool: ${name}`);
});

process.on('SIGINT', async () => {
  if (browserPromise) {
    const browser = await browserPromise;
    await browser.close();
  }
  process.exit(0);
});

const transport = new StdioServerTransport();
await server.connect(transport);
