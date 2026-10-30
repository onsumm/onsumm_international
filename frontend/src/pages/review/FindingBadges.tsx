/**
 * The small set of badges every finding card and drawer header share.
 *
 * Pulled out of FindingCard/FindingDrawer/FindingTable because the design brief
 * treats these as one visual vocabulary, not three ad-hoc implementations: the
 * same severity badge, the same verified/suspected distinction, wherever a
 * finding is shown. Severity and confidence are never colour alone here — every
 * badge below carries an icon and a word, so the distinction survives
 * greyscale, a colour-blind viewer, and a quick glance from across a desk.
 */
import { AlertOctagon, AlertTriangle, CheckCircle2, Clock, Info, Lock, Sparkles } from 'lucide-react'

import { Badge, type BadgeTone } from '../../ui'
import type { SeverityDisplay, SeverityTone } from './reviewFormat'

const SEVERITY_ICON: Record<SeverityTone, typeof AlertOctagon> = {
  blocking: AlertOctagon,
  high: AlertTriangle,
  review: Info,
}

const SEVERITY_BADGE_TONE: Record<SeverityTone, BadgeTone> = {
  blocking: 'danger',
  high: 'warn',
  review: 'info',
}

/** Severity: icon + word, three levels only. Never colour alone. */
export function SeverityBadge({ sev }: { sev: SeverityDisplay }) {
  const Icon = SEVERITY_ICON[sev.tone]
  return (
    <Badge tone={SEVERITY_BADGE_TONE[sev.tone]} icon={<Icon size={12} />}>
      {sev.label}
    </Badge>
  )
}

/**
 * Three pips, filled by how confident the agent says it is. Only shown next to
 * a Suspected badge — a verified finding is a fact, not a probability.
 */
export function ConfidencePips({ confidence }: { confidence: number }) {
  const filled = confidence >= 0.66 ? 3 : confidence >= 0.33 ? 2 : 1
  return (
    <span className="rh__pips" role="img" aria-label={`Agent confidence: ${filled} of 3`}>
      {[0, 1, 2].map(i => (
        <span key={i} className={`rh__pip${i < filled ? ' rh__pip--on' : ''}`} aria-hidden="true" />
      ))}
    </span>
  )
}

/**
 * The make-or-break distinction, per the brief: a deterministic finding styled
 * identically to an AI guess destroys trust in both. `✓ Verified` for a fact the
 * system checked itself; `◈ Suspected` (a quiet neutral badge, never the same
 * colour as a passing check) plus a confidence meter for a guess a human must
 * judge.
 */
export function ConfidenceBadge({ suspected, confidence }: { suspected: boolean; confidence: number }) {
  if (!suspected) {
    return (
      <Badge tone="success" icon={<CheckCircle2 size={12} />}>
        Verified
      </Badge>
    )
  }
  return (
    <span className="rh__confidence">
      <Badge tone="neutral" icon={<Sparkles size={12} />}>
        Suspected
      </Badge>
      <ConfidencePips confidence={confidence} />
    </span>
  )
}

/** Past its due date, urgency becomes a badge rather than just coloured text. */
export function OverdueBadge({ label }: { label: string }) {
  return (
    <Badge tone="warn" icon={<Clock size={12} />}>
      {label}
    </Badge>
  )
}

/**
 * "Can see but cannot fix." Never hidden: an approver who can see a GST problem
 * they cannot personally correct still has to know about it before they
 * approve the bill next to it.
 */
export function LockChip({ capability }: { capability: string }) {
  return (
    <Badge tone="neutral" icon={<Lock size={12} />}>
      Needs {capability}
    </Badge>
  )
}
