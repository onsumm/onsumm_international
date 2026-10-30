/**
 * One module-level store, subscribed to with useSyncExternalStore.
 *
 * Why a store rather than useState in the page: four things have to agree at all
 * times — the queue, the open drawer, the nav badge and the per-record strips
 * embedded in the bill and vendor screens. A resolution in the drawer must make
 * the badge tick down in the same frame. `useState` in ReviewHubPage cannot
 * reach the badge, and lifting it to App.tsx would rebuild the whole shell on
 * every finding update.
 *
 * ~200 lines, no dependency. The codebase hand-rolls its SSE parser, focus trap
 * and column-filter engine; adding a query library for one page would be a
 * larger commitment than the page itself.
 */
import { useCallback, useSyncExternalStore } from 'react'

import {
  type DismissReason, type Finding, type FindingAction, type FindingQuery,
  type ReviewCounts,
  bulkStatus, fetchCounts, fetchFinding, fetchFindings, rescan, runAction,
  setFindingStatus, undoFinding,
} from '../../api/review'

const COUNTS_CACHE_KEY = 'onsum_review_counts'

/** Mirrors BulkRequest.finding_ids' max_length in routers/review_hub.py. */
const BULK_MAX = 500
/** Mirrors that router's MAX_LIMIT, so expansion pages as coarsely as allowed. */
const BULK_EXPAND_PAGE = 200
/** Enough to reach BULK_MAX and know we have passed it, and no more. */
const BULK_EXPAND_MAX_PAGES = Math.ceil(BULK_MAX / BULK_EXPAND_PAGE) + 1

export interface UndoEntry {
  findingId: string
  token: string
  title: string
  expiresAt: number
  /** Where it sat in the queue, so undo restores the user's place. */
  index?: number
}

interface State {
  counts: ReviewCounts | null
  countsLoading: boolean
  byId: Map<string, Finding>
  /** Ordered ids for the current query. Frozen on load — see loadQueue. */
  order: string[]
  /** How many findings match the query, ignoring the page. Null before the
   *  first load, and on the grouped path, which has no pages. */
  total: number | null
  loading: boolean
  error: string | null
  /** Findings that arrived since the list was frozen, held back from the queue. */
  pending: Finding[]
  undo: UndoEntry | null
  busy: Set<string>
  /** A failed fix, keyed by finding id, shown inline on the card that owns it
   *  rather than only in a toast that has already scrolled out of view. */
  rowErrors: Map<string, string>
  /** Ids fetched by id rather than by the list — a deep link (`?f=`), or a
   *  cluster host. `loadQueue` replaces `byId` wholesale, so without this a
   *  deep-linked finding that isn't on the first page gets evicted the moment
   *  the list query lands and the drawer never opens. */
  pinned: Set<string>
}

const state: State = {
  counts: readCachedCounts(),
  countsLoading: false,
  byId: new Map(),
  order: [],
  total: null,
  loading: false,
  error: null,
  pinned: new Set(),
  pending: [],
  undo: null,
  busy: new Set(),
  rowErrors: new Map(),
}

const listeners = new Set<() => void>()
let snapshotVersion = 0

