export type Role =
  | 'admin'
  | 'finance_controller'
  | 'accountant'
  | 'approver'
  | 'member'
  | 'auditor'

export type Permission =
  | 'payment.view'
  | 'payment.create'
  | 'payment.authorize'
  | 'payment.edit_threshold'
  | 'banking.read'
  | 'banking.manage'
  | 'team.manage_members'
  | 'team.edit_permissions'
  | 'team.edit_company_profile'
  | 'accounting.view'
  | 'accounting.edit'
  | 'payables.view'
  | 'payables.initiate'
  | 'payables.approve'
  | 'payables.accounting_approval'

const ALL: Permission[] = [
  'payment.view', 'payment.create', 'payment.authorize', 'payment.edit_threshold',
  'banking.read', 'banking.manage',
  'team.manage_members', 'team.edit_permissions', 'team.edit_company_profile',
  'accounting.view', 'accounting.edit',
  'payables.view', 'payables.initiate', 'payables.approve', 'payables.accounting_approval',
]

export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  admin: ALL,

  finance_controller: [
    'payment.view', 'payment.create',
    'banking.read',
    'accounting.view', 'accounting.edit',
    'payables.view', 'payables.initiate', 'payables.accounting_approval',
  ],

  accountant: [
    'banking.read',
    'accounting.view', 'accounting.edit',
    'payables.view', 'payables.initiate', 'payables.accounting_approval',
  ],

  approver: [
    'payables.initiate', 'payables.approve',
  ],

  member: [
    'payables.view',
    'payables.initiate',
  ],

  auditor: [
    'payment.view',
    'banking.read',
    'accounting.view',
    'payables.view',
  ],
}

export const ROLE_LABELS: Record<Role, string> = {
  admin:              'Admin',
  finance_controller: 'Finance Controller',
  accountant:         'Accountant',
  approver:           'Approver',
  member:             'Member',
  auditor:            'Auditor',
}

export function can(role: Role, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role].includes(permission)
}

// ── Permission categories for the Roles UI ───────────────────────
export const PERMISSION_CATEGORIES = [
  {
    label: 'Payment',
    items: [
      { permission: 'payment.view'           as Permission, label: 'View Payments and Transactions' },
      { permission: 'payment.create'         as Permission, label: 'Payment Creation' },
      { permission: 'payment.authorize'      as Permission, label: 'Payment Authorize' },
      { permission: 'payment.edit_threshold' as Permission, label: 'Edit Payment Threshold Settings' },
    ],
  },
  {
    label: 'Banking Product',
    items: [
      { permission: 'banking.read'   as Permission, label: 'Read / Download access to each bank account' },
      { permission: 'banking.manage' as Permission, label: 'Add / Close' },
    ],
  },
  {
    label: 'Team and Settings',
    items: [
      { permission: 'team.manage_members'      as Permission, label: 'Invite / Remove Team members' },
      { permission: 'team.edit_permissions'    as Permission, label: 'Edit Team Permissions' },
      { permission: 'team.edit_company_profile' as Permission, label: 'Edit Company Profile' },
    ],
  },
  {
    label: 'Accounting and Taxation',
    items: [
      { permission: 'accounting.view' as Permission, label: 'View Accounting and Taxes' },
      { permission: 'accounting.edit' as Permission, label: 'Edit / Add Entries' },
    ],
  },
  {
    label: 'Payables (Invoices / Expense)',
    items: [
      { permission: 'payables.view'               as Permission, label: 'View Payables (Invoices / Expenses / Bills)' },
      { permission: 'payables.initiate'            as Permission, label: 'Payable Initiate' },
      { permission: 'payables.approve'             as Permission, label: 'Payable Approve' },
      { permission: 'payables.accounting_approval' as Permission, label: 'Accounting Approval' },
    ],
  },
]

// Demo users used to be listed here — twelve accounts with plaintext passwords and
// two hardcoded tenant UUIDs, shipped in the browser bundle to every visitor.
// Nothing read them: /api/login is server-authoritative, and the accounts are
// seeded by backend_ocr/scripts/seed_users.py. See permissions.test.ts, which
// fails the build if credentials reappear anywhere under src/.
