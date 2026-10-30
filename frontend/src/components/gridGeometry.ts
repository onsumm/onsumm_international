/**
 * Geometry and easing for the GridGlow background layer.
 *
 * Separate from the component so the maths is unit-testable, and so that module
 * exports only components (fast refresh stops working otherwise).
 */

/** Top-left corner of the cell containing (x, y). Negative coords floor correctly. */
export function cellOrigin(x: number, y: number, cell: number): { cx: number; cy: number } {
  return { cx: Math.floor(x / cell) * cell, cy: Math.floor(y / cell) * cell }
}

/**
 * How lit a cell is, 0…1, from its distance to the pointer in cell widths.
 *
 * Smoothstep rather than linear or squared: the derivative is zero at both ends,
 * so the field has no hard rim at the edge of its reach and no sharp peak under
 * the cursor. That is what makes it read as a pool of light rather than a
 * cursor-shaped decal.
 */
export function liftLevel(distanceInCells: number, radiusInCells: number): number {
  if (radiusInCells <= 0) return 0
  const t = 1 - Math.min(1, Math.max(0, distanceInCells / radiusInCells))
  return t * t * (3 - 2 * t)
}

/** Distance, in cell widths, from a point to the centre of cell (col, row). */
export function distanceToCell(
  px: number, py: number, col: number, row: number, cell: number,
): number {
  const dx = (col + 0.5) * cell - px
  const dy = (row + 0.5) * cell - py
  return Math.hypot(dx, dy) / cell
}

/** Grid dimensions needed to cover a box, rounded up so the edges are filled. */
export function gridSize(width: number, height: number, cell: number): { cols: number; rows: number } {
  return {
    cols: Math.max(0, Math.ceil(width / cell)),
    rows: Math.max(0, Math.ceil(height / cell)),
  }
}

/**
 * One step of critically-damped-ish smoothing toward a target.
 *
 * The motion is smoothed here, in JS, precisely because the target moves every
 * frame. A CSS transition on a value that is rewritten each frame restarts its
 * interpolation continuously, which is what made the first version wobble.
 * `rate` is per-second so the feel is frame-rate independent — the same on 60Hz
 * and 120Hz, where a fixed per-frame factor would be twice as fast.
 */
export function approach(current: number, target: number, rate: number, dtSeconds: number): number {
  const k = 1 - Math.exp(-rate * Math.min(dtSeconds, 0.05))
  return current + (target - current) * k
}
