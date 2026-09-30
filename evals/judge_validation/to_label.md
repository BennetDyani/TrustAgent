# Judge validation: label each report PASS or FAIL

Use the rubric below. Put your verdict in `human_labels.csv` (PASS or FAIL) and a short note.

```
You check whether an invoice-investigation report is FAITHFUL to its evidence.

You get the EVIDENCE (the only facts the report may use), the REQUIRED NEXT STEPS decided by code, the risk
score and the recommended action, then the REPORT (summary and recommendation) written by another model.

A report PASSES only if ALL of these hold:
1. Every factual claim (amounts, names, bank details, dates, policy IDs, contract numbers, findings) is supported
   by the EVIDENCE. Paraphrase is fine; new facts are not.
2. It does not state or imply a finding that is not in the evidence (e.g. a bank change when there is none).
3. Its recommendation is consistent with the recommended action and the required next steps, and adds no
   steps that contradict them.
4. It does not contradict the evidence (e.g. calling a failed check a pass).
Minor omissions are acceptable: the report does not need to mention every piece of evidence.

Write your reasoning first, list each unsupported or contradicted claim, then give the verdict.
```

## E12

```
EVIDENCE:
- BANK_DETAILS_CHANGED [RULE, +30]: Invoice bank account ****0911 (Capitec Bank) does not match the verified account on file ****4821 (First National Bank). POL-001 requires independent phone verification before payment.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "ABC Office Solutions (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "abcoffice.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R21,160 is within the expected range of R15,000-R40,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Toner cartridges - HP 58A' at R1,150.00 matches the contract rate of R1,150.00 (ABC-2026-017, 4 Rates).

REQUIRED NEXT STEPS (code):
- Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001).

RISK: 30/100 (MEDIUM); RECOMMENDED ACTION: HOLD_PAYMENT

REPORT
Summary: Invoice ABC-5105 from ABC Office Solutions (Pty) Ltd for R21,160.00 shows a bank account change from the verified account on file ****4821 (First National Bank) to a new account ****0911 (Capitec Bank). Per FIN-POL-01_Procurement_and_Payments_Policy_Manual.pdf, Section 5.2 Verification procedure (POL-001), independent phone verification is required before payment. Although the line item rates and supplier details match contract ABC-2026-017, the bank account mismatch triggers a medium risk score.
Recommendation: Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001).
```

