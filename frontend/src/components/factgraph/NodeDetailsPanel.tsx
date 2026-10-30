import { X } from 'lucide-react'
import { NODE_META, REL_LABELS } from './graphTheme'
import type { GraphNode, NodeType } from '../../api/factGraph'
import { displayLocale } from '../../ui/format'

export interface Neighbor {
  id: string
  label: string
  type: NodeType
  relation: string
}

interface Props {
  node: GraphNode
  neighbors: Neighbor[]
  onSelectNeighbor: (id: string) => void
  onClose: () => void
}

function prettyValue(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'number') return v.toLocaleString(displayLocale())
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export default function NodeDetailsPanel({ node, neighbors, onSelectNeighbor, onClose }: Props) {
  const meta = NODE_META[node.type] ?? NODE_META.fact
  const props = node.properties ?? {}
  const entries = Object.entries(props).filter(([k]) => k !== 'source' && k !== 'entities')

  return (
    <div style={{ width: 320, borderLeft: '1px solid #e5e7eb', background: '#fafafa', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderBottom: '1px solid #eef0f3' }}>
        <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, padding: '3px 10px', borderRadius: 20, background: meta.bg, color: meta.color }}>
          {meta.label}
        </span>
        <button onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#9ca3af', display: 'flex' }}>
          <X size={16} />
        </button>
      </div>

      <div style={{ padding: '16px', overflow: 'auto' }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: '#111827', lineHeight: 1.4, marginBottom: 14, wordBreak: 'break-word' }}>
          {node.label}
        </div>

        {entries.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 18 }}>
            {entries.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12 }}>
                <span style={{ color: '#6b7280', textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</span>
                <span style={{ color: '#111827', fontWeight: 600, textAlign: 'right', wordBreak: 'break-word' }}>{prettyValue(v)}</span>
              </div>
            ))}
          </div>
        )}

        {node.type === 'fact' && props.source != null && (
          <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 18 }}>
            Source: {prettyValue((props.source as Record<string, unknown>)?.source)}
          </div>
        )}

        <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4, color: '#9ca3af', marginBottom: 8 }}>
          Connections ({neighbors.length})
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {neighbors.length === 0 && <div style={{ fontSize: 12, color: '#9ca3af' }}>No connections.</div>}
          {neighbors.map(nb => {
            const nm = NODE_META[nb.type] ?? NODE_META.fact
            return (
              <div key={nb.id + nb.relation} onClick={() => onSelectNeighbor(nb.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 8, cursor: 'pointer' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f1f2f4' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: nm.color, flexShrink: 0 }} />
                <span style={{ fontSize: 12, color: '#111827', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{nb.label}</span>
                <span style={{ fontSize: 10, color: '#9ca3af', flexShrink: 0 }}>{REL_LABELS[nb.relation] ?? nb.relation}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
