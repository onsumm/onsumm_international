"""
tally_client.py — HTTP XML API calls to Tally Prime
"""
import os
import logging

import requests
from dotenv import load_dotenv

load_dotenv()

_HOST       = os.getenv("TALLY_HOST", "localhost")
_PORT       = os.getenv("TALLY_PORT", "9000")
TALLY_URL   = f"http://{_HOST}:{_PORT}"
TALLY_COMPANY = os.getenv("TALLY_COMPANY_NAME", "")

logger  = logging.getLogger(__name__)
HEADERS = {"Content-Type": "application/xml"}
TIMEOUT = 30


def _company_tag():
    return f"<SVCNAME>{TALLY_COMPANY}</SVCNAME>" if TALLY_COMPANY else ""


def post_xml(xml_body):
    try:
        resp = requests.post(TALLY_URL, data=xml_body.encode("utf-8"),
                             headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            f"Cannot reach Tally at {TALLY_URL}. "
            "Make sure Tally Prime is running with ODBC/XML server enabled."
        )


def fetch_ledgers():
    return post_xml(f"""
    <ENVELOPE>
      <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{_company_tag()}</HEADER>
      <BODY><EXPORTDATA><REQUESTDESC>
        <REPORTNAME>List of Accounts</REPORTNAME>
        <STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>
      </REQUESTDESC></EXPORTDATA></BODY>
    </ENVELOPE>""")


def fetch_vouchers(from_date, to_date, vch_type=""):
    type_filter = f"<VOUCHERTYPENAME>{vch_type}</VOUCHERTYPENAME>" if vch_type else ""
    primary = post_xml(f"""
    <ENVELOPE>
      <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{_company_tag()}</HEADER>
      <BODY><EXPORTDATA><REQUESTDESC>
        <REPORTNAME>Voucher Register</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          <SVFROMDATE>{from_date}</SVFROMDATE>
          <SVTODATE>{to_date}</SVTODATE>
          {type_filter}
        </STATICVARIABLES>
      </REQUESTDESC></EXPORTDATA></BODY>
    </ENVELOPE>""")

    # Some Tally setups return an empty Voucher Register for API calls.
    # Fallback to Day Book export for the same date range.
    if "<VOUCHER" not in primary.upper():
        logger.warning("Voucher Register returned no vouchers, trying Day Book fallback")
        fallback = post_xml(f"""
        <ENVELOPE>
          <HEADER><TALLYREQUEST>Export Data</TALLYREQUEST>{_company_tag()}</HEADER>
          <BODY><EXPORTDATA><REQUESTDESC>
            <REPORTNAME>Day Book</REPORTNAME>
            <STATICVARIABLES>
              <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
              <SVFROMDATE>{from_date}</SVFROMDATE>
              <SVTODATE>{to_date}</SVTODATE>
              {type_filter}
            </STATICVARIABLES>
          </REQUESTDESC></EXPORTDATA></BODY>
        </ENVELOPE>""")
        if "<VOUCHER" in fallback.upper():
            return fallback

    return primary


def import_xml(payload_xml):
    return post_xml(f"""
    <ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST>{_company_tag()}</HEADER>
      <BODY><IMPORTDATA>
        <REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME></REQUESTDESC>
        <REQUESTDATA>{payload_xml}</REQUESTDATA>
      </IMPORTDATA></BODY>
    </ENVELOPE>""")
