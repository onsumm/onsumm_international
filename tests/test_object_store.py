"""Document storage: S3 when S3_BUCKET is set, local disk when not, and old
rows (local paths, file_data blobs) still readable either way."""
import pytest
from botocore.exceptions import ClientError

from backend_ocr.app.core import object_store as ob


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **extra):
        self.objects[(Bucket, Key)] = (bytes(Body), extra)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture
def s3(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setattr(ob, "S3_BUCKET", "onsumm-assets-dev")
    monkeypatch.setattr(ob, "_client", fake)
    monkeypatch.setattr(ob, "LOCAL_ROOT", tmp_path / "backup")
    return fake


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.setattr(ob, "S3_BUCKET", "")
    monkeypatch.setattr(ob, "LOCAL_ROOT", tmp_path)
    return tmp_path


def test_keys_are_tenant_prefixed_and_cannot_escape():
    assert ob.key("vendor-docs", "t1", "s1", "d1") == "vendor-docs/t1/s1/d1"
    assert ob.key("bill-attachments", "t1", "inv", "../../etc/passwd") == "bill-attachments/t1/inv/passwd"
    assert ob.key("brand-logos", "t1", "..") == "brand-logos/t1/_"


def test_s3_round_trip(s3):
    ref = ob.save(ob.key("vendor-docs", "t1", "s1", "d1"), b"%PDF-1", "application/pdf")
    assert ref == "s3://onsumm-assets-dev/vendor-docs/t1/s1/d1"
    stored, extra = s3.objects[("onsumm-assets-dev", "vendor-docs/t1/s1/d1")]
    assert stored == b"%PDF-1" and extra["ServerSideEncryption"] == "AES256"
    assert ob.load(ref) == b"%PDF-1"

    ob.replace(ref, b"%PDF-2")
    assert ob.load(ref) == b"%PDF-2"

    # A user's delete drops the disk copy; S3 keeps the document.
    ob.drop_local_copy(ref)
    assert not (ob.LOCAL_ROOT / "vendor-docs/t1/s1/d1").exists()
    assert ob.load(ref) == b"%PDF-2"


def test_delete_keeps_a_pre_s3_file_that_is_the_only_copy(s3, tmp_path):
    old = tmp_path / "legacy-doc"
    old.write_bytes(b"only copy")
    ob.drop_local_copy(str(old))
    assert old.read_bytes() == b"only copy"


def test_backup_is_served_when_s3_fails(s3):
    ref = ob.save("bill-attachments/t1/inv/a.pdf", b"%PDF-a")
    assert (ob.LOCAL_ROOT / "bill-attachments/t1/inv/a.pdf").read_bytes() == b"%PDF-a"

    def down(**kw):
        raise ConnectionError("S3 unreachable")
    s3.get_object = down
    assert ob.load(ref) == b"%PDF-a"

    # No backup for this one: the S3 error is not swallowed.
    with pytest.raises(ConnectionError):
        ob.load("s3://onsumm-assets-dev/bill-attachments/t1/inv/other.pdf")


def test_backup_written_even_if_upload_fails(s3):
    def down(**kw):
        raise ConnectionError("S3 unreachable")
    s3.put_object = down
    with pytest.raises(ConnectionError):
        ob.save("vendor-docs/t1/s1/d9", b"%PDF-9")
    assert (ob.LOCAL_ROOT / "vendor-docs/t1/s1/d9").read_bytes() == b"%PDF-9"


def test_invoices_skip_the_disk_backup(s3):
    # Their backup is the file_data column.
    ob.save("invoices/t1/u1/a.pdf", b"%PDF", local_backup=False)
    assert not (ob.LOCAL_ROOT / "invoices/t1/u1/a.pdf").exists()


def test_legacy_local_path_still_reads_with_s3_on(s3, tmp_path):
    old = tmp_path / "doc"
    old.write_bytes(b"old")
    assert ob.load(str(old)) == b"old"
    assert ob.load(str(tmp_path / "gone")) is None
    assert ob.load(None) is None


def test_local_mode_writes_under_local_root(local):
    ref = ob.save(ob.key("bill-attachments", "t1", "inv", "a.xlsx"), b"x")
    assert ref == str(local / "bill-attachments/t1/inv/a.xlsx")
    assert ob.load(ref) == b"x"
    # With S3 off the disk file is the only copy, so a delete keeps it.
    ob.drop_local_copy(ref)
    assert ob.load(ref) == b"x"


def test_local_path_downloads_s3_object_for_pymupdf(s3):
    ref = ob.save("vendor-docs/t1/s1/d1", b"%PDF-contract")
    with ob.local_path(ref, suffix=".pdf") as p:
        assert p.suffix == ".pdf" and p.read_bytes() == b"%PDF-contract"
    assert not p.exists()
    with pytest.raises(FileNotFoundError):
        with ob.local_path("s3://onsumm-assets-dev/missing"):
            pass


class FakeCursor:
    def __init__(self, row):
        self.row, self.sql, self.args = row, None, None

    def execute(self, sql, args):
        self.sql, self.args = sql, args

    def fetchone(self):
        return self.row


def test_invoice_pdf_prefers_s3_then_db_blob(s3):
    ref = ob.save("invoices/t1/u1/a.pdf", b"%PDF-s3")
    cur = FakeCursor((ref, None))
    assert ob.invoice_pdf(cur, "u1", "t1") == b"%PDF-s3"
    assert "tenant_id" in cur.sql and cur.args == ["u1", "t1"]

    assert ob.invoice_pdf(FakeCursor(("uploads/a.pdf", memoryview(b"%PDF-db"))), "u2") == b"%PDF-db"
    # S3 object gone but the DB copy not yet dropped: still served.
    assert ob.invoice_pdf(FakeCursor(("s3://onsumm-assets-dev/nope", b"%PDF-db")), "u3") == b"%PDF-db"
    assert ob.invoice_pdf(FakeCursor(None), "u4") is None


# ── Signed links: documents never get a public URL ──────────────────────────

@pytest.fixture
def real_s3_client(monkeypatch):
    """A real boto3 client with throwaway keys: presigning is offline."""
    import boto3
    monkeypatch.setattr(ob, "S3_BUCKET", "onsumm-assets-dev")
    monkeypatch.setattr(ob, "FILES_CDN_DOMAIN", "")
    monkeypatch.setattr(ob, "_client", boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="secret"))


