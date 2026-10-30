import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  AlertCircle, ArrowLeft, Check, CheckCircle2, ChevronDown, Download, Eye, FolderInput, Forward, Mail, Paperclip, Plus,
  Receipt, RefreshCw, Reply, ReplyAll, RotateCcw, Search, Send, Sparkles, UserPlus, X,
} from 'lucide-react'
import type { Bill, EmailConnection, VendorContact } from '../../api/invoices'
import {
  draftInboxEmail, fetchDocumentTypes, fetchVendorInboxThread, inboxAttachmentUrl, saveInboxAttachment, sendInboxEmail,
  setThreadDone, summarizeVendorThread,
  type DocType, type InboxAttachment, type InboxMessage, type InboxThread, type VendorInbox as Inbox,
} from '../../api/vendorCard'
import { downloadAuthed, fetchAuthedObjectUrl } from '../../api/authedFiles'
import PdfFullscreen from '../PdfFullscreen'
import { Avatar, OriginTag, relDay, senderName, today } from './VendorViews'
import { aiIntents, billsMentioned, type IntentGroup } from './aiIntents'
import { billMoney } from './vendorStats'

/**
 * Every conversation with the vendor, worked from here: read, reply, reply
 * all, forward, file attachments into Documents, draft with AI and mark done,
 * so nobody has to open their mailbox to deal with a vendor.
 */

type Filter = 'all' | 'needs_reply' | 'waiting' | 'done'
type Mode = 'reply' | 'replyAll' | 'forward' | 'new'
const NEW = '__new'
const FILTERS: [Filter, string][] = [['all', 'All'], ['needs_reply', 'Needs reply'], ['waiting', 'Waiting on them'], ['done', 'Done']]
const EMAIL_RE = /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/

/** The message being forwarded: shown as a quote under the note, added to the email on send. */
interface Forwarded { from: string; when: string; subject: string; to: string; body: string }
interface Draft { mode: Mode; to: string[]; cc: string[]; showCc: boolean; subject: string; body: string; fwd?: Forwarded | null }

const whenOf = (m: InboxMessage) => m.internal_date
  ? new Date(m.internal_date).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' })
  : m.date

/** The email text of a forward: the note, then the original, the way mail clients quote it. */
function forwardText(note: string, f: Forwarded): string {
  return [note.trim(), '', '---------- Forwarded message ----------', `From: ${f.from}`, `Date: ${f.when}`,
    `Subject: ${f.subject}`, f.to ? `To: ${f.to}` : null, '', f.body].filter(l => l !== null).join('\n').replace(/^\n+/, '')
}

