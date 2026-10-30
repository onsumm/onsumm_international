---
name: Sovereign Ledger
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#424750'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#737781'
  outline-variant: '#c3c6d2'
  surface-tint: '#305ea0'
  primary: '#002a58'
  on-primary: '#ffffff'
  primary-container: '#004080'
  on-primary-container: '#83aef5'
  inverse-primary: '#a9c7ff'
  secondary: '#00696b'
  on-secondary: '#ffffff'
  secondary-container: '#79f2f4'
  on-secondary-container: '#006e6f'
  tertiary: '#2b2b2b'
  on-tertiary: '#ffffff'
  tertiary-container: '#414141'
  on-tertiary-container: '#afadac'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d6e3ff'
  primary-fixed-dim: '#a9c7ff'
  on-primary-fixed: '#001b3d'
  on-primary-fixed-variant: '#0e4686'
  secondary-fixed: '#7cf5f7'
  secondary-fixed-dim: '#5cd8da'
  on-secondary-fixed: '#002020'
  on-secondary-fixed-variant: '#004f50'
  tertiary-fixed: '#e5e2e1'
  tertiary-fixed-dim: '#c8c6c5'
  on-tertiary-fixed: '#1c1b1b'
  on-tertiary-fixed-variant: '#474746'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
  success-emerald: '#10B981'
  warning-amber: '#F59E0B'
  error-crimson: '#DC2626'
  background-surface: '#F8FAFC'
  invoice-blue: '#004080'
typography:
  display-lg:
    fontFamily: Public Sans
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Public Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
  headline-md:
    fontFamily: Public Sans
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-sm:
    fontFamily: Public Sans
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  mono-data:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  container-max: 1440px
  gutter: 24px
---

## Brand & Style
The design system is engineered for the high-stakes environment of B2B finance automation. It prioritizes clarity, speed of cognition, and absolute reliability. The visual language follows a **Corporate / Modern** aesthetic, leaning into a structured, data-first approach that mirrors the efficiency required by modern finance teams.

The style is characterized by ample white space, a disciplined color application, and a total absence of unnecessary ornamentation. Every pixel serves a functional purpose, ensuring that CFOs and accountants can navigate complex financial workflows without cognitive fatigue. The result is an interface that feels stable, authoritative, and enterprise-grade.

## Colors
The palette is rooted in "Deep Professional Blue" to establish institutional trust. 

- **Primary:** A deep, authoritative blue (#004080) used for primary actions and core navigation elements.
- **Secondary:** An energetic teal (#00A4A6) inspired by the brand's origins, used sparingly for highlights and secondary interactive elements.
- **Success/Warning:** Standardized functional colors for financial compliance. Emerald represents successful approvals, while Amber is strictly reserved for compliance flags and pending audits.
- **Neutral:** A range of cool grays derived from Slate to maintain a "crisp" and "sanitized" environment for heavy data entry.

## Typography
The typography system uses **Public Sans** for headings to provide a clean, institutional feel, and **Inter** for all body and UI elements due to its exceptional legibility in data-heavy contexts.

- **Tabular Figures:** Always enable "tabular numbers" (tnum) for financial data tables to ensure columns of figures align vertically.
- **Hierarchy:** Use FontWeight 600 for headers to create clear separation from body text. 
- **Scale:** For mobile, `headline-lg` should scale down to 24px to maintain readability within smaller viewport widths.

## Layout & Spacing
The layout follows a **Fixed Grid** model for desktop to ensure financial dashboards remain legible and consistent across various monitor sizes. 

- **Grid:** A 12-column grid with 24px gutters. 
- **Rhythm:** An 8pt linear scaling system for all padding and margins. 
- **Data Density:** In complex accounting views, use "Compact" spacing (12px padding) to maximize information density without sacrificing touch targets.
- **Breakpoints:** 
  - Mobile: < 600px (4 columns, 16px margins)
  - Tablet: 600px - 1024px (8 columns, 24px margins)
  - Desktop: > 1024px (12 columns, 24px margins, max-width 1440px)

## Elevation & Depth
Depth is conveyed primarily through **Tonal Layers** and extremely subtle **Ambient Shadows**. 

- **Surface Tiers:** The main background uses a very light gray (#F8FAFC). Cards and primary containers use pure white (#FFFFFF) to "pop" from the background.
- **Shadows:** Use a single, soft shadow style for floating elements like modals and dropdowns: `0px 4px 20px rgba(0, 0, 0, 0.05)`. 
- **Interactions:** Hover states on cards should not lift the element but rather apply a subtle border tint in the primary blue color to maintain a flat, professional profile.

## Shapes
The design system utilizes **Soft** roundedness (4px radius). This provides a modern touch while maintaining the structural integrity and "seriousness" of a financial tool. 

- **Standard Elements:** Buttons, Inputs, and Cards use a 4px radius.
- **Large Elements:** Modals and large dashboard containers use 8px (`rounded-lg`).
- **Tags/Status Chips:** Use 4px to match buttons; avoid pill shapes to keep the UI looking architectural rather than consumer-social.

## Components
- **Buttons:** Solid primary blue for main actions. Secondary buttons use a 1px border of the primary color with a transparent background. 
- **Data Tables:** The core of the product. Use a "Zebra-stripe" alternative only on hover. Headers must be sticky with a subtle bottom border. High-contrast text for values, neutral-slate for labels.
- **Inputs:** Use 1px borders (#CBD5E1). Focus states should use a 2px primary blue outline with no "glow" to maintain a crisp look.
- **Status Chips:** Rectangular with 4px radius. Use low-saturation background tints with high-saturation text (e.g., light green background with dark green text for "Approved").
- **Cards:** No shadows. Use a subtle 1px gray border (#E2E8F0) to define boundaries.
- **Iconography:** Use "Stroke" icons with a 1.5pt weight. Avoid filled icons unless used for active states in navigation.