"""Synthetic *software* unit tests ONLY; fixtures are not real EPA observations."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_exact_recoding import analyze

class TestRecoding(unittest.TestCase):
    def test_2e_only_removes_flag_but_does_not_remove_other_hb(self):
        snap="2025Q1"
        pws=[]; vs=[]
        for state in ("MS","AL","AR"):
            for j in range(2):
                pid=f"{state}{j:07d}"
                pws.append({"SUBMISSIONYEARQUARTER":snap,"PWSID":pid,"PWS_TYPE_CODE":"CWS","PWS_ACTIVITY_CODE":"A"})
                if j==0:
                    vs.append({"SUBMISSIONYEARQUARTER":snap,"PWSID":pid,"VIOLATION_ID": "001", "VIOLATION_CODE":"2E", "IS_HEALTH_BASED_IND":"Y", "NON_COMPL_PER_BEGIN_DATE":"11/01/2024"})
                    vs.append({"SUBMISSIONYEARQUARTER":snap,"PWSID":pid,"VIOLATION_ID": "002", "VIOLATION_CODE":"4G", "IS_HEALTH_BASED_IND":"N", "NON_COMPL_PER_BEGIN_DATE":"11/01/2024"})
        # One system has a second health-based violation and must remain counted.
        vs.append({"SUBMISSIONYEARQUARTER":snap,"PWSID":"MS0000000","VIOLATION_ID":"003", "VIOLATION_CODE":"45", "IS_HEALTH_BASED_IND":"Y", "NON_COMPL_PER_BEGIN_DATE":"12/01/2024"})
        vs.append(dict(vs[-1])) # exact repeated row must not be double-counted
        result, flags, audit = analyze(pws,vs,snap,states=("MS","AL","AR"))
        s={r['state']:r for r in result}
        self.assertEqual(s['MS']['pws_losing_health_flag'],0)
        self.assertEqual(s['AL']['pws_losing_health_flag'],1)
        self.assertEqual(s['AR']['pws_losing_health_flag'],1)
        self.assertEqual(s['MS']['pws_2024_health_onset_without_2E'],1)
        self.assertEqual(audit['audit']['duplicate_rows_within_snapshot'],1)

if __name__=="__main__":unittest.main()
