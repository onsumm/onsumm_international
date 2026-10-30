/**
 * Drilled into one kind of finding: the title once, then where it happens.
 *
 * Fifty rows that each repeat "Vendor bank account has never been verified" is
 * the same duplication the grouped queue exists to remove, just moved one level
 * down. Once the user has drilled in, the *kind* is established — what they need
 * is the list of records, which is a table.
 *
 * First real consumer of the DataTable primitive, which already carries sorting,
 * aria-sort, row keys and a caption.
 */
import type { Finding } from '../../api/review'
import { Button, type Column, DataTable, formatMoneyCompact } from '../../ui'
import { OverdueBadge, SeverityBadge } from './FindingBadges'
import { ageLabel, dueLabel, primaryAction, rowMoney, severityDisplay } from './reviewFormat'

interface Props {
  findings: Finding[]
  onOpen: (id: string) => void
  onAct?: (finding: Finding) => void
}

/** The record a finding is about, in the user's words. */
function subjectLabel(f: Finding): string {
  const named = f.entities.find(e => e.label && e.role === 'subject')
    ?? f.entities.find(e => e.label)
  return named?.label ?? f.subject.id?.slice(0, 8) ?? '—'
}

export function FindingTable({ findings, onOpen, onAct }: Props) {
  const columns: Column<Finding>[] = [
    {
      key: 'subject',
      header: 'Record',
      pinned: true,
      render: f => <span className="rh__t-subject">{subjectLabel(f)}</span>,
      sortValue: f => subjectLabel(f).toLowerCase(),
    },
    {
      key: 'detail',
      // The kind is in the heading above; this column carries only what varies
      // between one record and the next.
      header: 'What',
      render: f => <span className="rh__t-detail">{f.detail}</span>,
      sortValue: f => f.detail,
    },
    {
      key: 'severity',
      header: 'Severity',
      render: f => <SeverityBadge sev={severityDisplay(f.severity)} />,
      sortValue: f => ({ CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 })[f.severity] ?? 9,
    },
    {
      key: 'money',
      header: 'At risk',
      align: 'right',
      render: f =>
        rowMoney(f) ? (
          <span className="tnum">{formatMoneyCompact(rowMoney(f))}</span>
        ) : null,
      sortValue: f => rowMoney(f) ?? 0,
    },
    {
      key: 'age',
      header: 'Age',
      render: f => {
        const due = dueLabel(f.due_at)
        return due ? <OverdueBadge label={due} /> : <span>{ageLabel(f.first_detected_at)}</span>
      },
      sortValue: f => f.first_detected_at,
    },
    {
      key: 'act',
      header: '',
      pinned: true,
      align: 'right',
      render: f => {
        const action = primaryAction(f)
        if (!action || !f.can_act || !onAct) return null
        return (
          <Button
            size="sm"
            variant="secondary"
            // The row itself opens the finding; the button does the fix. Without
            // this the click would do both.
            onClick={e => {
              e.stopPropagation()
              onAct(f)
            }}
          >
            {action.label}
          </Button>
        )
      },
    },
  ]

  return (
    <DataTable
      rows={findings}
      columns={columns}
      rowKey={f => f.finding_id}
      onRowClick={f => onOpen(f.finding_id)}
      density="compact"
      caption={`Every record with this problem (${findings.length})`}
    />
  )
}
