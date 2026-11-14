#!/usr/bin/env python3
"""EPA SDWIS five-state manuscript robustness checks (standard-library only).

Uses original quarterly EPA SDWA ZIP, no downloads, no inference or data imputation.
Checks (1) possible influence of violations with unparseable onset dates and
(2) sensitivity to the retrospective 2026-active cohort using documented
FIRST_REPORTED_DATE and PWS_DEACTIVATION_DATE proxies. Never calls either proxy
actual 2023/2024 activity status. 2024 flags count newly starting violations only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

STATES = ("MS", "AL", "LA", "AR", "TN")
PWS_TYPES = {"CWS", "NTNCWS"}
BASE = "SDWA_PUB_WATER_SYSTEMS.csv"
VIOS = "SDWA_VIOLATIONS_ENFORCEMENT.csv"


def parse_date(v):
    v = (v or "").strip().split("T")[0].split(" ")[0]
    if not v or v.upper() in {"NULL", "N/A", "NA"}:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def source_table(z, target):
    found = [name for name in z.namelist() if name.replace("\\", "/").split("/")[-1].upper() == target.upper()]
    if len(found) != 1:
        raise RuntimeError(f"Expected exactly one {target} in EPA ZIP, found {len(found)}")
    return found[0]


def read_rows(z, src, required):
    with z.open(src) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(text)
        if reader.fieldnames is None:
            raise ValueError(f"No CSV header: {src}")
        fields = {(x or "").strip().upper(): x for x in reader.fieldnames}
        missing = set(required) - fields.keys()
        if missing:
            raise ValueError(f"Missing required columns in {src}: {sorted(missing)}")
        needed_fields = {key: fields[key] for key in required}
        for row in reader:
            yield {key: (row.get(real) or "").strip() for key, real in needed_fields.items()}


def state_counts_and_rates(cohort, records, mode="known", undated_unkeyed=None):
    """mode=known or all_undated_2024 (extreme stress scenario, NOT observed fact)."""
    d = {s: {"eligible": 0, "y23": 0, "raw": 0, "adjusted": 0, "any2e": 0, "losing": 0} for s in STATES}
    undated_by_system = defaultdict(set)
    dated23 = defaultdict(set)
    dated24 = defaultdict(set)
    for (pid, _vid), rec in records.items():
        if pid not in cohort:
            continue
        # Only verified health-based codes affect the observed lower counts.
        # Unknown health flags on undated rows are potentially health-based
        # in the deliberately extreme 2024 stress scenario.
        if rec["year"] == 2023 and rec["hb"] == "Y":
            dated23[pid].add(rec["code"])
        elif rec["year"] == 2024 and rec["hb"] == "Y":
            dated24[pid].add(rec["code"])
        elif rec["year"] is None and rec["hb"] in {"Y", "?"}:
            undated_by_system[pid].add(rec["code"])
    for pid, sysrec in cohort.items():
        s = sysrec["state"]
        c = d[s]
        c["eligible"] += 1
        codes23 = dated23.get(pid, set())
        codes24 = dated24.get(pid, set())
        if mode == "all_undated_2024":
            codes24 = codes24 | undated_by_system.get(pid, set())
            extra = (undated_unkeyed or {}).get(pid)
            if extra and extra["raw"]:
                # A sentinel is NOT a violation identifier.  It only
                # marks that this PWS could have had at least one additional
                # 2024 health-based flag.  No invented ID enters records.
                codes24 = codes24 | ({"UNKEYED_NON_2E_POSSIBLE"} if extra["adjusted"] else {"2E"})
        if codes23:
            c["y23"] += 1
        if codes24:
            c["raw"] += 1
        if codes24 - {"2E"}:
            c["adjusted"] += 1
        if "2E" in codes24:
            c["any2e"] += 1
        if codes24 and not codes24 - {"2E"}:
            c["losing"] += 1
    rates_raw = {s: c["raw"] / c["eligible"] for s, c in d.items() if c["eligible"]}
    rates_adj = {s: c["adjusted"] / c["eligible"] for s, c in d.items() if c["eligible"]}
    def rank(rates):
        return {s: 1 + sum(v < x for v in rates.values()) for s, x in rates.items()}
    raw_ranks, adj_ranks = rank(rates_raw), rank(rates_adj)
    out = []
    for s in STATES:
        c = d[s]
        if not c["eligible"]:
            continue
        out.append({"state": s, "eligible": c["eligible"], "onset2023": c["y23"],
                    "raw2024": c["raw"], "adjusted2024": c["adjusted"],
                    "flag_lost2024": c["losing"], "any2e2024": c["any2e"],
                    "raw_rate_pct": round(100 * rates_raw[s], 6),
                    "adjusted_rate_pct": round(100 * rates_adj[s], 6),
                    "raw_rank": raw_ranks[s], "adjusted_rank": adj_ranks[s]})
    rev = 0
    for a, b in combinations(rates_raw, 2):
        dr = rates_raw[a] - rates_raw[b]
        da = rates_adj[a] - rates_adj[b]
        if dr * da < 0:
            rev += 1
    return out, rev


def build_cohorts(pws):
    cutoff = date(2023, 12, 31)
    e2024 = date(2024, 12, 31)
    groups = {"A_2026_snapshot_active": {},
              "B_active_first_reported_by_2023": {},
              "C_first_reported_by_2023_not_deactivated_by_2024_proxy": {}}
    issues = Counter()
    for pid, r in pws.items():
        active = r["activity"] == "A"
        first = r["first"]
        deact = r["deact"]
        if not first:
            issues["systems_missing_or_invalid_first_report_date"] += 1
        if r["deact_raw"] and not deact:
            issues["systems_invalid_deactivation_date"] += 1
        if active and deact and deact <= e2024:
            issues["active_snapshot_but_old_deactivation_date"] += 1
        if active:
            groups["A_2026_snapshot_active"][pid] = r
        if active and first and first <= cutoff:
            groups["B_active_first_reported_by_2023"][pid] = r
        qualifying_inactive = (not active and deact and deact > e2024)
        consistent_active = active and not (deact and deact <= e2024)
        if first and first <= cutoff and (qualifying_inactive or consistent_active):
            groups["C_first_reported_by_2023_not_deactivated_by_2024_proxy"][pid] = r
    if not groups["A_2026_snapshot_active"]:
        raise RuntimeError("No snapshot-active systems: verify original cohort/snapshot")
    return groups, issues


def aggregate_unknown(cohort, records, known_rows, stressed_rows, undated_unkeyed, unkeyed_stats):
    by_id = defaultdict(set)
    undated_unique = Counter()
    undated_health_unique = Counter()
    undated_2e_unique = Counter()
    undated_non2e_health_unique = Counter()
    for (pid, vid), rec in records.items():
        if pid not in cohort or rec["year"] is not None:
            continue
        s = cohort[pid]["state"]
        undated_unique[s] += 1
        if rec["hb"] == "Y":
            undated_health_unique[s] += 1
            if rec["code"] == "2E":
                undated_2e_unique[s] += 1
            else:
                undated_non2e_health_unique[s] += 1
        by_id[pid].add((rec["code"], rec["hb"]))
    known = {r["state"]: r for r in known_rows}
    stressed = {r["state"]: r for r in stressed_rows}
    output = []
    for s in STATES:
        if s not in known:
            continue
        k, u = known[s], stressed[s]
        output.append({"state": s, "eligible": k["eligible"],
                       "unique_undated_violations": undated_unique[s],
                       "unique_undated_health_based": undated_health_unique[s],
                       "unique_undated_health_2E": undated_2e_unique[s],
                       "unique_undated_health_non2E": undated_non2e_health_unique[s],
                       "undated_rows_without_violation_id": unkeyed_stats[s]["rows_without_id"],
                       "undated_rows_without_id_health_Y": unkeyed_stats[s]["rows_health_Y"],
                       "undated_rows_without_id_health_unknown": unkeyed_stats[s]["rows_health_unknown"],
                       "systems_with_undated_rows_without_id": sum(1 for pid in undated_unkeyed if pid in cohort and pid[:2] == s),
                       "systems_with_any_undated_violation": sum(1 for pid in set(by_id) | set(undated_unkeyed) if pid in cohort and pid[:2] == s),
                       "observed_raw2024": k["raw2024"], "stress_raw2024_all_undated_as_2024": u["raw2024"],
                       "observed_adjusted2024": k["adjusted2024"],
                       "stress_adjusted2024_all_undated_as_2024": u["adjusted2024"],
                       "added_raw_flags_stress": u["raw2024"] - k["raw2024"],
                       "added_adjusted_flags_stress": u["adjusted2024"] - k["adjusted2024"],
                       "observed_rank_raw": k["raw_rank"], "stress_rank_raw": u["raw_rank"],
                       "observed_rank_adjusted": k["adjusted_rank"],
                       "stress_rank_adjusted": u["adjusted_rank"]})
    return output


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def run(archive, baseline, outdir):
    baseline_meta = json.loads((baseline / "audit.json").read_text(encoding="utf-8-sig"))
    expected_hash = baseline_meta.get("input_zip_sha256")
    if not expected_hash:
        raise RuntimeError("Original audit.json missing input ZIP SHA256. Cannot verify same input.")
    h = hashlib.sha256()
    with archive.open("rb") as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(b)
    got_hash = h.hexdigest()
    if got_hash.lower() != expected_hash.lower():
        raise RuntimeError("RAW ZIP HASH MISMATCH. Do not compare output to old baseline. "
                           f"Old={expected_hash}, Current={got_hash}")
    snapshot = baseline_meta["snapshot"]
    needed_pws = ("SUBMISSIONYEARQUARTER", "PWSID", "PWS_ACTIVITY_CODE", "PWS_TYPE_CODE",
                  "FIRST_REPORTED_DATE", "PWS_DEACTIVATION_DATE")
    needed_vio = ("SUBMISSIONYEARQUARTER", "PWSID", "VIOLATION_ID", "VIOLATION_CODE",
                  "IS_HEALTH_BASED_IND", "NON_COMPL_PER_BEGIN_DATE")
    pws = {}
    records = {}
    # Missing VIOLATION_ID + missing date cannot be reliably deduplicated.
    # Keep only per-PWS *possible* health-based status, never fabricate IDs.
    undated_unkeyed = defaultdict(lambda: {"raw": False, "adjusted": False})
    unkeyed_stats = defaultdict(Counter)
    stats = Counter()
    with zipfile.ZipFile(archive) as z:
        pfile, vfile = source_table(z, BASE), source_table(z, VIOS)
        for r in read_rows(z, pfile, needed_pws):
            if r["SUBMISSIONYEARQUARTER"] != snapshot:
                continue
            pid = r["PWSID"]
            if pid[:2] not in STATES or r["PWS_TYPE_CODE"] not in PWS_TYPES:
                continue
            p = {"state": pid[:2], "type": r["PWS_TYPE_CODE"],
                 "activity": r["PWS_ACTIVITY_CODE"], "first_raw": r["FIRST_REPORTED_DATE"],
                 "deact_raw": r["PWS_DEACTIVATION_DATE"],
                 "first": parse_date(r["FIRST_REPORTED_DATE"]),
                 "deact": parse_date(r["PWS_DEACTIVATION_DATE"])}
            if pid in pws and pws[pid] != p:
                raise RuntimeError(f"Conflicting system records in same snapshot: {pid}")
            pws[pid] = p
        cohorts, cohort_audit = build_cohorts(pws)
        universe = set().union(*(set(c) for c in cohorts.values()))
        for idx, r in enumerate(read_rows(z, vfile, needed_vio), 1):
            if r["SUBMISSIONYEARQUARTER"] != snapshot:
                continue
            pid = r["PWSID"]
            if pid not in universe:
                continue
            stats["raw_rows_in_candidate_universe"] += 1
            d = parse_date(r["NON_COMPL_PER_BEGIN_DATE"])
            if pid in cohorts["A_2026_snapshot_active"] and d is None:
                stats["original_a_raw_rows_with_undated_onset"] += 1
            vid = r["VIOLATION_ID"]
            code, hb = r["VIOLATION_CODE"].upper(), r["IS_HEALTH_BASED_IND"].upper()
            if not vid:
                stats["raw_rows_missing_violation_id"] += 1
                if d is not None and d.year in (2023, 2024):
                    # This would also have halted the original recoding:
                    # the published main-year event cannot be deduplicated.
                    raise RuntimeError(f"Missing VIOLATION_ID for a dated 2023/24 row: {pid}; cannot validate baseline")
                if d is None:
                    stats["raw_undated_rows_missing_violation_id"] += 1
                    s = pid[:2]
                    unkeyed_stats[s]["rows_without_id"] += 1
                    if hb == "Y":
                        unkeyed_stats[s]["rows_health_Y"] += 1
                    elif hb not in {"Y", "N"}:
                        unkeyed_stats[s]["rows_health_unknown"] += 1
                    # For the extreme stress scenario, unknown health
                    # flags are treated as possibly Y. Empty/unknown code
                    # could be a non-2E violation. This is conservative.
                    if hb != "N":
                        undated_unkeyed[pid]["raw"] = True
                        if code != "2E":
                            undated_unkeyed[pid]["adjusted"] = True
                continue
            if hb not in {"Y", "N"}:
                stats["raw_rows_invalid_health_flag"] += 1
                if d is not None and d.year in (2023, 2024):
                    raise RuntimeError(f"Unknown IS_HEALTH_BASED_IND for a dated 2023/24 row {pid} / {vid}: {hb!r}")
                if d is not None:
                    continue
                # Retain as possible health-based for stress scenario only.
                hb = "?"
            key = (pid, vid)
            y = d.year if d else None
            old = records.get(key)
            if old is None:
                records[key] = {"code": code, "hb": hb, "year": y, "has_unknown_row": y is None}
            else:
                stats["duplicate_raw_violation_rows"] += 1
                if (code, hb) != (old["code"], old["hb"]):
                    raise RuntimeError(f"Conflicting VIOLATION_CODE/IS_HEALTH_BASED_IND for same ID {key}")
                if y is None:
                    old["has_unknown_row"] = True
                elif old["year"] is None:
                    old["year"] = y
                elif old["year"] != y:
                    raise RuntimeError(f"Conflicting valid begin years for same ID {key}")
    stats["unique_violations_in_candidate_universe_all_years"] = len(records)
    stats["undated_ids_resolved_by_dated_duplicate"] = sum(
        1 for r in records.values() if r["year"] is not None and r["has_unknown_row"])
    stats["unique_violations_still_undated"] = sum(1 for r in records.values() if r["year"] is None)
    if stats["original_a_raw_rows_with_undated_onset"] != baseline_meta["audit"].get("rows_without_parseable_begin_date"):
        raise RuntimeError("Mismatch original A raw-row undated audit: "
                           f"new={stats['original_a_raw_rows_with_undated_onset']}, "
                           f"original={baseline_meta['audit'].get('rows_without_parseable_begin_date')}")
    baseline_rows = list(csv.DictReader((baseline / "state_comparison.csv").open(encoding="utf-8-sig", newline="")))
    expected = {r["state"]: r for r in baseline_rows}
    outputs = {}
    summary = {}
    for name, cohort in cohorts.items():
        known, inversions = state_counts_and_rates(cohort, records, "known", undated_unkeyed)
        upper, upper_inversions = state_counts_and_rates(cohort, records, "all_undated_2024", undated_unkeyed)
        outputs[name] = (known, upper)
        summary[name] = {"cohort_n": len(cohort), "observed_pairwise_reversals": inversions,
                         "extreme_undated_assignment_pairwise_reversals": upper_inversions}
    main_rows = {r["state"]: r for r in outputs["A_2026_snapshot_active"][0]}
    checks = {"eligible": "eligible_active_cohort_snapshot", "onset2023": "pws_2023_health_onset",
              "raw2024": "pws_2024_health_onset_raw", "adjusted2024": "pws_2024_health_onset_without_2E",
              "flag_lost2024": "pws_losing_health_flag", "any2e2024": "pws_any_2E_in_2024",
              "raw_rank": "raw_rank", "adjusted_rank": "adjusted_rank"}
    for state in STATES:
        if state not in main_rows or state not in expected:
            raise RuntimeError(f"Missing baseline state in derived results: {state}")
        for new_key, old_key in checks.items():
            if int(main_rows[state][new_key]) != int(expected[state][old_key]):
                raise RuntimeError(f"Baseline numerical mismatch {state}, {new_key}. STOP: no output written.")
    # All primary baseline counts were identically reproduced from unchanged EPA ZIP.
    outdir.mkdir(parents=True, exist_ok=True)
    cohort_rows = []
    for name, (known, _) in outputs.items():
        for r in known:
            cohort_rows.append({"cohort_definition": name, **r})
    write_csv(outdir / "cohort_sensitivity.csv", cohort_rows)
    missing_rows = aggregate_unknown(cohorts["A_2026_snapshot_active"], records,
                                     *outputs["A_2026_snapshot_active"],
                                     undated_unkeyed, unkeyed_stats)
    write_csv(outdir / "missing_dates_sensitivity.csv", missing_rows)
    audit = {"epa_zip_sha256": got_hash, "frozen_snapshot": snapshot,
             "source_files": {"pws": pfile, "violations": vfile},
             "baseline_verification": "PASS: 5 state denominators, 2023/2024 counts, 2E counts and ranks match prior output",
             "existing_baseline": {"active_snapshot_systems": len(cohorts["A_2026_snapshot_active"]),
                                  "unique_onset_violations_2023_2024_original": baseline_meta.get("unique_violations_selected")},
             "audit_counts": dict(stats),
             "unkeyed_undated_raw_rows_by_state": {s: dict(unkeyed_stats[s]) for s in STATES},
             "unkeyed_handling": "Undated rows with blank VIOLATION_ID are not assigned invented IDs, not counted as unique violations, and cannot alter observed 2023/2024 indicators. Their potentially health-based flags are included at system level in the extreme 2024 stress only; unknown health flags treated as possibly health-based; unknown codes treated as potentially non-2E.",
             "pws_candidate_audit": dict(cohort_audit),
             "cohort_results": summary,
             "methodological_cautions": [
                 "All undated-onset violations assigned to 2024 is a deliberately extreme upper stress scenario, NOT a data imputation or estimated 2024 count.",
                 "The lower bound counts only violations with parseable 2024 onset; the extreme stress adds all still-undated health-based or potentially health-based records, including rows without violation IDs at PWS-flag level. True-year membership is unknown.",
                 "Missing VIOLATION_ID on undated rows prevents unique-event counts for those rows, NOT conservative PWS-level possible-status stress. No IDs are imputed.",
                 "Rank outcomes under the single all-undated-to-2024 scenario do NOT exhaust rankings under every possible allocation of undated onset dates.",
                 "FIRST_REPORTED_DATE measures first database report, NOT actual operation start or activation date.",
                 "PWS_DEACTIVATION_DATE and snapshot activity alone cannot establish exact historical 2023/2024 system populations, especially for reopenings and incomplete dates.",
                 "The retrospective cohort proxy cannot substitute for historical quarterly snapshots; neither proxy changes original manuscript estimates.",
                 "No causal interpretation or actual drinking-water contamination measurement is warranted.",
             ]}
    (outdir / "robustness_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ROBUSTNESS CHECKS COMPLETED. Output folder:", outdir.resolve())
    print("Baseline: PASS. ZIP SHA256:", got_hash)
    print("Cohort counts:", {k: v["cohort_n"] for k, v in summary.items()})
    for row in missing_rows:
        print(row["state"], "known raw/adjusted", row["observed_raw2024"],
              row["observed_adjusted2024"], "stress", row["stress_raw2024_all_undated_as_2024"],
              row["stress_adjusted2024_all_undated_as_2024"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--zip", type=Path, required=True, help="Same ORIGINAL EPA SDWA_latest_downloads.zip")
    p.add_argument("--baseline", type=Path, required=True, help="Folder with old audit.json and state_comparison.csv")
    p.add_argument("--output", type=Path, default=Path("./robustness_output"))
    args = p.parse_args()
    if not args.zip.is_file(): p.error("ZIP path not found: " + str(args.zip))
    if not (args.baseline / "audit.json").is_file() or not (args.baseline / "state_comparison.csv").is_file():
        p.error("Baseline folder must contain audit.json and state_comparison.csv")
    try:
        run(args.zip, args.baseline, args.output)
    except Exception as exc:
        print("ERROR: robustness check was NOT completed:", exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
