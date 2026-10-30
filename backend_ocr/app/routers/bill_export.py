"""Rendering a bill to PDF.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

router = APIRouter()

from ..core.storage import UPLOADS_DIR
from ..core import object_store

@router.get("/api/bills/{invoice_id}/export-pdf")
def export_bill_pdf(invoice_id: str, company_id: str = Query(default=None)):
    """Export bill as PDF: original bill pages followed by approval chain activity page."""
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    import pypdf
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT fi.invoice_id, fi.invoice_number AS bill_number, fi.invoice_date, fi.status,
                          fi.total_amount, fi.tax_amount, fi.upload_id, u.file_name,
                          s.name AS vendor_name
                   FROM fact_invoices fi
                   LEFT JOIN raw_invoice_uploads u ON fi.upload_id = u.upload_id
                   LEFT JOIN dim_suppliers s ON fi.supplier_id = s.supplier_id
                   WHERE fi.invoice_id = %s::uuid AND fi.tenant_id = %s""",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")

            cur.execute(
                """SELECT step, actor_role, actor_name, action, comment, created_at
                   FROM bill_approval_chain WHERE invoice_id = %s ORDER BY created_at ASC""",
                (invoice_id,),
            )
            chain_rows = cur.fetchall()
    finally:
        put_conn(conn)

    bill_number = row["bill_number"] or str(row["invoice_id"])[:8]
    vendor_name = row["vendor_name"] or "Unknown Vendor"
    invoice_date = row["invoice_date"].strftime("%d %b %Y") if row["invoice_date"] else "-"
    total_raw = row["total_amount"]
    total_amount = f"Rs. {total_raw:,.2f}" if total_raw else "-"
    file_name = row["file_name"]
    upload_id = str(row["upload_id"]) if row.get("upload_id") else None

    # Load original PDF: try DB blob first, then disk fallback
    original_reader = None
    if file_name or upload_id:
        pdf_bytes = None
        _pc = get_conn()
        try:
            with _pc.cursor() as _cur:
                if upload_id:
                    pdf_bytes = object_store.invoice_pdf(_cur, upload_id)
        except Exception:
            pass
        finally:
            put_conn(_pc)

        if not pdf_bytes and file_name:
            pdf_path = UPLOADS_DIR / file_name
            if pdf_path.exists():
                try:
                    pdf_bytes = pdf_path.read_bytes()
                except Exception:
                    pass

        if pdf_bytes:
            try:
                original_reader = pypdf.PdfReader(BytesIO(pdf_bytes))
            except Exception:
                pass

    # Build approval chain activity page using reportlab
    activity_buf = BytesIO()
    doc = SimpleDocTemplate(
        activity_buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )
    styles = getSampleStyleSheet()

    # leading must exceed fontSize, or the title overprints the line below it
    h1 = ParagraphStyle('H1', parent=styles['Normal'], fontSize=18, leading=22, fontName='Helvetica-Bold',
                        textColor=colors.HexColor('#1e3a5f'), spaceAfter=6)
    meta = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=9, leading=13, fontName='Helvetica',
                          textColor=colors.HexColor('#6b7280'), spaceAfter=10)
    h2 = ParagraphStyle('H2', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold',
                        textColor=colors.HexColor('#1e3a5f'), spaceBefore=10, spaceAfter=6)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8, fontName='Helvetica',
                                leading=11)
    no_act = ParagraphStyle('NoAct', parent=styles['Normal'], fontSize=9, fontName='Helvetica',
                            textColor=colors.HexColor('#9ca3af'))

    from xml.sax.saxutils import escape

    def _txt(v) -> str:
        # Helvetica has no ₹ glyph (renders as a black box); Paragraph parses markup, so escape &/<.
        return escape(str(v).replace("₹", "Rs. "))

    story = []
    story.append(Paragraph("Bill Export", h1))
    story.append(Paragraph(
        f"Bill No: <b>{_txt(bill_number)}</b>  &nbsp;|&nbsp;  Vendor: <b>{_txt(vendor_name)}</b>  &nbsp;|&nbsp;  Date: {invoice_date}  &nbsp;|&nbsp;  Total: <b>{total_amount}</b>",
        meta,
    ))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e5e7eb'), spaceAfter=8))
    story.append(Paragraph("Approval Chain Activity", h2))

    if chain_rows:
        action_bg = {
            'approve': colors.HexColor('#d1fae5'),
            'approved': colors.HexColor('#d1fae5'),
            'reject': colors.HexColor('#fee2e2'),
            'rejected': colors.HexColor('#fee2e2'),
            'send_back': colors.HexColor('#fef3c7'),
            'submit': colors.HexColor('#dbeafe'),
            'submitted': colors.HexColor('#dbeafe'),
        }
        action_txt = {
            'approve': colors.HexColor('#065f46'),
            'approved': colors.HexColor('#065f46'),
            'reject': colors.HexColor('#991b1b'),
            'rejected': colors.HexColor('#991b1b'),
            'send_back': colors.HexColor('#92400e'),
            'submit': colors.HexColor('#1e40af'),
            'submitted': colors.HexColor('#1e40af'),
        }

        hdr = ParagraphStyle('Hdr', parent=styles['Normal'], fontSize=8, fontName='Helvetica-Bold',
                             textColor=colors.white)
        table_data = [[
            Paragraph("Actor", hdr), Paragraph("Role", hdr),
            Paragraph("Action", hdr), Paragraph("Comment", hdr), Paragraph("Time", hdr),
        ]]
        row_highlights = []
        for cr in chain_rows:
            action = cr["action"] or ""
            comment = cr["comment"] or "-"
            ts = cr["created_at"]
            ts_str = ts.strftime("%d %b %Y<br/>%H:%M") if ts else "-"
            role_label = (cr["actor_role"] or "").replace("_", " ").title()
            action_label = action.replace("_", " ").title()
            table_data.append([
                Paragraph(_txt(cr["actor_name"] or "-"), cell_style),
                Paragraph(role_label, cell_style),
                Paragraph(action_label, ParagraphStyle('AL', parent=cell_style,
                    textColor=action_txt.get(action, colors.HexColor('#374151')),
                    fontName='Helvetica-Bold')),
                Paragraph(_txt(comment), cell_style),
                Paragraph(ts_str, cell_style),
            ])
            row_highlights.append(action_bg.get(action, colors.white))

        # Actor, Role, Action, Comment, Time — fills the frame exactly so the table lines up with the headings
        fixed = [34*mm, 26*mm, 22*mm, 26*mm]
        frame_w = doc.width - 12  # SimpleDocTemplate's frame pads 6pt on each side
        col_widths = fixed[:3] + [frame_w - sum(fixed)] + fixed[3:]
        t = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e3a5f')),
            ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#d1d5db')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ]
        for i, bg in enumerate(row_highlights, start=1):
            if bg != colors.white:
                style_cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
        t.setStyle(TableStyle(style_cmds))
        story.append(t)
    else:
        story.append(Paragraph("No approval activity recorded.", no_act))

    doc.build(story)
    activity_buf.seek(0)

    # Merge: original bill pages + activity page
    writer = pypdf.PdfWriter()
    if original_reader:
        for page in original_reader.pages:
            writer.add_page(page)

    activity_reader = pypdf.PdfReader(activity_buf)
    for page in activity_reader.pages:
        writer.add_page(page)

    out_buf = BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)

    safe_name = bill_number.replace("/", "-").replace(" ", "_")
    return StreamingResponse(
        out_buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="bill_{safe_name}.pdf"'},
    )
