"""The one shape every detector emits, whatever produced it.

A deterministic adapter reading ai_anomalies and the LLM review agent both
construct a FindingDraft and hand it to store.upsert_finding(). That single
module boundary is what stops the two detection philosophies from drifting into
two half-implemented stores with different lifecycles.

Pure: no DB, no network. Safe to import from the FastAPI app.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# The closed spine. finding_type is open-ended on purpose — the agent mints new
# ones at runtime — but every finding must map to one of these eight families,
# because lanes, badge counts and the nav badge all key off them and must not
# grow a "misc" bucket the first time an agent invents something.
FAMILIES: tuple[str, ...] = (
    "approval", "payment", "vendor_identity", "tax",
    "reconciliation", "data_quality", "duplication", "fx",
)

SUBJECT_TYPES: tuple[str, ...] = (
    "bill", "invoice", "vendor", "payment", "disbursement",
    "upload", "line_item", "gstin", "bank_account",
    "recon_run", "agent_run", "tenant",
)

SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

VERIFICATIONS: tuple[str, ...] = ("VERIFIED", "UNVERIFIABLE", "UNVERIFIED", "REFUTED")

CAPABILITIES: tuple[str, ...] = ("view", "edit", "approve", "verify", "pay", "admin")


def one_line(text: str | None, limit: int = 160) -> str:
    """First sentence of someone else's prose.

    Descriptions on `ai_anomalies` and recommendations on `fx_rate_alerts` are
    written elsewhere in the platform and run to a paragraph. Truncating to the
    first sentence is the honest way to shorten text we do not own — the full
    version still travels in the evidence payload, where it can be read in full
    by anyone who opens the finding.
    """
    if not text:
        return ""
    flat = " ".join(str(text).split())
    for stop in (". ", "; ", " — "):
        head, sep, _ = flat.partition(stop)
        if sep and len(head) >= 40:
            flat = head + ("." if stop.startswith(".") else "")
            break
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0] + "…"


def jsonb(value, default=None):
    """Decode a jsonb column.

    This pool registers no jsonb codec, so asyncpg hands back **text** where
    psycopg2 hands back a list or dict. That difference has now caused three
    separate production bugs — `.get()` on a string, and worst,
    `", ".join(assignees)` iterating an assignee list character by character,
    which made every approval step look unassigned and produced a page of false
    CRITICAL findings. Read every jsonb column through here.
    """
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def condition_hash(*parts: object) -> str:
    """Stable hash of the inputs that define "this problem, in this state".

    Used for two things: deciding whether a re-detection is the same problem
    unchanged (leave the user's acknowledgement alone) or the same problem with
    new facts (re-open it and bump occurrence_count). Keep the inputs to values
    a human would consider part of the problem — not timestamps, not row
    versions, or every pass looks like a change.
    """
    payload = json.dumps(
        [None if p is None else str(p) for p in parts],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class FindingDraft:
    tenant_id: str
    finding_type: str
    family: str
    subject_type: str
    subject_id: str | None
    severity: str
    title: str
    deep_link: str
    condition_hash: str
    detector: str

    detail: str = ""
    # (entity_type, entity_id, role, label)
    entities: tuple[tuple[str, str, str, str | None], ...] = ()
    confidence: float = 1.0
    verification: str = "VERIFIED"
    money_at_risk: Decimal | None = None
    money_currency: str = "INR"
    money_at_risk_inr: Decimal | None = None
    due_at: datetime | None = None
    evidence: dict = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    audience_roles: tuple[str, ...] = ()
    audience_users: tuple[str, ...] = ()
    audience_departments: tuple[str, ...] = ()
    required_capability: str = "view"
    source_service: str = "bills"
    detector_version: str = "v1"
    agent_run_id: str | None = None
    model: str | None = None
    cost_usd: Decimal | None = None
    type_id: str | None = None
    # Set when one subject can carry more than one finding of the same type —
    # e.g. three unmatched line items on one bill.
    key_qualifier: str | None = None

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown family {self.family!r}")
        if self.subject_type not in SUBJECT_TYPES:
            raise ValueError(f"unknown subject_type {self.subject_type!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.verification not in VERIFICATIONS:
            raise ValueError(f"unknown verification {self.verification!r}")
        if self.required_capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {self.required_capability!r}")

    @property
    def finding_key(self) -> str:
        parts = [self.finding_type, self.subject_type, self.subject_id or "-"]
        if self.key_qualifier:
            parts.append(self.key_qualifier)
        return ":".join(parts)


# Evidence payloads declare a *shape*, never a problem type. The frontend
# dispatches on this and falls back to a generic renderer, which is what lets a
# finding type invented at runtime still render well.
EVIDENCE_KINDS: tuple[str, ...] = (
    "diff", "code_diff", "table", "timeline", "fields", "text", "proposals",
)


def code_diff_evidence(
    *,
    left_label: str,
    left_value: str,
    right_label: str,
    right_value: str,
    left_status: str | None = None,
    right_status: str | None = None,
    note: str = "",
    facts: list[tuple[str, str, str]] | None = None,
    document: dict | None = None,
) -> dict:
    """Two codes compared character by character.

    For a misread identifier, the useful thing is not a sentence saying four
    characters differ — it is *seeing* which four. The renderer computes the
    differing positions itself and marks them, so this payload stays a plain
    before/after and the highlighting cannot drift out of sync with the values.

    `status` is 'verified' | 'failed' | None and drives the badge beside each.
    """
    out = {
        "kind": "code_diff",
        "left": {"label": left_label, "value": left_value, "status": left_status},
        "right": {"label": right_label, "value": right_value, "status": right_status},
        "note": note,
        "facts": [{"label": l, "left": a, "right": b} for l, a, b in (facts or [])],
    }
    if document:
        out["document_ref"] = document
    return out


def diff_evidence(
    left_label: str,
    right_label: str,
    rows: list[tuple[str, str, str]],
    *,
    document: dict | None = None,
) -> dict:
    """rows: (field label, left value, right value).

    `document` attaches the source page so the drawer can show the invoice next
    to what was extracted from it. For anything that turns on "did we read this
    right?", being able to look is the difference between a finding a user can
    action and one they have to go somewhere else to judge.
    """
    out = {
        "kind": "diff",
        "left_label": left_label,
        "right_label": right_label,
        "rows": [{"label": lbl, "left": l, "right": r} for lbl, l, r in rows],
    }
    if document:
        out["document_ref"] = document
    return out


def document_ref(
    upload_id: str | None,
    *,
    label: str | None = None,
    page: int | None = None,
    highlight: str | None = None,
) -> dict | None:
    """Points at a stored upload, served by GET /api/docs/{upload_id}.

    `highlight` is the string worth finding on the page — the drawer shows it
    beside the preview so the eye knows what it is checking.
    """
    if not upload_id:
        return None
    ref = {"upload_id": str(upload_id)}
    if label:
        ref["label"] = label
    if page:
        ref["page"] = page
    if highlight:
        ref["highlight"] = highlight
    return ref


def table_evidence(columns: list[str], rows: list[list], note: str = "",
                   *, document: dict | None = None) -> dict:
    out = {"kind": "table", "columns": columns, "rows": rows, "note": note}
    if document:
        out["document_ref"] = document
    return out


def timeline_evidence(steps: list[dict], note: str = "") -> dict:
    """steps: [{label, at, actor, state: 'done'|'current'|'pending'|'blocked'}]"""
    return {"kind": "timeline", "steps": steps, "note": note}


def fields_evidence(pairs: list[tuple[str, object]], note: str = "",
                    *, document: dict | None = None) -> dict:
    out = {"kind": "fields",
           "fields": [{"label": k, "value": v} for k, v in pairs], "note": note}
    if document:
        out["document_ref"] = document
    return out


def text_evidence(body: str, bullets: list[str] | None = None) -> dict:
    return {"kind": "text", "body": body, "bullets": bullets or []}



def proposals_evidence(proposals: list[dict], note: str = "",
                       *, document: dict | None = None) -> dict:
    """Several proposed corrections to one record, each independently accepted.

    The other evidence kinds describe a problem; this one carries the fix. Each
    proposal is {id, group, label, current, proposed, why, writable}:

      · `current` is struck through and `proposed` offered in its place, so the
        reader sees what would change rather than a field that has silently
        become something else.
      · `writable` false means we can say what is wrong and cannot put it
        right — the row is shown without a tick. A vendor's own GST split is
        the case that matters: recording IGST here does not make the credit
        claimable, only a reissued invoice does.
      · Nothing is applied until the user says so, and they may take some and
        leave others. The finding closes when nothing is left to accept.
    """
    out = {"kind": "proposals", "proposals": proposals, "note": note}
    if document:
        out["document_ref"] = document
    return out

def field(key: str, label: str, *, kind: str = "text", value: object = None,
          placeholder: str = "", pattern: str = "", hint: str = "") -> dict:
    """One input on an executable action.

    An action with fields is a correction, not just a trigger: the user is
    fixing the thing the finding is about — a mistyped IFSC, say — and then
    running the operation against what they fixed. `pattern` is a plain regex
    the drawer applies before it will submit, so a malformed value is caught
    where it was typed rather than by a 422 from a provider.
    """
    return {"key": key, "label": label, "kind": kind, "value": value,
            "placeholder": placeholder, "pattern": pattern, "hint": hint}


def action(
    action_id: str,
    label: str,
    *,
    kind: str = "navigate",
    endpoint: str | None = None,
    method: str = "POST",
    fields: list[dict] | None = None,
    preview: str = "",
    capability: str = "edit",
    destructive: bool = False,
    href: str | None = None,
    body: dict | None = None,
    confirm: dict | None = None,
    success: dict | None = None,
    queued: str = "",
    emphasis: str = "primary",
) -> dict:
    """Build one entry for actions_jsonb.

    Two things an executable action must declare, because HTTP 200 is not an
    outcome. `success` is the field/value pairs the response must carry for the
    problem to be gone — /verify-bank answers 200 with persisted=false when the
    bank said yes but we could not store it, and treating that as fixed removes
    a finding that is still true. `queued` is the message for an action that
    only *starts* something: /gstins/primary/reverify resets the vendor and
    notifies the GST worker, so its 200 means "asked", not "verified".

    kind='safe'     executed by the hub via POST /act — reversible, <=3 fields,
                    and always by calling the endpoint that already owns the
                    operation, never by writing a second copy of its logic.
    kind='approve'  an approval decision, executed the same way but always
                    behind a confirmation naming the vendor and the amount.
                    Built server-side at read time by
                    backend_ocr/app/domain/review/bill_actions.py, which offers
                    it only to callers the approvals endpoint would accept.
    kind='navigate' rendered as a deep link. Everything that moves money is
                    this, so the user lands on the screen that already has the
                    OTP flow, the confirmations and the audit trail.
    kind='notify'   Executed by the hub via POST /api/review/act/notify — the
                    same principle as 'safe' (call the logic that already owns
                    the operation), pointed at the one endpoint whose job is
                    sending a message, because no vendor/user router owns that
                    already. The *effect* is a message to a person (an in-app
                    notification or a vendor email) rather than a data write,
                    so unlike 'safe' it always requires `confirm` naming the
                    destination and a preview of the message. Logs an
                    `agent_runs` row (agent_name='communication') for audit,
                    same as any other LLM- or agent-driven step.
    """
    return {
        "id": action_id,
        "label": label,
        "kind": kind,
        "endpoint": endpoint,
        "method": method,
        "fields": fields or [],
        "preview": preview,
        "capability": capability,
        "destructive": destructive,
        "href": href,
        "body": body or {},
        # What the user is asked to agree to before an executable action runs.
        # None means the action needs no confirmation.
        "confirm": confirm,
        # How to read the response. None means a 2xx is the whole answer.
        "success": success,
        # Non-empty when the action only queues work; the hub then reports it as
        # started rather than done, and leaves the finding in the queue.
        "queued": queued,
        # Which of several executable actions is the expected one. When a
        # finding offers a real either/or — keep the contract's value vs keep
        # the one on file — rendering both as equally weighted buttons leaves
        # the user guessing which is the change and which is the status quo.
        # 'secondary' renders muted; the default stays 'primary'.
        "emphasis": emphasis,
    }
