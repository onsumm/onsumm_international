"""Vendor logos: deciding which website is the vendor's.

A wrong logo is worse than initials, so these pin down when a match is refused:
proprietors (personal PAN) are never searched by name, a search result must be
the vendor's name give or take generic words, and a shared or the buyer's own
mail domain never stands for the vendor.
"""
from __future__ import annotations

import struct

import pytest
from fastapi import HTTPException

from backend_ocr.app.routers import vendor_logos as vl


class TestIsIndividual:
    def test_proprietor_gstin(self):
        # PAN AVMPS4714J: 4th letter P → a person.
        assert vl.is_individual("07AVMPS4714J1Z4", None)

    @pytest.mark.parametrize("gstn", ["06AAICC7367B1ZQ", "07AASFA1405H1ZN"])
    def test_company_and_firm(self, gstn):
        assert not vl.is_individual(gstn, None)

    def test_falls_back_to_pan(self):
        assert vl.is_individual(None, "akdpb6482c")
        assert not vl.is_individual("", "AAICC7367B")

    def test_unknown_is_not_individual(self):
        assert not vl.is_individual(None, None)


class TestNameMatches:
    @pytest.mark.parametrize("vendor,hit,domain", [
        ("Razorpay Software Private Limited", "Razorpay", "razorpay.com"),
        ("Signzy Technologies Pvt. Ltd.", "Signzy", "signzy.com"),
        ("Freshworks Technologies Private Limited", "Freshworks", "freshworks.com"),
        ("Tata Consultancy Services Limited", "Tata Consultancy Services", "tcs.com"),
        ("Stripe, Inc.", "Stripe", "stripe.com"),
    ])
    def test_accepts_the_same_company(self, vendor, hit, domain):
        assert vl.name_matches(vendor, hit, domain)

    @pytest.mark.parametrize("vendor,hit,domain", [
        # Another company sharing one word.
        ("Lotus Valley Technologies Pvt Ltd", "Lotus", "lotus.com"),
        ("Bharti Airtel Limited", "Airtel Africa", "airtel.africa"),
        # Only generic words in common.
        ("Quick Ads Media", "Media", "media.com"),
        ("Digital Solutions Pvt Ltd", "Digital", "digital.com"),
        # Too short to tell apart.
        ("Dodi Solutions Private Limited", "Dodi", "dodi.com"),
        # Not the identical name, and the domain doesn't carry it.
        ("Convertup Media Solutions Pvt Ltd", "Convertup Media", "someotherbrand.com"),
    ])
    def test_refuses_lookalikes(self, vendor, hit, domain):
        assert not vl.name_matches(vendor, hit, domain)

    def test_identical_name_trusts_the_results_domain(self):
        assert vl.name_matches("Convertup Media Private Limited", "Convertup Media", "convertup.in")

    def test_ms_prefix_and_ampersand(self):
        assert vl.name_matches("M/S. MEDIA JUNCTION", "Media Junction", "mediajunction.in")
        assert vl._tokens("Ernst & Young LLP") == ["ernst", "young"]


class TestPickEmailDomain:
    def test_company_domain(self):
        assert vl.pick_email_domain("Razorpay", "billing@razorpay.com", [], set()) == "razorpay.com"

    def test_freemail_is_no_one(self):
        assert vl.pick_email_domain("JMD Compusoft", "jmdcompusoft@gmail.com", ["a@yahoo.co.in"], set()) is None

    def test_buyers_own_domain_is_not_the_vendor(self):
        assert vl.pick_email_domain("Acme", "ap@onsumm.ai", [], {"onsumm.ai"}) is None

    def test_contact_domain_when_vendor_email_is_gmail(self):
        assert vl.pick_email_domain("Signzy", "signzy@gmail.com", ["ar@signzy.com"], set()) == "signzy.com"

    def test_several_prefers_the_one_named_like_the_vendor(self):
        got = vl.pick_email_domain("Accelerate Media Pvt Ltd", None, ["a@brightloans.in", "b@acceleratemedia.in"], set())
        assert got == "acceleratemedia.in"

    def test_several_unnamed_falls_back_to_vendor_email(self):
        got = vl.pick_email_domain("Accelerate Media", "x@brinkads.com", ["a@brightloans.in"], set())
        assert got == "brinkads.com"

    def test_several_unnamed_without_vendor_email_is_ambiguous(self):
        assert vl.pick_email_domain("Accelerate Media", None, ["a@brightloans.in", "b@brinkads.com"], set()) is None


class TestParentDomain:
    @pytest.mark.parametrize("domain,parent", [
        ("in.airtel.com", "airtel.com"),
        ("fr.dtdc.com", "dtdc.com"),
        ("mail.acme.co.in", "acme.co.in"),
        ("airtel.com", None),
        ("tatatel.co.in", None),
    ])
    def test_parent(self, domain, parent):
        assert vl.parent_domain(domain) == parent


