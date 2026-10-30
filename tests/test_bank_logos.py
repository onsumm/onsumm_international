"""Bank symbols are read from S3, with the bundled copy as the backup."""
import pytest
from fastapi import HTTPException

from backend_ocr.app.routers import bank_logos as bl


def test_lists_the_bundled_symbols():
    codes = bl.available_codes()
    assert {"HDFC", "SBIN", "ICIC"} <= set(codes)
    assert all(len(c) == 4 and c.isupper() for c in codes)


def test_serves_s3_when_it_has_the_file(monkeypatch):
    monkeypatch.setattr(bl.object_store, "enabled", lambda: True)
    seen = {}
    def load(ref):
        seen["ref"] = ref
        return b"from-s3"
    monkeypatch.setattr(bl.object_store, "load", load)
    assert bl.logo_bytes("HDFC") == b"from-s3"
    assert seen["ref"].endswith("/static/bank-logos/HDFC.png")


def test_falls_back_to_the_bundled_copy_when_s3_fails(monkeypatch):
    monkeypatch.setattr(bl.object_store, "enabled", lambda: True)
    def boom(ref):
        raise RuntimeError("s3 down")
    monkeypatch.setattr(bl.object_store, "load", boom)
    assert bl.logo_bytes("HDFC") == (bl.LOGO_DIR / "HDFC.png").read_bytes()


def test_unknown_or_malformed_codes_are_404(monkeypatch):
    monkeypatch.setattr(bl.object_store, "enabled", lambda: False)
    monkeypatch.setattr(bl, "require_tenant", lambda: "t1")
    for code in ("ZZZZ", "../x", "HD1C"):
        with pytest.raises(HTTPException) as e:
            bl.get_bank_logo(code)
        assert e.value.status_code == 404
    assert bl.get_bank_logo("hdfc").media_type == "image/png"
