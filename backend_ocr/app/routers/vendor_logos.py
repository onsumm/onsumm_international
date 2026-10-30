"""Vendor logos, found once and kept.

Where a vendor's website comes from, best evidence first:

  1. Its own email domain: the vendor's email or a contact's, unless it is a
     shared mailbox (gmail.com …) or the buyer's own domain. A company's mail
     domain is its website.
  2. A company-name search (Clearbit's public company-suggest endpoint), only
     for companies and firms, and only on an unambiguous match. A proprietor's
     GSTIN carries a personal PAN (4th letter 'P'), so their name, which is a
     person's name, is never searched and never gets a stranger's logo.

The logo is the site's icon from Google's favicon service. When there's no
website (most proprietors) or it has no usable icon:

  3. The logo printed on the vendor's own bills: images in the header of the
     first page of its latest bills, minus QR codes and scans, with gpt-4o-mini
     picking which one (if any) is the issuer's logo, not a QR code, a product
     photo, a decoration, a partner brand or the buyer's. The bills already go
     to OpenAI for reading; only these small header crops are sent here.

Logos are kept in the database (a few KB each) so every server shows the same
ones. A miss is looked at again when the vendor sends a new bill, or after 30
days. A wrong one can be replaced with the right website, or removed, from the
vendor card; neither is undone by the next lookup.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import struct
import threading
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ..config import OPENAI_API_KEY, get_conn, put_conn
from ..core import object_store
from ..security.auth import require_tenant

log = logging.getLogger(__name__)
router = APIRouter()

# Mirrors routers/email_connection._FREEMAIL_DOMAINS: a shared mail domain says
# nothing about which company a sender is from.
_FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "hotmail.com", "outlook.com", "live.com", "rediffmail.com", "icloud.com",
    "protonmail.com", "proton.me", "aol.com",
})
_SUGGEST_URL = "https://autocomplete.clearbit.com/v1/companies/suggest"
_ICON_URL = "https://t1.gstatic.com/faviconV2"
_RETRY_DAYS = 30
_MIN_ICON_PX = 32          # a 16px favicon blown up to an avatar is a smudge
_MAX_ICON_BYTES = 200_000
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")

# Legal-form words that say nothing about which company it is.
_LEGAL = frozenset({
    "private", "pvt", "limited", "ltd", "llp", "inc", "incorporated", "pbc", "corp", "corporation",
    "co", "company", "opc", "plc", "llc", "gmbh", "the", "ms", "and", "of",
})
# Words shared by thousands of companies; a name made only of these matches anything.
_GENERIC = frozenset({
    "technologies", "technology", "tech", "solutions", "solution", "services", "service", "software",
    "systems", "enterprises", "enterprise", "media", "digital", "consulting", "consultants", "ventures",
    "labs", "global", "international", "group", "industries", "trading", "traders", "internet",
    "communications", "india", "infotech", "networks", "logistics", "marketing", "business", "web",
})


# ── Matching ─────────────────────────────────────────────────────────────────

def _tokens(name: str | None) -> list[str]:
    """A company name as comparable words, without its legal form."""
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"^\s*m\s*/\s*s\.?\s*", " ", n)      # "M/S. MEDIA JUNCTION"
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return [t for t in n.split() if t not in _LEGAL]


def _distinct(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in _GENERIC]


def _label(domain: str) -> str:
    """The name part of a domain: tatatel.co.in → tatatel."""
    return re.sub(r"[^a-z0-9]", "", domain.split(".")[0])


def is_individual(gstn: str | None, pan: str | None) -> bool:
    """True for a proprietor: the PAN's 4th letter is 'P' (a person)."""
    p = (gstn or "").strip().upper()
    p = p[2:12] if len(p) == 15 else (pan or "").strip().upper()
    return len(p) == 10 and p[3] == "P"


def name_matches(vendor_name: str, suggestion_name: str, domain: str) -> bool:
    """Whether a search result is this vendor, not just a company with a similar name.

    The result's name has to be the vendor's name give or take generic words
    (every distinctive word of the vendor's, nothing the vendor's name lacks), and
    its domain has to carry the vendor's first distinctive word, unless the names
    are identical. Precision over reach: initials beat someone else's logo.
    """
    v, s = _tokens(vendor_name), _tokens(suggestion_name)
    if not v or not s:
        return False
    if v == s:
        return True
    vd = _distinct(v)
    if not vd or len("".join(vd)) < 5:       # "Dodi Solutions": too short to tell apart
        return False
    if not set(vd) <= set(s) or not set(s) <= set(v):
        return False
    return vd[0] in _label(domain)


