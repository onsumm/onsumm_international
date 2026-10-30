/**
 * Review Hub — /review
 *
 * One queue for everything wrong across the platform, scoped to what this user
 * can see and ranked by what it costs them.
 *
 * The information architecture rests on one rule:
 *
 *   **The census is always complete. The presentation is always bounded.**
 *
 * Counts for every lane are on screen at all times and never lie, but the page
 * renders twelve findings until the user asks for more. Someone with 400 open
 * findings sees the number 400 immediately and twelve cards. Nothing is hidden;
 * almost nothing is shown. The scope line states the active filter in words,
 * with a one-click escape, so the user is never in a state where they don't
 * know what is being withheld.
 *
 * The second rule: this file knows about *shapes*, never about *issue types*.
 * There is no switch on finding_type anywhere in the page. Lane, rank, severity,
 * evidence shape and the action list all arrive from the server, so a finding
 * type the agent invents tomorrow renders correctly today.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Check, CheckCircle2, RefreshCw } from 'lucide-react'

import type { DismissReason, Finding, FindingAction, SortDir, SortKey } from '../../api/review'
import { SORT_KEYS } from '../../api/review'
import { useAuth } from '../../auth/AuthContext'
import {
  type ReviewView, REVIEW_VIEWS, focusPath, reviewBrowsePath, reviewPath,
} from '../../routes/paths'
import {
  Button,
  EmptyState, ErrorState, PAGE_SIZES, Pager, SkeletonRows, formatMoneyCompact, useToast,
} from '../../ui'
import { ActionConfirm } from './ActionConfirm'
import { ClusterDrillIn } from './ClusterDrillIn'
import { DismissDialog } from './DismissDialog'
import { FindingDrawer } from './FindingDrawer'
import { FindingTable } from './FindingTable'
import { KpiStrip } from './KpiStrip'
import { QueueTable } from './QueueTable'
import { ReviewActivity } from './ReviewActivity'
import { useRecordCards, type RecordRef } from '../../components/records/recordLinks'
import { REVIEW_CHANGED_EVENT } from '../../components/records/reviewEvents'
import { CLAIM_CONFIG } from '../../components/records/claimFields'
import {
  FAMILY_ORDER, familyDisplay, pageCount, primaryAction, scopeLine,
  vendorFix,
} from './reviewFormat'
import { ShortcutsSheet } from './ShortcutsSheet'
import { reviewActions, useFinding, useQueue, useReviewCounts } from './useReviewStore'
import './review.css'

/** The queue's sort when the URL names none. */
const DEFAULT_SORT: SortKey = 'priority'

const PAGE_SIZE_KEY = 'review.perPage'

/** The last size this browser chose, for a URL that names none. */
function rememberedPageSize(): number {
  try {
    const n = Number(localStorage.getItem(PAGE_SIZE_KEY))
    return (PAGE_SIZES as readonly number[]).includes(n) ? n : PAGE_SIZES[0]
  } catch {
    return PAGE_SIZES[0]
  }
}
const COUNTS_POLL_MS = 45_000

