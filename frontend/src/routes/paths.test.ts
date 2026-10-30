import { describe, it, expect } from 'vitest'
import {
  APP_NAME,
  PATHS,
  UPLOAD_REVIEW_PATH,
  billPath,
  findingPath,
  focusPath,
  isAppPath,
  isKnownRoute,
  pageForPath,
  payPath,
  reviewPath,
  settingsPath,
  titleForPath,
  vendorPath,
} from './paths'

describe('route registry', () => {
  it('gives every navigable page a unique path', () => {
    const paths = Object.values(PATHS)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('maps a path back to the nav item that should look active', () => {
    expect(pageForPath(PATHS.bills)).toBe('bills')
    expect(pageForPath(PATHS.settings)).toBe('settings')
    expect(pageForPath('/nope')).toBeNull()
  })

  it('keeps the parent nav item active on nested routes', () => {
    expect(pageForPath(UPLOAD_REVIEW_PATH)).toBe('upload')
    expect(pageForPath('/bills/abc-123')).toBe('bills')
  })

  it('does not treat a path that merely shares a prefix as a match', () => {
    // "/billsomething" must not activate "/bills".
    expect(pageForPath('/billsomething')).toBeNull()
    expect(isAppPath('/billsomething')).toBe(false)
  })

  it('separates app routes from marketing routes', () => {
    expect(isAppPath(PATHS.bills)).toBe(true)
    expect(isAppPath('/bills/abc-123')).toBe(true)
    expect(isAppPath('/')).toBe(false)
    expect(isAppPath('/pricing')).toBe(false)
  })
})

describe('replaying a remembered deep link', () => {
  it('accepts routes that are actually registered', () => {
    expect(isKnownRoute(PATHS.bills)).toBe(true)
    expect(isKnownRoute(UPLOAD_REVIEW_PATH)).toBe(true)
    expect(isKnownRoute(`${PATHS.bills}?status=PENDING`)).toBe(true)
  })

  it('accepts the payment flow and a settings section', () => {
    expect(isKnownRoute('/bills/pay/abc-123')).toBe(true)
    expect(isKnownRoute('/settings/tds')).toBe(true)
    expect(isKnownRoute('/settings/invented')).toBe(false)
  })

  it('accepts a record deep link, so signing in returns to the bill or vendor', () => {
    expect(isKnownRoute('/bills/abc-123')).toBe(true)
    expect(isKnownRoute('/vendors/abc-123')).toBe(true)
    expect(isKnownRoute('/vendors/abc-123/contacts')).toBe(true)
    expect(isKnownRoute('/vendors/abc-123?q=x')).toBe(true)
  })

  it('rejects app-shaped paths that have no route, so the redirect cannot loop', () => {
    // Remembered while signed out (isAppPath) but with no <Route> to land on —
    // replaying one would fall through to the catch-all and redirect to itself
    // forever.
    expect(isAppPath('/bills/abc-123/history')).toBe(true)
    expect(isKnownRoute('/bills/abc-123/history')).toBe(false)
    // A tab that does not exist must not match the vendor pattern either.
    expect(isKnownRoute('/vendors/abc-123/invented')).toBe(false)
  })

  it('rejects arbitrary paths', () => {
    expect(isKnownRoute('/')).toBe(false)
    expect(isKnownRoute('/nope')).toBe(false)
  })
})

describe('document titles', () => {
  it('gives each page a distinct, branded title', () => {
    expect(titleForPath(PATHS.bills)).toBe(`Bills · ${APP_NAME}`)
    expect(titleForPath(PATHS.settings)).toBe(`Settings · ${APP_NAME}`)
    expect(titleForPath(PATHS.bills)).not.toBe(titleForPath(PATHS.settings))
  })

  it('falls back to the bare app name for unknown paths', () => {
    expect(titleForPath('/nope')).toBe(APP_NAME)
  })

  it('inherits the parent title on nested routes', () => {
    expect(titleForPath('/bills/abc-123')).toBe(`Bills · ${APP_NAME}`)
  })
})

describe('link builders', () => {
  it('pins a bill on the payment flow with a query param', () => {
    expect(payPath('sup-1')).toBe('/bills/pay/sup-1')
    expect(payPath('sup-1', 'inv-9')).toBe('/bills/pay/sup-1?bill=inv-9')
    expect(payPath('sup-1', 'inv-9', 5664)).toBe('/bills/pay/sup-1?bill=inv-9&amount=5664')
    // An amount only means something for a pinned bill.
    expect(payPath('sup-1', undefined, 5664)).toBe('/bills/pay/sup-1')
  })

  it('leaves the default tab and section out of the URL', () => {
    expect(vendorPath('v1')).toBe('/vendors/v1')
    expect(vendorPath('v1', 'details')).toBe('/vendors/v1')
    expect(vendorPath('v1', 'contacts')).toBe('/vendors/v1/contacts')
    expect(settingsPath()).toBe('/settings')
    expect(settingsPath('approval-chain')).toBe('/settings')
    expect(settingsPath('tds')).toBe('/settings/tds')
  })

  it('keeps the payment flow and a bill at different depths', () => {
    // Both live under /bills, so both light up the Bills nav item, but one is a
    // record and the other is a flow — they never collide because the flow
    // always carries the /pay/ segment.
    expect(isKnownRoute(payPath('sup-1'))).toBe(true)
    expect(isKnownRoute(billPath('inv-1'))).toBe(true)
    expect(pageForPath(payPath('sup-1'))).toBe('bills')
    expect(pageForPath(billPath('inv-1'))).toBe('bills')
    // A two-segment path that is neither is still rejected.
    expect(isKnownRoute('/bills/pay/sup-1/extra')).toBe(false)
  })
})

describe('review hub', () => {
  it('gives the hub an address and nested views', () => {
    expect(reviewPath()).toBe('/review')
    expect(reviewPath('queue')).toBe('/review')       // default view stays out of the URL
    expect(reviewPath('snoozed')).toBe('/review/snoozed')
    expect(isKnownRoute('/review')).toBe(true)
    expect(isKnownRoute('/review/snoozed')).toBe(true)
    expect(isKnownRoute('/review/resolved')).toBe(true)
    expect(isKnownRoute('/review/invented')).toBe(false)
    expect(isKnownRoute('/review/resolved/extra')).toBe(false)
  })

  it('keeps an emailed finding link replayable after sign-in', () => {
    // An approver clicks a link in an alert email while signed out and must land
    // on that exact finding once they log in.
    expect(isAppPath('/review?f=fnd_9a31')).toBe(true)
    expect(isKnownRoute(findingPath('fnd_9a31'))).toBe(true)
    expect(pageForPath('/review/resolved')).toBe('review')
  })

  it('builds a deep link that highlights the field at the root', () => {
    expect(focusPath(billPath('abc'), 'vendor.gstin', 'fnd_1'))
      .toBe('/bills/abc?focus=vendor.gstin&from=review%3Afnd_1')
  })

  it('preserves a query string the target already had', () => {
    // payPath() carries ?bill=, and a finding about a payment deep-links to it.
    expect(focusPath('/bills/pay/sup1?bill=b1', 'bank_account'))
      .toBe('/bills/pay/sup1?bill=b1&focus=bank_account')
  })

  it('gives the hub its own tab title', () => {
    expect(titleForPath('/review')).toContain('Review Hub')
    expect(titleForPath('/review/resolved')).toContain('Review Hub')
  })
})
