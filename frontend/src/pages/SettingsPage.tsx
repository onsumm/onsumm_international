import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  CheckCircle2, XCircle, ChevronDown, Check, Shield, Users,
  GitBranch, RotateCcw, Sparkles, Upload, X, GripVertical, Plus, Save, ArrowRight, Tag, BookOpen,
  Percent, ChevronRight, Lock, Building2, Pencil, Trash2, ImagePlus, TrendingUp, Layers, Briefcase, Wallet, Globe,
} from 'lucide-react'
import {
  ROLE_LABELS, PERMISSION_CATEGORIES,
  type Role, can,
} from '../auth/permissions'
import { useAuth } from '../auth/AuthContext'
import { getChain, saveChain, resetChain, DEFAULT_CHAIN, AVAILABLE_ROLES, type ChainStep } from '../utils/chainConfig'
import {
  fetchChainConfig, saveChainConfig, fetchDepartments, fetchUsers, addDepartment, deleteDepartment, fetchChainDefault, saveChainAsDefault,
  fetchCharges, createCharge, updateCharge, deleteCharge,
  fetchCoaRules, createCoaRule, updateCoaRule, deleteCoaRule,
  fetchTdsSections, createTdsSection, updateTdsSection, deleteTdsSection,
  fetchVendorCategories, createVendorCategory, updateVendorCategory, deleteVendorCategory,
  fetchMappingRules, createMappingRule, deleteMappingRule,
  fetchTdsPreferences, saveTdsPreferences,
  fetchVendorOverrides, createVendorOverride, updateVendorOverride, deleteVendorOverride, submitCatchupDecision,
  fetchFyState,
  fetchVendors,
  fetchCategories, createCategory, deleteCategory,
  fetchCategoryExpHeads, setCategoryExpHead,
  createUser, updateUser, deleteUser,
} from '../api/invoices'
import type { Charge, CoaRule, CompanyUser, TdsSection, VendorCategory, TdsMappingRule, TdsPreferences, VendorTdsOverride, VendorTdsFyState, CatchupSummary, Vendor, Category, FxSettings } from '../api/invoices'
import { fetchFxSettings, updateFxSettings, fetchDemoPaymentSettings, saveDemoPaymentSettings } from '../api/invoices'
import { useJurisdiction, useService } from '../auth/EntitlementsContext'
import JurisdictionSettings from '../components/settings/JurisdictionSettings'
import { useNavigate, useParams } from 'react-router-dom'
import { SETTINGS_SECTIONS, settingsPath, type SettingsSection as RouteSettingsSection } from '../routes/paths'
import { useAuthedObjectUrl } from '../api/authedFiles'
import { fetchCostCenters, createCostCenter, updateCostCenter, deleteCostCenter, type CostCenter } from '../api/invoices'
import { BASE_CATEGORIES, addCustomCategory, removeCustomCategory } from '../utils/categories'
import axios from 'axios'

const ROLES = Object.keys(ROLE_LABELS) as Role[]

const ROLE_COLORS: Record<string, { bg: string; color: string; dot: string }> = {
  admin:              { bg: '#fef3c7', color: '#92400e', dot: '#f59e0b' },
  finance_controller: { bg: '#dbeafe', color: '#1e40af', dot: '#3b82f6' },
  accountant:         { bg: '#d1fae5', color: '#065f46', dot: '#10b981' },
  approver:           { bg: '#ede9fe', color: '#5b21b6', dot: '#8b5cf6' },
  member:             { bg: '#f3f4f6', color: '#374151', dot: '#9ca3af' },
  auditor:            { bg: '#fce7f3', color: '#9d174d', dot: '#ec4899' },
}

