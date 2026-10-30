"""Matcher, watcher classification, contract-rate and wallet checks, Tally voucher — no network, no DB."""
import xml.etree.ElementTree as ET
from decimal import Decimal

from backend_ocr.app.domain.crypto.recon import (
    ExpectedLeg, TransferRow, contract_overbilling, match_payout, wallet_addresses_in,
)
from backend_ocr.app.domain.crypto.watcher import classify
from backend_ocr.app.domain.crypto.ledger import TallyPaymentVoucherBuilder, voucher_lines
from backend_ocr.app.integrations.chains.evm import TransferLog

USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
VENDOR = "0x7a5C0000000000000000000000000000000Ee91b"
TAX = "0x7A00000000000000000000000000000000000Ta0".replace("Ta0", "ab0")
SAFE = "0x4B1e00000000000000000000000000000000c2d0"
OTHER = "0x9999999999999999999999999999999999999999"


def _t(id_, to, units, conf=3, token=USDC, removed=False):
    return TransferRow(id_, "0xtx", token, to, units, conf, removed)


def test_single_transfer_confirms():
    out = match_payout([ExpectedLeg(VENDOR, 3_600_000_000, "PAYOUT")], [_t("a", VENDOR.lower(), 3_600_000_000)], USDC, 3)
    assert out.decision == "CONFIRMED" and out.matches == {"a": "PAYOUT"}


def test_single_transfer_below_target_is_confirming():
    out = match_payout([ExpectedLeg(VENDOR, 100, "PAYOUT")], [_t("a", VENDOR, 100, conf=1)], USDC, 3)
    assert out.decision == "CONFIRMING"


def test_multisend_two_legs_confirm():
    legs = [ExpectedLeg(VENDOR, 3_420_000_000, "PAYOUT"), ExpectedLeg(TAX, 180_000_000, "TAX")]
    out = match_payout(legs, [_t("a", VENDOR, 3_420_000_000), _t("b", TAX, 180_000_000)], USDC, 3)
    assert out.decision == "CONFIRMED"
    assert out.matches == {"a": "PAYOUT", "b": "TAX"}


def test_one_leg_missing_stays_pending():
    legs = [ExpectedLeg(VENDOR, 3_420_000_000, "PAYOUT"), ExpectedLeg(TAX, 180_000_000, "TAX")]
    out = match_payout(legs, [_t("a", VENDOR, 3_420_000_000)], USDC, 3)
    assert out.decision == "PENDING" and len(out.missing) == 1


def test_wrong_amount_is_mismatch():
    legs = [ExpectedLeg(VENDOR, 3_420_000_000, "PAYOUT"), ExpectedLeg(TAX, 180_000_000, "TAX")]
    out = match_payout(legs, [_t("a", VENDOR, 3_420_000_000), _t("b", TAX, 170_000_000)], USDC, 3)
    assert out.decision == "MISMATCH" and out.mismatches == ["b"]


def test_wrong_token_is_mismatch():
    out = match_payout([ExpectedLeg(VENDOR, 100, "PAYOUT")], [_t("a", VENDOR, 100, token=OTHER)], USDC, 3)
    assert out.decision == "MISMATCH"


def test_reorg_removed_leg_does_not_confirm():
    out = match_payout([ExpectedLeg(VENDOR, 100, "PAYOUT")], [_t("a", VENDOR, 100, removed=True)], USDC, 3)
    assert out.decision == "PENDING"


def _log(frm, to):
    return TransferLog("0xtx", 0, 10, "0xb", USDC, frm, to, 1)


def test_classify_direction_and_unmatched_outbound_source():
    tenant = "t-1"
    watched = {SAFE.lower(): {"tenant_id": tenant}, TAX.lower(): {"tenant_id": tenant}}
    assert classify(_log(SAFE, VENDOR), watched) == (tenant, "OUT")
    assert classify(_log(SAFE, TAX), watched) == (tenant, "OUT")   # tax leg of a payout
    assert classify(_log(OTHER, SAFE), watched) == (tenant, "IN")
    assert classify(_log(OTHER, VENDOR), watched) is None


def test_contract_overbilling_by_line():
    lines = [{"line_number": 1, "product_name": "Brand design retainer - October", "quantity": 1,
              "unit_price": Decimal("4200"), "line_total": Decimal("4200")}]
    rates = [{"item_key": "brand design retainer", "unit_price": Decimal("3600"), "currency_code": "USD"}]
    res = contract_overbilling(lines, rates, Decimal("4200"))
    assert res["over_by"] == "600.00"


def test_contract_overbilling_whole_bill_and_within_rate():
    rates = [{"item_key": "*", "unit_price": Decimal("3600"), "currency_code": "USD"}]
    assert contract_overbilling([], rates, Decimal("4200"))["over_by"] == "600.00"
    assert contract_overbilling([], rates, Decimal("3600")) is None


def test_wallet_addresses_found_in_ocr_text():
    text = '{"text": "Pay to 0x7a5C0000000000000000000000000000000Ee91b or bank"}'
    assert wallet_addresses_in(text) == {VENDOR.lower()}


def test_tally_payment_voucher_is_well_formed_and_balanced():
    lines = voucher_lines(Decimal("3600"), Decimal("3420"), Decimal("180"))
    lines[0]["ledger"] = "Nova Design LLC & Co <US>"
    xml = TallyPaymentVoucherBuilder().build({
        "date": "2026-10-31", "voucher_number": "USDC-NOVA-1042", "party": "Nova Design LLC & Co <US>",
        "narration": "USDC payout. Tx 0xabc https://sepolia.basescan.org/tx/0xabc", "lines": lines,
    })
    root = ET.fromstring(xml)
    entries = root.findall(".//ALLLEDGERENTRIES.LIST")
    amounts = [Decimal(e.find("AMOUNT").text) for e in entries]
    assert sum(amounts) == 0
    assert root.find(".//VOUCHER").get("VCHTYPE") == "Payment"
    assert entries[0].find("ISDEEMEDPOSITIVE").text == "Yes" and amounts[0] == Decimal("-3600.00")


def test_voucher_without_withholding_has_two_lines():
    assert len(voucher_lines(Decimal("10"), Decimal("10"), Decimal("0"))) == 2
