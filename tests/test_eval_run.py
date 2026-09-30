import csv

import pandas as pd

from agentops import eval_run
from agentops.trial import TrialResult


def fake_trial(fault, mode, trial_no):
    if fault == "crash" and trial_no == 2:
        raise RuntimeError("reset failed: boom")
    ok = mode == "agent" or fault == "crash"
    return TrialResult(fault, mode, trial_no, ok, 10.0 if ok else 40.0, 3, 500 if mode == "agent" else 0,
                       0.01 if mode == "agent" else 0.0, "finished" if ok else "step_limit", f"{mode}-{fault}-{trial_no}")


def run(tmp_path, **kw):
    eval_run.run_eval(trials=2, modes=["baseline", "agent"], faults=["crash", "bad_config"],
                      out_dir=tmp_path, trial_fn=fake_trial, **kw)
    return tmp_path / "results.csv"


def test_run_eval_writes_rows_and_survives_failed_trial(tmp_path):
    path = run(tmp_path)
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 8
    assert list(rows[0].keys()) == eval_run.FIELDS
    errors = [r for r in rows if r["error"]]
    assert len(errors) == 2  # crash trial 2, both modes
    assert all(r["success"] == "False" and "boom" in r["error"] for r in errors)


def test_summarize_excludes_failed_trials_from_mttr(tmp_path):
    df = pd.read_csv(run(tmp_path))
    s = eval_run.summarize(df).set_index(["mode", "fault"])
    assert s.loc[("agent", "bad_config"), "success_rate"] == 1.0
    assert s.loc[("baseline", "bad_config"), "success_rate"] == 0.0
    assert pd.isna(s.loc[("baseline", "bad_config"), "mttr_s"])
    assert s.loc[("agent", "bad_config"), "mttr_s"] == 10.0
    assert s.loc[("agent", "crash"), "trials"] == 2


def test_report_files(tmp_path):
    path = run(tmp_path)
    eval_run.write_report(path, tmp_path)
    md = (tmp_path / "summary.md").read_text()
    assert "| mode | fault |" in md and "Overall" in md
    assert (tmp_path / "success_rate.png").stat().st_size > 0
    assert (tmp_path / "mttr.png").stat().st_size > 0