def test_s3_link_is_signed_and_short_lived(real_s3_client, monkeypatch):
    monkeypatch.setattr(ob, "SIGNED_URL_TTL", 300)
    url = ob.signed_url("s3://onsumm-assets-dev/invoices/t1/u1/a.pdf")
    assert "onsumm-assets-dev" in url and "invoices/t1/u1/a.pdf" in url
    assert "X-Amz-Signature=" in url and "X-Amz-Expires=300" in url


def test_cloudfront_link_when_cdn_is_configured(real_s3_client, monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "cf.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    monkeypatch.setattr(ob, "FILES_CDN_DOMAIN", "files.example.net")
    monkeypatch.setattr(ob, "CLOUDFRONT_KEY_ID", "K123")
    monkeypatch.setattr(ob, "CLOUDFRONT_PRIVATE_KEY_PATH", str(pem))
    monkeypatch.setattr(ob, "_cf_signer", None)

    url = ob.signed_url("s3://onsumm-assets-dev/vendor-docs/t1/s1/a b.pdf")
    assert url.startswith("https://files.example.net/vendor-docs/t1/s1/a%20b.pdf?")
    assert "Signature=" in url and "Key-Pair-Id=K123" in url and "Expires=" in url
    # A row from another bucket is never routed through this CDN.
    assert "files.example.net" not in ob.signed_url("s3://onsumm-assets/x")


def test_no_link_for_a_document_not_in_s3(real_s3_client, tmp_path):
    assert ob.signed_url(str(tmp_path / "legacy.pdf")) is None
    assert ob.signed_url(None) is None
    assert ob.signed_link_response(str(tmp_path / "legacy.pdf")) is None


def test_link_response_shape(real_s3_client):
    import json
    res = ob.signed_link_response("s3://onsumm-assets-dev/invoices/t1/u1/a.pdf")
    assert res.media_type == ob.SIGNED_LINK_TYPE
    assert res.headers["cache-control"] == "no-store"
    assert "X-Amz-Signature=" in json.loads(res.body)["url"]


def test_signing_failure_falls_back_to_streaming(real_s3_client, monkeypatch):
    monkeypatch.setattr(ob, "FILES_CDN_DOMAIN", "files.example.net")
    monkeypatch.setattr(ob, "CLOUDFRONT_KEY_ID", "K123")
    monkeypatch.setattr(ob, "CLOUDFRONT_PRIVATE_KEY_PATH", "/nonexistent.pem")
    monkeypatch.setattr(ob, "_cf_signer", None)
    assert ob.signed_link_response("s3://onsumm-assets-dev/invoices/t1/u1/a.pdf") is None
