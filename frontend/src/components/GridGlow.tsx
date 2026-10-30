/**
 * A quiet grid that catches light where the pointer goes.
 *
 * Design intent, after two versions that got it wrong:
 *
 *  - The background must lose to the content. A mid-grey field with 3px rules
 *    made the decoration heavier than the white card in front of it. So: a
 *    near-white canvas, 1px hairlines at ~6% ink — structure you notice only
 *    once you look for it.
 *
 *  - Light gains colour as it gains intensity. Faint light is nearly colourless
 *    and only the core carries real chroma, which is why the previous
 *    uniformly-blue lines read as paint. Chroma here scales with level.
 *
 *  - Motion is smoothed in JS, not by CSS transitions. The lift target changes
 *    every frame; a CSS transition would restart its interpolation every frame,
 *    which is exactly what made it jitter. Each frame writes an eased value, and
 *    only `transform`/`opacity` are touched — both composited, neither repaints.
 *
 * Cost: one rAF loop that runs only while something is still moving, writing two
 * properties per affected cell (a ~7×7 window). Nothing is re-laid-out.
 *
 * Decorative: aria-hidden, and it never takes pointer events, so nothing behind
 * it loses a click.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { approach, cellOrigin, distanceToCell, gridSize, liftLevel } from './gridGeometry'
import './gridglow.css'

export interface GridGlowProps {
  /** Grid spacing in px. */
  cell?: number
  /** How far the light reaches, in cell widths. */
  radius?: number
  /** Peak lift in px for the cell directly under the pointer. */
  lift?: number
}

/** Below this a cell is visually flat, so it can be dropped from the loop. */
const EPSILON = 0.004
/** Smoothing rate, per second. Higher follows the cursor more tightly. */
const RISE_RATE = 14
const FALL_RATE = 7

export function GridGlow({ cell = 56, radius = 3, lift = 6 }: GridGlowProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ cols: 0, rows: 0 })

  const pointer = useRef({ x: 0, y: 0, active: false })
  const levels = useRef(new Map<number, number>())
  const frame = useRef(0)
  const last = useRef(0)

  // The grid covers the container, and the container is the page.
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const measure = () => {
      const r = root.getBoundingClientRect()
      setSize(prev => {
        const next = gridSize(r.width, r.height, cell)
        return next.cols === prev.cols && next.rows === prev.rows ? prev : next
      })
    }
    measure()
    // Guarded: an environment without ResizeObserver still gets the measurement
    // above rather than throwing on mount.
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(root)
    return () => ro.disconnect()
  }, [cell])

  useEffect(() => {
    const root = rootRef.current
    if (!root || !size.cols) return

    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const paint = (el: HTMLElement, level: number) => {
      if (level < EPSILON) {
        el.style.opacity = '0'
        el.style.transform = ''
        // Reset the level too, or the ring keeps a sliver of blue on a cell
        // that is otherwise fully dark.
        el.style.setProperty('--gg-l', '0')
        return
      }
      // Chroma tracks intensity: the ring only shows up once a cell is properly
      // lit, so the faint outer cells stay neutral.
      el.style.opacity = String(level * 0.9)
      el.style.transform = `translate3d(0, ${(-lift * level).toFixed(2)}px, 0) scale(${(1 + level * 0.02).toFixed(4)})`
      el.style.setProperty('--gg-l', level.toFixed(3))
    }

    const tick = (now: number) => {
      frame.current = 0
      const grid = gridRef.current
      if (!grid) return
      const dt = last.current ? (now - last.current) / 1000 : 1 / 60
      last.current = now

      const { x, y, active } = pointer.current
      const span = Math.ceil(radius)
      const targets = new Map<number, number>()

      if (active) {
        const { cx, cy } = cellOrigin(x, y, cell)
        const col0 = cx / cell
        const row0 = cy / cell
        // Only the window around the pointer is considered — never the whole grid.
        for (let row = row0 - span; row <= row0 + span; row++) {
          if (row < 0 || row >= size.rows) continue
          for (let col = col0 - span; col <= col0 + span; col++) {
            if (col < 0 || col >= size.cols) continue
            const level = liftLevel(distanceToCell(x, y, col, row, cell), radius)
            if (level >= EPSILON) targets.set(row * size.cols + col, level)
          }
        }
      }

      // Every cell that is lit or wants to be gets one eased step. Cells the
      // light has left keep animating down instead of snapping flat.
      const indices = new Set([...levels.current.keys(), ...targets.keys()])
      let moving = false
      for (const i of indices) {
        const target = targets.get(i) ?? 0
        const current = levels.current.get(i) ?? 0
        const next = reduced
          ? target
          : approach(current, target, target > current ? RISE_RATE : FALL_RATE, dt)
        const el = grid.children[i] as HTMLElement | undefined
        if (Math.abs(next - target) < EPSILON && target < EPSILON) {
          levels.current.delete(i)
          if (el) paint(el, 0)
          continue
        }
        levels.current.set(i, next)
        if (el) paint(el, next)
        if (Math.abs(next - target) > EPSILON) moving = true
      }

      // Idle when nothing is in motion: no loop running behind a still cursor.
      if (moving || (active && targets.size)) frame.current = requestAnimationFrame(tick)
      else last.current = 0
    }

    const kick = () => {
      if (!frame.current) frame.current = requestAnimationFrame(tick)
    }

    const onMove = (e: PointerEvent) => {
      const r = root.getBoundingClientRect()
      pointer.current = { x: e.clientX - r.left, y: e.clientY - r.top, active: true }
      root.style.setProperty('--gg-x', `${pointer.current.x}px`)
      root.style.setProperty('--gg-y', `${pointer.current.y}px`)
      root.dataset.lit = 'on'
      kick()
    }
    const onLeave = () => {
      pointer.current.active = false
      delete root.dataset.lit
      kick()
    }

    // Listen on the parent: this layer is pointer-events:none, so it never sees
    // events itself, and the parent is what the user is actually moving over.
    const target = root.parentElement ?? root
    target.addEventListener('pointermove', onMove)
    target.addEventListener('pointerleave', onLeave)
    return () => {
      target.removeEventListener('pointermove', onMove)
      target.removeEventListener('pointerleave', onLeave)
      if (frame.current) cancelAnimationFrame(frame.current)
      frame.current = 0
      last.current = 0
    }
  }, [cell, radius, lift, size.cols, size.rows])

  const cells = useMemo(
    () => Array.from({ length: size.cols * size.rows }, (_, i) => i),
    [size.cols, size.rows],
  )

  return (
    <div
      ref={rootRef}
      className="gg"
      aria-hidden="true"
      style={{ ['--gg-cell' as string]: `${cell}px`, ['--gg-reach' as string]: `${cell * radius}px` }}
    >
      <span className="gg__wash" />
      <span className="gg__lit" />
      <div ref={gridRef} className="gg__cells" style={{ ['--gg-cols' as string]: size.cols }}>
        {cells.map(i => (
          <span key={i} className="gg__cell" />
        ))}
      </div>
    </div>
  )
}
