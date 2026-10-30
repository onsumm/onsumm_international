import { CheckCircle2, Upload, Database, Hash, FileText } from 'lucide-react'
import type { IngestResult } from '../api/invoices'

interface Props {
  result: IngestResult
  onNew: () => void
  onGoToBills: () => void
}

export default function DonePage({ result, onNew, onGoToBills }: Props) {
  return (
    <div className="done-page">
      <div className="done-card">
        <div className="done-icon"><CheckCircle2 size={44} /></div>
        <h2>Invoice Saved Successfully</h2>
        <p>Extracted, reviewed, and ingested into the database.</p>
        <div className="done-ids">
          <div className="done-id-row"><Upload size={13} /><span>Upload ID</span><code>{result.upload_id}</code></div>
          <div className="done-id-row"><Database size={13} /><span>Bill ID</span><code>{result.stg_bill_id}</code></div>
          <div className="done-id-row"><Hash size={13} /><span>Line Items</span><code>{result.line_items_count}</code></div>
        </div>
        <div className="done-btns">
          <button className="done-btn done-btn--secondary" onClick={onNew}>Process Another Invoice</button>
          <button className="done-btn done-btn--primary" onClick={onGoToBills}><FileText size={14} /> Go to Bills</button>
        </div>
      </div>

      <style>{`
        .done-page { display: flex; align-items: center; justify-content: center; flex: 1; padding: 40px; }
        .done-card { background: white; border-radius: var(--radius); border: 1px solid var(--gray-200); padding: 40px 32px; text-align: center; max-width: 440px; width: 100%; box-shadow: var(--shadow-md); display: flex; flex-direction: column; align-items: center; gap: 12px; }
        .done-icon { width: 72px; height: 72px; border-radius: 50%; background: var(--green-light); color: var(--green); display: flex; align-items: center; justify-content: center; }
        .done-card h2 { font-size: 20px; font-weight: 700; color: var(--gray-900); }
        .done-card p { font-size: 13px; color: var(--gray-500); }
        .done-ids { width: 100%; background: var(--gray-50); border-radius: var(--radius-sm); padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--gray-200); }
        .done-id-row { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--gray-600); }
        .done-id-row span { font-weight: 600; min-width: 72px; }
        .done-id-row code { font-family: monospace; font-size: 11px; color: var(--gray-700); word-break: break-all; }
        .done-btns { display: flex; gap: 10px; width: 100%; margin-top: 4px; }
        .done-btn { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 16px; border: none; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600; font-family: inherit; cursor: pointer; }
        .done-btn--primary  { background: var(--blue); color: white; }
        .done-btn--primary:hover { background: var(--blue-hover); }
        .done-btn--secondary { background: var(--gray-100); color: var(--gray-700); border: 1px solid var(--gray-200); }
        .done-btn--secondary:hover { background: var(--gray-200); }
      `}</style>
    </div>
  )
}
