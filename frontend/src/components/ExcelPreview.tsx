import { useState, useEffect, useRef, useCallback } from 'react'
import * as XLSX from 'xlsx'

interface SheetData {
  name: string
  headers: string[]
  rows: (string | number | boolean | null)[][]
}

export interface CellDiff {
  sheet: string
  row: number   // 1-based data row
  col: number   // 0-based
  header: string
  oldValue: string
  newValue: string
}

interface Props {
  url?: string
  blobData?: ArrayBuffer
  editable?: boolean
  onSave?: (file: File, diff: CellDiff[]) => Promise<void>
}

export function ExcelPreview({ url, blobData, editable = false, onSave }: Props) {
  const [sheets, setSheets] = useState<SheetData[]>([])
  const [activeSheet, setActiveSheet] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // edits: key = "sheetIdx:row:col" → { oldValue, newValue }
  const [edits, setEdits] = useState<Map<string, { old: string; new: string }>>(new Map())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const originalBuf = useRef<ArrayBuffer | null>(null)
  const originalSheets = useRef<SheetData[]>([])

  useEffect(() => {
    let cancelled = false
    const parse = (buf: ArrayBuffer) => {
      try {
        originalBuf.current = buf
        const wb = XLSX.read(buf, { type: 'array' })
        const parsed: SheetData[] = wb.SheetNames.map(name => {
          const ws = wb.Sheets[name]
          const rows: (string | number | boolean | null)[][] = XLSX.utils.sheet_to_json(ws, { header: 1, defval: null }) as (string | number | boolean | null)[][]
          const headers = (rows[0] ?? []).map(h => h == null ? '' : String(h))
          return { name, headers, rows: rows.slice(1) }
        })
        if (!cancelled) {
          setSheets(parsed)
          originalSheets.current = parsed
          setEdits(new Map())
          setLoading(false)
        }
      } catch {
        if (!cancelled) { setError('Could not parse spreadsheet.'); setLoading(false) }
      }
    }
    if (blobData) {
      parse(blobData)
    } else if (url) {
      fetch(url)
        .then(r => r.arrayBuffer())
        .then(buf => { if (!cancelled) parse(buf) })
        .catch(() => { if (!cancelled) { setError('Failed to load file.'); setLoading(false) } })
    }
    return () => { cancelled = true }
  }, [url, blobData])

  const getCellValue = useCallback((si: number, ri: number, ci: number): string => {
    const key = `${si}:${ri}:${ci}`
    if (edits.has(key)) return edits.get(key)!.new
    const v = sheets[si]?.rows[ri]?.[ci]
    return v == null ? '' : String(v)
  }, [sheets, edits])

  const handleCellChange = useCallback((si: number, ri: number, ci: number, newVal: string) => {
    const key = `${si}:${ri}:${ci}`
    const orig = (() => {
      const v = originalSheets.current[si]?.rows[ri]?.[ci]
      return v == null ? '' : String(v)
    })()
    setEdits(prev => {
      const next = new Map(prev)
      if (newVal === orig) {
        next.delete(key)
      } else {
        next.set(key, { old: orig, new: newVal })
      }
      return next
    })
  }, [])

  const handleSave = async () => {
    if (!onSave || !originalBuf.current) return
    setSaving(true)
    setSaveMsg(null)
    try {
      // Apply all edits onto a fresh workbook
      const wb = XLSX.read(originalBuf.current, { type: 'array' })
      const diff: CellDiff[] = []

      edits.forEach(({ old: oldValue, new: newValue }, key) => {
        const [si, ri, ci] = key.split(':').map(Number)
        const sheetName = sheets[si].name
        const ws = wb.Sheets[sheetName]
        // row in sheet: +2 because row 0 = headers, row ri = data row ri, sheet row = ri+2 (1-based + header)
        const cellAddr = XLSX.utils.encode_cell({ r: ri + 1, c: ci })
        const existing = ws[cellAddr]
        ws[cellAddr] = { ...(existing ?? {}), v: newValue, t: 's', w: newValue }
        diff.push({ sheet: sheetName, row: ri + 1, col: ci, header: sheets[si].headers[ci] ?? `Col ${ci + 1}`, oldValue, newValue })
      })

      const outBuf: ArrayBuffer = XLSX.write(wb, { type: 'array', bookType: 'xlsx' })
      const file = new File([outBuf], 'document.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      await onSave(file, diff)

      // After save: update originalSheets so edits become baseline
      originalBuf.current = outBuf
      setSheets(prev => {
        const next = prev.map((s, si) => ({
          ...s,
          rows: s.rows.map((row, ri) => row.map((cell, ci) => {
            const key = `${si}:${ri}:${ci}`
            return edits.has(key) ? edits.get(key)!.new : cell
          }))
        }))
        originalSheets.current = next
        return next
      })
      setEdits(new Map())
      setSaveMsg('Saved successfully')
      setTimeout(() => setSaveMsg(null), 3000)
    } catch {
      setSaveMsg('Save failed. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#9ca3af', fontSize: 13, gap: 8 }}>
      <span style={{ display: 'inline-block', width: 16, height: 16, border: '2px solid #e5e7eb', borderTopColor: '#6366f1', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
      Loading spreadsheet…
    </div>
  )

  if (error) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#ef4444', fontSize: 13 }}>{error}</div>
  )

  if (!sheets.length) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#9ca3af', fontSize: 13 }}>Empty spreadsheet</div>
  )

  const sheet = sheets[activeSheet]
  const isDirty = edits.size > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      {/* Toolbar */}
      {editable && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 12px', background: '#f9fafb', borderBottom: '1px solid #e5e7eb', flexShrink: 0, gap: 12 }}>
          <span style={{ fontSize: 12, color: isDirty ? '#b45309' : '#9ca3af' }}>
            {isDirty ? `${edits.size} unsaved change${edits.size > 1 ? 's' : ''}` : 'Click any cell to edit'}
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {saveMsg && (
              <span style={{ fontSize: 12, color: saveMsg.startsWith('Saved') ? '#16a34a' : '#ef4444' }}>{saveMsg}</span>
            )}
            {isDirty && (
              <button
                onClick={() => { setEdits(new Map()); setSaveMsg(null) }}
                disabled={saving}
                style={{ fontSize: 12, padding: '4px 10px', border: '1px solid #e5e7eb', borderRadius: 6, background: '#fff', color: '#6b7280', cursor: 'pointer', fontFamily: 'inherit' }}
              >Discard</button>
            )}
            <button
              onClick={handleSave}
              disabled={!isDirty || saving}
              style={{
                fontSize: 12, padding: '4px 14px', border: 'none', borderRadius: 6,
                background: isDirty && !saving ? '#1d4ed8' : '#e5e7eb',
                color: isDirty && !saving ? '#fff' : '#9ca3af',
                cursor: isDirty && !saving ? 'pointer' : 'default',
                fontFamily: 'inherit', fontWeight: 600,
                transition: 'background 0.15s',
              }}
            >{saving ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      )}

      {/* Sheet tabs */}
      {sheets.length > 1 && (
        <div style={{ display: 'flex', gap: 2, padding: '6px 12px 0', background: '#f9fafb', borderBottom: '1px solid #e5e7eb', flexShrink: 0, overflowX: 'auto' }}>
          {sheets.map((s, i) => (
            <button
              key={i}
              onClick={() => setActiveSheet(i)}
              style={{
                padding: '5px 14px', fontSize: 12, fontWeight: i === activeSheet ? 600 : 400,
                borderRadius: '6px 6px 0 0', border: '1px solid #e5e7eb',
                borderBottom: i === activeSheet ? '1px solid #fff' : '1px solid #e5e7eb',
                background: i === activeSheet ? '#fff' : '#f3f4f6',
                color: i === activeSheet ? '#1e40af' : '#6b7280',
                cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap',
                marginBottom: i === activeSheet ? -1 : 0,
              }}
            >{s.name}</button>
          ))}
        </div>
      )}

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto', background: '#fff' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
          {sheet.headers.length > 0 && (
            <thead>
              <tr>
                <th style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', padding: '6px 10px', color: '#6b7280', fontSize: 11, fontWeight: 600, textAlign: 'center', minWidth: 36 }}>#</th>
                {sheet.headers.map((h, i) => (
                  <th key={i} style={{ background: '#1e3a5f', border: '1px solid #2d4a6f', padding: '7px 12px', color: '#fff', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap', minWidth: 80 }}>{h || `Col ${i + 1}`}</th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {sheet.rows.map((_, ri) => (
              <tr key={ri} style={{ background: ri % 2 === 0 ? '#fff' : '#f8fafc' }}>
                <td style={{ border: '1px solid #e5e7eb', padding: '5px 8px', color: '#9ca3af', fontSize: 10, textAlign: 'center', userSelect: 'none' }}>{ri + 1}</td>
                {sheet.headers.map((__, ci) => {
                  const key = `${activeSheet}:${ri}:${ci}`
                  const isEdited = edits.has(key)
                  const val = getCellValue(activeSheet, ri, ci)
                  return (
                    <td
                      key={ci}
                      contentEditable={editable}
                      suppressContentEditableWarning
                      onBlur={editable ? e => handleCellChange(activeSheet, ri, ci, e.currentTarget.textContent ?? '') : undefined}
                      style={{
                        border: '1px solid #e5e7eb',
                        padding: '5px 12px',
                        color: '#374151',
                        whiteSpace: 'nowrap',
                        maxWidth: 240,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        background: isEdited ? '#fefce8' : undefined,
                        outline: editable ? 'none' : undefined,
                        cursor: editable ? 'text' : undefined,
                      }}
                      onFocus={editable ? e => {
                        e.currentTarget.style.background = '#eff6ff'
                        e.currentTarget.style.boxShadow = 'inset 0 0 0 2px #3b82f6'
                      } : undefined}
                      onBlurCapture={editable ? e => {
                        const k = `${activeSheet}:${ri}:${ci}`
                        e.currentTarget.style.background = edits.has(k) ? '#fefce8' : ''
                        e.currentTarget.style.boxShadow = ''
                      } : undefined}
                    >{val}</td>
                  )
                })}
              </tr>
            ))}
            {sheet.rows.length === 0 && (
              <tr><td colSpan={sheet.headers.length + 1} style={{ padding: 24, textAlign: 'center', color: '#9ca3af' }}>No data in this sheet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
