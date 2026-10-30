import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, waitFor } from '@testing-library/react'
import { GridGlow } from './GridGlow'
import { approach, distanceToCell, gridSize, liftLevel } from './gridGeometry'

const CELL = 76
const BOX = { width: CELL * 5, height: CELL * 3 } // 5 × 3 cells

let originalRect: () => DOMRect

beforeEach(() => {
  // jsdom performs no layout, so the component would measure 0×0 and render no
  // cells at all. Give it a fixed box.
  originalRect = HTMLElement.prototype.getBoundingClientRect
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { ...BOX, top: 0, left: 0, right: BOX.width, bottom: BOX.height, x: 0, y: 0, toJSON: () => {} } as DOMRect
  }
})

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = originalRect
})

function mount(props: Parameters<typeof GridGlow>[0] = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const utils = render(<GridGlow cell={CELL} {...props} />, { container: host })
  const layer = host.querySelector('.gg') as HTMLElement
  const cellAt = (i: number) => layer.querySelectorAll('.gg__cell')[i] as HTMLElement
  return {
    host,
    layer,
    cells: () => Array.from(layer.querySelectorAll('.gg__cell')) as HTMLElement[],
    /** The smoothed 0…1 level the component last painted onto a cell. */
    level: (i: number) => Number(cellAt(i).style.getPropertyValue('--gg-l') || 0),
    opacityOf: (i: number) => Number(cellAt(i).style.opacity || 0),
    transformOf: (i: number) => cellAt(i).style.transform,
    ...utils,
  }
}

/**
 * jsdom implements no PointerEvent, so RTL's fireEvent.pointerMove dispatches a
 * bare Event and silently drops clientX/clientY. A MouseEvent under the
 * pointermove type carries the coordinates the handler actually reads.
 */
function pointerMove(target: HTMLElement, clientX: number, clientY: number) {
  fireEvent(target, new MouseEvent('pointermove', { clientX, clientY, bubbles: true }))
}

describe('light falloff', () => {
  it('peaks under the pointer and reaches zero at the edge of its reach', () => {
    expect(liftLevel(0, 3)).toBe(1)
    expect(liftLevel(3, 3)).toBe(0)
    expect(liftLevel(9, 3)).toBe(0)
  })

  it('eases in and out, so there is no rim at the edge and no spike at the centre', () => {
    // Smoothstep: symmetric around the midpoint, flat derivative at both ends.
    expect(liftLevel(1.5, 3)).toBeCloseTo(0.5, 5)
    // Near the rim the level is tiny — the field fades out rather than stopping.
    expect(liftLevel(2.9, 3)).toBeLessThan(0.01)
    // …and near the centre it is nearly full, without a hard peak.
    expect(liftLevel(0.1, 3)).toBeGreaterThan(0.99)
  })

  it('measures distance from the cell centre, in cell widths', () => {
    expect(distanceToCell(150, 150, 1, 1, 100)).toBe(0)
    expect(distanceToCell(150, 150, 2, 1, 100)).toBe(1)
  })

  it('rounds the grid up so the last partial row and column still exist', () => {
    expect(gridSize(300, 200, 100)).toEqual({ cols: 3, rows: 2 })
    expect(gridSize(301, 201, 100)).toEqual({ cols: 4, rows: 3 })
    expect(gridSize(0, 0, 100)).toEqual({ cols: 0, rows: 0 })
  })
})

describe('smoothing', () => {
  it('moves toward the target without overshooting it', () => {
    const step = approach(0, 1, 14, 1 / 60)
    expect(step).toBeGreaterThan(0)
    expect(step).toBeLessThan(1)
  })

  it('is frame-rate independent: the same elapsed time travels the same distance', () => {
    // One 1/30s step must land where two 1/60s steps do, or the animation runs
    // at double speed on a 120Hz display.
    const oneBig = approach(0, 1, 14, 1 / 30)
    const twoSmall = approach(approach(0, 1, 14, 1 / 60), 1, 14, 1 / 60)
    expect(oneBig).toBeCloseTo(twoSmall, 6)
  })

  it('clamps a long frame gap so a backgrounded tab does not snap on return', () => {
    expect(approach(0, 1, 14, 10)).toBeCloseTo(approach(0, 1, 14, 0.05), 6)
  })
})

