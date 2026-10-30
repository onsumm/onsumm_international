import type { Meta, StoryObj } from '@storybook/react'
import { useState } from 'react'
import { Trash2, Plus, Check } from 'lucide-react'
import { Button, IconButton, Badge, Input, Select, Textarea, Checkbox } from './index'
import { Switch } from './Switch'
import { ConfirmDialog } from './ConfirmDialog'
import { EmptyState, ErrorState, Skeleton } from './States'
import { formatMoney } from './format'

const meta: Meta = { title: 'Foundations/Primitives', parameters: { layout: 'padded' } }
export default meta
type Story = StoryObj

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div style={{ marginBottom: 28 }}>
    <div
      style={{
        fontSize: 'var(--fs-2xs)',
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '.06em',
        color: 'var(--ink-400)',
        marginBottom: 10,
      }}
    >
      {label}
    </div>
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>{children}</div>
  </div>
)

export const Buttons: Story = {
  render: () => (
    <>
      <Row label="Variants">
        <Button variant="primary">Primary</Button>
        <Button variant="secondary">Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger">Danger</Button>
        <Button variant="success">Success</Button>
      </Row>
      <Row label="Sizes">
        <Button size="sm">Small</Button>
        <Button size="md">Medium</Button>
        <Button size="lg">Large</Button>
      </Row>
      <Row label="States">
        <Button variant="primary" loading>
          Saving
        </Button>
        <Button variant="primary" disabled>
          Disabled
        </Button>
        <Button variant="primary" iconLeft={<Plus size={14} />}>
          With icon
        </Button>
      </Row>
      <Row label="Icon buttons — accessible name is mandatory">
        <IconButton label="Add row" icon={<Plus size={15} />} />
        <IconButton label="Delete bill" icon={<Trash2 size={15} />} tone="danger" />
      </Row>
    </>
  ),
}

export const Badges: Story = {
  render: () => (
    <Row label="Tones — each passes AA on its own tint">
      <Badge tone="neutral">Draft</Badge>
      <Badge tone="info">Pending Accountant</Badge>
      <Badge tone="success" icon={<Check size={11} />}>
        Approved
      </Badge>
      <Badge tone="warn">Needs revision</Badge>
      <Badge tone="danger">Overdue</Badge>
      <Badge tone="brand">Cost centre</Badge>
    </Row>
  ),
}

export const Forms: Story = {
  render: function Forms() {
    const [on, setOn] = useState(true)
    return (
      <div style={{ display: 'grid', gap: 20, maxWidth: 420 }}>
        <Input label="Vendor name" placeholder="Acme Ltd" />
        <Input label="Vendor GSTIN" mono defaultValue="27AAPFU0939F1Z5" hint="15 characters" />
        <Input label="Vendor GSTIN" mono defaultValue="27AAPFU0939F1ZZ" error="Checksum does not match" />
        <Select
          label="Category"
          placeholder="Select a category"
          options={[
            { value: 'travel', label: 'Travel' },
            { value: 'software', label: 'Software' },
          ]}
        />
        <Textarea label="Reason for override" placeholder="Why is the invoice right and Nova wrong?" />
        <Checkbox label="Include disabled cost centres" />
        <Switch checked={on} onChange={setOn} label="Vendor Bill Agent" showLabel description="Watches flagged contacts" />
      </div>
    )
  },
}

export const Confirmation: Story = {
  render: function Confirmation() {
    const [open, setOpen] = useState(false)
    return (
      <>
        <Button variant="danger" onClick={() => setOpen(true)}>
          Delete 3 bills
        </Button>
        <ConfirmDialog
          open={open}
          onCancel={() => setOpen(false)}
          onConfirm={() => setOpen(false)}
          title="Delete 3 bills?"
          description="They move to the Deleted tab and stop appearing in reports. An admin can restore them."
          impact={[
            { label: 'Bills', value: '3' },
            { label: 'Total value', value: formatMoney(42_000_000) },
          ]}
          items={['Acme Ltd — INV-2291', 'Globex — INV-1180', 'Initech — INV-0042']}
          confirmLabel="Delete bills"
          requireTyped="DELETE"
        />
      </>
    )
  },
}

export const States: Story = {
  render: () => (
    <div style={{ display: 'grid', gap: 24 }}>
      <div style={{ border: '1px solid var(--line-200)', borderRadius: 12 }}>
        <EmptyState
          title="No bills yet"
          message="Upload an invoice or let the Vendor Bill Agent pick one up from a watched mailbox."
          action={<Button variant="primary">Upload invoice</Button>}
        />
      </div>
      <div style={{ border: '1px solid var(--line-200)', borderRadius: 12 }}>
        <ErrorState message="We could not reach the server. Your work is safe." onRetry={() => {}} />
      </div>
      <div style={{ display: 'grid', gap: 8, maxWidth: 320 }}>
        <Skeleton width="70%" />
        <Skeleton width="90%" />
        <Skeleton width="45%" />
      </div>
    </div>
  ),
}
