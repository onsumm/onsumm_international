/**
 * Where the user last clicked, as an offset from the centre of the screen.
 *
 * The bill card grows out of the row that was clicked (bm-zoom-open reads the
 * offset as --bm-dx/--bm-dy). The Bills page measures the row itself, but a card
 * opened through a URL change — a record card, a Review finding — has no element
 * in hand by the time it mounts. Remembering the last pointer press lets those
 * cards make the same gesture.
 */

let last: { x: number; y: number; at: number } | null = null

if (typeof window !== 'undefined') {
  // Capture phase, so a handler that stops propagation can't hide the press.
  window.addEventListener('pointerdown', e => { last = { x: e.clientX, y: e.clientY, at: Date.now() } }, true)
}

/**
 * `"<dx>px <dy>px"` for a press in the last second, else `null`: a card opened
 * from a pasted link or Back has nothing to grow from and should grow from the
 * centre.
 */
export function clickOrigin(maxAgeMs = 1000): string | null {
  if (!last || Date.now() - last.at > maxAgeMs) return null
  const dx = last.x - window.innerWidth / 2
  const dy = last.y - window.innerHeight / 2
  return `${dx.toFixed(1)}px ${dy.toFixed(1)}px`
}