describe('grid glow', () => {
  it('is hidden from assistive technology', () => {
    expect(mount().layer).toHaveAttribute('aria-hidden', 'true')
  })

  it('lights the rules and washes the surface with masked layers, not per-cell colour', () => {
    const { layer } = mount()
    expect(layer.querySelectorAll('.gg__lit')).toHaveLength(1)
    expect(layer.querySelectorAll('.gg__wash')).toHaveLength(1)
  })

  it('builds a cell for every square in the measured box', () => {
    expect(mount().cells()).toHaveLength(15) // 5 × 3
  })

  it('starts with every cell dark and flat', () => {
    const { cells } = mount()
    expect(cells().every(c => !c.style.opacity && !c.style.transform)).toBe(true)
  })

  it('animates a cell up rather than snapping it', async () => {
    const { host, level } = mount()
    pointerMove(host, CELL * 2.5, CELL * 1.5) // centre of cell (2,1) → index 7
    // First frame is a step toward the target, not the target itself.
    await waitFor(() => expect(level(7)).toBeGreaterThan(0), { timeout: 4000 })
    expect(level(7)).toBeLessThan(1)
    // …and it keeps climbing on later frames.
    await waitFor(() => expect(level(7)).toBeGreaterThan(0.8), { timeout: 4000 })
  })

  it('lifts and fades each cell by its own level', async () => {
    const { host, level, opacityOf, transformOf } = mount()
    pointerMove(host, CELL * 2.5, CELL * 1.5)
    await waitFor(() => expect(level(7)).toBeGreaterThan(0.8), { timeout: 4000 })
    // Only composited properties are touched — no box-shadow interpolation.
    expect(opacityOf(7)).toBeGreaterThan(0)
    expect(transformOf(7)).toMatch(/translate3d\(0, -\d/)
    expect(transformOf(7)).toMatch(/scale\(1\.0/)
  })

  it('gives the pointer’s own cell more light than its neighbour', async () => {
    const { host, level } = mount()
    pointerMove(host, CELL * 2.5, CELL * 1.5)
    await waitFor(() => expect(level(7)).toBeGreaterThan(0.8), { timeout: 4000 })
    expect(level(6)).toBeGreaterThan(0)
    expect(level(6)).toBeLessThan(level(7))
  })

  it('settles the cell it leaves while raising the one it enters', async () => {
    const { host, level } = mount()
    pointerMove(host, CELL * 0.5, CELL * 0.5)
    await waitFor(() => expect(level(0)).toBeGreaterThan(0.8), { timeout: 4000 })

    pointerMove(host, CELL * 4.5, CELL * 2.5) // far corner, out of reach
    await waitFor(() => expect(level(14)).toBeGreaterThan(0.8), { timeout: 4000 })
    // The one left behind animates down instead of hanging in the air.
    await waitFor(() => expect(level(0)).toBe(0), { timeout: 4000 })
  })

  it('drops every lit cell when the pointer leaves the surface', async () => {
    const { host, layer, level, cells } = mount()
    pointerMove(host, CELL * 2.5, CELL * 1.5)
    await waitFor(() => expect(level(7)).toBeGreaterThan(0.8), { timeout: 4000 })

    fireEvent.pointerLeave(host)
    expect(layer.dataset.lit).toBeUndefined()
    await waitFor(() => expect(cells().every(c => Number(c.style.opacity || 0) === 0)).toBe(true), { timeout: 4000 })
  })

  it('moves the lamp to the pointer', async () => {
    const { host, layer } = mount()
    pointerMove(host, 120, 80)
    await waitFor(() => expect(layer.dataset.lit).toBe('on'))
    expect(layer.style.getPropertyValue('--gg-x')).toBe('120px')
    expect(layer.style.getPropertyValue('--gg-y')).toBe('80px')
  })

  it('derives the light’s reach from grid spacing and radius', () => {
    const { layer } = mount({ cell: 64, radius: 3 })
    expect(layer.style.getPropertyValue('--gg-cell')).toBe('64px')
    expect(layer.style.getPropertyValue('--gg-reach')).toBe('192px')
  })
})
