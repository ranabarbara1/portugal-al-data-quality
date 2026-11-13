"""Run every data quality rule on the RNAL extract and write the reports.

Usage:
    python src/run_quality.py path/to/al.csv --reference-date 2026-11-13

Outputs (no personal data: names and street addresses are never written):
- results/scorecard.csv          one line per rule: failures and % of records
- results/failures_by_record.csv NrRNAL + municipality + rule codes that failed
- results/by_district.csv        failure rate per district for the key rules
- figures/scorecard.png
- figures/geocoding_by_district.png
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from rules import build_rules, is_reliable_geocoding

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"


def load_register(path):
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig")


def run(register, reference_date):
    rules = build_rules(reference_date)
    failures = pd.DataFrame({rule.code: rule.check(register).astype(bool) for rule in rules})

    scorecard = pd.DataFrame([
        {
            "rule": rule.code,
            "dimension": rule.dimension,
            "description": rule.description,
            "status": rule.status,
            "failed_records": int(failures[rule.code].sum()),
            "failed_pct": round(failures[rule.code].mean() * 100, 2),
        }
        for rule in rules
    ])
    return failures, scorecard


def plot_scorecard(scorecard):
    data = scorecard.sort_values("failed_pct")
    colours = data["status"].map({"defect": "#c0392b", "confirm with data owner": "#e6a23c"})
    fig, ax = plt.subplots(figsize=(13, 7))
    ax.barh(data["rule"] + "  " + data["description"].str.slice(0, 75), data["failed_pct"], color=colours)
    ax.set_xscale("symlog", linthresh=0.1)
    ax.set_xlabel("Records failing the rule (%, symmetric log scale)")
    ax.set_title(f"RNAL data quality scorecard ({int(scorecard.attrs['records']):,} records)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in ["#c0392b", "#e6a23c"]]
    ax.legend(handles, ["Defect", "Confirm with data owner"], loc="lower right")
    fig.tight_layout()
    fig.savefig(FIGURES / "scorecard.png", dpi=150)
    plt.close(fig)


def plot_geocoding(register):
    share = (
        register.assign(reliable=is_reliable_geocoding(register))
        .groupby("Distrito")["reliable"].mean().mul(100).sort_values()
    )
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(share.index, share.values, color="#2e86c1")
    ax.set_xlabel("Records with reliable geocoding (%)")
    ax.set_title("Geocoding reliability by district")
    fig.tight_layout()
    fig.savefig(FIGURES / "geocoding_by_district.png", dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="RNAL CSV downloaded from dados.gov.pt")
    parser.add_argument("--reference-date", required=True, help="Extraction date, YYYY-MM-DD")
    args = parser.parse_args()

    RESULTS.mkdir(exist_ok=True)
    FIGURES.mkdir(exist_ok=True)

    register = load_register(args.csv)
    reference_date = pd.Timestamp(args.reference_date, tz="UTC") + pd.Timedelta(days=1)
    failures, scorecard = run(register, reference_date)
    scorecard.attrs["records"] = len(register)

    scorecard.to_csv(RESULTS / "scorecard.csv", index=False)

    failed_any = failures.any(axis=1)
    per_record = register.loc[failed_any, ["NrRNAL", "Concelho", "Distrito"]].copy()
    per_record["failed_rules"] = failures[failed_any].apply(lambda row: ",".join(row.index[row]), axis=1)
    per_record.to_csv(RESULTS / "failures_by_record.csv", index=False)

    key_rules = ["C01", "K01", "K02", "U02", "A02"]
    by_district = failures[key_rules].groupby(register["Distrito"]).mean().mul(100).round(2)
    by_district.insert(0, "records", register.groupby("Distrito").size())
    by_district.to_csv(RESULTS / "by_district.csv")

    plot_scorecard(scorecard)
    plot_geocoding(register)

    defect_rules = scorecard.loc[scorecard["status"] == "defect", "rule"]
    with_defect = failures[defect_rules].any(axis=1).sum()
    print(f"{len(register):,} records; {with_defect:,} ({with_defect / len(register):.1%}) with at least one confirmed defect")
    print(scorecard[["rule", "dimension", "failed_records", "failed_pct", "status"]].to_string(index=False))


if __name__ == "__main__":
    main()