def pick_email_domain(vendor_name: str, vendor_email: str | None, contact_emails: list[str],
                      own_domains: set[str]) -> str | None:
    """The vendor's website from its email addresses, or None when there's no telling."""
    def dom(e: str | None) -> str | None:
        if not e or "@" not in e:
            return None
        d = e.rsplit("@", 1)[1].strip().lower().rstrip(".")
        return d if _DOMAIN_RE.match(d) and d not in _FREEMAIL_DOMAINS and d not in own_domains else None

    own = dom(vendor_email)
    found = [d for d in [own, *map(dom, contact_emails)] if d]
    if not found:
        return None
    uniq = list(dict.fromkeys(found))
    if len(uniq) == 1:
        return uniq[0]
    # Several: the one that carries the vendor's name, else the vendor's own address.
    words = _distinct(_tokens(vendor_name)) or _tokens(vendor_name)
    named = [d for d in uniq if any(len(w) >= 4 and w in _label(d) for w in words)]
    if len(named) == 1:
        return named[0]
    return own


# ── Fetching ─────────────────────────────────────────────────────────────────

def _icon_size(data: bytes) -> int | None:
    """Pixel width of a PNG, or None when it isn't one (then it isn't judged)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">I", data[16:20])[0]
    return None


def fetch_icon(client: httpx.Client, domain: str) -> tuple[bytes, str] | None:
    r = client.get(_ICON_URL, params={
        "client": "SOCIAL", "type": "FAVICON", "fallback_opts": "TYPE,SIZE,URL",
        "url": f"https://{domain}", "size": "128",
    })
    if r.status_code != 200:
        return None           # 404: the site has no icon
    mime = r.headers.get("content-type", "").split(";")[0].strip()
    data = r.content
    if not mime.startswith("image/") or not data or len(data) > _MAX_ICON_BYTES:
        return None
    w = _icon_size(data)
    if w is not None and w < _MIN_ICON_PX:
        return None
    return data, mime


# Country second levels, where the registrable domain is three labels (acme.co.in).
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "ac", "edu", "firm", "gen", "ind", "res"})


def parent_domain(domain: str) -> str | None:
    """in.airtel.com → airtel.com; airtel.co.in → None (already the company's)."""
    parts = domain.split(".")
    keep = 3 if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2 else 2
    return ".".join(parts[-keep:]) if len(parts) > keep else None


def icon_for(client: httpx.Client, domain: str) -> tuple[str, bytes, str] | None:
    """The domain's logo, else its parent's (mail often comes from a subdomain)."""
    for d in dict.fromkeys(x for x in (domain, parent_domain(domain)) if x):
        icon = fetch_icon(client, d)
        if icon:
            return d, icon[0], icon[1]
    return None


def search_domain(client: httpx.Client, name: str) -> str | None:
    r = client.get(_SUGGEST_URL, params={"query": " ".join(_tokens(name))[:80]})
    if r.status_code != 200:
        return None
    for hit in (r.json() or [])[:3]:
        d = (hit.get("domain") or "").lower()
        if _DOMAIN_RE.match(d) and name_matches(name, hit.get("name") or "", d):
            return d
    return None


# ── From the vendor's own bills ──────────────────────────────────────────────

_BILLS_LOOKED_AT = 3
_MAX_PDF_BYTES = 15_000_000
_MAX_CANDIDATES = 4
_LOGO_PX = 160


def _is_qr_like(img) -> bool:
    """A square of nothing but black and white: a QR code (e-invoices carry one up top)."""
    from PIL import ImageStat
    w, h = img.size
    if not 0.8 <= w / h <= 1.25:
        return False
    sat = ImageStat.Stat(img.convert("HSV")).mean[1]
    hist = img.convert("L").histogram()
    total = sum(hist) or 1
    dark, light = sum(hist[:90]) / total, sum(hist[190:]) / total
    return sat < 12 and dark + light > 0.9 and dark > 0.2


def _ahash(img) -> int:
    """A 64-bit look-alike hash: the same logo drawn twice, or at another size, hashes nearly the same."""
    g = img.convert("L").resize((8, 8))
    px = list(g.getdata())
    avg = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > avg)


