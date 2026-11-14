#!/usr/bin/env python3
"""Conservative, auditable recoding of EPA ECHO SDWA raw quarterly CSV ZIP.

DOES NOT download data. Requires original EPA SDWA_latest_downloads.zip.
No imputation. No causal interpretation. Standard Python library only.

Target: new violations starting in CY2023/24 for same fixed cohort of active CWS/NTNCWS
in 5 preselected states, under one EPA release snapshot. Compare PWS-level
health-based flags before/after removing VIOLATION_CODE 2E in CY2024.
"""
import argparse, csv, io, json, re, zipfile, hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

STATES = ("MS", "AL", "LA", "AR", "TN")  # Mississippi and its four land-border neighbors
PWS_TYPES = {"CWS", "NTNCWS"}  # exclude TNCWS not ordinarily subject to LCR inventory requirement
TABLES = ("SDWA_PUB_WATER_SYSTEMS.csv", "SDWA_VIOLATIONS_ENFORCEMENT.csv")


def locate(z, base):
    matches = [name for name in z.namelist() if name.upper().split('/')[-1] == base.upper()]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {base}, found {matches}. Inspect archive manually.")
    return matches[0]


def read_csv(z, name):
    with z.open(name) as binary:
        text = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.DictReader(text)
        if not reader.fieldnames:
            raise ValueError(f"CSV header missing: {name}")
        for row in reader:
            yield {k.strip().upper(): (v or "").strip() for k, v in row.items() if k}


def year_of_begin(s):
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0].strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).year
        except ValueError:
            pass
    return None


def rank_min(values):
    # lower violation rate = better rank (competition ranking for ties)
    return {k: 1 + sum(1 for v in values.values() if v < val) for k, val in values.items()}


