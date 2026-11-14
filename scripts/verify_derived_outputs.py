#!/usr/bin/env python3
"""Reconcile the seven archived derived output files (no raw EPA ZIP needed)."""
import csv, json, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "derived"
PROV = ROOT / "data" / "provenance"

def csvrows(filename):
    with (DATA/filename).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def main():
    s = csvrows("state_comparison.csv")
    p = csvrows("pws_flags.csv")
    c = csvrows("cohort_sensitivity.csv")
    u = csvrows("unkeyed_classification.csv")
    assert len(p) == 3781, len(p)
    assert len({r["PWSID"] for r in p}) == len(p)
    assert {r["state"] for r in s} == {"MS","AL","LA","AR","TN"}
    ss = {r["state"]: r for r in s}
    sums = collections.defaultdict(lambda:collections.Counter())
    for r in p:
        st=r["STATE"]
        for k in ["HBC_2023_ONSET", "HBC_2024_ONSET_RAW", "HBC_2024_ONSET_NO_2E", "HAS_2E_2024"]:
            assert r[k] in ("0","1")
            sums[st][k]+=int(r[k])
        if int(r["HBC_2024_ONSET_NO_2E"]) > int(r["HBC_2024_ONSET_RAW"]):
            raise AssertionError("Adjusted flag greater than raw flag")
    raw=adj=lost=n2e=overlap=0
    for st,r in ss.items():
        n=sum(x["STATE"]==st for x in p)
        assert int(r["eligible_active_cohort_snapshot"])==n
        for col,source in [
            ("pws_2023_health_onset","HBC_2023_ONSET"),
            ("pws_2024_health_onset_raw","HBC_2024_ONSET_RAW"),
            ("pws_2024_health_onset_without_2E","HBC_2024_ONSET_NO_2E"),
            ("pws_any_2E_in_2024","HAS_2E_2024")]:
            assert int(r[col])==sums[st][source],(st,col)
        r0=int(r["pws_2024_health_onset_raw"])
        a0=int(r["pws_2024_health_onset_without_2E"])
        e=int(r["pws_any_2E_in_2024"])
        assert int(r["pws_losing_health_flag"]) == r0-a0
        assert abs(100*r0/n - float(r["raw_2024_rate_pct"]))<0.00001
        assert abs(100*a0/n - float(r["adjusted_2024_rate_pct"]))<0.00001
        raw+=r0;adj+=a0;lost+=(r0-a0);n2e+=e
    overlap=sum(int(r["HAS_2E_2024"]) and int(r["HBC_2024_ONSET_NO_2E"]) for r in p)
    assert (raw,adj,lost,n2e,overlap)==(646,248,398,451,53)
    assert n2e-overlap == lost
    d={x["cohort_definition"] for x in c}
    assert len(d)==3
    assert len(c)==15
    for x in c:
        if x["cohort_definition"].startswith("A_"):
            assert int(x["eligible"])==int(ss[x["state"]]["eligible_active_cohort_snapshot"])
            assert int(x["raw2024"])==int(ss[x["state"]]["pws_2024_health_onset_raw"])
            assert int(x["adjusted2024"])==int(ss[x["state"]]["pws_2024_health_onset_without_2E"])
    audit=json.loads((PROV/"audit.json").read_text(encoding="utf-8-sig"))
    robust=json.loads((PROV/"robustness_audit.json").read_text(encoding="utf-8-sig"))
    unkeyed=json.loads((PROV/"unkeyed_audit.json").read_text(encoding="utf-8-sig"))
    sha='4d92d5f46b423742d5fd608257dc63ef666a832d9211199c28434cd8f41dccb0'
    assert audit['input_zip_sha256']==sha
    assert robust['epa_zip_sha256']==sha
    assert unkeyed['epa_zip_sha256']==sha
    assert unkeyed['union_missing_violation_id_raw_rows']==23109
    assert unkeyed['classification_totals_union_raw_rows']=={'enforcement_fields_only':23109}
    assert all(x['classification']=='enforcement_fields_only' for x in u)
    print('PASS: 3,781 PWS; raw=646; adjusted=248; lost=398; 2E=451; overlap=53')
    print('PASS: state counts/rates, cohort rows, and 2026Q3 SHA-256 reconciled')
    print('PASS: unkeyed diagnostic: 23,109 enforcement-only rows')
    print('NOTE: this does NOT reprocess the national EPA ZIP or validate all raw events')
if __name__ == '__main__': main()
