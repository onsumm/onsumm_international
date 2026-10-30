/**
 * Trackpad pinch-to-zoom for a react-pdf page.
 *
 * Chrome, Edge and Firefox report a pinch as wheel events with ctrlKey set;
 * Safari sends its own gesturestart/gesturechange. Re-rendering the PDF canvas
 * on every event would stutter, so while the fingers are moving the page is
 * only scaled with a CSS transform anchored at the pointer.
 *
 * When the gesture goes quiet the new width is committed. react-pdf resizes and
 * redraws its canvas asynchronously, and the canvas is blank in between, so:
 *   1. a snapshot of the scaled page is laid over the scroller, exactly where it
 *      sits on screen, and the real page is hidden;
 *   2. once react-pdf reports the new render (onRenderSuccess), the page is
 *      shown at its real size and scrolled so the point that was under the
 *      fingers is under them again, and the snapshot is removed.
 * Doing the scroll any earlier fails: until the canvas has grown there is
 * nothing to scroll into, and the zoom ends up anchored at the top-left corner.
 */
import { useCallback, useEffect, useRef } from 'react'

const SETTLE_MS = 160
const MIN_W = 200
const MAX_W = 4000
/** If react-pdf never reports back, don't leave the page hidden. */
const RENDER_TIMEOUT_MS = 2000

type SafariGestureEvent = UIEvent & { scale: number; clientX: number; clientY: number }

type Gesture = {
  page: HTMLElement
  scale: number
  /** Pointer position in the page's own (unscaled) coordinates. */
  px: number; py: number
  /** Pointer position on screen. */
  vx: number; vy: number
}

type Pending = Gesture & { snapshot: HTMLElement | null; timeout: number }

/** A frozen copy of the page as it looks now, laid over the scroller. */
function snapshotOver(scroller: HTMLElement, page: HTMLElement): HTMLElement | null {
  const src = page.querySelector('canvas')
  if (!src) return null
  const clip = scroller.getBoundingClientRect()
  const r = page.getBoundingClientRect()

  const wrap = document.createElement('div')
  Object.assign(wrap.style, {
    position: 'fixed', left: `${clip.left}px`, top: `${clip.top}px`,
    width: `${clip.width}px`, height: `${clip.height}px`,
    overflow: 'hidden', pointerEvents: 'none', zIndex: '10001',
  })
  const copy = document.createElement('canvas')
  copy.width = src.width
  copy.height = src.height
  copy.getContext('2d')?.drawImage(src, 0, 0)
  Object.assign(copy.style, {
    position: 'absolute', left: `${r.left - clip.left}px`, top: `${r.top - clip.top}px`,
    width: `${r.width}px`, height: `${r.height}px`,
  })
  wrap.appendChild(copy)
  document.body.appendChild(wrap)
  return wrap
}

export function usePinchZoom(
  scrollerRef: React.RefObject<HTMLElement>,
  width: number,
  onWidth: (w: number) => void,
  /** Re-binds when the scroller is swapped out (e.g. a different document). */
  rebindKey?: unknown,
) {
  // Read inside listeners without re-binding them on every render.
  const widthRef = useRef(width)
  widthRef.current = width
  const onWidthRef = useRef(onWidth)
  onWidthRef.current = onWidth

  const gesture = useRef<Gesture | null>(null)
  const pending = useRef<Pending | null>(null)
  const timer = useRef<number | undefined>(undefined)

  /** Show the page at its new size, put the pinch point back, drop the snapshot. */
  const finish = useCallback(() => {
    const p = pending.current, scroller = scrollerRef.current
    if (!p) return
    pending.current = null
    window.clearTimeout(p.timeout)
    p.page.style.transform = ''
    p.page.style.transformOrigin = ''
    p.page.style.willChange = ''
    p.page.style.visibility = ''
    if (scroller) {
      const r = p.page.getBoundingClientRect()
      scroller.scrollLeft += r.left + p.px * p.scale - p.vx
      scroller.scrollTop += r.top + p.py * p.scale - p.vy
    }
    p.snapshot?.remove()
  }, [scrollerRef])

  useEffect(() => {
    const scroller = scrollerRef.current
    if (!scroller) return
    let safariStart = 1

    const begin = (clientX: number, clientY: number) => {
      if (gesture.current) return gesture.current
      if (pending.current) return null      // still redrawing the last pinch
      const page = scroller.querySelector<HTMLElement>('.react-pdf__Page')
      if (!page) return null
      const r = page.getBoundingClientRect()
      const px = clientX - r.left, py = clientY - r.top
      page.style.transformOrigin = `${px}px ${py}px`
      page.style.willChange = 'transform'
      gesture.current = { page, scale: 1, px, py, vx: clientX, vy: clientY }
      return gesture.current
    }

    const apply = (scale: number) => {
      const g = gesture.current; if (!g) return
      const w = widthRef.current
      g.scale = Math.min(MAX_W / w, Math.max(MIN_W / w, scale))
      g.page.style.transform = `scale(${g.scale})`
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(settle, SETTLE_MS)
    }

    const settle = () => {
      const g = gesture.current; if (!g) return
      gesture.current = null
      const next = Math.round(widthRef.current * g.scale)
      if (next === widthRef.current) {
        g.page.style.transform = ''
        g.page.style.transformOrigin = ''
        g.page.style.willChange = ''
        return
      }
      const snapshot = snapshotOver(scroller, g.page)
      if (snapshot) g.page.style.visibility = 'hidden'
      pending.current = { ...g, snapshot, timeout: window.setTimeout(finish, RENDER_TIMEOUT_MS) }
      onWidthRef.current(next)
    }

    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return            // ordinary scrolling stays ordinary
      e.preventDefault()                // and the browser doesn't zoom the whole app
      const g = begin(e.clientX, e.clientY); if (!g) return
      apply(g.scale * Math.exp(-e.deltaY * 0.01))
    }

    const onGestureStart = (e: Event) => {
      const ge = e as SafariGestureEvent
      e.preventDefault()
      const g = begin(ge.clientX, ge.clientY); if (!g) return
      safariStart = g.scale
    }
    const onGestureChange = (e: Event) => {
      e.preventDefault()
      if (!gesture.current) return
      apply(safariStart * (e as SafariGestureEvent).scale)
    }

    scroller.addEventListener('wheel', onWheel, { passive: false })
    scroller.addEventListener('gesturestart', onGestureStart)
    scroller.addEventListener('gesturechange', onGestureChange)
    return () => {
      window.clearTimeout(timer.current)
      scroller.removeEventListener('wheel', onWheel)
      scroller.removeEventListener('gesturestart', onGestureStart)
      scroller.removeEventListener('gesturechange', onGestureChange)
      finish()
    }
  }, [scrollerRef, rebindKey, finish])

  /** Pass to the react-pdf <Page>'s onRenderSuccess. */
  return finish
}
