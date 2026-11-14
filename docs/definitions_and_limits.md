# Construct definitions and inference limits

- Geography: MS, AL, LA, AR, TN only; purposively bounded.
- Unit: public water system, uniquely identified by PWSID.
- Main cohort: active CWS/NTNCWS in 2026Q3 source extract, not confirmed historical 2024-active systems.
- Outcome: binary indicator of at least one health-based recorded violation with `NON_COMPL_PER_BEGIN_DATE` in CY2024, using deduplicated `(PWSID, VIOLATION_ID)` events.
- Alternative indicator: exclude **only** `VIOLATION_CODE=2E` from the health-based 2024-onset set, while preserving other health-based codes.
- `4G` is not removed; it was classified as non-health-based in this extraction.
- Comparator: not official EPA annual uncorrected-violation prevalence. Do not infer actual lead exposure, contamination or regulatory causality.
- State ranks: ascending rates in **these five states only**, not U.S. national ranks or intrinsic quality ranks.
- Additional cohorts: eligibility proxies; first database report is not operating start, and deactivation dates do not fully reconstruct historical populations.
- The 23,109 unkeyed rows in union candidate cohort were classified as enforcement-fields-only, not as undated violation events. This does not rule out unreported violations.
- The extreme all-undated-to-2024 stress was judged unsuitable for substantive conclusions.