export default function ReviewHubPage() {
  const [params, setParams] = useSearchParams()
  const { view: rawView } = useParams()
  const navigate = useNavigate()
  const view: ReviewView =
    rawView && (REVIEW_VIEWS as readonly string[]).includes(rawView)
      ? (rawView as ReviewView)
      : 'queue'
  const toast = useToast()
  const { activeBrand } = useAuth()

  const counts = useReviewCounts()
  const { items, loading, error, total, pendingCount, undo, busy, rowErrors } = useQueue()
  const recordCards = useRecordCards()

  // The Type filter: any number of families, in the URL as ?types=a,b. None
  // selected is every type. Not ?type= — that is the single-kind drill-in
  // (drillType below), a finding_type rather than a family.
  const types = useMemo(
    () => (params.get('types') || '').split(',').map(t => t.trim()).filter(Boolean),
    [params],
  )
  // Drilling into one kind is a URL state, so it is shareable and Back works.
  const drillType = params.get('type')
  const openId = params.get('f')
  const open = useFinding(openId)
  // Drilling into one finding's cluster — see ClusterDrillIn. A different URL
  // slot from `type`: a type-drill lists every instance of a *kind*, a cluster
  // is the specific siblings of one finding the drawer named.
  const clusterId = params.get('cluster')
  const clusterHost = useFinding(clusterId)

  // Page and sort live in the URL for the same reason lane and type do: a
  // controller who finds something on page 4 sorted by exposure has to be able
  // to send that page to someone, and Back has to mean what it looks like it
  // means.
  const page = Math.max(Number(params.get('page') || 1), 1)
  // Rows per page: in the URL (?per=50) so it survives a refresh and travels
  // with a link; else what this browser last chose.
  const perParam = Number(params.get('per'))
  const perPage = (PAGE_SIZES as readonly number[]).includes(perParam) ? perParam : rememberedPageSize()
  // Priority (P0 first) is the default: the queue is worked top-down by how
  // urgent each item is. The server breaks ties within a priority by rank.
  const rawSort = params.get('sort')
  const sort: SortKey = (SORT_KEYS as readonly string[]).includes(rawSort ?? '')
    ? (rawSort as SortKey)
    : DEFAULT_SORT
  const direction: SortDir = params.get('dir') === 'asc' ? 'asc' : 'desc'

  const query = useMemo(
    () => ({
      lane: null,
      family: types.length ? types : undefined,
      // Snoozed and Resolved are the same queue with a different status filter.
      // Resolved is the audit trail: finance will ask who dismissed what and why,
      // and "we don't keep it" is not an answer that survives that conversation.
      //
      // The queue itself only ever shows what this user can act on now — "the
      // rest" (visible but not actionable) is reached through Browse, the real
      // unbounded table, rather than by widening this filter in place. See the
      // scope line's "Show everything" button below.
      scope: view === 'queue' ? ('actionable' as const) : ('visible' as const),
      status: view === 'queue' || view === 'activity' ? ('open' as const) : (view as 'snoozed' | 'resolved'),
      // One row per finding, always. The queue used to collapse by kind and
      // ungroup only inside a type drill-in; the drill-in is now just this
      // same table with `finding_type` pinned.
      group: false,
      finding_type: drillType || undefined,
      limit: perPage,
      offset: (page - 1) * perPage,
      sort,
      direction,
    }),
    [types, view, drillType, page, perPage, sort, direction],
  )

  // Brand switching must not leave the previous brand's findings on screen with
  // the new brand's counts.
  useEffect(() => {
    reviewActions.reset()
  }, [activeBrand?.id])

  useEffect(() => {
    void reviewActions.loadCounts()
    // Activity is the audit log, not a queue — nothing to load here.
    if (view !== 'activity') void reviewActions.loadQueue(query)
  }, [query, view])

  // A card opened over the queue (a bill's corrections) changed findings.
  useEffect(() => {
    const reload = () => { void reviewActions.loadQueue(query); void reviewActions.loadCounts() }
    window.addEventListener(REVIEW_CHANGED_EVENT, reload)
    return () => window.removeEventListener(REVIEW_CHANGED_EVENT, reload)
  }, [query])


  // Counts stay live so the nav badge is honest on every page. Paused when the
  // tab is hidden — the same treatment AgentHubPage uses.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void reviewActions.loadCounts()
    }, COUNTS_POLL_MS)
    return () => clearInterval(t)
  }, [])

  // Fetch on every open, not only when the finding is missing from the store.
  // The list and the detail are not the same payload: previews, the cluster and
  // the event history exist only on the detail. Opening a row the list had
  // already cached therefore used to render a drawer with none of them, while
  // reloading the same URL — where nothing is cached — rendered the full one.
  // Same finding, two different drawers, which is worse than either.
  useEffect(() => {
    if (openId) void reviewActions.openFinding(openId)
  }, [openId])

  // The cluster view needs the full detail fetch too — `cluster.sample` only
  // exists on GET /findings/{id}, never on the grouped list, so a finding
  // reached by clicking a card and going straight to `?cluster=` (rather than
  // via the drawer, which already fetched it) still needs this.
  useEffect(() => {
    if (clusterId) void reviewActions.openFinding(clusterId)
  }, [clusterId])

  useEffect(() => {
    if (error) {
      toast.error(error)
      reviewActions.clearError()
    }
  }, [error, toast])

  const setParam = useCallback(
    (key: string, value: string | null) => {
      const next = new URLSearchParams(params)
      if (value == null) next.delete(key)
      else next.set(key, value)
      setParams(next, { replace: key === 'f' })
    },
    [params, setParams],
  )

  // Toggle one type in or out; "All types" clears. Any change starts at page 1.
  const setTypes = useCallback(
    (next: string[]) => {
      const p = new URLSearchParams(params)
      if (next.length) p.set('types', next.join(','))
      else p.delete('types')
      p.delete('page')
      p.delete('lane')
      setParams(p)
    },
    [params, setParams],
  )
  const toggleType = (fam: string) =>
    setTypes(types.includes(fam) ? types.filter(t => t !== fam) : [...types, fam])

  const openFinding = useCallback((id: string) => {
    // A stalled approval is answered on the bill itself: open the bill card,
    // where the approver can approve (or send back) in one step, instead of a
    // finding card that only describes the wait.
    const f = items.find(x => x.finding_id === id)
    const bill = f?.finding_type === 'approval_stalled'
      ? f.entities.find(e => e.type === 'bill' || e.type === 'invoice')
      : undefined
    // A missing / unverified GSTIN or bank account is fixed on that tab of the
    // vendor card — open it there, pointing at the exact control that fixes it.
    const fix = f ? vendorFix(f) : null
    const vendor = fix ? f?.entities.find(e => e.type === 'vendor') : undefined
    // Suggested corrections are made on the bill itself: open its card with
    // the suggestions on it, current value against proposed.
    const claimVendor = f && CLAIM_CONFIG[f.finding_type]
      ? f.entities.find(e => e.type === 'vendor')
      : undefined
    const contactVendor = f?.finding_type === 'vendor_contact_new'
      ? f.entities.find(e => e.type === 'vendor')
      : undefined
    const contactBody = f?.actions.find(a => a.id === 'keep_new')?.body as Record<string, unknown> | undefined
    const codingBill = f?.finding_type === 'bill_coding_corrections'
      ? f.entities.find(e => e.type === 'bill' || e.type === 'invoice')
      : undefined
    if (bill) recordCards.openBill(bill.id, {
      findingId: f?.finding_id,
      guide: {
        targets: ['bill.approve'],
        message: 'Waiting on your approval. Approve it here, or send it back with a note.',
      },
    })
    else if (codingBill && f) recordCards.openBill(codingBill.id, {
      findingId: f.finding_id, corrections: { findingId: f.finding_id },
    })
    else if (f?.finding_type === 'vendor_contact_new' && contactVendor && contactBody) {
      // A contact found in a contract: the vendor card's Add Contact form,
      // already filled in — the user checks it and saves.
      recordCards.openVendor(contactVendor.id, 'contacts', {
        findingId: f.finding_id,
        guide: {
          targets: ['contacts.save'],
          message: 'Found in a vendor contract — check the details and Save to add them.',
        },
        prefill: {
          findingId: f.finding_id,
          contact: {
            name: String(contactBody.name ?? ''), role: String(contactBody.role ?? ''),
            email: String(contactBody.email ?? ''), phone: String(contactBody.phone ?? ''),
          },
        },
      })
    }
    else if (claimVendor && f) {
      // A one-field vendor change (a contract's PAN / GSTIN / terms, or a
      // brand name the GST portal replaced): built into that field on the
      // vendor card.
      recordCards.openVendor(claimVendor.id, 'details', {
        findingId: f.finding_id, claim: { findingId: f.finding_id },
      })
    }
    else if (vendor && fix) recordCards.openVendor(vendor.id, fix.tab, {
      findingId: f?.finding_id,
      guide: { targets: fix.targets, message: fix.message, messages: fix.messages },
    })
    else setParam('f', id)
  }, [items, recordCards, setParam])

  /**
   * Clicking the active column reverses it; clicking another switches to it,
   * descending. Descending first because every sortable column here is one
   * where the interesting end is the top — most at risk, most urgent, oldest —
   * so ascending-first would make the first click on each column the wrong one.
   *
   * Both writes reset to page 1. Staying on page 4 through a re-sort shows a
   * slice of a list the user has never seen the start of.
   */
  const changeSort = useCallback(
    (key: SortKey) => {
      const next = new URLSearchParams(params)
      const flipping = key === sort
      const nextDir = flipping && direction === 'desc' ? 'asc' : 'desc'
      if (key === DEFAULT_SORT && !flipping) next.delete('sort')
      else next.set('sort', key)
      if (nextDir === 'asc') next.set('dir', 'asc')
      else next.delete('dir')
      next.delete('page')
      setParams(next)
    },
    [params, setParams, sort, direction],
  )

  const goToPage = useCallback(
    (n: number) => {
      const next = new URLSearchParams(params)
      if (n <= 1) next.delete('page')
      else next.set('page', String(n))
      setParams(next)
      // A new page under an unmoved scroll position reads as nothing having
      // happened; the table's own scroller is what the user is looking at.
      document.querySelector('.rh__browse-tablewrap')?.scrollTo({ top: 0 })
    },
    [params, setParams],
  )
  const closeDrawer = useCallback(() => setParam('f', null), [setParam])

  // A bill or vendor opens as a card over the hub, so the queue, its filters and
  // the open finding are all still there when the card closes.
  const entityRecord = (type: string, id: string): RecordRef | null => {
    if (type === 'bill' || type === 'invoice') return { kind: 'bill', id }
    if (type === 'vendor') return { kind: 'vendor', id }
    return null
  }
  const entityHref = (type: string, id: string): string | null => {
    const ref = entityRecord(type, id)
    return ref ? recordCards.hrefFor(ref) : null
  }
  const openEntity = (type: string, id: string) => {
    const ref = entityRecord(type, id)
    if (ref) recordCards.open(ref)
  }

  // The one polite live region on the page (see the accessibility notes in the
  // design brief): it announces the outcome of something the user did —
  // "Resolved. 22 remaining." — and nothing else. The census line above is
  // *not* wired to aria-live, because it also updates on the background counts
  // poll every 45s, and an announcement nobody asked for is exactly the noise
  // that gets a screen-reader user to switch live regions off altogether.
  const [liveMsg, setLiveMsg] = useState('')
  const announceResolution = useCallback(
    (verb: string) => {
      const remaining = Math.max((counts?.total_actionable ?? 1) - 1, 0)
      setLiveMsg(`${verb}. ${remaining} remaining.`)
    },
    [counts],
  )

  const handleStatus = useCallback(
    async (
      f: Finding,
      status: 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED',
      opts?: { reason?: string; snoozeDays?: number; suppressScope?: 'type_tenant_wide' },
    ) => {
      try {
        await reviewActions.setStatus(f.finding_id, status, opts)
        if (status === 'RESOLVED') announceResolution('Resolved')
        else if (status === 'DISMISSED') announceResolution('Dismissed')
        else if (status === 'SNOOZED') announceResolution('Snoozed')
        closeDrawer()
      } catch {
        /* the store surfaces the message through the toast effect above */
      }
    },
    [closeDrawer, announceResolution],
  )

  // The drawer names the action it ran; the queue's row-level button has only
  // one to run, so it falls back to the safe fix. Picking "the first executable
  // action" for both would let a row-level click land on an approval.
  const runFix = useCallback(
    async (f: Finding, action?: FindingAction, values?: Record<string, string>) => {
      const fix = action ?? f.actions.find(a => a.kind === 'safe')
      if (!fix) return
      try {
        const res = await reviewActions.act(f, fix, values)
        if (res.queued) {
          // Started, not finished: say so, and leave the finding where it is.
          toast.show({ tone: 'info', title: String(res.message) })
        } else {
          toast.success(String(res.message || 'Done.'))
          announceResolution('Resolved')
          // The counts and the queue both change when a bill leaves the chain.
          void reviewActions.loadCounts()
        }
        closeDrawer()
      } catch (e) {
        // The row itself carries the inline error (see FindingCard's `error`
        // prop, sourced from the store's rowErrors) — the toast is a second,
        // transient notice for whoever is looking at the toast tray instead.
        toast.error(e instanceof Error ? e.message : 'That did not go through.')
      }
    },
    [toast, closeDrawer, announceResolution],
  )

  // A row's primary action, from the row. Suspected findings offer one now
  // (reviewFormat.ts::primaryAction), so this is the path a PAN or
  // payment-terms approval actually takes — and it must be the same decision
  // the drawer asks for, not a quieter one. Hence: an action carrying inputs
  // opens the drawer, where there is somewhere to type them; an action
  // carrying a confirmation shows it, with its before → after line; only an
  // action with neither runs on the click.
  const [confirming, setConfirming] = useState<{ finding: Finding; action: FindingAction } | null>(null)
  const handleAct = useCallback((f: Finding) => {
    const fix = primaryAction(f)
    if (!fix) return
    if (fix.fields?.length) {
      openFinding(f.finding_id)
      return
    }
    if (fix.confirm) {
      setConfirming({ finding: f, action: fix })
      return
    }
    void runFix(f, fix)
  }, [runFix, openFinding])

  const [rescanning, setRescanning] = useState(false)
  const handleRescan = useCallback(async () => {
    setRescanning(true)
    try {
      const res = await reviewActions.rescan()
      toast.success(res.queued ? 'Rescan queued.' : 'A rescan is already running.')
    } catch {
      toast.error('Could not queue a rescan.')
    } finally {
      setRescanning(false)
    }
  }, [toast])

  // ── Keyboard triage: j/k move, o open, e primary action, s snooze, d
  // dismiss, x select, u undo, ? shows the sheet. Disabled while the drawer or
  // another dialog owns focus, and while the user is typing anywhere. ────────
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [kbdDismissTarget, setKbdDismissTarget] = useState<Finding | null>(null)
  // Resolving your way through page 5 shrinks the list under you, and a page
  // past the end returns nothing — no rows, therefore no pager, therefore no
  // way back. Land on the last page that exists instead.
  useEffect(() => {
    if (loading || total === null || total === 0) return
    const last = pageCount(total, perPage)
    if (page > last) goToPage(last)
  }, [loading, total, page, goToPage])

  // A selection is a set of rows on screen. Carried to another page it is a
  // bulk bar counting findings the user can no longer see — and `runBulk`
  // intersects the selection with the loaded rows, so acting on it would have
  // silently done nothing at all.
  useEffect(() => {
    setSelectedIds(new Set())
  }, [page, sort, direction])

  const flatList = !drillType // keyboard triage operates on the card queue, not the drilled-in table
  const listLength = flatList ? items.length : 0

  useEffect(() => {
    // Focus follows the queue: if the row it pointed at is gone, the same
    // numeric index now names whichever card took its place.
    setFocusedIndex(i => {
      if (listLength === 0) return null
      if (i == null) return 0
      return Math.min(i, listLength - 1)
    })
  }, [listLength])

  useEffect(() => {
    if (focusedIndex == null || !flatList) return
    const f = items[focusedIndex]
    if (f) document.getElementById(`rh-open-${f.finding_id}`)?.focus({ preventScroll: false })
    // Only re-run when the *index* changes, or when the id at that index does
    // (a background refresh replacing the object at the same slot must not
    // yank focus away from what the user is looking at).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedIndex])

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const selectAll = useCallback(() => {
    setSelectedIds(prev => {
      const allOn = items.length > 0 && items.every(f => prev.has(f.finding_id))
      if (allOn) return new Set()
      return new Set(items.map(f => f.finding_id))
    })
  }, [items])

  // 👍/👎 is both an honesty signal to the user and the system's training data.
  // There is no dedicated feedback endpoint in api/review.ts yet, so this reuses
  // the status transitions that already exist: acknowledging keeps a confirmed
  // guess in the queue, dismissing with a reason retires a wrong one the same
  // way "Not an issue" does anywhere else in the hub.
  const [feedbackBusyId, setFeedbackBusyId] = useState<string | null>(null)
  const sendFeedback = useCallback(
    async (f: Finding, good: boolean) => {
      setFeedbackBusyId(f.finding_id)
      try {
        if (good) {
          await reviewActions.setStatus(f.finding_id, 'ACKNOWLEDGED')
          toast.success('Thanks — kept in the queue as confirmed.')
        } else {
          await reviewActions.setStatus(f.finding_id, 'DISMISSED', { reason: 'not_an_issue' })
          toast.success('Thanks — that helps the agent calibrate.')
        }
      } catch {
        toast.error('That feedback did not go through.')
      } finally {
        setFeedbackBusyId(null)
      }
    },
    [toast],
  )

  // No paging/notification endpoint exists in api/review.ts (see the design
  // brief compliance notes) — the honest thing this button can do today is copy
  // a link straight to this finding, rather than claim a message was sent that
  // never actually was.
  const notify = useCallback(
    (f: Finding) => {
      const link = `${window.location.origin}${window.location.pathname}?f=${encodeURIComponent(f.finding_id)}`
      void navigator.clipboard?.writeText(link).catch(() => {})
      toast.show({
        tone: 'info',
        title: `Needs ${f.required_capability}`,
        message: 'Link to this finding copied — send it to whoever holds that role.',
      })
    },
    [toast],
  )

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (open || clusterId) return // the drawer, or the cluster table, owns keyboard input while shown
      if (shortcutsOpen || kbdDismissTarget) {
        if (e.key === '?') setShortcutsOpen(false)
        return // Modal's own Escape handling covers the rest
      }
      const target = e.target as HTMLElement | null
      const typing = !!target && (
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable
      )
      if (typing) return

      if (e.key === '?') {
        e.preventDefault()
        setShortcutsOpen(true)
        return
      }
      if (e.key === 'u') {
        if (undo) void reviewActions.undoLast()
        return
      }
      if (!flatList || listLength === 0) return
      const i = focusedIndex ?? 0
      const f = items[i]

      switch (e.key) {
        case 'j':
          e.preventDefault()
          setFocusedIndex(Math.min(i + 1, listLength - 1))
          break
        case 'k':
          e.preventDefault()
          setFocusedIndex(Math.max(i - 1, 0))
          break
        case 'o':
          if (f) openFinding(f.finding_id)
          break
        case 'e':
          // handleAct now decides for itself whether this can be committed
          // from the queue — a suspected finding is no longer routed straight
          // to the drawer, it gets the same confirmation as any other.
          if (f && f.can_act) handleAct(f)
          break
        case 's':
          if (f) void handleStatus(f, 'SNOOZED', { snoozeDays: 7 })
          break
        case 'd':
          if (f) setKbdDismissTarget(f)
          break
        case 'x':
          if (f) toggleSelect(f.finding_id)
          break
        default:
          return
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [
    open, clusterId, shortcutsOpen, kbdDismissTarget, flatList, listLength, focusedIndex,
    items, undo, openFinding, handleAct, handleStatus, toggleSelect,
  ])

  // ── Minimal bulk bar for the `x`-selected rows. The full cluster drill-in
  // bulk bar (impact numbers, excluded count, type-to-confirm) is a separate,
  // richer surface — see FindingTable / the cluster view — this is the general
  // queue's lighter equivalent so `x` has something real to do. ─────────────
  const [bulkBusy, setBulkBusy] = useState(false)
  const runBulk = useCallback(
    async (status: 'SNOOZED' | 'DISMISSED' | 'OPEN', reason?: string) => {
      if (selectedIds.size === 0) return
      setBulkBusy(true)
      try {
        // A selected row is a grouped card standing for every finding of its
        // kind, so act on all of them — the id on the row is one
        // representative and sending only that snoozed 1 of 16.
        const rows = items.filter(f => selectedIds.has(f.finding_id))
        const { ids, truncated } = await reviewActions.expandForBulk(rows, query)
        if (ids.length === 0) return
        const res = await reviewActions.bulk({
          finding_ids: ids, status, reason, snooze_days: status === 'SNOOZED' ? 7 : undefined,
        })
        // The server's own count, not the selection's: one row can be 224
        // findings, and some members can legitimately fail.
        toast.success(
          `${res.counts.updated} updated` +
          (res.counts.failed ? `, ${res.counts.failed} did not go through` : '') +
          (truncated ? ` — capped at ${ids.length}, run it again for the rest` : '') + '.',
        )
        announceResolution(
          status === 'SNOOZED' ? 'Snoozed' : status === 'OPEN' ? 'Back in the queue' : 'Dismissed',
        )
        setSelectedIds(new Set())
        // Selected rows leave the actionable queue on the next load; a frozen
        // queue mid-triage should not vanish these out from under the user.
        void reviewActions.loadQueue(query)
      } catch (e) {
        toast.error(
          e instanceof Error && e.message
            ? `That bulk action did not go through: ${e.message}`
            : 'That bulk action did not go through.',
        )
      } finally {
        setBulkBusy(false)
      }
    },
    [selectedIds, items, toast, announceResolution, query],
  )

  // Once drilled in, the kind is established — so it is stated once, up top,
  // with the totals, and every row below carries only what varies.
  const drillSummary = drillType
    ? {
        title: items[0]?.title ?? drillType.replace(/_/g, ' '),
        count: items.length,
        money: items.reduce((sum, f) => sum + (f.money_at_risk_inr ?? 0), 0),
      }
    : null

  const actionable = counts?.total_actionable ?? 0
  const visible = counts?.total_visible ?? 0

  return (
    <div className="rh">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="rh__header">
        <div className="rh__titlerow">
          {/* The count lives in the To do tile below; repeating it here was
              the same number three times in one glance. */}
          <h1 className="rh__title">Review</h1>
        </div>

        <button
          type="button"
          className="rh__refresh"
          onClick={handleRescan}
          disabled={rescanning}
        >
          <RefreshCw size={14} className={rescanning ? 'rh__spin' : undefined} aria-hidden="true" />
          {rescanning ? 'Scanning…' : 'Rescan'}
        </button>
      </header>

      {/* The one live region on the page. Only user-driven outcomes announce
          here — never the background counts poll (see the comment above). */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">{liveMsg}</p>

      {/* Exposure, workload, throughput, value — in that order, because that is
          the order the questions get asked in: how bad is it, how much of it is
          mine, are we keeping up, was it worth it. */}
      <KpiStrip counts={counts} />

      {/* ── Type filter: pick one, several, or all ───────────────────────
          The same coloured tags the queue's Type column uses, so a filter
          and the rows it shows read as the same thing. */}
      <div className={`rh__typefilter${types.length ? ' rh__typefilter--some' : ''}`}
           role="group" aria-label="Filter by type">
        <button
          type="button"
          className="rh__type rh__type--c0 rh__typechip"
          aria-pressed={types.length === 0}
          onClick={() => setTypes([])}
        >
          All types
          <span className="rh__typechip-n">{actionable}</span>
        </button>
        {FAMILY_ORDER.map(fam => {
          const n = counts?.by_family?.[fam]
          const on = types.includes(fam)
          if (!on && (!n || n.actionable === 0)) return null
          const { label, cat } = familyDisplay(fam)
          return (
            <button
              key={fam}
              type="button"
              className={`rh__type rh__type--c${cat} rh__typechip`}
              aria-pressed={on}
              onClick={() => toggleType(fam)}
            >
              {on && <Check size={12} strokeWidth={3} aria-hidden="true" />}
              {label}
              <span className="rh__typechip-n">{n?.actionable ?? 0}</span>
            </button>
          )
        })}
      </div>

      {/* ── Tabs ───────────────────────────────────────────────────────── */}
      <div className="rh__tabsrow">
        <div className="rh__tabs" role="group" aria-label="Lifecycle">
          {REVIEW_VIEWS.map(v => (
            <button
              key={v}
              type="button"
              className="rh__tab"
              aria-pressed={view === v}
              onClick={() => navigate(reviewPath(v))}
            >
              {v === 'queue' ? 'Open' : v === 'snoozed' ? 'Snoozed' : v === 'resolved' ? 'Resolved' : 'Activity'}
              {v === 'snoozed' && counts?.snoozed ? ` (${counts.snoozed})` : ''}
            </button>
          ))}
        </div>

        {/* The whole "show everything / overwhelm nobody" argument, in one
            line, on the same row as the tabs rather than a full-width strip
            of its own. The escape hatch is a real destination — Browse
            (design brief artboard 8), the unbounded, filterable table —
            rather than widening this page's own scope in place. */}
        {view === 'queue' && counts && (visible > 0 || actionable > 0) && (
          <div className="rh__scope-inline">
            <span>{scopeLine(items.length, actionable, visible)}</span>
            {visible > actionable && (
              <button type="button" className="rh__scope-more" onClick={() => navigate(reviewBrowsePath())}>
                Show everything
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── The queue ──────────────────────────────────────────────────── */}
      {view === 'activity' ? <ReviewActivity /> : (
      <div className="rh__wrap">
        {clusterId ? (
          clusterHost ? (
            <ClusterDrillIn
              finding={clusterHost}
              onOpenMember={openFinding}
              onBack={() => {
                // Leaving the cluster table for the finding it came from — one
                // params update, not "clear cluster" then "set f" as two, which
                // would race (the second call's clone of `params` predates the
                // first's write and would silently undo it).
                const next = new URLSearchParams(params)
                next.delete('cluster')
                next.set('f', clusterId!)
                setParams(next)
              }}
            />
          ) : (
            <div className="rh__state">
              <SkeletonRows rows={6} />
            </div>
          )
        ) : (
        <>
        {view === 'queue' && pendingCount > 0 && (
          <button type="button" className="rh__pending" onClick={() => reviewActions.showPending()}>
            {pendingCount} new since you opened this — show
          </button>
        )}

        {loading && items.length === 0 && (
          <div className="rh__state">
            <SkeletonRows rows={6} />
          </div>
        )}

        {error && items.length === 0 && (
          <div className="rh__state">
            <ErrorState
              title="Could not load your findings"
              message={error}
              onRetry={() => void reviewActions.loadQueue(query)}
            />
          </div>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="rh__state">
            {view === 'queue' && counts && counts.total_visible === 0 ? (
              <ZeroState counts={counts} onRescan={handleRescan} />
            ) : (
              <EmptyState
                title={view === 'resolved' ? 'Nothing closed yet' : 'Nothing here'}
                message={
                  view === 'resolved'
                    ? 'Findings you resolve or dismiss will be listed here.'
                    : view === 'snoozed'
                      ? 'Nothing is snoozed.'
                      : 'No findings match this filter.'
                }
              />
            )}
          </div>
        )}

        {items.length > 0 && drillSummary && (
          <>
            <div className="rh__drill">
              <div>
                <p className="rh__drill-title">{drillSummary.title}</p>
                <p className="rh__drill-sub">
                  {drillSummary.count} record{drillSummary.count === 1 ? '' : 's'}
                  {drillSummary.money > 0 && ` · ${formatMoneyCompact(drillSummary.money)} at risk`}
                </p>
              </div>
              <button
                type="button"
                className="rh__scope-more"
                onClick={() => setParam('type', null)}
              >
                Back to everything
              </button>
            </div>
            <FindingTable findings={items} onOpen={openFinding} onAct={handleAct} />
          </>
        )}

        {items.length > 0 && !drillSummary && (
          <QueueTable
            findings={items}
            busy={busy}
            selectedIds={selectedIds}
            rowErrors={rowErrors}
            sort={sort}
            direction={direction}
            onSort={changeSort}
            onOpen={openFinding}
            onAct={handleAct}
            onToggleSelect={toggleSelect}
            onSelectAll={selectAll}
            onNotify={notify}
            onFeedback={(f, good) => void sendFeedback(f, good)}
            feedbackBusyId={feedbackBusyId}
            entityHref={entityHref}
            onOpenEntity={openEntity}
          />
        )}

        {items.length > 0 && (
          <Pager
            label="Pages of findings"
            page={page}
            perPage={perPage}
            total={total ?? items.length}
            loading={loading}
            onGo={goToPage}
            onPerPage={n => {
              try { localStorage.setItem(PAGE_SIZE_KEY, String(n)) } catch { /* private mode */ }
              const next = new URLSearchParams(params)
              next.set('per', String(n))
              next.delete('page')  // a different page size starts again at page 1
              setParams(next)
            }}
          />
        )}

        {selectedIds.size > 0 && (
          <div className="rh__bulkbar">
            <span className="rh__bulkbar-count">{selectedIds.size} selected</span>
            <span className="rh__bulkbar-actions">
              {/* On the Snoozed tab the only push-it-away action is the one
                  that has already been taken. What is missing there is the way
                  back: a snooze expires on its own, so without this the only
                  way to act on something early was to open all 25 and reopen
                  them one at a time. */}
              {view === 'snoozed' ? (
                <Button size="sm" variant="secondary" loading={bulkBusy}
                        onClick={() => void runBulk('OPEN')}>
                  Unsnooze
                </Button>
              ) : (
                <Button size="sm" variant="secondary" loading={bulkBusy}
                        onClick={() => void runBulk('SNOOZED')}>
                  Snooze a week
                </Button>
              )}
              <Button size="sm" variant="ghost" loading={bulkBusy}
                      onClick={() => void runBulk('DISMISSED', 'not_an_issue')}>
                Dismiss
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelectedIds(new Set())}>
                Clear
              </Button>
            </span>
          </div>
        )}

        {undo && <UndoStrip title={undo.title} expiresAt={undo.expiresAt} />}
        </>
        )}
      </div>
      )}

      {open && (
        <FindingDrawer
          finding={open}
          busy={busy.has(open.finding_id)}
          onClose={closeDrawer}
          // Lines of a rolled-up card are read under the same view the row was
          // counted in; Resolved shows every finding on its own.
          lineScope={query.status === 'resolved' ? undefined : { status: query.status, scope: query.scope }}
          onStatus={(status, opts) => void handleStatus(open, status, opts)}
          // The write landed on the bill; whether the finding survives it is
          // the adapter's call on the next scan, so this only refetches.
          onApplied={() => {
            void reviewActions.loadQueue(query)
            void reviewActions.loadCounts()
          }}
          onAct={(action, values) => void runFix(open, action, values)}
          onNavigate={href => {
            // Carry the finding id so the destination can offer a way back —
            // without it every deep link ends the triage session.
            navigate(focusPath(href, undefined, open.finding_id))
          }}
        />
      )}

      <ShortcutsSheet open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

      {confirming && (
        <ActionConfirm
          action={confirming.action}
          onCancel={() => setConfirming(null)}
          onConfirm={action => {
            const { finding } = confirming
            setConfirming(null)
            void runFix(finding, action)
          }}
        />
      )}

      <DismissDialog
        open={!!kbdDismissTarget}
        onCancel={() => setKbdDismissTarget(null)}
        onConfirm={(reason: DismissReason) => {
          const f = kbdDismissTarget
          setKbdDismissTarget(null)
          if (f) void handleStatus(f, 'DISMISSED', {
            reason, suppressScope: reason === 'never_show_type' ? 'type_tenant_wide' : undefined,
          })
        }}
      />
    </div>
  )
}

/**
 * The 30-second undo strip: pinned below the queue, ticking down, never a
 * toast that has already scrolled out of view by the time someone wants it.
 */
function UndoStrip({ title, expiresAt }: { title: string; expiresAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [])
  const secondsLeft = Math.max(0, Math.ceil((expiresAt - now) / 1000))

  useEffect(() => {
    if (secondsLeft === 0) reviewActions.dismissUndo()
  }, [secondsLeft])

  return (
    <div className="rh__undo" role="status">
      <span>Done — {title}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)' }}>
        <span className="rh__undo-count tnum" aria-hidden="true">{secondsLeft}s</span>
        <span style={{ display: 'flex', gap: 'var(--sp-2)' }}>
          <button
            type="button"
            className="rh__undo-btn"
            onClick={() => void reviewActions.undoLast()}
          >
            Undo
          </button>
          <button
            type="button"
            className="rh__undo-btn"
            onClick={() => reviewActions.dismissUndo()}
          >
            Dismiss
          </button>
        </span>
      </span>
    </div>
  )
}

/**
 * The pager.
 *
 * Numbered, not "load more". Grouping used to keep the queue to about twelve
 * rows; one row per finding means 636 findings are 26 pages, and an
 * ever-growing list gives a user no way to say where they are, no way to send
 * someone else there, and no end. "76–100 of 636" answers "how far through am
 * I", which is the question the old explicit-button comment was already
 * trying to answer.
 *
 * The window is first, last and the neighbours of the current page, so the
 * control stays one line at 26 pages and at 260.
 */

/**
 * The zero state reads as an achievement, not an absence. Three things stop it
 * looking like a failed fetch: evidence that a scan ran, evidence of value
 * delivered, and something to do.
 */
function ZeroState({
  counts,
  onRescan,
}: {
  counts: NonNullable<ReturnType<typeof useReviewCounts>>
  onRescan: () => void
}) {
  const scanned = counts.last_scan_at
    ? new Date(counts.last_scan_at).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : null

  return (
    <div className="rh__empty">
      <span className="rh__empty-mark" aria-hidden="true"><CheckCircle2 size={22} /></span>
      <h2 className="rh__empty-title">Nothing needs you right now.</h2>
      <p className="rh__empty-sub">
        {scanned ? `Last checked ${scanned}.` : 'No checks have run yet.'}
      </p>
      <div className="rh__empty-stats">
        <span><b className="tnum">{counts.resolved_this_week}</b> resolved this week</span>
        <span>
          <b className="tnum">{formatMoneyCompact(counts.protected_this_week_inr)}</b> protected
        </span>
      </div>
      <div style={{ marginTop: 'var(--sp-5)' }}>
        <Button variant="secondary" size="sm" onClick={onRescan}>Rescan now</Button>
      </div>
    </div>
  )
}