def same_picture(a: int, b: int) -> bool:
    return bin(a ^ b).count("1") <= 6


def _tidy(img) -> bytes:
    """Trim the white around a logo and keep its shape (a wide wordmark stays
    wide, so it fills its tile instead of shrinking into a square), PNG."""
    from PIL import Image, ImageChops
    rgb = img.convert("RGB")
    box = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).convert("L").point(lambda v: 255 if v > 18 else 0).getbbox()
    if box:
        rgb = rgb.crop(box)
    pad = max(2, int(min(rgb.size) * 0.06))
    framed = Image.new("RGB", (rgb.width + 2 * pad, rgb.height + 2 * pad), "white")
    framed.paste(rgb, (pad, pad))
    framed.thumbnail((_LOGO_PX * 2, _LOGO_PX))
    out = io.BytesIO()
    framed.save(out, "PNG", optimize=True)
    return out.getvalue()


def header_images(pdf: bytes) -> tuple[list[dict], str]:
    """Images in the header of a bill's first page that could be a logo, and the header's text.

    Leaves out specks, full-page scans and banners, anything below the top
    30% of the page, odd shapes, blank boxes and QR codes."""
    import fitz
    from PIL import Image
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
    except Exception:
        return [], ""
    out: dict[str, dict] = {}
    try:
        if not doc.page_count:
            return [], ""
        page = doc[0]
        W, H = page.rect.width, page.rect.height
        text = " ".join(page.get_text("text", clip=fitz.Rect(0, 0, W, H * 0.30)).split())[:600]
        for info in page.get_image_info():
            b = fitz.Rect(info["bbox"]) & page.rect
            w, h = b.width, b.height
            if w < 18 or h < 12 or w > W * 0.55 or h > H * 0.25 or b.y0 > H * 0.30:
                continue
            if not 0.3 <= w / h <= 5:
                continue
            pix = page.get_pixmap(clip=b, dpi=200, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if sum(img.convert("L").histogram()[246:]) / (img.width * img.height) > 0.985:
                continue                                   # blank
            if _is_qr_like(img):
                continue
            key = hashlib.md5(img.resize((24, 24)).tobytes()).hexdigest()[:12]
            # Up top and to the left is where a letterhead puts its logo.
            out.setdefault(key, {"img": img, "key": key, "hash": _ahash(img), "rank": b.y0 / H + 0.3 * b.x0 / W})
    finally:
        doc.close()
    return list(out.values()), text


_PICK_PROMPT = """These images come from the top of an invoice issued by "{vendor}".
Which one is the issuer's own logo? It may show a trade or brand name instead of
"{vendor}" (a proprietor trades under a business name); the invoice header text
below shows which names the issuer goes by.{header}
A letterhead logo can be a symbol with no words; one that sits at the top of
every bill the issuer sent is very likely theirs.
Never pick: a QR code or barcode, a signature, stamp or seal, a product or stock
photo, a religious or decorative picture, a certification or government mark,
another brand the issuer sells or works with, or the buyer's logo{buyer}.
Reply as JSON: {{"reason": "<one sentence>", "logo": <image number, or null if none is the issuer's logo>}}"""


def pick_logo(vendor: str, buyers: list[str], images: list, header: str = "", notes: list[str] | None = None) -> int | None:
    """Which of the images is the vendor's logo (index), per gpt-4o-mini; None if none."""
    if not OPENAI_API_KEY or not images:
        return None
    from openai import OpenAI
    content: list[dict] = [{"type": "text", "text": _PICK_PROMPT.format(
        vendor=vendor, buyer=f" ({', '.join(buyers[:3])})" if buyers else "",
        header=f"\nInvoice header text: {header}" if header else "")}]
    for i, img in enumerate(images, 1):
        buf = io.BytesIO()
        im = img.copy()
        im.thumbnail((256, 256))
        im.save(buf, "PNG")
        note = notes[i - 1] if notes and i <= len(notes) else ""
        content.append({"type": "text", "text": f"Image {i}{f' ({note})' if note else ''}:"})
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "detail": "low"}})
    resp = OpenAI(api_key=OPENAI_API_KEY, timeout=30).chat.completions.create(
        model="gpt-4o-mini", temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": content}],
    )
    n = json.loads(resp.choices[0].message.content or "{}").get("logo")
    return n - 1 if isinstance(n, int) and 1 <= n <= len(images) else None


