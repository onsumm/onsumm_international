/**
 * The `?` shortcut sheet.
 *
 * A finance controller clearing a queue every morning for years is worth a
 * keyboard, and a keyboard nobody can discover is worth nothing — so every key
 * below is listed here rather than left to be found by accident.
 */
import { Modal } from '../../ui'

const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: 'j / k', does: 'Move to the next / previous finding' },
  { keys: 'o', does: 'Open the focused finding' },
  { keys: 'e', does: 'Run its primary action' },
  { keys: 's', does: 'Snooze it a week' },
  { keys: 'd', does: 'Dismiss it (asks why)' },
  { keys: 'x', does: 'Select it for a bulk action' },
  { keys: 'u', does: 'Undo the last resolution' },
  { keys: '?', does: 'Show this sheet' },
]

export function ShortcutsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Keyboard shortcuts" size="sm">
      <dl className="rh__kbd-list">
        {SHORTCUTS.map(s => (
          <div key={s.keys} className="rh__kbd-row">
            <dt>
              {s.keys.split(' / ').map((k, i) => (
                <span key={k}>
                  {i > 0 && ' / '}
                  <kbd className="rh__kbd">{k}</kbd>
                </span>
              ))}
            </dt>
            <dd>{s.does}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  )
}