def analyze(pws_rows, vio_rows, snapshot, states=STATES):
    systems = {}
    conflicting_system_rows = []
    for r in pws_rows:
        if r.get("SUBMISSIONYEARQUARTER") != snapshot: continue
        pid = r.get("PWSID", "")
        if len(pid) < 3 or pid[:2] not in states: continue
        if r.get("PWS_ACTIVITY_CODE") != "A" or r.get("PWS_TYPE_CODE") not in PWS_TYPES: continue
        entry = (pid[:2], r.get("PWS_TYPE_CODE"))
        if pid in systems and systems[pid] != entry: conflicting_system_rows.append(pid)
        systems[pid] = entry
    if not systems:
        raise ValueError("No eligible active CWS/NTNCWS systems; verify snapshot, field names, and archive.")
    if conflicting_system_rows:
        raise ValueError(f"Conflicting records for eligible PWSIDs: {conflicting_system_rows[:10]}")
    seen = {}
    health = {2023: defaultdict(set), 2024: defaultdict(set)}
    new2e = set()
    code_counts = defaultdict(int)
    audit = defaultdict(int)
    for r in vio_rows:
        if r.get("SUBMISSIONYEARQUARTER") != snapshot: continue
        pid = r.get("PWSID", "")
        if pid not in systems: continue
        y = year_of_begin(r.get("NON_COMPL_PER_BEGIN_DATE", ""))
        if y not in (2023, 2024):
            if y is None: audit["rows_without_parseable_begin_date"] += 1
            continue
        vid = r.get("VIOLATION_ID", "")
        if not vid:
            raise ValueError("Missing VIOLATION_ID in a selected violation row: cannot deduplicate reliably")
        code = r.get("VIOLATION_CODE", "").upper()
        hb = r.get("IS_HEALTH_BASED_IND", "").upper()
        if hb not in ("Y", "N"):
            raise ValueError(f"Invalid IS_HEALTH_BASED_IND for {pid} {vid}: {hb!r}")
        key = (pid, vid)
        comparison = (code, hb, y)
        if key in seen:
            audit["duplicate_rows_within_snapshot"] += 1
            if seen[key] != comparison:
                raise ValueError(f"Conflicting code/health/year for same violation {key}: {seen[key]} vs {comparison}")
            continue
        seen[key] = comparison
        code_counts[f"{y}:{code}:{hb}"] += 1
        if code == "2E" and hb == "N": audit["2E_not_health_based"] += 1
        if code == "4G" and hb == "Y": audit["4G_health_based"] += 1
        if hb == "Y":
            health[y][pid].add(code)
            if y == 2024 and code == "2E": new2e.add(pid)
    if audit["2E_not_health_based"] or audit["4G_health_based"]:
        raise ValueError("Unexpected official health flag for 2E or 4G. Inspect codes before interpreting.")
    per_system = []
    counts = defaultdict(lambda: defaultdict(int))
    for pid, (state, ptype) in systems.items():
        c23 = health[2023].get(pid, set())
        c24 = health[2024].get(pid, set())
        raw23 = int(bool(c23))
        raw24 = int(bool(c24))
        adjusted24 = int(bool(c24 - {"2E"}))
        count = counts[state]
        count["eligible"] += 1
        count["2023"] += raw23
        count["2024_raw"] += raw24
        count["2024_adjusted"] += adjusted24
        count["2E_any"] += int(pid in new2e)
        count["2E_only_health_based"] += raw24 - adjusted24
        per_system.append({"PWSID": pid, "STATE": state, "PWS_TYPE": ptype,
                           "HBC_2023_ONSET": raw23, "HBC_2024_ONSET_RAW": raw24,
                           "HBC_2024_ONSET_NO_2E": adjusted24, "HAS_2E_2024": int(pid in new2e)})
    if not new2e:
        raise ValueError("No 2024 code-2E violations found in fixed cohort. Check snapshot and coverage; no rank claims.")
    raw_rates = {s: counts[s]["2024_raw"] / counts[s]["eligible"] for s in states if counts[s]["eligible"]}
    adj_rates = {s: counts[s]["2024_adjusted"] / counts[s]["eligible"] for s in states if counts[s]["eligible"]}
    if len(raw_rates) < 3:
        raise ValueError("Fewer than 3 states have eligible PWS denominators; do not claim geographic ranking.")
    ranks_raw, ranks_adj = rank_min(raw_rates), rank_min(adj_rates)
    states_out = []
    for s in states:
        c = counts[s]
        if not c["eligible"]: continue
        states_out.append({"state": s, "eligible_active_cohort_snapshot": c["eligible"],
                           "pws_2023_health_onset": c["2023"],
                           "pws_2024_health_onset_raw": c["2024_raw"],
                           "pws_2024_health_onset_without_2E": c["2024_adjusted"],
                           "pws_any_2E_in_2024": c["2E_any"],
                           "pws_losing_health_flag": c["2E_only_health_based"],
                           "raw_2024_rate_pct": round(100 * raw_rates[s], 6),
                           "adjusted_2024_rate_pct": round(100 * adj_rates[s], 6),
                           "raw_rank": ranks_raw[s], "adjusted_rank": ranks_adj[s],
                           "rank_change_adjusted_minus_raw": ranks_adj[s] - ranks_raw[s]})
    meta = {"snapshot": snapshot, "states_prespecified": list(states),
            "fixed_cohort": "active CWS/NTNCWS in source snapshot; NOT historical active populations per year",
            "year_rule": "year of NON_COMPL_PER_BEGIN_DATE (newly starting violations only)",
            "method": "one frozen quarterly snapshot; unique (PWSID,VIOLATION_ID) then PWS-year binary flags",
            "year2024_exclusion": "only VIOLATION_CODE=2E (preserve other health-based codes and all 4G)",
            "source_status": "this output may only be labeled empirical if generated from the actual EPA ZIP",
            "unique_violations_selected": len(seen),
            "raw_rows_2E_2024_health_flagged": code_counts.get("2024:2E:Y", 0),
            "raw_rows_4G_2024_nonhealth": code_counts.get("2024:4G:N", 0),
            "audit": dict(audit),
            "warning": "Not EPA official annual uncorrected-violation prevalence; no inference about observed contamination, causal policy effect, or historical 2023/24 active-system denominators."}
    return states_out, per_system, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True, type=Path, help="Official EPA SDWA_latest_downloads.zip")
    ap.add_argument("--output", type=Path, default=Path("./verified_output"))
    ap.add_argument("--snapshot", help="Optional exact SUBMISSIONYEARQUARTER; default newest shared one")
    args = ap.parse_args()
    if not args.zip.is_file(): ap.error(f"EPA ZIP not found: {args.zip}")
    with zipfile.ZipFile(args.zip) as z:
        file_map = {base: locate(z,base) for base in TABLES}
        needed = {"SUBMISSIONYEARQUARTER", "PWSID"}
        snapshots = {}
        for base in TABLES:
            present = set()
            for r in read_csv(z, file_map[base]):
                val = r.get("SUBMISSIONYEARQUARTER", "")
                if val: present.add(val)
            snapshots[base] = present
        overlap = snapshots[TABLES[0]] & snapshots[TABLES[1]]
        if not overlap: raise ValueError("No shared submission snapshot between official files.")
        snapshot = args.snapshot or max(overlap)
        if snapshot not in overlap:
            raise ValueError(f"Requested snapshot not in both files; shared snapshots: {sorted(overlap)[-8:]}")
        print("Frozen source snapshot:", snapshot)
        summary, per_system, meta = analyze(read_csv(z,file_map[TABLES[0]]),
                                             read_csv(z,file_map[TABLES[1]]),snapshot)
    args.output.mkdir(parents=True,exist_ok=True)
    for filename, rows in (("state_comparison.csv",summary), ("pws_flags.csv",per_system)):
        with (args.output / filename).open("w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    meta["files_used_in_zip"] = file_map
    meta["input_filename"] = args.zip.name
    digest=hashlib.sha256()
    with args.zip.open("rb") as src:
        for block in iter(lambda: src.read(1024*1024), b""):
            digest.update(block)
    meta["input_zip_sha256"] = digest.hexdigest()
    with (args.output / "audit.json").open("w",encoding="utf-8") as f:
        json.dump(meta,f,indent=2,ensure_ascii=False)
    print("Actual raw-source results written to:",args.output.resolve())
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
