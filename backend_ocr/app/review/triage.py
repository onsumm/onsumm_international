"""Decide whether an entity is worth a model call. Pure and deterministic.

Worth being explicit about what this optimises, because it is not what people
assume: **triage protects attention, not the budget.**

The budget is genuinely not the binding constraint. A dossier is roughly 8k in
and 2k out, which on the house model is about a quarter of a cent, so full
coverage of 10,000 bills a month lands near $25 for a tenant. That is
affordable. What is *not* affordable is precision: an open-ended "what is wrong
here?" over a correct bill still returns something, because the question
presupposes a defect. Every unnecessary call is a chance to manufacture a
plausible non-issue, and two of those in a row is how a hub loses its audience.

So the gate is about picking entities where a model has something real to add.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

DEFAULT_MATERIALITY_INR = Decimal("25000")
DEFAULT_EXPLORATION_PCT = 3


@dataclass(frozen=True)
class Subject:
    tenant_id: str
    subject_type: str
    subject_id: str | None
    dossier_hash: str
    amount_inr: Decimal | None = None
    #: a deterministic adapter already fired on this subject
    has_existing_finding: bool = False
    #: how many prior bills exist for this vendor — baselines need history
    history_count: int = 0


@dataclass(frozen=True)
class Decision:
    run: bool
    tier: str
    reason: str


def _exploration_bucket(subject_id: str | None) -> int:
    """Stable per subject, so the same entity is always in or always out.

    Random sampling would make precision measurement noisy and would re-roll the
    dice every pass, which is both more expensive and less informative.
    """
    if not subject_id:
        return 100
    digest = hashlib.sha256(subject_id.encode()).hexdigest()
    return int(digest[:8], 16) % 100


def should_review(
    s: Subject,
    *,
    materiality_inr: Decimal | None = None,
    exploration_pct: int = DEFAULT_EXPLORATION_PCT,
    last_dossier_hash: str | None = None,
) -> Decision:
    # Nothing about the entity a model would reason over has changed, so a second
    # call would produce the same answer at the same price. This single check is
    # what removes the 5–15x multiplier from re-reviewing a bill at every state
    # change on its way through the chain.
    if last_dossier_hash and last_dossier_hash == s.dossier_hash:
        return Decision(False, "standard", "unchanged since the last review")

    floor = materiality_inr if materiality_inr is not None else DEFAULT_MATERIALITY_INR

    # Highest yield per token: a deterministic check has already found something,
    # so the model is explaining and expanding rather than searching blind.
    if s.has_existing_finding:
        return Decision(True, "standard", "a deterministic check already fired here")

    # The exploration slice is the mechanism that actually discovers problem
    # classes nobody wrote a rule for. It ignores materiality on purpose —
    # restricting discovery to large bills would only ever find the problems that
    # large bills have.
    if _exploration_bucket(s.subject_id) < exploration_pct:
        return Decision(True, "standard", f"exploration slice ({exploration_pct}%)")

    if s.amount_inr is not None and s.amount_inr >= floor:
        tier = "deep" if s.amount_inr >= floor * 20 else "standard"
        return Decision(True, tier, f"material (>= {floor})")

    return Decision(False, "cheap", "below the materiality floor and nothing else flagged")


def idempotency_key(
    tenant_id: str, subject_type: str, subject_id: str | None,
    dossier_hash: str, prompt_version: str, model: str,
) -> str:
    """The same entity, in the same state, reviewed by the same prompt and model,
    must never be paid for twice — across restarts and duplicated NOTIFYs."""
    raw = f"{tenant_id}|{subject_type}|{subject_id or '-'}|{dossier_hash}|{prompt_version}|{model}"
    return hashlib.sha256(raw.encode()).hexdigest()


def baseline_is_usable(history_count: int, minimum: int = 6) -> bool:
    """Whether "unusual for this vendor" is a question that can be answered.

    Below this, any claim about a norm is unverifiable by construction and must
    be demoted rather than published as a comparison. A brand-new tenant's hub is
    adapter-only for its first weeks, and that is the correct behaviour, not a gap.
    """
    return history_count >= minimum