class TestWebsiteAndIcon:
    @pytest.mark.parametrize("raw,clean", [
        ("https://www.Razorpay.com/pricing?x=1", "razorpay.com"),
        ("airtel.in", "airtel.in"),
        ("billing@tcs.com", "tcs.com"),
        ("http://shop.example.co.in:8080/", "shop.example.co.in"),
    ])
    def test_clean_website(self, raw, clean):
        assert vl._clean_website(raw) == clean

    @pytest.mark.parametrize("raw", ["not a site", "localhost", "", "http://"])
    def test_rejects_non_websites(self, raw):
        with pytest.raises(HTTPException) as e:
            vl._clean_website(raw)
        assert e.value.status_code == 422

    def test_icon_size_reads_png_width(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 128, 128)
        assert vl._icon_size(png) == 128
        assert vl._icon_size(b"GIF89a....") is None

    def test_small_favicons_are_refused(self):
        class R:
            status_code = 200
            headers = {"content-type": "image/png"}
            content = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 16, 16)

        class C:
            def get(self, *a, **k):
                return R()

        assert vl.fetch_icon(C(), "tiny.com") is None
        R.content = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 64, 64)
        assert vl.fetch_icon(C(), "fine.com") == (R.content, "image/png")


# ── Logos from the vendor's own bills ────────────────────────────────────────

def _png(img) -> bytes:
    import io
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _checker(n=29, cell=6):
    from PIL import Image
    im = Image.new("RGB", (n * cell, n * cell), "white")
    px = im.load()
    for y in range(n * cell):
        for x in range(n * cell):
            if ((x // cell) * 7 + (y // cell) * 13) % 3 == 0:
                px[x, y] = (0, 0, 0)
    return im


def _logo(w=240, h=120):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, h - 10, h - 10), fill=(220, 40, 60))
    d.rectangle((h, h // 3, w - 10, h // 3 + 30), fill=(30, 90, 200))
    return im


class TestBillLogo:
    def test_qr_codes_are_not_logos(self):
        assert vl._is_qr_like(_checker())
        assert not vl._is_qr_like(_logo(260, 220))      # squarish, but in colour
        from PIL import Image
        # A black-and-white wordmark is wide, not a square.
        assert not vl._is_qr_like(Image.new("RGB", (300, 80), "black"))

    def test_tidy_trims_and_keeps_the_shape(self):
        from PIL import Image
        import io
        canvas = Image.new("RGB", (1200, 900), "white")
        canvas.paste(_logo(480, 120), (500, 600))
        out = Image.open(io.BytesIO(vl._tidy(canvas)))
        # Trimmed to the logo (its drawn part is 460x100), keeping that shape, within bounds.
        assert 3.8 < out.width / out.height < 4.8
        assert out.width <= vl._LOGO_PX * 2 and out.height <= vl._LOGO_PX
        cols = [x for x in range(out.width) if any(out.getpixel((x, y)) != (255, 255, 255) for y in range(out.height))]
        assert cols[-1] - cols[0] > out.width * 0.85

    def test_header_images_keeps_the_letterhead_logo_only(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(fitz.Rect(40, 40, 160, 100), stream=_png(_logo()))             # letterhead logo
        page.insert_image(fitz.Rect(470, 40, 550, 120), stream=_png(_checker()))         # e-invoice QR
        page.insert_image(fitz.Rect(40, 600, 160, 660), stream=_png(_logo()))            # lower down: a stamp, say
        page.insert_text((200, 70), "ACME TRADERS  GSTIN 07AAACA1234A1Z5")
        imgs, text = vl.header_images(doc.tobytes())
        assert len(imgs) == 1 and imgs[0]["rank"] < 0.2
        assert "ACME TRADERS" in text

    def test_a_scanned_bill_offers_nothing(self):
        import fitz
        from PIL import Image
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=_png(Image.new("RGB", (595, 842), (240, 240, 235))))
        assert vl.header_images(doc.tobytes())[0] == []

    def test_the_same_logo_redrawn_is_one_picture(self):
        from PIL import ImageFilter
        a = _logo(240, 120)
        b = a.resize((520, 260)).filter(ImageFilter.GaussianBlur(1))     # same logo, rendered bigger and softer
        assert vl.same_picture(vl._ahash(a), vl._ahash(b))
        assert not vl.same_picture(vl._ahash(a), vl._ahash(_checker()))

    def test_not_a_pdf(self):
        assert vl.header_images(b"not a pdf") == ([], "")


class TestPickLogo:
    def _fake(self, monkeypatch, reply):
        calls = []

        class Completions:
            def create(self, **kw):
                calls.append(kw)
                msg = type("M", (), {"content": reply})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

        class Fake:
            def __init__(self, **kw):
                self.chat = type("Chat", (), {"completions": Completions()})()

        monkeypatch.setattr(vl, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr("openai.OpenAI", Fake)
        return calls

    def test_picks_the_named_image(self, monkeypatch):
        calls = self._fake(monkeypatch, '{"logo": 2}')
        assert vl.pick_logo("Acme", ["Onsumm"], [_logo(), _logo()], "ACME TRADERS") == 1
        text = calls[0]["messages"][0]["content"][0]["text"]
        assert '"Acme"' in text and "Onsumm" in text and "ACME TRADERS" in text

    @pytest.mark.parametrize("reply", ['{"logo": null}', '{"logo": 5}', '{"logo": "1"}', "{}"])
    def test_none_or_nonsense_is_no_logo(self, monkeypatch, reply):
        self._fake(monkeypatch, reply)
        assert vl.pick_logo("Acme", [], [_logo(), _logo()]) is None

    def test_without_a_key_it_never_guesses(self, monkeypatch):
        monkeypatch.setattr(vl, "OPENAI_API_KEY", "")
        assert vl.pick_logo("Acme", [], [_logo()]) is None
