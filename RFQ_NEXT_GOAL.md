# RFQ Next Goal (Execution Plan)

## Goal: production-ready internal release candidate (RC1)

## Priority path
1. **Stabilize release process** ✅
   - preflight script in place (`scripts/preflight.sh`)
2. **Close functional gaps**
   - verify end-to-end supplier roundtrip on a real project sample
   - verify quote export/import loop on at least 2 suppliers and 10+ items
3. **Operational readiness**
   - create `.env` from `.env.example` on target host
   - run migration + preflight before each deployment
4. **Release checkpoint**
   - freeze commits
   - run preflight
   - tag RC (`v0.1.0-rc1`)

## Done definition for RC1
- preflight green
- supplier portal flow tested on real sample
- quote upsert + export-to-item verified
- no blocker issues in auth/security behavior