def _where(rank: float) -> str:
    return "top-left of the page" if rank < 0.15 else "near the top of the page" if rank < 0.3 else "lower in the header"


def bill_logo(supplier_id: str, vendor: str, buyers: list[str]) -> tuple[bytes, str] | None:
    """The logo printed on the vendor's latest bills, or None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.storage_path,
                       CASE WHEN octet_length(r.file_data) <= %s THEN r.file_data END
                  FROM fact_invoices fi
                  JOIN raw_invoice_uploads r ON r.upload_id = fi.upload_id
                 WHERE fi.supplier_id = %s
                   AND COALESCE(fi.soft_status, 'active') = 'active'
                   AND (r.storage_path LIKE 's3://%%' OR r.file_data IS NOT NULL)
                 ORDER BY fi.invoice_date DESC NULLS LAST
                 LIMIT %s
                """,
                (_MAX_PDF_BYTES, supplier_id, _BILLS_LOOKED_AT),
            )
            rows = cur.fetchall()
    finally:
        put_conn(conn)
    pdfs = []
    for ref, blob in rows:
        data = object_store.load(ref) if object_store.is_s3(ref) else None
        data = data or (bytes(blob) if blob else None)
        if data and len(data) <= _MAX_PDF_BYTES:
            pdfs.append(data)
    seen: dict[str, dict] = {}
    header = ""
    for pdf in pdfs:
        if not pdf.startswith(b"%PDF"):
            continue
        found, text = header_images(pdf)
        header = header or text
        counted: set[str] = set()
        for c in found:
            # The same logo, even redrawn, is one candidate, counted once per bill.
            twin = next((k for k, v in seen.items() if same_picture(v["hash"], c["hash"])), None)
            if twin is None:
                seen[c["key"]] = {**c, "bills": 1}
                counted.add(c["key"])
            elif twin not in counted:
                seen[twin]["bills"] += 1
                counted.add(twin)
    if not seen:
        return None
    # On every bill beats on one; then the most letterhead-like spot.
    cands = sorted(seen.values(), key=lambda c: (-c["bills"], c["rank"]))[:_MAX_CANDIDATES]
    bills = sum(1 for p in pdfs if p.startswith(b"%PDF"))
    notes = [f"{_where(c['rank'])}, on {c['bills']} of the issuer's {bills} latest bills" for c in cands]
    i = pick_logo(vendor, buyers, [c["img"] for c in cands], header, notes)
    return (_tidy(cands[i]["img"]), "image/png") if i is not None else None


# ── Resolving a tenant's vendors (in the background) ─────────────────────────

_running: set[str] = set()
_lock = threading.Lock()
_table_ready = False


