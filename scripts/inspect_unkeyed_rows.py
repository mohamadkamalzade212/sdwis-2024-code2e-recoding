#!/usr/bin/env python3
"""Bounded source diagnostic: rows missing VIOLATION_ID in EPA ECHO SDWA ZIP.

Read-only, streaming, standard-library-only. No imputed records or manuscript changes.
Classifies by NONEMPTY documented violation- and enforcement-specific fields.
A classification is a DATA PATTERN, not proof of an underlying SDWIS event.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import zipfile

STATES = ("MS", "AL", "LA", "AR", "TN")
PWS_TYPES = {"CWS", "NTNCWS"}
SNAPSHOT = "2026Q3"
PWS_FILE = "SDWA_PUB_WATER_SYSTEMS.csv"
VIO_FILE = "SDWA_VIOLATIONS_ENFORCEMENT.csv"

# As documented by EPA ECHO SDWA Data Element Dictionary.
VIOL_FIELDS = (
    "NON_COMPL_PER_BEGIN_DATE", "NON_COMPL_PER_END_DATE", "VIOLATION_CODE",
    "VIOLATION_CATEGORY_CODE", "IS_HEALTH_BASED_IND", "CONTAMINANT_CODE",
    "VIOL_MEASURE", "UNIT_OF_MEASURE", "FEDERAL_MCL", "STATE_MCL",
    "IS_MAJOR_VIOL_IND", "SEVERITY_IND_CNT", "CALCULATED_RTC_DATE",
    "VIOLATION_STATUS", "PUBLIC_NOTIFICATION_TIER", "CALCULATED_PUB_NOTIF_TIER",
    "VIOL_ORIGINATOR_CODE", "SAMPLE_RESULT_ID", "CORRECTIVE_ACTION_ID",
    "RULE_CODE", "RULE_GROUP_CODE", "RULE_FAMILY_CODE",
    "VIOL_FIRST_REPORTED_DATE", "VIOL_LAST_REPORTED_DATE",
)
ENF_FIELDS = (
    "ENFORCEMENT_ID", "ENFORCEMENT_DATE", "ENFORCEMENT_ACTION_TYPE_CODE",
    "ENF_ACTION_CATEGORY", "ENF_ORIGINATOR_CODE", "ENF_FIRST_REPORTED_DATE",
    "ENF_LAST_REPORTED_DATE",
)
META_FIELDS = {"SUBMISSIONYEARQUARTER", "PWSID", "VIOLATION_ID", "FACILITY_ID"}
MISSING_MARKERS = {"", "NULL", "N/A", "NA", "NONE"}


def nonempty(value):
    return (str(value).strip().upper() not in MISSING_MARKERS) if value is not None else False


def parse_date(value):
    value = (value or "").strip().split("T")[0].split(" ")[0]
    if not nonempty(value):
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def locate(z, filename):
    a = [name for name in z.namelist()
         if name.replace("\\", "/").split("/")[-1].upper() == filename.upper()]
    if len(a) != 1:
        raise ValueError(f"Expected exactly one {filename} in ZIP; found {len(a)}")
    return a[0]


def csv_rows(z, name):
    with z.open(name) as raw:
        fh = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV header missing for {name}")
        header_map = {(h or "").strip().upper(): h for h in reader.fieldnames if h}
        for row in reader:
            yield {k: (row.get(v) or "").strip() for k, v in header_map.items()}


def read_header(z, name):
    with z.open(name) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as fh:
            return {(v or "").strip().upper() for v in next(csv.reader(fh))}


def cohorts(z, pws_file, snapshot):
    groups = {"A_snapshot_active": set(), "B_active_first_reported_by_2023": set(),
              "C_reported_by_2023_not_deactivated_by_2024_proxy": set()}
    cutoff, end24 = date(2023, 12, 31), date(2024, 12, 31)
    for r in csv_rows(z, pws_file):
        pid = r.get("PWSID", "")
        if (r.get("SUBMISSIONYEARQUARTER") != snapshot or pid[:2] not in STATES
                or r.get("PWS_TYPE_CODE") not in PWS_TYPES):
            continue
        first = parse_date(r.get("FIRST_REPORTED_DATE"))
        deact = parse_date(r.get("PWS_DEACTIVATION_DATE"))
        active = r.get("PWS_ACTIVITY_CODE", "") == "A"
        if active:
            groups["A_snapshot_active"].add(pid)
        if active and first and first <= cutoff:
            groups["B_active_first_reported_by_2023"].add(pid)
        good_inactive = not active and deact and deact > end24
        good_active = active and not (deact and deact <= end24)
        if first and first <= cutoff and (good_inactive or good_active):
            groups["C_reported_by_2023_not_deactivated_by_2024_proxy"].add(pid)
    return groups


def classify(has_viol, has_enf):
    if has_viol and has_enf: return "violation_fields_and_enforcement_without_id"
    if has_viol: return "violation_fields_without_id"
    if has_enf: return "enforcement_fields_only"
    return "no_violation_or_enforcement_fields"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(archive: Path, baseline: Path | None, outdir: Path, sample_per_group: int):
    if not archive.is_file():
        raise ValueError(f"ZIP not found: {archive}")
    expected_hash = None
    if baseline is not None:
        apath = baseline / "audit.json"
        if not apath.is_file():
            raise ValueError("Baseline audit.json not found")
        audit = json.loads(apath.read_text(encoding="utf-8-sig"))
        expected_hash = audit.get("input_zip_sha256")
        if audit.get("snapshot") != SNAPSHOT:
            raise ValueError(f"Unexpected baseline snapshot: {audit.get('snapshot')}")
    digest = sha256(archive)
    if expected_hash and digest.lower() != expected_hash.lower():
        raise ValueError("ZIP SHA256 mismatch with baseline; STOP. Do not mix source vintages.")

    by_cohort_state_class = Counter()
    event_fields = Counter()
    enforcement_fields = Counter()
    pattern_counts = Counter()
    rows_by_pws = Counter()
    other_columns = Counter()
    flags = Counter()
    dated_unkeyed = Counter()
    random_gen = random.Random(20261111)
    samples = defaultdict(list)
    sample_counts = Counter()
    last_header = set()
    with zipfile.ZipFile(archive) as z:
        pws_name = locate(z, PWS_FILE)
        vio_name = locate(z, VIO_FILE)
        header = read_header(z, vio_name)
        required_vio = set(("SUBMISSIONYEARQUARTER", "PWSID", "VIOLATION_ID", "NON_COMPL_PER_BEGIN_DATE",
                            "VIOLATION_CODE", "IS_HEALTH_BASED_IND"))
        required_pws = {"SUBMISSIONYEARQUARTER", "PWSID", "PWS_ACTIVITY_CODE", "PWS_TYPE_CODE",
                        "FIRST_REPORTED_DATE", "PWS_DEACTIVATION_DATE"}
        if required_vio - header or required_pws - read_header(z, pws_name):
            raise ValueError("CSV schema does not match prior EPA data; STOP and inspect headers.")
        present_viol = tuple(c for c in VIOL_FIELDS if c in header)
        present_enf = tuple(c for c in ENF_FIELDS if c in header)
        if len(present_enf) == 0 or len(present_viol) < 15:
            raise ValueError("Insufficient event fields to classify; STOP and inspect source schema.")
        last_header = header
        groups = cohorts(z, pws_name, SNAPSHOT)
        if len(groups["A_snapshot_active"]) != 3781:
            raise ValueError("Baseline A cohort not 3,781. STOP: data/snapshot/sample differs.")
        universe = set().union(*groups.values())
        if len(groups["B_active_first_reported_by_2023"]) != 3768 or len(groups["C_reported_by_2023_not_deactivated_by_2024_proxy"]) != 3853:
            raise ValueError("Baseline B/C cohorts differ. STOP: cannot reconcile previous audit.")
        recognized = META_FIELDS | set(present_viol) | set(present_enf)
        for row in csv_rows(z, vio_name):
            pid = row.get("PWSID", "")
            if row.get("SUBMISSIONYEARQUARTER") != SNAPSHOT or pid not in universe:
                continue
            if nonempty(row.get("VIOLATION_ID")):
                continue
            flags["union_missing_id_rows"] += 1
            flags["active_missing_id_rows"] += pid in groups["A_snapshot_active"]
            if parse_date(row.get("NON_COMPL_PER_BEGIN_DATE")) is not None:
                dated_unkeyed["rows_with_valid_begin_date"] += 1
            violation_nonempty = [k for k in present_viol if nonempty(row.get(k))]
            enforcement_nonempty = [k for k in present_enf if nonempty(row.get(k))]
            cls = classify(bool(violation_nonempty), bool(enforcement_nonempty))
            if nonempty(row.get("FACILITY_ID")):
                flags["missing_id_with_facility_id"] += 1
            for col in violation_nonempty:
                event_fields[col] += 1
            for col in enforcement_nonempty:
                enforcement_fields[col] += 1
            for col in header - recognized:
                if nonempty(row.get(col)):
                    other_columns[col] += 1
            pattern_counts[cls] += 1
            rows_by_pws[pid] += 1
            for cohort, ids in groups.items():
                if pid in ids:
                    by_cohort_state_class[(cohort, pid[:2], cls)] += 1
            groupkey = (pid[:2], cls)
            sample_counts[groupkey] += 1
            n = sample_counts[groupkey]
            picked = {
                "state": pid[:2], "classification": cls, "pwsid": pid,
                "violation_code": row.get("VIOLATION_CODE", ""),
                "violation_category": row.get("VIOLATION_CATEGORY_CODE", ""),
                "is_health_based": row.get("IS_HEALTH_BASED_IND", ""),
                "non_compliance_begin": row.get("NON_COMPL_PER_BEGIN_DATE", ""),
                "violation_first_reported": row.get("VIOL_FIRST_REPORTED_DATE", ""),
                "enforcement_id_present": int(nonempty(row.get("ENFORCEMENT_ID"))),
                "enforcement_type": row.get("ENFORCEMENT_ACTION_TYPE_CODE", ""),
                "enforcement_date": row.get("ENFORCEMENT_DATE", ""),
                "nonempty_violation_fields": ";".join(violation_nonempty),
                "nonempty_enforcement_fields": ";".join(enforcement_nonempty),
            }
            arr = samples[groupkey]
            if len(arr) < sample_per_group:
                arr.append(picked)
            else:
                j = random_gen.randrange(n)
                if j < sample_per_group:
                    arr[j] = picked
        # Safety: no output until consistency is established.
        if flags["union_missing_id_rows"] != 23109 or flags["active_missing_id_rows"] != 22561:
            raise ValueError("Missing-ID row counts disagree with v0.3.1 audit. STOP, do not interpret.")
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for (cohort, state, cls), n in sorted(by_cohort_state_class.items()):
        rows.append({"cohort": cohort, "state": state, "classification": cls, "raw_rows": n})
    write_csv(outdir / "unkeyed_classification.csv", rows)
    examples = [v for key in sorted(samples) for v in samples[key]]
    write_csv(outdir / "unkeyed_sample.csv", examples)
    data = {
        "status": "COMPLETED_SOURCE_PATTERN_DIAGNOSTIC_NOT_EVENT_VERIFICATION",
        "snapshot": SNAPSHOT,
        "epa_zip_sha256": digest,
        "baseline_sha256_verified": bool(expected_hash),
        "cohort_n": {name: len(s) for name, s in groups.items()},
        "scope_universe_n": len(universe),
        "union_missing_violation_id_raw_rows": flags["union_missing_id_rows"],
        "A_snapshot_missing_violation_id_raw_rows": flags["active_missing_id_rows"],
        "missing_id_rows_with_valid_non_compliance_begin": dated_unkeyed["rows_with_valid_begin_date"],
        "missing_id_rows_with_facility_id": flags["missing_id_with_facility_id"],
        "classification_totals_union_raw_rows": dict(pattern_counts),
        "violation_field_nonempty_frequency_union": dict(event_fields.most_common()),
        "enforcement_field_nonempty_frequency_union": dict(enforcement_fields.most_common()),
        "other_unexpected_columns_nonempty_frequency_union": dict(other_columns.most_common()),
        "distinct_systems_with_missing_id_union": len(rows_by_pws),
        "source_columns": sorted(last_header),
        "interpretation_notes": [
            "A blank VIOLATION_ID does NOT demonstrate a real violation or a missing violation event.",
            "enforcement_fields_only means enforcement-related columns have values and violation-specific fields do not; SDWIS may contain unlinked enforcement activities.",
            "no_violation_or_enforcement_fields may indicate placeholders or incomplete rows; cannot assert their origin from this output alone.",
            "violation_fields_without_id or violation_fields_and_enforcement_without_id require source review; never assign invented VIOLATION_ID or year.",
            "Main manuscript 2023/2024 onset estimates are NOT recomputed or changed by this diagnostic.",
        ],
    }
    (outdir / "unkeyed_audit.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("UNKEYED DIAGNOSTIC COMPLETE. Output:", outdir.resolve())
    print("ZIP SHA256 verified:", bool(expected_hash))
    print("Cohorts:", {k:len(v) for k,v in groups.items()})
    print("Missing-ID rows: A=", flags["active_missing_id_rows"], "union=", flags["union_missing_id_rows"])
    print("Classifications:", dict(pattern_counts))


def write_csv(path, records):
    if not records:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--zip", type=Path, required=True, help="Original EPA SDWA_latest_downloads.zip")
    a.add_argument("--baseline", type=Path, required=True, help="Original verified_output folder containing audit.json")
    a.add_argument("--output", type=Path, default=Path("unkeyed_output"))
    a.add_argument("--sample-per-state-class", type=int, default=4)
    args = a.parse_args()
    if not (1 <= args.sample_per_state_class <= 10):
        a.error("Sample size must be 1..10")
    try:
        run(args.zip, args.baseline, args.output, args.sample_per_state_class)
    except Exception as e:
        print("DIAGNOSTIC NOT COMPLETE:", e, file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
