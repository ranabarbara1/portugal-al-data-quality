"""Export the audit as a star schema for the Power BI report.

Usage:
    python src/export_powerbi.py path/to/al.csv --reference-date 2026-11-13

Writes to powerbi/data/ (no personal data: names, street addresses and point
coordinates are never written; the finest location level is the municipality):
- dim_establishment.csv  one row per record (NrRNAL is unique, rule U01 = 0)
- dim_rule.csv           one row per rule: dimension, description, status
- fact_failure.csv       one row per (record, failed rule)
"""

import argparse
from pathlib import Path

import pandas as pd

from rules import build_rules, parse_date
from run_quality import load_register

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "powerbi" / "data"


def establishments(register):
    capacity = pd.to_numeric(register["NrUtentes"], errors="coerce")
    registered = parse_date(register["DataRegisto"])
    return pd.DataFrame({
        "NrRNAL": register["NrRNAL"],
        "Modalidade": register["Modalidade"],
        "NrUtentes": capacity.astype("Int64"),
        "AnoRegisto": registered.dt.year.astype("Int64"),
        "FiabilidadeGeo": register["FiabilidadeGeo"],
        "Concelho": register["Concelho"],
        "Distrito": register["Distrito"],
        "NUTSII": register["NUTSII"],
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="RNAL CSV downloaded from dados.gov.pt")
    parser.add_argument("--reference-date", required=True, help="Extraction date, YYYY-MM-DD")
    args = parser.parse_args()

    register = load_register(args.csv)
    if register["NrRNAL"].duplicated().any():
        raise SystemExit("NrRNAL is not unique: the star schema needs a surrogate key")

    reference_date = pd.Timestamp(args.reference_date, tz="UTC") + pd.Timedelta(days=1)
    rules = build_rules(reference_date)

    failures = pd.DataFrame({r.code: r.check(register).astype(bool) for r in rules})
    failures.insert(0, "NrRNAL", register["NrRNAL"])
    fact = (
        failures.melt(id_vars="NrRNAL", var_name="Rule", value_name="failed")
        .query("failed")
        .drop(columns="failed")
    )

    dim_rule = pd.DataFrame([
        {"Rule": r.code, "Dimension": r.dimension, "Description": r.description, "Status": r.status}
        for r in rules
    ])

    OUT.mkdir(parents=True, exist_ok=True)
    establishments(register).to_csv(OUT / "dim_establishment.csv", index=False, encoding="utf-8")
    dim_rule.to_csv(OUT / "dim_rule.csv", index=False, encoding="utf-8")
    fact.to_csv(OUT / "fact_failure.csv", index=False, encoding="utf-8")

    print(f"dim_establishment: {len(register):,} rows")
    print(f"dim_rule:          {len(dim_rule)} rows")
    print(f"fact_failure:      {len(fact):,} rows")
    print(fact.groupby("Rule").size().to_string())


if __name__ == "__main__":
    main()
