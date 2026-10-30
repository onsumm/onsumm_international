import { useState } from 'react'
import { Download, FileSpreadsheet, FileText, Archive, Table2 } from 'lucide-react'
import { downloadExpenseReport, downloadExpenseSummaryCSV } from '../api/invoices'
import { useAuth } from '../auth/AuthContext'

export default function DataExtractionPage() {
  const { user, activeBrand } = useAuth()
  const [allTime, setAllTime] = useState(false)
  const [reportMonth, setReportMonth] = useState(() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  })
  const [reportStatus, setReportStatus] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [downloadingCsv, setDownloadingCsv] = useState(false)
  const [lastDownloaded, setLastDownloaded] = useState<string | null>(null)

  const effectiveMonth = allTime ? undefined : reportMonth

  async function handleDownload() {
    setDownloading(true)
    try {
      await downloadExpenseReport(effectiveMonth, user?.company_id, reportStatus || undefined, activeBrand?.id)
      setLastDownloaded(`Excel report — ${allTime ? 'All time' : reportMonth}`)
    } catch (e) {
      console.error(e)
    } finally {
      setDownloading(false)
    }
  }

  async function handleDownloadCsv(report: 'summary' | 'raw' | 'both' = 'summary') {
    setDownloadingCsv(true)
    try {
      await downloadExpenseSummaryCSV(effectiveMonth, user?.company_id, reportStatus || undefined, report, activeBrand?.id)
      setLastDownloaded(`${report === 'both' ? 'Both CSVs (ZIP)' : report === 'summary' ? 'Summary CSV' : 'Raw CSV'} — ${allTime ? 'All time' : reportMonth}`)
    } catch (e) {
      console.error(e)
    } finally {
      setDownloadingCsv(false)
    }
  }

  const busy = downloading || downloadingCsv

  const EXPORTS = [
    {
      icon: FileSpreadsheet,
      title: 'Excel Report',
      desc: 'Full expense data with COA ledger mapping, formatted for accounting review.',
      color: '#16a34a',
      bg: '#f0fdf4',
      border: '#a7f3d0',
      action: () => handleDownload(),
      loading: downloading,
      label: downloading ? 'Generating…' : 'Download Excel',
    },
    {
      icon: Table2,
      title: 'Summary CSV',
      desc: 'Aggregated spend by category and ledger account — easy to pivot in Excel.',
      color: '#2563eb',
      bg: '#eff6ff',
      border: '#bfdbfe',
      action: () => handleDownloadCsv('summary'),
      loading: downloadingCsv,
      label: downloadingCsv ? 'Generating…' : 'Download CSV',
    },
    {
      icon: FileText,
      title: 'Raw CSV',
      desc: 'One row per bill with all fields — invoice date, vendor, amount, status, TDS.',
      color: '#d97706',
      bg: '#fffbeb',
      border: '#fde68a',
      action: () => handleDownloadCsv('raw'),
      loading: downloadingCsv,
      label: downloadingCsv ? 'Generating…' : 'Download Raw',
    },
    {
      icon: Archive,
      title: 'Both CSVs (ZIP)',
      desc: 'Summary and raw CSV bundled into a single ZIP archive.',
      color: '#7c3aed',
      bg: '#f5f3ff',
      border: '#ddd6fe',
      action: () => handleDownloadCsv('both'),
      loading: downloadingCsv,
      label: downloadingCsv ? 'Generating…' : 'Download ZIP',
    },
  ]

  return (
    <div className="de">
      {/* Header */}
      <div className="de__header">
        <div>
          <h1 className="de__title">Data Extraction</h1>
          <p className="de__sub">Export your bills and expense data in multiple formats</p>
        </div>
      </div>

      {/* Filters */}
      <div className="de__filters">
        <div className="de__filter-group">
          <div className="de__filter-label">Month</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="month"
              value={reportMonth}
              onChange={e => setReportMonth(e.target.value)}
              className="de__input"
              disabled={allTime}
              style={{ opacity: allTime ? 0.4 : 1 }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12,
              color: '#374151', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              <input
                type="checkbox"
                checked={allTime}
                onChange={e => setAllTime(e.target.checked)}
                style={{ cursor: 'pointer' }}
              />
              All time
            </label>
          </div>
        </div>
        <div className="de__filter-group">
          <div className="de__filter-label">Status Filter</div>
          <select value={reportStatus} onChange={e => setReportStatus(e.target.value)} className="de__input">
            <option value="">All statuses</option>
            <option value="APPROVED">Approved only</option>
          </select>
        </div>
        {lastDownloaded && (
          <div className="de__last">
            <span style={{ color: '#059669', fontSize: 12, fontWeight: 600 }}>✓</span>
            <span style={{ fontSize: 12, color: '#6b7280' }}>Last: {lastDownloaded}</span>
          </div>
        )}
      </div>

      {/* Export cards */}
      <div className="de__grid">
        {EXPORTS.map(ex => (
          <div key={ex.title} className="de__card" style={{ borderTop: `3px solid ${ex.color}` }}>
            <div className="de__card-head">
              <div style={{ width: 40, height: 40, borderRadius: 10, background: ex.bg, border: `1px solid ${ex.border}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <ex.icon size={20} color={ex.color} />
              </div>
              <div>
                <div className="de__card-title">{ex.title}</div>
              </div>
            </div>
            <div className="de__card-desc">{ex.desc}</div>
            <button
              onClick={ex.action}
              disabled={busy}
              className="de__btn"
              style={{ background: ex.loading ? '#9ca3af' : ex.color, opacity: busy && !ex.loading ? 0.5 : 1 }}
            >
              <Download size={14} />
              {ex.label}
            </button>
          </div>
        ))}
      </div>

      <style>{`
        .de {
          height: 100%; display: flex; flex-direction: column;
          padding: 28px 32px 24px; background: var(--canvas); overflow-y: auto;
        }
        .de__header {
          display: flex; align-items: flex-end; justify-content: space-between; margin-bottom: 24px;
        }
        .de__title { font-size: var(--fs-2xl); font-weight: var(--fw-bold); color: var(--ink-900); margin: 0 0 2px; letter-spacing: -0.02em; }
        .de__sub   { font-size: var(--fs-sm); color: var(--ink-500); margin: 0; }

        .de__filters {
          display: flex; align-items: flex-end; gap: 16px; flex-wrap: wrap;
          background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
          padding: 16px 20px; margin-bottom: 24px;
        }
        .de__filter-group { display: flex; flex-direction: column; gap: 4px; }
        .de__filter-label { font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; }
        .de__input {
          padding: 7px 10px; border: 1px solid #e5e7eb; border-radius: 7px;
          font-size: 13px; font-family: inherit; color: #374151; outline: none; background: #fff;
        }
        .de__last {
          display: flex; align-items: center; gap: 6px; padding: 7px 12px;
          background: #f0fdf4; border: 1px solid #a7f3d0; border-radius: 8px;
          margin-top: 14px;
        }

        .de__grid {
          display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;
        }
        @media (max-width: 1100px) { .de__grid { grid-template-columns: repeat(2, 1fr); } }
        @media (max-width: 560px) { .de__grid { grid-template-columns: 1fr; } }

        .de__card {
          background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px;
          display: flex; flex-direction: column; gap: 12px;
        }
        .de__card-head { display: flex; align-items: center; gap: 12px; }
        .de__card-title { font-size: 15px; font-weight: 700; color: #111827; }
        .de__card-desc { font-size: 12.5px; color: #6b7280; line-height: 1.5; flex: 1; }
        .de__btn {
          display: flex; align-items: center; justify-content: center; gap: 6px;
          padding: 9px 0; border: none; border-radius: 8px;
          color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit;
          transition: opacity 0.15s;
        }
        .de__btn:disabled { cursor: not-allowed; }
        .de__btn:not(:disabled):hover { filter: brightness(1.08); }
      `}</style>
    </div>
  )
}
