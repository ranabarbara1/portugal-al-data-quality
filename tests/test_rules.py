"""Unit tests for the data quality rules, on small synthetic records."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import rules  # noqa: E402

REFERENCE_DATE = pd.Timestamp("2026-11-14", tz="UTC")

# X/Y in Web Mercator for a point in Lisbon (38.7223, -9.1393).
LISBON_X, LISBON_Y = -1017382.2, 4681971.1


def record(**overrides):
    base = {
        "X": str(LISBON_X),
        "Y": str(LISBON_Y),
        "NrRNAL": "1",
        "Denominacao": "Casa Azul",
        "DataRegisto": "2020/01/10 10:00:00+00",
        "DataAberturaPublico": "2020/02/01 00:00:00+00",
        "Modalidade": "Apartamento",
        "NrUtentes": "4",
        "Endereco": "Rua A 1",
        "CodigoPostal": "1100-001",
        "LatLong": "38,7223 ; -9,1393",
        "FiabilidadeGeo": "Fiavel",
        "DTMNFR": "110601",
        "Distrito": "Lisboa",
        "Concelho": "Lisboa",
    }
    base.update(overrides)
    return base


def frame(*records):
    return pd.DataFrame(list(records))


def test_clean_record_passes_every_defect_rule():
    df = frame(record())
    for rule in rules.build_rules(REFERENCE_DATE):
        assert not rule.check(df).any(), rule.code


@pytest.mark.parametrize("value,expected", [("1100-001", False), ("1100001", True), ("330", True), ("", False)])
def test_invalid_postal_code(value, expected):
    assert bool(rules.invalid_postal_code(frame(record(CodigoPostal=value))).iloc[0]) == expected


@pytest.mark.parametrize("name", ["", "  ", "N/A", "null", "-"])
def test_missing_or_placeholder_name(name):
    assert rules.missing_name(frame(record(Denominacao=name))).iloc[0]


def test_missing_postal_code():
    assert rules.missing_postal_code(frame(record(CodigoPostal=None))).iloc[0]


def test_geo_reliability_accent_variant_is_invalid():
    assert rules.invalid_geo_reliability(frame(record(FiabilidadeGeo="Fiável"))).iloc[0]


def test_placeholder_date():
    assert rules.placeholder_or_implausible_date(frame(record(DataRegisto="1900/01/01 00:00:00+00"))).iloc[0]


def test_opening_in_future():
    df = frame(record(DataAberturaPublico="2033/03/20 00:00:00+00"))
    assert rules.opening_in_future(df, REFERENCE_DATE).iloc[0]


def test_latlong_formats_are_parsed_and_flagged():
    df = frame(record(LatLong="38.7223;-9.1393"), record(LatLong="38,7223;-9,1393"))
    parsed = rules.parse_latlong(df["LatLong"])
    assert parsed["lat"].round(4).tolist() == [38.7223, 38.7223]
    assert rules.non_standard_latlong_format(df).all()


def test_coordinates_disagree_when_latlong_points_elsewhere():
    porto = record(LatLong="41,1579 ; -8,6291")
    assert rules.coordinates_disagree(frame(porto)).iloc[0]
    assert not rules.coordinates_disagree(frame(record())).iloc[0]


def test_possible_duplicate_ignores_case_and_spaces():
    df = frame(record(NrRNAL="1"), record(NrRNAL="2", Denominacao="  casa   AZUL "))
    assert rules.possible_duplicate_establishment(df).all()


def test_postal_code_in_other_municipality():
    peers = [record(NrRNAL=str(i)) for i in range(9)]
    outlier = record(NrRNAL="99", DTMNFR="131201")  # same postal code, other municipality
    flags = rules.postal_code_in_other_municipality(frame(*peers, outlier))
    assert flags.tolist() == [False] * 9 + [True]


def test_capacity_outlier_is_relative_to_modality():
    flats = [record(NrRNAL=str(i), NrUtentes="4") for i in range(20)]
    big_flat = record(NrRNAL="50", NrUtentes="118")
    hostel = record(NrRNAL="60", Modalidade="EstabelecimentoHospedagemHostel", NrUtentes="118")
    flags = rules.capacity_outlier(frame(*flats, big_flat, hostel))
    assert flags.iloc[20] and not flags.iloc[21]
