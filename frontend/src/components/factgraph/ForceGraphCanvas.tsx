import { forwardRef, useImperativeHandle, useMemo, useRef, useCallback, useEffect } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { forceCollide, forceX, forceY } from 'd3-force'
import { NODE_META, REL_LABELS } from './graphTheme'
import type { NodeType } from '../../api/factGraph'

// react-force-graph mutates node objects in place (adds x/y/vx/vy). These are the
// objects the modal owns; the sim writes coordinates onto them.
export interface SimNode {
  id: string
  type: NodeType
  label: string
  properties?: Record<string, unknown>
  degree?: number
  x?: number
  y?: number
}
export interface SimLink {
  source: string | SimNode
  target: string | SimNode
  relation: string
  weight?: number
}

export interface CanvasHandle {
  zoomToFit: (ms?: number) => void
  centerNode: (id: string) => void
}

interface Props {
  width: number
  height: number
  data: { nodes: SimNode[]; links: SimLink[] }
  highlightNodes: Set<string>
  highlightLinks: Set<SimLink>
  selectedId: string | null
  queryMatch: Set<string>
  dimmed: Set<NodeType>
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
}

const endId = (x: string | SimNode): string => (typeof x === 'object' ? x.id : x)

function radiusOf(n: SimNode): number {
  const base = (NODE_META[n.type] ?? NODE_META.fact).radius
  if (n.type === 'vendor') return base + Math.min(6, (n.degree ?? 0) * 0.5)
  if (n.type === 'tenant') return base
  return base
}

// Link rest-length by relation — spokes short, the tenant hub a bit longer.
function linkDistance(l: SimLink): number {
  switch (l.relation) {
    case 'owns': return 90
    case 'has_bill': return 34
    case 'poc_of': return 40
    default: return 46          // constrains / about (facts)
  }
}

