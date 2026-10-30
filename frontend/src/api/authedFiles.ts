import { useEffect, useState } from 'react'

/**
 * Authenticated access to binary API resources (logos, PDFs, attachments).
 *
 * WHY THIS EXISTS
 * ---------------
 * Browser-native GETs — `<img src>`, `<a href download>`, react-pdf's
 * `<Document file>` — cannot set an `Authorization` header. The previous
 * approach appended `?access_token=<JWT>` to the URL, which leaks a
 * full-scope, 60-minute bearer token into:
 *
 *   • browser history and the address bar
 *   • server / CDN / reverse-proxy access logs
 *   • the `Referer` header of any subsequent outbound request
 *   • screenshots and screen shares
 *
 * That is an automatic finding in any pen-test and a blocker in enterprise
 * IT-security review.
 *
 * Instead we fetch the resource with the normal authenticated `fetch` (the
 * global patch in `api/invoices.ts` attaches the bearer header and transparently
 * refreshes on 401), then hand the browser a same-origin `blob:` URL. The token
 * never appears in a URL, and the blob URL is opaque, origin-scoped, and dies
 * with the document.
 */

const API_BASE = (import.meta.env.VITE_API_URL ?? '') as string

/**
 * Resolve a root-relative API path against the backend, not against the page.
 *
 * This lives here rather than at each call site because leaving it to callers
 * is how the Review Hub's document preview broke: five of the six places that
 * fetch `/api/docs/{id}` wrote `${API_BASE}/api/docs/…` and DocumentPane wrote
 * `/api/docs/…`. In dev both work — vite proxies /api to :8001 — so the
 * difference is invisible until a deployed build, where the frontend is on
 * Amplify and the API is on api.onsumm.ai.
 *
 * What made it hard to read as a bug: Amplify's SPA rewrite answers an unknown
 * path with index.html and a **200**, so the fetch succeeded, the blob was
 * HTML, and the failure surfaced two layers later as react-pdf declining to
 * parse it — under a message about the document having been removed. The same
 * trap is documented at the top of api/review.ts, for the same reason.
 *
 * Absolute URLs pass through untouched, so the callers that already prefix
 * keep working exactly as they did.
 */
export function withApiBase(url: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith('//')) return url
  if (!url.startsWith('/')) return url
  return `${API_BASE}${url}`
}

/**
 * The content type the API answers `?signed=1` with when the file is in S3:
 * `{ url, expires_in }`, a link to that one object that works for a few
 * minutes. Documents never have a public URL — the API checks the tenant
 * before it signs, and the bucket refuses anything unsigned.
 */
export const SIGNED_LINK_TYPE = 'application/vnd.onsumm.signed-link+json'

function withSignedParam(url: string): string {
  const [path, hash] = url.split('#')
  return `${path}${path.includes('?') ? '&' : '?'}signed=1${hash !== undefined ? `#${hash}` : ''}`
}

async function readFile(res: Response, url: string): Promise<Blob> {
  if (!res.ok) throw new Error(`Request failed (${res.status})`)
  // A document is never text/html. If one comes back it is a login page, an
  // error page, or an SPA catch-all rewrite that swallowed the request — all
  // of which arrive with a 200 and none of which are the file. Rejecting here
  // turns "this PDF is corrupt" into an honest failure at the point it
  // actually happened.
  const type = res.headers.get('content-type') ?? ''
  if (type.includes('text/html')) {
    throw new Error(`Expected a file, got an HTML page (${res.status}) from ${url}`)
  }
  return res.blob()
}

/**
 * Fetch a protected file. The API is asked for a signed link first, so the
 * bytes come straight from storage rather than through the API server; a file
 * not in S3 is streamed by the API as before. If storage can't be reached the
 * API is asked again for the bytes themselves (it falls back to its backup).
 * The signed link carries no token and is fetched with no credentials.
 */
export async function fetchAuthedBlob(url: string): Promise<Blob> {
  const res = await fetch(withApiBase(withSignedParam(url)))
  if (res.ok && (res.headers.get('content-type') ?? '').includes(SIGNED_LINK_TYPE)) {
    const { url: link } = (await res.json()) as { url: string }
    try {
      const file = await fetch(link, { credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store' })
      if (file.ok) return await file.blob()
    } catch { /* storage unreachable from this browser — fall through */ }
    return readFile(await fetch(withApiBase(url)), url)
  }
  return readFile(res, url)
}

/** Fetch a protected resource and wrap it in an object URL. Caller must revoke. */
export async function fetchAuthedObjectUrl(url: string): Promise<string> {
  return URL.createObjectURL(await fetchAuthedBlob(url))
}

export interface AuthedObjectUrl {
  /** Object URL, or null while loading or on failure. */
  url: string | null
  loading: boolean
  /** True when the fetch failed — render your fallback (placeholder, retry). */
  error: boolean
}

/**
 * Resolve a protected resource to an object URL for the lifetime of the
 * component. Revokes the previous URL whenever `src` changes and on unmount, so
 * long-lived screens (bill lists, brand switchers) do not leak blobs.
 *
 * Pass `null`/`undefined` to skip fetching entirely.
 */
export function useAuthedObjectUrl(src: string | null | undefined): AuthedObjectUrl {
  const [state, setState] = useState<AuthedObjectUrl>({ url: null, loading: !!src, error: false })

  useEffect(() => {
    if (!src) {
      setState({ url: null, loading: false, error: false })
      return
    }

    let cancelled = false
    let objectUrl: string | null = null
    setState({ url: null, loading: true, error: false })

    fetchAuthedObjectUrl(src)
      .then(next => {
        // The effect was torn down mid-flight — release immediately rather than
        // storing a URL nobody will ever revoke.
        if (cancelled) { URL.revokeObjectURL(next); return }
        objectUrl = next
        setState({ url: next, loading: false, error: false })
      })
      .catch(() => {
        if (!cancelled) setState({ url: null, loading: false, error: true })
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [src])

  return state
}

/**
 * Download a protected resource under a chosen filename, without ever putting
 * the token in a URL. Throws on failure so the caller can surface an error.
 */
export async function downloadAuthed(url: string, filename: string): Promise<void> {
  const objectUrl = await fetchAuthedObjectUrl(url)
  try {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // Safari needs the URL to outlive the synchronous click handler.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
  }
}
