/**
 * The Sovereign Ledger palette, shared between the landing pages and the
 * long-form content pages. It was previously a `const C` local to
 * LandingApp.tsx; the content pages need the same values and the two drifting
 * apart would be visible the moment a reader crosses from /product to an
 * article.
 */
export const FAQS_STYLE_TOKENS = {
  bg: '#f8f9ff',
  surface: '#ffffff',
  primary: '#002a58',
  primaryCont: '#004080',
  secondary: '#00696b',
  onSurface: '#0b1c30',
  onSurfaceVar: '#424750',
  outline: '#737781',
  outlineVar: '#c3c6d2',
  border: '#e2e8f0',
} as const
