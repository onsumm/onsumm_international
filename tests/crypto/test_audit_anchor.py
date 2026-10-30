"""Merkle trees, proofs, anchor calldata and the hash-chain check — no network, no DB."""
import hashlib
import json
import uuid

import pytest

from backend_ocr.app.domain.audit import anchor as A
from backend_ocr.app.domain.audit.hasher import ZERO_HASH, row_hash, verify_rows


def _h(i: int) -> bytes:
    return hashlib.sha256(f"row-{i}".encode()).digest()


SALT = bytes(range(32))


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 13])
def test_every_leaf_proves_against_root(n):
    leaves = [A.leaf(SALT, _h(i)) for i in range(n)]
    tree = A.MerkleBuilder(leaves)
    for i, lf in enumerate(leaves):
        assert A.verify_proof(lf, tree.proof(i), tree.root)


def test_tampered_leaf_or_path_fails():
    leaves = [A.leaf(SALT, _h(i)) for i in range(5)]
    tree = A.MerkleBuilder(leaves)
    path = tree.proof(2)
    assert not A.verify_proof(A.leaf(SALT, _h(99)), path, tree.root)
    bad = list(path)
    bad[0] = hashlib.sha256(b"x").digest()
    assert not A.verify_proof(leaves[2], bad, tree.root)


def test_salt_changes_leaf_and_domain_prefixes_differ():
    assert A.leaf(SALT, _h(1)) != A.leaf(bytes(32), _h(1))
    a, b = _h(1), _h(2)
    # A leaf can never be mistaken for an inner node with the same input bytes.
    assert A.leaf(a[:0], a + b) != A.node(a, b)


def test_node_is_order_independent():
    a, b = _h(1), _h(2)
    assert A.node(a, b) == A.node(b, a)


def test_empty_tree_has_fixed_root():
    assert A.MerkleBuilder([]).root == hashlib.sha256(b"").digest()


def test_top_leaf_binds_tenant():
    root = _h(7)
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    assert A.top_leaf(t1, root) != A.top_leaf(t2, root)
    assert A.top_leaf(t1, root) == hashlib.sha256(b"\x02" + uuid.UUID(t1).bytes + root).digest()


def test_two_level_proof_end_to_end():
    tenants = sorted(str(uuid.uuid4()) for _ in range(3))
    trees = {t: A.MerkleBuilder([A.leaf(SALT, _h(i + k)) for i in range(4)]) for k, t in enumerate(tenants)}
    top = A.MerkleBuilder([A.top_leaf(t, trees[t].root) for t in tenants])
    t = tenants[1]
    lf = A.leaf(SALT, _h(2 + 1))
    assert A.verify_proof(lf, trees[t].proof(2), trees[t].root)
    assert A.verify_proof(A.top_leaf(t, trees[t].root), top.proof(1), top.root)


def test_anchor_calldata_round_trip():
    root = _h(3)
    data = A.encode_anchor_calldata(root, 17, 412)
    assert data.startswith("0x4f4e534d01")
    assert len(bytes.fromhex(data[2:])) == 53
    decoded = A.decode_anchor_calldata(data)
    assert decoded == {"version": 1, "merkle_root": "0x" + root.hex(), "from_seq": 17, "to_seq": 412}
    assert A.decode_anchor_calldata("0xdeadbeef") is None


def test_contract_call_encoding():
    data = A.encode_contract_call(_h(3), 1, 2)
    raw = bytes.fromhex(data[2:])
    from web3 import Web3
    assert raw[:4] == bytes(Web3.keccak(text="anchor(bytes32,uint64,uint64)"))[:4]
    assert len(raw) == 4 + 32 * 3
    assert int.from_bytes(raw[-32:], "big") == 2


def test_share_token_round_trip_and_tamper():
    log_id, tenant = str(uuid.uuid4()), str(uuid.uuid4())
    token = A.make_share_token(log_id, tenant)
    assert A.read_share_token(token) == (log_id, tenant)
    payload, sig = token.split(".")
    other = A._b64(json.dumps({"l": str(uuid.uuid4()), "t": tenant}, separators=(",", ":")).encode())
    assert A.read_share_token(f"{other}.{sig}") is None
    assert A.read_share_token("garbage") is None


def _chain(n):
    rows, prev = [], ZERO_HASH
    for i in range(1, n + 1):
        canonical = json.dumps({"seq": i, "x": i})
        h = row_hash(prev, canonical)
        rows.append((i, prev, h, canonical))
        prev = h
    return rows


def test_verify_rows_ok():
    assert verify_rows(_chain(5)) == {"ok": True, "checked": 5, "first_broken_seq": None}


def test_verify_rows_detects_edit_delete_reorder():
    rows = _chain(5)
    edited = list(rows)
    s, p, h, c = edited[2]
    edited[2] = (s, p, h, c.replace('"x": 3', '"x": 4'))
    assert verify_rows(edited)["first_broken_seq"] == 3

    deleted = rows[:2] + rows[3:]
    assert verify_rows(deleted)["first_broken_seq"] == 3

    swapped = [rows[0], rows[2], rows[1]] + rows[3:]
    assert verify_rows(swapped)["first_broken_seq"] == 2
