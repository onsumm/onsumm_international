/**
 * One action's confirmation, wherever that action is offered from.
 *
 * The drawer used to own this markup. Once the queue row started offering the
 * primary action directly (see reviewFormat.ts::primaryAction), the row needed
 * the identical dialog — same title, same impact list, same before → after
 * line — or "approve from the queue" would have been a quieter, less informed
 * version of "approve from the drawer" rather than the same decision reached
 * sooner. One component, three callers.
 *
 * Actions carrying `fields` are deliberately not handled here: those need the
 * values typed before there is anything to agree to, so a surface that has
 * nowhere to put a form opens the drawer instead.
 */
import type { FindingAction } from '../../api/review'
import { ConfirmDialog } from '../../ui'

interface Props {
  action: FindingAction
  onCancel: () => void
  onConfirm: (action: FindingAction) => void
}

export function ActionConfirm({ action, onCancel, onConfirm }: Props) {
  return (
    <ConfirmDialog
      open
      onCancel={onCancel}
      onConfirm={() => onConfirm(action)}
      title={action.confirm?.title || action.label}
      description={action.preview}
      impact={action.confirm?.impact}
      items={action.confirm?.items}
      confirmLabel={action.label}
      requireTyped={action.confirm?.requireTyped}
      tone={action.destructive ? 'danger' : 'primary'}
    >
      {action.confirm?.change && (
        <div className="rh__change">
          <span className="rh__change-label">{action.confirm.change.label}</span>
          <span className="rh__change-codes">
            <span className="rh__change-from">{action.confirm.change.from}</span>
            <span className="rh__change-arrow" aria-hidden="true">→</span>
            <span className="rh__change-to">{action.confirm.change.to}</span>
          </span>
        </div>
      )}
    </ConfirmDialog>
  )
}
