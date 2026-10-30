import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { Switch } from './Switch'
import { Modal } from './Modal'
import { ConfirmDialog } from './ConfirmDialog'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { Input } from './Field'
import { DataTable, type Column } from './DataTable'
import { formatMoney, formatMoneyCompact, formatRelativeDays, humanise } from './format'

describe('Switch', () => {
  it('is a real switch a keyboard user can operate', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [on, setOn] = useState(false)
      return <Switch checked={on} onChange={setOn} label="Vendor Bill Agent" />
    }
    render(<Harness />)

    const sw = screen.getByRole('switch', { name: 'Vendor Bill Agent' })
    expect(sw).toHaveAttribute('aria-checked', 'false')

    // The whole point: reachable by Tab and toggled by the keyboard. The old
    // <span onClick> could do neither.
    await user.tab()
    expect(sw).toHaveFocus()
    await user.keyboard(' ')
    expect(sw).toHaveAttribute('aria-checked', 'true')
    await user.keyboard('{Enter}')
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })

  it('does not fire when disabled', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Switch checked={false} onChange={onChange} label="Agent" disabled />)
    await user.click(screen.getByRole('switch'))
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('Modal', () => {
  it('exposes itself as a named dialog', () => {
    render(
      <Modal open onClose={() => {}} title="Bill detail" description="Acme Ltd">
        <p>body</p>
      </Modal>,
    )
    const dialog = screen.getByRole('dialog', { name: 'Bill detail' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByText('Acme Ltd')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Modal open onClose={onClose} title="Bill detail">
        <p>body</p>
      </Modal>,
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('traps Tab inside and restores focus to the opener on close', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          <Modal open={open} onClose={() => setOpen(false)} title="Trapped">
            <button>Inside A</button>
            <button>Inside B</button>
          </Modal>
        </>
      )
    }
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Open' })
    await user.click(opener)

    // Cycling forward past the last control wraps to the first, never escaping
    // to the page behind.
    const inside = ['Close dialog', 'Inside A', 'Inside B']
    for (let i = 0; i < 6; i++) {
      const active = document.activeElement as HTMLElement
      expect(inside).toContain(active.getAttribute('aria-label') ?? active.textContent)
      await user.tab()
    }

    await user.keyboard('{Escape}')
    expect(opener).toHaveFocus()
  })
})

describe('ConfirmDialog', () => {
  it('shows what is affected and how much is at stake', () => {
    render(
      <ConfirmDialog
        open
        onCancel={() => {}}
        onConfirm={() => {}}
        title="Delete 3 bills?"
        description="They move to the Deleted tab."
        impact={[
          { label: 'Bills', value: '3' },
          { label: 'Total value', value: '₹4,20,00,000' },
        ]}
        items={['Acme Ltd — INV-1', 'Globex — INV-2', 'Initech — INV-3']}
      />,
    )
    // The native confirm() this replaces could show none of this.
    expect(screen.getByText('₹4,20,00,000')).toBeInTheDocument()
    expect(screen.getByText('Acme Ltd — INV-1')).toBeInTheDocument()
  })

  it('keeps the destructive action locked until the confirmation word is typed', async () => {
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(
      <ConfirmDialog
        open
        onCancel={() => {}}
        onConfirm={onConfirm}
        title="Delete bills?"
        description="This cannot be undone."
        requireTyped="DELETE"
        confirmLabel="Delete"
      />,
    )
    const confirm = screen.getByRole('button', { name: 'Delete' })
    expect(confirm).toBeDisabled()

    await user.type(screen.getByLabelText('Type DELETE to confirm'), 'DELETE')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})

describe('Button / IconButton', () => {
  it('defaults to type=button so it cannot submit a surrounding form', () => {
    const onSubmit = vi.fn(e => e.preventDefault())
    render(
      <form onSubmit={onSubmit}>
        <Button>Just a button</Button>
      </form>,
    )
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('blocks interaction while loading', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(
      <Button loading onClick={onClick}>
        Save
      </Button>,
    )
    const btn = screen.getByRole('button')
    expect(btn).toHaveAttribute('aria-busy', 'true')
    await user.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('forces an accessible name on icon-only controls', () => {
    render(<IconButton label="Discard draft" icon={<svg />} />)
    expect(screen.getByRole('button', { name: 'Discard draft' })).toBeInTheDocument()
  })
})

describe('Input', () => {
  it('associates its label and announces errors', () => {
    render(<Input label="Vendor GSTIN" error="Checksum does not match" />)
    const input = screen.getByLabelText('Vendor GSTIN')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('Checksum does not match')
    // The error is wired to the control, so a screen reader reads it on focus.
    expect(input.getAttribute('aria-describedby')).toBeTruthy()
  })
})

interface Row {
  id: string
  vendor: string
  amount: number | null
}
const ROWS: Row[] = [
  { id: '1', vendor: 'Globex', amount: 500 },
  { id: '2', vendor: 'Acme', amount: 12_000 },
  { id: '3', vendor: 'Initech', amount: null },
]
const COLS: Column<Row>[] = [
  { key: 'vendor', header: 'Vendor', render: r => r.vendor, sortValue: r => r.vendor },
  { key: 'amount', header: 'Amount', render: r => r.amount ?? '—', sortValue: r => r.amount, align: 'right' },
]

describe('DataTable', () => {
  const vendors = () =>
    screen
      .getAllByRole('row')
      .slice(1)
      .map(r => within(r).getAllByRole('cell')[0].textContent)

  it('sorts a column and reports the state via aria-sort', async () => {
    const user = userEvent.setup()
    render(<DataTable rows={ROWS} columns={COLS} rowKey={r => r.id} caption="Bills" />)

    await user.click(screen.getByRole('button', { name: /Vendor/ }))
    expect(vendors()).toEqual(['Acme', 'Globex', 'Initech'])
    expect(screen.getByRole('columnheader', { name: /Vendor/ })).toHaveAttribute('aria-sort', 'ascending')

    await user.click(screen.getByRole('button', { name: /Vendor/ }))
    expect(vendors()).toEqual(['Initech', 'Globex', 'Acme'])
    expect(screen.getByRole('columnheader', { name: /Vendor/ })).toHaveAttribute('aria-sort', 'descending')

    // Third activation clears back to natural order.
    await user.click(screen.getByRole('button', { name: /Vendor/ }))
    expect(vendors()).toEqual(['Globex', 'Acme', 'Initech'])
  })

  it('sorts empty values last in both directions', async () => {
    const user = userEvent.setup()
    render(<DataTable rows={ROWS} columns={COLS} rowKey={r => r.id} caption="Bills" />)
    const amount = screen.getByRole('button', { name: /Amount/ })

    await user.click(amount)
    expect(vendors()).toEqual(['Globex', 'Acme', 'Initech']) // 500, 12000, null

    await user.click(amount)
    // Descending must NOT promote the null to the top — an unknown amount is not
    // the largest amount.
    expect(vendors()).toEqual(['Acme', 'Globex', 'Initech'])
  })

  it('does not mutate the rows array it is given', () => {
    const rows = [...ROWS]
    const snapshot = rows.map(r => r.id)
    render(<DataTable rows={rows} columns={COLS} rowKey={r => r.id} caption="Bills" sort={{ key: 'vendor', dir: 'asc' }} />)
    expect(rows.map(r => r.id)).toEqual(snapshot)
  })

  it('supports selection with a working select-all', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [sel, setSel] = useState<Set<string>>(new Set())
      return (
        <>
          <span data-testid="count">{sel.size}</span>
          <DataTable
            rows={ROWS}
            columns={COLS}
            rowKey={r => r.id}
            caption="Bills"
            selectedIds={sel}
            onSelectionChange={setSel}
          />
        </>
      )
    }
    render(<Harness />)
    await user.click(screen.getByRole('checkbox', { name: 'Select all rows' }))
    expect(screen.getByTestId('count')).toHaveTextContent('3')
  })
})

describe('formatters', () => {
  it('formats INR without decimals by default and with them when precise', () => {
    expect(formatMoney(1234567)).toBe('₹12,34,567')
    expect(formatMoney(1234.5, { precise: true })).toBe('₹1,234.50')
    expect(formatMoney(null)).toBe('—')
  })

  it('uses Indian crore/lakh units for compact display', () => {
    expect(formatMoneyCompact(45_000_000)).toBe('₹4.5Cr')
    expect(formatMoneyCompact(340_000)).toBe('₹3.4L')
    expect(formatMoneyCompact(56_000)).toBe('₹56K')
    expect(formatMoneyCompact(-45_000_000)).toBe('-₹4.5Cr')
  })

  it('describes dates relative to a fixed anchor', () => {
    const now = new Date(2026, 7, 2)
    expect(formatRelativeDays(new Date(2026, 7, 2), now)).toBe('today')
    expect(formatRelativeDays(new Date(2026, 7, 5), now)).toBe('in 3 days')
    expect(formatRelativeDays(new Date(2026, 6, 30), now)).toBe('3 days ago')
  })

  it('humanises status keys', () => {
    expect(humanise('PENDING_ACCOUNTANT')).toBe('Pending Accountant')
    expect(humanise(null)).toBe('—')
  })
})
