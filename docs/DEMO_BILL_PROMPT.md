# Demo Bill Generation Prompt

Use the prompt below with any AI (ChatGPT, Claude, Gemini) to generate realistic Indian tax invoice PDFs for testing the Onsumm.Ai upload flow.

---

## Demo Vendors for "Demo Company Pvt Ltd"

| # | Vendor Name | GSTIN | Category |
|---|-------------|-------|----------|
| 1 | Airtel Broadband Pvt Ltd | 07AAACA2173B1ZN | Internet & Telecom |
| 2 | Amazon Web Services India Pvt Ltd | 07AANCA2608P1ZX | Cloud & Hosting |
| 3 | Hanto Workspace Technologies Pvt Ltd | 29AAACH5684N1ZR | Office Rent |
| 4 | Intelliroot Technologies Pvt Ltd | 29AABCI5876Q1ZP | Software Services |
| 5 | SME Travels Manjunath | 29ABCFS4329M1ZK | Travel & Transport |
| 6 | Swiggy for Business | 29AABCS4773D1ZJ | Meals & Catering |
| 7 | Zomato for Enterprise | 27AABCZ8980C1ZP | Meals & Catering |
| 8 | Jio Business Solutions | 27AARJA0997P1ZN | Mobile & Telecom |
| 9 | WeWork India Management Pvt Ltd | 27AABCW5656G1ZD | Co-working Space |
| 10 | Razorpay Software Pvt Ltd | 29AABCR4100R1ZK | Payment Gateway Fees |

**Buyer (Bill To):**
- Company: Demo Company Pvt Ltd
- GSTIN: 29AACCD1234F1ZP
- Address: 4th Floor, Tower B, Prestige Tech Park, Marathahalli, Bengaluru - 560103, Karnataka
- PAN: AACCD1234F

---

## AI Prompt — Copy & Paste

```
Generate 10 realistic Indian Tax Invoice PDFs (one per vendor) for testing a bill management system.

BUYER (same for all invoices):
- Company: Demo Company Pvt Ltd
- GSTIN: 29AACCD1234F1ZP
- Address: 4th Floor, Tower B, Prestige Tech Park, Marathahalli, Bengaluru - 560103, Karnataka
- PAN: AACCD1234F

Generate one invoice from each of these vendors:

1. AIRTEL BROADBAND PVT LTD (GSTIN: 07AAACA2173B1ZN, Delhi)
   - Service: Leased Line Internet — 100 Mbps, Monthly
   - HSN/SAC: 998426
   - Amount: ₹12,500 + 18% GST (IGST since interstate)
   - Invoice Date: 01-Apr-2026, Due: 15-Apr-2026

2. AMAZON WEB SERVICES INDIA PVT LTD (GSTIN: 07AANCA2608P1ZX, Delhi)
   - Service: AWS EC2 + S3 Cloud Usage — April 2026
   - HSN/SAC: 998319
   - Amount: ₹38,420 + 18% GST (IGST)
   - Invoice Date: 03-Apr-2026, Due: 20-Apr-2026

3. HANTO WORKSPACE TECHNOLOGIES PVT LTD (GSTIN: 29AAACH5684N1ZR, Bengaluru)
   - Service: Office Space Rent — HSR Layout, April 2026
   - HSN/SAC: 997212
   - Amount: ₹85,000 + 9% CGST + 9% SGST (intrastate)
   - Invoice Date: 01-Apr-2026, Due: 05-Apr-2026

4. INTELLIROOT TECHNOLOGIES PVT LTD (GSTIN: 29AABCI5876Q1ZP, Bengaluru)
   - Service: Annual SaaS Subscription — HR Software
   - HSN/SAC: 998314
   - Amount: ₹1,20,000 + 9% CGST + 9% SGST
   - Invoice Date: 05-Apr-2026, Due: 19-Apr-2026

5. SME TRAVELS MANJUNATH (GSTIN: 29ABCFS4329M1ZK, Bengaluru)
   - Service: Employee Cab Services — April 2026 (50 trips)
   - HSN/SAC: 996411
   - Amount: ₹24,500 + 5% CGST + 5% SGST
   - Invoice Date: 30-Mar-2026, Due: 14-Apr-2026

6. SWIGGY FOR BUSINESS (GSTIN: 29AABCS4773D1ZJ, Bengaluru)
   - Service: Corporate Meal Credits — March 2026
   - HSN/SAC: 996331
   - Amount: ₹18,750 + 5% CGST + 5% SGST
   - Invoice Date: 31-Mar-2026, Due: 15-Apr-2026

7. ZOMATO FOR ENTERPRISE (GSTIN: 27AABCZ8980C1ZP, Mumbai)
   - Service: Team Lunch Orders — March 2026
   - HSN/SAC: 996331
   - Amount: ₹9,840 + 18% GST (IGST)
   - Invoice Date: 31-Mar-2026, Due: 10-Apr-2026

8. JIO BUSINESS SOLUTIONS (GSTIN: 27AARJA0997P1ZN, Mumbai)
   - Service: Corporate SIM Plan — 20 connections, April 2026
   - HSN/SAC: 998422
   - Amount: ₹7,200 + 18% GST (IGST)
   - Invoice Date: 01-Apr-2026, Due: 15-Apr-2026

9. WEWORK INDIA MANAGEMENT PVT LTD (GSTIN: 27AABCW5656G1ZD, Mumbai)
   - Service: Hot Desk Membership — 5 seats, April 2026
   - HSN/SAC: 997212
   - Amount: ₹55,000 + 18% GST (IGST)
   - Invoice Date: 01-Apr-2026, Due: 07-Apr-2026

10. RAZORPAY SOFTWARE PVT LTD (GSTIN: 29AABCR4100R1ZK, Bengaluru)
    - Service: Payment Gateway Transaction Fees — March 2026
    - HSN/SAC: 997158
    - Amount: ₹14,320 + 9% CGST + 9% SGST
    - Invoice Date: 31-Mar-2026, Due: 14-Apr-2026

FORMAT REQUIREMENTS for each PDF:
- Standard Indian Tax Invoice layout
- Include: Invoice Number (e.g. INV-2026-0042), Invoice Date, Due Date, Place of Supply
- Seller block: Name, Address, GSTIN, PAN, State
- Buyer block: Demo Company Pvt Ltd details as above
- Line items table: #, Description, HSN/SAC, Qty, Unit, Rate, Amount
- Tax breakdown table: Taxable Amount, CGST%, CGST Amt, SGST%, SGST Amt, IGST%, IGST Amt
- Summary: Sub Total, Total Tax, Grand Total (in INR)
- Amount in words (Indian format)
- Bank details section: Account No, IFSC, Bank Name, Branch
- Authorized signatory box at bottom right

Make each invoice look like it comes from the respective company with their own branding/letterhead style.
```

---

## Quick Test Flow

Once you have the 10 PDFs, test each role:

| Role | Username | Password | What to test |
|------|----------|----------|--------------|
| **Member** | `member` | `member123` | Upload PDFs → OCR extracts data → Submit |
| **Approver** | `approver` | `approver123` | Can only see upload; cannot view Bills/Vendors |
| **Accountant** | `accountant` | `accountant123` | View Bills, Vendors; upload invoices |
| **Finance Controller** | `finance` | `finance123` | View Bills, Vendors, payments |
| **Auditor** | `auditor` | `auditor123` | View-only: Bills, Vendors — cannot upload |
| **Admin** | `admin` | `admin123` | Full access including Settings → Roles |