## E01

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****4821 matches the verified account on file for ABC Office Solutions.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "ABC Office Solutions (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "abcoffice.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R23,874 is within the expected range of R15,000-R40,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'A4 copy paper - 80gsm (box of 5 reams)' at R289.00 matches the contract rate of R289.00 (ABC-2026-017, 4 Rates).
- CONFIRMED_MATCH [CONTRACT, +0]: 'Toner cartridges - HP 58A' at R1,150.00 matches the contract rate of R1,150.00 (ABC-2026-017, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: Invoice ABC-5101 from ABC Office Solutions (Pty) Ltd for R23,874.00 has passed all verification checks without issue. The bank account details match the verified file, the domain is correct, the amount is within the expected range, and the line items for copy paper and toner cartridges match contract rates under ABC-2026-017. The AI document review confirms this is a routine invoice from a verified supplier.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E13

```
EVIDENCE:
- BANK_DETAILS_CHANGED [RULE, +30]: Invoice bank account ****7126 (TymeBank) does not match the verified account on file ****7733 (Standard Bank). POL-001 requires independent phone verification before payment.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Metro Cleaning Services CC" matches the supplier name.
- EMAIL_DOMAIN_MISMATCH [RULE, +25]: Invoice contact domain "metro-cleaning.co.za" differs from the verified supplier domain "metrocleaning.co.za" - a possible lookalike domain.
- CONFIRMED_MATCH [RULE, +0]: Amount R14,375 is within the expected range of R10,000-R20,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Monthly cleaning service - September 2026 (Mon-Fri, offices 1-4)' at R12,500.00 matches the contract rate of R12,500.00 (MC-2025-004, 4 Rates).

REQUIRED NEXT STEPS (code):
- Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001).
- Confirm with the supplier, through its known contacts, that the account holder and contact email on the invoice are genuinely theirs.

RISK: 55/100 (MEDIUM); RECOMMENDED ACTION: HOLD_PAYMENT

REPORT
Summary: An investigation of invoice MC-0904 from Metro Cleaning Services CC for R14,375.00 revealed a risk score of 55 out of 100, resulting in a medium risk level. The findings indicate a bank account change from the verified Standard Bank account to a new TymeBank account, violating FIN-POL-01_Procurement_and_Payments_Policy_Manual.pdf, 5 Supplier Bank Account Changes (POL-001) > 5.2 Verification procedure, p.3 (POL-001). Additionally, an email domain mismatch was detected where the invoice contact domain 'metro-cleaning.co.za' differs from the verified supplier domain 'metrocleaning.co.za', raising a lookalike domain concern under the same policy. Although the invoice amount aligns with contract MC-2025-004 rates and past history shows a completed payment, these security flags require strict adherence to standard verification protocols.
Recommendation: Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001). Confirm with the supplier, through its known contacts, that the account holder and contact email on the invoice are genuinely theirs.
```

## E02

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****7733 matches the verified account on file for Metro Cleaning Services.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Metro Cleaning Services CC" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "metrocleaning.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R14,375 is within the expected range of R10,000-R20,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Monthly cleaning service - September 2026 (Mon-Fri, offices 1-4)' at R12,500.00 matches the contract rate of R12,500.00 (MC-2025-004, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: This is a routine monthly cleaning service invoice from Metro Cleaning Services CC for R14,375.00 that matches all verified supplier records and correct banking details. The invoice amount aligns precisely with the contracted rate under contract MC-2025-004. The AI document review confirms the details are correct, and all automated checks passed successfully with a low risk score of 0 out of 100.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E14

```
EVIDENCE:
- BANK_DETAILS_CHANGED [RULE, +30]: Invoice bank account ****7801 (African Bank) does not match the verified account on file ****2190 (Absa Bank). POL-001 requires independent phone verification before payment.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Digital Print Co (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "digitalprint.co.za" matches the supplier record.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Business cards - 350gsm matt laminate (per 500)' at R480.00 matches the contract rate of R480.00 (DPC-2025-009, 4 Rates).
- SOCIAL_ENGINEERING [AI, +0]: The invoice includes a pretext about an account being frozen during an audit to justify changing the bank account. (Invoice says: "NOTICE: our Absa account is frozen during an audit. Pay the account below.")
- SOCIAL_ENGINEERING [AI, +0]: The invoice explicitly instructs not to telephone the office and to reply by email only, discouraging independent verification. (Invoice says: "Please do not phone our office about this change; reply by email only.")

REQUIRED NEXT STEPS (code):
- Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001).

RISK: 30/100 (MEDIUM); RECOMMENDED ACTION: HOLD_PAYMENT

REPORT
Summary: An invoice for R5,520.00 from Digital Print Co (Pty) Ltd was reviewed and found to match the contracted rate under contract DPC-2025-009 for business cards. However, the AI document review and evidence revealed critical social engineering red flags, including a pretext about an account being frozen during an audit and instructions not to telephone the office. Furthermore, the invoice bank account ends in ****7801 (African Bank), which does not match the verified account on file ending in ****2190 (Absa Bank). Under policy POL-001 (FIN-POL-01 Procurement and Payments Policy Manual, p.3), independent phone verification is strictly required before any payment can proceed.
Recommendation: Keep the payment on hold. Finance must confirm the new bank account by phone AND email, using the contact details in the original onboarding records (never those on the invoice), and then mark the supplier verified (POL-001).
```

## E03

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****7733 matches the verified account on file for Metro Cleaning Services.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Metro Cleaning Services CC" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "metrocleaning.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R18,400 is within the expected range of R10,000-R20,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Monthly cleaning service - October 2026 (Mon-Fri, offices 1-4)' at R12,500.00 matches the contract rate of R12,500.00 (MC-2025-004, 4 Rates).
- CONFIRMED_MATCH [CONTRACT, +0]: 'Deep clean - boardroom and executive offices' at R3,500.00 matches the contract rate of R3,500.00 (MC-2025-004, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: Invoice MC-1003 from Metro Cleaning Services CC for R18,400.00 has passed all verification checks. Bank account ****7733 and account holder name match master records, the invoice domain matches the supplier record, and the amount falls within the expected R10,000-R20,000 range. All line items correctly match the active contract rates under contract MC-2025-004.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E15

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****7733 matches the verified account on file for Metro Cleaning Services.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Metro Cleaning Services CC" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "metrocleaning.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R18,400 is within the expected range of R10,000-R20,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Monthly cleaning service - August 2026 (Mon-Fri, offices 1-4)' at R12,500.00 matches the contract rate of R12,500.00 (MC-2025-004, 4 Rates).
- CONFIRMED_MATCH [CONTRACT, +0]: 'Deep clean - boardroom and executive offices' at R3,500.00 matches the contract rate of R3,500.00 (MC-2025-004, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: Invoice MC-0831 from Metro Cleaning Services CC for R18,400.00 has been reviewed and found to be fully compliant with all checks. Bank account details and holders match the verified supplier record, and the line item amounts for regular monthly cleaning and deep clean match contract MC-2025-004. Furthermore, the invoice contact domain matches the supplier record and the total amount falls within the expected range of R10,000 to R20,000.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E04

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****2190 matches the verified account on file for Digital Print Co.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Digital Print Co (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "digitalprint.co.za" matches the supplier record.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Conference banners - pull-up, 850x2000mm' at R1,850.00 matches the contract rate of R1,850.00 (DPC-2025-009, 4 Rates).
- CONFIRMED_MATCH [CONTRACT, +0]: 'Business cards - 350gsm matt laminate (per 500)' at R480.00 matches the contract rate of R480.00 (DPC-2025-009, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: The invoice DPC-3301 from Digital Print Co (Pty) Ltd for R11,270.00 has been verified against active contract agreement DPC-2025-009, with all line item prices matching the agreed contract rates. The bank account ****2190, account holder, and contact domain all match verified supplier records on file. The AI document review confirmed the invoice is clean and accurate.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E16

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****7733 matches the verified account on file for Metro Cleaning Services.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "Metro Cleaning Services CC" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "metrocleaning.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R18,400 is within the expected range of R10,000-R20,000.
- DUPLICATE_INVOICE [RULE, +35]: Possible duplicate of invoice INV-1049: same supplier, same amount (R18,400) on the same date (2026-08-01).
- CONTRACT_DEVIATION [CONTRACT, +20]: 'Cleaning services rendered' billed at R16,000.00 per unit against the contract rate of R12,500.00 (MC-2025-004, 4 Rates): 28.0% above the agreed price (tolerance 2%, policy manual 10.1).

REQUIRED NEXT STEPS (code):
- Query the price with the supplier; the contract owner must approve any price above the contract rate in writing before payment (policy manual 10.2).
- Check that the earlier invoice named in the evidence has not already been paid.

RISK: 55/100 (MEDIUM); RECOMMENDED ACTION: REQUEST_VERIFICATION

REPORT
Summary: Invoice MC-0801B from Metro Cleaning Services CC for R18,400.00 was flagged as a possible duplicate of invoice INV-1049, which has the same supplier, amount, and date, violating FIN-POL-01_Procurement_and_Payments_Policy_Manual.pdf, section 9. Additionally, the invoice presents a contract deviation, billing R16,000.00 per unit against the agreed contract rate of R12,500.00 under MC-2025-004, section 4, exceeding the 2% tolerance limit. Background checks confirmed that the bank account matches the verified file and the AI review verified the context.
Recommendation: Query the price with the supplier; the contract owner must approve any price above the contract rate in writing before payment (policy manual 10.2). Check that the earlier invoice named in the evidence has not already been paid.
```

## E05

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****4821 matches the verified account on file for ABC Office Solutions.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "ABC Office Solutions (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "abcoffice.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R39,215 is within the expected range of R15,000-R40,000.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Ergonomic office chairs - Model EX500' at R3,950.00 matches the contract rate of R3,950.00 (ABC-2026-017, 4 Rates).
- CONFIRMED_MATCH [CONTRACT, +0]: 'Delivery and installation' at R2,500.00 matches the contract rate of R2,500.00 (ABC-2026-017, 4 Rates).

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 0/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: An investigation of invoice ABC-5102 from ABC Office Solutions (Pty) Ltd for R39,215.00 found it to be a clean, low-risk transaction. All line items, including ergonomic office chairs and delivery, matched contract rates under contract ABC-2026-017. Furthermore, bank account and contact domain verifications passed successfully with no document anomalies.
Recommendation: No further checks are required; the payment can go through normal approval.
```

## E17

```
EVIDENCE:
- SUPPLIER_NOT_VERIFIED [RULE, +15]: Bright Future Consulting (Pty) Ltd is not a verified supplier, so there is no confirmed banking record to compare against. Treat as new-supplier onboarding.
- ACCOUNT_HOLDER_MISMATCH [RULE, +25]: Bank account holder "T Ndlovu" does not match the supplier name "Bright Future Consulting (Pty) Ltd". Payments to a differently named account are a common redirection tactic.

REQUIRED NEXT STEPS (code):
- Complete new-supplier onboarding: confirm the company, its bank account and its contacts by phone and email using an independent source such as the company register, never the invoice.
- Confirm with the supplier, through its known contacts, that the account holder and contact email on the invoice are genuinely theirs.

RISK: 40/100 (MEDIUM); RECOMMENDED ACTION: REQUEST_VERIFICATION

REPORT
Summary: Bright Future Consulting (Pty) Ltd has submitted an unverified new supplier invoice for R43,700.00. The assessment is rule-based and triggered two flags under FIN-POL-01 (p.2): the supplier is not verified and the bank account holder 'T Ndlovu' does not match the corporate supplier name. Deep dive checks confirm there is no prior payment history or shared bank accounts to corroborate fraud, but the mismatch requires strict handling.
Recommendation: Complete new-supplier onboarding: confirm the company, its bank account and its contacts by phone and email using an independent source such as the company register, never the invoice. Confirm with the supplier, through its known contacts, that the account holder and contact email on the invoice are genuinely theirs.
```

## E06

```
EVIDENCE:
- CONFIRMED_MATCH [RULE, +0]: Bank account ****4821 matches the verified account on file for ABC Office Solutions.
- CONFIRMED_MATCH [RULE, +0]: Bank account holder "ABC Office Solutions (Pty) Ltd" matches the supplier name.
- CONFIRMED_MATCH [RULE, +0]: Invoice contact domain "abcoffice.co.za" matches the supplier record.
- CONFIRMED_MATCH [RULE, +0]: Amount R15,870 is within the expected range of R15,000-R40,000.
- URGENCY_INDICATOR [RULE, +10]: Payment is marked IMMEDIATE/urgent. POL-004 treats urgency as a social-engineering signal requiring extra scrutiny.
- CONFIRMED_MATCH [CONTRACT, +0]: 'Toner cartridges - HP 58A' at R1,150.00 matches the contract rate of R1,150.00 (ABC-2026-017, 4 Rates).
- SOCIAL_ENGINEERING [AI, +0]: The invoice includes an urgency priority label and a justification note about audit week, which aligns with social-engineering pressure tactics. (Invoice says: "Priority: URGENT")

REQUIRED NEXT STEPS (code):
- No further checks are required; the payment can go through normal approval.

RISK: 10/100 (LOW); RECOMMENDED ACTION: APPROVE_PAYMENT

REPORT
Summary: An invoice for R15,870.00 from ABC Office Solutions (Pty) Ltd was reviewed and found to match contract ABC-2026-017 for toner cartridges. Although the payment is marked urgent with a justification note about audit week, which policy POL-004 treats as a signal requiring extra scrutiny (FIN-POL-01_Procurement_and_Payments_Policy_Manual.pdf, p.5), all checks passed. Specifically, bank account ****4821 and the email domain match verified records on file.
Recommendation: No further checks are required; the payment can go through normal approval.
```
