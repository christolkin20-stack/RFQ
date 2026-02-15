# RFQ User Guide — Quoting Flow (v1)

This is a practical user guide for the current RFQ app flow.

## 1) Core concept
**Quotes Center** is the single source of truth for quote data.
- All pricing inputs eventually end up here.
- Other views (RFQ Planner, Item details, Supplier portal) read from or write to this center.

---

## 2) Main entry points to quote data

### A. RFQ Planner → Quoting Process
Use when managing quotation rounds by supplier.
- Manual path
- Supplier interaction path (supplier portal after approval flow)

### B. Item details (Suppliers & Pricing)
Use for per-item supplier and pricing edits.
- Quick review of supplier prices and status
- Updates should stay aligned with Quotes Center

### C. Quotes (+ NEW QUOTE button)
Use for creating or browsing quote records directly.
- Enter quote number (or let system auto-generate)
- Link quote to supplier and items

### D. Quote Add + Upload
Use for bulk scenarios:
- Upload files
- Multi-upload
- Bulk prices import

---

## 3) Recommended daily process (buyer)
1. Create/update RFQ project
2. Send supplier access links (when needed)
3. Collect draft/submit responses
4. Approve valid responses
5. Verify quote number + supplier mapping in Quotes Center
6. Compare prices and choose winner
7. Notify non-winning suppliers
8. Export/report if needed

---

## 4) Data quality checklist (before award)
- [ ] Each active supplier quote has a valid quote number
- [ ] Supplier name normalization is consistent
- [ ] Currency is set and consistent
- [ ] MOQ / lead time captured
- [ ] Main supplier per item is intentional
- [ ] No duplicate quote lines for same item+supplier+round

---

## 5) Known implementation notes
- In development mode, some auth restrictions are relaxed.
- In production mode, buyer write APIs require authentication.
- Security hardening and API modularization are in progress.

---

## 6) Short glossary
- **Quotes Center**: central quote records and quote lines
- **RFQ Planner**: process/round orchestration
- **Supplier portal**: supplier-facing submission flow
- **Bulk import**: mass price/quote ingestion path

---

## 7) Next documentation upgrades
- Add screenshots per step
- Add role-specific guide (Buyer / Manager / Supplier)
- Add troubleshooting FAQ (common import and submit errors)
