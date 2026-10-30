"""Merkle anchoring of the audit log, and the proof bundles auditors verify.

What gets anchored
──────────────────
Every audit row that is hashed (`row_hash` set by the chain trigger) and not
yet anchored. Per tenant, the rows' salted leaves form a tenant tree; the
tenant roots form one top tree. The top root is written to the chain in one
transaction, so one anchor covers every tenant at the cost of one transfer.

    leaf      = sha256(0x00 || salt || row_hash)           salt is per tenant, secret
    node      = sha256(0x01 || min(a, b) || max(a, b))     sorted pair: no left/right flags
    top_leaf  = sha256(0x02 || tenant_uuid_16_bytes || tenant_root)

The domain prefixes stop a leaf being passed off as an inner node. The salt
stops anyone guessing a short record from its hash; it is handed over only in
that tenant's proof bundle. Odd levels duplicate their last element.

How it is written
─────────────────
`calldata` mode (default, nothing to deploy): the anchor wallet sends a 0-value
transaction to itself whose input is

    "ONSM" | version 0x01 | merkle_root (32) | from_seq (8, BE) | to_seq (8, BE)

`contract` mode, when ANCHOR_CONTRACT is set: `OnsummAnchor.anchor(root,
fromSeq, toSeq)` (contracts/src/OnsummAnchor.sol).

Without an anchor key or RPC the anchor is still built and stored with status
BUILT, so proof bundles work locally; only the on-chain witness is missing.
The anchor wallet holds gas only; it cannot touch client funds.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

import psycopg2.extras

from ...config import JWT_SECRET
from ..crypto.settings import get_settings
from .hasher import canonical_text, row_hash

logger = logging.getLogger(__name__)

ANCHOR_MAGIC = bytes.fromhex("4f4e534d")  # "ONSM"
ANCHOR_VERSION = 1
# keccak256("anchor(bytes32,uint64,uint64)")[:4], computed at import so it can't drift from the ABI.
_ANCHOR_SELECTOR: Optional[bytes] = None
# Distinct from the payment and review workers' advisory-lock keys.
_ANCHOR_LOCK_KEY = 875_291_004_421


# ── Merkle ──────────────────────────────────────────────────────────────────

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf(salt: bytes, row_hash: bytes) -> bytes:
    return sha256(b"\x00" + bytes(salt) + bytes(row_hash))


def node(a: bytes, b: bytes) -> bytes:
    lo, hi = sorted([bytes(a), bytes(b)])
    return sha256(b"\x01" + lo + hi)


def top_leaf(tenant_id: str, tenant_root: bytes) -> bytes:
    return sha256(b"\x02" + uuid.UUID(str(tenant_id)).bytes + bytes(tenant_root))


EMPTY_ROOT = sha256(b"")


class MerkleBuilder:
    """A tree over a fixed list of leaves, with proofs by leaf index."""

    def __init__(self, leaves: list[bytes]):
        self.levels: list[list[bytes]] = [list(leaves) or [EMPTY_ROOT]]
        while len(self.levels[-1]) > 1:
            level = list(self.levels[-1])
            if len(level) % 2:
                level.append(level[-1])
            self.levels.append([node(level[i], level[i + 1]) for i in range(0, len(level), 2)])

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]

    def proof(self, index: int) -> list[bytes]:
        path: list[bytes] = []
        for level in self.levels[:-1]:
            sibling = index ^ 1
            path.append(level[sibling] if sibling < len(level) else level[index])
            index //= 2
        return path


def verify_proof(leaf_hash: bytes, path: list[bytes], root: bytes) -> bool:
    h = bytes(leaf_hash)
    for sibling in path:
        h = node(h, sibling)
    return h == bytes(root)


# ── Anchor transaction encoding ─────────────────────────────────────────────

def encode_anchor_calldata(root: bytes, from_seq: int, to_seq: int) -> str:
    if len(root) != 32:
        raise ValueError("merkle root must be 32 bytes")
    body = ANCHOR_MAGIC + bytes([ANCHOR_VERSION]) + bytes(root) + int(from_seq).to_bytes(8, "big") \
        + int(to_seq).to_bytes(8, "big")
    return "0x" + body.hex()


def decode_anchor_calldata(data: str) -> Optional[dict]:
    raw = bytes.fromhex(data[2:] if data.startswith("0x") else data)
    if len(raw) != 4 + 1 + 32 + 8 + 8 or raw[:4] != ANCHOR_MAGIC:
        return None
    return {
        "version": raw[4],
        "merkle_root": "0x" + raw[5:37].hex(),
        "from_seq": int.from_bytes(raw[37:45], "big"),
        "to_seq": int.from_bytes(raw[45:53], "big"),
    }


def _anchor_selector() -> bytes:
    global _ANCHOR_SELECTOR
    if _ANCHOR_SELECTOR is None:
        from web3 import Web3
        _ANCHOR_SELECTOR = bytes(Web3.keccak(text="anchor(bytes32,uint64,uint64)"))[:4]
    return _ANCHOR_SELECTOR


def encode_contract_call(root: bytes, from_seq: int, to_seq: int) -> str:
    """ABI-encoded OnsummAnchor.anchor(bytes32,uint64,uint64) call (static args: 3 words)."""
    body = _anchor_selector() + bytes(root) + int(from_seq).to_bytes(32, "big") + int(to_seq).to_bytes(32, "big")
    return "0x" + body.hex()


# ── Share tokens ────────────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _share_key() -> bytes:
    return hmac.new(JWT_SECRET.encode(), b"onsumm-audit-proof-share", hashlib.sha256).digest()


def make_share_token(log_id: str, tenant_id: str) -> str:
    payload = _b64(json.dumps({"l": str(log_id), "t": str(tenant_id)}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_share_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_share_token(token: str) -> Optional[tuple[str, str]]:
    """(log_id, tenant_id) for a valid token, else None. Proof links do not expire:
    an auditor may check a record years later."""
    try:
        payload, sig = token.split(".", 1)
        good = _b64(hmac.new(_share_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(payload))
        return str(uuid.UUID(data["l"])), str(uuid.UUID(data["t"]))
    except Exception:
        return None


# ── Service ─────────────────────────────────────────────────────────────────

def _hex(b) -> Optional[str]:
    return None if b is None else "0x" + bytes(b).hex()


def _tenant_salt(cur, tenant_id: str) -> bytes:
    cur.execute(
        """INSERT INTO tenant_audit_salts (tenant_id, salt) VALUES (%s, gen_random_bytes(32))
           ON CONFLICT (tenant_id) DO NOTHING""",
        (tenant_id,),
    )
    cur.execute("SELECT salt FROM tenant_audit_salts WHERE tenant_id = %s", (tenant_id,))
    return bytes(cur.fetchone()["salt"])


def anchor_view(row: dict) -> dict:
    from ...integrations.chains.evm import explorer_tx_url
    settings = get_settings()
    tx_hash = row.get("tx_hash")
    return {
        "anchor_id": row["id"],
        "merkle_root": _hex(row["merkle_root"]),
        "from_seq": row.get("from_seq"),
        "to_seq": row.get("to_seq"),
        "tenant_count": row.get("tenant_count"),
        "chain_id": row.get("chain_id") or settings.chain_id,
        "mode": row.get("mode") or "calldata",
        "contract": row.get("contract"),
        "tx_hash": tx_hash,
        "explorer": explorer_tx_url(tx_hash) if tx_hash else None,
        "status": row.get("status"),
        "anchored_at": row["anchored_at"].isoformat() if row.get("anchored_at") else None,
    }


@dataclass
class AnchorResult:
    anchor: Optional[dict]
    rows: int


class AnchorService:
    """Builds the Merkle trees for every unanchored hashed row and anchors the root."""

    def anchor_now(self, conn) -> AnchorResult:
        settings = get_settings()
        mode = "contract" if settings.anchor_contract else "calldata"
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS got", (_ANCHOR_LOCK_KEY,))
            if not cur.fetchone()["got"]:
                conn.rollback()
                raise RuntimeError("Another anchor is being built right now; try again in a moment.")
            cur.execute(
                """SELECT log_id, tenant_id::text AS tenant_id, seq, row_hash
                     FROM wf_audit_log
                    WHERE anchor_id IS NULL AND row_hash IS NOT NULL
                    ORDER BY tenant_id, seq"""
            )
            rows = cur.fetchall()
            if not rows:
                conn.rollback()
                return AnchorResult(anchor=None, rows=0)

            by_tenant: dict[str, list[dict]] = {}
            for r in rows:
                by_tenant.setdefault(r["tenant_id"], []).append(r)

            tenant_roots: list[tuple[str, bytes, int, int, int]] = []
            for tenant_id in sorted(by_tenant):
                trows = by_tenant[tenant_id]
                salt = _tenant_salt(cur, tenant_id)
                tree = MerkleBuilder([leaf(salt, bytes(r["row_hash"])) for r in trows])
                tenant_roots.append((tenant_id, tree.root, trows[0]["seq"], trows[-1]["seq"], len(trows)))

            top = MerkleBuilder([top_leaf(t, root) for t, root, *_ in tenant_roots])
            from_seq = min(t[2] for t in tenant_roots)
            to_seq = max(t[3] for t in tenant_roots)

            cur.execute(
                """INSERT INTO audit_anchors (merkle_root, from_seq, to_seq, tenant_count, chain, chain_id,
                                              contract, mode, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BUILT') RETURNING *""",
                (psycopg2.Binary(top.root), from_seq, to_seq, len(tenant_roots), settings.chain,
                 settings.chain_id, settings.anchor_contract or None, mode),
            )
            anchor = cur.fetchone()
            for tenant_id, root, first, last, count in tenant_roots:
                cur.execute(
                    """INSERT INTO audit_anchor_tenant_roots (anchor_id, tenant_id, tenant_root, first_seq,
                                                              last_seq, leaf_count)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (anchor["id"], tenant_id, psycopg2.Binary(root), first, last, count),
                )
            cur.execute(
                "UPDATE wf_audit_log SET anchor_id = %s WHERE log_id = ANY(%s::uuid[])",
                (anchor["id"], [str(r["log_id"]) for r in rows]),
            )
            conn.commit()

        anchor = self._submit(conn, anchor, top.root, from_seq, to_seq, mode, [t[0] for t in tenant_roots])
        return AnchorResult(anchor=anchor_view(anchor), rows=len(rows))

    def _submit(self, conn, anchor: dict, root: bytes, from_seq: int, to_seq: int, mode: str,
                tenant_ids: list[str]) -> dict:
        """Send the anchor transaction. The trees are already stored, so a chain
        failure leaves a usable BUILT anchor and a finding, never lost work."""
        settings = get_settings()
        if not (settings.configured() and settings.anchor_signer_key):
            return anchor
        try:
            from eth_account import Account
            from ...integrations.chains.evm import get_chain
            chain = get_chain()
            if mode == "contract":
                tx_hash = chain.send_tx(settings.anchor_signer_key, settings.anchor_contract,
                                        encode_contract_call(root, from_seq, to_seq))
            else:
                self_address = Account.from_key(settings.anchor_signer_key).address
                tx_hash = chain.send_tx(settings.anchor_signer_key, self_address,
                                        encode_anchor_calldata(root, from_seq, to_seq))
            status, error = "SUBMITTED", None
        except Exception as e:
            logger.exception("[audit-anchor] anchor %s could not be sent", anchor["id"])
            tx_hash, status, error = None, "FAILED", str(e)[:500]

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE audit_anchors SET tx_hash = %s, status = %s, error = %s WHERE id = %s RETURNING *",
                (tx_hash, status, error, anchor["id"]),
            )
            anchor = cur.fetchone()
            if status == "FAILED":
                from ..crypto.findings import raise_finding
                for tenant_id in tenant_ids:
                    raise_finding(cur, tenant_id, "ANCHOR_FAILED", "MEDIUM", "tenant", tenant_id,
                                  f"Audit anchor #{anchor['id']} could not be written on-chain",
                                  {"anchor_id": anchor["id"], "error": error})
            conn.commit()
        return anchor

    def refresh_status(self, conn, anchor: dict) -> dict:
        """SUBMITTED → CONFIRMED / FAILED once the receipt is known."""
        if anchor.get("status") != "SUBMITTED" or not anchor.get("tx_hash"):
            return anchor
        try:
            from ...integrations.chains.evm import get_chain
            receipt = get_chain().get_receipt(anchor["tx_hash"])
        except Exception:
            return anchor
        if receipt is None:
            return anchor
        status = "CONFIRMED" if int(receipt.get("status", 0)) == 1 else "FAILED"
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("UPDATE audit_anchors SET status = %s WHERE id = %s RETURNING *", (status, anchor["id"]))
            anchor = cur.fetchone()
        conn.commit()
        return anchor


class ProofError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ProofService:
    """Builds the ProofBundle of 08_API_CONTRACT §7 for one audit row."""

    def bundle(self, conn, tenant_id: str, log_id: str) -> dict:
        settings = get_settings()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT log_id, tenant_id::text AS tenant_id, seq, table_name, entity_id::text AS entity_id,
                          action::text AS action, user_id::text AS user_id,
                          correlation_id::text AS correlation_id, old_values_jsonb, new_values_jsonb,
                          created_at, prev_hash, row_hash, anchor_id
                     FROM wf_audit_log WHERE log_id = %s::uuid AND tenant_id = %s::uuid""",
                (log_id, tenant_id),
            )
            row = cur.fetchone()
            if not row:
                raise ProofError(404, "Audit record not found")
            if row["row_hash"] is None:
                raise ProofError(409, "This record is not part of the hash chain")
            if row["anchor_id"] is None:
                raise ProofError(409, "This record has not been anchored yet. Anchor now, then download the proof.")

            canonical = canonical_text(cur, log_id)
            if row_hash(bytes(row["prev_hash"]), canonical) != bytes(row["row_hash"]):
                # The stored fields no longer hash to the stored row_hash: edited after it was written.
                raise ProofError(409, "This audit record was altered after it was written; no valid proof exists.")
            cur.execute("SELECT salt FROM tenant_audit_salts WHERE tenant_id = %s", (tenant_id,))
            salt = bytes(cur.fetchone()["salt"])

            cur.execute(
                """SELECT log_id, row_hash FROM wf_audit_log
                    WHERE tenant_id = %s AND anchor_id = %s ORDER BY seq""",
                (tenant_id, row["anchor_id"]),
            )
            trows = cur.fetchall()
            index = next(i for i, r in enumerate(trows) if str(r["log_id"]) == str(row["log_id"]))
            tenant_tree = MerkleBuilder([leaf(salt, bytes(r["row_hash"])) for r in trows])

            cur.execute(
                """SELECT tenant_id::text AS tenant_id, tenant_root FROM audit_anchor_tenant_roots
                    WHERE anchor_id = %s ORDER BY tenant_id""",
                (row["anchor_id"],),
            )
            roots = cur.fetchall()
            top_index = next(i for i, r in enumerate(roots) if r["tenant_id"] == tenant_id)
            top_tree = MerkleBuilder([top_leaf(r["tenant_id"], bytes(r["tenant_root"])) for r in roots])

            cur.execute("SELECT * FROM audit_anchors WHERE id = %s", (row["anchor_id"],))
            anchor = cur.fetchone()

        stored_root = bytes(roots[top_index]["tenant_root"])
        if tenant_tree.root != stored_root or top_tree.root != bytes(anchor["merkle_root"]):
            # The rows under this anchor no longer hash to what was anchored.
            raise ProofError(409, "Stored audit rows no longer match their anchor: the log was altered.")

        token = make_share_token(str(row["log_id"]), tenant_id)
        record = {
            "tenant_id": row["tenant_id"], "seq": row["seq"], "table_name": row["table_name"],
            "entity_id": row["entity_id"], "action": row["action"], "user_id": row["user_id"],
            "correlation_id": row["correlation_id"], "old_values_jsonb": row["old_values_jsonb"],
            "new_values_jsonb": row["new_values_jsonb"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        leaf_hash = leaf(salt, bytes(row["row_hash"]))
        tleaf = top_leaf(tenant_id, tenant_tree.root)
        return {
            "version": 1,
            "log_id": str(row["log_id"]),
            "canonical": canonical,
            "record": record,
            "prev_hash": _hex(row["prev_hash"]),
            "row_hash": _hex(row["row_hash"]),
            "salt": _hex(salt),
            "leaf": _hex(leaf_hash),
            "tenant_id": tenant_id,
            "tenant_path": [_hex(h) for h in tenant_tree.proof(index)],
            "tenant_root": _hex(tenant_tree.root),
            "top_leaf": _hex(tleaf),
            "top_path": [_hex(h) for h in top_tree.proof(top_index)],
            "top_root": _hex(top_tree.root),
            "anchor": anchor_view(anchor),
            "share_url": f"{settings.portal_base_url}/verify?share={token}",
        }
