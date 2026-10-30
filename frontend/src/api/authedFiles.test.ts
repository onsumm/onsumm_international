import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fetchAuthedObjectUrl, fetchAuthedBlob, downloadAuthed, withApiBase, SIGNED_LINK_TYPE } from './authedFiles'

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap(entry => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return /\.(ts|tsx)$/.test(entry) ? [full] : []
  })
}

describe('authenticated file access', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['pdf-bytes']), { status: 200 })))
  })
  afterEach(() => vi.unstubAllGlobals())

  it('requests the resource without putting a token in the URL', async () => {
    await fetchAuthedObjectUrl('/api/docs/abc?company_id=c1')
    const calledWith = vi.mocked(fetch).mock.calls[0][0] as string
    expect(calledWith).toBe('/api/docs/abc?company_id=c1&signed=1')
    expect(calledWith).not.toContain('access_token')
  })

  it('fetches a file in S3 from its signed link, with no credentials', async () => {
    const link = 'https://files.example/invoices/t1/u1/a.pdf?Signature=x'
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url === link
      ? new Response('from-storage', { status: 200 })
      : new Response(JSON.stringify({ url: link, expires_in: 300 }),
          { status: 200, headers: { 'content-type': SIGNED_LINK_TYPE } })))

    const blob = await fetchAuthedBlob('/api/docs/abc')

    expect(await blob.text()).toBe('from-storage')
    const calls = vi.mocked(fetch).mock.calls
    expect(calls[0][0]).toBe('/api/docs/abc?signed=1')
    expect(calls[1][0]).toBe(link)
    expect(calls[1][1]).toMatchObject({ credentials: 'omit', referrerPolicy: 'no-referrer' })
  })

  it('falls back to the API serving the file when storage is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('signed=1')) {
        return new Response(JSON.stringify({ url: 'https://files.example/x' }),
          { status: 200, headers: { 'content-type': SIGNED_LINK_TYPE } })
      }
      if (url.startsWith('https://files.example')) throw new TypeError('Failed to fetch')
      return new Response('from-api', { status: 200 })
    }))

    const blob = await fetchAuthedBlob('/api/bills/1/attachments/2/download?preview=true')

    expect(await blob.text()).toBe('from-api')
    expect(vi.mocked(fetch).mock.calls[2][0]).toBe('/api/bills/1/attachments/2/download?preview=true')
  })

  it('returns an opaque blob URL rather than an API URL', async () => {
    const url = await fetchAuthedObjectUrl('/api/docs/abc')
    expect(url.startsWith('blob:')).toBe(true)
  })

  it('throws on a failed request so callers can show an error state', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 403 })))
    await expect(fetchAuthedObjectUrl('/api/docs/abc')).rejects.toThrow('403')
  })

  it('downloads under the requested filename without exposing a token', async () => {
    const click = vi.fn()
    const orig = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = orig(tag) as HTMLAnchorElement
      if (tag === 'a') el.click = click
      return el
    })

    await downloadAuthed('/api/bills/1/attachments/2', 'Invoice March.pdf')

    expect(click).toHaveBeenCalledOnce()
    const requested = vi.mocked(fetch).mock.calls[0][0] as string
    expect(requested).not.toContain('access_token')
  })
})

/**
 * The Review Hub's document preview showed "the original document could not be
 * loaded" for a bill that rendered fine on the Bills screen. Five of the six
 * places fetching /api/docs/{id} prefixed the API base; DocumentPane did not.
 * The vite dev proxy hides the difference, and a deployed Amplify build answers
 * the unknown path with index.html and a 200 — so the fetch "succeeded", the
 * blob was HTML, and the failure only surfaced when react-pdf refused to parse
 * it, under a message blaming a deleted file.
 */
describe('API paths resolve against the backend, not the page', () => {
  it('leaves an already-absolute URL alone, so existing callers do not double up', () => {
    expect(withApiBase('https://api.onsumm.ai/api/docs/abc'))
      .toBe('https://api.onsumm.ai/api/docs/abc')
    expect(withApiBase('//cdn.example.com/logo.png')).toBe('//cdn.example.com/logo.png')
    expect(withApiBase('blob:http://localhost/xyz')).toBe('blob:http://localhost/xyz')
  })

  it('passes a root-relative path through unchanged when there is no base', () => {
    // VITE_API_URL is empty in dev and in tests; vite proxies /api to :8001.
    expect(withApiBase('/api/docs/abc')).toBe('/api/docs/abc')
  })

  it('refuses an HTML body, however cheerful its status code', async () => {
    // This is what an SPA catch-all rewrite returns: 200, text/html, index.html.
    // Accepting it produced a blob that looked like a file and was not one.
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<!doctype html><html></html>', {
      status: 200,
      headers: { 'content-type': 'text/html; charset=utf-8' },
    })))
    await expect(fetchAuthedObjectUrl('/api/docs/abc')).rejects.toThrow(/HTML page/)
  })

  it('accepts a PDF that arrives with its own content type', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['%PDF-1.4']), {
      status: 200,
      headers: { 'content-type': 'application/pdf' },
    })))
    expect(await fetchAuthedObjectUrl('/api/docs/abc')).toMatch(/^blob:/)
  })
})

/**
 * Every /api/ path handed to fetch() has to resolve against the backend. The
 * codebase re-declares `const API_BASE = import.meta.env.VITE_API_URL` in seven
 * files, which is exactly the kind of convention one call site quietly skips.
 */
describe('no bare /api/docs fetch outside the chokepoint', () => {
  it('routes document fetches through authedFiles', () => {
    const offenders = walk(SRC)
      .filter(f => !f.endsWith('authedFiles.ts') && !f.endsWith('authedFiles.test.ts'))
      .filter(f => {
        const src = readFileSync(f, 'utf8')
        // A template literal opening straight onto /api/ — i.e. no ${API_BASE}
        // in front of it — handed to fetch() rather than to authedFiles.
        return /fetch\(\s*[`'"]\/api\//.test(src)
      })
      .map(f => f.replace(`${SRC}/`, 'src/'))

    expect(offenders).toEqual([])
  })
})

/**
 * Regression guard for the Phase 0.4 security fix. A bearer token in a query
 * string leaks into logs, browser history and Referer headers; if anyone
 * reintroduces the pattern, this fails loudly rather than shipping quietly.
 */
describe('no bearer tokens in URLs (security regression guard)', () => {
  it('has no access_token query parameter anywhere in src/', () => {
    const offenders = walk(SRC)
      .filter(f => !f.endsWith('authedFiles.ts') && !f.endsWith('authedFiles.test.ts'))
      .filter(f => /[?&]access_token=|['"`]access_token['"`]/.test(readFileSync(f, 'utf8')))
      .map(f => f.replace(`${SRC}/`, 'src/'))

    expect(offenders).toEqual([])
  })
})