def _ensure_table(cur) -> None:
    # Also in migrations.py; repeated (once per process) so a server that
    # hasn't run the migration yet still works.
    global _table_ready
    if _table_ready:
        return
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendor_logos (
            supplier_id UUID PRIMARY KEY,
            tenant_id   UUID NOT NULL,
            domain      TEXT,
            source      VARCHAR(10),
            status      VARCHAR(10) NOT NULL,
            mime        VARCHAR(40),
            image       BYTEA,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vlogo_tenant ON vendor_logos (tenant_id)")
    _table_ready = True


def _due(cur, tenant: str, supplier_id: str | None = None) -> list[tuple]:
    """Vendors never looked up, or whose miss is old enough to try again."""
    cur.execute(
        f"""
        SELECT s.supplier_id::text, s.name, s.legal_name, s.gstn, s.pan, s.email,
               COALESCE(array_remove(array_agg(DISTINCT c.email), NULL), '{{}}')
          FROM dim_suppliers s
          LEFT JOIN supplier_contacts c ON c.supplier_id = s.supplier_id AND c.tenant_id = s.tenant_id
          LEFT JOIN vendor_logos l ON l.supplier_id = s.supplier_id
         WHERE s.tenant_id = %s AND s.is_active = true
           AND (%s::uuid IS NULL OR s.supplier_id = %s::uuid)
           AND (l.supplier_id IS NULL
                OR (l.status = 'none' AND (
                        l.fetched_at < now() - interval '{_RETRY_DAYS} days'
                        -- a new bill may carry the logo
                     OR EXISTS (SELECT 1 FROM fact_invoices fi
                                 WHERE fi.supplier_id = s.supplier_id AND fi.tenant_id = s.tenant_id
                                   AND fi.created_at > l.fetched_at))))
         GROUP BY s.supplier_id, s.name, s.legal_name, s.gstn, s.pan, s.email
         LIMIT 500
        """,
        (tenant, supplier_id, supplier_id),
    )
    return cur.fetchall()


def _buyer_names(cur, tenant: str) -> list[str]:
    cur.execute("SELECT name FROM brands WHERE tenant_id = %s ORDER BY name", (tenant,))
    return [r[0] for r in cur.fetchall() if r[0]]


def _own_domains(cur, tenant: str) -> set[str]:
    cur.execute("SELECT DISTINCT lower(split_part(email, '@', 2)) FROM users WHERE company_id = %s AND email LIKE '%%@%%'", (tenant,))
    return {r[0] for r in cur.fetchall() if r[0]} - _FREEMAIL_DOMAINS


def _resolve_one(client: httpx.Client, row: tuple, own: set[str], buyers: list[str] | None = None) -> tuple[str, str | None, str | None, bytes | None, str | None]:
    sid, name, legal, gstn, pan, email, contacts = row
    mail_domain = pick_email_domain(name, email, list(contacts or []), own)
    if mail_domain and (hit := icon_for(client, mail_domain)):
        return sid, hit[0], "email", hit[1], hit[2]
    # No mail domain, or its site has no usable icon: look the company up by name.
    if not is_individual(gstn, pan):
        for n in dict.fromkeys(x for x in (name, legal) if x):
            d = search_domain(client, n)
            if d and d not in own and (hit := icon_for(client, d)):
                return sid, hit[0], "name", hit[1], hit[2]
    # No website logo: the one on its bills.
    if (logo := bill_logo(sid, name, buyers or [])):
        return sid, mail_domain, "bill", logo[0], logo[1]
    return sid, mail_domain, "email" if mail_domain else None, None, None


def resolve_tenant(tenant: str, supplier_id: str | None = None) -> int:
    """Look up every vendor that's due. Returns how many got a logo."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            rows = _due(cur, tenant, supplier_id)
            own = _own_domains(cur, tenant)
            buyers = _buyer_names(cur, tenant)
        conn.commit()
    finally:
        put_conn(conn)
    if not rows:
        return 0

    results = []
    with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "Onsumm/1.0"}) as client:
        def one(row):
            try:
                return _resolve_one(client, row, own, buyers)
            except Exception as e:             # one vendor's network hiccup isn't everyone's
                log.info("vendor logo lookup failed for %s: %s", row[0], e)
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = [r for r in pool.map(one, rows) if r]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for sid, domain, source, image, mime in results:
                cur.execute(
                    """
                    INSERT INTO vendor_logos (supplier_id, tenant_id, domain, source, status, mime, image, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (supplier_id) DO UPDATE
                       SET domain = EXCLUDED.domain, source = EXCLUDED.source, status = EXCLUDED.status,
                           mime = EXCLUDED.mime, image = EXCLUDED.image, fetched_at = now()
                     WHERE vendor_logos.status = 'none'      -- never undo a hand-set or removed logo
                    """,
                    (sid, tenant, domain, source, "found" if image else "none", mime, image),
                )
        conn.commit()
    finally:
        put_conn(conn)
    return sum(1 for r in results if r[3])


def _resolve_in_background(tenant: str) -> None:
    with _lock:
        if tenant in _running:
            return
        _running.add(tenant)

    def run():
        try:
            n = resolve_tenant(tenant)
            log.info("vendor logos: %s found for tenant %s", n, tenant)
        except Exception:
            log.exception("vendor logo lookup failed for tenant %s", tenant)
        finally:
            with _lock:
                _running.discard(tenant)

    threading.Thread(target=run, name=f"vendor-logos-{tenant[:8]}", daemon=True).start()


# ── API ──────────────────────────────────────────────────────────────────────

@router.get("/api/vendor-logos")
def list_vendor_logos():
    """Which vendors have a logo, and its version (for the image URL).

    Starts looking up any vendor that hasn't been yet; `pending` says how many,
    so the page knows to ask again shortly."""
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                "SELECT supplier_id::text, extract(epoch FROM fetched_at)::bigint FROM vendor_logos WHERE tenant_id = %s AND status = 'found'",
                (tenant,),
            )
            logos = {sid: v for sid, v in cur.fetchall()}
            pending = len(_due(cur, tenant))
        conn.commit()
    finally:
        put_conn(conn)
    if pending:
        _resolve_in_background(tenant)
    return {"logos": logos, "pending": pending}


def _check_vendor(cur, tenant: str, supplier_id: str) -> None:
    try:
        _uuid.UUID(supplier_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Vendor not found")
    cur.execute("SELECT 1 FROM dim_suppliers WHERE supplier_id = %s AND tenant_id = %s", (supplier_id, tenant))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Vendor not found")


@router.get("/api/vendors/{supplier_id}/logo")
def get_vendor_logo(supplier_id: str):
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _check_vendor(cur, tenant, supplier_id)
            cur.execute(
                "SELECT image, mime, domain FROM vendor_logos WHERE supplier_id = %s AND tenant_id = %s AND status = 'found'",
                (supplier_id, tenant),
            )
            row = cur.fetchone()
    finally:
        put_conn(conn)
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="No logo for this vendor")
    # The URL carries the version, so a changed logo is a new URL.
    return Response(content=bytes(row[0]), media_type=row[1] or "image/png",
                    headers={"Cache-Control": "private, max-age=604800", "X-Logo-Domain": row[2] or ""})


class LogoWebsiteBody(BaseModel):
    website: str


def _clean_website(raw: str) -> str:
    d = raw.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split("?")[0].split("@")[-1].split(":")[0]
    d = d[4:] if d.startswith("www.") else d
    if not _DOMAIN_RE.match(d):
        raise HTTPException(status_code=422, detail="That doesn't look like a website. Try something like acme.com.")
    return d


@router.put("/api/vendors/{supplier_id}/logo")
def set_vendor_logo(supplier_id: str, body: LogoWebsiteBody):
    """Use this website's logo for the vendor (fixes a wrong or missing one)."""
    tenant = require_tenant()
    domain = _clean_website(body.website)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _check_vendor(cur, tenant, supplier_id)
    finally:
        put_conn(conn)
    try:
        with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "Onsumm/1.0"}) as client:
            icon = fetch_icon(client, domain)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Couldn't reach the logo service. Try again in a moment.")
    if not icon:
        raise HTTPException(status_code=404, detail=f"No logo found for {domain}.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute(
                """
                INSERT INTO vendor_logos (supplier_id, tenant_id, domain, source, status, mime, image, fetched_at)
                VALUES (%s, %s, %s, 'manual', 'found', %s, %s, now())
                ON CONFLICT (supplier_id) DO UPDATE
                   SET domain = EXCLUDED.domain, source = 'manual', status = 'found',
                       mime = EXCLUDED.mime, image = EXCLUDED.image, fetched_at = now()
                RETURNING extract(epoch FROM fetched_at)::bigint
                """,
                (supplier_id, tenant, domain, icon[1], icon[0]),
            )
            version = cur.fetchone()[0]
        conn.commit()
    finally:
        put_conn(conn)
    return {"version": version, "domain": domain}


@router.delete("/api/vendors/{supplier_id}/logo")
def remove_vendor_logo(supplier_id: str):
    """Show initials instead, and don't look it up again."""
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _check_vendor(cur, tenant, supplier_id)
            _ensure_table(cur)
            cur.execute(
                """
                INSERT INTO vendor_logos (supplier_id, tenant_id, status, fetched_at)
                VALUES (%s, %s, 'hidden', now())
                ON CONFLICT (supplier_id) DO UPDATE SET status = 'hidden', image = NULL, mime = NULL, fetched_at = now()
                """,
                (supplier_id, tenant),
            )
        conn.commit()
    finally:
        put_conn(conn)
    return {"removed": True}
