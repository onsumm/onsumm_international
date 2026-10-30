import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Pencil, Trash2 } from 'lucide-react'
import { fetchVendorLogos, removeVendorLogo, setVendorLogo, vendorLogoPath } from '../../api/vendorCard'
import { fetchAuthedObjectUrl } from '../../api/authedFiles'

/**
 * Vendor logos, looked up on the server from each vendor's website and shown
 * wherever a vendor's initials were. One list of which vendors have one is
 * shared by every logo on screen; while the server is still looking vendors
 * up, it's asked again every few seconds, so logos appear as they're found.
 */

let versions: Record<string, number> = {}
let started = false
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(l => l())
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

const POLL_MS = 6000
const MAX_POLLS = 10

async function load(polls = 0, lastPending = Infinity): Promise<void> {
  try {
    const { logos, pending } = await fetchVendorLogos()
    versions = logos
    emit()
    // Keep asking while lookups are making progress (or have only just begun).
    if (pending > 0 && polls < MAX_POLLS && (pending < lastPending || polls < 2)) {
      window.setTimeout(() => { void load(polls + 1, pending) }, POLL_MS)
    }
  } catch { /* no logos is fine: initials stay */ }
}

function ensureLoaded() {
  if (started) return
  started = true
  void load()
}

/** The vendor's logo version, or undefined when it has none (yet). */
export function useVendorLogoVersion(supplierId: string | undefined): number | undefined {
  useEffect(ensureLoaded, [])
  return useSyncExternalStore(subscribe, () => (supplierId ? versions[supplierId] : undefined))
}

// Logo images, fetched once per version and kept for the session (a few KB each).
const images = new Map<string, Promise<string | null>>()
const ready = new Map<string, string | null>()

function imageFor(supplierId: string, version: number): Promise<string | null> {
  const key = `${supplierId}:${version}`
  let p = images.get(key)
  if (!p) {
    p = fetchAuthedObjectUrl(vendorLogoPath(supplierId, version)).catch(() => null)
    p.then(u => ready.set(key, u))
    images.set(key, p)
  }
  return p
}

/** The vendor's logo image URL once it's loaded; null until then or when there's none. */
export function useVendorLogo(supplierId: string | undefined): string | null {
  const v = useVendorLogoVersion(supplierId)
  const key = supplierId && v ? `${supplierId}:${v}` : ''
  const [src, setSrc] = useState<string | null>(() => (key ? ready.get(key) ?? null : null))
  useEffect(() => {
    if (!supplierId || !v) { setSrc(null); return }
    let live = true
    const cached = ready.get(`${supplierId}:${v}`)
    if (cached !== undefined) { setSrc(cached); return }
    imageFor(supplierId, v).then(u => { if (live) setSrc(u) })
    return () => { live = false }
  }, [supplierId, v])
  return src
}

/**
 * A vendor's logo in `className`'s frame, or `fallback` (initials) until it
 * loads or when there isn't one. `children` sit on top either way (badges).
 */
export function VendorLogo({ supplierId, className, fallback, children }: {
  supplierId: string; className: string; fallback: ReactNode; children?: ReactNode
}) {
  const src = useVendorLogo(supplierId)
  return (
    <div className={`${className}${src ? ' has-logo' : ''}`}>
      {src ? <img src={src} alt="" className="vlogo__img" draggable={false} /> : fallback}
      {children}
    </div>
  )
}

/** Set the vendor's website to take its logo from, or remove the logo. */
export function LogoEditor({ supplierId, notify }: {
  supplierId: string
  notify?: (msg: string, kind: 'info' | 'error') => void
}) {
  const [open, setOpen] = useState(false)
  const [site, setSite] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const has = !!useVendorLogoVersion(supplierId)
  const ref = useRef<HTMLDivElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  // Drawn at the page root, just under the logo: inside the card header it was
  // clipped by the cards below it.
  const [at, setAt] = useState<{ top: number; left: number } | null>(null)
  const place = () => {
    const r = ref.current?.parentElement?.getBoundingClientRect()
    if (r) setAt({ top: r.bottom + 10, left: Math.max(12, Math.min(r.left, window.innerWidth - 300)) })
  }

  useEffect(() => {
    if (!open) return
    place()
    const close = (e: MouseEvent) => {
      const t = e.target as Node
      if (!ref.current?.contains(t) && !pop.current?.contains(t)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    const move = () => place()
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc, true)
    window.addEventListener('resize', move)
    window.addEventListener('scroll', move, true)
    return () => {
      document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc, true)
      window.removeEventListener('resize', move); window.removeEventListener('scroll', move, true)
    }
  }, [open])

  const save = async () => {
    if (!site.trim()) return
    setBusy(true); setErr('')
    try {
      const r = await setVendorLogo(supplierId, site.trim())
      versions = { ...versions, [supplierId]: r.version }
      emit()
      setOpen(false); setSite('')
      notify?.(`Logo from ${r.domain}`, 'info')
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setErr(typeof d === 'string' ? d : 'Couldn’t get a logo from that website.')
    } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true); setErr('')
    try {
      await removeVendorLogo(supplierId)
      const { [supplierId]: _gone, ...rest } = versions
      versions = rest
      emit()
      setOpen(false)
    } catch { setErr('Couldn’t remove the logo.') } finally { setBusy(false) }
  }

  return (
    <div className="vc__logo-ed" ref={ref}>
      <button className="vc__logo-edit" title={has ? 'Change logo' : 'Add logo'} aria-label={has ? 'Change logo' : 'Add logo'}
        aria-expanded={open} onClick={() => { setOpen(o => !o); setErr('') }}>
        <Pencil size={11} />
      </button>
      {open && at && createPortal(
        <div className="vc__portal"><div className="vc__logo-pop" ref={pop} role="dialog" aria-label="Vendor logo" style={{ top: at.top, left: at.left }}>
          <div className="vc__logo-pop-h">{has ? 'Change logo' : 'Add a logo'}</div>
          <p className="vc__logo-pop-p">The vendor’s website. We’ll use its logo.</p>
          <form className="vc__logo-pop-f" onSubmit={e => { e.preventDefault(); void save() }}>
            {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
            <input autoFocus value={site} onChange={e => { setSite(e.target.value); setErr('') }} placeholder="acme.com" aria-label="Website" disabled={busy} />
            <button className="vc__btn vc__btn--sm vc__btn--primary" disabled={busy || !site.trim()}>{busy ? 'Finding…' : 'Use'}</button>
          </form>
          {err && <div className="vc__err vc__err--inline vc__logo-pop-err">{err}</div>}
          {has && (
            <button className="vc__lnk vc__logo-pop-rm" onClick={() => void remove()} disabled={busy}><Trash2 size={13} />Remove logo, show initials</button>
          )}
        </div></div>,
        document.body,
      )}
    </div>
  )
}

/** Tests only: forget what's loaded. */
export function __resetVendorLogos() {
  versions = {}; started = false; images.clear(); ready.clear(); emit()
}
