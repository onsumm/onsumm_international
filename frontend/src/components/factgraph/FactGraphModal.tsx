import { useEffect, useMemo, useRef, useState, useCallback, lazy, Suspense } from 'react'
import { Network, X, RefreshCw, AlertTriangle, Search } from 'lucide-react'
import { fetchFactGraph, openFactGraphStream } from '../../api/factGraph'
import type { FactGraphStream } from '../../api/factGraph'
import type { FactGraph, GraphNode, NodeType } from '../../api/factGraph'
// The canvas pulls in react-force-graph-2d and d3-force. Only this modal renders
// it, so it loads when the graph is opened rather than on first paint. The types
// stay a static import — they are erased at compile time and cost nothing.
import type { SimNode, SimLink, CanvasHandle } from './ForceGraphCanvas'
const ForceGraphCanvas = lazy(() => import('./ForceGraphCanvas'))
import NodeDetailsPanel, { type Neighbor } from './NodeDetailsPanel'
import { NODE_META, NODE_ORDER } from './graphTheme'

interface Props {
  companyId?: string
  onClose: () => void
}

type Status = 'loading' | 'ready' | 'empty' | 'error'

const endId = (x: string | SimNode): string => (typeof x === 'object' ? x.id : x)
const linkKey = (s: string, rel: string, t: string) => `${s}|${rel}|${t}`