function emit() {
  snapshotVersion += 1
  listeners.forEach(l => l())
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

function readCachedCounts(): ReviewCounts | null {
  // Counts only, so the nav badge paints instantly instead of flashing zero on
  // every navigation. Never finding bodies — a stale finding is a lie.
  try {
    const raw = sessionStorage.getItem(COUNTS_CACHE_KEY)
    return raw ? (JSON.parse(raw) as ReviewCounts) : null
  } catch {
    return null
  }
}

function writeCachedCounts(c: ReviewCounts) {
  try {
    sessionStorage.setItem(COUNTS_CACHE_KEY, JSON.stringify(c))
  } catch {
    /* private mode, quota, blocked site data — the badge just starts empty */
  }
}

// ── Reads ─────────────────────────────────────────────────────────────────────

export function useReviewCounts(): ReviewCounts | null {
  return useSyncExternalStore(subscribe, () => state.counts)
}

export function useReviewVersion(): number {
  return useSyncExternalStore(subscribe, () => snapshotVersion)
}

export function useFinding(id: string | null): Finding | undefined {
  useReviewVersion()
  return id ? state.byId.get(id) : undefined
}

export function useQueue() {
  useReviewVersion()
  return {
    items: state.order.map(id => state.byId.get(id)).filter(Boolean) as Finding[],
    loading: state.loading,
    error: state.error,
    total: state.total,
    pendingCount: state.pending.length,
    undo: state.undo,
    busy: state.busy,
    rowErrors: state.rowErrors,
  }
}

/** Findings attached to one record, for the inline strip on a bill or vendor. */
export function useEntityFindings(subjectType: string, subjectId: string | null) {
  useReviewVersion()
  if (!subjectId) return []
  return [...state.byId.values()].filter(
    f => f.subject.type === subjectType && f.subject.id === subjectId && !isClosed(f),
  )
}

function isClosed(f: Finding) {
  return f.status === 'RESOLVED' || f.status === 'DISMISSED' || f.status === 'AUTO_RESOLVED'
}

/**
 * Take a finding out of the list it is being shown in, because it stopped being
 * true — approved, fixed, resolved, snoozed away.
 *
 * Nothing else moves. The list is frozen on load by design (see loadQueue and
 * `pending`), so this removes one id rather than re-running the query: a user
 * mid-triage must not have the twelve rows under their cursor re-ordered
 * because they cleared one.
 *
 * Returns where it was, so an undo can put it back where the user left it.
 */
function retire(id: string): number {
  const at = state.order.indexOf(id)
  const f = state.byId.get(id)

  // A grouped card stands for every finding of its type, so clearing one member
  // must not take the other twenty-four off the screen with it. The card stays
  // and the census it carries drops by one.
  if (f?.group && f.group.count > 1) {
    state.byId.set(id, {
      ...f,
      group: {
        ...f.group,
        count: f.group.count - 1,
        money_at_risk_inr:
          f.group.money_at_risk_inr != null
            ? Math.max(0, f.group.money_at_risk_inr - (f.money_at_risk_inr ?? 0))
            : f.group.money_at_risk_inr,
      },
    })
    return -1
  }

  if (at >= 0) state.order = [...state.order.slice(0, at), ...state.order.slice(at + 1)]
  return at
}

/** Put a retired finding back where it was, for undo. */
function unretire(id: string, at: number | undefined) {
  if (at == null || at < 0 || state.order.includes(id)) return
  const next = [...state.order]
  next.splice(Math.min(at, next.length), 0, id)
  state.order = next
}

// ── Writes ────────────────────────────────────────────────────────────────────

let queryToken = 0

export const reviewActions = {
  async loadCounts() {
    state.countsLoading = true
    emit()
    try {
      const c = await fetchCounts()
      state.counts = c
      writeCachedCounts(c)
    } catch {
      /* the badge keeps its last value rather than flashing to zero */
    } finally {
      state.countsLoading = false
      emit()
    }
  },

  /**
   * Load the first page for a query, replacing the list.
   *
   * The resulting order is then FROZEN: a later poll updates cards in place and
   * parks genuinely new ones in `pending`, but never reorders. Cards shuffling
   * under a cursor mid-triage is how someone resolves the wrong thing.
   */
  async loadQueue(q: FindingQuery) {
    const token = ++queryToken
    state.loading = true
    state.error = null
    emit()
    try {
      const res = await fetchFindings(q)
      if (token !== queryToken) return  // a newer query won; discard this one
      const next = new Map(res.items.map(f => [f.finding_id, f]))
      // A finding opened by id (a `?f=` deep link) is fetched in parallel with
      // this query and usually wins the race, so replacing the map wholesale
      // dropped it again before the drawer could read it — which is why a deep
      // link only worked for rows that happened to be on the first page.
      for (const id of state.pinned) {
        const held = state.byId.get(id)
        if (held && !next.has(id)) next.set(id, held)
      }
      state.byId = next
      state.order = res.items.map(f => f.finding_id)
      state.total = res.total
      state.pending = []
    } catch (e) {
      if (token !== queryToken) return
      state.error = e instanceof Error ? e.message : 'Could not load findings'
    } finally {
      if (token === queryToken) {
        state.loading = false
        emit()
      }
    }
  },

  /** Background refresh: update in place, hold new arrivals back. */
  async refresh(q: FindingQuery) {
    try {
      const res = await fetchFindings(q)
      const seen = new Set(state.order)
      const arrivals: Finding[] = []
      for (const f of res.items) {
        if (seen.has(f.finding_id)) state.byId.set(f.finding_id, f)
        else if (!state.pending.some(p => p.finding_id === f.finding_id)) arrivals.push(f)
      }
      state.pending = [...state.pending, ...arrivals]
      emit()
    } catch {
      /* a failed background poll is not worth interrupting a triage session for */
    }
  },

  /** Fold the held-back findings into the visible queue, on the user's say-so. */
  showPending() {
    for (const f of state.pending) {
      state.byId.set(f.finding_id, f)
      state.order.unshift(f.finding_id)
    }
    state.pending = []
    emit()
  },

  async openFinding(id: string) {
    state.pinned.add(id)
    try {
      const full = await fetchFinding(id)
      state.byId.set(id, full)
      emit()
      return full
    } catch (e) {
      state.error = e instanceof Error ? e.message : 'Could not open that finding'
      emit()
      return undefined
    }
  },

  async setStatus(
    id: string,
    status: 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED' | 'OPEN',
    opts: { reason?: DismissReason | string; snoozeDays?: number;
            suppressScope?: 'exact_key' | 'type_on_subject' | 'type_tenant_wide' } = {},
  ) {
    const before = state.byId.get(id)
    state.busy = new Set(state.busy).add(id)
    emit()
    try {
      const res = await setFindingStatus(id, {
        status,
        reason: opts.reason,
        snooze_days: opts.snoozeDays,
        suppress_scope: opts.suppressScope,
      })
      state.byId.set(id, res.finding)
      // Resolved, dismissed and snoozed all mean "not in this list any more" —
      // snoozed included, since it moves to the Snoozed tab.
      const at = status === 'ACKNOWLEDGED' || status === 'OPEN' ? -1 : retire(id)
      if (res.undo_token && before) {
        state.undo = {
          findingId: id,
          token: res.undo_token,
          title: before.title,
          index: at,
          expiresAt: Date.now() + (res.undo_expires_in ?? 30) * 1000,
        }
      }
      void reviewActions.loadCounts()
      return res.finding
    } catch (e) {
      state.error = e instanceof Error ? e.message : 'That did not go through'
      throw e
    } finally {
      const next = new Set(state.busy)
      next.delete(id)
      state.busy = next
      emit()
    }
  },

  async undoLast() {
    const u = state.undo
    if (!u || Date.now() > u.expiresAt) return
    try {
      const res = await undoFinding(u.findingId, u.token)
      state.byId.set(u.findingId, res.finding)
      // Back in the list at the row the user was looking at, not at the top.
      unretire(u.findingId, u.index)
      state.undo = null
      void reviewActions.loadCounts()
    } catch (e) {
      state.error = e instanceof Error ? e.message : 'That undo has expired'
    } finally {
      emit()
    }
  },

  dismissUndo() {
    state.undo = null
    emit()
  },

  /**
   * Every finding the selected rows actually stand for.
   *
   * A queue row is a grouped card — "Vendor has no bank account on file × 224"
   * — and the finding_id it carries is one representative, not the group. So
   * selecting a card and bulk-snoozing it moved exactly one of the 224, which
   * is not what "1 selected → Snooze a week" looks like it does.
   *
   * Expanded here rather than by handing the endpoint a finding_type, because
   * by-type would act on every *visible* finding of that kind — the card was
   * counted under the queue's own filters (scope, lane, status) and says "16
   * you can act on", so re-querying with that same query is what makes the
   * two agree. It also keeps the per-member failure reporting the bulk
   * endpoint promises.
   */
  async expandForBulk(rows: Finding[], q: FindingQuery) {
    const ids: string[] = []
    const seen = new Set<string>()
    const add = (id: string) => {
      if (!seen.has(id)) { seen.add(id); ids.push(id) }
    }
    let truncated = false

    for (const f of rows) {
      if (!f.group || f.group.count <= 1) {
        add(f.finding_id)
        continue
      }
      let cursor: string | undefined
      // The endpoint pages at MAX_LIMIT=200, so a 224-member group needs more
      // than one trip; stop early once past the cap rather than walking a
      // group of thousands only to slice it away.
      //
      // Both stop conditions past the cursor matter: a page that adds nothing
      // new means the cursor is not advancing, and this loop is driven by
      // whatever the server returns — it must not be able to spin forever on
      // a repeated page. The page bound is the backstop for anything else.
      for (let page = 0; page < BULK_EXPAND_MAX_PAGES; page += 1) {
        const before = ids.length
        const res = await fetchFindings({
          ...q, group: false, finding_type: f.group.finding_type,
          limit: BULK_EXPAND_PAGE, cursor,
        })
        res.items.forEach(m => add(m.finding_id))
        cursor = res.next_cursor ?? undefined
        if (!cursor || ids.length > BULK_MAX || ids.length === before) break
      }
      if (ids.length > BULK_MAX) truncated = true
    }

    return { ids: ids.slice(0, BULK_MAX), truncated }
  },

  async bulk(body: Parameters<typeof bulkStatus>[0]) {
    const res = await bulkStatus(body)
    for (const id of res.updated) {
      const f = state.byId.get(id)
      // snoozed_until has to move with the status, not just the status: the
      // server clears it for every non-SNOOZED outcome, and a cached finding
      // still carrying "snoozed until Friday" renders a row that has just been
      // un-snoozed as though it were still asleep.
      if (f) {
        state.byId.set(id, {
          ...f,
          status: body.status,
          snoozed_until: body.status === 'SNOOZED' ? f.snoozed_until : null,
        })
      }
    }
    void reviewActions.loadCounts()
    emit()
    return res
  },

  /** Run a safe fix, then drop the card — the server has already closed it. */
  async act(finding: Finding, action: FindingAction, values: Record<string, string> = {}) {
    state.busy = new Set(state.busy).add(finding.finding_id)
    // A retry clears the previous failure's inline message immediately, rather
    // than leaving a stale error showing while the new attempt is in flight.
    if (state.rowErrors.has(finding.finding_id)) {
      const next = new Map(state.rowErrors)
      next.delete(finding.finding_id)
      state.rowErrors = next
    }
    emit()
    try {
      const res = (await runAction(finding, action, values)) as Record<string, unknown>

      // A notify action is a side communication, never a fix: asking the
      // vendor to confirm a PAN/GSTIN does not make the conflict go away, so
      // the finding must stay open regardless of whether the message actually
      // sent. Reuses the same "started, not finished" shape `action.queued`
      // already produces below, rather than a second result shape callers
      // would need to branch on separately.
      if (action.kind === 'notify') {
        return {
          queued: true,
          message:
            res?.status === 'ok'
              ? (typeof res.message === 'string' && res.message) || 'Sent.'
              : (typeof res?.message === 'string' && res.message) ||
                'Could not send — reach them yourself.',
        }
      }

      // A 200 is not an outcome. Some of these endpoints only *start* the work
      // — /gstins/primary/reverify resets the vendor and notifies the GST
      // worker — and others answer 200 while reporting that the problem is
      // still there. Clearing the row on the status code alone told users a
      // thing was fixed when it had not been looked at yet.
      if (action.queued) {
        return { queued: true, message: action.queued }
      }

      const expected = action.success
      if (expected && !Object.entries(expected).every(([k, v]) => res?.[k] === v)) {
        throw new Error(
          (typeof res?.message === 'string' && res.message) ||
          (typeof res?.detail === 'string' && res.detail) ||
          'That ran, but the problem is still there.',
        )
      }

      const current = state.byId.get(finding.finding_id)
      if (current) {
        state.byId.set(finding.finding_id, { ...current, status: 'RESOLVED' })
      }
      // The bill was approved, or the fix was applied: the finding is gone from
      // the queue now, not at the next load. A hub that still lists what you
      // just cleared is one you start scrolling past.
      retire(finding.finding_id)
      void reviewActions.loadCounts()
      return { ...res, queued: false }
    } catch (e) {
      // Kept on the card that owns it, not only in a toast that has already
      // scrolled out of view by the time the response comes back.
      const next = new Map(state.rowErrors)
      next.set(finding.finding_id, e instanceof Error ? e.message : 'That did not go through.')
      state.rowErrors = next
      throw e
    } finally {
      const next = new Set(state.busy)
      next.delete(finding.finding_id)
      state.busy = next
      emit()
    }
  },

  clearRowError(id: string) {
    if (!state.rowErrors.has(id)) return
    const next = new Map(state.rowErrors)
    next.delete(id)
    state.rowErrors = next
    emit()
  },

  async rescan(subjectType = 'tenant', subjectId?: string) {
    return rescan({ subject_type: subjectType, subject_id: subjectId })
  },

  /**
   * Apply one cluster's fix to every selected member — the cluster drill-in's
   * bulk bar (design brief §4 artboard 5).
   *
   * There is no bulk-execute endpoint: `POST /findings/bulk` only ever changes
   * lifecycle status (resolved/dismissed/snoozed), it never runs a finding's
   * own remediation. So this reuses `act()` once per member, exactly the call
   * a single card's fix button makes, sequentially, one at a time — each
   * member gets busy/rowError tracking for free because `act()` already writes
   * both into this same store.
   *
   * A member not already loaded (the cluster's `sample` carries only an id, a
   * title and a money figure — not its own `actions`) is fetched first via
   * `openFinding`, which is the same request the drawer makes when a card is
   * opened. `matchActionId` picks the sibling's action with the same id as the
   * one the user clicked on the primary finding; sibling rows can and do carry
   * a different default value in that action's fields (their own wrong GSTIN,
   * their own bank account), so running "the same fix" means running each
   * member's own version of it, not replaying the primary's literal request.
   */
  async runClusterFix(ids: string[], matchActionId?: string) {
    const results: { id: string; ok: boolean; error?: string }[] = []
    for (const id of ids) {
      let f = state.byId.get(id)
      if (!f) {
        f = await reviewActions.openFinding(id)
      }
      if (!f) {
        results.push({ id, ok: false, error: 'Could not load this record' })
        continue
      }
      const action =
        (matchActionId && f.actions.find(a => a.id === matchActionId && a.kind === 'safe')) ||
        f.actions.find(a => a.kind === 'safe' && !a.destructive)
      if (!action) {
        results.push({ id, ok: false, error: 'No one-click fix for this record' })
        continue
      }
      try {
        await reviewActions.act(f, action)
        results.push({ id, ok: true })
      } catch (e) {
        results.push({ id, ok: false, error: e instanceof Error ? e.message : 'That did not go through.' })
      }
    }
    return results
  },

  clearError() {
    state.error = null
    emit()
  },

  /** Brand switching must not leave the previous brand's findings on screen. */
  reset() {
    state.byId = new Map()
    state.pinned = new Set()
    state.order = []
    state.total = null
    state.pending = []
    state.undo = null
    state.counts = null
    state.error = null
    state.rowErrors = new Map()
    try {
      sessionStorage.removeItem(COUNTS_CACHE_KEY)
    } catch {
      /* nothing to clear */
    }
    emit()
  },
}

/** Stable callback for components that only need to trigger a reload. */
export function useReviewReload(q: FindingQuery) {
  return useCallback(() => void reviewActions.loadQueue(q), [JSON.stringify(q)])  // eslint-disable-line react-hooks/exhaustive-deps
}
