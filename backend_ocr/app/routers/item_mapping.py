"""Line-item to catalogue matching, and the SAP catalogue connection.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import re
import secrets
import datetime
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

router = APIRouter()

from ..core.tenant_settings import _sap_matching_enabled

_SAP_LLM_MODEL = "gpt-4o-mini"     # mapping engine

_SAP_CATALOG_CACHE: dict = {}      # tenant -> (loaded_ts, bundle)

_SAP_VENDOR_CACHE: dict = {}       # tenant -> (loaded_ts, [rows]) — item→supplier map

_SAP_CACHE_TTL = 600.0

_SAP_PREFILTER_K = 30              # candidates fed to the LLM per line item

_SAP_TYPE_MISMATCH_CAP = 0.30     # max confidence when the product type doesn't match

_SAP_TYPE_MIN_SIM = 0.45          # min token-set similarity to treat as the same product type

# Domain abbreviations expanded on BOTH query and catalog so fuzzy token matching
# aligns vendor shorthand with the SAP master.
_SAP_ABBREV = {
    "ss": "stainless steel", "ms": "mild steel", "ci": "cast iron",
    "gi": "galvanized iron", "cs": "carbon steel", "ss304": "stainless steel 304",
    "nb": "nominal bore", "od": "outer diameter", "id": "inner diameter",
    "sch": "schedule", "thk": "thickness", "thick": "thickness", "dia": "diameter",
    "pcs": "pieces", "pc": "piece", "nos": "number", "galv": "galvanized",
    "sst": "stainless steel",
}

_SAP_INCH_RE = re.compile(r'(?<=\d)\s*["”″]')           # 16" / 2 ″ -> inch (only after a digit)

_SAP_DOTABBR_RE = re.compile(r"(?<=[a-z])\.(?=[a-z])")  # c.i -> ci

_SAP_NUM_RE = re.compile(r"\d+(?:\.\d+)?")              # numeric spec tokens

_SAP_MIXED_FRAC_RE = re.compile(r"(\d+)[.\-\s](\d+)\s*/\s*(\d+)")   # 1.1/2, 1-1/2, 1 1/2

_SAP_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")                      # 1/2, 3/4

def _sap_defrac(text: str) -> str:
    """Convert inch fractions to decimals so sizes compare numerically:
    '1.1/2' / '1-1/2' / '1 1/2' → '1.5'; '1/2' → '0.5'; '3/4' → '0.75'."""
    s = text or ""

    def _mixed(m):
        w, n, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{w + n / d:g}" if d else m.group(0)

    def _frac(m):
        n, d = int(m.group(1)), int(m.group(2))
        return f"{n / d:g}" if d else m.group(0)

    s = _SAP_MIXED_FRAC_RE.sub(_mixed, s)
    return _SAP_FRAC_RE.sub(_frac, s)

def _sap_norm(text: str) -> str:
    """Normalize for lexical matching: lower-case, convert inch fractions to
    decimals, expand inch marks (only a quote right after a digit) and dotted/known
    abbreviations, drop other punctuation, keep numeric specs."""
    s = _sap_defrac((text or "").lower())
    s = _SAP_INCH_RE.sub(" inch ", s)
    s = _SAP_DOTABBR_RE.sub("", s)
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    return " ".join(_SAP_ABBREV.get(t, t) for t in s.split()).strip()

_SAP_RATING_RE = re.compile(
    r"\b(?:class|cl|sch|schedule|pn|grade|gr|no)\s*[-#.]?\s*\d+[a-z]?\b|#\s*\d+", re.I)

def _sap_numbers(text: str) -> set:
    """Set of DIMENSION spec tokens (as floats) — 40, 10.0, 1.5, 72…  Inch fractions
    (1.1/2 → 1.5) are converted first; pressure/class/schedule ratings (Class 10,
    SCH 40, #150, PN 16) are stripped so a rating number is never mistaken for a
    size and never blocks an otherwise-exact dimension match."""
    cleaned = _SAP_RATING_RE.sub(" ", _sap_defrac(text or ""))
    return {float(n) for n in _SAP_NUM_RE.findall(cleaned)}

def _sap_alpha(text: str) -> str:
    """Product-TYPE words only (sizes stripped). 'External Circlip 65mm' →
    'external circlip mm'. Product type — not size — determines the right catalog
    item / HSN, so this is the primary matching signal."""
    return re.sub(r"\s+", " ", re.sub(r"[\d.]+", " ", _sap_norm(text))).strip()

def _sap_rank(desc: str, rows: list, top_k: int) -> list:
    """Deterministic, type-first matcher (no LLM). Ranks the WHOLE catalog by
    product-type word similarity; numeric/full-string overlap are minor tiebreakers
    that can only reorder same-type rows, never let a wrong-type row that merely
    shares a number (e.g. an oil seal sharing '65' with 'External Circlip 65mm')
    outrank the right family.

    Confidence band per chosen row:
      • type_sim < _SAP_TYPE_MIN_SIM → different product type → no-match band (≤0.30)
      • right type + ALL line numbers present → exact spec → 0.85–0.99
      • right type, size differs/absent → review band 0.45–0.70 (num_match=false)"""
    from rapidfuzz import fuzz
    qn = _sap_norm(desc)
    qa = _sap_alpha(desc)
    qnums = _sap_numbers(desc)
    if not qa or not rows:
        return []

    scored = []
    for r in rows:
        type_sim = fuzz.token_set_ratio(qa, _sap_alpha(r["original_desc"] or r["desc"])) / 100.0
        full_sim = fuzz.token_set_ratio(qn, r["norm"]) / 100.0
        num_ov = (len(qnums & r["nums"]) / len(qnums)) if qnums else 0.0
        rank = type_sim + 0.05 * full_sim + 0.08 * num_ov   # type dominates
        scored.append((rank, type_sim, r))
    scored.sort(key=lambda t: t[0], reverse=True)

    out, seen = [], set()
    for _rank, type_sim, r in scored:
        code = r["code"]
        if code in seen:
            continue
        seen.add(code)
        num_ok = (not qnums) or qnums.issubset(r["nums"])
        if type_sim < _SAP_TYPE_MIN_SIM:
            conf, type_ok, why = round(min(0.30, type_sim), 4), False, "different product type"
        elif num_ok:
            conf, type_ok, why = round(min(0.99, 0.85 + 0.14 * type_sim), 4), True, "matches type and size"
        else:
            conf, type_ok, why = round(0.45 + 0.25 * type_sim, 4), True, "same product type, size differs"
        out.append({
            "item_code": code, "material_desc": r.get("original_desc") or r["desc"],
            "hsn_code": r.get("hsn", ""), "score": conf, "hsn_match": False,
            "num_match": num_ok, "type_match": type_ok, "reason": why,
            "model_confidence": conf, "confidence_label": _sap_conf_label(conf),
            "ambiguous": False,
        })
        if len(out) >= top_k:
            break
    return out

def _sap_conf_label(x: float) -> str:
    """high / medium / low band for a 0..1 confidence, for the UI's tick & colour."""
    return "high" if x >= 0.8 else "medium" if x >= 0.5 else "low"

_SAP_MPN_RE = re.compile(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*")

_SAP_MPN_UNIT_RE = re.compile(
    r"^\d+(?:\.\d+)?(?:MM|CM|NB|KG|ML|MTR|GMS?|SQMM|INCH|DIA|V|W|A|L|M)?$")

def _sap_mpn_tokens(text: str) -> set:
    """Manufacturer/part-number tokens — alphanumeric model codes like 'LBT-410',
    'CPA-1020', 'SS304', 'MISO-50-200'. Pure dimension+unit tokens (65MM, 40NB) are
    excluded so a size is never mistaken for a part number."""
    out = set()
    for tok in _SAP_MPN_RE.findall((text or "").upper()):
        core = re.sub(r"[^A-Z0-9]", "", tok)        # 'SS-304' and 'SS304' → 'SS304'
        if len(core) < 4:
            continue
        if not (re.search(r"[A-Z]", core) and re.search(r"\d", core)):
            continue
        if _SAP_MPN_UNIT_RE.match(tok):
            continue
        out.add(core)
    return out

def _sap_hsn_prefix_match(a: str, b: str) -> bool:
    """Two HSNs are the SAME FAMILY iff their first 4 digits match. The 4-digit
    heading is the family level — e.g. 927167 and 9271676 are both family 9271 and
    are treated as absolutely the same. Trailing digits (6/8-digit sub-codes) are
    ignored for family comparison."""
    a = re.sub(r"\D", "", a or "")
    b = re.sub(r"\D", "", b or "")
    return len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]

def _sap_llm_decide(desc: str, hsn_sac: str | None, cands: list) -> list:
    """The LLM makes the final, logical decision — it is NOT overridden by the
    deterministic score. The type-first ranker only supplies a sane candidate
    slate (so the model never sees cross-type junk like an oil seal for a circlip);
    gpt-4o-mini then reasons about which candidate is actually the same product.

    It may also decide NONE of them is the same product — in which case the top is
    demoted to the no-match band so the UI shows "map manually" rather than a wrong
    code. The displayed confidence still comes from the deterministic band (exact
    size vs different size); the LLM owns WHICH item, the band owns HOW sure.

    Returns the slate reordered with the LLM's choice first. On any error or no API
    key, returns the slate unchanged (deterministic fallback)."""
    import json
    if not cands or not OPENAI_API_KEY:
        return cands
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        listing = "\n".join(
            f"{i}. item_code={c['item_code']} | desc={c['material_desc']} | hsn={c['hsn_code']}"
            f" | size_matches_line={c.get('num_match')}"
            for i, c in enumerate(cands)
        )
        usr = (
            f"Invoice line item: {desc}" + (f" (invoice HSN {hsn_sac})" if hsn_sac else "") + "\n\n"
            f"Candidate SAP catalog materials (pre-ranked best-first):\n{listing}\n\n"
            "Choose the candidate that is the SAME PRODUCT as the invoice line:\n"
            "1. Product TYPE decides the match — a circlip is never an oil seal, a bush is "
            "never a bar. A different SIZE is STILL the same product (e.g. a 1.5\" flange and "
            "a 3\" flange are both flanges).\n"
            "2. Among same-type candidates, prefer the one whose SIZE matches the line "
            "(size_matches_line=true, or its dimensions equal the line's). If none has the "
            "exact size, pick the CLOSEST same-type item — that is still a valid match; just "
            "set size_match=false.\n"
            "3. Material grade (SS vs MS) is a weak signal, especially when the line names "
            "more than one material — do not reject a good type+size match over it.\n"
            "same_product is TRUE whenever your chosen candidate is the same TYPE of product, "
            "even if the size differs. Set same_product=false and index=-1 ONLY when NO "
            "candidate is the same type of product at all.\n"
            "Also report your OWN confidence (0..1) that the chosen item is correct, and set "
            "ambiguous=true with runner_up=<index> when two candidates are similarly good and "
            "a human should pick.\n"
            'Return JSON {"index": <0-based index of best, or -1>, "same_product": true|false, '
            '"size_match": true|false, "confidence": <0..1>, "ambiguous": true|false, '
            '"runner_up": <0-based index or -1>, "reason": "short logical justification"}.'
        )
        resp = client.chat.completions.create(
            model=_SAP_LLM_MODEL,
            messages=[{"role": "system", "content":
                       "You map vendor invoice line items to SAP catalog materials. "
                       "Product type is decisive; never match across product types."},
                      {"role": "user", "content": usr}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        idx = data.get("index")
        reason = (data.get("reason") or "")[:120]
        if data.get("same_product") is False or not isinstance(idx, int) or not (0 <= idx < len(cands)):
            # LLM judged NO candidate is even the same product type → coherent no-match:
            # demote every candidate to the no-match band so the UI says "map manually".
            return [{**c, "score": min(c["score"], _SAP_TYPE_MISMATCH_CAP),
                     "type_match": False, "model_confidence": 0.0, "confidence_label": "low",
                     "ambiguous": False,
                     "reason": reason or "no candidate is the same product type"} for c in cands]
        try:
            mconf = max(0.0, min(1.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            mconf = cands[idx].get("score", 0.0)
        ambiguous = bool(data.get("ambiguous"))
        chosen = {**cands[idx], "reason": reason or cands[idx].get("reason", ""),
                  "model_confidence": round(mconf, 4), "confidence_label": _sap_conf_label(mconf),
                  "ambiguous": ambiguous}
        rest = [c for i, c in enumerate(cands) if i != idx]
        runner = data.get("runner_up")                  # surface the alternative first for the picker
        if ambiguous and isinstance(runner, int) and 0 <= runner < len(cands) and runner != idx:
            rc = cands[runner]
            rest = [rc] + [c for c in rest if c["item_code"] != rc["item_code"]]
        return [chosen] + rest
    except Exception:
        return cands

def _sap_match(desc: str, hsn_sac: str | None, catalog: dict, top_k: int) -> list:
    """Build a sane candidate SLATE, then let the LLM logically decide the match.

      1. Type-first deterministic rank over the whole catalog (`_sap_rank`) — the
         slate's backbone; keeps cross-type junk out so the LLM can't pick it.
      2. MPN — rows sharing a part number with the line (strongest signal) are
         folded into the slate even if their fuzzy rank is lower.
      3. HSN — else rows whose HSN agrees with the line's HSN (4/6/8-digit prefix).
      4. `_sap_llm_decide` reasons over the slate and chooses (or rejects all). Its
         decision stands — it is NOT gated by the deterministic score.

    The displayed confidence still comes from the deterministic band (exact size →
    high, different size → review), but the LLM owns WHICH item is the match."""
    rows = catalog["rows"]
    if not rows:
        return []

    slate = _sap_rank(desc, rows, max(top_k, 10))

    qmpn = _sap_mpn_tokens(desc)
    pool_rows = [r for r in rows if qmpn and (_sap_mpn_tokens(r["original_desc"] or r["desc"]) & qmpn)]
    if not pool_rows and hsn_sac:
        pool_rows = [r for r in rows if _sap_hsn_prefix_match(hsn_sac, r.get("hsn"))]
    if pool_rows:                                   # fold strong-signal pool into the slate
        seen = {c["item_code"] for c in slate}
        slate = [c for c in _sap_rank(desc, pool_rows, 4) if c["item_code"] not in seen] + slate

    # Only same-type candidates are worth the LLM's judgement; if none are even the
    # right product type, there is nothing to decide — return the deterministic top
    # (already in the no-match band) so the UI shows "map manually".
    viable = [c for c in slate if c.get("type_match")]
    if not viable:
        return slate[:top_k]

    decided = _sap_llm_decide(desc, hsn_sac, viable[:10])

    # Type guardrail: the LLM may choose among same-TYPE candidates, but it must not
    # pick one whose product-type words match clearly WORSE than another available
    # candidate (e.g. choosing "SS TEE 40 NB" over "SS Bend 40 NB" for a Bend). If a
    # candidate's type wording is materially closer, promote it to the top.
    if decided and decided[0].get("type_match"):
        from rapidfuzz import fuzz
        qa = _sap_alpha(desc)
        type_sim = lambda c: fuzz.token_set_ratio(qa, _sap_alpha(c["material_desc"])) / 100.0
        chosen_sim = type_sim(decided[0])
        best = max(decided, key=type_sim)
        if type_sim(best) - chosen_sim > 0.15 and best["item_code"] != decided[0]["item_code"]:
            decided = [best] + [c for c in decided if c["item_code"] != best["item_code"]]

    chosen = {c["item_code"] for c in decided}
    tail = [c for c in slate if c["item_code"] not in chosen]
    out = (decided + tail)[:top_k]
    # Flag whether each candidate's HSN is the same 4-digit family as the invoice line.
    for c in out:
        c["hsn_match"] = _sap_hsn_prefix_match(hsn_sac, c.get("hsn_code")) if hsn_sac else False
    # Deterministic ambiguity: two DIFFERENT item codes with the SAME description are a
    # genuine "which one?" — let the user pick even if the model didn't flag it.
    if (len(out) >= 2 and not out[0].get("ambiguous") and out[0].get("type_match")
            and out[0]["item_code"] != out[1]["item_code"]
            and _sap_norm(out[0]["material_desc"]) == _sap_norm(out[1]["material_desc"])):
        out[0]["ambiguous"] = True
    return out

def _load_sap_catalog(tenant: str):
    """Return {"rows": [{code, desc, expanded_desc, alpha, numeric_qty, numeric_volume,
    numeric_dimension, dimension_unit, volume_unit, norm, nums}, ...], "by_code": {...}}.

    The ONLY source is the DB table `sap_item_catalog` (every distinct
    (item_code, material_desc) pair with its HSN). The `sap_catalog_expanded (1).csv`
    export is deliberately NOT used: it is stale, partial (whole product families
    like circlips / teflon bushes are missing) and carries no HSN, so falling back
    to it produced confident wrong matches (e.g. "External Circlip 65mm" → an oil
    seal that merely shares the number 65).

    On a transient DB error we return an EMPTY result and do NOT cache it, so a
    single connectivity blip can't poison matching for the whole cache TTL — the
    next request simply retries the DB."""
    import time

    cached = _SAP_CATALOG_CACHE.get(tenant)
    if cached and (time.time() - cached[0]) < _SAP_CACHE_TTL:
        return cached[1]

    rows, by_code = [], {}
    last_err = None
    for attempt in range(2):                       # one quick retry — RDS DNS can blip
        try:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT item_code, material_desc, hsn_code
                           FROM sap_item_catalog
                           WHERE tenant_id = %s AND is_active
                           ORDER BY item_code""",
                        (tenant,),
                    )
                    for code, desc, hsn in cur.fetchall():
                        rows.append({"code": code, "desc": desc, "hsn": hsn or "",
                                     "original_desc": desc, "expanded_desc": desc,
                                     "alpha": "", "numeric_qty": "", "numeric_volume": "",
                                     "numeric_dimension": "", "dimension_unit": "", "volume_unit": "",
                                     "rating_value": "", "rating_unit": "",
                                     "norm": _sap_norm(desc), "nums": _sap_numbers(desc)})
                        by_code.setdefault(code, []).append((desc, hsn or ""))
            finally:
                put_conn(conn)
            break
        except Exception as e:
            last_err = e
            rows, by_code = [], {}

    if not rows:
        import logging
        logging.warning(f"[SAP] catalog unavailable for tenant {tenant} "
                        f"(rows=0, err={last_err}); returning empty — NOT cached.")
        return {"rows": [], "by_code": {}}        # do not cache a failed/empty load

    bundle = {"rows": rows, "by_code": by_code}
    _SAP_CATALOG_CACHE[tenant] = (time.time(), bundle)
    return bundle

class MappingMatchRequest(BaseModel):
    description: str | None = None
    hsn_sac: str | None = None
    company_id: str | None = None
    top_k: int = 5

@router.post("/api/mapping/match")
def mapping_match(body: MappingMatchRequest):
    """Map a bill line-item 'Description of Goods' → SAP item code.

    Deterministic type-first ranker over the DB catalog (no embeddings, no LLM):
    product-TYPE word similarity drives the match; numbers are a tiebreaker only.
    See `_sap_rank`.
    """

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    if not _sap_matching_enabled(tenant):          # tenant-level separation (DB flag)
        return {"candidates": []}
    desc = (body.description or "").strip()
    if not desc:
        return {"candidates": []}

    catalog = _load_sap_catalog(tenant)
    if not catalog["rows"]:
        return {"candidates": []}

    top_k = max(1, min(body.top_k or 30, _SAP_PREFILTER_K))
    return {"candidates": _sap_match(desc, body.hsn_sac, catalog, top_k)}

class MappingBatchItem(BaseModel):
    index: int
    description: str | None = None
    hsn_sac: str | None = None

class MappingMatchBatchRequest(BaseModel):
    items: list[MappingBatchItem] = []
    company_id: str | None = None
    top_k: int = 5

@router.post("/api/mapping/match-batch")
def mapping_match_batch(body: MappingMatchBatchRequest):
    """Map ALL bill line items with the deterministic type-first ranker.

    No embeddings, no LLM — each line is ranked independently against the DB
    catalog by `_sap_rank` (product type drives the match; numbers tiebreak).
    Deterministic and fast. Returns one entry per input index.
    """

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    if not _sap_matching_enabled(tenant):          # tenant-level separation (DB flag)
        return {"results": [{"index": it.index, "candidates": []} for it in body.items]}
    catalog = _load_sap_catalog(tenant)
    items = [it for it in body.items if (it.description or "").strip()]
    if not catalog["rows"] or not items:
        return {"results": [{"index": it.index, "candidates": []} for it in body.items]}

    top_k = max(1, min(body.top_k or 30, _SAP_PREFILTER_K))

    # Each line runs an independent LLM decision — fan them out concurrently so a
    # multi-line bill costs ~one call's latency, not N sequential calls.
    from concurrent.futures import ThreadPoolExecutor
    def _one(it):
        return {"index": it.index,
                "candidates": _sap_match((it.description or "").strip(), it.hsn_sac, catalog, top_k)}
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        results = list(ex.map(_one, items))

    present = {r["index"] for r in results}
    for it in body.items:
        if it.index not in present:
            results.append({"index": it.index, "candidates": []})
    results.sort(key=lambda r: r["index"])
    return {"results": results}

class MappingSearchRequest(BaseModel):
    query: str
    company_id: str | None = None
    limit: int = 20

@router.post("/api/mapping/search")
def mapping_search(body: MappingSearchRequest):
    """Free-text search over ALL catalog material descriptions / item codes — for
    when the operator rejects the auto-match or there is no match and wants to find
    the right material manually. Substring hits rank first, then fuzzy similarity."""
    from rapidfuzz import fuzz

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    if not _sap_matching_enabled(tenant):          # tenant-level separation (DB flag)
        return {"results": []}
    q = (body.query or "").strip()
    if not q:
        return {"results": []}
    rows = _load_sap_catalog(tenant)["rows"]
    if not rows:
        return {"results": []}

    ql = q.lower()
    limit = max(1, min(body.limit or 20, 50))
    scored = []
    for r in rows:
        desc = r.get("original_desc") or r["desc"]
        hay = f"{desc} {r['code']}".lower()
        s = (200 if ql in hay else 0) + fuzz.token_set_ratio(ql, desc.lower())
        scored.append((s, r))
    scored.sort(key=lambda t: t[0], reverse=True)

    out, seen = [], set()
    for _s, r in scored:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append({"item_code": r["code"], "material_desc": r.get("original_desc") or r["desc"],
                    "hsn_code": r.get("hsn", "")})
        if len(out) >= limit:
            break
    return {"results": out}

def _sap_clean_code(v: str) -> str:
    """Normalize a SAP item code the same way the catalog loader does: trim, then
    drop zero-padding so padded exports align with the unpadded catalog codes."""
    c = (v or "").strip()
    return c.lstrip("0") or c

def _load_sap_vendor_map(tenant: str):
    """Return a list of {item_code, vendor_gstn, supplier_code, supplier_name, freq}
    from sap_item_vendors for the tenant. Cached with the same TTL as the catalog.
    A transient DB error returns an EMPTY list and is NOT cached (see _load_sap_catalog)."""
    import time

    cached = _SAP_VENDOR_CACHE.get(tenant)
    if cached and (time.time() - cached[0]) < _SAP_CACHE_TTL:
        return cached[1]

    rows, last_err = [], None
    for _attempt in range(2):
        try:
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT item_code, vendor_gstn, supplier_code, supplier_name, freq
                           FROM sap_item_vendors
                           WHERE tenant_id = %s""",
                        (tenant,),
                    )
                    rows = [
                        {"item_code": ic, "vendor_gstn": (g or "").upper(),
                         "supplier_code": sc, "supplier_name": sn or "", "freq": int(fr or 1)}
                        for ic, g, sc, sn, fr in cur.fetchall()
                    ]
            finally:
                put_conn(conn)
            break
        except Exception as e:
            last_err = e
            rows = []

    if not rows:
        import logging
        logging.warning(f"[SAP] vendor map unavailable for tenant {tenant} "
                        f"(rows=0, err={last_err}); returning empty — NOT cached.")
        return []
    _SAP_VENDOR_CACHE[tenant] = (time.time(), rows)
    return rows

class VendorCodeRequest(BaseModel):
    item_codes: list[str] = []
    vendor_gstin: str | None = None
    company_id: str | None = None

def _sap_tally_supplier(rows: list) -> list:
    """Collapse vendor-map rows to ranked distinct supplier codes by summed freq."""
    tally: dict = {}
    for r in rows:
        e = tally.setdefault(r["supplier_code"],
                             {"supplier_code": r["supplier_code"],
                              "supplier_name": r["supplier_name"], "freq": 0})
        e["freq"] += r["freq"]
        if r["supplier_name"] and not e["supplier_name"]:
            e["supplier_name"] = r["supplier_name"]
    return sorted(tally.values(), key=lambda t: t["freq"], reverse=True)

@router.post("/api/mapping/vendor-code")
def mapping_vendor_code(body: VendorCodeRequest):
    """Resolve the bill's vendor SAP (supplier) code.

    A vendor SAP code is an identity of the VENDOR, so the authoritative key is
    the bill's GSTIN → SAP `Invoice Supplier Code` (independent of which items
    were matched). Resolution order:

      1. GSTIN present and found in the SAP register → that supplier code
         (`matched_by='gstin'`, `vendor_in_master=True`). Authoritative.
      2. GSTIN present but NOT in the register → the vendor isn't onboarded in
         the SAP master; return no code with `vendor_in_master=False` rather than
         guessing a different vendor from the items.
      3. No GSTIN at all → best-effort guess from the matched item codes
         (`matched_by='item'`, `ambiguous=True`) — flagged as a guess because a
         part is usually supplied by several vendors, so this is NOT the bill's
         vendor with any confidence.

    Tenant-gated by the DB flag."""

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    empty = {"supplier_code": None, "supplier_name": None, "matched_by": "none",
             "vendor_in_master": None, "ambiguous": False, "candidates": []}
    if not _sap_matching_enabled(tenant):
        return empty

    vmap = _load_sap_vendor_map(tenant)
    gst = (body.vendor_gstin or "").strip().upper()

    # 1/2 — GSTIN is the vendor's true identity: match against the WHOLE register.
    if gst:
        gst_rows = [r for r in vmap if r["vendor_gstn"] == gst]
        if not gst_rows:
            # Vendor GST known but absent from SAP master — a new/unmapped vendor.
            return {**empty, "matched_by": "gstin_unmatched", "vendor_in_master": False}
        ranked = _sap_tally_supplier(gst_rows)
        top = ranked[0]
        return {
            "supplier_code": top["supplier_code"],
            "supplier_name": top["supplier_name"] or None,
            "matched_by": "gstin",
            "vendor_in_master": True,
            "ambiguous": len(ranked) > 1,       # same vendor may hold >1 SAP code
            "candidates": ranked[:5],
        }

    # 3 — No GSTIN: fall back to an item-derived GUESS (explicitly low-confidence).
    codes = {_sap_clean_code(c) for c in (body.item_codes or []) if (c or "").strip()}
    rows = [r for r in vmap if r["item_code"] in codes] if codes else []
    if not rows:
        return empty
    ranked = _sap_tally_supplier(rows)
    top = ranked[0]
    return {
        "supplier_code": top["supplier_code"],
        "supplier_name": top["supplier_name"] or None,
        "matched_by": "item",
        "vendor_in_master": None,
        "ambiguous": True,                      # a part maps to many vendors — a guess
        "candidates": ranked[:5],
    }

@router.post("/api/sap/generate-token")
def generate_sap_pairing_token():
    """One-time pairing token for the SAP agent (mirror of the Tally flow).
    Stored on int_connections with source='SAP'. Expires in 30 minutes."""
    import secrets
    from datetime import datetime, timedelta, timezone

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    token   = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO int_connections
                    (connection_id, tenant_id, source, status,
                     agent_pairing_token, agent_pairing_expires_at, sync_config_jsonb)
                VALUES (gen_random_uuid(), %s, 'SAP', 'PENDING', %s, %s, '{}')
                ON CONFLICT (tenant_id, source) DO UPDATE
                  SET agent_pairing_token      = EXCLUDED.agent_pairing_token,
                      agent_pairing_expires_at = EXCLUDED.agent_pairing_expires_at,
                      status                   = 'PENDING'
                """,
                (tenant, token, expires),
            )
        conn.commit()
    finally:
        put_conn(conn)

    return {
        "token":      token,
        "expires_at": expires.isoformat(),
        "instructions": (
            "Download the Onsumm SAP Agent, run it on the SAP server, and paste this token "
            "when prompted. Token expires in 30 minutes."
        ),
    }

@router.get("/api/sap/connection")
def get_sap_connection():
    """Dashboard polls this for the SAP agent's connection status."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT connection_id, status, agent_version,
                       last_sync_at, error_message, created_at
                FROM int_connections
                WHERE tenant_id = %s AND source = 'SAP'
                LIMIT 1
                """,
                (tenant,),
            )
            row = cur.fetchone()
    finally:
        put_conn(conn)

    if not row:
        raise HTTPException(status_code=404, detail="No SAP connection found")

    connection_id, status, agent_version, last_sync_at, error_message, paired_at = row
    return {
        "connection_id":  str(connection_id),
        "status":         status,
        "agent_version":  agent_version,
        "last_sync_at":   last_sync_at.isoformat() if last_sync_at else None,
        "error_message":  error_message,
        "paired_at":      paired_at.isoformat() if paired_at else None,
    }