// ── Per-step assignee picker ─────────────────────────────────────────────────
function AssigneePicker({ step, companyId, department, onChange }: {
  step: ChainStep; companyId?: string; department?: string; onChange: (ids: string[]) => void
}) {
  const [open, setOpen]       = useState(false)
  const [eligible, setEligible] = useState<{ username: string; name: string }[]>([])
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (step.locked) {
      fetchUsers(companyId, department || undefined)
        .then(users => setEligible(users))
        .catch(() => {})
    } else {
      fetchUsers(companyId)
        .then(users => {
          const filtered = users.filter(u => u.role === step.role)
          setEligible(filtered)
          // Auto-select if step label matches a user's name and no assignee chosen yet
          if ((step.assignees ?? []).length === 0) {
            const match = filtered.find(u => u.name.toLowerCase() === step.label.toLowerCase())
            if (match) onChange([match.name])
          }
        })
        .catch(() => {})
    }
  }, [companyId, department, step.role, step.locked])

  // When label is typed to match a user and no assignee is set yet, auto-select them
  useEffect(() => {
    if (!step.locked && (step.assignees ?? []).length === 0 && eligible.length > 0) {
      const match = eligible.find(u => u.name.toLowerCase() === step.label.toLowerCase())
      if (match) onChange([match.name])
    }
  }, [step.label])

  const selected = step.assignees ?? []
  const label = selected.length === 0
    ? (step.locked ? 'Anyone in dept' : 'Anyone in role')
    : selected.length === 1
      ? selected[0]
      : `${selected.length} people`

  function toggle(name: string) {
    const next = selected.includes(name)
      ? selected.filter(n => n !== name)
      : [...selected, name]
    onChange(next)
  }

  useEffect(() => {
    if (!open) return
    function handleOutside(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    return () => document.removeEventListener('mousedown', handleOutside)
  }, [open])

  return (
    <div className="ap" ref={ref} onPointerDown={e => e.stopPropagation()}>
      {/* Names display on card — only show selected assignees, or nothing if "Anyone in role" */}
      {selected.length > 0 && (
        <div className="ap__names">
          {eligible.filter(u => selected.includes(u.name)).map(u => (
            <span key={u.username} className="ap__name-chip ap__name-chip--sel">
              <span className="ap__avatar-sm">{u.name.charAt(0)}</span>
              {u.name}
            </span>
          ))}
        </div>
      )}
      <button className="ap__btn" onClick={() => setOpen(o => !o)} tabIndex={0}>
        <Users size={10} />
        {label}
        <ChevronDown size={10} />
      </button>
      {open && (
        <div className="ap__menu">
          <div className="ap__hint">{step.locked ? 'Who is the submitter?' : 'Who can approve this step?'}</div>
          <button
            className={`ap__opt ${selected.length === 0 ? 'ap__opt--active' : ''}`}
            onClick={() => { onChange([]); setOpen(false) }}
          >
            <span className="ap__check">{selected.length === 0 && <Check size={10} />}</span>
            {step.locked ? 'Anyone in dept' : 'Anyone in role'}
          </button>
          {eligible.map(u => (
            <button
              key={u.username}
              className={`ap__opt ${selected.includes(u.name) ? 'ap__opt--active' : ''}`}
              onClick={() => { toggle(u.name); }}
            >
              <span className="ap__check">{selected.includes(u.name) && <Check size={10} />}</span>
              <span className="ap__avatar-sm">{u.name.charAt(0)}</span>
              {u.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Approval Chain Editor ────────────────────────────────────────────────────
const EMPTY_CHAIN: ChainStep[] = [
  { id: 'submit', label: 'Submitted', role: 'member', locked: true },
]

function ChainEditor({ companyId, department }: { companyId?: string; department?: string }) {
  const [steps, setSteps]           = useState<ChainStep[] | null>(null) // null = loading
  const [nlText, setNlText]         = useState('')
  const [parsing, setParsing]       = useState(false)
  const [parseErr, setParseErr]     = useState('')
  const [saved, setSaved]           = useState(false)
  const [savedDefault, setSavedDefault] = useState(false)
  const [showAdd, setShowAdd]       = useState(false)
  const [addMenuPos, setAddMenuPos] = useState<{ top: number; left: number } | null>(null)
  const addBtnRef = useRef<HTMLButtonElement>(null)
  const [removingId, setRemovingId] = useState<string | null>(null)

  // Load chain from backend — no default pre-population to avoid flicker
  useEffect(() => {
    setSteps(null) // show spinner on tab switch
    fetchChainConfig(companyId, department).then(apiSteps => {
      const mapped: ChainStep[] = apiSteps.length > 0
        ? apiSteps.map((s, i) => ({ id: s.role + '_' + i, label: s.label, role: s.role, locked: s.locked, assignees: s.assignees ?? [] }))
        : [...EMPTY_CHAIN]
      setSteps(mapped)
    }).catch(() => setSteps([...EMPTY_CHAIN]))
  }, [companyId, department])

  // Load all company users when menu opens
  useEffect(() => {
    if (!showAdd) return
    fetchUsers(companyId).then(setAllUsers).catch(() => {})
  }, [showAdd, companyId])

  // Close add menu on outside click
  useEffect(() => {
    if (!showAdd) return
    const handler = (e: PointerEvent) => {
      const menu = document.querySelector('.ce__add-menu')
      const btn  = addBtnRef.current
      if (menu?.contains(e.target as Node) || btn?.contains(e.target as Node)) return
      setShowAdd(false); setAddMenuPos(null)
    }
    document.addEventListener('pointerdown', handler)
    return () => document.removeEventListener('pointerdown', handler)
  }, [showAdd])

  const activeSteps = steps ?? []
  const [dragId,   setDragId]       = useState<string | null>(null)
  const [insertAt, setInsertAt]     = useState<number | null>(null)

  // Refs — never cause re-renders
  const dragStateRef  = useRef<{ id: string; originIdx: number; offsetX: number; offsetY: number; cardW: number; cardH: number } | null>(null)
  const ghostRef      = useRef<HTMLDivElement>(null)
  const cardEls       = useRef<Map<string, HTMLElement>>(new Map())
  const prevPositions = useRef<Map<string, number>>(new Map())   // left-x before re-render
  const fileRef       = useRef<HTMLInputElement>(null)

  // ── FLIP: record positions before insertAt changes, animate after ──────────
  // We call this manually before setInsertAt
  function snapPositions() {
    cardEls.current.forEach((el, id) => {
      prevPositions.current.set(id, el.getBoundingClientRect().left)
    })
  }

  useLayoutEffect(() => {
    if (!dragId) return
    cardEls.current.forEach((el, id) => {
      if (id === dragId) return
      const prev = prevPositions.current.get(id)
      if (prev == null) return
      const curr = el.getBoundingClientRect().left
      const dx = prev - curr
      if (Math.abs(dx) < 1) return
      // Apply inverse transform instantly, then transition to 0
      el.style.transition = 'none'
      el.style.transform  = `translateX(${dx}px)`
      requestAnimationFrame(() => {
        el.style.transition = 'transform 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)'
        el.style.transform  = ''
      })
    })
  }, [insertAt])  // fires after every insertAt change

  // Display order: remove dragged card, insert gap placeholder at insertAt
  const displaySteps: (ChainStep | { id: '__gap__'; isGap: true })[] = (() => {
    if (!dragId || insertAt === null) return activeSteps
    const filtered = activeSteps.filter(s => s.id !== dragId)
    const clampedInsert = Math.max(1, Math.min(insertAt, filtered.length))
    const result: (ChainStep | { id: '__gap__'; isGap: true })[] = [...filtered]
    result.splice(clampedInsert, 0, { id: '__gap__', isGap: true })
    return result
  })()

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>, id: string, idx: number) {
    if (activeSteps[idx]?.locked) return
    const rect = e.currentTarget.getBoundingClientRect()
    dragStateRef.current = { id, originIdx: idx, offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top, cardW: rect.width, cardH: rect.height }
    if (ghostRef.current) {
      ghostRef.current.style.left   = `${rect.left}px`
      ghostRef.current.style.top    = `${rect.top}px`
      ghostRef.current.style.width  = `${rect.width}px`
      ghostRef.current.style.height = `${rect.height}px`
    }
    e.currentTarget.setPointerCapture(e.pointerId)
    snapPositions()
    setDragId(id)
    setInsertAt(idx)
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const ds = dragStateRef.current
    if (!ds || !ghostRef.current) return
    e.preventDefault()
    // Ghost follows cursor — direct DOM, zero jank
    ghostRef.current.style.left = `${e.clientX - ds.offsetX}px`
    ghostRef.current.style.top  = `${e.clientY - ds.offsetY}px`

    // Collect visible non-dragged, non-locked cards sorted by visual x position
    const cards: { mid: number }[] = []
    cardEls.current.forEach((el, id) => {
      if (id === ds.id || id === '__gap__') return
      const step = activeSteps.find(s => s.id === id)
      if (!step || step.locked) return
      const r = el.getBoundingClientRect()
      cards.push({ mid: r.left + r.width / 2 })
    })
    cards.sort((a, b) => a.mid - b.mid)
    // insertAt = 1 + number of unlocked cards whose midpoint cursor has passed
    let newInsert = 1
    for (const c of cards) { if (e.clientX > c.mid) newInsert++ }
    newInsert = Math.max(1, Math.min(newInsert, activeSteps.length))
    if (newInsert !== insertAt) { snapPositions(); setInsertAt(newInsert) }
  }

  function onPointerUp() {
    const ds = dragStateRef.current
    if (ds && insertAt !== null) {
      setSteps(prev => {
        const src = prev ?? []
        const next = src.filter(s => s.id !== ds.id)
        const item = src.find(s => s.id === ds.id)!
        next.splice(Math.min(insertAt, next.length), 0, item)
        return next
      })
    }
    dragStateRef.current = null
    setDragId(null); setInsertAt(null)
  }

  async function parseNL() {
    if (!nlText.trim()) return
    setParsing(true); setParseErr('')
    try {
      const form = new FormData(); form.append('text', nlText)
      const { data } = await axios.post<{ steps: { label: string; role: string }[] }>('/api/settings/extract-chain', form)
      applyExtracted(data.steps)
    } catch { setParseErr('Could not parse. Try being more specific about roles.') }
    finally { setParsing(false) }
  }

  async function parseFile(file: File) {
    setParsing(true); setParseErr('')
    try {
      const form = new FormData(); form.append('file', file)
      const { data } = await axios.post<{ steps: { label: string; role: string }[] }>('/api/settings/extract-chain', form)
      applyExtracted(data.steps)
    } catch { setParseErr('Could not extract chain from file.') }
    finally { setParsing(false) }
  }

  function applyExtracted(extracted: { label: string; role: string }[]) {
    const n: ChainStep[] = extracted.map((s, i) => ({
      id: s.role === 'member' ? 'submit' : `${s.role}_${i}_${Date.now()}`,
      label: s.label, role: s.role, locked: s.role === 'member',
    }))
    if (n.length > 0) { setSteps(n); setNlText('') }
  }

  function removeStep(id: string) {
    setRemovingId(id)
    setTimeout(() => { setSteps(s => s?.filter(x => x.id !== id) ?? null); setRemovingId(null) }, 250)
  }

  const [customRole, setCustomRole]   = useState('')
  const [customLabel, setCustomLabel] = useState('')
  const [allUsers, setAllUsers]       = useState<CompanyUser[]>([])

  function addStep(role: string, label: string) {
    setSteps(s => [...(s ?? []), { id: `${role}_${Date.now()}`, label, role }])
    setCustomRole(''); setCustomLabel('')
    setShowAdd(false)
  }

  function updateLabel(id: string, label: string) {
    setSteps(s => s?.map(x => x.id === id ? { ...x, label } : x) ?? null)
  }

  function updateAssignees(id: string, assignees: string[]) {
    setSteps(s => s?.map(x => x.id === id ? { ...x, assignees } : x) ?? null)
  }

  function doSave() {
    const apiSteps = activeSteps.map((s, i) => ({ step_order: i, label: s.label, role: s.role, locked: !!s.locked, assignees: s.assignees ?? [] }))
    saveChainConfig(apiSteps, companyId, department).catch(() => {})
    setSaved(true); setTimeout(() => setSaved(false), 2000)
  }

  async function doSaveDefault() {
    const apiSteps = activeSteps.map((s, i) => ({ step_order: i, label: s.label, role: s.role, locked: !!s.locked, assignees: s.assignees ?? [] }))
    await saveChainConfig(apiSteps, companyId, department).catch(() => {})
    await saveChainAsDefault(companyId, department).catch(() => {})
    setSavedDefault(true); setTimeout(() => setSavedDefault(false), 2000)
  }

  function doReset() {
    setSteps(null)
    fetchChainDefault(companyId, department).then(apiSteps => {
      const mapped: ChainStep[] = apiSteps.length > 0
        ? apiSteps.map((s, i) => ({ id: s.role + '_' + i, label: s.label, role: s.role, locked: s.locked, assignees: s.assignees ?? [] }))
        : [...EMPTY_CHAIN]
      setSteps(mapped)
    }).catch(() => setSteps([...EMPTY_CHAIN]))
  }

  function updateRole(id: string, role: string) {
    setSteps(s => (s ?? []).map(x => x.id === id ? { ...x, role } : x))
  }

  const isDragging  = dragId !== null
  const draggedStep = activeSteps.find(s => s.id === dragId)

  if (steps === null) {
    return <div style={{ padding: '32px', textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>Loading chain…</div>
  }

  return (
    <div className="ce">
      <div className="ce__input-row">
        <div className="ce__nl-wrap">
          <textarea className="ce__nl"
            placeholder="Describe in natural language… e.g. 'Bills go to department approver, then accountant, then CFO'"
            value={nlText} onChange={e => setNlText(e.target.value)} rows={2}
          />
          <button className="ce__parse-btn" onClick={parseNL} disabled={!nlText.trim() || parsing}>
            <Sparkles size={13} /> {parsing ? 'Parsing…' : 'Parse with AI'}
          </button>
        </div>
        <div className="ce__or">or</div>
        <button className="ce__upload-btn" onClick={() => fileRef.current?.click()} disabled={parsing}>
          <Upload size={14} /> Upload PDF / Excel
        </button>
        <input ref={fileRef} type="file" style={{ display: 'none' }}
          accept=".pdf,.xlsx,.xls,.csv,.txt,.docx"
          onChange={e => { const f = e.target.files?.[0]; if (f) parseFile(f); e.target.value = '' }}
        />
      </div>
      {parseErr && <div className="ce__err">{parseErr}</div>}

      <div className="ce__flow-label">
        Approval Flow
        <span className="ce__flow-hint">Drag to reorder · Click label to rename · Type role name</span>
      </div>

      <div
        className={`ce__flow ${isDragging ? 'ce__flow--dragging' : ''}`}
        onPointerMove={isDragging ? onPointerMove : undefined}
        onPointerUp={isDragging ? onPointerUp : undefined}
      >
        {displaySteps.map((item, i) => {
          if ('isGap' in item) {
            return (
              <div key="__gap__" className="ce__flow-item">
                {i > 0 && <div className="ce__arrow ce__arrow--insert"><ArrowRight size={14} /></div>}
                <div className="ce__gap" style={{ width: dragStateRef.current?.cardW ?? 180, height: dragStateRef.current?.cardH ?? 110 }} />
              </div>
            )
          }

          const step = item as ChainStep
          const rc   = ROLE_COLORS[step.role] ?? { bg: '#f3f4f6', color: '#374151', dot: '#9ca3af' }
          const isRemoving = step.id === removingId

          return (
            <div key={step.id} className="ce__flow-item">
              {i > 0 && <div className="ce__arrow"><ArrowRight size={14} /></div>}
              <div
                ref={el => { if (el) cardEls.current.set(step.id, el); else cardEls.current.delete(step.id) }}
                className={`ce__card ${step.locked ? 'ce__card--locked' : ''} ${isRemoving ? 'ce__card--removing' : ''}`}
                style={{ borderColor: rc.dot }}
                onPointerDown={!step.locked ? e => onPointerDown(e, step.id, activeSteps.indexOf(step)) : undefined}
              >
                <div className="ce__card-top">
                  {!step.locked && <span className="ce__grip"><GripVertical size={12} /></span>}
                  <div className="ce__card-dot" style={{ background: rc.dot }} />
                  <input
                    className="ce__card-label"
                    value={step.label}
                    onChange={e => updateLabel(step.id, e.target.value)}
                    readOnly={step.locked}
                    onPointerDown={e => e.stopPropagation()}
                    style={{ color: rc.color }}
                  />
                  {!step.locked && (
                    <button className="ce__remove" onPointerDown={e => e.stopPropagation()} onClick={() => removeStep(step.id)}>
                      <X size={10} />
                    </button>
                  )}
                </div>
                <div className="ce__card-bottom" onPointerDown={e => e.stopPropagation()}>
                  {!step.locked && (
                    <input
                      className="ce__role-input"
                      placeholder="Role (e.g. approver)"
                      value={step.role}
                      onChange={e => updateRole(step.id, e.target.value)}
                    />
                  )}
                  <AssigneePicker step={step} companyId={companyId} department={department} onChange={ids => updateAssignees(step.id, ids)} />
                </div>
              </div>
            </div>
          )
        })}

        {/* Done cap */}
        <div className="ce__flow-item">
          <div className="ce__arrow"><ArrowRight size={14} /></div>
          <div className="ce__card ce__card--done">
            <div className="ce__card-top">
              <div className="ce__card-dot" style={{ background: '#10b981' }} />
              <span className="ce__card-label-static">Approved</span>
            </div>
          </div>
        </div>

        {/* Add step */}
        <div className="ce__flow-item">
          <button
            ref={addBtnRef}
            className="ce__add-btn"
            onClick={() => {
              const r = addBtnRef.current?.getBoundingClientRect()
              if (r) setAddMenuPos({ top: r.bottom + 6, left: r.left })
              setShowAdd(s => !s)
            }}
          ><Plus size={14} /></button>
          {showAdd && addMenuPos && (
            <div className="ce__add-menu" style={{ top: addMenuPos.top, left: addMenuPos.left }}>
              <div className="ce__add-hint">Quick add</div>
              {[
                { role: 'member',             label: 'Member',             color: '#6b7280' },
                { role: 'approver',           label: 'Approver',           color: '#8b5cf6' },
                { role: 'accountant',         label: 'Accountant',         color: '#10b981' },
                { role: 'finance_controller', label: 'Finance Controller', color: '#3b82f6' },
                { role: 'admin',              label: 'Admin',              color: '#f59e0b' },
                { role: 'auditor',            label: 'Auditor',            color: '#ec4899' },
              ].map(r => (
                <button key={r.role} className="ce__add-opt"
                  onPointerDown={e => e.stopPropagation()}
                  onClick={() => addStep(r.role, r.label)}>
                  <span className="ce__add-dot" style={{ background: r.color }} />{r.label}
                </button>
              ))}
              <div className="ce__add-divider" />
              <div className="ce__add-hint">Custom step</div>
              <div className="ce__add-custom">
                {allUsers.length > 0 && (
                  <div className="ce__add-people">
                    <div className="ce__add-people-hint">Click to add as step</div>
                    {allUsers.map(u => (
                      <button
                        key={u.username}
                        className="ce__add-person-chip"
                        onPointerDown={e => e.stopPropagation()}
                        onClick={() => addStep(u.role, u.name)}
                      >
                        <span className="ce__add-person-av">{u.name.charAt(0)}</span>
                        <span className="ce__add-person-info">
                          <span className="ce__add-person-name">{u.name}</span>
                          <span className="ce__add-person-role">{u.role}{u.department ? ` · ${u.department}` : ''}</span>
                        </span>
                        <Plus size={12} style={{ marginLeft: 'auto', color: '#7c3aed', flexShrink: 0 }} />
                      </button>
                    ))}
                  </div>
                )}
                <div className="ce__add-divider" />
                <div className="ce__add-hint">Or type manually</div>
                <input
                  className="ce__add-custom-inp"
                  placeholder="Step label (e.g. HOD Approval)"
                  value={customLabel}
                  onChange={e => setCustomLabel(e.target.value)}
                  onPointerDown={e => e.stopPropagation()}
                />
                <input
                  className="ce__add-custom-inp"
                  placeholder="Role name (e.g. approver)"
                  value={customRole}
                  onChange={e => setCustomRole(e.target.value)}
                  onPointerDown={e => e.stopPropagation()}
                />
                <button
                  className="ce__add-custom-btn"
                  disabled={!customLabel.trim() || !customRole.trim()}
                  onPointerDown={e => e.stopPropagation()}
                  onClick={() => addStep(customRole.trim(), customLabel.trim())}
                >
                  <Plus size={12} /> Add Step
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Floating ghost */}
      {isDragging && draggedStep && (() => {
        const rc = ROLE_COLORS[draggedStep.role] ?? { bg: '#f3f4f6', color: '#374151', dot: '#9ca3af' }
        return (
          <div ref={ghostRef} className="ce__ghost" style={{ borderColor: rc.dot }}>
            <div className="ce__card-top">
              <span className="ce__grip"><GripVertical size={12} /></span>
              <div className="ce__card-dot" style={{ background: rc.dot }} />
              <span className="ce__card-label" style={{ color: rc.color, width: 'auto' }}>{draggedStep.label}</span>
            </div>
          </div>
        )
      })()}

      <div className="ce__actions">
        <button className="ce__reset-btn" onClick={doReset}><RotateCcw size={13} /> Reset to Default</button>
        <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
          <button className="ce__default-btn" onClick={doSaveDefault}>
            {savedDefault ? <><Check size={13} /> Saved as Default!</> : <><BookOpen size={13} /> Save as Default</>}
          </button>
          <button className="ce__save-btn" onClick={doSave}>
            {saved ? <><Check size={13} /> Saved!</> : <><Save size={13} /> Save Chain</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Charges Master ───────────────────────────────────────────────────────────
const EMPTY_FORM = { name: '', hsn_sac: '', default_tax_rate: '' }

function ChargesManager({ companyId }: { companyId?: string }) {
  const [charges, setCharges]   = useState<Charge[]>([])
  const [loading, setLoading]   = useState(true)
  const [form, setForm]         = useState(EMPTY_FORM)
  const [editId, setEditId]     = useState<string | null>(null)
  const [saving, setSaving]     = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    fetchCharges(companyId).then(setCharges).finally(() => setLoading(false))
  }, [companyId])

  const resetForm = () => { setForm(EMPTY_FORM); setEditId(null); setError('') }

  const startEdit = (c: Charge) => {
    setEditId(c.charge_id)
    setForm({ name: c.name, hsn_sac: c.hsn_sac ?? '', default_tax_rate: c.default_tax_rate != null ? String(c.default_tax_rate) : '' })
    setError('')
  }

  const handleSave = async () => {
    if (!form.name.trim()) { setError('Name is required'); return }
    setSaving(true); setError('')
    try {
      const payload = {
        name: form.name.trim(),
        hsn_sac: form.hsn_sac.trim() || undefined,
        default_tax_rate: form.default_tax_rate ? parseFloat(form.default_tax_rate) : undefined,
        company_id: companyId,
      }
      if (editId) {
        const updated = await updateCharge(editId, payload)
        setCharges(cs => cs.map(c => c.charge_id === editId ? { ...c, ...updated } : c))
      } else {
        const created = await createCharge(payload)
        setCharges(cs => [...cs, created].sort((a, b) => a.name.localeCompare(b.name)))
      }
      resetForm()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    await deleteCharge(id, companyId)
    setCharges(cs => cs.filter(c => c.charge_id !== id))
    if (editId === id) resetForm()
  }

  if (loading) return <div className="chg__loading">Loading charges master…</div>

  return (
    <div className="chg__wrap">
      {/* ── AI Header Banner ── */}
      <div className="chg__ai-header">
        <div className="chg__ai-left">
          <div className="chg__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="chg__ai-title">Charges & Ancillary Master Engine</div>
            <div className="chg__ai-sub">Agents identify freight, packing, handling, custom duties & SAC tax codes across invoice documents.</div>
          </div>
        </div>
        <div className="chg__ai-stats">
          <span className="chg__stat-badge">
            <span className="chg__stat-dot" />
            {charges.length} Active Charges
          </span>
        </div>
      </div>

      {/* ── Add / Edit Form Card ── */}
      <div className="chg__add-card">
        <div className="chg__add-header">
          <Plus size={13} style={{ color: '#ea580c' }} />
          <span>{editId ? 'Edit Charge Code' : 'Add New Charge Code'}</span>
        </div>
        <div className="chg__add-row">
          <input
            className="chg__inp-ai chg__inp-ai--name"
            placeholder="Charge name (e.g. Freight & Logistics) *"
            value={form.name}
            onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
          />
          <input
            className="chg__inp-ai chg__inp-ai--hsn"
            placeholder="HSN / SAC code"
            value={form.hsn_sac}
            onChange={e => setForm(f => ({ ...f, hsn_sac: e.target.value }))}
          />
          <input
            className="chg__inp-ai chg__inp-ai--rate"
            placeholder="Tax rate %"
            type="number"
            min="0"
            max="100"
            step="0.01"
            value={form.default_tax_rate}
            onChange={e => setForm(f => ({ ...f, default_tax_rate: e.target.value }))}
          />
          <button className="chg__add-btn-ai" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : editId ? <><Check size={13} /> Update Charge</> : <><Plus size={13} /> Add Charge</>}
          </button>
          {editId && <button className="chg__cancel-btn-ai" onClick={resetForm}>Cancel</button>}
        </div>
        {error && <div className="chg__err">{error}</div>}
      </div>

      {/* ── Charges Table Card ── */}
      <div className="chg__card">
        <div className="chg__card-header">
          <div className="chg__card-title">
            <Percent size={14} className="chg__icon-orange" /> Registered Charges
          </div>
          <span className="chg__card-count">Used in bill line item extra charges and SAC tax calculations</span>
        </div>

        {charges.length === 0 ? (
          <div className="chg__empty-state">
            <div className="chg__empty-icon"><Percent size={24} /></div>
            <div className="chg__empty-title">No Charges Defined</div>
            <div className="chg__empty-sub">Add your first ancillary charge above (e.g., Freight, Handling, Insurance).</div>
          </div>
        ) : (
          <table className="chg__table">
            <colgroup>
              <col style={{ width: '45%' }} />
              <col style={{ width: '25%' }} />
              <col style={{ width: '18%' }} />
              <col style={{ width: '12%' }} />
            </colgroup>
            <thead>
              <tr>
                <th>Charge Name</th>
                <th>HSN / SAC</th>
                <th>Tax Rate</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {charges.map(c => (
                <tr key={c.charge_id} className={`chg__tr ${editId === c.charge_id ? 'chg__tr--editing' : ''}`}>
                  <td style={{ fontWeight: 600, color: '#0f172a' }}>{c.name}</td>
                  <td>
                    {c.hsn_sac ? (
                      <span className="chg__hsn-badge">{c.hsn_sac}</span>
                    ) : (
                      <span className="chg__dash">—</span>
                    )}
                  </td>
                  <td>
                    {c.default_tax_rate != null ? (
                      <span className="chg__rate-badge">{c.default_tax_rate}%</span>
                    ) : (
                      <span className="chg__dash">—</span>
                    )}
                  </td>
                  <td className="chg__action-cell">
                    <div className="chg__action-wrap">
                      <button className="chg__action-btn chg__action-btn--primary" onClick={() => startEdit(c)}>Edit</button>
                      <button className="chg__del-btn" onClick={() => handleDelete(c.charge_id)} title="Delete charge">
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── COA Rules Manager ────────────────────────────────────────────────────────
const EXP_HEADS = [
  'ACCOUNTS','ADMINISTRATION','ASSETS','DUTIES & TAXES','IT','LEGAL',
  'MAN POWER','MARKETING','OFFICE','PANTRY','PROFESSIONAL','RENT','UTILITY','OTHER',
]
import { getAllCategories, syncCategoriesFromApi } from '../utils/categories'
const EMPTY_COA = { exp_head: '', vendor_name: '', category: '' }

function CoaRulesManager({ companyId, externalCategories }: { companyId?: string; externalCategories?: string[] }) {
  const [rules, setRules]           = useState<CoaRule[]>([])
  const [loading, setLoading]       = useState(true)
  const [form, setForm]             = useState(EMPTY_COA)
  const [editId, setEditId]         = useState<string | null>(null)
  const [saving, setSaving]         = useState(false)
  const [error, setError]           = useState('')
  const [vendors, setVendors]       = useState<Vendor[]>([])
  const [categoryOptions, setCategoryOptions] = useState<string[]>(['', ...getAllCategories()])
  const [vendorOpen, setVendorOpen] = useState(false)
  const [vendorQuery, setVendorQuery] = useState('')
  const vendorRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchCoaRules(companyId).then(setRules).finally(() => setLoading(false))
    fetchVendors(false, companyId).then(setVendors).catch(() => {})
    syncCategoriesFromApi(companyId).then(cats => setCategoryOptions(['', ...cats]))
  }, [companyId])

  // Sync immediately when parent CategoriesManager adds/removes a category
  useEffect(() => {
    if (externalCategories) setCategoryOptions(externalCategories)
  }, [externalCategories])

  // Close vendor dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (vendorRef.current && !vendorRef.current.contains(e.target as Node)) {
        setVendorOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const resetForm = () => { setForm(EMPTY_COA); setVendorQuery(''); setVendorOpen(false); setEditId(null); setError('') }

  const startEdit = (r: CoaRule) => {
    setEditId(r.id)
    const vn = r.vendor_name ?? ''
    setForm({ exp_head: r.exp_head, vendor_name: vn, category: r.category ?? '' })
    setVendorQuery(vn)
    setVendorOpen(false)
    setError('')
  }

  const handleSave = async () => {
    if (!form.exp_head) { setError('EXP HEAD is required'); return }
    setSaving(true); setError('')
    try {
      const payload = {
        exp_head:    form.exp_head,
        vendor_name: form.vendor_name.trim() || undefined,
        category:    form.category.trim() || undefined,
        priority:    0,
        company_id:  companyId,
      }
      if (editId) {
        const updated = await updateCoaRule(editId, payload)
        setRules(rs => rs.map(r => r.id === editId ? updated : r))
      } else {
        const created = await createCoaRule(payload)
        setRules(rs => [...rs, created])
      }
      resetForm()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    await deleteCoaRule(id, companyId)
    setRules(rs => rs.filter(r => r.id !== id))
    if (editId === id) resetForm()
  }

  if (loading) return <div className="coa__loading">Loading COA rules…</div>

  return (
    <div className="coa__wrap">
      {/* ── AI Header Banner ── */}
      <div className="coa__ai-header">
        <div className="coa__ai-left">
          <div className="coa__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="coa__ai-title">Chart of Accounts (COA) Mapping Engine</div>
            <div className="coa__ai-sub">Agents match vendor names & categories to automate Chart of Accounts accounting entries.</div>
          </div>
        </div>
        <div className="coa__ai-stats">
          <span className="coa__stat-badge">
            <span className="coa__stat-dot" />
            {rules.length} Active Rules
          </span>
        </div>
      </div>

      {/* ── Add / Edit Form Card ── */}
      <div className="coa__add-card">
        <div className="coa__add-header">
          <Plus size={13} style={{ color: '#0d9488' }} />
          <span>{editId ? 'Edit COA Mapping Rule' : 'Create New COA Mapping Rule'}</span>
        </div>

        <div className="coa__add-row">
          <select
            className="coa__sel-ai coa__sel-ai--head"
            value={form.exp_head}
            onChange={e => setForm(f => ({ ...f, exp_head: e.target.value }))}
          >
            <option value="">— EXP HEAD (Required) * —</option>
            {EXP_HEADS.map(h => <option key={h} value={h}>{h}</option>)}
          </select>

          <div className="coa__vendor-wrap" ref={vendorRef}>
            <input
              className="coa__inp-ai"
              placeholder="Vendor name (optional)..."
              value={form.vendor_name}
              autoComplete="off"
              onChange={e => {
                const val = e.target.value
                setForm(f => ({ ...f, vendor_name: val }))
                setVendorQuery(val)
                setVendorOpen(true)
              }}
              onFocus={() => { setVendorQuery(form.vendor_name); setVendorOpen(true) }}
            />
            {vendorOpen && (
              <ul className="coa__vendor-list">
                {vendors
                  .filter(v => !vendorQuery || v.name.toLowerCase().includes(vendorQuery.toLowerCase()))
                  .slice(0, 50)
                  .map(v => (
                    <li
                      key={v.supplier_id}
                      className="coa__vendor-opt"
                      onMouseDown={e => {
                        e.preventDefault()
                        setForm(f => ({ ...f, vendor_name: v.name }))
                        setVendorOpen(false)
                      }}
                    >
                      {v.name}
                    </li>
                  ))
                }
                {vendors.filter(v => !vendorQuery || v.name.toLowerCase().includes(vendorQuery.toLowerCase())).length === 0 && (
                  <li className="coa__vendor-empty">No vendors found</li>
                )}
              </ul>
            )}
          </div>

          <select
            className="coa__sel-ai coa__sel-ai--cat"
            value={form.category}
            onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
          >
            {categoryOptions.map(c => <option key={c} value={c}>{c || 'Category (optional)'}</option>)}
          </select>

          <button className="coa__add-btn-ai" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving…' : editId ? <><Check size={13} /> Update Rule</> : <><Plus size={13} /> Add Rule</>}
          </button>
          {editId && <button className="coa__cancel-btn-ai" onClick={resetForm}>Cancel</button>}
        </div>
        {error && <div className="coa__err">{error}</div>}
      </div>

      {/* ── Rules Table Card ── */}
      <div className="coa__card">
        <div className="coa__card-header">
          <div className="coa__card-title">
            <BookOpen size={14} className="coa__icon-teal" /> COA Mapping Rules
          </div>
          <span className="coa__card-count">Match Hierarchy: Vendor + Category → Vendor → Category → OTHER</span>
        </div>

        {rules.length === 0 ? (
          <div className="coa__empty-state">
            <div className="coa__empty-icon"><BookOpen size={24} /></div>
            <div className="coa__empty-title">No COA Rules Defined</div>
            <div className="coa__empty-sub">Add your first mapping rule above to automate vendor and category expense head assignments.</div>
          </div>
        ) : (
          <table className="coa__table">
            <colgroup>
              <col style={{ width: '28%' }} />
              <col style={{ width: '34%' }} />
              <col style={{ width: '24%' }} />
              <col style={{ width: '14%' }} />
            </colgroup>
            <thead>
              <tr>
                <th>EXP HEAD</th>
                <th>Vendor Match</th>
                <th>Category</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.map(r => (
                <tr key={r.id} className={`coa__tr ${editId === r.id ? 'coa__tr--editing' : ''}`}>
                  <td>
                    <span className="coa__badge-pill">
                      <span className="coa__badge-dot" />
                      {r.exp_head}
                    </span>
                  </td>
                  <td>
                    {r.vendor_name ? (
                      <span className="coa__cell-vendor">
                        <Building2 size={13} className="coa__cell-icon" />
                        {r.vendor_name}
                      </span>
                    ) : (
                      <span className="coa__dash">— Any Vendor —</span>
                    )}
                  </td>
                  <td>
                    {r.category ? (
                      <span className="coa__cell-cat">
                        <Tag size={12} className="coa__cell-icon" />
                        {r.category}
                      </span>
                    ) : (
                      <span className="coa__dash">— Any Category —</span>
                    )}
                  </td>
                  <td className="coa__action-cell">
                    <div className="coa__action-wrap">
                      <button
                        className={`coa__action-btn ${editId === r.id ? 'coa__action-btn--secondary' : 'coa__action-btn--primary'}`}
                        onClick={() => editId === r.id ? resetForm() : startEdit(r)}
                        title={editId === r.id ? 'Cancel edit' : 'Edit rule'}
                      >
                        {editId === r.id ? 'Cancel' : 'Edit'}
                      </button>
                      <button
                        className="coa__del-btn"
                        onClick={() => handleDelete(r.id)}
                        title="Remove rule"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Department-aware chain editor wrapper ─────────────────────────────────────
function DeptChainEditor({ companyId }: { companyId?: string }) {
  const [departments, setDepartments] = useState<string[]>([])
  const [activeDept, setActiveDept]   = useState<string | null>(null)
  const [newDept, setNewDept]         = useState('')
  const [adding, setAdding]           = useState(false)
  const [loading, setLoading]         = useState(true)

  const reload = () => {
    setLoading(true)
    fetchDepartments(companyId).then(depts => {
      setDepartments(depts)
      setActiveDept(d => d && depts.includes(d) ? d : depts[0] ?? null)
    }).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { reload() }, [companyId])

  async function handleAddDept() {
    if (!newDept.trim()) return
    await addDepartment(newDept.trim(), companyId).catch(() => {})
    setNewDept(''); setAdding(false)
    reload()
  }

  async function handleDeleteDept(dept: string) {
    if (!confirm(`Delete department "${dept}" and its chain?`)) return
    await deleteDepartment(dept, companyId).catch(() => {})
    reload()
  }

  if (loading) return <div style={{ padding: 24, color: '#9ca3af', fontSize: 13 }}>Loading departments…</div>

  return (
    <div>
      <div className="dept-tabs">
        {departments.map(dept => (
          <div key={dept} className={`dept-tab-wrap${activeDept === dept ? ' dept-tab-wrap--active' : ''}`}>
            <button
              className={`dept-tab${activeDept === dept ? ' dept-tab--active' : ''}`}
              onClick={() => setActiveDept(dept)}
            >
              {dept}
            </button>
            <button className="dept-tab-del" title="Delete department" onClick={() => handleDeleteDept(dept)}>
              <X size={10} />
            </button>
          </div>
        ))}
        {adding ? (
          <div style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: -2 }}>
            <input
              autoFocus
              className="dept-add-input"
              placeholder="Department name"
              value={newDept}
              onChange={e => setNewDept(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleAddDept(); if (e.key === 'Escape') setAdding(false) }}
            />
            <button className="dept-add-confirm" onClick={handleAddDept}><Check size={12} /></button>
            <button className="dept-add-cancel" onClick={() => { setAdding(false); setNewDept('') }}><X size={12} /></button>
          </div>
        ) : (
          <button className="dept-tab-add" onClick={() => setAdding(true)}>
            <Plus size={12} /> Add Department
          </button>
        )}
      </div>

      {departments.length === 0 && !adding && (
        <div style={{ padding: '24px 0', color: '#9ca3af', fontSize: 13 }}>
          No departments yet. Add one above to create department-specific approval chains.
        </div>
      )}

      {activeDept && (
        <ChainEditor key={activeDept} companyId={companyId} department={activeDept} />
      )}

      {!activeDept && departments.length === 0 && (
        <ChainEditor companyId={companyId} />
      )}

      <style>{`
        .dept-tabs { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 16px; border-bottom: 2px solid #e5e7eb; padding-bottom: 0; align-items: flex-end; }
        .dept-tab-wrap { display: flex; align-items: center; border-bottom: 2px solid transparent; margin-bottom: -2px; }
        .dept-tab-wrap--active { border-bottom-color: #4f46e5; }
        .dept-tab { padding: 8px 12px 8px 14px; font-size: 13px; font-weight: 500; color: #6b7280; background: none; border: none; cursor: pointer; transition: color 0.15s; border-radius: 4px 0 0 0; }
        .dept-tab:hover { color: #111827; }
        .dept-tab--active { color: #4f46e5; }
        .dept-tab-del { display: flex; align-items: center; padding: 4px 6px 8px 2px; background: none; border: none; color: #d1d5db; cursor: pointer; border-radius: 0 4px 0 0; }
        .dept-tab-del:hover { color: #ef4444; }
        .dept-tab-add { display: flex; align-items: center; gap: 4px; padding: 6px 12px; font-size: 12px; font-weight: 500; color: #6366f1; background: none; border: 1.5px dashed #c7d2fe; border-radius: 6px; cursor: pointer; margin-bottom: 4px; transition: all 0.15s; }
        .dept-tab-add:hover { background: #eef2ff; border-color: #6366f1; }
        .dept-add-input { font-size: 12px; border: 1.5px solid #6366f1; border-radius: 6px; padding: 5px 10px; outline: none; font-family: inherit; }
        .dept-add-confirm { display: flex; align-items: center; padding: 5px 8px; background: #4f46e5; color: white; border: none; border-radius: 5px; cursor: pointer; }
        .dept-add-cancel { display: flex; align-items: center; padding: 5px 8px; background: #f3f4f6; color: #6b7280; border: none; border-radius: 5px; cursor: pointer; }
      `}</style>
    </div>
  )
}

const CAT_TH = { padding: '6px 10px', textAlign: 'left' as const, fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase' as const, letterSpacing: '0.05em' }
const CAT_SEL = { fontSize: 12, padding: '3px 6px', border: '1px solid #d1d5db', borderRadius: 4, background: '#fff', color: '#374151', cursor: 'pointer' }

// ── Categories Manager ───────────────────────────────────────────────────────
function CategoriesManager({ companyId, onChanged }: { companyId?: string; onChanged?: (cats: string[]) => void }) {
  const [custom, setCustom]           = useState<Category[]>([])
  const [expHeadMap, setExpHeadMap]   = useState<Record<string, string>>({})
  const [loading, setLoading]         = useState(true)
  const [newName, setNewName]         = useState('')
  const [newExpHead, setNewExpHead]   = useState('')
  const [saving, setSaving]           = useState(false)
  const [error, setError]             = useState('')

  useEffect(() => {
    Promise.all([
      fetchCategories(companyId),
      fetchCategoryExpHeads(companyId),
    ]).then(([cats, heads]) => { setCustom(cats); setExpHeadMap(heads) })
      .finally(() => setLoading(false))
  }, [companyId])

  async function handleChangeExpHead(name: string, expHead: string) {
    try {
      await setCategoryExpHead(name, expHead || null, companyId)
      setExpHeadMap(m => expHead ? { ...m, [name]: expHead } : Object.fromEntries(Object.entries(m).filter(([k]) => k !== name)))
      setCustom(c => c.map(x => x.name === name ? { ...x, exp_head: expHead || null } : x))
    } catch (e) {
      setError('Failed to save exp head — please try again')
    }
  }

  async function handleAdd() {
    const name = newName.trim()
    if (!name) { setError('Name is required'); return }
    if (BASE_CATEGORIES.map(c => c.toLowerCase()).includes(name.toLowerCase())) {
      setError('This is already a built-in category'); return
    }
    setSaving(true); setError('')
    try {
      const created = await createCategory(name, newExpHead, companyId)
      setCustom(c => [...c, created])
      if (newExpHead) setExpHeadMap(m => ({ ...m, [name]: newExpHead }))
      addCustomCategory(name)
      setNewName(''); setNewExpHead('')
      onChanged?.(getAllCategories())
    } catch { setError('Save failed') }
    finally { setSaving(false) }
  }

  async function handleDelete(cat: Category) {
    await deleteCategory(cat.id, companyId)
    setCustom(c => c.filter(x => x.id !== cat.id))
    setExpHeadMap(m => Object.fromEntries(Object.entries(m).filter(([k]) => k !== cat.name)))
    removeCustomCategory(cat.name)
    onChanged?.(getAllCategories())
  }

  const EXP_HEAD_THEMES: Record<string, { bg: string; color: string; border: string; dot: string }> = {
    ACCOUNTS:       { bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0', dot: '#22c55e' },
    ADMINISTRATION: { bg: '#f8fafc', color: '#334155', border: '#cbd5e1', dot: '#64748b' },
    ASSETS:         { bg: '#f0fdfa', color: '#0f766e', border: '#99f6e4', dot: '#14b8a6' },
    'DUTIES & TAXES': { bg: '#f8fafc', color: '#334155', border: '#cbd5e1', dot: '#64748b' },
    IT:             { bg: '#eef2ff', color: '#4338ca', border: '#c7d2fe', dot: '#6366f1' },
    LEGAL:          { bg: '#f8fafc', color: '#334155', border: '#cbd5e1', dot: '#64748b' },
    'MAN POWER':    { bg: '#eef2ff', color: '#4338ca', border: '#c7d2fe', dot: '#6366f1' },
    MARKETING:      { bg: '#fdf2f8', color: '#be185d', border: '#fbcfe8', dot: '#ec4899' },
    OFFICE:         { bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0', dot: '#16a34a' },
    PANTRY:         { bg: '#fff7ed', color: '#c2410c', border: '#ffedd5', dot: '#f97316' },
    PROFESSIONAL:   { bg: '#f8fafc', color: '#334155', border: '#cbd5e1', dot: '#64748b' },
    RENT:           { bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0', dot: '#22c55e' },
    UTILITY:        { bg: '#fffbeb', color: '#b45309', border: '#fde68a', dot: '#f59e0b' },
    OTHER:          { bg: '#f8fafc', color: '#475569', border: '#e2e8f0', dot: '#94a3b8' },
  }

  const expHeadSelect = (name: string) => (
    <select
      value={expHeadMap[name] ?? ''}
      onChange={e => handleChangeExpHead(name, e.target.value)}
      className="cat__sel-ai"
    >
      <option value="">— Select Exp Head —</option>
      {EXP_HEADS.map(h => <option key={h} value={h}>{h}</option>)}
    </select>
  )

  const expBadge = (name: string) => {
    const head = expHeadMap[name]
    if (!head) return null
    const theme = EXP_HEAD_THEMES[head] ?? { bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0', dot: '#22c55e' }
    return (
      <span className="cat__head-badge" style={{ background: theme.bg, color: theme.color, borderColor: theme.border }}>
        <span className="cat__head-dot" style={{ background: theme.dot }} />
        {head}
      </span>
    )
  }

  return (
    <div className="cat__wrap">
      {/* ── AI Header Banner ── */}
      <div className="cat__ai-header">
        <div className="cat__ai-left">
          <div className="cat__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="cat__ai-title">Expense Category Rules Engine</div>
            <div className="cat__ai-sub">Agents automatically map incoming bills, vendor invoices, and COA rules to these expense heads.</div>
          </div>
        </div>
        <div className="cat__ai-stats">
          <span className="cat__stat-badge cat__stat-badge--slate">
            <span className="cat__stat-dot cat__stat-dot--slate" />
            {BASE_CATEGORIES.length} Built-in
          </span>
          <span className="cat__stat-badge cat__stat-badge--green">
            <span className="cat__stat-dot cat__stat-dot--green" />
            {custom.length} Custom
          </span>
        </div>
      </div>

      {/* ── Grid of Category Cards ── */}
      <div className="cat__grid">
        {/* ── Built-in Categories Card ── */}
        <div className="cat__card">
          <div className="cat__card-header">
            <div className="cat__card-title">
              <Tag size={14} className="cat__icon-green" /> Built-in Categories
            </div>
            <span className="cat__card-count">{BASE_CATEGORIES.length} System Default</span>
          </div>

          {loading ? (
            <div className="cat__loading">Loading categories…</div>
          ) : (
            <table className="cat__table">
              <colgroup>
                <col style={{ width: '55%' }} />
                <col style={{ width: '45%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Category Name</th>
                  <th>Expense Head Mapping</th>
                </tr>
              </thead>
              <tbody>
                {BASE_CATEGORIES.map(c => (
                  <tr key={c} className="cat__tr">
                    <td>
                      <div className="cat__name-cell">
                        {expBadge(c)}
                        <span className="cat__name-text">{c}</span>
                      </div>
                    </td>
                    <td>{expHeadSelect(c)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Custom Categories Card ── */}
        <div className="cat__card">
          <div className="cat__card-header">
            <div className="cat__card-title">
              <Layers size={14} className="cat__icon-green" /> Custom Categories
            </div>
            <span className="cat__card-count">{custom.length} Custom</span>
          </div>

          {loading ? (
            <div className="cat__loading">Loading custom categories…</div>
          ) : custom.length === 0 ? (
            <div className="cat__empty-state">
              <div className="cat__empty-icon"><Tag size={22} /></div>
              <div className="cat__empty-title">No Custom Categories</div>
              <div className="cat__empty-sub">Create company-specific expense categories below to extend AI mapping.</div>
            </div>
          ) : (
            <table className="cat__table">
              <colgroup>
                <col style={{ width: '50%' }} />
                <col style={{ width: '40%' }} />
                <col style={{ width: '10%' }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Category Name</th>
                  <th>Expense Head</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {custom.map(c => (
                  <tr key={c.id} className="cat__tr">
                    <td>
                      <div className="cat__name-cell">
                        {expBadge(c.name)}
                        <span className="cat__name-text">{c.name}</span>
                      </div>
                    </td>
                    <td>{expHeadSelect(c.name)}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button className="cat__del-btn" onClick={() => handleDelete(c)} title="Remove category">
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* ── Add Category Bar ── */}
          <div className="cat__add-card">
            <div className="cat__add-header">
              <Plus size={13} style={{ color: '#4f46e5' }} />
              <span>Add New Custom Category</span>
            </div>
            <div className="cat__add-row">
              <input
                className="cat__inp-ai"
                placeholder="e.g. Cloud Services, Health..."
                value={newName}
                onChange={e => { setNewName(e.target.value); setError('') }}
                onKeyDown={e => e.key === 'Enter' && handleAdd()}
              />
              <select
                className="cat__sel-ai cat__sel-ai--add"
                value={newExpHead}
                onChange={e => setNewExpHead(e.target.value)}
              >
                <option value="">Exp Head (Optional)</option>
                {EXP_HEADS.map(h => <option key={h} value={h}>{h}</option>)}
              </select>
              <button className="cat__add-btn-ai" onClick={handleAdd} disabled={saving}>
                {saving ? 'Adding…' : <><Plus size={13} /> Add Category</>}
              </button>
            </div>
            {error && <div className="cat__err">{error}</div>}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── User Manager (admin only) ────────────────────────────────────────────────
const BLANK_FORM = { username: '', password: '', name: '', email: '', role: 'member' as string, department: '' }

function UserManager({ companyId, currentUser }: { companyId?: string; currentUser?: string }) {
  const [users, setUsers]         = useState<CompanyUser[]>([])
  const [depts, setDepts]         = useState<string[]>([])
  const [loading, setLoading]     = useState(true)
  const [adding, setAdding]       = useState(false)
  const [form, setForm]           = useState(BLANK_FORM)
  const [saving, setSaving]       = useState(false)
  const [err, setErr]             = useState('')
  const [newDept, setNewDept]     = useState('')
  const [addingDept, setAddingDept] = useState(false)
  const [editingUser, setEditingUser] = useState<string | null>(null)
  const [editForm, setEditForm]   = useState<{ name: string; email: string; role: string; department: string; password: string }>({ name: '', email: '', role: '', department: '', password: '' })

  const reload = () => {
    setLoading(true)
    Promise.all([
      fetchUsers(companyId),
      fetchDepartments(companyId),
    ]).then(([u, d]) => { setUsers(u); setDepts(d) }).finally(() => setLoading(false))
  }

  useEffect(() => { reload() }, [companyId])

  const handleAdd = async () => {
    setErr('')
    if (!form.username || !form.password || !form.name) { setErr('Username, name and password are required.'); return }
    setSaving(true)
    try {
      await createUser({ ...form, department: form.department || undefined, company_id: companyId })
      setAdding(false)
      setForm(BLANK_FORM)
      reload()
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Failed to create user.')
    } finally {
      setSaving(false)
    }
  }

  const handleAddDept = async () => {
    const name = newDept.trim()
    if (!name) return
    try {
      await addDepartment(name, companyId)
      setDepts(d => [...d, name].sort())
      setForm(p => ({ ...p, department: name }))
      setNewDept('')
      setAddingDept(false)
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Failed to create department. Please try again.')
    }
  }

  const handleDelete = async (username: string) => {
    if (!confirm(`Delete user "${username}"?`)) return
    await deleteUser(username, companyId)
    reload()
  }

  const startEdit = (u: CompanyUser) => {
    setEditingUser(u.username)
    setEditForm({ name: u.name, email: u.email, role: u.role, department: u.department ?? '', password: '' })
    setErr('')
  }

  const handleSaveEdit = async () => {
    if (!editingUser) return
    if (!window.confirm(`Save changes to "${editingUser}"?`)) return
    if (!window.confirm('Are you sure? This will update the team member permanently.')) return
    setSaving(true); setErr('')
    try {
      await updateUser(editingUser, {
        name: editForm.name || undefined,
        email: editForm.email || undefined,
        role: editForm.role || undefined,
        department: editForm.department || undefined,
        password: editForm.password || undefined,
      }, companyId)
      setEditingUser(null)
      reload()
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? 'Failed to save changes.')
    } finally { setSaving(false) }
  }

  return (
    <div className="sp__team-wrap">
      {loading ? (
        <div style={{ padding: 24, color: '#9ca3af', fontSize: 13 }}>Loading…</div>
      ) : (
        <div className="sp__team-card">
          <table className="sp__team-table">
            <colgroup>
              <col className="sp__col-member" />
              <col className="sp__col-email" />
              <col className="sp__col-role" />
              <col className="sp__col-dept" />
              <col className="sp__col-username" />
              <col className="sp__col-action" />
            </colgroup>
            <thead>
              <tr>
                <th>Member</th>
                <th>Email</th>
                <th>Role</th>
                <th>Department</th>
                <th>Username</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => {
                const c = ROLE_COLORS[u.role] ?? { bg: '#f3f4f6', color: '#374151', dot: '#9ca3af' }
                const isMe = u.username === currentUser
                const isEditing = editingUser === u.username
                return (
                  <React.Fragment key={u.username}>
                    <tr className={`sp__tr ${isMe ? 'sp__tr--me' : ''} ${isEditing ? 'sp__tr--editing' : ''}`}>
                      <td>
                        <div className="sp__member-cell">
                          <div className="sp__avatar" style={{ background: c.bg, color: c.color }}>{u.name.charAt(0)}</div>
                          <div>
                            <div className="sp__member-name">{u.name}</div>
                            {isMe && <span className="sp__me-badge">You</span>}
                          </div>
                        </div>
                      </td>
                      <td className="sp__td-mono">{u.email}</td>
                      <td>
                        <span className="sp__role-badge" style={{ background: c.bg, color: c.color }}>
                          <span className="sp__badge-dot" style={{ background: c.dot }} />
                          {ROLE_LABELS[u.role as Role] ?? u.role}
                        </span>
                      </td>
                      <td className="sp__td-mono">{u.department ?? '—'}</td>
                      <td className="sp__td-mono">{u.username}</td>
                      <td className="sp__action-cell">
                        <div className="sp__action-wrap">
                          <button
                            onClick={() => isEditing ? setEditingUser(null) : startEdit(u)}
                            className={`sp__action-btn ${isEditing ? 'sp__action-btn--secondary' : 'sp__action-btn--primary'}`}
                            title={isEditing ? 'Cancel' : 'Edit user'}
                          >
                            {isEditing ? 'Cancel' : 'Edit'}
                          </button>
                          {!isMe && (
                            <button
                              onClick={() => handleDelete(u.username)}
                              className="sp__delete-btn"
                              title="Remove user"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {isEditing && (
                      <tr className="sp__edit-row">
                        <td colSpan={6} style={{ padding: '14px 18px' }}>
                          {err && <div style={{ fontSize: 12, color: '#b42318', background: '#fef3f2', padding: '8px 10px', borderRadius: 8, marginBottom: 10, border: '1px solid #f5c0b7' }}>{err}</div>}
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                            {([
                              { key: 'name',  label: 'Full Name' },
                              { key: 'email', label: 'Email' },
                              { key: 'password', label: 'New Password', placeholder: 'Leave blank to keep current', type: 'password' },
                            ] as const).map(f => (
                              <div key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{f.label}</label>
                                <input
                                  type={('type' in f ? f.type : 'text') as string}
                                  value={editForm[f.key]}
                                  placeholder={'placeholder' in f ? f.placeholder : ''}
                                  onChange={e => setEditForm(p => ({ ...p, [f.key]: e.target.value }))}
                                  style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}
                                />
                              </div>
                            ))}
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                              <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Role</label>
                              <select value={editForm.role} onChange={e => setEditForm(p => ({ ...p, role: e.target.value }))}
                                style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}>
                                {Object.entries(ROLE_LABELS).map(([r, l]) => <option key={r} value={r}>{l}</option>)}
                              </select>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                              <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Department</label>
                              {addingDept ? (
                                <div style={{ display: 'flex', gap: 4 }}>
                                  <input autoFocus value={newDept} onChange={e => setNewDept(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') handleAddDept(); if (e.key === 'Escape') { setAddingDept(false); setNewDept('') } }}
                                    placeholder="New department name"
                                    style={{ flex: 1, padding: '8px 10px', border: '1px solid #6d5efc', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}
                                  />
                                  <button onClick={handleAddDept} style={{ padding: '8px 10px', background: '#4f46e5', color: '#fff', border: 'none', borderRadius: 8, fontSize: 12, cursor: 'pointer' }}>Add</button>
                                  <button onClick={() => { setAddingDept(false); setNewDept('') }} style={{ padding: '8px 8px', background: '#f3f4f6', color: '#6b7280', border: 'none', borderRadius: 8, cursor: 'pointer' }}><X size={12} /></button>
                                </div>
                              ) : (
                                <select value={editForm.department}
                                  onChange={e => e.target.value === '__new__' ? setAddingDept(true) : setEditForm(p => ({ ...p, department: e.target.value }))}
                                  style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}>
                                  <option value="">— None —</option>
                                  {depts.map(d => <option key={d} value={d}>{d}</option>)}
                                  <option value="__new__">+ Add new department…</option>
                                </select>
                              )}
                            </div>
                          </div>
                          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                            <button onClick={handleSaveEdit} disabled={saving}
                              style={{ padding: '8px 16px', background: '#4f46e5', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 700, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
                              {saving ? 'Saving…' : 'Save Changes'}
                            </button>
                            <button onClick={() => { setEditingUser(null); setErr('') }}
                              style={{ padding: '8px 12px', background: '#f3f4f6', color: '#6b7280', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {adding ? (
        <div className="sp__team-add-card">
          <div className="sp__team-add-title">Add New User</div>
          {err && <div className="sp__team-alert">{err}</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
            {([
              { key: 'name',     label: 'Full Name',    placeholder: 'Pooja Gautam' },
              { key: 'email',    label: 'Email',         placeholder: 'pooja@company.in' },
              { key: 'username', label: 'Username',      placeholder: 'pooja' },
              { key: 'password', label: 'Password',      placeholder: 'Choose password', type: 'password' },
            ] as const).map(f => (
              <div key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{f.label}</label>
                <input
                  type={('type' in f ? f.type : 'text') as string}
                  value={form[f.key]}
                  onChange={e => setForm(p => ({ ...p, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}
                />
              </div>
            ))}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Role</label>
              <select value={form.role} onChange={e => setForm(p => ({ ...p, role: e.target.value }))}
                style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}>
                {Object.entries(ROLE_LABELS).map(([r, l]) => <option key={r} value={r}>{l}</option>)}
              </select>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', letterSpacing: '0.04em', textTransform: 'uppercase' }}>Department</label>
              {addingDept ? (
                <div style={{ display: 'flex', gap: 4 }}>
                  <input autoFocus value={newDept} onChange={e => setNewDept(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') handleAddDept(); if (e.key === 'Escape') { setAddingDept(false); setNewDept('') } }} placeholder="New department name" style={{ flex: 1, padding: '8px 10px', border: '1px solid #6d5efc', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }} />
                  <button onClick={handleAddDept} style={{ padding: '8px 10px', background: '#4f46e5', color: '#fff', border: 'none', borderRadius: 8, fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap' }}>Add</button>
                  <button onClick={() => { setAddingDept(false); setNewDept('') }} style={{ padding: '8px 8px', background: '#f3f4f6', color: '#6b7280', border: 'none', borderRadius: 8, cursor: 'pointer' }}><X size={12} /></button>
                </div>
              ) : (
                <select value={form.department} onChange={e => { if (e.target.value === '__new__') { setAddingDept(true) } else setForm(p => ({ ...p, department: e.target.value })) }} style={{ padding: '8px 10px', border: '1px solid #dfe3ea', borderRadius: 8, fontSize: 13, fontFamily: 'inherit', outline: 'none', background: '#fff', color: '#111827' }}>
                  <option value="">— None —</option>
                  {depts.map(d => <option key={d} value={d}>{d}</option>)}
                  <option value="__new__">+ Add new department…</option>
                </select>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button onClick={handleAdd} disabled={saving} style={{ padding: '8px 16px', background: '#4f46e5', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 700, cursor: 'pointer', opacity: saving ? 0.6 : 1 }}>
              {saving ? 'Adding…' : 'Add User'}
            </button>
            <button onClick={() => { setAdding(false); setForm(BLANK_FORM); setErr('') }} style={{ padding: '8px 12px', background: '#f3f4f6', color: '#6b7280', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div style={{ paddingTop: 12 }}>
          <button onClick={() => setAdding(true)} className="sp__add-user-btn">
            <Plus size={13} /> Add User
          </button>
        </div>
      )}
    </div>
  )
}

// ── TDS Configuration ────────────────────────────────────────────────────────

type TdsTab = 'sections' | 'categories' | 'mappings' | 'overrides' | 'fy_state' | 'preferences'

const MODE_LABELS: Record<string, string> = {
  cumulative: 'Cumulative',
  always_deduct: 'Always deduct',
  per_bill_threshold: 'Per-bill',
}
const MODE_COLORS: Record<string, { bg: string; color: string }> = {
  always_deduct: { bg: '#E1F5EE', color: '#0F6E56' },
  per_bill_threshold: { bg: '#E6F1FB', color: '#185FA5' },
  cumulative: { bg: '#F1EFE8', color: '#5F5E5A' },
}

function fmtRate(r: number) { return `${r}%` }
function fmtINR(n: number | null) {
  if (n == null) return '—'
  return '₹' + Number(n).toLocaleString('en-IN')
}
function fmtThreshold(type: string, amt: number | null, amt2: number | null): string {
  if (type === 'NONE') return '—'
  if (type === 'PER_TXN') return `${fmtINR(amt)}/txn`
  if (type === 'AGGREGATE_FY') return `${fmtINR(amt)}/FY`
  if (type === 'PER_MONTH') return `${fmtINR(amt)}/month`
  if (type === 'EXCESS_OF') return `> ${fmtINR(amt)}`
  if (type === 'DUAL') return `${fmtINR(amt)}/txn · ${fmtINR(amt2)}/FY`
  return type
}

const THRESHOLD_TYPES = ['NONE', 'PER_TXN', 'AGGREGATE_FY', 'PER_MONTH', 'EXCESS_OF', 'DUAL']
const THRESHOLD_LABELS: Record<string, string> = {
  NONE: 'None', PER_TXN: 'Per Transaction', AGGREGATE_FY: 'Aggregate FY',
  PER_MONTH: 'Per Month', EXCESS_OF: 'Excess Of', DUAL: 'Dual (txn + FY)',
}

// The engine reads GST_EXCLUSIVE and TAXABLE identically (TDS on the taxable
// value); anything else means TDS on the invoice total. TAXABLE is kept so
// editing a section that already carries it doesn't silently change the base.
const BASE_CALCULATIONS = ['GST_EXCLUSIVE', 'GST_INCLUSIVE', 'TAXABLE']
const BASE_CALC_LABELS: Record<string, string> = {
  GST_EXCLUSIVE: 'Before GST (taxable value)',
  GST_INCLUSIVE: 'On invoice total (incl. GST)',
  TAXABLE: 'Before GST (legacy label)',
}

const EMPTY_SECTION = {
  return_code: '', old_section_label: '', new_section_label: '', nature_of_payment: '',
  default_rate: '', pan_missing_rate: '20', threshold_type: 'NONE',
  threshold_amount: '', threshold_amount_2: '', base_calculation: 'GST_EXCLUSIVE',
  effective_from: '2024-04-01',
}

function TdsConfigManager({ companyId, disabled = false }: { companyId?: string; disabled?: boolean }) {
  const [tab, setTab] = useState<TdsTab>('sections')

  // ── Sections state
  const [sections, setSections] = useState<TdsSection[]>([])
  const [secLoading, setSecLoading] = useState(true)
  const [secForm, setSecForm] = useState(EMPTY_SECTION)
  const [secEditId, setSecEditId] = useState<string | null>(null)
  const [secErr, setSecErr] = useState('')

  // ── Categories state
  const [categories, setCategories] = useState<VendorCategory[]>([])
  const [catLoading, setCatLoading] = useState(true)
  const [catName, setCatName] = useState('')
  const [catDesc, setCatDesc] = useState('')
  const [catEditId, setCatEditId] = useState<string | null>(null)
  const [catErr, setCatErr] = useState('')

  // ── Mappings state
  const [rules, setRules] = useState<TdsMappingRule[]>([])
  const [mapLoading, setMapLoading] = useState(true)
  const [mapSecId, setMapSecId] = useState('')
  const [mapCatId, setMapCatId] = useState('')
  const [mapRate, setMapRate] = useState('')
  const [mapDesc, setMapDesc] = useState('')
  const [mapErr, setMapErr] = useState('')

  // ── Overrides state
  const [overrides, setOverrides] = useState<VendorTdsOverride[]>([])
  const [ovLoading, setOvLoading] = useState(true)
  const [ovForm, setOvForm] = useState({ supplier_id: '', tds_section_id: '', tds_mode: 'cumulative', override_rate: '', certificate_no: '', effective_from: '', valid_to: '', mode_reason: '' })
  const [ovErr, setOvErr] = useState('')
  const [catchupAlert, setCatchupAlert] = useState<{ override_id: string; summary: CatchupSummary } | null>(null)
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [vendorsLoaded, setVendorsLoaded] = useState(false)

  // ── FY State
  const [fyStates, setFyStates] = useState<VendorTdsFyState[]>([])
  const [fyLoading, setFyLoading] = useState(true)

  // ── Preferences state
  const [prefs, setPrefs] = useState<TdsPreferences>({
    deduct_on: 'BOOKING', base_default: 'GST_EXCLUSIVE',
    auto_apply: true, fy_start_month: 4, pan_missing_rate: 20,
    max_catchup_pct: 50, max_backdate_days: 90,
  })
  const [prefSaved, setPrefSaved] = useState(false)

  // ── Load on tab change
  useEffect(() => {
    if (tab === 'sections' && secLoading) {
      fetchTdsSections(companyId).then(setSections).finally(() => setSecLoading(false))
    }
    if (tab === 'categories' && catLoading) {
      fetchVendorCategories(companyId).then(setCategories).finally(() => setCatLoading(false))
    }
    if (tab === 'mappings' && mapLoading) {
      Promise.all([
        fetchMappingRules(companyId),
        fetchTdsSections(companyId),
        fetchVendorCategories(companyId),
      ]).then(([r, s, c]) => { setRules(r); setSections(s); setCategories(c) })
        .finally(() => setMapLoading(false))
    }
    if (tab === 'overrides' && ovLoading) {
      Promise.all([
        fetchVendorOverrides(),
        fetchTdsSections(companyId),
        !vendorsLoaded ? fetchVendors(false, companyId) : Promise.resolve(vendors),
      ]).then(([o, s, v]) => { setOverrides(o); setSections(s); setVendors(v); setVendorsLoaded(true) })
        .finally(() => setOvLoading(false))
    }
    if (tab === 'fy_state' && fyLoading) {
      fetchFyState().then(d => setFyStates(Array.isArray(d) ? d : [])).catch(() => setFyStates([])).finally(() => setFyLoading(false))
    }
    if (tab === 'preferences') {
      fetchTdsPreferences(companyId).then(setPrefs)
    }
  }, [tab])

  // ── Sections handlers
  async function secSave() {
    setSecErr('')
    if (!secForm.return_code.trim() || !secForm.nature_of_payment.trim()) {
      setSecErr('Return code and nature of payment are required'); return
    }
    const body = {
      return_code: secForm.return_code.trim(),
      old_section_label: secForm.old_section_label.trim() || null,
      new_section_label: secForm.new_section_label.trim() || null,
      nature_of_payment: secForm.nature_of_payment.trim(),
      default_rate: parseFloat(secForm.default_rate) || 0,
      pan_missing_rate: parseFloat(secForm.pan_missing_rate) || 20,
      threshold_type: secForm.threshold_type,
      threshold_amount: secForm.threshold_amount ? parseFloat(secForm.threshold_amount) : null,
      threshold_amount_2: secForm.threshold_amount_2 ? parseFloat(secForm.threshold_amount_2) : null,
      base_calculation: secForm.base_calculation,
      effective_from: secForm.effective_from || '2024-04-01',
    }
    try {
      if (secEditId) {
        await updateTdsSection(secEditId, body)
        setSections(ss => ss.map(s => s.tds_section_id === secEditId ? { ...s, ...body } as TdsSection : s))
      } else {
        const { tds_section_id } = await createTdsSection(body)
        setSections(ss => [...ss, { ...body, tds_section_id, is_active: true, effective_to: null } as TdsSection])
      }
      setSecForm(EMPTY_SECTION); setSecEditId(null)
    } catch { setSecErr('Save failed') }
  }
  async function secDelete(id: string) {
    await deleteTdsSection(id)
    setSections(ss => ss.filter(s => s.tds_section_id !== id))
  }
  function secEdit(s: TdsSection) {
    setSecEditId(s.tds_section_id)
    setSecForm({
      return_code: s.return_code, old_section_label: s.old_section_label ?? '',
      new_section_label: s.new_section_label ?? '',
      nature_of_payment: s.nature_of_payment,
      default_rate: String(s.default_rate), pan_missing_rate: String(s.pan_missing_rate),
      threshold_type: s.threshold_type,
      threshold_amount: s.threshold_amount != null ? String(s.threshold_amount) : '',
      threshold_amount_2: s.threshold_amount_2 != null ? String(s.threshold_amount_2) : '',
      base_calculation: s.base_calculation ?? 'GST_EXCLUSIVE',
      effective_from: s.effective_from,
    })
    setTab('sections')
  }

  // ── Categories handlers
  async function catSave() {
    setCatErr('')
    if (!catName.trim()) { setCatErr('Name is required'); return }
    try {
      if (catEditId) {
        await updateVendorCategory(catEditId, { name: catName.trim(), description: catDesc.trim() || undefined })
        setCategories(cs => cs.map(c => c.category_id === catEditId ? { ...c, name: catName.trim(), description: catDesc.trim() || null } : c))
      } else {
        const { category_id } = await createVendorCategory({ name: catName.trim(), description: catDesc.trim() || undefined, display_order: categories.length * 10 })
        setCategories(cs => [...cs, { category_id, name: catName.trim(), description: catDesc.trim() || null, display_order: cs.length * 10, is_active: true, vendor_count: 0, tds_rate: null, section_code: null, section_label: null }])
      }
      setCatName(''); setCatDesc(''); setCatEditId(null)
    } catch { setCatErr('Save failed') }
  }
  async function catDelete(id: string) {
    try { await deleteVendorCategory(id); setCategories(cs => cs.filter(c => c.category_id !== id)) }
    catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setCatErr(msg ?? 'Cannot delete — vendors may be assigned')
    }
  }

  // ── Mappings handlers
  async function mapAdd() {
    setMapErr('')
    if (!mapSecId) { setMapErr('Select a TDS section'); return }
    const nextPriority = rules.length ? Math.max(...rules.map(r => r.priority)) + 10 : 10
    try {
      const { rule_id } = await createMappingRule({
        tds_section_id: mapSecId,
        vendor_category_id: mapCatId || undefined,
        priority: nextPriority,
        rate_override: mapRate ? parseFloat(mapRate) : undefined,
        description: mapDesc.trim() || undefined,
      })
      const sec = sections.find(s => s.tds_section_id === mapSecId)
      const cat = categories.find(c => c.category_id === mapCatId)
      setRules(rs => [...rs, {
        rule_id, priority: nextPriority, rate_override: mapRate ? parseFloat(mapRate) : null,
        description: mapDesc.trim() || null, is_active: true,
        tds_section_id: mapSecId, section_return_code: sec?.return_code ?? '',
        old_section_label: sec?.old_section_label ?? null, section_nature: sec?.nature_of_payment ?? '',
        vendor_category_id: mapCatId || null, category_name: cat?.name ?? null,
      }])
      setMapSecId(''); setMapCatId(''); setMapRate(''); setMapDesc('')
    } catch { setMapErr('Save failed') }
  }
  async function mapDelete(id: string) {
    await deleteMappingRule(id)
    setRules(rs => rs.filter(r => r.rule_id !== id))
  }

  // ── Preferences handler
  async function prefSave() {
    await saveTdsPreferences(prefs, companyId)
    setPrefSaved(true); setTimeout(() => setPrefSaved(false), 2000)
  }

  const activeSections = sections.filter(s => s.is_active)
  const activeCategories = categories.filter(c => c.is_active)

  return (
    <div className="tds__wrap">
      {/* ── AI Header Banner ── */}
      <div className="tds__ai-header">
        <div className="tds__ai-left">
          <div className="tds__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="tds__ai-title">Tax Deducted at Source (TDS) Compliance Engine</div>
            <div className="tds__ai-sub">Agents compute and apply automated TDS deductions under Indian Tax Laws across vendor payments & bills.</div>
          </div>
        </div>
        <div className="tds__ai-stats">
          <span className="tds__stat-badge">
            <span className="tds__stat-dot" />
            {sections.length} Configured Sections
          </span>
        </div>
      </div>

      {/* Inner tab bar */}
      <div className="tds__tabbar">
        {(['sections', 'categories', 'mappings', 'overrides', 'fy_state', 'preferences'] as TdsTab[]).map(t => (
          <button key={t} className={`tds__tabbtn ${tab === t ? 'tds__tabbtn--active' : ''}`} onClick={() => setTab(t)}>
            {t === 'sections' ? 'Sections' : t === 'categories' ? 'Vendor Categories' : t === 'mappings' ? 'Mapping Rules' : t === 'overrides' ? 'Vendor Overrides' : t === 'fy_state' ? 'FY State' : 'Preferences'}
          </button>
        ))}
      </div>

      {/* ── Sections panel ── */}
      {tab === 'sections' && (
        <div className="tds__panel">
          {/* Add/Edit form */}
          <div className="tds__form-row">
            {/* The payment code that goes on the return is the 4-digit one
                (1027), not the old section number — the placeholder used to say
                "e.g. 194J" here, which invited the section label into this field. */}
            <input className="tds__inp tds__inp--code" placeholder="Payment code e.g. 1027" value={secForm.return_code}
              onChange={e => setSecForm(f => ({ ...f, return_code: e.target.value }))}
              title="4-digit payment code used on the TDS return (Income Tax Act 2025)" />
            <input className="tds__inp tds__inp--label" placeholder="Old section e.g. 194JB" value={secForm.old_section_label}
              onChange={e => setSecForm(f => ({ ...f, old_section_label: e.target.value }))}
              title="Pre-2025 section number, shown throughout the app" />
            <input className="tds__inp tds__inp--label" placeholder="New section e.g. 393(1) Sl.8(iv)" value={secForm.new_section_label}
              onChange={e => setSecForm(f => ({ ...f, new_section_label: e.target.value }))}
              title="Income Tax Act 2025 section reference" />
            <input className="tds__inp tds__inp--nature" placeholder="Nature of payment (required)" value={secForm.nature_of_payment}
              onChange={e => setSecForm(f => ({ ...f, nature_of_payment: e.target.value }))} />
            <input className="tds__inp tds__inp--rate" placeholder="Rate %" value={secForm.default_rate}
              onChange={e => setSecForm(f => ({ ...f, default_rate: e.target.value }))} type="number" step="0.1" />
            <select className="tds__inp tds__inp--thresh" value={secForm.threshold_type}
              onChange={e => setSecForm(f => ({ ...f, threshold_type: e.target.value }))}>
              {THRESHOLD_TYPES.map(t => <option key={t} value={t}>{THRESHOLD_LABELS[t]}</option>)}
            </select>
            {secForm.threshold_type !== 'NONE' && (
              <input className="tds__inp tds__inp--tamt" placeholder="₹ amount" value={secForm.threshold_amount}
                onChange={e => setSecForm(f => ({ ...f, threshold_amount: e.target.value }))} type="number" />
            )}
            {secForm.threshold_type === 'DUAL' && (
              <input className="tds__inp tds__inp--tamt" placeholder="₹ FY aggregate" value={secForm.threshold_amount_2}
                onChange={e => setSecForm(f => ({ ...f, threshold_amount_2: e.target.value }))} type="number" />
            )}
            <select className="tds__inp tds__inp--thresh" value={secForm.base_calculation}
              onChange={e => setSecForm(f => ({ ...f, base_calculation: e.target.value }))}
              title="Whether TDS is deducted on the taxable value or on the GST-inclusive total">
              {BASE_CALCULATIONS.map(b => <option key={b} value={b}>{BASE_CALC_LABELS[b]}</option>)}
            </select>
            <button className="tds__save-btn" onClick={secSave}>
              {secEditId ? <><Save size={12} /> Update</> : <><Plus size={12} /> Add</>}
            </button>
            {secEditId && (
              <button className="tds__cancel-btn" onClick={() => { setSecForm(EMPTY_SECTION); setSecEditId(null); setSecErr('') }}>Cancel</button>
            )}
          </div>
          {secErr && <div className="tds__err">{secErr}</div>}
          {secLoading ? <div className="tds__empty">Loading…</div> : activeSections.length === 0
            ? <div className="tds__empty">No sections defined. Add one above.</div>
            : (
              <table className="tds__table">
                <thead><tr>
                  <th>Code</th><th>Old Section</th><th>New Section</th><th>Nature of Payment</th>
                  <th>Rate</th><th>No-PAN Rate</th><th>Threshold</th><th>Base</th><th></th>
                </tr></thead>
                <tbody>
                  {activeSections.map(s => (
                    <tr key={s.tds_section_id} className={`tds__row ${secEditId === s.tds_section_id ? 'tds__row--editing' : ''}`}>
                      <td><span className="tds__code-badge">{s.return_code}</span></td>
                      <td><span className="tds__soft">{s.old_section_label ?? '—'}</span></td>
                      <td><span className="tds__soft">{s.new_section_label ?? '—'}</span></td>
                      <td style={{ maxWidth: 220 }}>{s.nature_of_payment}</td>
                      <td><strong>{fmtRate(s.default_rate)}</strong></td>
                      <td><span className="tds__soft">{fmtRate(s.pan_missing_rate)}</span></td>
                      <td><span className="tds__thresh-badge">{fmtThreshold(s.threshold_type, s.threshold_amount, s.threshold_amount_2)}</span></td>
                      <td><span className="tds__soft">{BASE_CALC_LABELS[s.base_calculation] ?? s.base_calculation ?? '—'}</span></td>
                      <td className="tds__actions-cell">
                        <div className="tds__action-wrap">
                          <button className="tds__edit-btn" onClick={() => secEdit(s)}>Edit</button>
                          <button className="tds__del-btn" onClick={() => secDelete(s.tds_section_id)} title="Delete section"><Trash2 size={13} /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}

      {/* ── Vendor Categories panel ── */}
      {tab === 'categories' && (
        <div className="tds__panel">
          <div className="tds__form-row">
            <input className="tds__inp tds__inp--nature" placeholder="Category name (required)" value={catName}
              onChange={e => setCatName(e.target.value)} />
            <input className="tds__inp tds__inp--nature" placeholder="Description (optional)" value={catDesc}
              onChange={e => setCatDesc(e.target.value)} />
            <button className="tds__save-btn" onClick={catSave}>
              {catEditId ? <><Save size={12} /> Update</> : <><Plus size={12} /> Add</>}
            </button>
            {catEditId && (
              <button className="tds__cancel-btn" onClick={() => { setCatName(''); setCatDesc(''); setCatEditId(null); setCatErr('') }}>Cancel</button>
            )}
          </div>
          {catErr && <div className="tds__err">{catErr}</div>}
          {catLoading ? <div className="tds__empty">Loading…</div> : activeCategories.length === 0
            ? <div className="tds__empty">No vendor categories. Add one above.</div>
            : (
              <table className="tds__table">
                <thead><tr><th>Category Name</th><th>Description</th><th>Vendors</th><th></th></tr></thead>
                <tbody>
                  {activeCategories.map(c => (
                    <tr key={c.category_id} className={`tds__row ${catEditId === c.category_id ? 'tds__row--editing' : ''}`}>
                      <td style={{ fontWeight: 600 }}>{c.name}</td>
                      <td><span className="tds__soft">{c.description ?? '—'}</span></td>
                      <td>
                        <span className="tds__count-badge">{c.vendor_count} vendor{c.vendor_count !== 1 ? 's' : ''}</span>
                      </td>
                      <td className="tds__actions-cell">
                        <button className="tds__edit-btn" onClick={() => { setCatEditId(c.category_id); setCatName(c.name); setCatDesc(c.description ?? '') }}>Edit</button>
                        <button className="tds__del-btn" onClick={() => catDelete(c.category_id)} title={c.vendor_count > 0 ? 'Vendors assigned' : 'Delete'}><X size={11} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          <div className="tds__hint">These categories appear in the vendor details drawer to assign TDS sections. Separate from COA expense categories.</div>
        </div>
      )}

      {/* ── Mapping Rules panel ── */}
      {tab === 'mappings' && (
        <div className="tds__panel">
          <div className="tds__map-legend">First match wins — rules are checked in priority order (lowest number first)</div>
          <div className="tds__form-row">
            <select className="tds__inp tds__inp--nature" value={mapCatId} onChange={e => setMapCatId(e.target.value)}>
              <option value="">— Any category (catch-all) —</option>
              {activeCategories.map(c => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
            </select>
            <ChevronRight size={14} style={{ color: '#9ca3af', flexShrink: 0 }} />
            <select className="tds__inp tds__inp--nature" value={mapSecId} onChange={e => setMapSecId(e.target.value)}>
              <option value="">— Select TDS section —</option>
              {activeSections.map(s => <option key={s.tds_section_id} value={s.tds_section_id}>{s.return_code}{s.old_section_label ? ` / ${s.old_section_label}` : ''} — {s.nature_of_payment}</option>)}
            </select>
            <input className="tds__inp tds__inp--rate" placeholder="Rate override %" type="number" step="0.1" value={mapRate}
              onChange={e => setMapRate(e.target.value)} title="Leave blank to use section default" />
            <button className="tds__save-btn" onClick={mapAdd}><Plus size={12} /> Add Rule</button>
          </div>
          {mapErr && <div className="tds__err">{mapErr}</div>}
          {mapLoading ? <div className="tds__empty">Loading…</div> : rules.filter(r => r.is_active).length === 0
            ? <div className="tds__empty">No mapping rules. Add one above.</div>
            : (
              <table className="tds__table">
                <thead><tr>
                  <th style={{ width: 48 }}>#</th>
                  <th>Vendor Category</th><th></th><th>TDS Section</th><th>Rate</th><th></th>
                </tr></thead>
                <tbody>
                  {rules.filter(r => r.is_active).sort((a, b) => a.priority - b.priority).map(r => (
                    <tr key={r.rule_id} className="tds__row">
                      <td><span className="tds__priority-num">{r.priority}</span></td>
                      <td>
                        {r.category_name
                          ? <span className="tds__cat-chip">{r.category_name}</span>
                          : <span className="tds__soft">Any category</span>}
                      </td>
                      <td><ChevronRight size={13} style={{ color: '#d1d5db' }} /></td>
                      <td>
                        <span className="tds__code-badge">{r.section_return_code}</span>
                        {r.old_section_label && <span className="tds__soft" style={{ marginLeft: 6 }}>{r.old_section_label}</span>}
                        <span className="tds__soft" style={{ marginLeft: 6 }}>· {r.section_nature}</span>
                      </td>
                      <td>
                        {r.rate_override != null
                          ? <strong>{fmtRate(r.rate_override)}</strong>
                          : <span className="tds__soft">Section default</span>}
                      </td>
                      <td className="tds__actions-cell">
                        <button className="tds__del-btn" onClick={() => mapDelete(r.rule_id)}><X size={11} /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}

      {/* ── Vendor Overrides panel ── */}
      {tab === 'overrides' && (
        <div className="tds__panel">
          <div className="tds__form-row" style={{ flexWrap: 'wrap', gap: 8 }}>
            <select className="tds__inp tds__inp--nature" value={ovForm.supplier_id} onChange={e => setOvForm(f => ({ ...f, supplier_id: e.target.value }))}>
              <option value="">— Select vendor —</option>
              {vendors.map(v => <option key={v.supplier_id} value={v.supplier_id}>{v.name}</option>)}
            </select>
            <select className="tds__inp tds__inp--nature" value={ovForm.tds_section_id} onChange={e => setOvForm(f => ({ ...f, tds_section_id: e.target.value }))}>
              <option value="">— TDS section —</option>
              {activeSections.map(s => <option key={s.tds_section_id} value={s.tds_section_id}>{s.return_code}{s.old_section_label ? ` / ${s.old_section_label}` : ''} — {s.nature_of_payment}</option>)}
            </select>
            <select className="tds__inp" style={{ minWidth: 140 }} value={ovForm.tds_mode} onChange={e => setOvForm(f => ({ ...f, tds_mode: e.target.value }))}>
              <option value="cumulative">Cumulative</option>
              <option value="always_deduct">Always deduct</option>
              <option value="per_bill_threshold">Per-bill threshold</option>
            </select>
            <input className="tds__inp tds__inp--rate" placeholder="Rate override %" type="number" step="0.1" value={ovForm.override_rate}
              onChange={e => setOvForm(f => ({ ...f, override_rate: e.target.value }))} title="Leave blank for section default" />
            <input className="tds__inp" style={{ minWidth: 120 }} placeholder="Effective from" type="date" value={ovForm.effective_from}
              onChange={e => setOvForm(f => ({ ...f, effective_from: e.target.value }))} />
            <input className="tds__inp" style={{ minWidth: 160 }} placeholder="Reason / note" value={ovForm.mode_reason}
              onChange={e => setOvForm(f => ({ ...f, mode_reason: e.target.value }))} />
            <button className="tds__save-btn" onClick={async () => {
              setOvErr('')
              if (!ovForm.supplier_id) { setOvErr('Select a vendor'); return }
              try {
                const res = await createVendorOverride({
                  supplier_id: ovForm.supplier_id,
                  tds_section_id: ovForm.tds_section_id || undefined,
                  tds_mode: ovForm.tds_mode,
                  override_rate: ovForm.override_rate ? parseFloat(ovForm.override_rate) : undefined,
                  effective_from: ovForm.effective_from || undefined,
                  mode_reason: ovForm.mode_reason || undefined,
                })
                if (res.catchup) {
                  setCatchupAlert({ override_id: res.override_id, summary: res.catchup })
                }
                setOvForm({ supplier_id: '', tds_section_id: '', tds_mode: 'cumulative', override_rate: '', certificate_no: '', effective_from: '', valid_to: '', mode_reason: '' })
                fetchVendorOverrides().then(setOverrides)
              } catch { setOvErr('Save failed') }
            }}><Plus size={12} /> Add Override</button>
          </div>
          {ovErr && <div className="tds__err">{ovErr}</div>}

          {catchupAlert && (
            <div style={{ background: '#FAEEDA', border: '1px solid #EF9F27', borderRadius: 8, padding: '12px 16px', margin: '12px 0' }}>
              <div style={{ color: '#633806', fontWeight: 500, marginBottom: 4 }}>TDS liability on prior bills — action required</div>
              <div style={{ color: '#854F0B', fontSize: 13 }}>
                {catchupAlert.summary.affected_bill_count} bills totalling ₹{catchupAlert.summary.bills_total_amount.toLocaleString('en-IN')} were processed without TDS.
                Outstanding liability: ₹{catchupAlert.summary.catchup_pending.toLocaleString('en-IN')}.
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button style={{ background: '#E1F5EE', color: '#0F6E56', border: 'none', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontWeight: 500, fontSize: 13 }}
                  onClick={async () => { await submitCatchupDecision(catchupAlert.override_id, 'recover'); setCatchupAlert(null) }}>
                  Recover from next bill
                </button>
                <button style={{ background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13 }}
                  onClick={async () => { await submitCatchupDecision(catchupAlert.override_id, 'absorb'); setCatchupAlert(null) }}>
                  Absorb as company liability
                </button>
              </div>
            </div>
          )}

          {ovLoading ? <div className="tds__empty">Loading…</div> : overrides.length === 0
            ? <div className="tds__empty">No vendor overrides. Add one above.</div>
            : (
              <table className="tds__table">
                <thead><tr>
                  <th>Vendor</th><th>Section</th><th>Mode</th><th>Rate</th><th>Effective</th><th>Status</th><th></th>
                </tr></thead>
                <tbody>
                  {overrides.map(o => {
                    const mc = MODE_COLORS[o.tds_mode] || MODE_COLORS.cumulative
                    return (
                      <tr key={o.override_id} className="tds__row" style={!o.is_active ? { opacity: 0.5 } : undefined}>
                        <td style={{ fontWeight: 500 }}>{o.supplier_name || o.supplier_id.slice(0, 8)}</td>
                        <td>
                          {o.section_return_code && <span className="tds__code-badge">{o.section_return_code}</span>}
                          {o.old_section_label && <span className="tds__soft" style={{ marginLeft: 4 }}>{o.old_section_label}</span>}
                          {!o.section_return_code && <span className="tds__soft">—</span>}
                        </td>
                        <td>
                          <span style={{ background: mc.bg, color: mc.color, padding: '2px 8px', borderRadius: 4, fontSize: 12, fontWeight: 500 }}>
                            {MODE_LABELS[o.tds_mode] || o.tds_mode}
                          </span>
                        </td>
                        <td>{o.override_rate != null ? <strong>{o.override_rate}%</strong> : <span className="tds__soft">Default</span>}</td>
                        <td><span className="tds__soft">{o.effective_from || '—'}</span></td>
                        <td>
                          {o.is_active
                            ? <span style={{ color: '#059669', fontSize: 12 }}>Active</span>
                            : <span style={{ color: '#9ca3af', fontSize: 12 }}>Expired</span>}
                        </td>
                        <td className="tds__actions-cell">
                          <button className="tds__del-btn" onClick={async () => {
                            await deleteVendorOverride(o.override_id)
                            setOverrides(os => os.filter(x => x.override_id !== o.override_id))
                          }}><X size={11} /></button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
        </div>
      )}

      {/* ── FY State panel ── */}
      {tab === 'fy_state' && (
        <div className="tds__panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div className="tds__map-legend">Running FY state per vendor x section. Updated on every bill processed.</div>
            <button className="tds__save-btn" onClick={() => { setFyLoading(true); fetchFyState().then(d => setFyStates(Array.isArray(d) ? d : [])).catch(() => setFyStates([])).finally(() => setFyLoading(false)) }}>
              <RotateCcw size={12} /> Refresh
            </button>
          </div>
          {fyLoading ? <div className="tds__empty">Loading...</div> : fyStates.length === 0
            ? <div className="tds__empty">No FY state records yet. State is created when the first bill is processed for a vendor+section combination.</div>
            : (
              <table className="tds__table">
                <thead><tr>
                  <th>Vendor</th><th>Section</th><th>FY</th>
                  <th style={{ textAlign: 'right' }}>Cumulative Billed</th>
                  <th style={{ textAlign: 'right' }}>TDS Deducted</th>
                  <th>Threshold</th>
                  <th style={{ textAlign: 'right' }}>Catch-up Pending</th>
                  <th>Decision</th>
                </tr></thead>
                <tbody>
                  {fyStates.map(fs => (
                    <tr key={fs.state_id} className="tds__row">
                      <td style={{ fontWeight: 500 }}>{fs.supplier_name || fs.supplier_id.slice(0, 8)}</td>
                      <td>
                        {fs.return_code && <span className="tds__code-badge">{fs.return_code}</span>}
                        {fs.old_section_label && <span className="tds__soft" style={{ marginLeft: 4 }}>{fs.old_section_label}</span>}
                      </td>
                      <td><span className="tds__soft">{fs.fy}</span></td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {Number(fs.cumulative_billed).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {Number(fs.cumulative_tds_deducted).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
                      </td>
                      <td>
                        {fs.threshold_crossed
                          ? <span style={{ color: '#059669', fontSize: 12, fontWeight: 500 }}>Crossed</span>
                          : <span style={{ color: '#9ca3af', fontSize: 12 }}>Below</span>}
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {Number(fs.catchup_pending) > 0
                          ? <span style={{ color: '#b45309', fontWeight: 500 }}>
                              {Number(fs.catchup_pending).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
                            </span>
                          : <span className="tds__soft">-</span>}
                      </td>
                      <td>
                        {fs.catchup_decision === 'recover'
                          ? <span style={{ background: '#E1F5EE', color: '#0F6E56', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>Recover</span>
                          : fs.catchup_decision === 'absorb'
                          ? <span style={{ background: '#FEF3C7', color: '#92400E', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>Absorbed</span>
                          : fs.catchup_decision === 'pending'
                          ? <span style={{ background: '#FAEEDA', color: '#854F0B', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>Pending</span>
                          : fs.catchup_decision === 'abandoned'
                          ? <span style={{ background: '#F3F4F6', color: '#6B7280', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>Abandoned</span>
                          : <span className="tds__soft">-</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}

      {/* ── Preferences panel ── */}
      {tab === 'preferences' && (
        <div className="tds__panel tds__panel--prefs">
          <div className="tds__pref-grid">
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Deduct TDS On</div>
              <div className="tds__pref-ctrl">
                {(['BOOKING', 'PAYMENT', 'EARLIER'] as const).map(v => (
                  <label key={v} className="tds__radio-opt">
                    <input type="radio" name="deduct_on" checked={prefs.deduct_on === v} onChange={() => setPrefs(p => ({ ...p, deduct_on: v }))} />
                    {v === 'BOOKING' ? 'On Booking' : v === 'PAYMENT' ? 'On Payment' : 'Earlier of the two'}
                  </label>
                ))}
              </div>
            </div>
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Base Amount</div>
              <div className="tds__pref-ctrl">
                {(['GST_EXCLUSIVE', 'TOTAL_AMOUNT'] as const).map(v => (
                  <label key={v} className="tds__radio-opt">
                    <input type="radio" name="base_default" checked={prefs.base_default === v} onChange={() => setPrefs(p => ({ ...p, base_default: v }))} />
                    {v === 'GST_EXCLUSIVE' ? 'GST Exclusive (recommended)' : 'Total Amount (incl. GST)'}
                  </label>
                ))}
                <div className="tds__pref-hint">Per CBDT Circular 23/2017: when GST is shown separately, TDS is deducted on the pre-GST amount.</div>
              </div>
            </div>
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Auto-Apply TDS</div>
              <div className="tds__pref-ctrl">
                <label className="tds__toggle">
                  <input type="checkbox" checked={prefs.auto_apply} onChange={e => setPrefs(p => ({ ...p, auto_apply: e.target.checked }))} />
                  <span className="tds__toggle-track" />
                  <span className="tds__toggle-label">{prefs.auto_apply ? 'Enabled' : 'Disabled'}</span>
                </label>
              </div>
            </div>
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Default No-PAN Rate</div>
              <div className="tds__pref-ctrl">
                <input className="tds__inp tds__inp--rate" type="number" value={prefs.pan_missing_rate}
                  onChange={e => setPrefs(p => ({ ...p, pan_missing_rate: parseFloat(e.target.value) || 20 }))} />
                <span className="tds__pref-hint">Section 206AA penalty when PAN is not available.</span>
              </div>
            </div>
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Max Catch-up Per Bill</div>
              <div className="tds__pref-ctrl">
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input className="tds__inp tds__inp--rate" type="number" value={prefs.max_catchup_pct}
                    onChange={e => setPrefs(p => ({ ...p, max_catchup_pct: parseInt(e.target.value) || 50 }))} />
                  <span style={{ color: '#6b7280', fontSize: 13 }}>%</span>
                </div>
                <span className="tds__pref-hint">Maximum % of a bill's base amount recoverable as catch-up TDS in one bill.</span>
              </div>
            </div>
            <div className="tds__pref-row">
              <div className="tds__pref-lbl">Max Backdate Days</div>
              <div className="tds__pref-ctrl">
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input className="tds__inp tds__inp--rate" type="number" value={prefs.max_backdate_days}
                    onChange={e => setPrefs(p => ({ ...p, max_backdate_days: parseInt(e.target.value) || 90 }))} />
                  <span style={{ color: '#6b7280', fontSize: 13 }}>days</span>
                </div>
                <span className="tds__pref-hint">Bills older than this are flagged REVIEW_REQUIRED instead of auto-processing.</span>
              </div>
            </div>
          </div>
          <div style={{ marginTop: 20 }}>
            <button className="tds__save-btn" onClick={prefSave}>
              {prefSaved ? <><Check size={12} /> Saved!</> : <><Save size={12} /> Save Preferences</>}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Brands Manager ───────────────────────────────────────────────────────────
const BRAND_API = import.meta.env.VITE_API_URL ?? ''

/**
 * Brand logo thumbnail. Fetched with the auth header and shown as a blob URL —
 * see api/authedFiles.ts for why the token must not ride in the URL.
 * `cacheBust` changes after an upload to force a refetch.
 */
function BrandLogoThumb({ brandId, companyId, cacheBust }: {
  brandId: string; companyId?: string; cacheBust: number
}) {
  const logo = useAuthedObjectUrl(
    `${BRAND_API}/api/brands/${brandId}/logo?company_id=${companyId ?? ''}&t=${cacheBust}`
  )
  return (
    <div style={{
      width: 40, height: 40, borderRadius: 6, background: '#f3f4f6',
      border: '1px solid #e5e7eb', overflow: 'hidden', flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      {logo.url
        ? <img src={logo.url} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
        : <Briefcase size={18} color="#9ca3af" aria-hidden="true" />}
    </div>
  )
}

interface BrandEntry { id: string; name: string; has_logo: boolean; is_default: boolean; created_at: string | null }

function BrandsManager({ companyId }: { companyId?: string }) {
  const { brands: ctxBrands, setActiveBrand, activeBrand } = useAuth()
  const [brands, setBrands] = useState<BrandEntry[]>([])
  const [addName, setAddName] = useState('')
  const [adding, setAdding]   = useState(false)
  const [editId, setEditId]   = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [saving, setSaving]   = useState(false)
  const [err, setErr]         = useState('')
  const logoInputRef = useRef<HTMLInputElement>(null)
  const [uploadingId, setUploadingId] = useState<string | null>(null)
  const [logoTs, setLogoTs] = useState<Record<string, number>>({})

  const reload = async () => {
    const r = await fetch(`${BRAND_API}/api/brands?company_id=${companyId ?? ''}`)
    const d = await r.json()
    setBrands(d.brands ?? [])
  }

  useEffect(() => { reload() }, [companyId])
  // Keep in sync with context (e.g. after brand switch)
  useEffect(() => { setBrands(ctxBrands as BrandEntry[]) }, [ctxBrands])

  const handleAdd = async () => {
    if (!addName.trim()) return
    setSaving(true); setErr('')
    try {
      await fetch(`${BRAND_API}/api/brands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: addName.trim(), company_id: companyId ?? '' }),
      })
      setAddName(''); setAdding(false)
      await reload()
    } catch { setErr('Failed to add brand') } finally { setSaving(false) }
  }

  const handleRename = async (id: string) => {
    if (!editName.trim()) return
    setSaving(true); setErr('')
    try {
      await fetch(`${BRAND_API}/api/brands/${id}?company_id=${companyId ?? ''}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: editName.trim() }),
      })
      setEditId(null)
      await reload()
    } catch { setErr('Failed to rename') } finally { setSaving(false) }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this brand? Bills and vendors in this brand will remain in the database but won\'t be visible under any brand until reassigned.')) return
    try {
      const r = await fetch(`${BRAND_API}/api/brands/${id}?company_id=${companyId ?? ''}`, { method: 'DELETE' })
      if (!r.ok) { const d = await r.json(); setErr(d.detail ?? 'Delete failed'); return }
      await reload()
    } catch { setErr('Failed to delete') }
  }

  const handleLogoUpload = async (brandId: string, file: File) => {
    setUploadingId(brandId)
    const fd = new FormData()
    fd.append('file', file)
    try {
      await fetch(`${BRAND_API}/api/brands/${brandId}/logo?company_id=${companyId ?? ''}`, { method: 'POST', body: fd })
      const ts = Date.now()
      setLogoTs(prev => ({ ...prev, [brandId]: ts }))
      await reload()
      // Update context brand if this is the active one
      const updated = brands.find(b => b.id === brandId)
      if (updated && activeBrand?.id === brandId) setActiveBrand({ ...activeBrand, has_logo: true })
    } catch { setErr('Logo upload failed') } finally { setUploadingId(null) }
  }

  return (
    <div className="brands__wrap" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── AI Header Banner ── */}
      <div className="brands__ai-header">
        <div className="brands__ai-left">
          <div className="brands__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="brands__ai-title">Brand Workspace Engine</div>
            <div className="brands__ai-sub">Agents partition vendor bills, line items, and approval chains across registered company brands.</div>
          </div>
        </div>
        <div className="brands__ai-stats">
          <span className="brands__stat-badge">
            <span className="brands__stat-dot" />
            {brands.length} Active Brands
          </span>
        </div>
      </div>

      {err && <div style={{ color: '#dc2626', fontSize: 13, marginBottom: 4 }}>{err}</div>}

      <div className="brands__list">
        {brands.map(b => (
          <div key={b.id} className="brands__row">
            {/* Logo thumbnail */}
            <BrandLogoThumb
              brandId={b.id}
              companyId={companyId}
              cacheBust={logoTs[b.id] ?? 0}
            />

            {/* Name / edit inline */}
            <div className="brands__name-area">
              {editId === b.id ? (
                <div className="brands__inline-edit">
                  <input
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleRename(b.id); if (e.key === 'Escape') setEditId(null) }}
                    autoFocus
                    className="brands__field"
                  />
                  <button onClick={() => handleRename(b.id)} disabled={saving}
                    className="brands__save-btn">
                    Save
                  </button>
                  <button onClick={() => setEditId(null)}
                    className="brands__cancel-btn">
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="brands__name-line">
                  <span className="brands__name">{b.name}</span>
                  {b.is_default && (
                    <span className="brands__default-badge">Default</span>
                  )}
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="brands__actions">
              {/* Upload logo */}
              <button
                title="Upload logo"
                onClick={() => { setUploadingId(b.id); logoInputRef.current?.click() }}
                className="brands__icon-btn"
              >
                {uploadingId === b.id ? '…' : <ImagePlus size={15} />}
              </button>
              <input
                ref={logoInputRef}
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f && uploadingId) handleLogoUpload(uploadingId, f)
                  e.target.value = ''
                }}
              />

              {editId !== b.id && (
                <button
                  title="Rename"
                  onClick={() => { setEditId(b.id); setEditName(b.name) }}
                  className="brands__icon-btn"
                >
                  <Pencil size={14} />
                </button>
              )}

              {!b.is_default && (
                <button
                  title="Delete"
                  onClick={() => handleDelete(b.id)}
                  className="brands__icon-btn brands__icon-btn--danger"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {adding ? (
        <div className="brands__add-form">
          <input
            value={addName}
            onChange={e => setAddName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleAdd(); if (e.key === 'Escape') { setAdding(false); setAddName('') } }}
            placeholder="Brand name…"
            autoFocus
            className="brands__field"
          />
          <button onClick={handleAdd} disabled={saving || !addName.trim()}
            className="brands__save-btn"
          >
            Add
          </button>
          <button onClick={() => { setAdding(false); setAddName('') }}
            className="brands__cancel-btn"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="brands__add-trigger"
        >
          <Plus size={14} /> Add Brand
        </button>
      )}

      <p className="brands__hint">
        Bills, vendors, and dashboard are filtered per brand. Approval chains are shared across all brands.
      </p>
    </div>
  )
}

function CostCentersManager({ companyId }: { companyId?: string }) {
  const { brands, activeBrand } = useAuth()
  const [brandId, setBrandId] = useState<string>(activeBrand?.id ?? '')
  const [items, setItems] = useState<CostCenter[]>([])
  const [loading, setLoading] = useState(false)
  const [addName, setAddName] = useState('')
  const [adding, setAdding] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { if (!brandId && activeBrand?.id) setBrandId(activeBrand.id) }, [activeBrand?.id])

  const reload = async () => {
    if (!brandId) { setItems([]); return }
    setLoading(true); setErr('')
    try {
      setItems(await fetchCostCenters(companyId, brandId, true))
    } catch { setErr('Failed to load cost centers') } finally { setLoading(false) }
  }
  useEffect(() => { reload() }, [brandId, companyId])

  const handleAdd = async () => {
    if (!addName.trim() || !brandId) return
    setSaving(true); setErr('')
    try {
      await createCostCenter(addName.trim(), brandId, companyId)
      setAddName(''); setAdding(false)
      await reload()
    } catch (e) { setErr(errMsg(e, 'Failed to add cost center')) } finally { setSaving(false) }
  }

  const handleRename = async (id: string) => {
    if (!editName.trim()) return
    setSaving(true); setErr('')
    try {
      await updateCostCenter(id, { name: editName.trim(), company_id: companyId })
      setEditId(null); await reload()
    } catch (e) { setErr(errMsg(e, 'Failed to rename')) } finally { setSaving(false) }
  }

  const handleToggle = async (cc: CostCenter) => {
    setErr('')
    try {
      await updateCostCenter(cc.id, { is_active: !cc.is_active, company_id: companyId })
      await reload()
    } catch (e) { setErr(errMsg(e, 'Failed to update')) }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this cost center? If any bills use it, disable it instead.')) return
    setErr('')
    try {
      await deleteCostCenter(id, companyId)
      await reload()
    } catch (e) { setErr(errMsg(e, 'Delete failed')) }
  }

  const hasBrands = brands.length > 0

  return (
    <div className="cc__wrap">
      {/* ── AI Header Banner ── */}
      <div className="cc__ai-header">
        <div className="cc__ai-left">
          <div className="cc__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="cc__ai-title">Cost Center Segmenter</div>
            <div className="cc__ai-sub">Agents assign line items and budget allocations to specific department or project cost centers.</div>
          </div>
        </div>
        <div className="cc__ai-stats">
          <span className="cc__stat-badge">
            <span className="cc__stat-dot" />
            {items.length} Cost Centers
          </span>
        </div>
      </div>

      {err && <div style={{ color: '#dc2626', fontSize: 13, marginBottom: 4 }}>{err}</div>}

      {!hasBrands ? (
        <div style={{ fontSize: 13, color: '#9ca3af' }}>Create a brand first — cost centers are scoped to a brand.</div>
      ) : (
        <>
          {/* Brand selector */}
          <div className="cc__brand-select">
            <label>Brand</label>
            <select
              value={brandId}
              onChange={e => setBrandId(e.target.value)}
              className="cc__select"
            >
              {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>

          <div className="cc__list">
            {loading && <div style={{ fontSize: 13, color: '#9ca3af' }}>Loading…</div>}
            {!loading && items.length === 0 && (
              <div style={{ fontSize: 13, color: '#9ca3af' }}>No cost centers yet for this brand.</div>
            )}
            {items.map(cc => (
              <div key={cc.id} className={`cc__row ${cc.is_active ? '' : 'cc__row--disabled'}`}>
                <div className="cc__name-area">
                  {editId === cc.id ? (
                    <div className="cc__inline-edit">
                      <input
                        value={editName}
                        onChange={e => setEditName(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleRename(cc.id); if (e.key === 'Escape') setEditId(null) }}
                        autoFocus
                        className="cc__field"
                      />
                      <button onClick={() => handleRename(cc.id)} disabled={saving}
                        className="cc__save-btn">Save</button>
                      <button onClick={() => setEditId(null)}
                        className="cc__cancel-btn">Cancel</button>
                    </div>
                  ) : (
                    <div className="cc__name-line">
                      <span className="cc__name">{cc.name}</span>
                      {!cc.is_active && (
                        <span className="cc__disabled-badge">Disabled</span>
                      )}
                    </div>
                  )}
                </div>
                <div className="cc__actions">
                  <button title={cc.is_active ? 'Disable' : 'Enable'} onClick={() => handleToggle(cc)}
                    className="cc__toggle-btn">
                    {cc.is_active ? 'Disable' : 'Enable'}
                  </button>
                  {editId !== cc.id && (
                    <button title="Rename" onClick={() => { setEditId(cc.id); setEditName(cc.name) }}
                      className="cc__icon-btn">
                      <Pencil size={14} />
                    </button>
                  )}
                  <button title="Delete" onClick={() => handleDelete(cc.id)}
                    className="cc__icon-btn cc__icon-btn--danger">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>

          {adding ? (
            <div className="cc__add-form">
              <input
                value={addName}
                onChange={e => setAddName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleAdd(); if (e.key === 'Escape') { setAdding(false); setAddName('') } }}
                placeholder="Cost center name…"
                autoFocus
                className="cc__field"
              />
              <button onClick={handleAdd} disabled={saving || !addName.trim()}
                className="cc__save-btn">Add</button>
              <button onClick={() => { setAdding(false); setAddName('') }}
                className="cc__cancel-btn">Cancel</button>
            </div>
          ) : (
            <button onClick={() => setAdding(true)}
              className="cc__add-trigger">
              <Plus size={14} /> Add Cost Center
            </button>
          )}
        </>
      )}
    </div>
  )
}

function errMsg(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

// ── FX & Currency Settings ──────────────────────────────────────────────────
const FX_CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY'] as const
const FX_SYMBOLS: Record<string, string> = { USD: '$', EUR: '€', GBP: '£', JPY: '¥' }

function FxSettingsManager({ companyId }: { companyId?: string }) {
  const [settings, setSettings] = useState<FxSettings | null>(null)
  const [loading, setLoading]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [saved, setSaved]       = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    fetchFxSettings(companyId).then(setSettings).catch(() => {}).finally(() => setLoading(false))
  }, [companyId])

  const update = (patch: Partial<FxSettings>) => {
    setSettings(s => s ? { ...s, ...patch } : s)
    setSaved(false)
  }

  const updateThreshold = (ccy: string, key: string, value: string) => {
    setSettings(s => {
      if (!s) return s
      const t = { ...s.thresholds }
      t[ccy] = { ...t[ccy], [key]: value === '' ? undefined : parseFloat(value) }
      return { ...s, thresholds: t }
    })
    setSaved(false)
  }

  const handleSave = async () => {
    if (!settings) return
    setSaving(true); setError('')
    try {
      const result = await updateFxSettings({
        company_id: companyId,
        alerts_enabled: settings.alerts_enabled,
        reconciliation_tolerance_pct: settings.reconciliation_tolerance_pct,
        stale_alert_days: settings.stale_alert_days,
        thresholds: settings.thresholds as Record<string, Record<string, number>>,
      })
      setSettings(result)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 24, color: '#9ca3af', fontSize: 13 }}>Loading FX settings...</div>
  if (!settings) return <div style={{ padding: 24, color: '#9ca3af', fontSize: 13 }}>Failed to load FX settings.</div>

  const inputStyle: React.CSSProperties = {
    padding: '7px 10px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 13,
    fontFamily: 'inherit', outline: 'none', width: '100%', boxSizing: 'border-box',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase',
    letterSpacing: '0.05em', marginBottom: 4,
  }

  return (
    <div className="fx__wrap" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* ── AI Header Banner ── */}
      <div className="fx__ai-header">
        <div className="fx__ai-left">
          <div className="fx__ai-spark-icon">
            <Sparkles size={16} />
          </div>
          <div>
            <div className="fx__ai-title">Multi-Currency & FX Intelligence Engine</div>
            <div className="fx__ai-sub">Agents monitor real-time foreign exchange volatility, rate staleness, and optimize cross-border payment timing.</div>
          </div>
        </div>
        <div className="fx__ai-stats">
          <span className="fx__stat-badge">
            <span className="fx__stat-dot" />
            {FX_CURRENCIES.length} Foreign Currencies Tracked
          </span>
        </div>
      </div>

      {/* Global toggles */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '16px' }}>
          <div style={labelStyle}>Enable FX Alerts</div>
          <button
            onClick={() => update({ alerts_enabled: !settings.alerts_enabled })}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '8px 16px', borderRadius: 8,
              border: `1.5px solid ${settings.alerts_enabled ? '#10b981' : '#d1d5db'}`,
              background: settings.alerts_enabled ? '#f0fdf4' : '#f9fafb',
              color: settings.alerts_enabled ? '#059669' : '#6b7280',
              fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', width: '100%',
            }}
          >
            {settings.alerts_enabled ? <><CheckCircle2 size={14} /> ON</> : <><XCircle size={14} /> OFF</>}
          </button>
        </div>

        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '16px' }}>
          <div style={labelStyle}>Reconciliation Tolerance</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input
              type="number" min="0" max="10" step="0.1"
              value={settings.reconciliation_tolerance_pct}
              onChange={e => update({ reconciliation_tolerance_pct: parseFloat(e.target.value) || 0 })}
              style={{ ...inputStyle, width: 80, textAlign: 'right' }}
            />
            <span style={{ fontSize: 13, color: '#6b7280', fontWeight: 600 }}>%</span>
          </div>
        </div>

        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '16px' }}>
          <div style={labelStyle}>Stale Rate Alert (days)</div>
          <input
            type="number" min="1" max="30" step="1"
            value={settings.stale_alert_days}
            onChange={e => update({ stale_alert_days: parseInt(e.target.value) || 3 })}
            style={{ ...inputStyle, width: 80 }}
          />
        </div>
      </div>

      {/* Per-currency thresholds */}
      <div style={{ fontSize: 14, fontWeight: 700, color: '#111827' }}>Currency Thresholds</div>
      <div style={{ fontSize: 12, color: '#9ca3af', marginTop: -14 }}>
        Alert thresholds for each currency. Rates are INR per 1 unit of foreign currency.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14 }}>
        {FX_CURRENCIES.map(ccy => {
          const t = settings.thresholds[ccy] ?? {}
          return (
            <div key={ccy} style={{
              border: '1px solid #e5e7eb', borderRadius: 10, padding: '16px', background: '#fff',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                <span style={{
                  fontWeight: 700, color: '#4338ca', padding: '3px 10px', borderRadius: 6,
                  background: '#eef2ff', fontSize: 13,
                }}>
                  {FX_SYMBOLS[ccy]} {ccy}
                </span>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {([
                  { key: 'pay_now_below',           label: 'Pay now below' },
                  { key: 'delay_pay_above',         label: 'Delay payment above' },
                  { key: 'collect_now_above',       label: 'Collect now above' },
                  { key: 'volatility_threshold_pct', label: 'Volatility alert %' },
                ] as const).map(f => (
                  <div key={f.key}>
                    <div style={{ fontSize: 10, fontWeight: 600, color: '#9ca3af', marginBottom: 3 }}>
                      {f.label}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {f.key !== 'volatility_threshold_pct' && (
                        <span style={{ fontSize: 12, color: '#9ca3af' }}>INR</span>
                      )}
                      <input
                        type="number"
                        step={f.key === 'volatility_threshold_pct' ? '0.1' : '0.01'}
                        value={(t as Record<string, number | undefined>)[f.key] ?? ''}
                        onChange={e => updateThreshold(ccy, f.key, e.target.value)}
                        style={{ ...inputStyle, textAlign: 'right' }}
                        placeholder={f.key === 'volatility_threshold_pct' ? '1.5' : '0.00'}
                      />
                      {f.key === 'volatility_threshold_pct' && (
                        <span style={{ fontSize: 12, color: '#9ca3af' }}>%</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>

      {/* Save */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '9px 20px',
            background: saved ? '#059669' : '#4f46e5', color: '#fff', border: 'none',
            borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: saving ? 'default' : 'pointer',
            fontFamily: 'inherit', opacity: saving ? 0.6 : 1, transition: 'background 0.2s',
          }}
        >
          {saving ? 'Saving...' : saved ? <><Check size={14} /> Saved!</> : <><Save size={14} /> Save Settings</>}
        </button>
        {error && <span style={{ fontSize: 12, color: '#dc2626' }}>{error}</span>}
      </div>
    </div>
  )
}

// ── Demo payments ────────────────────────────────────────────────────────────
/** Where a demo payment's receipt goes. Shown only with the Demo Payments licence. */
function DemoPaymentsSettings({ companyId }: { companyId?: string }) {
  const [email, setEmail]   = useState('')
  const [people, setPeople] = useState<CompanyUser[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState(false)
  const [error, setError]   = useState('')

  useEffect(() => {
    fetchDemoPaymentSettings().then(s => setEmail(s.poc_email ?? '')).catch(() => setError("Couldn't load the demo settings.")).finally(() => setLoading(false))
    fetchUsers(companyId).then(us => setPeople(us.filter(u => u.email))).catch(() => {})
  }, [companyId])

  const handleSave = async () => {
    setSaving(true); setError('')
    try {
      const r = await saveDemoPaymentSettings(email.trim() || null)
      setEmail(r.poc_email ?? '')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setError(typeof d === 'string' ? d : "Couldn't save. Try again.")
    } finally { setSaving(false) }
  }

  if (loading) return <div style={{ fontSize: 13, color: '#9ca3af' }}>Loading…</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 520 }}>
      <div style={{ padding: '10px 14px', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontSize: 13, color: '#92400e' }}>
        Demo mode is on. Pay skips the bank, the bill is marked paid, and no money moves.
      </div>
      <div>
        <label htmlFor="demo-poc" style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 6 }}>
          Send payment receipts to
        </label>
        <input id="demo-poc" type="email" list="demo-poc-people" value={email} placeholder="name@company.com"
          onChange={e => { setEmail(e.target.value); setSaved(false) }}
          onKeyDown={e => { if (e.key === 'Enter') handleSave() }}
          style={{ padding: '8px 10px', border: '1px solid #d1d5db', borderRadius: 6, fontSize: 13, fontFamily: 'inherit', width: '100%', boxSizing: 'border-box' }} />
        <datalist id="demo-poc-people">
          {people.map(p => <option key={p.username} value={p.email}>{p.name}</option>)}
        </datalist>
        <div style={{ fontSize: 12, color: '#6b7280', marginTop: 6 }}>
          Every vendor's receipt goes to this one address instead of the vendor. Leave it empty to send none.
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button onClick={handleSave} disabled={saving}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '9px 20px',
            background: saved ? '#059669' : '#4f46e5', color: '#fff', border: 'none',
            borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: saving ? 'default' : 'pointer',
            fontFamily: 'inherit', opacity: saving ? 0.6 : 1, transition: 'background 0.2s',
          }}>
          {saving ? 'Saving...' : saved ? <><Check size={14} /> Saved!</> : <><Save size={14} /> Save Settings</>}
        </button>
        {error && <span style={{ fontSize: 12, color: '#dc2626' }}>{error}</span>}
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────
// The section list lives in routes/paths.ts because it is also the URL contract.
// 'country' (the tenant's jurisdiction rules) is served at /settings/country by
// the same :section route and handled here until it joins that list.
type SettingsSection = RouteSettingsSection | 'country'

const SETTINGS_NAV: { id: SettingsSection; label: string; sub: string; icon: React.ReactNode; color: string }[] = [
  { id: 'approval-chain', label: 'Approval Chain',     sub: 'Customise bill approval workflow',             icon: <GitBranch size={16} />,  color: '#7c3aed' },
  { id: 'country',        label: 'Country Rules',       sub: 'Tax checks, currency and payment rails for your country', icon: <Globe size={16} />, color: '#0f766e' },
  { id: 'roles',          label: 'Roles & Permissions', sub: 'View what each role can access',               icon: <Shield size={16} />,     color: '#059669' },
  { id: 'team',           label: 'Team Members',        sub: 'Manage users in your company',                 icon: <Users size={16} />,      color: '#2563eb' },
  { id: 'brands',         label: 'Brands',              sub: 'Manage brands under this account',             icon: <Building2 size={16} />,  color: '#db2777' },
  { id: 'cost-centers',   label: 'Cost Centers',        sub: 'Optional expense segmentation per brand',      icon: <Layers size={16} />,     color: '#0284c7' },
  { id: 'charges',        label: 'Charges Master',      sub: 'Reusable charge types with HSN/SAC codes',     icon: <Tag size={16} />,        color: '#ea580c' },
  { id: 'categories',     label: 'Expense Categories',  sub: 'Category list for bills and COA rules',        icon: <Tag size={16} />,        color: '#16a34a' },
  { id: 'coa',            label: 'COA Rules',           sub: 'Map vendors and categories to expense heads',  icon: <BookOpen size={16} />,   color: '#0d9488' },
  { id: 'tds',            label: 'TDS Configuration',   sub: 'TDS sections, mappings and preferences',       icon: <Percent size={16} />,    color: '#4f46e5' },
  { id: 'fx',             label: 'FX & Currency',       sub: 'FX alert thresholds and multi-currency config', icon: <TrendingUp size={16} />, color: '#0891b2' },
  { id: 'demo-payments',  label: 'Demo Payments',       sub: 'Simulated payments and where receipts go',      icon: <Wallet size={16} />,     color: '#d97706' },
]

export default function SettingsPage() {
  const { user } = useAuth()
  // The open section is the address: /settings/tds is linkable, survives a
  // refresh, and Back steps between sections.
  const navigate = useNavigate()
  const { section } = useParams<{ section?: string }>()
  const activeSection: SettingsSection = section === 'country'
    ? 'country'
    : SETTINGS_SECTIONS.includes(section as RouteSettingsSection)
      ? (section as RouteSettingsSection)
      : 'approval-chain'
  const setActiveSection = (next: SettingsSection) =>
    navigate(next === 'country' ? `${settingsPath()}/country` : settingsPath(next))
  const [selected, setSelected] = useState<Role>('admin')
  const [open, setOpen]         = useState(false)
  const [sharedCats, setSharedCats] = useState<string[]>(getAllCategories())

  const isAdmin = user?.role === 'admin'
  const demoPayments = useService('demo_payments')
  // India-only sections follow the tenant's rule packs (auth/jurisdiction.ts).
  const jur = useJurisdiction()

  if (!isAdmin) {
    return (
      <div className="sp sp--locked">
        <div className="sp__lock-icon"><Lock size={36} /></div>
        <h2 className="sp__lock-title">Admin Access Only</h2>
        <p className="sp__lock-sub">Settings can only be viewed and modified by administrators.<br />Contact your admin to make configuration changes.</p>
      </div>
    )
  }

  // Demo Payments only exists for a tenant with that licence.
  // TDS Configuration is Indian TDS, so it exists only where that pack applies.
  const nav = SETTINGS_NAV
    .filter(n => n.id !== 'demo-payments' || demoPayments)
    .filter(n => n.id !== 'tds' || jur.packs.tds_india)
    .map(n => n.id === 'fx' && jur.base_currency !== 'INR'
      ? { ...n, sub: `FX alerts and conversion into ${jur.base_currency}, your books currency` }
      : n)
  const active = nav.find(n => n.id === activeSection) ?? nav[0]

  return (
    <>
      <div className="sp">
        {/* ── Sidebar ── */}
        <div className="sp__sidebar">
          <div className="sp__sidebar-title">Settings</div>
          <nav className="sp__nav">
            {nav.map(item => (
              <button
                key={item.id}
                className={`sp__nav-item ${activeSection === item.id ? 'sp__nav-item--active' : ''}`}
                onClick={() => setActiveSection(item.id)}
                style={activeSection === item.id ? { '--nav-color': item.color } as React.CSSProperties : {}}
              >
                <span className="sp__nav-icon" style={{ color: activeSection === item.id ? item.color : '#9ca3af' }}>
                  {item.icon}
                </span>
                <span className="sp__nav-label">{item.label}</span>
                {activeSection === item.id && <ChevronRight size={14} className="sp__nav-chevron" style={{ color: item.color }} />}
              </button>
            ))}
          </nav>
        </div>

        {/* ── Main content ── */}
        <div className="sp__main">
          <div className="sp__page-hdr">
            <span className="sp__page-icon" style={{ background: active.color + '18', color: active.color }}>
              {active.icon}
            </span>
            <div>
              <div className="sp__page-title">{active.label}</div>
              <div className="sp__page-sub">{active.sub}</div>
            </div>
          </div>

          <div className="sp__page-body">

            {activeSection === 'approval-chain' && (
              <DeptChainEditor companyId={user?.company_id} />
            )}

            {activeSection === 'roles' && (
              <div>
                <div className="sp__role-row">
                  <span className="sp__role-row-label">Viewing permissions for</span>
                  <div className="sp__select-wrap">
                    <button className="sp__select" onClick={() => setOpen(o => !o)}>
                      <span className="sp__select-dot" style={{ background: ROLE_COLORS[selected]?.dot }} />
                      <span className="sp__select-val">{ROLE_LABELS[selected]}</span>
                      <ChevronDown size={14} style={{ marginLeft: 'auto', color: '#9ca3af' }} />
                    </button>
                    {open && (
                      <div className="sp__dropdown">
                        {ROLES.map(r => (
                          <button
                            key={r}
                            className={`sp__opt ${r === selected ? 'sp__opt--active' : ''}`}
                            onClick={() => { setSelected(r); setOpen(false) }}
                          >
                            <span className="sp__opt-dot" style={{ background: ROLE_COLORS[r]?.dot }} />
                            {ROLE_LABELS[r]}
                            {r === selected && <Check size={13} style={{ marginLeft: 'auto', color: '#059669' }} />}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="sp__matrix">
                  {PERMISSION_CATEGORIES.map(cat => (
                    <div key={cat.label} className="sp__cat">
                      <div className="sp__cat-label">{cat.label}</div>
                      <div className="sp__cat-items">
                        {cat.items.map(item => {
                          const granted = can(selected, item.permission)
                          return (
                            <div key={item.permission} className="sp__perm-row">
                              <span className="sp__perm-name">{item.label}</span>
                              {granted
                                ? <span className="sp__perm-yes"><CheckCircle2 size={16} /> Allowed</span>
                                : <span className="sp__perm-no"><XCircle size={16} /> Not allowed</span>}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeSection === 'team' && (
              <UserManager companyId={user?.company_id} currentUser={user?.username} />
            )}

            {activeSection === 'charges' && (
              <ChargesManager companyId={user?.company_id} />
            )}

            {activeSection === 'categories' && (
              <CategoriesManager companyId={user?.company_id} onChanged={cats => setSharedCats(['', ...cats])} />
            )}

            {activeSection === 'coa' && (
              <CoaRulesManager companyId={user?.company_id} externalCategories={sharedCats} />
            )}

            {activeSection === 'brands' && (
              <BrandsManager companyId={user?.company_id} />
            )}

            {activeSection === 'cost-centers' && (
              <CostCentersManager companyId={user?.company_id} />
            )}

            {activeSection === 'country' && <JurisdictionSettings />}

            {activeSection === 'tds' && jur.packs.tds_india && (
              <TdsConfigManager companyId={user?.company_id} />
            )}

            {activeSection === 'fx' && (
              <FxSettingsManager companyId={user?.company_id} />
            )}

            {activeSection === 'demo-payments' && demoPayments && (
              <DemoPaymentsSettings companyId={user?.company_id} />
            )}

          </div>
        </div>
      </div>

      <style>{`
        /* ── Shell ── */
        .sp {
          height: 100%; display: flex; flex-direction: row;
          overflow: hidden; background: var(--canvas);
        }

        /* ── Sidebar ── */
        .sp__sidebar {
          width: 220px; flex-shrink: 0;
          background: var(--surface-0); border-right: 1px solid var(--line-200);
          display: flex; flex-direction: column;
          padding: 24px 0 16px;
          overflow-y: auto;
        }
        .sp__sidebar-title {
          font-size: var(--fs-lg); font-weight: var(--fw-bold); color: var(--ink-900);
          padding: 0 20px 16px; border-bottom: 1px solid var(--line-100); margin-bottom: 8px;
        }
        .sp__nav { display: flex; flex-direction: column; gap: 2px; padding: 0 10px; }
        .sp__nav-item {
          display: flex; align-items: center; gap: 10px;
          padding: 9px 10px; border-radius: 8px; border: none;
          background: none; cursor: pointer; font-family: inherit;
          font-size: 13px; font-weight: 500; color: #6b7280;
          text-align: left; transition: background 0.12s, color 0.12s;
          width: 100%;
        }
        .sp__nav-item:hover { background: #f3f4f6; color: #111827; }
        .sp__nav-item--active { background: #f5f3ff; color: #7c3aed; font-weight: 600; }
        .sp__nav-icon { display: flex; align-items: center; flex-shrink: 0; }
        .sp__nav-label { flex: 1; }
        .sp__nav-chevron { flex-shrink: 0; }

        /* ── Main content area ── */
        .sp__main {
          flex: 1; min-width: 0; display: flex; flex-direction: column;
          overflow: hidden;
        }
        .sp__page-hdr {
          display: flex; align-items: center; gap: 14px;
          padding: 24px 32px 20px; border-bottom: 1px solid var(--line-100);
          background: var(--surface-0); flex-shrink: 0;
        }
        .sp__page-icon {
          width: 38px; height: 38px; border-radius: 10px;
          display: flex; align-items: center; justify-content: center; flex-shrink: 0;
        }
        .sp__page-title { font-size: var(--fs-xl); font-weight: var(--fw-bold); color: var(--ink-900); letter-spacing: -0.02em; }
        .sp__page-sub   { font-size: var(--fs-xs); color: var(--ink-500); margin-top: 2px; }
        .sp__page-body  { flex: 1; overflow-y: auto; padding: 28px 32px; }

        /* legacy section styles kept for inner managers that still use them */
        .sp__section-body  { padding: 16px 20px; }
        .sp__section-body--pad0 { padding: 0; overflow: hidden; border-radius: 0 0 12px 12px; }

        /* Role selector */
        .sp__role-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
        .sp__role-row-label { font-size: 13px; color: #6b7280; white-space: nowrap; }
        .sp__select-wrap { position: relative; }
        .sp__select {
          display: flex; align-items: center; gap: 8px; padding: 8px 12px; min-width: 200px;
          border: 1.5px solid #e5e7eb; border-radius: 8px; background: white; cursor: pointer;
          font-family: inherit; font-size: 13px; font-weight: 600; color: #111827;
          transition: border-color 0.12s;
        }
        .sp__select:hover { border-color: #2563eb; }
        .sp__select-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
        .sp__select-val { flex: 1; text-align: left; }
        .sp__dropdown {
          position: absolute; top: calc(100% + 6px); left: 0; z-index: 50;
          background: white; border: 1px solid #e5e7eb; border-radius: 10px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.1); min-width: 200px; overflow: hidden;
        }
        .sp__opt {
          display: flex; align-items: center; gap: 8px; padding: 10px 14px;
          width: 100%; background: none; border: none; font-size: 13px; font-weight: 500;
          color: #374151; cursor: pointer; font-family: inherit; text-align: left; transition: background 0.1s;
        }
        .sp__opt:hover { background: #f9fafb; }
        .sp__opt--active { background: #f0fdf4; color: #059669; font-weight: 600; }
        .sp__opt-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

        /* Permission matrix */
        .sp__matrix { border: 1px solid #f3f4f6; border-radius: 8px; overflow: hidden; margin-top: 0; }
        .sp__cat { border-bottom: 1px solid #f3f4f6; }
        .sp__cat:last-child { border-bottom: none; }
        .sp__cat-label {
          font-size: 11px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.05em; color: #9ca3af; padding: 10px 16px 6px; background: #fafafa;
        }
        .sp__cat-items { padding: 4px 0 8px; }
        .sp__perm-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; }
        .sp__perm-name { font-size: 13px; color: #374151; }
        .sp__perm-yes { display: flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; color: #059669; }
        .sp__perm-no  { display: flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; color: #d1d5db; }

        /* Team table */
        .sp__team-wrap { display: flex; flex-direction: column; gap: 12px; }
        .sp__team-card {
          background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04); overflow: hidden;
        }
        .sp__team-table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
        .sp__col-member { width: 23%; }
        .sp__col-email { width: 19%; }
        .sp__col-role { width: 16%; }
        .sp__col-dept { width: 14%; }
        .sp__col-username { width: 14%; }
        .sp__col-action { width: 14%; }
        .sp__team-table th {
          padding: 12px 18px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.08em; color: #64748b; background: #f8fafc;
          border-bottom: 1px solid #e2e8f0; text-align: left;
          white-space: nowrap;
        }
        .sp__team-table tbody tr { height: 72px; }
        .sp__team-table td { padding: 14px 18px; border-bottom: 1px solid #eef2f7; vertical-align: middle; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; }
        .sp__team-table td:last-child { text-align: right; }
        .sp__tr:last-child td { border-bottom: none; }
        .sp__tr--me td { background: #f4fbff; }
        .sp__tr:hover:not(.sp__tr--me) td { background: #fafbff; }
        .sp__tr--editing td { background: #f8f9ff; }
        .sp__member-cell { display: flex; align-items: center; gap: 10px; min-width: 0; white-space: nowrap; line-height: 1; }
        .sp__avatar {
          width: 32px; height: 32px; border-radius: 50%;
          font-size: 12px; font-weight: 800;
          display: flex; align-items: center; justify-content: center; flex-shrink: 0;
          letter-spacing: -0.03em;
        }
        .sp__member-name { font-size: 13px; font-weight: 700; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; }
        .sp__me-badge {
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 9px; font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase;
          background: #dbeafe; color: #1d4ed8; padding: 2px 6px; border-radius: 999px; margin-top: 3px;
        }
        .sp__td-mono { font-family: monospace; font-size: 12px; color: #475467; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; }
        .sp__role-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 4px 9px; border-radius: 999px; font-size: 11px; font-weight: 700;
          letter-spacing: 0.02em; border: 1px solid rgba(15, 23, 42, 0.04);
          white-space: nowrap; line-height: 1;
        }
        .sp__badge-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
        .sp__action-cell { text-align: right; vertical-align: middle; white-space: nowrap; overflow: visible; text-overflow: clip; padding: 14px 16px 14px 8px; }
        .sp__action-wrap { display: inline-flex; align-items: center; justify-content: flex-end; gap: 10px; }
        .sp__action-btn {
          border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 700; font-family: inherit;
          padding: 6px 10px; transition: filter 0.12s ease; white-space: nowrap; flex-shrink: 0;
          min-width: 58px;
        }
        .sp__action-btn:hover { filter: brightness(0.98); }
        .sp__action-btn--primary { background: #eaf1ff; color: #2563eb; }
        .sp__action-btn--secondary { background: #f3f4f6; color: #475467; }
        .sp__delete-btn {
          display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
          border: 1px solid #fee2e2; border-radius: 8px; background: #fff; color: #ef4444; cursor: pointer;
          flex-shrink: 0; min-width: 30px; transition: all 0.15s ease;
        }
        .sp__delete-btn:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }
        .sp__team-add-card {
          background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04);
        }
        .sp__team-add-title {
          font-size: 13px; font-weight: 800; color: #111827; margin-bottom: 12px;
        }
        .sp__team-alert {
          font-size: 12px; color: #b42318; background: #fef3f2; border: 1px solid #f5c0b7; padding: 8px 10px; border-radius: 8px; margin-bottom: 10px;
        }
        .sp__add-user-btn {
          display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 8px; background: #f5f3ff; border: 1px dashed #c7d2fe; color: #4f46e5; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit;
        }
        .sp__pass        { font-family: monospace; font-size: 12px; color: #374151; }
        .sp__pass-hidden { font-size: 12px; color: #d1d5db; letter-spacing: 2px; }
        .sp__note { font-size: 11px; color: #9ca3af; padding: 10px 20px 12px; border-top: 1px solid #f3f4f6; }

        /* ── Chain Editor ── */
        .ce { display: flex; flex-direction: column; gap: 14px; }

        .ce__input-row {
          display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap;
        }
        .ce__nl-wrap { flex: 1; min-width: 260px; display: flex; gap: 8px; align-items: flex-start; }
        .ce__nl {
          flex: 1; padding: 9px 12px; border: 1.5px solid #e5e7eb; border-radius: 8px;
          font-family: inherit; font-size: 13px; color: #374151; resize: none; line-height: 1.5;
          transition: border-color 0.12s;
        }
        .ce__nl:focus { outline: none; border-color: #7c3aed; }
        .ce__parse-btn {
          display: flex; align-items: center; gap: 6px; padding: 9px 14px;
          background: #7c3aed; color: white; border: none; border-radius: 8px;
          font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap;
          font-family: inherit; transition: background 0.12s;
        }
        .ce__parse-btn:hover:not(:disabled) { background: #6d28d9; }
        .ce__parse-btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .ce__or { font-size: 12px; color: #9ca3af; padding-top: 10px; }

        .ce__upload-btn {
          display: flex; align-items: center; gap: 7px; padding: 9px 14px;
          border: 1.5px dashed #d1d5db; border-radius: 8px; background: #fafafa;
          font-size: 12px; font-weight: 600; color: #6b7280; cursor: pointer;
          font-family: inherit; white-space: nowrap; transition: border-color 0.12s, color 0.12s;
        }
        .ce__upload-btn:hover:not(:disabled) { border-color: #7c3aed; color: #7c3aed; }
        .ce__upload-btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .ce__err { font-size: 12px; color: #ef4444; }

        .ce__flow-label {
          display: flex; align-items: baseline; gap: 10px;
          font-size: 11px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.05em; color: #9ca3af;
        }
        .ce__flow-hint { font-size: 11px; font-weight: 400; text-transform: none; letter-spacing: 0; color: #d1d5db; }

        @keyframes ce-slide-in {
          from { opacity: 0; transform: scale(0.85) translateY(6px); }
          to   { opacity: 1; transform: scale(1)    translateY(0); }
        }
        @keyframes ce-slide-out {
          from { opacity: 1; transform: scale(1)    translateY(0); }
          to   { opacity: 0; transform: scale(0.85) translateY(6px); }
        }
        @keyframes ce-insert-pulse {
          0%,100% { box-shadow: 0 0 0 2px #7c3aed40; }
          50%      { box-shadow: 0 0 0 4px #7c3aed80; }
        }

        .ce__flow {
          display: flex; align-items: flex-start; flex-wrap: nowrap;
          gap: 8px; padding: 24px 20px;
          background: #fafafa; border: 1px solid #f3f4f6; border-radius: 10px;
          user-select: none; min-height: 380px;
          overflow-x: auto; overflow-y: visible;
          scrollbar-width: thin; scrollbar-color: #e5e7eb transparent;
        }
        .ce__flow::-webkit-scrollbar { height: 6px; }
        .ce__flow::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 3px; }
        .ce__flow--dragging { cursor: grabbing; }
        .ce__flow-item { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

        .ce__card {
          display: flex; flex-direction: column; gap: 0;
          background: white; border: 1.5px solid #e5e7eb;
          border-radius: 10px; cursor: grab; user-select: none;
          box-shadow: 0 1px 3px rgba(0,0,0,0.06);
          transition: box-shadow 0.2s cubic-bezier(0.34,1.56,0.64,1),
                      opacity 0.2s ease,
                      transform 0.2s cubic-bezier(0.34,1.56,0.64,1),
                      border-color 0.15s ease;
          min-width: 200px;
          width: 200px;
          min-height: 130px;
          animation: ce-slide-in 0.25s cubic-bezier(0.34,1.56,0.64,1) both;
        }
        .ce__card:hover:not(.ce__card--locked):not(.ce__card--dragging) {
          box-shadow: 0 4px 16px rgba(0,0,0,0.1);
          transform: translateY(-1px);
        }
        .ce__flow--dragging .ce__card { cursor: grabbing; }
        .ce__card--ghost {
          opacity: 0.25;
          pointer-events: none;
        }
        .ce__card--slot {
          animation: ce-insert-pulse 0.7s ease infinite;
        }
        .ce__card--removing {
          animation: ce-slide-out 0.22s ease forwards;
        }
        .ce__card--locked   { cursor: default; background: #f9fafb; }
        .ce__card--done     { cursor: default; background: #f0fdf4; border-color: #10b981 !important; }

        /* Gap placeholder shown where dragged card will drop */
        .ce__gap {
          border: 2px dashed #cbd5e1; border-radius: 10px;
          background: #f8fafc;
        }

        /* Floating ghost that follows cursor */
        .ce__ghost {
          position: fixed; z-index: 9999; pointer-events: none;
          background: white; border: 2px solid;
          border-radius: 10px;
          box-shadow: 0 16px 48px rgba(0,0,0,0.25), 0 4px 12px rgba(0,0,0,0.15);
          transform: rotate(2deg) scale(1.04);
          transition: none;
          opacity: 0.95;
        }

        .ce__card-top {
          display: flex; align-items: center; gap: 6px; padding: 8px 10px;
        }
        .ce__card-bottom {
          padding: 0 10px 8px; border-top: 1px solid #f9fafb; padding-top: 5px;
          display: flex; flex-direction: column; gap: 5px;
        }
        .ce__role-input {
          width: 100%; font-size: 10px; font-weight: 600; color: #6b7280;
          border: 1px solid #e5e7eb; border-radius: 5px; padding: 3px 7px;
          font-family: inherit; outline: none; background: #f9fafb;
          transition: border-color 0.12s;
        }
        .ce__role-input:focus { border-color: #7c3aed; background: white; }

        .ce__grip { color: #d1d5db; display: flex; align-items: center; flex-shrink: 0; }
        .ce__card-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

        .ce__card-label {
          font-size: 12px; font-weight: 700; border: none; background: transparent;
          font-family: inherit; flex: 1; min-width: 0;
          outline: none; cursor: text; padding: 0; white-space: nowrap;
        }
        .ce__card-label:read-only { cursor: default; }
        .ce__card-label-static { font-size: 12px; font-weight: 700; color: #059669; }

        .ce__card-role {
          font-size: 9px; font-weight: 600; padding: 2px 6px; border-radius: 10px;
          text-transform: capitalize; white-space: nowrap; flex-shrink: 0;
        }
        .ce__remove {
          display: flex; align-items: center; justify-content: center;
          width: 16px; height: 16px; border-radius: 50%; background: #fee2e2;
          color: #ef4444; border: none; cursor: pointer; flex-shrink: 0;
          transition: background 0.1s; margin-left: auto;
        }
        .ce__remove:hover { background: #fecaca; }

        .ce__arrow {
          color: #d1d5db; display: flex; align-items: center; align-self: center; margin-top: -12px;
          transition: color 0.15s ease, transform 0.15s ease;
        }
        .ce__arrow--insert { color: #7c3aed; transform: scale(1.3); }

        .ce__add-btn {
          width: 30px; height: 30px; border-radius: 50%; border: 2px dashed #d1d5db;
          background: white; color: #9ca3af; display: flex; align-items: center;
          justify-content: center; cursor: pointer;
          transition: border-color 0.2s ease, color 0.2s ease, transform 0.2s cubic-bezier(0.34,1.56,0.64,1);
          align-self: center; margin-top: -12px;
        }
        .ce__add-btn:hover { border-color: #7c3aed; color: #7c3aed; transform: scale(1.15) rotate(90deg); }
        .ce__add-btn:hover { border-color: #7c3aed; color: #7c3aed; }
        .ce__add-menu {
          position: fixed; z-index: 9999;
          background: white; border: 1px solid #e5e7eb; border-radius: 10px;
          box-shadow: 0 8px 32px rgba(0,0,0,0.15); min-width: 220px;
          max-height: 420px; overflow-y: auto;
        }
        .ce__add-hint {
          padding: 8px 14px 6px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.05em; color: #9ca3af; background: #fafafa;
          border-bottom: 1px solid #f3f4f6;
        }
        .ce__add-divider { height: 1px; background: #f3f4f6; margin: 4px 0; }
        .ce__add-custom { padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
        .ce__add-people { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
        .ce__add-people-hint { font-size: 10px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
        .ce__add-person-chip {
          display: flex; align-items: center; gap: 8px; padding: 6px 8px;
          background: none; border: 1px solid #f3f4f6; border-radius: 7px;
          cursor: pointer; text-align: left; transition: all 0.12s; width: 100%;
        }
        .ce__add-person-chip:hover { background: #f5f3ff; border-color: #c4b5fd; }
        .ce__add-person-av {
          width: 24px; height: 24px; border-radius: 50%; background: #e0e7ff; color: #4f46e5;
          font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0;
        }
        .ce__add-person-info { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
        .ce__add-person-name { font-size: 12px; font-weight: 600; color: #111827; }
        .ce__add-person-role { font-size: 10px; color: #9ca3af; }
        .ce__add-custom-inp {
          width: 100%; font-size: 12px; border: 1px solid #e5e7eb; border-radius: 6px;
          padding: 6px 10px; font-family: inherit; outline: none;
          transition: border-color 0.12s;
        }
        .ce__add-custom-inp:focus { border-color: #7c3aed; }
        .ce__add-custom-btn {
          display: flex; align-items: center; gap: 4px; justify-content: center;
          padding: 7px 12px; background: #7c3aed; color: white; border: none;
          border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;
          font-family: inherit; transition: background 0.12s;
        }
        .ce__add-custom-btn:hover { background: #6d28d9; }
        .ce__add-custom-btn:disabled { background: #e5e7eb; color: #9ca3af; cursor: not-allowed; }
        .ce__add-opt {
          display: flex; align-items: center; gap: 8px; padding: 9px 14px;
          width: 100%; background: none; border: none; font-size: 13px; font-weight: 500;
          color: #374151; cursor: pointer; font-family: inherit; text-align: left; transition: background 0.1s;
        }
        .ce__add-opt:hover { background: #f5f3ff; color: #7c3aed; }
        .ce__add-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

        /* Assignee picker */
        .ap { position: relative; }
        .ap__names { display: flex; flex-direction: column; gap: 3px; margin-bottom: 5px; }
        .ap__name-chip {
          display: flex; align-items: center; gap: 5px;
          font-size: 11px; font-weight: 500; color: #374151;
          padding: 2px 4px; border-radius: 4px;
        }
        .ap__name-chip--sel { color: #4f46e5; font-weight: 600; }
        .ap__avatar-sm {
          width: 18px; height: 18px; border-radius: 50%;
          background: #e0e7ff; color: #4f46e5;
          font-size: 10px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0;
        }
        .ap__btn {
          display: flex; align-items: center; gap: 4px; padding: 3px 8px;
          border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa;
          font-size: 10px; font-weight: 600; color: #6b7280; cursor: pointer;
          font-family: inherit; white-space: nowrap; transition: border-color 0.12s, color 0.12s;
          width: 100%;
        }
        .ap__btn:hover { border-color: #7c3aed; color: #7c3aed; }
        .ap__menu {
          position: absolute; top: calc(100% + 4px); left: 0; z-index: 200;
          background: white; border: 1px solid #e5e7eb; border-radius: 10px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.12); overflow: hidden; min-width: 190px;
        }
        .ap__hint {
          padding: 8px 12px 6px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.05em; color: #9ca3af; background: #fafafa;
          border-bottom: 1px solid #f3f4f6;
        }
        .ap__opt {
          display: flex; align-items: center; gap: 8px; padding: 8px 12px;
          width: 100%; background: none; border: none; font-size: 12px; font-weight: 500;
          color: #374151; cursor: pointer; font-family: inherit; text-align: left; transition: background 0.1s;
        }
        .ap__opt:hover { background: #f5f3ff; }
        .ap__opt--active { color: #7c3aed; font-weight: 600; }
        .ap__check { width: 14px; height: 14px; display: flex; align-items: center; justify-content: center; color: #7c3aed; flex-shrink: 0; }
        .ce__actions {
          display: flex; align-items: center; justify-content: flex-end; gap: 8px;
          padding-top: 4px;
        }
        .ce__reset-btn {
          display: flex; align-items: center; gap: 6px; padding: 8px 14px;
          border: 1.5px solid #e5e7eb; border-radius: 8px; background: white;
          font-size: 12px; font-weight: 600; color: #6b7280; cursor: pointer;
          font-family: inherit; transition: border-color 0.12s, color 0.12s;
        }
        .ce__reset-btn:hover { border-color: #ef4444; color: #ef4444; }
        .ce__save-btn {
          display: flex; align-items: center; gap: 6px; padding: 8px 16px;
          border: none; border-radius: 8px; background: #7c3aed; color: white;
          font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
          transition: background 0.15s ease, transform 0.15s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.15s ease;
        }
        .ce__save-btn:hover { background: #6d28d9; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(124,58,237,0.4); }
        .ce__save-btn:active { transform: translateY(0); }
        .ce__default-btn {
          display: flex; align-items: center; gap: 6px; padding: 8px 14px;
          border: 1.5px solid #6366f1; border-radius: 8px; background: white;
          font-size: 12px; font-weight: 600; color: #6366f1; cursor: pointer;
          font-family: inherit; transition: all 0.12s;
        }
        .ce__default-btn:hover { background: #eef2ff; }

        .sp__section-icon--orange { background: #fff7ed; color: #ea580c; }
        .sp__section-icon--teal   { background: #f0fdfa; color: #0d9488; }

        /* ── Charges Manager (App Orange Theme) ── */
        .chg__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .chg__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(234, 88, 12, 0.05) 0%, rgba(249, 115, 22, 0.04) 100%);
          border: 1px solid rgba(234, 88, 12, 0.15);
          box-shadow: 0 4px 20px rgba(234, 88, 12, 0.02);
        }
        .chg__ai-left { display: flex; align-items: center; gap: 12px; }
        .chg__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #ea580c 0%, #c2410c 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(234, 88, 12, 0.25); flex-shrink: 0;
        }
        .chg__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .chg__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .chg__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .chg__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #fff7ed; color: #c2410c; border: 1px solid #ffedd5;
        }
        .chg__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #ea580c; }

        .chg__add-card {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          padding: 16px 20px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.03);
          display: flex; flex-direction: column; gap: 12px;
        }
        .chg__add-header {
          display: flex; align-items: center; gap: 6px;
          font-size: 12px; font-weight: 800; color: #0f172a; text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .chg__add-row { display: flex; align-items: center; gap: 10px; }
        .chg__inp-ai {
          padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 8px;
          font-size: 12.5px; font-weight: 500; color: #0f172a; outline: none; background: #fff;
          font-family: inherit; transition: all 0.15s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
          box-sizing: border-box;
        }
        .chg__inp-ai:focus { border-color: #ea580c; box-shadow: 0 0 0 3px rgba(234, 88, 12, 0.15); }
        .chg__inp-ai--name { flex: 1; min-width: 180px; }
        .chg__inp-ai--hsn  { width: 140px; }
        .chg__inp-ai--rate { width: 110px; }

        .chg__add-btn-ai {
          display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
          border-radius: 8px; border: none; background: linear-gradient(135deg, #ea580c 0%, #c2410c 100%);
          color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit;
          box-shadow: 0 2px 8px rgba(234, 88, 12, 0.25); white-space: nowrap; transition: all 0.15s ease;
        }
        .chg__add-btn-ai:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
        .chg__add-btn-ai:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

        .chg__cancel-btn-ai {
          padding: 8px 14px; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;
          border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
          transition: all 0.15s ease; white-space: nowrap;
        }
        .chg__cancel-btn-ai:hover { background: #e2e8f0; }
        .chg__err { font-size: 12px; color: #ef4444; }

        .chg__card {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.03); overflow: hidden;
        }
        .chg__card-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 14px 18px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
        }
        .chg__card-title {
          display: flex; align-items: center; gap: 8px;
          font-size: 13px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em;
        }
        .chg__icon-orange { color: #ea580c; }
        .chg__card-count { font-size: 11px; font-weight: 600; color: #64748b; }

        .chg__table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
        .chg__table th {
          padding: 12px 18px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.08em; color: #64748b; background: #f8fafc;
          border-bottom: 1px solid #e2e8f0; text-align: left;
        }
        .chg__tr { transition: background 0.15s ease; border-bottom: 1px solid #f1f5f9; height: 52px; }
        .chg__tr:last-child { border-bottom: none; }
        .chg__tr:hover:not(.chg__tr--editing) { background: #fffdfb; }
        .chg__tr--editing { background: #fff7ed; }
        .chg__tr td { padding: 12px 18px; vertical-align: middle; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        .chg__hsn-badge {
          display: inline-block; padding: 2px 8px; border-radius: 6px;
          background: #f8fafc; color: #475569; font-size: 11px; font-weight: 700;
          border: 1px solid #e2e8f0; font-family: monospace;
        }
        .chg__rate-badge {
          display: inline-block; padding: 2px 8px; border-radius: 6px;
          background: #fff7ed; color: #ea580c; font-size: 11px; font-weight: 700;
          border: 1px solid #ffedd5;
        }
        .chg__dash { color: #cbd5e1; font-size: 12px; font-style: italic; }

        .chg__action-cell { text-align: right; vertical-align: middle; white-space: nowrap; overflow: visible; text-overflow: clip; }
        .chg__action-wrap { display: inline-flex; align-items: center; justify-content: flex-end; gap: 10px; }
        .chg__action-btn {
          border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 700; font-family: inherit;
          padding: 6px 12px; transition: filter 0.12s ease; white-space: nowrap; flex-shrink: 0;
        }
        .chg__action-btn:hover { filter: brightness(0.98); }
        .chg__action-btn--primary { background: #ffedd5; color: #c2410c; }

        .chg__del-btn {
          display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
          border: 1px solid #fee2e2; border-radius: 8px; background: #fff; color: #ef4444; cursor: pointer;
          flex-shrink: 0; min-width: 30px; transition: all 0.15s ease;
        }
        .chg__del-btn:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }
        .chg__loading { padding: 30px; text-align: center; color: #94a3b8; font-size: 13px; font-weight: 500; }
        .chg__empty-state {
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          padding: 40px 20px; text-align: center; color: #94a3b8;
        }
        .chg__empty-icon {
          width: 48px; height: 48px; border-radius: 12px; background: #fff7ed; color: #ea580c;
          display: flex; align-items: center; justify-content: center; margin-bottom: 12px;
        }
        .chg__empty-title { font-size: 14px; font-weight: 700; color: #334155; }
        .chg__empty-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; max-width: 340px; }

        /* ── Brands Manager (App Purple Theme) ── */
        .brands__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .brands__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(139, 92, 246, 0.05) 0%, rgba(124, 58, 237, 0.04) 100%);
          border: 1px solid rgba(139, 92, 246, 0.15);
          box-shadow: 0 4px 20px rgba(139, 92, 246, 0.02);
        }
        .brands__ai-left { display: flex; align-items: center; gap: 12px; }
        .brands__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(139, 92, 246, 0.25); flex-shrink: 0;
        }
        .brands__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .brands__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .brands__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .brands__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #f5f3ff; color: #6d28d9; border: 1px solid #ddd6fe;
        }
        .brands__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #8b5cf6; }

        /* ── Cost Centers Manager (App Sky Theme) ── */
        .cc__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .cc__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(2, 132, 199, 0.05) 0%, rgba(3, 105, 161, 0.04) 100%);
          border: 1px solid rgba(2, 132, 199, 0.15);
          box-shadow: 0 4px 20px rgba(2, 132, 199, 0.02);
        }
        .cc__ai-left { display: flex; align-items: center; gap: 12px; }
        .cc__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(2, 132, 199, 0.25); flex-shrink: 0;
        }
        .cc__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .cc__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .cc__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .cc__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #f0f9ff; color: #0369a1; border: 1px solid #bae6fd;
        }
        .cc__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #0284c7; }

        /* ── COA Rules Manager (App Teal Theme) ── */
        .coa__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .coa__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(13, 148, 136, 0.05) 0%, rgba(20, 184, 166, 0.04) 100%);
          border: 1px solid rgba(13, 148, 136, 0.15);
          box-shadow: 0 4px 20px rgba(13, 148, 136, 0.02);
        }
        .coa__ai-left { display: flex; align-items: center; gap: 12px; }
        .coa__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(13, 148, 136, 0.25); flex-shrink: 0;
        }
        .coa__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .coa__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .coa__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .coa__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #f0fdfa; color: #0f766e; border: 1px solid #ccfbf1;
        }
        .coa__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #0d9488; }

        .coa__add-card {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          padding: 16px 20px; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.03);
          display: flex; flex-direction: column; gap: 12px;
        }
        .coa__add-header {
          display: flex; align-items: center; gap: 6px;
          font-size: 12px; font-weight: 800; color: #0f172a; text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .coa__add-row { display: flex; align-items: center; gap: 10px; }
        .coa__sel-ai {
          padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 8px;
          font-size: 12.5px; font-weight: 600; color: #334155; background: #fff;
          outline: none; cursor: pointer; transition: all 0.15s ease;
          font-family: inherit; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
        }
        .coa__sel-ai:hover { border-color: #94a3b8; }
        .coa__sel-ai:focus { border-color: #0d9488; box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.15); }
        .coa__sel-ai--head { width: 220px; }
        .coa__sel-ai--cat  { width: 200px; }

        .coa__vendor-wrap { position: relative; flex: 1; min-width: 180px; }
        .coa__vendor-list {
          position: absolute; top: calc(100% + 4px); left: 0; right: 0;
          background: white; border: 1px solid #e2e8f0; border-radius: 8px;
          box-shadow: 0 10px 25px rgba(0,0,0,0.1); max-height: 220px; overflow-y: auto;
          z-index: 50; margin: 0; padding: 4px 0; list-style: none;
        }
        .coa__vendor-opt {
          padding: 8px 12px; font-size: 13px; font-weight: 500; color: #1e293b; cursor: pointer;
        }
        .coa__vendor-opt:hover { background: #f0fdfa; color: #0d9488; }
        .coa__vendor-empty { padding: 8px 12px; font-size: 12px; color: #94a3b8; }

        .coa__inp-ai {
          width: 100%; padding: 8px 12px; border: 1px solid #cbd5e1; border-radius: 8px;
          font-size: 12.5px; font-weight: 500; color: #0f172a; outline: none; background: #fff;
          font-family: inherit; transition: all 0.15s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
          box-sizing: border-box;
        }
        .coa__inp-ai:focus { border-color: #0d9488; box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.15); }

        .coa__add-btn-ai {
          display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
          border-radius: 8px; border: none; background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%);
          color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit;
          box-shadow: 0 2px 8px rgba(13, 148, 136, 0.25); white-space: nowrap; transition: all 0.15s ease;
        }
        .coa__add-btn-ai:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
        .coa__add-btn-ai:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

        .coa__cancel-btn-ai {
          padding: 8px 14px; background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;
          border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
          transition: all 0.15s ease; white-space: nowrap;
        }
        .coa__cancel-btn-ai:hover { background: #e2e8f0; }
        .coa__err { font-size: 12px; color: #ef4444; }

        .coa__card {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.03); overflow: hidden;
        }
        .coa__card-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 14px 18px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
        }
        .coa__card-title {
          display: flex; align-items: center; gap: 8px;
          font-size: 13px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em;
        }
        .coa__icon-teal { color: #0d9488; }
        .coa__card-count {
          font-size: 11px; font-weight: 600; color: #64748b;
        }

        .coa__table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
        .coa__table th {
          padding: 12px 18px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.08em; color: #64748b; background: #f8fafc;
          border-bottom: 1px solid #e2e8f0; text-align: left;
        }
        .coa__tr { transition: background 0.15s ease; border-bottom: 1px solid #f1f5f9; height: 56px; }
        .coa__tr:last-child { border-bottom: none; }
        .coa__tr:hover:not(.coa__tr--editing) { background: #fafbff; }
        .coa__tr--editing { background: #f0fdfa; }
        .coa__tr td { padding: 12px 18px; vertical-align: middle; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        .coa__badge-pill {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #f0fdfa; color: #0d9488; border: 1px solid #ccfbf1;
          letter-spacing: 0.02em; line-height: 1;
        }
        .coa__badge-dot { width: 7px; height: 7px; border-radius: 50%; background: #14b8a6; flex-shrink: 0; }

        .coa__cell-vendor { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; color: #1e293b; }
        .coa__cell-cat { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; color: #475569; }
        .coa__cell-icon { color: #94a3b8; flex-shrink: 0; }
        .coa__dash { color: #cbd5e1; font-size: 12px; font-style: italic; }

        .coa__action-cell { text-align: right; vertical-align: middle; white-space: nowrap; overflow: visible; text-overflow: clip; }
        .coa__action-wrap { display: inline-flex; align-items: center; justify-content: flex-end; gap: 10px; }
        .coa__action-btn {
          border: none; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 700; font-family: inherit;
          padding: 6px 12px; transition: filter 0.12s ease; white-space: nowrap; flex-shrink: 0;
        }
        .coa__action-btn:hover { filter: brightness(0.98); }
        .coa__action-btn--primary { background: #e0f2fe; color: #0369a1; }
        .coa__action-btn--secondary { background: #f1f5f9; color: #475569; }

        .coa__del-btn {
          display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
          border: 1px solid #fee2e2; border-radius: 8px; background: #fff; color: #ef4444; cursor: pointer;
          flex-shrink: 0; min-width: 30px; transition: all 0.15s ease;
        }
        .coa__del-btn:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }

        .coa__loading { padding: 30px; text-align: center; color: #94a3b8; font-size: 13px; font-weight: 500; }
        .coa__empty-state {
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          padding: 40px 20px; text-align: center; color: #94a3b8;
        }
        .coa__empty-icon {
          width: 48px; height: 48px; border-radius: 12px; background: #f0fdfa; color: #0d9488;
          display: flex; align-items: center; justify-content: center; margin-bottom: 12px;
        }
        .coa__empty-title { font-size: 14px; font-weight: 700; color: #334155; }
        .coa__empty-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; max-width: 320px; }

        /* ── Section icon: indigo / green ── */
        .sp__section-icon--indigo { background: #eef2ff; color: #4f46e5; }
        .sp__section-icon--green  { background: #f0fdf4; color: #16a34a; }

        /* ── Categories Manager (App Green Theme) ── */
        .cat__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .cat__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(22, 163, 74, 0.05) 0%, rgba(16, 185, 129, 0.04) 100%);
          border: 1px solid rgba(22, 163, 74, 0.15);
          box-shadow: 0 4px 20px rgba(22, 163, 74, 0.02);
        }
        .cat__ai-left { display: flex; align-items: center; gap: 12px; }
        .cat__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #16a34a 0%, #15803d 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(22, 163, 74, 0.25); flex-shrink: 0;
        }
        .cat__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .cat__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .cat__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .cat__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
        }
        .cat__stat-badge--slate { background: #f8fafc; color: #334155; border: 1px solid #e2e8f0; }
        .cat__stat-badge--green { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
        .cat__stat-dot { width: 7px; height: 7px; border-radius: 50%; }
        .cat__stat-dot--slate { background: #94a3b8; }
        .cat__stat-dot--green { background: #16a34a; }

        .cat__grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
        @media (max-width: 1024px) { .cat__grid { grid-template-columns: 1fr; } }

        .cat__card {
          background: #fff; border: 1px solid #e2e8f0; border-radius: 14px;
          box-shadow: 0 10px 30px rgba(15, 23, 42, 0.03); overflow: hidden;
          display: flex; flex-direction: column;
        }
        .cat__card-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 14px 18px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
        }
        .cat__card-title {
          display: flex; align-items: center; gap: 8px;
          font-size: 13px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em;
        }
        .cat__icon-green { color: #16a34a; }
        .cat__card-count {
          font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.06em; color: #64748b; background: #fff;
          padding: 3px 8px; border-radius: 6px; border: 1px solid #e2e8f0;
        }

        .cat__table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
        .cat__table th {
          padding: 10px 16px; font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.07em; color: #64748b; background: #fff;
          border-bottom: 1px solid #e2e8f0; text-align: left;
        }
        .cat__tr { transition: background 0.15s ease; border-bottom: 1px solid #f1f5f9; }
        .cat__tr:last-child { border-bottom: none; }
        .cat__tr:hover { background: #f8fafc; }
        .cat__tr td { padding: 10px 16px; vertical-align: middle; }

        .cat__name-cell { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .cat__name-text { font-size: 13px; font-weight: 600; color: #1e293b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        .cat__head-badge {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 3px 8px; border-radius: 6px; font-size: 10px; font-weight: 800;
          letter-spacing: 0.03em; text-transform: uppercase; border: 1px solid transparent;
          flex-shrink: 0; line-height: 1;
        }
        .cat__head-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

        .cat__sel-ai {
          width: 100%; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 8px;
          font-size: 12px; font-weight: 600; color: #334155; background: #fff;
          outline: none; cursor: pointer; transition: all 0.15s ease;
          font-family: inherit; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
        }
        .cat__sel-ai:hover { border-color: #94a3b8; }
        .cat__sel-ai:focus { border-color: #16a34a; box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.15); }
        .cat__sel-ai--add { flex: 1; max-width: 180px; }

        .cat__del-btn {
          display: inline-flex; align-items: center; justify-content: center;
          width: 28px; height: 28px; border-radius: 8px; border: 1px solid #fee2e2;
          background: #fff; color: #ef4444; cursor: pointer; transition: all 0.15s ease;
        }
        .cat__del-btn:hover { background: #fef2f2; border-color: #fca5a5; color: #dc2626; }

        .cat__loading { padding: 30px; text-align: center; color: #94a3b8; font-size: 13px; font-weight: 500; }
        .cat__empty-state {
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          padding: 36px 20px; text-align: center; color: #94a3b8;
        }
        .cat__empty-icon {
          width: 44px; height: 44px; border-radius: 12px; background: #f1f5f9; color: #94a3b8;
          display: flex; align-items: center; justify-content: center; margin-bottom: 10px;
        }
        .cat__empty-title { font-size: 13px; font-weight: 700; color: #334155; }
        .cat__empty-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; max-width: 240px; }

        .cat__add-card {
          padding: 14px 18px; background: #f8fafc; border-top: 1px solid #e2e8f0;
          display: flex; flex-direction: column; gap: 10px;
        }
        .cat__add-header {
          display: flex; align-items: center; gap: 6px;
          font-size: 11px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.05em; color: #475569;
        }
        .cat__add-row { display: flex; align-items: center; gap: 8px; }
        .cat__inp-ai {
          flex: 1; padding: 7px 12px; border: 1px solid #cbd5e1; border-radius: 8px;
          font-size: 12.5px; font-weight: 500; color: #0f172a; outline: none; background: #fff;
          font-family: inherit; transition: all 0.15s ease; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
        }
        .cat__inp-ai:focus { border-color: #16a34a; box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.15); }
        .cat__add-btn-ai {
          display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px;
          border-radius: 8px; border: none; background: linear-gradient(135deg, #16a34a 0%, #15803d 100%);
          color: #fff; font-size: 12px; font-weight: 700; cursor: pointer; font-family: inherit;
          box-shadow: 0 2px 8px rgba(22, 163, 74, 0.25); white-space: nowrap; transition: all 0.15s ease;
        }
        .cat__add-btn-ai:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
        .cat__add-btn-ai:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }
        .cat__err { font-size: 12px; color: #ef4444; }

        /* ── TDS Configuration ── */
        .tds__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .tds__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(79, 70, 229, 0.05) 0%, rgba(99, 102, 241, 0.04) 100%);
          border: 1px solid rgba(79, 70, 229, 0.15);
          box-shadow: 0 4px 20px rgba(79, 70, 229, 0.02);
        }
        .tds__ai-left { display: flex; align-items: center; gap: 12px; }
        .tds__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(79, 70, 229, 0.25); flex-shrink: 0;
        }
        .tds__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .tds__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .tds__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .tds__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #eef2ff; color: #4338ca; border: 1px solid #c7d2fe;
        }
        .tds__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #4f46e5; }

        /* ── FX & Currency ── */
        .fx__wrap { display: flex; flex-direction: column; gap: 16px; padding: 4px 0; }
        .fx__ai-header {
          display: flex; align-items: center; justify-content: space-between; gap: 16px;
          padding: 16px 20px; border-radius: 14px;
          background: linear-gradient(135deg, rgba(8, 145, 178, 0.05) 0%, rgba(14, 116, 144, 0.04) 100%);
          border: 1px solid rgba(8, 145, 178, 0.15);
          box-shadow: 0 4px 20px rgba(8, 145, 178, 0.02);
        }
        .fx__ai-left { display: flex; align-items: center; gap: 12px; }
        .fx__ai-spark-icon {
          width: 36px; height: 36px; border-radius: 10px;
          background: linear-gradient(135deg, #0891b2 0%, #0e7490 100%);
          color: #fff; display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 12px rgba(8, 145, 178, 0.25); flex-shrink: 0;
        }
        .fx__ai-title { font-size: 14px; font-weight: 800; color: #0f172a; letter-spacing: -0.01em; }
        .fx__ai-sub { font-size: 12px; color: #64748b; margin-top: 2px; line-height: 1.4; }
        .fx__ai-stats { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
        .fx__stat-badge {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 5px 12px; border-radius: 999px; font-size: 11px; font-weight: 700;
          background: #ecfeff; color: #0e7490; border: 1px solid #cff4fc;
        }
        .fx__stat-dot { width: 7px; height: 7px; border-radius: 50%; background: #0891b2; }

        .tds { font-family: inherit; }

        .tds__tabbar {
          display: flex; border-bottom: 1px solid #e5e7eb; padding: 0 20px;
          gap: 0; background: #fafafa;
        }
        .tds__tabbtn {
          padding: 10px 16px; font-size: 12px; font-weight: 600; border: none;
          background: none; cursor: pointer; color: #6b7280; border-bottom: 2px solid transparent;
          margin-bottom: -1px; font-family: inherit; transition: color 0.12s, border-color 0.12s;
        }
        .tds__tabbtn--active { color: #4f46e5; border-bottom-color: #4f46e5; }
        .tds__tabbtn:hover:not(.tds__tabbtn--active) { color: #374151; }

        .tds__panel { padding: 16px 20px; }
        .tds__panel--prefs { max-width: 600px; }

        .tds__form-row {
          display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;
        }
        .tds__inp {
          padding: 7px 10px; border: 1.5px solid #e5e7eb; border-radius: 8px;
          font-size: 12px; font-family: inherit; color: #111827; outline: none;
          background: white; transition: border-color 0.12s;
        }
        .tds__inp:focus { border-color: #4f46e5; }
        .tds__inp--code   { width: 90px; }
        .tds__inp--label  { width: 110px; }
        .tds__inp--nature { flex: 1; min-width: 180px; }
        .tds__inp--rate   { width: 80px; }
        .tds__inp--thresh { width: 150px; }
        .tds__inp--tamt   { width: 120px; }

        .tds__save-btn {
          display: flex; align-items: center; gap: 5px; padding: 7px 14px;
          background: #4f46e5; color: white; border: none; border-radius: 8px;
          font-size: 12px; font-weight: 600; cursor: pointer; font-family: inherit;
          white-space: nowrap; transition: background 0.15s;
        }
        .tds__save-btn:hover { background: #4338ca; }
        .tds__cancel-btn {
          padding: 7px 12px; background: none; border: 1.5px solid #e5e7eb;
          border-radius: 8px; font-size: 12px; color: #6b7280; cursor: pointer;
          font-family: inherit; transition: border-color 0.12s;
        }
        .tds__cancel-btn:hover { border-color: #9ca3af; }
        .tds__err   { font-size: 12px; color: #ef4444; margin-bottom: 10px; }
        .tds__empty { font-size: 13px; color: #9ca3af; padding: 12px 0; }
        .tds__hint  { font-size: 11px; color: #9ca3af; margin-top: 12px; padding-top: 10px; border-top: 1px solid #f3f4f6; }

        .tds__table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 4px; }
        .tds__table th {
          text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 0.04em; color: #9ca3af; padding: 0 12px 8px 0;
        }
        .tds__row { border-top: 1px solid #f3f4f6; }
        .tds__row--editing { background: #eef2ff; }
        .tds__row td { padding: 9px 12px 9px 0; vertical-align: middle; }

        .tds__code-badge {
          display: inline-block; padding: 2px 8px; border-radius: 6px;
          background: #eef2ff; color: #4f46e5; font-size: 11px; font-weight: 700;
          border: 1px solid #c7d2fe; white-space: nowrap; font-family: monospace;
        }
        .tds__thresh-badge {
          display: inline-block; padding: 2px 8px; border-radius: 6px;
          background: #f3f4f6; color: #374151; font-size: 11px; font-weight: 600;
        }
        .tds__count-badge {
          display: inline-block; padding: 2px 8px; border-radius: 12px;
          background: #f0fdf4; color: #16a34a; font-size: 11px; font-weight: 600;
          border: 1px solid #bbf7d0;
        }
        .tds__priority-num {
          display: inline-flex; align-items: center; justify-content: center;
          width: 28px; height: 28px; border-radius: 50%; background: #f3f4f6;
          font-size: 11px; font-weight: 700; color: #6b7280;
        }
        .tds__cat-chip {
          display: inline-block; padding: 2px 9px; border-radius: 12px;
          background: #faf5ff; color: #7c3aed; font-size: 11px; font-weight: 600;
          border: 1px solid #e9d5ff;
        }
        .tds__soft { font-size: 12px; color: #6b7280; }
        .tds__actions-cell { text-align: right; white-space: nowrap; }
        .tds__edit-btn {
          padding: 4px 10px; border: 1.5px solid #e5e7eb; border-radius: 6px;
          background: white; font-size: 11px; font-weight: 600; color: #374151;
          cursor: pointer; font-family: inherit; margin-right: 4px; transition: border-color 0.12s;
        }
        .tds__edit-btn:hover { border-color: #4f46e5; color: #4f46e5; }
        .tds__del-btn {
          padding: 4px 7px; border: 1.5px solid #fee2e2; border-radius: 6px;
          background: #fff5f5; color: #ef4444; cursor: pointer; display: inline-flex;
          align-items: center; transition: background 0.12s;
        }
        .tds__del-btn:hover { background: #fee2e2; }

        /* Mapping rules */
        .tds__map-legend {
          font-size: 11px; color: #6b7280; background: #fafafa; border: 1px solid #e5e7eb;
          border-radius: 8px; padding: 7px 12px; margin-bottom: 12px;
          display: flex; align-items: center; gap: 6px;
        }

        /* Preferences */
        .tds__pref-grid { display: flex; flex-direction: column; gap: 20px; }
        .tds__pref-row { display: flex; gap: 16px; align-items: flex-start; }
        .tds__pref-lbl {
          width: 160px; flex-shrink: 0; font-size: 13px; font-weight: 600;
          color: #374151; padding-top: 4px;
        }
        .tds__pref-ctrl { display: flex; flex-direction: column; gap: 6px; }
        .tds__pref-hint { font-size: 11px; color: #9ca3af; margin-top: 2px; }
        .tds__radio-opt {
          display: flex; align-items: center; gap: 8px; font-size: 13px; color: #374151; cursor: pointer;
        }
        .tds__radio-opt input { accent-color: #4f46e5; }

        /* Toggle switch */
        .tds__toggle { display: flex; align-items: center; gap: 10px; cursor: pointer; }
        .tds__toggle input { display: none; }
        .tds__toggle-track {
          width: 36px; height: 20px; border-radius: 10px; background: #d1d5db;
          position: relative; transition: background 0.2s; flex-shrink: 0;
        }
        .tds__toggle input:checked + .tds__toggle-track { background: #4f46e5; }
        .tds__toggle-track::after {
          content: ''; position: absolute; top: 2px; left: 2px;
          width: 16px; height: 16px; border-radius: 50%; background: white;
          transition: transform 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,.2);
        }
        .tds__toggle input:checked + .tds__toggle-track::after { transform: translateX(16px); }
        .tds__toggle-label { font-size: 13px; color: #374151; }

        /* ── Settings material system ─────────────────────────────────────
           A light, consistent hierarchy keeps configuration work calm: one
           primary surface, quiet secondary materials, and immediate feedback
           on every actionable control. */
        .sp__page-body { background: #f7f8fb; }
        .brands__ai-header, .cc__ai-header, .chg__ai-header, .cat__ai-header, .coa__ai-header, .tds__ai-header, .fx__ai-header {
          min-height: 72px; padding: 18px 20px; border-radius: 18px;
          box-shadow: 0 10px 30px rgba(15,23,42,.035);
        }
        .brands__ai-title, .cc__ai-title, .chg__ai-title, .cat__ai-title, .coa__ai-title, .tds__ai-title, .fx__ai-title {
          font-size: 15px; letter-spacing: -.018em; line-height: 1.2;
        }
        .brands__ai-sub, .cc__ai-sub, .chg__ai-sub, .cat__ai-sub, .coa__ai-sub, .tds__ai-sub, .fx__ai-sub { line-height: 1.5; }
        .brands__stat-badge, .cc__stat-badge, .chg__stat-badge, .cat__stat-badge, .coa__stat-badge, .tds__stat-badge, .fx__stat-badge {
          min-height: 30px; box-sizing: border-box; padding: 6px 12px;
        }
        .chg__card, .coa__card, .cat__card { border-radius: 16px; box-shadow: 0 8px 26px rgba(15,23,42,.035); }
        .chg__add-card, .coa__add-card { border-radius: 16px; box-shadow: 0 8px 26px rgba(15,23,42,.035); }
        :is(.chg__add-btn-ai, .coa__add-btn-ai, .cat__add-btn-ai, .tds__save-btn, .fx__save-btn, .brands__save-btn, .cc__save-btn):active { transform: scale(.97); }

        /* Brands — rows make the current scope obvious and give every action
           a reliable, touch-sized target. */
        .brands__list { display:flex; flex-direction:column; gap:10px; margin-bottom:4px; }
        .brands__row, .cc__row {
          display:flex; align-items:center; gap:12px; min-height:58px; padding:12px 14px;
          background:rgba(255,255,255,.94); border:1px solid #e2e8f0; border-radius:14px;
          box-shadow:0 2px 8px rgba(15,23,42,.035); transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
        }
        .brands__row:hover, .cc__row:hover { transform:translateY(-1px); border-color:#cbd5e1; box-shadow:0 8px 20px rgba(15,23,42,.06); }
        .brands__name-area, .cc__name-area { flex:1; min-width:0; }
        .brands__name-line, .cc__name-line { display:flex; align-items:center; gap:8px; min-width:0; }
        .brands__name, .cc__name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#172033; font-size:13px; font-weight:700; }
        .brands__default-badge, .cc__disabled-badge { border-radius:999px; padding:3px 8px; font-size:10px; font-weight:800; letter-spacing:.045em; text-transform:uppercase; }
        .brands__default-badge { background:#eef2ff; color:#4f46e5; border:1px solid #c7d2fe; }
        .cc__disabled-badge { background:#f1f5f9; color:#64748b; border:1px solid #e2e8f0; }
        .brands__actions, .cc__actions { display:flex; align-items:center; gap:4px; flex-shrink:0; }
        .brands__icon-btn, .cc__icon-btn { display:inline-grid; place-items:center; width:32px; height:32px; padding:0; border:0; border-radius:9px; background:transparent; color:#64748b; cursor:pointer; transition:background .15s ease, color .15s ease, transform .1s ease; }
        .brands__icon-btn:hover, .cc__icon-btn:hover { background:#f1f5f9; color:#334155; }
        .brands__icon-btn--danger, .cc__icon-btn--danger { color:#ef4444; }
        .brands__icon-btn--danger:hover, .cc__icon-btn--danger:hover { background:#fef2f2; color:#dc2626; }
        .brands__inline-edit, .cc__inline-edit, .brands__add-form, .cc__add-form { display:flex; align-items:center; gap:8px; }
        .brands__field, .cc__field, .cc__select {
          min-height:36px; box-sizing:border-box; border:1px solid #cbd5e1; border-radius:10px; padding:7px 10px; outline:none;
          color:#172033; background:#fff; font:inherit; font-size:13px; transition:border-color .15s ease, box-shadow .15s ease;
        }
        .brands__field { flex:1; }
        .brands__field:focus { border-color:#6366f1; box-shadow:0 0 0 3px rgba(99,102,241,.14); }
        .cc__field:focus, .cc__select:focus { border-color:#0284c7; box-shadow:0 0 0 3px rgba(2,132,199,.14); }
        .brands__save-btn, .cc__save-btn { min-height:36px; border:0; border-radius:10px; padding:7px 13px; color:white; font:700 12px inherit; cursor:pointer; box-shadow:0 3px 10px rgba(15,23,42,.14); transition:transform .12s ease, filter .15s ease; }
        .brands__save-btn { background:#5b5ce2; } .cc__save-btn { background:#087eaa; }
        .brands__save-btn:hover, .cc__save-btn:hover { filter:brightness(1.05); }
        .brands__save-btn:disabled, .cc__save-btn:disabled { cursor:not-allowed; opacity:.5; box-shadow:none; }
        .brands__cancel-btn, .cc__cancel-btn, .cc__toggle-btn { min-height:36px; border:1px solid #dbe3ee; border-radius:10px; padding:7px 11px; background:#fff; color:#64748b; font:600 12px inherit; cursor:pointer; }
        .brands__add-trigger, .cc__add-trigger { display:flex; align-items:center; justify-content:center; gap:7px; min-height:42px; border:1.5px dashed #cbd5e1; border-radius:12px; background:rgba(255,255,255,.45); color:#64748b; font:700 13px inherit; cursor:pointer; transition:background .15s ease, border-color .15s ease, color .15s ease; }
        .brands__add-trigger:hover { color:#4f46e5; border-color:#a5b4fc; background:#f5f3ff; }
        .cc__add-trigger:hover { color:#0369a1; border-color:#7dd3fc; background:#f0f9ff; }
        .brands__hint { margin:4px 0 0; color:#94a3b8; font-size:12px; line-height:1.55; }

        /* Cost centers — the brand selector is intentionally adjacent to the
           list it changes, rather than reading like another global setting. */
        .cc__wrap { max-width:760px; }
        .cc__brand-select { display:flex; flex-direction:column; gap:7px; margin-bottom:2px; }
        .cc__brand-select label { color:#475569; font-size:12px; font-weight:750; }
        .cc__select { width:min(100%,320px); }
        .cc__list { display:flex; flex-direction:column; gap:8px; margin:2px 0 4px; }
        .cc__row--disabled { opacity:.6; background:#fafafa; }

        @media (max-width:720px) {
          .brands__ai-header, .cc__ai-header, .chg__ai-header, .cat__ai-header, .coa__ai-header, .tds__ai-header, .fx__ai-header { align-items:flex-start; flex-direction:column; }
          .brands__inline-edit, .cc__inline-edit, .brands__add-form, .cc__add-form { align-items:stretch; flex-wrap:wrap; }
          .brands__field, .cc__field { min-width:100%; }
          .brands__row, .cc__row { align-items:flex-start; }
        }
        @media (prefers-reduced-motion: reduce) {
          .brands__row, .cc__row, .brands__icon-btn, .cc__icon-btn, .brands__save-btn, .cc__save-btn { transition:none; transform:none !important; }
        }

        /* Admin-only locked state */
        .sp--locked {
          height: 100%; display: flex; flex-direction: column;
          align-items: center; justify-content: center; gap: 12px;
          background: #f9fafb; text-align: center; padding: 40px;
        }
        .sp__lock-icon {
          width: 72px; height: 72px; border-radius: 20px;
          background: #fef2f2; color: #dc2626;
          display: flex; align-items: center; justify-content: center;
          margin-bottom: 4px;
        }
        .sp__lock-title { font-size: 18px; font-weight: 700; color: #111827; margin: 0; }
        .sp__lock-sub   { font-size: 13px; color: #6b7280; margin: 0; line-height: 1.6; }

        /* Phones: the 220px section list beside the content left the editor
           ~150px. Stack them — sections become a swipeable chip row on top,
           and the whole page scrolls as one. */
        @media (width <= 768px) {
          .sp { flex-direction: column; overflow-y: auto; }
          .sp__sidebar {
            width: auto; padding: 12px 0 0; overflow: visible;
            border-right: none; border-bottom: 1px solid var(--line-200);
          }
          .sp__sidebar-title { padding: 0 16px 8px; margin-bottom: 0; border-bottom: none; }
          .sp__nav {
            flex-direction: row; gap: 6px; padding: 0 16px 12px;
            overflow-x: auto; scrollbar-width: none;
          }
          .sp__nav::-webkit-scrollbar { display: none; }
          .sp__nav-item {
            flex-shrink: 0; width: auto; white-space: nowrap;
            padding: 8px 12px; border: 1px solid var(--line-200); border-radius: 999px;
          }
          .sp__nav-chevron { display: none; }
          .sp__main { overflow: visible; }
          .sp__page-hdr { padding: 16px; }
          .sp__page-body { padding: 16px; overflow: visible; }
          .sp__role-row { flex-wrap: wrap; }
          .ce__nl-wrap { min-width: 100%; flex-direction: column; align-items: stretch; }
          .sp__perm-row { gap: 8px; }
        }
      `}</style>
    </>
  )
}