/** A message in the open thread; one just sent carries how its send is going. */
type LocalMsg = InboxMessage & { state?: 'sending' | 'sent' }

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** 'Today', 'Yesterday', or the date: the dividers between days in a thread. */
function dayLabel(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms), now = new Date()
  const key = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`
  const y = new Date(now); y.setDate(now.getDate() - 1)
  if (key(d) === key(now)) return 'Today'
  if (key(d) === key(y)) return 'Yesterday'
  return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' })
}

function errText(e: unknown, fallback: string): string {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback
}

/** "Name <a@x>, b@y" → [{name, email}] */
function people(header?: string): { name: string; email: string }[] {
  if (!header) return []
  return header.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(part => {
    const m = part.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/)
    const email = (m ? m[2] : part).trim().toLowerCase()
    return { name: (m?.[1] ?? '').trim(), email }
  }).filter(p => p.email.includes('@'))
}
const addrOf = (header: string) => people(header)[0]?.email ?? header.trim().toLowerCase()

const draftKey = (sid: string, tid: string) => `vc-draft:${sid}:${tid}`
function loadDraft(sid: string, tid: string): Partial<Draft> | null {
  try { const raw = localStorage.getItem(draftKey(sid, tid)); return raw ? JSON.parse(raw) : null } catch { return null }
}
function saveDraft(sid: string, tid: string, d: Draft | null) {
  try {
    if (!d || (!d.body.trim() && !d.subject.trim())) localStorage.removeItem(draftKey(sid, tid))
    else localStorage.setItem(draftKey(sid, tid), JSON.stringify(d))
  } catch { /* drafts are a convenience */ }
}

const STATUS_CHIP: Record<InboxThread['status'], ReactNode> = {
  needs_reply: <span className="vc__chip vc__chip--xs vc__chip--warn"><i className="vc__d" />Needs reply</span>,
  waiting: <span className="vc__chip vc__chip--xs">Waiting on them</span>,
  done: <span className="vc__chip vc__chip--xs vc__chip--ok"><CheckCircle2 size={11} />Done</span>,
}

export default function VendorInbox({
  supplierId, inbox, loading, emailConn, connecting, contacts, activeThreadId, onActiveThread,
  onReload, onConnect, onAddContact, attachFiles, setAttachFiles, onPickBills, notify, composeTo = null,
  bills = [], billFiles = {}, onOpenBill, onDocsChanged, brandId, signer,
}: {
  supplierId: string
  inbox: Inbox | null
  loading: boolean
  emailConn: EmailConnection | null
  connecting: boolean
  contacts: VendorContact[]
  activeThreadId: string | null
  onActiveThread: (id: string | null) => void
  onReload: () => void
  onConnect: () => void
  onAddContact: (email: string) => void
  attachFiles: File[]
  setAttachFiles: (f: File[] | ((prev: File[]) => File[])) => void
  onPickBills: () => void
  notify: (msg: string, type?: 'error' | 'info') => void
  /** A contact to address a new email to. */
  composeTo?: string | null
  /** The vendor's bills: bill numbers in emails link to them. */
  bills?: Bill[]
  /** Attached file name → invoice id, for files attached from the bill picker. */
  billFiles?: Record<string, string>
  onOpenBill?: (invoiceId: string) => void
  /** An attachment was filed into Documents. */
  onDocsChanged?: () => void
  brandId?: string | null
  /** The user's name, for AI drafts' sign-off. */
  signer?: string
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<LocalMsg[] | null>(null)
  const [threadError, setThreadError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [summary, setSummary] = useState<{ summary: string; actions: { label: string; detail: string }[] } | null>(null)
  const [summaryError, setSummaryError] = useState('')
  const [summarizing, setSummarizing] = useState(false)
  const [draft, setDraft] = useState<Draft>({ mode: 'reply', to: [], cc: [], showCc: false, subject: '', body: '' })
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState('')
  const [aiOpen, setAiOpen] = useState(false)
  const [aiIntent, setAiIntent] = useState('')
  const [drafting, setDrafting] = useState(false)
  const [statusOverride, setStatusOverride] = useState<Record<string, InboxThread['status']>>({})
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null)
  const [docTypes, setDocTypes] = useState<string[]>([])
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const readRef = useRef<HTMLDivElement>(null)
  // The AI draft types itself in; the composer shimmers while it does.
  const [typing, setTyping] = useState(false)
  const [justSent, setJustSent] = useState(false)
  // "Marked done · Undo", for a few seconds after marking one done.
  const [doneBanner, setDoneBanner] = useState<string | null>(null)
  // The composer stays a slim bar until it's wanted, so the thread gets the room.
  const [composerOpen, setComposerOpen] = useState(false)
  // Messages already there when a thread opens animate in turn; later ones arrive on their own.
  const firstPaint = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const [hl, setHl] = useState<{ y: number; h: number } | null>(null)
  // "Write with AI" over text already typed asks first; then one Undo puts the old text back.
  const [aiAsk, setAiAsk] = useState<string | null>(null)
  const [aiUndo, setAiUndo] = useState<string | null>(null)
  // Text left in To / Cc that isn't an address blocks Send instead of being dropped.
  const [rcptInvalid, setRcptInvalid] = useState({ to: false, cc: false })
  // Forward is still fetching the original's files.
  const [fwdLoading, setFwdLoading] = useState(0)
  const fwdFiles = useRef<File[]>([])
  const fwdRun = useRef(0)
  // Refs so a guard sees the latest values even inside an await or a fast double key press.
  const sendingRef = useRef(false)
  const threadRef = useRef(activeThreadId)
  threadRef.current = activeThreadId
  // Which thread the draft in state belongs to, so autosave never files it under another one.
  const draftFor = useRef<string | null>(null)
  const attachCount = useRef(attachFiles.length)
  attachCount.current = attachFiles.length
  const lastThread = useRef<string | null>(null)

  const me = (inbox?.me || emailConn?.email || '').toLowerCase()
  const threads = useMemo(
    () => (inbox?.threads ?? []).map(t => (statusOverride[t.thread_id] ? { ...t, status: statusOverride[t.thread_id] } : t)),
    [inbox, statusOverride],
  )
  const q = query.trim().toLowerCase()
  const searched = q
    ? threads.filter(t => [t.subject, t.snippet, t.counterparty?.name, t.counterparty?.email, ...t.participants.map(p => `${p.name} ${p.email}`)]
        .join(' ').toLowerCase().includes(q))
    : threads
  const count = (f: Filter) => searched.filter(t => f === 'all' || t.status === f).length
  const shown = searched.filter(t => filter === 'all' || t.status === filter)
  const active = activeThreadId && activeThreadId !== NEW ? threads.find(t => t.thread_id === activeThreadId) ?? null : null
  const isNew = activeThreadId === NEW

  // What the email is about: bills attached to it, and bills the conversation mentions.
  const billById = useMemo(() => new Map(bills.map(b => [b.invoice_id, b])), [bills])
  const billFor = (f: File) => { const id = billFiles[f.name]; return id ? billById.get(id) : undefined }
  const attachedBills = attachFiles.map(billFor).filter((b): b is Bill => !!b)
  const mentionedBills = useMemo(
    () => billsMentioned(bills, [...(messages ?? []).map(m => m.body), active?.subject ?? '', draft.subject]),
    [bills, messages, active, draft.subject],
  )
  const intentGroups = aiIntents({ attached: attachedBills, mentioned: mentionedBills, now: today(), hasThread: !!active && !isNew })

  const suggestions = useMemo(() => {
    const s = new Set<string>()
    contacts.forEach(c => c.email && s.add(c.email.toLowerCase()))
    active?.participants.forEach(p => !p.me && s.add(p.email))
    return [...s]
  }, [contacts, active])

  useEffect(() => { fetchDocumentTypes().then(t => setDocTypes(t.map(x => x.label))).catch(() => {}) }, [])

  // Open the newest thread when the list arrives, unless one was asked for.
  useEffect(() => {
    if (!activeThreadId && threads.length && window.matchMedia?.('(min-width: 961px)').matches) onActiveThread(threads[0].thread_id)
  }, [threads.length])  // eslint-disable-line react-hooks/exhaustive-deps

  // Load the open thread.
  useEffect(() => {
    setMessages(null); setThreadError(''); setSummary(null); setSummaryError(''); setExpanded(false); setSendError(''); setAiOpen(false)
    setComposerOpen(isNew); setDoneBanner(null); setAiAsk(null); setAiUndo(null)
    fwdRun.current++; fwdFiles.current = []; setFwdLoading(0)
    // Attachments belong to the thread they were added in: don't let them ride along to another one.
    if (activeThreadId && lastThread.current && lastThread.current !== activeThreadId && attachCount.current) {
      const n = attachCount.current
      setAttachFiles([])
      // Mid-send they're already on their way with that email; nothing to tell.
      if (!sendingRef.current) notify(`Removed ${n} attachment${n === 1 ? '' : 's'} added in the other conversation`, 'info')
    }
    if (activeThreadId) lastThread.current = activeThreadId
    if (!activeThreadId || isNew) return
    let live = true
    fetchVendorInboxThread(supplierId, activeThreadId)
      .then(r => { if (live) { firstPaint.current = r.messages.length; setMessages(r.messages) } })
      .catch(e => live && setThreadError(errText(e, 'Couldn’t load this conversation.')))
    return () => { live = false }
  }, [activeThreadId, supplierId])  // eslint-disable-line react-hooks/exhaustive-deps

  // The selected thread's highlight slides to whichever one is picked.
  useLayoutEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('.vc__th.on')
    setHl(el ? { y: el.offsetTop, h: el.offsetHeight } : null)
  }, [activeThreadId, filter, query, inbox, statusOverride])

  useEffect(() => {
    if (!doneBanner) return
    const t = window.setTimeout(() => setDoneBanner(null), 6000)
    return () => window.clearTimeout(t)
  }, [doneBanner])

  // Open at the newest message, like a chat; and follow it when one is sent.
  useEffect(() => {
    const el = readRef.current
    if (el && messages?.length) el.scrollTop = el.scrollHeight
  }, [messages?.length])

  // Composer: a saved draft for this thread, or a fresh reply to the right people.
  // A new thread always starts from its own saved draft or an empty body, never the last thread's text.
  useEffect(() => {
    draftFor.current = activeThreadId
    if (!activeThreadId) return
    const saved = loadDraft(supplierId, activeThreadId)
    if (isNew) {
      const c = contacts.find(x => x.id === composeTo) ?? contacts.find(x => x.is_primary && x.email) ?? contacts.find(x => x.email)
      setDraft({ mode: 'new', to: c?.email ? [c.email.toLowerCase()] : [], cc: [], showCc: false, subject: '', body: '', ...saved })
    } else if (active) {
      setDraft({ ...replyDraft(active, 'reply'), ...saved })
    } else {
      setDraft({ mode: 'reply', to: [], cc: [], showCc: false, subject: '', body: '', ...saved })
    }
  }, [activeThreadId, composeTo])  // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the draft safe as it's typed.
  useEffect(() => {
    if (!activeThreadId || draftFor.current !== activeThreadId) return
    const t = window.setTimeout(() => saveDraft(supplierId, activeThreadId, draft), 400)
    return () => window.clearTimeout(t)
  }, [draft, activeThreadId, supplierId])

  /** Who to answer and how. The body starts empty unless the caller carries it over (same thread only). */
  function replyDraft(t: InboxThread, mode: 'reply' | 'replyAll', msg?: InboxMessage, body = ''): Draft {
    const lastTheirs = msg && !msg.outbound ? addrOf(msg.from)
      : [...(messages ?? [])].reverse().map(m => addrOf(m.from)).find(a => a && a !== me)
    const to = [lastTheirs || t.counterparty?.email].filter((x): x is string => !!x)
    const everyone = msg
      ? [...people(msg.from), ...people(msg.to), ...people(msg.cc)].map(p => p.email)
      : t.participants.map(p => p.email)
    const cc = mode === 'replyAll' ? [...new Set(everyone)].filter(a => a !== me && !to.includes(a)) : []
    return { mode, to, cc, showCc: cc.length > 0, subject: '', body }
  }

  const startReply = (mode: 'reply' | 'replyAll', msg?: InboxMessage) => {
    if (!active) return
    // Leaving a forward: its copies of the original's files go with it; the note stays.
    if (draft.mode === 'forward') dropForwarded()
    setDraft(replyDraft(active, mode, msg, draft.body))
    setComposerOpen(true)
    window.setTimeout(() => bodyRef.current?.focus(), 0)
  }

  /** Take back the files a forward brought along, leaving anything the user attached. */
  function dropForwarded() {
    fwdRun.current++; setFwdLoading(0)
    const mine = fwdFiles.current
    fwdFiles.current = []
    if (mine.length) setAttachFiles(prev => prev.filter(f => !mine.includes(f)))
  }

  const startForward = async (msg: InboxMessage) => {
    if (!active) return
    dropForwarded()
    const run = fwdRun.current
    const subj = (msg.subject || active.subject).replace(/^(re|fwd?):\s*/i, '')
    // What's typed becomes the forward's note, and attached files stay, rather than being wiped.
    setDraft(d => ({
      mode: 'forward', to: [], cc: [], showCc: false, subject: `Fwd: ${subj}`, body: d.body,
      fwd: { from: msg.from, when: whenOf(msg), subject: msg.subject || active.subject, to: msg.to, body: msg.body },
    }))
    setComposerOpen(true)
    // Bring the original attachments along; Send waits for them.
    const atts = msg.attachments ?? []
    if (!atts.length) return
    setFwdLoading(atts.length)
    for (const a of atts) {
      try {
        const url = await fetchAuthedObjectUrl(inboxAttachmentUrl(supplierId, active.thread_id, msg.id, a))
        const blob = await (await fetch(url)).blob()
        URL.revokeObjectURL(url)
        if (run !== fwdRun.current) return  // moved on: another forward, a reply or another thread
        const f = new File([blob], a.filename, { type: a.mime_type || blob.type })
        fwdFiles.current = [...fwdFiles.current, f]
        setAttachFiles(prev => [...prev, f])
      } catch { if (run === fwdRun.current) notify(`Couldn’t attach ${a.filename}`, 'error') }
      if (run === fwdRun.current) setFwdLoading(n => Math.max(0, n - 1))
    }
  }

  const summarize = async () => {
    if (!active) return
    setSummarizing(true); setSummaryError('')
    try { setSummary(await summarizeVendorThread(supplierId, active.thread_id)) }
    // An error is an error, not a "Copilot summary".
    catch (e) { setSummaryError(errText(e, 'Couldn’t summarise this conversation.')) }
    finally { setSummarizing(false) }
  }

  /** Over typed text, ask first: replace it or add the draft below. */
  const writeWithAi = (intent: string) => {
    setAiOpen(false)
    if (draft.body.trim()) { setAiAsk(intent); setComposerOpen(true); return }
    void runAi(intent, 'replace')
  }

  const runAi = async (intent: string, how: 'replace' | 'below') => {
    setAiAsk(null)
    const before = draft.body
    setDrafting(true)
    try {
      const text = await draftInboxEmail(supplierId, {
        threadId: active?.thread_id ?? null, intent, signer,
        attachedBillIds: attachedBills.map(b => b.invoice_id), billIds: mentionedBills.map(b => b.invoice_id),
      })
      setComposerOpen(true)
      await typeIn(text, how === 'below' && before.trim() ? `${before.trimEnd()}\n\n` : '')
      setAiUndo(before)
      window.setTimeout(() => bodyRef.current?.focus(), 0)
    } catch (e) { notify(errText(e, 'Couldn’t write a draft just now.'), 'error') }
    finally { setDrafting(false); setAiIntent('') }
  }

  /** Put AI text in the box a few characters a frame, so it reads as being written. */
  function typeIn(text: string, prefix = ''): Promise<void> {
    if (reducedMotion() || typeof requestAnimationFrame === 'undefined') {
      setDraft(d => ({ ...d, body: prefix + text }))
      return Promise.resolve()
    }
    setTyping(true)
    const total = Math.min(1100, 250 + text.length * 6)
    const t0 = performance.now()
    return new Promise(resolve => {
      const step = () => {
        const n = Math.min(text.length, Math.max(1, Math.ceil(((performance.now() - t0) / total) * text.length)))
        setDraft(d => ({ ...d, body: prefix + text.slice(0, n) }))
        if (n < text.length) requestAnimationFrame(step)
        else { setTyping(false); resolve() }
      }
      requestAnimationFrame(step)
    })
  }

  const toggleDone = async (t: InboxThread) => {
    const done = t.status !== 'done'
    setStatusOverride(s => ({ ...s, [t.thread_id]: done ? 'done' : (t.last_outbound ? 'waiting' : 'needs_reply') }))
    try {
      await setThreadDone(supplierId, t.thread_id, done)
      if (done) setDoneBanner(t.thread_id); else { setDoneBanner(null); notify('Reopened', 'info') }
      onReload()
    }
    catch (e) {
      setStatusOverride(s => { const n = { ...s }; delete n[t.thread_id]; return n })
      notify(errText(e, 'Couldn’t update the conversation.'), 'error')
    }
  }

  const send = async () => {
    // Cmd/Ctrl+Enter reaches here too: never twice, never mid-AI, never before a forward's files arrive.
    if (sendingRef.current || sending || typing || drafting || fwdLoading) return
    const forwarding = draft.mode === 'forward'
    if (rcptInvalid.to || rcptInvalid.cc) { setSendError('Fix or remove the address that isn’t valid.'); return }
    if (!draft.to.length) { setSendError('Add someone to send it to.'); return }
    if ((isNew || forwarding) && !draft.subject.trim()) { setSendError('Add a subject.'); return }
    if (!draft.body.trim() && !(forwarding && draft.fwd)) { setSendError('Write a message first.'); return }
    sendingRef.current = true
    setSending(true); setSendError(''); setAiUndo(null)
    const key = activeThreadId!
    const sentDraft = draft
    const threadId = isNew || forwarding ? null : active?.thread_id ?? null
    const tempId = `sending-${Date.now()}`
    const sentBody = draft.body
    if (threadId && active) {
      setMessages(ms => [...(ms ?? []), {
        id: tempId, outbound: true, from: me, to: draft.to.join(', '), cc: draft.cc.join(', '),
        date: '', internal_date: Date.now(), subject: '', body: sentBody, attachments: [], state: 'sending',
      }])
      setDraft(d => ({ ...d, body: '' }))
    }
    try {
      const res = await sendInboxEmail(supplierId, {
        to: draft.to, cc: draft.cc, subject: isNew || forwarding ? draft.subject : undefined,
        body: forwarding && draft.fwd ? forwardText(draft.body, draft.fwd) : draft.body, threadId, files: attachFiles,
      })
      saveDraft(supplierId, key, null)
      notify(`Sent to ${res.to.join(', ')}`, 'info')
      if (threadId && active) setStatusOverride(s => ({ ...s, [active.thread_id]: 'waiting' }))
      // Switched threads while it was sending: leave the one now open alone.
      if (threadRef.current !== key) { onReload(); return }
      setAttachFiles([]); fwdFiles.current = []
      setJustSent(true); window.setTimeout(() => setJustSent(false), 1600)
      if (threadId && active) {
        setMessages(ms => (ms ?? []).map(m => (m.id === tempId ? { ...m, state: 'sent', from: res.from || me } : m)))
        window.setTimeout(() => setMessages(ms => ms && ms.map(m => (m.id === tempId ? { ...m, state: undefined } : m))), 2200)
        // Back to a one-line "Reply to …" bar, ready for the next one.
        setDraft(d => replyDraft(active, 'reply', undefined, d.body))
        setComposerOpen(false)
      } else if (res.thread_id) {
        onActiveThread(res.thread_id)
      }
      onReload()
    } catch (e) {
      if (threadRef.current !== key) {
        // Moved on: keep the text as that thread's draft rather than dropping it into this one.
        saveDraft(supplierId, key, sentDraft)
        notify(errText(e, 'Couldn’t send. Your text is saved as a draft in that conversation.'), 'error')
        return
      }
      // Take the bubble back out and give the text back.
      setMessages(ms => ms && ms.filter(m => m.id !== tempId))
      setDraft(d => ({ ...d, body: d.body || sentBody }))
      setSendError(errText(e, 'Couldn’t send. Try again.'))
    } finally { sendingRef.current = false; setSending(false) }
  }

  const connected = !!emailConn?.connected || !!inbox?.connected
  const list = messages ?? []
  const hidden = !expanded && list.length > 3 ? list.length - 3 : 0
  const visible = hidden ? [list[0], ...list.slice(-2)] : list

  return (
    <>
      {preview && <PdfFullscreen url={preview.url} name={preview.name} onClose={() => setPreview(null)} />}

      {/* An error wins over "Connect": a failed load must not pass for a mailbox that was never connected. */}
      {inbox?.error ? (
        <div className="vc__syncbar vc__syncbar--warn" role="alert">
          <AlertCircle size={14} /><span className="vc__grow">{inbox.error}</span>
          <button className="vc__btn vc__btn--sm" onClick={onReload} disabled={loading}>Try again</button>
          {connected && <button className="vc__btn vc__btn--sm" onClick={onConnect}>Reconnect</button>}
        </div>
      ) : !connected ? (
        <div className="vc__syncbar vc__syncbar--off">
          <Mail size={14} />
          <span className="vc__grow">Connect your mailbox to see and answer every email with this vendor here, including the ones they start.</span>
          <button className="vc__btn vc__btn--sm vc__btn--primary" disabled={connecting} onClick={onConnect}>{connecting ? 'Connecting…' : 'Connect Gmail'}</button>
        </div>
      ) : null}

      <div className={`vc__mail${activeThreadId ? ' has-active' : ''}`}>
        {/* ── Conversations ── */}
        <div className="vc__mail-list">
          <div className="vc__mail-top">
            <label className="vc__search vc__grow"><Search size={14} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search conversations" /></label>
            <button className="vc__iconbtn" title="Refresh" aria-label="Refresh" onClick={onReload} disabled={loading}><RefreshCw size={15} className={loading ? 'vc__spin' : ''} /></button>
          </div>
          <div className="vc__mail-filters">
            {FILTERS.map(([k, l]) => (
              <button key={k} className={`vc__fchip${filter === k ? ' on' : ''}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}<span key={count(k)} className="vc__c vc__bump">{count(k)}</span></button>
            ))}
          </div>
          <div className="vc__threads" ref={listRef}>
            {hl && shown.length > 0 && <span className="vc__th-hl" style={{ transform: `translateY(${hl.y}px)`, height: hl.h }} aria-hidden="true" />}
            {loading && !inbox ? <ListSkeleton />
              : !shown.length ? (
                <div className="vc__empty vc__fadein">
                  <div>
                    <div className="vc__empty-ic">{q ? <Search size={18} /> : filter === 'done' ? <CheckCircle2 size={18} /> : <Mail size={18} />}</div>
                    {q ? 'No conversations match.' : threads.length ? 'Nothing here.' : inbox?.error ? 'Couldn’t load conversations.' : connected ? 'No emails with this vendor yet.' : 'Nothing sent from Onsumm yet.'}
                  </div>
                </div>
              )
              : shown.map((t, i) => <ThreadItem key={t.thread_id} t={t} i={i} on={t.thread_id === activeThreadId} onClick={() => onActiveThread(t.thread_id)} />)}
          </div>
          <div className="vc__mail-new">
            <button className="vc__btn vc__btn--sm vc__btn--primary vc__w100" disabled={!connected} onClick={() => onActiveThread(NEW)}><Plus size={14} />New email</button>
          </div>
        </div>

        {/* ── Conversation ── */}
        <section className="vc__read">
          {isNew ? (
            <div className="vc__read-h">
              <button className="vc__iconbtn vc__iconbtn--sm vc__back" aria-label="Back to conversations" onClick={() => onActiveThread(null)}><ArrowLeft size={14} /></button>
              <div className="vc__grow"><div className="vc__read-subj">New email</div><div className="vc__read-ppl">From {me || 'your mailbox'}</div></div>
            </div>
          ) : !active ? (
            <div className="vc__empty"><div><div className="vc__empty-ic"><Mail size={20} /></div><div className="vc__empty-t">Pick a conversation</div>Or start a new email to this vendor.</div></div>
          ) : (
            <>
              <div className="vc__read-h vc__pane-in" key={`h-${active.thread_id}`}>
                <button className="vc__iconbtn vc__iconbtn--sm vc__back" aria-label="Back to conversations" onClick={() => onActiveThread(null)}><ArrowLeft size={14} /></button>
                <div className="vc__grow vc__min0">
                  <div className="vc__read-subj">{active.subject}</div>
                  <div className="vc__read-ppl">
                    {STATUS_CHIP[active.status]}<OriginTag t={active} />
                    <People list={active.participants.filter(p => !p.me)} />
                  </div>
                </div>
                <div className="vc__read-acts">
                  {/* Titles: at phone width these shrink to bare icons. */}
                  <button className="vc__btn vc__btn--sm vc__btn--ghost vc__brandc" title="Summarise" onClick={summarize} disabled={summarizing || !messages?.length}><Sparkles size={14} />{summarizing ? 'Summarising…' : 'Summarise'}</button>
                  <button className="vc__btn vc__btn--sm" title={active.status === 'done' ? 'Reopen' : 'Mark done'} onClick={() => toggleDone(active)}>
                    {active.status === 'done' ? <><RotateCcw size={14} />Reopen</> : <><CheckCircle2 size={14} />Mark done</>}
                  </button>
                </div>
              </div>
              <div className="vc__read-body vc__pane-in" ref={readRef} key={`b-${active.thread_id}`}>
                {doneBanner === active.thread_id && (
                  <div className="vc__donebar" role="status">
                    <span className="vc__donecheck"><Check size={13} strokeWidth={3} /></span>
                    <span className="vc__grow">Marked done. It comes back if they write again.</span>
                    <button className="vc__lnk vc__lnk--brand" onClick={() => toggleDone(active)}>Undo</button>
                  </div>
                )}
                {summarizing && !summary && (
                  <div className="vc__ai vc__ai--loading"><div className="vc__ai-in">
                    <div className="vc__ai-k"><Sparkles size={13} />Reading the conversation…</div>
                    <span className="vc__skel" style={{ width: '92%' }} /><span className="vc__skel" style={{ width: '78%' }} /><span className="vc__skel" style={{ width: '55%' }} />
                  </div></div>
                )}
                {summaryError && !summarizing && (
                  <div className="vc__alert" role="alert"><AlertCircle size={16} /><div className="vc__grow">{summaryError}</div>
                    <button className="vc__lnk vc__lnk--brand" onClick={summarize}>Try again</button></div>
                )}
                {summary && (
                  <div className="vc__ai"><div className="vc__ai-in">
                    <div className="vc__ai-k"><Sparkles size={13} />Copilot summary<button className="vc__iconbtn vc__iconbtn--xs vc__push" aria-label="Close summary" onClick={() => setSummary(null)}><X size={12} /></button></div>
                    {summary.summary}
                    {summary.actions.length > 0 && (
                      <div className="vc__ai-steps">
                        {summary.actions.map(a => (
                          <div key={a.label} className="vc__ai-step">
                            <div className="vc__grow"><b>{a.label}</b>{a.detail ? <span className="vc__muted"> — {a.detail}</span> : null}</div>
                            <button className="vc__btn vc__btn--sm" disabled={drafting} onClick={() => writeWithAi(`${a.label}. ${a.detail}`)}><Sparkles size={13} />Draft reply</button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div></div>
                )}
                {active.unknown_senders.length > 0 && (
                  <div className="vc__alert vc__alert--info">
                    <UserPlus size={16} />
                    <div className="vc__grow"><b>{active.unknown_senders.join(', ')}</b> {active.unknown_senders.length === 1 ? 'isn’t a contact yet.' : 'aren’t contacts yet.'} Add them so their emails count as this vendor’s and the agent can reach them.
                      <div className="vc__alert-acts">{active.unknown_senders.map(a => <button key={a} className="vc__btn vc__btn--sm" onClick={() => onAddContact(a)}>Add {a}</button>)}</div>
                    </div>
                  </div>
                )}
                {threadError ? <div className="vc__alert"><AlertCircle size={16} /><div>{threadError}</div></div>
                  : !messages ? <ThreadSkeleton />
                  : visible.map((m, i) => {
                    const day = dayLabel(m.internal_date)
                    const newDay = !!day && (i === 0 || dayLabel(visible[i - 1].internal_date) !== day || (hidden > 0 && i === 1))
                    // Messages there when the thread opened come in one after another; a new one arrives on its own.
                    const order = list.indexOf(m) < firstPaint.current ? i : 0
                    return (
                    <div key={m.id}>
                      {hidden > 0 && i === 1 && (
                        <button className="vc__collapsed" onClick={() => setExpanded(true)}><span>{hidden} earlier message{hidden === 1 ? '' : 's'}</span></button>
                      )}
                      {newDay && <div className="vc__day"><span>{day}</span></div>}
                      <Message m={m} me={me} bills={bills} onOpenBill={onOpenBill} order={order}
                        onReply={() => startReply('reply', m)} onReplyAll={() => startReply('replyAll', m)} onForward={() => startForward(m)}>
                        {m.attachments?.map(a => (
                          <AttachmentTile key={a.attachment_id} a={a} docTypes={docTypes}
                            onPreview={/\.pdf$/i.test(a.filename) ? () => setPreview({ url: inboxAttachmentUrl(supplierId, active.thread_id, m.id, a), name: a.filename }) : undefined}
                            onDownload={() => downloadAuthed(inboxAttachmentUrl(supplierId, active.thread_id, m.id, a), a.filename)}
                            onSave={async (docType, docLabel) => {
                              try {
                                await saveInboxAttachment(supplierId, { threadId: active.thread_id, messageId: m.id, attachment: a, docType, docLabel, brandId })
                                notify(`Saved to Documents › ${docType === 'contract' ? 'Contracts & IOs' : docLabel}`, 'info')
                                onDocsChanged?.()
                                if (docLabel && !docTypes.includes(docLabel)) setDocTypes(t => [...t, docLabel])
                              // Rethrow so the tile doesn't mark itself filed.
                              } catch (e) { notify(errText(e, `Couldn’t save ${a.filename}.`), 'error'); throw e }
                            }} />
                        ))}
                      </Message>
                    </div>
                    )
                  })}
              </div>
            </>
          )}

          {(isNew || active) && connected && (
            <Composer
              draft={draft} setDraft={setDraft} isNew={isNew} suggestions={suggestions}
              onMode={m => (m === 'reply' || m === 'replyAll') && active ? startReply(m) : m === 'forward' && list.length ? startForward(list[list.length - 1]) : undefined}
              canThreadModes={!!active && !isNew}
              attachFiles={attachFiles} setAttachFiles={setAttachFiles} onPickBills={onPickBills}
              bodyRef={bodyRef} sending={sending} error={sendError} onSend={send}
              billFor={billFor}
              ai={{
                open: aiOpen, setOpen: setAiOpen, intent: aiIntent, setIntent: setAiIntent, drafting, run: writeWithAi, groups: intentGroups,
                ask: aiAsk, onAnswer: how => { if (how && aiAsk) void runAi(aiAsk, how); else { setAiAsk(null); setAiIntent('') } },
                canUndo: aiUndo !== null, onEdited: () => aiUndo !== null && setAiUndo(null),
                onUndo: () => { const b = aiUndo ?? ''; setAiUndo(null); setDraft(d => ({ ...d, body: b })); window.setTimeout(() => bodyRef.current?.focus(), 0) },
              }}
              threadKey={activeThreadId ?? ''} fwdLoading={fwdLoading}
              rcptInvalid={rcptInvalid.to || rcptInvalid.cc}
              onRcptInvalid={(k, v) => setRcptInvalid(r => (r[k] === v ? r : { ...r, [k]: v }))}
              open={composerOpen} typing={typing} justSent={justSent}
              replyName={active?.counterparty?.name || active?.counterparty?.email || ''}
              onOpen={(focus = true) => { setComposerOpen(true); if (focus) window.setTimeout(() => bodyRef.current?.focus(), 0) }}
              onClose={() => { setComposerOpen(false); setAiOpen(false); setSendError(''); if (active) setDraft(d => replyDraft(active, 'reply', undefined, d.body)) }}
            />
          )}
        </section>
      </div>
    </>
  )
}

function ListSkeleton() {
  return (
    <div className="vc__skel-list" aria-busy="true" aria-label="Loading conversations">
      {[0, 1, 2, 3].map(i => (
        <div key={i} className="vc__skel-row">
          <span className="vc__skel vc__skel--av" />
          <span className="vc__grow"><span className="vc__skel" style={{ width: '45%' }} /><span className="vc__skel" style={{ width: '80%' }} /><span className="vc__skel" style={{ width: '60%' }} /></span>
        </div>
      ))}
    </div>
  )
}

function ThreadItem({ t, i, on, onClick }: { t: InboxThread; i: number; on: boolean; onClick: () => void }) {
  const who = t.counterparty?.name || t.counterparty?.email || senderName(t.last_from)
  return (
    <button className={`vc__th${on ? ' on' : ''}${t.status === 'needs_reply' ? ' unread' : ''}${t.status === 'done' ? ' is-done' : ''}`}
            style={{ '--i': Math.min(i, 12) } as CSSProperties} aria-current={on ? 'true' : undefined} onClick={onClick}>
      <span className={`vc__th-av${t.status === 'needs_reply' ? ' is-hot' : ''}`}><Avatar name={who || '?'} /></span>
      <div className="vc__th-main">
        <div className="vc__th-top">
          <span className="vc__th-who">{who}{t.message_count > 1 && <span className="vc__muted"> · {t.message_count}</span>}</span>
          {t.status === 'needs_reply' && <span className="vc__udot" />}
          <span className="vc__th-time">{relDay(t.last_at)}</span>
        </div>
        <div className="vc__th-subj">{t.subject}</div>
        <div className="vc__th-snip">{t.last_outbound && <span className="vc__muted">You: </span>}{t.snippet}</div>
        <div className="vc__th-tags">
          {STATUS_CHIP[t.status]}
          {t.started_by === 'vendor' && <OriginTag t={t} />}
          {t.has_attachments && <span className="vc__chip vc__chip--xs"><Paperclip size={11} /></span>}
        </div>
      </div>
    </button>
  )
}

/** Plain text with links clickable and the vendor's bill numbers opening the bill. */
function RichBody({ text, bills, onOpenBill }: { text: string; bills: Bill[]; onOpenBill?: (id: string) => void }) {
  const byId = useMemo(() => {
    const m = new Map<string, Bill>()
    for (const b of bills) if (b.id && b.id.length >= 4) m.set(b.id.toLowerCase(), b)
    return m
  }, [bills])
  const pattern = useMemo(() => {
    const ids = [...byId.keys()].sort((a, b) => b.length - a.length).slice(0, 300)
    const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
    // Bill numbers only as whole tokens, so "1234" doesn't light up inside "12345" or a phone number.
    return new RegExp(`(https?://[^\\s<>]+${ids.length ? `|(?<![a-z0-9])(?:${ids.map(esc).join('|')})(?![a-z0-9])` : ''})`, 'gi')
  }, [byId])
  const parts = text.split(pattern)
  return (
    <div className="vc__msg-body">
      {parts.map((p, i) => {
        if (i % 2 === 0) return p
        if (/^https?:\/\//i.test(p)) return <a key={i} href={p} target="_blank" rel="noopener noreferrer">{p}</a>
        const b = byId.get(p.toLowerCase())
        return b && onOpenBill
          ? <button key={i} className="vc__billref" onClick={() => onOpenBill(b.invoice_id)} title="Open bill"><Receipt size={12} />{p}</button>
          : p
      })}
    </div>
  )
}

function ThreadSkeleton() {
  return (
    <div className="vc__skel-thread" aria-busy="true" aria-label="Loading conversation">
      {[0, 1, 2].map(i => (
        <div key={i} className={`vc__skel-bubble${i === 1 ? ' is-out' : ''}`}>
          <span className="vc__skel" style={{ width: '30%' }} /><span className="vc__skel" style={{ width: '90%' }} /><span className="vc__skel" style={{ width: '70%' }} />
        </div>
      ))}
    </div>
  )
}

/** Up to three faces, then the names. */
function People({ list }: { list: { email: string; name: string }[] }) {
  if (!list.length) return null
  const names = list.map(p => p.name || p.email)
  return (
    <span className="vc__ppl" title={list.map(p => `${p.name ? `${p.name} ` : ''}<${p.email}>`).join(', ')}>
      <span className="vc__avstack">{list.slice(0, 3).map(p => <Avatar key={p.email} name={p.name || p.email} size="sm" />)}</span>
      <span className="vc__ppl-names">{names.slice(0, 2).join(', ')}{names.length > 2 && ` +${names.length - 2}`}</span>
    </span>
  )
}

function Message({ m, me, bills, onOpenBill, order = 0, onReply, onReplyAll, onForward, children }: {
  m: LocalMsg; me: string; bills: Bill[]; onOpenBill?: (id: string) => void; order?: number
  onReply: () => void; onReplyAll: () => void; onForward: () => void; children?: ReactNode
}) {
  const when = m.internal_date ? new Date(m.internal_date) : null
  const name = m.outbound ? 'You' : senderName(m.from)
  const label = (ps: { name: string; email: string }[]) => ps.map(p => (p.email === me ? 'you' : p.name || p.email)).join(', ')
  const to = label(people(m.to)), cc = label(people(m.cc))
  return (
    <article className={`vc__mmsg${m.outbound ? ' is-out' : ''}${m.state ? ` is-${m.state}` : ''}`} style={{ '--i': Math.min(order, 8) } as CSSProperties}>
      <Avatar name={m.outbound ? (me || 'You') : senderName(m.from) || '?'} size="lg" />
      <div className="vc__mmsg-main">
        <div className="vc__mmsg-h">
          <span className="vc__msg-who">{name}</span>
          {!m.outbound && <span className="vc__muted vc__xs">{addrOf(m.from)}</span>}
          {m.state === 'sending' ? <span className="vc__msg-time vc__sendstate"><RefreshCw size={11} className="vc__spin" />Sending…</span>
            : m.state === 'sent' ? <span className="vc__msg-time vc__sendstate is-sent"><Check size={12} strokeWidth={3} />Sent</span>
            : <span className="vc__msg-time">{when ? when.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' }) : 'Just now'}</span>}
          <span className="vc__mmsg-acts">
            <button className="vc__iconbtn vc__iconbtn--sm" title="Reply" aria-label="Reply" onClick={onReply}><Reply size={14} /></button>
            <button className="vc__iconbtn vc__iconbtn--sm" title="Reply all" aria-label="Reply all" onClick={onReplyAll}><ReplyAll size={14} /></button>
            <button className="vc__iconbtn vc__iconbtn--sm" title="Forward" aria-label="Forward" onClick={onForward}><Forward size={14} /></button>
          </span>
        </div>
        {(to || cc) && <div className="vc__mmsg-to">to {to}{cc && <> · cc {cc}</>}</div>}
        <RichBody text={m.body} bills={bills} onOpenBill={onOpenBill} />
        {children && <div className="vc__atts">{children}</div>}
      </div>
    </article>
  )
}

/** Menus close on Escape or a click outside, and arrow keys move between their items. */
function useMenu(open: boolean, close: () => void) {
  const ref = useRef<HTMLSpanElement>(null)
  const closeRef = useRef(close)
  closeRef.current = close
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) closeRef.current() }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      closeRef.current()
      // Back to the button that opened it.
      ref.current?.querySelector<HTMLElement>('[aria-expanded]')?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  // A menu anchored near a screen edge slides back so all of it shows, with
  // a 16px gutter (on a 360px phone the AI menu would otherwise run off).
  useLayoutEffect(() => {
    const m = open ? ref.current?.querySelector<HTMLElement>('[role="menu"]') : null
    if (!m) return
    m.style.translate = ''
    const r = m.getBoundingClientRect()
    const over = r.right - (window.innerWidth - 16)
    const under = 16 - r.left
    if (over > 0) m.style.translate = `${-Math.min(over, Math.max(0, r.left - 16))}px 0`
    else if (under > 0) m.style.translate = `${under}px 0`
  }, [open])
  // Typed letters jump to the next item starting with them (typeahead);
  // letters typed within a second build up, so "re" finds "Rate cards".
  const typed = useRef({ text: '', at: 0 })
  const onMenuKey = (e: React.KeyboardEvent<HTMLElement>) => {
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"], input')]
    if (!items.length) return
    const inInput = (document.activeElement as HTMLElement | null)?.tagName === 'INPUT'
    if (e.key.length === 1 && !inInput && !e.metaKey && !e.ctrlKey && !e.altKey && e.key.trim()) {
      const now = Date.now()
      typed.current = { text: (now - typed.current.at < 1000 ? typed.current.text : '') + e.key.toLowerCase(), at: now }
      const q = typed.current.text
      const from = items.indexOf(document.activeElement as HTMLElement)
      const order = [...items.slice(from + (q.length === 1 ? 1 : 0)), ...items.slice(0, from + (q.length === 1 ? 1 : 0))]
      const hit = order.find(el => el.getAttribute('role') === 'menuitem' && (el.textContent ?? '').trim().toLowerCase().startsWith(q))
      if (hit) { e.preventDefault(); hit.focus() }
      return
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    e.preventDefault()
    const i = items.indexOf(document.activeElement as HTMLElement)
    const n = items.length
    items[e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : e.key === 'ArrowDown' ? (i + 1) % n : (i <= 0 ? n - 1 : i - 1)].focus()
  }
  return { ref, onMenuKey }
}

function AttachmentTile({ a, docTypes, onPreview, onDownload, onSave }: {
  a: InboxAttachment; docTypes: string[]
  onPreview?: () => void; onDownload: () => void; onSave: (t: DocType, label: string | null) => Promise<void>
}) {
  const [menu, setMenu] = useState(false)
  const [naming, setNaming] = useState(false)
  const [label, setLabel] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const ext = (a.filename.split('.').pop() || '').toUpperCase().slice(0, 4)
  const { ref: menuRef, onMenuKey } = useMenu(menu, () => { setMenu(false); setNaming(false) })
  const save = async (t: DocType, l: string | null) => {
    setSaving(true); setMenu(false); setNaming(false)
    try { await onSave(t, l); setSaved(t === 'contract' ? 'Contracts & IOs' : l) }
    catch { /* the parent told the user; it stays unfiled */ }
    finally { setSaving(false) }
  }
  return (
    <div className="vc__att2">
      <button className="vc__att2-main" onClick={onPreview ?? onDownload} title={onPreview ? 'Preview' : 'Download'}>
        <span className={`vc__ficon vc__ficon--sm vc__ficon--${ext === 'PDF' ? 'pdf' : /JPE?G|PNG|GIF|WEBP/.test(ext) ? 'img' : /XLSX?|CSV/.test(ext) ? 'xls' : 'doc'}`}>{ext || 'FILE'}</span>
        <span className="vc__min0"><span className="vc__att-n">{a.filename}</span><span className="vc__muted vc__xs">{Math.max(1, Math.round(a.size / 1024))} KB{saved ? ` · in ${saved}` : ''}</span></span>
      </button>
      <span className="vc__att2-acts">
        {onPreview && <button className="vc__iconbtn vc__iconbtn--sm" title="Preview" aria-label={`Preview ${a.filename}`} onClick={onPreview}><Eye size={14} /></button>}
        <button className="vc__iconbtn vc__iconbtn--sm" title="Download" aria-label={`Download ${a.filename}`} onClick={onDownload}><Download size={14} /></button>
        <span className="vc__menuwrap" ref={menuRef}>
          <button className="vc__iconbtn vc__iconbtn--sm" title="Save to Documents" aria-label={`Save ${a.filename} to Documents`} aria-expanded={menu} disabled={saving} onClick={() => setMenu(o => !o)}>
            {saving ? <RefreshCw size={14} className="vc__spin" /> : <FolderInput size={14} />}
          </button>
          {menu && (
            <div className="vc__menu" role="menu" tabIndex={-1} onKeyDown={onMenuKey}>
              <div className="vc__menu-h">Save to Documents</div>
              <button role="menuitem" onClick={() => save('contract', null)}>Contracts &amp; IOs</button>
              {docTypes.slice(0, 6).map(t => <button key={t} role="menuitem" onClick={() => save('other', t)}>{t}</button>)}
              {naming ? (
                <form className="vc__menu-new" onSubmit={e => { e.preventDefault(); if (label.trim()) save('other', label.trim()) }}>
                  <input autoFocus value={label} maxLength={80} placeholder="New type, e.g. Rate cards" onChange={e => setLabel(e.target.value)} />
                  <button className="vc__btn vc__btn--sm vc__btn--primary" disabled={!label.trim()}>Save</button>
                </form>
              ) : <button role="menuitem" onClick={() => setNaming(true)}><Plus size={13} />New type…</button>}
            </div>
          )}
        </span>
      </span>
    </div>
  )
}

function Recipients({ label, list, onChange, suggestions, autoFocus, onInvalid }: {
  label: string; list: string[]; onChange: (l: string[]) => void; suggestions: string[]; autoFocus?: boolean
  /** Text is left in the box that isn't an email address: Send must wait. */
  onInvalid?: (invalid: boolean) => void
}) {
  const [text, setText] = useState('')
  const [bad, setBad] = useState(false)
  const id = `vc-rcpt-${label.toLowerCase()}`
  const invalid = text.split(/[\s,;]+/).some(p => p && !EMAIL_RE.test(p.toLowerCase()))
  const onInvalidRef = useRef(onInvalid)
  onInvalidRef.current = onInvalid
  useEffect(() => { onInvalidRef.current?.(invalid) }, [invalid])
  useEffect(() => () => onInvalidRef.current?.(false), [])
  const add = (raw: string) => {
    const parts = raw.split(/[\s,;]+/).map(s => s.trim().toLowerCase()).filter(Boolean)
    const good = parts.filter(p => EMAIL_RE.test(p))
    setBad(parts.length > good.length)
    if (good.length) onChange([...new Set([...list, ...good])])
    setText(parts.length > good.length ? parts.filter(p => !EMAIL_RE.test(p)).join(' ') : '')
  }
  return (
    <div className={`vc__rcpt${bad ? ' is-bad' : ''}`}>
      <label className="vc__rcpt-l" htmlFor={id}>{label}</label>
      <div className="vc__rcpt-box">
        {list.map(a => (
          <span key={a} className="vc__rcpt-chip"><Avatar name={a} size="sm" />{a}
            <button aria-label={`Remove ${a}`} onClick={() => onChange(list.filter(x => x !== a))}><X size={11} /></button>
          </span>
        ))}
        <input id={id} list={`${id}-s`} value={text} autoFocus={autoFocus} placeholder={list.length ? '' : 'Email address'}
          aria-invalid={bad || undefined} aria-describedby={bad ? `${id}-err` : undefined}
          onChange={e => { setBad(false); if (/[,;]\s*$/.test(e.target.value)) add(e.target.value); else setText(e.target.value) }}
          onKeyDown={e => {
            if ((e.key === 'Enter' || e.key === 'Tab') && text.trim()) { e.preventDefault(); add(text) }
            if (e.key === 'Backspace' && !text && list.length) onChange(list.slice(0, -1))
          }}
          onBlur={() => text.trim() && add(text)} />
        <datalist id={`${id}-s`}>{suggestions.filter(s => !list.includes(s)).map(s => <option key={s} value={s} />)}</datalist>
      </div>
      {bad && <span id={`${id}-err`} className="vc__rcpt-err" role="alert">Not a valid email</span>}
    </div>
  )
}

function Composer({
  draft, setDraft, isNew, suggestions, onMode, canThreadModes, attachFiles, setAttachFiles, onPickBills, billFor, bodyRef, sending, error, onSend, ai,
  open, onOpen, onClose, typing, justSent, replyName, threadKey, fwdLoading, rcptInvalid, onRcptInvalid,
}: {
  draft: Draft; setDraft: (f: (d: Draft) => Draft) => void; isNew: boolean; suggestions: string[]
  onMode: (m: Mode) => void; canThreadModes: boolean
  attachFiles: File[]; setAttachFiles: (f: File[] | ((prev: File[]) => File[])) => void; onPickBills: () => void
  bodyRef: React.RefObject<HTMLTextAreaElement>; sending: boolean; error: string; onSend: () => void
  billFor: (f: File) => Bill | undefined
  ai: {
    open: boolean; setOpen: (o: boolean) => void; intent: string; setIntent: (s: string) => void; drafting: boolean; run: (intent: string) => void; groups: IntentGroup[]
    /** Asking whether to replace typed text; then the one-step undo. */
    ask: string | null; onAnswer: (how: 'replace' | 'below' | null) => void
    canUndo: boolean; onUndo: () => void; onEdited: () => void
  }
  /** Recipients' typed text is per thread. */
  threadKey: string
  /** Files a forward is still fetching. */
  fwdLoading: number
  rcptInvalid: boolean; onRcptInvalid: (k: 'to' | 'cc', invalid: boolean) => void
  /** Unfolded; otherwise an empty reply sits as a one-line bar. */
  open: boolean; onOpen: (focus?: boolean) => void; onClose: () => void
  /** The AI draft is typing itself in. */
  typing: boolean
  justSent: boolean
  replyName: string
}) {
  const showSubject = isNew || draft.mode === 'forward'
  const empty = !draft.body && !attachFiles.length
  const collapsed = !open && !isNew && draft.mode === 'reply' && empty && !ai.drafting
  // The note grows with what's typed, up to a point, instead of scrolling a tiny box.
  useEffect(() => {
    const el = bodyRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 260)}px`
  }, [draft.body, draft.mode, bodyRef, collapsed])
  // A pill slides under whichever of Reply / Reply all / Forward is on.
  const modesRef = useRef<HTMLDivElement>(null)
  const [ink, setInk] = useState<{ x: number; w: number } | null>(null)
  useLayoutEffect(() => {
    const el = modesRef.current?.querySelector<HTMLElement>('button.on')
    setInk(el ? { x: el.offsetLeft, w: el.offsetWidth } : null)
  }, [draft.mode, collapsed, canThreadModes])
  const modes: [Mode, string, ReactNode][] = [['reply', 'Reply', <Reply size={13} />], ['replyAll', 'Reply all', <ReplyAll size={13} />], ['forward', 'Forward', <Forward size={13} />]]
  const writing = ai.drafting || typing
  const { ref: aiMenuRef, onMenuKey: onAiMenuKey } = useMenu(ai.open, () => ai.setOpen(false))

  if (collapsed) {
    return (
      <div className="vc__composer">
        <div className="vc__cmodes vc__cmodes--bar">
          <button className="vc__cprompt" onClick={() => onOpen()}>
            {justSent
              ? <span className="vc__sentpill"><Check size={12} strokeWidth={3} />Sent</span>
              : <span className="vc__cprompt-ic"><Reply size={13} /></span>}
            <span className="vc__ellipsis">{replyName ? `Reply to ${replyName}…` : 'Write a reply…'}</span>
          </button>
          {/* Titles: at phone width these shrink to bare icons. */}
          <button title="Reply all" onClick={() => onMode('replyAll')}><ReplyAll size={13} />Reply all</button>
          <button title="Forward" onClick={() => onMode('forward')}><Forward size={13} />Forward</button>
          <button className="vc__brandc" title="Write with AI" onClick={() => { onOpen(false); ai.setOpen(true) }}>
            <Sparkles size={13} />Write with AI
            {ai.groups.length > 1 && <i className="vc__aidot" aria-label="Suggestions about these bills" />}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="vc__composer">
      <div className={`vc__composer-box vc__cexp${writing ? ' is-ai' : ''}`}>
        {canThreadModes && (
          <div className={`vc__cmodes${ink ? ' has-ink' : ''}`} ref={modesRef}>
            {ink && <span className="vc__cmode-ink" style={{ transform: `translateX(${ink.x}px)`, width: ink.w }} aria-hidden="true" />}
            {modes.map(([m, l, icon]) => (
              <button key={m} className={draft.mode === m ? 'on' : ''} onClick={() => onMode(m)}>{icon}{l}</button>
            ))}
            {empty && (
              <button className="vc__cmin" title="Close" aria-label="Close the reply" onClick={onClose}><ChevronDown size={14} /></button>
            )}
          </div>
        )}
        <Recipients key={`to-${threadKey}-${draft.mode}`} label="To" list={draft.to} onChange={to => setDraft(d => ({ ...d, to }))} suggestions={suggestions}
          autoFocus={(isNew || draft.mode === 'forward') && !draft.to.length} onInvalid={v => onRcptInvalid('to', v)} />
        {draft.showCc
          ? <Recipients key={`cc-${threadKey}`} label="Cc" list={draft.cc} onChange={cc => setDraft(d => ({ ...d, cc }))} suggestions={suggestions} onInvalid={v => onRcptInvalid('cc', v)} />
          : <button className="vc__cc-add" onClick={() => setDraft(d => ({ ...d, showCc: true }))}>Cc</button>}
        {showSubject && <input className="vc__subj" value={draft.subject} onChange={e => setDraft(d => ({ ...d, subject: e.target.value }))} placeholder="Subject" />}
        <div className="vc__cbody">
          <textarea ref={bodyRef} value={draft.body} rows={draft.mode === 'forward' ? 2 : 3} readOnly={typing} aria-busy={writing}
            placeholder={ai.drafting ? '' : draft.mode === 'forward' ? 'Add a note…' : isNew ? 'Write your message…' : 'Write a reply…'}
            onChange={e => { ai.onEdited(); setDraft(d => ({ ...d, body: e.target.value })) }}
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSend() }
              if (e.key === 'Escape' && empty && canThreadModes) onClose()
            }} />
          {ai.drafting && !typing && !draft.body && (
            <div className="vc__cthinking" aria-hidden="true"><Sparkles size={13} />Writing a draft<span className="vc__wdots"><i /><i /><i /></span></div>
          )}
        </div>
        {ai.ask && (
          <div className="vc__aiask" role="alert">
            <Sparkles size={13} /><span className="vc__grow">You’ve already written something. Replace it with the AI draft?</span>
            <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={() => ai.onAnswer('replace')}>Replace</button>
            <button className="vc__btn vc__btn--sm" onClick={() => ai.onAnswer('below')}>Add below</button>
            <button className="vc__btn vc__btn--sm vc__btn--ghost" onClick={() => ai.onAnswer(null)}>Cancel</button>
          </div>
        )}
        {ai.canUndo && !writing && (
          <div className="vc__aiask vc__aiask--undo" role="status">
            <Sparkles size={13} /><span className="vc__grow">AI draft written.</span>
            <button className="vc__lnk vc__lnk--brand" onClick={ai.onUndo}>Undo</button>
          </div>
        )}
        {draft.mode === 'forward' && draft.fwd && (
          <div className="vc__fwd" aria-label="Forwarded message">
            <div className="vc__fwd-h"><Forward size={12} />Forwarded message</div>
            <div className="vc__fwd-meta"><b>{senderName(draft.fwd.from) || draft.fwd.from}</b> · {draft.fwd.when}</div>
            <div className="vc__fwd-body">{draft.fwd.body}</div>
          </div>
        )}
        {(attachFiles.length > 0 || fwdLoading > 0) && (
          <div className="vc__composer-files">
            {attachFiles.map((f, i) => {
              const b = billFor(f)
              return (
              <span key={`${f.name}-${i}`} className={`vc__chip vc__chip--xs${b ? ' vc__chip--info' : ''}`} title={f.name}>
                {b ? <><Receipt size={11} />{b.id} · {billMoney(b)}</> : <><Paperclip size={11} />{f.name}</>}
                <button className="vc__chipx" aria-label={`Remove ${b ? b.id : f.name}`} onClick={() => setAttachFiles(a => a.filter(x => x !== f))}><X size={11} /></button>
              </span>
              )
            })}
            {fwdLoading > 0 && (
              <span className="vc__chip vc__chip--xs" role="status"><RefreshCw size={11} className="vc__spin" />Attaching {fwdLoading} file{fwdLoading === 1 ? '' : 's'} from the original…</span>
            )}
          </div>
        )}
        <div className="vc__composer-row">
          <label className="vc__iconbtn vc__iconbtn--sm" title="Attach a file" aria-label="Attach a file">
            <Paperclip size={14} />
            <input type="file" multiple hidden onChange={e => { const fs = Array.from(e.target.files ?? []); setAttachFiles(a => [...a, ...fs]); e.target.value = '' }} />
          </label>
          <button className="vc__btn vc__btn--sm vc__btn--ghost" title="Attach bills" onClick={onPickBills}><Receipt size={13} />Bills</button>
          <span className="vc__menuwrap" ref={aiMenuRef}>
            <button className={`vc__btn vc__btn--sm vc__btn--ghost vc__brandc${writing ? ' is-writing' : ''}`} disabled={writing} onClick={() => ai.setOpen(!ai.open)} aria-expanded={ai.open}>
              <Sparkles size={13} />{writing ? 'Writing…' : 'Write with AI'}
              {ai.groups.length > 1 && !writing && <i className="vc__aidot" aria-label="Suggestions about these bills" />}
              <ChevronDown size={12} />
            </button>
            {ai.open && (
              <div className="vc__menu vc__menu--up vc__menu--wide" role="menu" tabIndex={-1} onKeyDown={onAiMenuKey}>
                {ai.groups.map((g, gi) => (
                  <div key={g.title} className="vc__menu-grp" role="group" aria-label={g.title}>
                    <div className="vc__menu-h">{g.title}</div>
                    {g.items.map((t, ti) => (
                      <button key={t} role="menuitem" style={{ '--i': Math.min(gi * 3 + ti, 10) } as CSSProperties} onClick={() => ai.run(t)}>{t}</button>
                    ))}
                  </div>
                ))}
                <form className="vc__menu-new" onSubmit={e => { e.preventDefault(); if (ai.intent.trim()) ai.run(ai.intent.trim()) }}>
                  <input autoFocus value={ai.intent} placeholder="Or say it in your words…" onChange={e => ai.setIntent(e.target.value)} />
                  <button className="vc__btn vc__btn--sm vc__btn--primary" disabled={!ai.intent.trim()}>Write</button>
                </form>
              </div>
            )}
          </span>
          {error && <span className="vc__err vc__err--inline vc__shake" key={error}>{error}</span>}
          <span className="vc__grow" />
          <span className="vc__muted vc__xs vc__kbdhint">⌘/Ctrl + Enter</span>
          <button className={`vc__btn vc__btn--sm vc__btn--primary vc__send${sending ? ' is-sending' : ''}${justSent ? ' is-sent' : ''}`} onClick={onSend} disabled={sending || writing || fwdLoading > 0 || rcptInvalid}
            title={fwdLoading > 0 ? 'Waiting for the forwarded files' : rcptInvalid ? 'Fix the address that isn’t valid' : undefined}>
            {justSent ? <><Check size={14} strokeWidth={3} />Sent</> : <><Send size={14} />{sending ? 'Sending…' : 'Send'}</>}
          </button>
        </div>
      </div>
    </div>
  )
}
