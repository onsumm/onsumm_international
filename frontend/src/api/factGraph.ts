import axios from 'axios'

// Fact knowledge graph — read API for the Agent Hub visualization + retrieval.
// Tenant is passed as `company_id` (the incumbent convention shared by api/agent.ts
// and the /api/agents backend router).

export type NodeType = 'tenant' | 'vendor' | 'bill' | 'contact' | 'fact'

export interface GraphNode {
  id: string
  type: NodeType
  label: string
  ref_id?: string | null
  properties?: Record<string, unknown>
}

export interface GraphEdge {
  edge_id?: string
  source: string
  target: string
  relation: string
  weight?: number
}

export interface FactGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export async function fetchFactGraph(companyId: string, limit = 500): Promise<FactGraph> {
  const { data } = await axios.get<FactGraph>('/api/agents/fact-graph', {
    params: { company_id: companyId, limit },
  })
  return data
}

export interface FactSearchResult {
  node_id: string
  fact_text: string
  fact_type?: string
  confidence?: number
  distance?: number
}

export async function searchFacts(companyId: string, q: string, k = 8): Promise<FactSearchResult[]> {
  const { data } = await axios.get<{ results: FactSearchResult[] }>('/api/agents/fact-graph/search', {
    params: { company_id: companyId, q, k },
  })
  return data.results ?? []
}

const BASE = (): string => (import.meta.env.VITE_API_URL ?? '') as string

/** Handle for a live stream. Mirrors the one method the caller actually used. */
export interface FactGraphStream {
  close: () => void
}

interface StreamHandlers {
  onSnapshot: (g: FactGraph) => void
  onDelta: (g: FactGraph) => void
  onDone?: () => void
  onError?: () => void
}

/** Parse one SSE block ("event: x\ndata: {...}") and route it to a handler. */
function dispatchSseBlock(block: string, handlers: StreamHandlers): 'done' | void {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith(':')) continue                      // comment / keep-alive
    const sep = line.indexOf(':')
    const field = sep === -1 ? line : line.slice(0, sep)
    // A single leading space after the colon is part of the framing, not the value.
    let value = sep === -1 ? '' : line.slice(sep + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  if (event === 'done') { handlers.onDone?.(); return 'done' }
  if (!data.length) return
  try {
    const payload = JSON.parse(data.join('\n')) as FactGraph
    if (event === 'snapshot') handlers.onSnapshot(payload)
    else if (event === 'delta') handlers.onDelta(payload)
  } catch { /* malformed frame — skip it rather than kill the stream */ }
}

/**
 * Open the live "graph builds in real time" stream. `onSnapshot` fires once with
 * the current graph, then `onDelta` fires for each batch of new nodes/edges.
 *
 * Implemented over `fetch` + ReadableStream rather than `EventSource`, because
 * EventSource cannot send an `Authorization` header — the old version worked
 * around that by putting the bearer token in the query string, which leaks it
 * into logs, history and Referer headers (see api/authedFiles.ts). The patched
 * global fetch attaches the header and handles 401-refresh for us.
 */
export function openFactGraphStream(companyId: string, handlers: StreamHandlers): FactGraphStream {
  const controller = new AbortController()
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    controller.abort()
  }

  void (async () => {
    try {
      const res = await fetch(
        `${BASE()}/api/agents/fact-graph/stream?company_id=${encodeURIComponent(companyId)}`,
        { signal: controller.signal, headers: { Accept: 'text/event-stream' } },
      )
      if (!res.ok || !res.body) throw new Error(`Stream failed (${res.status})`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // Frames are separated by a blank line; normalise CRLF first.
        buffer = buffer.replace(/\r\n/g, '\n')
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          if (dispatchSseBlock(block, handlers) === 'done') { close(); return }
        }
      }
      handlers.onDone?.()
    } catch {
      // An abort from close() is expected teardown, not a failure.
      if (!closed) handlers.onError?.()
    }
  })()

  return { close }
}