export default function FactGraphModal({ companyId, onClose }: Props) {
  const [status, setStatus] = useState<Status>('loading')
  const [error, setError] = useState<string>('')
  const [graphData, setGraphData] = useState<{ nodes: SimNode[]; links: SimLink[] }>({ nodes: [], links: [] })
  const [streaming, setStreaming] = useState(false)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [dimmed, setDimmed] = useState<Set<NodeType>>(new Set())

  const nodesRef = useRef<Map<string, SimNode>>(new Map())
  const linksRef = useRef<Map<string, SimLink>>(new Map())
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<CanvasHandle>(null)
  const esRef = useRef<FactGraphStream | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })

  // ── Merge helper: preserves existing node objects (and their sim positions) ──
  const mergeGraph = useCallback((g: FactGraph, replace = false) => {
    if (replace) { nodesRef.current.clear(); linksRef.current.clear() }
    for (const n of g.nodes) {
      const existing = nodesRef.current.get(n.id)
      if (existing) {
        existing.label = n.label; existing.type = n.type; existing.properties = n.properties
      } else {
        nodesRef.current.set(n.id, { id: n.id, type: n.type, label: n.label, properties: n.properties })
      }
    }
    for (const e of g.edges) {
      const k = linkKey(e.source, e.relation, e.target)
      if (!linksRef.current.has(k)) {
        // Seed a brand-new node near an already-placed neighbour so it "grows out".
        const src = nodesRef.current.get(e.source)
        const tgt = nodesRef.current.get(e.target)
        const anchor = (src && src.x != null) ? src : (tgt && tgt.x != null) ? tgt : null
        for (const n of [src, tgt]) {
          if (n && n.x == null && anchor) {
            n.x = (anchor.x ?? 0) + (Math.random() - 0.5) * 40
            n.y = (anchor.y ?? 0) + (Math.random() - 0.5) * 40
          }
        }
        linksRef.current.set(k, { source: e.source, target: e.target, relation: e.relation, weight: e.weight })
      }
    }
    setGraphData({ nodes: [...nodesRef.current.values()], links: [...linksRef.current.values()] })
  }, [])

  // ── Initial load + live stream ──────────────────────────────────────────────
  useEffect(() => {
    if (!companyId) { setStatus('empty'); return }
    let cancelled = false
    setStatus('loading')
    fetchFactGraph(companyId)
      .then(g => {
        if (cancelled) return
        mergeGraph(g, true)
        setStatus(g.nodes.length ? 'ready' : 'empty')
        // Open the live stream for incremental "builds as it settles" updates.
        setStreaming(true)
        esRef.current = openFactGraphStream(companyId, {
          onSnapshot: sg => { if (!cancelled) mergeGraph(sg, false) },
          onDelta: dg => { if (!cancelled && (dg.nodes.length || dg.edges.length)) { mergeGraph(dg, false); setStatus('ready') } },
          onDone: () => setStreaming(false),
          onError: () => setStreaming(false),
        })
      })
      .catch(err => { if (!cancelled) { setError(err?.response?.data?.detail || err?.message || 'Failed to load fact graph.'); setStatus('error') } })
    return () => {
      cancelled = true
      esRef.current?.close()
      esRef.current = null
    }
  }, [companyId, mergeGraph])

  // ── Escape to close ───────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // ── Container sizing ────────────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect
      setSize({ w: Math.floor(r.width), h: Math.floor(r.height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Fit to view once the first graph is in.
  useEffect(() => {
    if (status === 'ready') {
      const t = setTimeout(() => canvasRef.current?.zoomToFit(600), 700)
      return () => clearTimeout(t)
    }
  }, [status])

  // ── Adjacency for hover highlight + details neighbours ────────────────────
  const adjacency = useMemo(() => {
    const m = new Map<string, SimLink[]>()
    for (const l of graphData.links) {
      const s = endId(l.source), t = endId(l.target)
      if (!m.has(s)) m.set(s, [])
      if (!m.has(t)) m.set(t, [])
      m.get(s)!.push(l); m.get(t)!.push(l)
    }
    return m
  }, [graphData.links])

  const { highlightNodes, highlightLinks } = useMemo(() => {
    const hn = new Set<string>()
    const hl = new Set<SimLink>()
    if (hoverId) {
      hn.add(hoverId)
      for (const l of adjacency.get(hoverId) ?? []) {
        hl.add(l); hn.add(endId(l.source)); hn.add(endId(l.target))
      }
    }
    return { highlightNodes: hn, highlightLinks: hl }
  }, [hoverId, adjacency])

  // Drop orphan nodes (degree 0 — e.g. supplier-less bills) so nothing drifts off
  // into empty space, and tag each node with its degree for size scaling.
  const visibleData = useMemo(() => {
    const deg = new Map<string, number>()
    for (const l of graphData.links) {
      const s = endId(l.source), t = endId(l.target)
      deg.set(s, (deg.get(s) ?? 0) + 1)
      deg.set(t, (deg.get(t) ?? 0) + 1)
    }
    const nodes = graphData.nodes.filter(n => n.type === 'tenant' || (deg.get(n.id) ?? 0) > 0)
    for (const n of nodes) (n as SimNode).degree = deg.get(n.id) ?? 0
    const keep = new Set(nodes.map(n => n.id))
    const links = graphData.links.filter(l => keep.has(endId(l.source)) && keep.has(endId(l.target)))
    return { nodes, links }
  }, [graphData])

  const orphanCount = graphData.nodes.length - visibleData.nodes.length

  const queryMatch = useMemo(() => {
    const s = new Set<string>()
    const q = query.trim().toLowerCase()
    if (!q) return s
    for (const n of graphData.nodes) if ((n.label || '').toLowerCase().includes(q)) s.add(n.id)
    return s
  }, [query, graphData.nodes])

  const selectedNode: GraphNode | null = useMemo(
    () => (selectedId ? nodesRef.current.get(selectedId) ?? null : null),
    [selectedId, graphData.nodes],
  )

  const neighbors: Neighbor[] = useMemo(() => {
    if (!selectedId) return []
    const out: Neighbor[] = []
    for (const l of adjacency.get(selectedId) ?? []) {
      const otherId = endId(l.source) === selectedId ? endId(l.target) : endId(l.source)
      const n = nodesRef.current.get(otherId)
      if (n) out.push({ id: n.id, label: n.label, type: n.type, relation: l.relation })
    }
    return out
  }, [selectedId, adjacency])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const n of visibleData.nodes) c[n.type] = (c[n.type] ?? 0) + 1
    return c
  }, [visibleData.nodes])

  const toggleType = (t: NodeType) => setDimmed(prev => {
    const next = new Set(prev)
    next.has(t) ? next.delete(t) : next.add(t)
    return next
  })

  const onSearchEnter = () => {
    const first = graphData.nodes.find(n => queryMatch.has(n.id))
    if (first) { setSelectedId(first.id); canvasRef.current?.centerNode(first.id) }
  }

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose() }}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 2000, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Inter, sans-serif' }}>
      <div style={{ width: 'min(1200px, 94vw)', height: '86vh', background: '#fff', borderRadius: 14, display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid #f3f4f6', background: '#f9fafb' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Network size={18} color="#4f46e5" />
            <span style={{ fontSize: 15, fontWeight: 700, color: '#0b1c30' }}>Fact Graph</span>
            <span style={{ fontSize: 12, color: '#6b7280' }}>
              {visibleData.nodes.length} nodes · {visibleData.links.length} edges
              {orphanCount > 0 && <span style={{ color: '#9ca3af' }}> · {orphanCount} unlinked hidden</span>}
            </span>
            {streaming && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#4f46e5', marginLeft: 4 }}>
                <span className="fg-pulse" style={{ width: 7, height: 7, borderRadius: '50%', background: '#4f46e5' }} /> live
              </span>
            )}
          </div>
          <button onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#6b7280', display: 'flex' }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <div ref={containerRef} style={{ flex: 1, position: 'relative', background: '#f9fafb' }}>
            {/* Search box */}
            <div style={{ position: 'absolute', top: 12, left: 12, zIndex: 5, display: 'flex', alignItems: 'center', gap: 6, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '6px 10px', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              <Search size={14} color="#9ca3af" />
              <input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onSearchEnter() }}
                placeholder="Search nodes…"
                style={{ border: 'none', outline: 'none', fontSize: 13, fontFamily: 'inherit', width: 160, background: 'transparent' }} />
            </div>

            {/* Legend / type filters */}
            <div style={{ position: 'absolute', bottom: 12, left: 12, zIndex: 5, display: 'flex', flexDirection: 'column', gap: 4, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 10px', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
              {NODE_ORDER.map(t => {
                const meta = NODE_META[t]
                const off = dimmed.has(t)
                return (
                  <div key={t} onClick={() => toggleType(t)}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', opacity: off ? 0.35 : 1, fontSize: 12 }}>
                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: meta.bg, border: `2px solid ${meta.color}`, flexShrink: 0 }} />
                    <span style={{ color: '#374151', flex: 1 }}>{meta.label}</span>
                    <span style={{ color: '#9ca3af' }}>{counts[t] ?? 0}</span>
                  </div>
                )
              })}
            </div>

            {/* States */}
            {status === 'loading' && (
              <Centered><RefreshCw size={22} color="#6b7280" className="fg-spin" /><div style={msgStyle}>Building fact graph…</div></Centered>
            )}
            {status === 'error' && (
              <Centered>
                <AlertTriangle size={26} color="#dc2626" />
                <div style={msgStyle}>{error}</div>
                <button onClick={() => { setStatus('loading'); esRef.current?.close(); if (companyId) fetchFactGraph(companyId).then(g => { mergeGraph(g, true); setStatus(g.nodes.length ? 'ready' : 'empty') }).catch(err => { setError(err?.message || 'Failed'); setStatus('error') }) }}
                  style={ghostBtn}>Retry</button>
              </Centered>
            )}
            {status === 'empty' && (
              <Centered>
                <Network size={30} color="#9ca3af" strokeWidth={1} />
                <div style={{ ...msgStyle, maxWidth: 320 }}>No facts yet. The graph builds itself as the agent processes vendor bills and emails.</div>
              </Centered>
            )}
            {status === 'ready' && size.w > 0 && (
              <Suspense fallback={
                <Centered>
                  <RefreshCw size={20} color="#9ca3af" style={{ animation: 'fg-spin 1s linear infinite' }} />
                </Centered>
              }>
                <ForceGraphCanvas
                  ref={canvasRef}
                  width={size.w}
                  height={size.h}
                  data={visibleData}
                  highlightNodes={highlightNodes}
                  highlightLinks={highlightLinks}
                  selectedId={selectedId}
                  queryMatch={queryMatch}
                  dimmed={dimmed}
                  onHover={setHoverId}
                  onSelect={setSelectedId}
                />
              </Suspense>
            )}
          </div>

          {selectedNode && (
            <NodeDetailsPanel node={selectedNode} neighbors={neighbors}
              onSelectNeighbor={id => { setSelectedId(id); canvasRef.current?.centerNode(id) }}
              onClose={() => setSelectedId(null)} />
          )}
        </div>
      </div>

      <style>{`
        .fg-spin { animation: fg-spin 0.8s linear infinite; }
        @keyframes fg-spin { to { transform: rotate(360deg); } }
        .fg-pulse { animation: fg-pulse 1.2s ease-in-out infinite; }
        @keyframes fg-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
      `}</style>
    </div>
  )
}

const msgStyle: React.CSSProperties = { fontSize: 13, color: '#6b7280', textAlign: 'center', marginTop: 12 }
const ghostBtn: React.CSSProperties = { marginTop: 14, padding: '7px 16px', borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff', color: '#374151', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2 }}>
      {children}
    </div>
  )
}
