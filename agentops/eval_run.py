from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from agentops import chaos, config

FIELDS = ["fault", "mode", "trial", "success", "mttr_s", "steps", "tokens", "cost",
          "stop_reason", "incident_id", "error"]


def _append(path: Path, row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def run_eval(trials, modes, faults, out_dir, trial_fn) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.csv"
    for fault in faults:
        for n in range(1, trials + 1):
            for mode in modes:
                print(f"trial {fault} #{n} [{mode}]", flush=True)
                try:
                    r = trial_fn(fault, mode, n)
                    row = {"fault": r.fault, "mode": r.mode, "trial": r.trial, "success": r.success,
                           "mttr_s": round(r.mttr_s, 2), "steps": r.steps, "tokens": r.tokens,
                           "cost": round(r.cost, 5), "stop_reason": r.stop_reason,
                           "incident_id": r.incident_id, "error": ""}
                except Exception as e:
                    row = {"fault": fault, "mode": mode, "trial": n, "success": False, "mttr_s": "",
                           "steps": 0, "tokens": 0, "cost": 0, "stop_reason": "error",
                           "incident_id": "", "error": f"{type(e).__name__}: {e}"}
                _append(path, row)


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["success"] = _as_bool(df["success"])
    df["mttr_s"] = pd.to_numeric(df["mttr_s"], errors="coerce")
    keys = ["mode", "fault"]
    base = df.groupby(keys).agg(trials=("success", "size"), success_rate=("success", "mean"),
                                steps=("steps", "mean"), tokens=("tokens", "mean"), cost=("cost", "mean"))
    mttr = df[df["success"]].groupby(keys)["mttr_s"].mean().rename("mttr_s")
    return base.join(mttr).reset_index()


def overall(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["success"] = _as_bool(df["success"])
    df["mttr_s"] = pd.to_numeric(df["mttr_s"], errors="coerce")
    g = df.groupby("mode")
    out = g.agg(trials=("success", "size"), success_rate=("success", "mean"),
                tokens=("tokens", "mean"), cost=("cost", "sum"))
    out["mttr_s"] = df[df["success"]].groupby("mode")["mttr_s"].mean()
    return out.reset_index()


def _table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in frame.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                v = "-" if pd.isna(v) else (f"{v:.0%}" if c == "success_rate" else f"{v:.2f}")
            cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def to_markdown(summary: pd.DataFrame, overall_df: pd.DataFrame) -> str:
    return ("# AgentOps eval summary\n\n## Per fault\n\n" + _table(summary) +
            "\n\n## Overall\n\n" + _table(overall_df) + "\n\n"
            "MTTR averages successful trials only. Cost is the mean per trial (per-fault table) "
            "or the total (overall table).\n")


def _grouped_bar(summary: pd.DataFrame, column: str, ylabel: str, title: str, path: Path) -> None:
    pivot = summary.pivot(index="fault", columns="mode", values=column)
    ax = pivot.plot(kind="bar", figsize=(8, 4.5), rot=20)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xlabel("")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def write_report(csv_path, out_dir) -> None:
    out_dir = Path(out_dir)
    df = pd.read_csv(csv_path)
    summary = summarize(df)
    (out_dir / "summary.md").write_text(to_markdown(summary, overall(df)))
    _grouped_bar(summary, "success_rate", "success rate", "Success rate by fault: baseline vs agent",
                 out_dir / "success_rate.png")
    _grouped_bar(summary, "mttr_s", "seconds (successful trials)", "Mean time to recovery by fault",
                 out_dir / "mttr.png")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the AgentOps evaluation")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--modes", nargs="+", default=["baseline", "agent"], choices=["baseline", "agent"])
    p.add_argument("--faults", nargs="+", default=list(chaos.FAULTS), choices=chaos.FAULTS)
    p.add_argument("--out", default=str(config.ROOT / "eval"))
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()
    out = Path(args.out)

    if not args.report_only:
        from agentops.checkpoint import CheckpointStore
        from agentops.db import connect
        from agentops.handlers import make_handler
        from agentops.trace import TraceStore
        from agentops.trial import run_trial

        conn = connect(config.DB_PATH)
        trace, cps = TraceStore(conn), CheckpointStore(conn)
        llm = None
        if "agent" in args.modes:
            from agentops.llm import OpenRouterClient
            llm = OpenRouterClient()
        handlers = {m: make_handler(m, llm=llm, trace=trace, checkpoints=cps) for m in args.modes}

        def trial_fn(fault, mode, n):
            return run_trial(fault, mode, n, handlers[mode], trace)

        run_eval(args.trials, args.modes, args.faults, out, trial_fn)
    write_report(out / "results.csv", out)
    print((out / "summary.md").read_text())


if __name__ == "__main__":
    main()