const ForceGraphCanvas = forwardRef<CanvasHandle, Props>(function ForceGraphCanvas(props, ref) {
  const {
    width, height, data, highlightNodes, highlightLinks, selectedId, queryMatch, dimmed,
    onHover, onSelect,
  } = props
  const fgRef = useRef<any>(null)
  const fittedRef = useRef(false)

  useImperativeHandle(ref, () => ({
    zoomToFit: (ms = 500) => fgRef.current?.zoomToFit(ms, 70),
    centerNode: (id: string) => {
      const n = data.nodes.find(x => x.id === id)
      if (n && n.x != null && n.y != null) fgRef.current?.centerAt(n.x, n.y, 600)
    },
  }), [data.nodes])

  // Tune the physics for a compact, readable layout (no hairball, no drifting).
  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    fg.d3Force('charge')?.strength(-140).distanceMax(600)
    fg.d3Force('link')?.distance(linkDistance).strength(0.75)
    fg.d3Force('collide', forceCollide((n: SimNode) => radiusOf(n) + 4).iterations(2))
    // Gentle pull toward center so weakly-connected nodes stay in frame.
    fg.d3Force('x', forceX(0).strength(0.045))
    fg.d3Force('y', forceY(0).strength(0.045))
    fg.d3ReheatSimulation?.()
  }, [data.nodes.length])

  const anyHighlight = highlightNodes.size > 0
  const anyQuery = queryMatch.size > 0

  const paintNode = useCallback((node: SimNode, ctx: CanvasRenderingContext2D, scale: number) => {
    const meta = NODE_META[node.type] ?? NODE_META.fact
    const isDimByType = dimmed.has(node.type)
    const isDimByHover = anyHighlight && !highlightNodes.has(node.id)
    const isDimByQuery = anyQuery && !queryMatch.has(node.id)
    const dim = isDimByType || isDimByHover || isDimByQuery
    const selected = node.id === selectedId
    const r = radiusOf(node) + (selected ? 2 : 0)

    ctx.globalAlpha = dim ? 0.1 : 1

    // soft outer glow / selection halo
    if (selected || (anyHighlight && highlightNodes.has(node.id))) {
      ctx.beginPath()
      ctx.arc(node.x!, node.y!, r + 4, 0, 2 * Math.PI)
      ctx.fillStyle = meta.color + '26'
      ctx.fill()
    }

    // solid, saturated fill + white ring for contrast
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, r, 0, 2 * Math.PI)
    ctx.fillStyle = meta.color
    ctx.fill()
    ctx.lineWidth = 1.4
    ctx.strokeStyle = '#ffffff'
    ctx.stroke()

    // Labels: company/vendor names are the map's landmarks; facts/contacts on hover
    // or zoom-in; bills stay unlabeled unless hovered/selected (there are too many).
    const focused = selected || (anyHighlight && highlightNodes.has(node.id))
    const showLabel =
      node.type === 'tenant' ? true :
      node.type === 'vendor' ? (scale > 0.55 || focused) :
      node.type === 'fact' ? (scale > 1.3 || focused) :
      node.type === 'contact' ? (scale > 1.1 || focused) :
      focused
    if (showLabel && !dim) {
      const fontSize = Math.max(3.5, (node.type === 'tenant' ? 14 : 11) / scale)
      ctx.font = `${node.type === 'tenant' ? 700 : 500} ${fontSize}px Inter, system-ui, sans-serif`
      const label = node.label || ''
      const short = label.length > 30 ? label.slice(0, 28) + '…' : label
      const tw = ctx.measureText(short).width
      const ty = node.y! + r + 2
      // pill background for legibility
      ctx.globalAlpha = dim ? 0.1 : 0.92
      ctx.fillStyle = '#ffffff'
      const padX = 2 / scale, h = fontSize + 2 / scale
      roundRect(ctx, node.x! - tw / 2 - padX, ty, tw + padX * 2, h, 2 / scale)
      ctx.fill()
      ctx.globalAlpha = dim ? 0.1 : 1
      ctx.fillStyle = node.type === 'tenant' ? meta.color : '#374151'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillText(short, node.x!, ty + 1 / scale)
    }
    ctx.globalAlpha = 1
  }, [dimmed, anyHighlight, highlightNodes, anyQuery, queryMatch, selectedId])

  const paintPointerArea = useCallback((node: SimNode, color: string, ctx: CanvasRenderingContext2D) => {
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, radiusOf(node) + 3, 0, 2 * Math.PI)
    ctx.fill()
  }, [])

  const linkColor = useCallback((l: SimLink) => {
    if (highlightLinks.has(l)) return '#6366f1'
    return anyHighlight ? '#eceef1' : '#dfe3e8'
  }, [highlightLinks, anyHighlight])
  const linkWidth = useCallback((l: SimLink) => (highlightLinks.has(l) ? 2.2 : 0.8), [highlightLinks])
  const linkParticles = useCallback((l: SimLink) => (highlightLinks.has(l) ? 4 : 0), [highlightLinks])

  // Relation label on highlighted edges
  const linkLabel = useCallback((l: SimLink) => REL_LABELS[l.relation] ?? l.relation, [])

  const graphData = useMemo(() => data, [data])

  return (
    <ForceGraph2D
      ref={fgRef}
      width={width}
      height={height}
      graphData={graphData}
      backgroundColor="#fbfbfd"
      nodeRelSize={1}
      cooldownTime={5000}
      warmupTicks={30}
      d3AlphaDecay={0.022}
      d3VelocityDecay={0.34}
      nodeCanvasObject={paintNode}
      nodePointerAreaPaint={paintPointerArea}
      linkColor={linkColor}
      linkWidth={linkWidth}
      linkDirectionalParticles={linkParticles}
      linkDirectionalParticleWidth={2}
      linkLabel={linkLabel}
      onNodeHover={(n: SimNode | null) => onHover(n ? n.id : null)}
      onNodeClick={(n: SimNode) => onSelect(n.id)}
      onBackgroundClick={() => onSelect(null)}
      onNodeDragEnd={(n: any) => { n.fx = n.x; n.fy = n.y }}
      onEngineStop={() => {
        if (!fittedRef.current) { fittedRef.current = true; fgRef.current?.zoomToFit(600, 70) }
      }}
    />
  )
})

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

export default ForceGraphCanvas
export { endId }
