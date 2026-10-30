import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { can, ROLE_PERMISSIONS, type Role } from './permissions'

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap(entry => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return walk(full)
    return /\.(ts|tsx)$/.test(entry) ? [full] : []
  })
}

/**
 * Regression guards, in the same shape as the "no bearer tokens in URLs" walk in
 * api/authedFiles.test.ts.
 *
 * This file used to export DEMO_USERS: twelve accounts with plaintext passwords
 * and two hardcoded tenant UUIDs, compiled into the bundle served to every
 * visitor. Nothing imported it, so it survived unnoticed. These tests fail the
 * build if either pattern comes back.
 */
describe('no credentials or tenant ids in the shipped bundle', () => {
  const files = walk(SRC).filter(f => !f.endsWith('permissions.test.ts'))

  it('has no plaintext password literal anywhere in src/', () => {
    const offenders = files.filter(f =>
      /password:\s*['"][^'"]+['"]/.test(readFileSync(f, 'utf8')),
    )
    expect(offenders).toEqual([])
  })

  it('has no hardcoded tenant UUIDs', () => {
    // Tenant ids belong in the database and the JWT, never in source. A literal
    // here is how per-customer behaviour gets baked into the client.
    const uuid = /['"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['"]/i
    const offenders = files.filter(f => {
      const text = readFileSync(f, 'utf8')
      // Test fixtures may legitimately use ids; only non-test sources are policed.
      return !/\.test\.tsx?$/.test(f) && uuid.test(text)
    })
    expect(offenders).toEqual([])
  })
})

describe('role permissions', () => {
  it('does not let a member authorize payments', () => {
    expect(can('member', 'payment.authorize')).toBe(false)
    expect(can('auditor', 'payment.authorize')).toBe(false)
  })

  it('gives admin every permission', () => {
    const everything = new Set(Object.values(ROLE_PERMISSIONS).flat())
    for (const p of everything) expect(can('admin', p)).toBe(true)
  })

  it('defines permissions for every role', () => {
    const roles: Role[] = ['admin', 'finance_controller', 'accountant', 'approver', 'member', 'auditor']
    for (const r of roles) expect(ROLE_PERMISSIONS[r]).toBeDefined()
  })
})
