"""Data quality rules for the Portuguese Local Accommodation Register (RNAL).

Each rule receives the register as a DataFrame and returns a boolean Series that
is True for the records that FAIL the rule. Rules are grouped by the usual data
quality dimensions: completeness, validity, uniqueness, consistency and accuracy.

`status` separates confirmed defects from rules that still need confirmation from
the data owner: some patterns look wrong but may be legitimate business cases.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

POSTAL_CODE = r"\d{4}-\d{3}"
LATLONG_STANDARD = r"-?\d+,\d+ ; -?\d+,\d+"  # format used by most records
VALID_MODALITIES = {
    "Apartamento",
    "Moradia",
    "Quartos",
    "EstabelecimentoHospedagem",
    "EstabelecimentoHospedagemHostel",
}
VALID_GEO_RELIABILITY = {"Fiavel", "PoucoFiavel", "NaoFiavel"}
MIN_PLAUSIBLE_DATE = pd.Timestamp("1950-01-01", tz="UTC")


@dataclass(frozen=True)
class Rule:
    code: str
    dimension: str
    description: str
    check: Callable[[pd.DataFrame], pd.Series]
    status: str = "defect"  # "defect" or "confirm with data owner"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def parse_date(series):
    return pd.to_datetime(series, errors="coerce", utc=True)


def parse_latlong(series):
    """Extract latitude and longitude whatever the separator and decimal mark."""
    parts = series.fillna("").str.extract(r"(-?\d+[.,]\d+)\s*;\s*(-?\d+[.,]\d+)")
    parts = parts.apply(lambda col: pd.to_numeric(col.str.replace(",", ".", regex=False), errors="coerce"))
    parts.columns = ["lat", "lon"]
    return parts


def web_mercator_to_wgs84(x, y):
    lon = np.degrees(x / 6378137.0)
    lat = np.degrees(2 * np.arctan(np.exp(y / 6378137.0)) - np.pi / 2)
    return lat, lon


def distance_km(lat1, lon1, lat2, lon2):
    """Equirectangular approximation, accurate enough for distances of a few km."""
    dlat = lat1 - lat2
    dlon = (lon1 - lon2) * np.cos(np.radians((lat1 + lat2) / 2))
    return np.hypot(dlat, dlon) * 111.32


def robust_outlier(values, groups, k=10):
    """Flag values above median + k * MAD within each group (MAD floor of 1)."""
    median = values.groupby(groups).transform("median")
    mad = (values - median).abs().groupby(groups).transform("median").clip(lower=1)
    return values > median + k * mad


# -----------------------------------------------------------------------------
# Rules
# -----------------------------------------------------------------------------

def missing_postal_code(df):
    return df["CodigoPostal"].isna() | (df["CodigoPostal"].str.strip() == "")


def invalid_postal_code(df):
    present = ~missing_postal_code(df)
    return present & ~df["CodigoPostal"].str.strip().str.fullmatch(POSTAL_CODE)


PLACEHOLDERS = {"", "n/a", "na", "null", "none", "-"}


def missing_name(df):
    """Name missing or filled with a placeholder such as 'N/A'."""
    return df["Denominacao"].fillna("").str.strip().str.lower().isin(PLACEHOLDERS)


def invalid_modality(df):
    return ~df["Modalidade"].isin(VALID_MODALITIES)


def invalid_geo_reliability(df):
    return ~df["FiabilidadeGeo"].isin(VALID_GEO_RELIABILITY)


def non_positive_capacity(df):
    return pd.to_numeric(df["NrUtentes"], errors="coerce").fillna(0) <= 0


def capacity_outlier(df):
    capacity = pd.to_numeric(df["NrUtentes"], errors="coerce")
    return robust_outlier(capacity, df["Modalidade"]).fillna(False)


def placeholder_or_implausible_date(df):
    registration = parse_date(df["DataRegisto"])
    opening = parse_date(df["DataAberturaPublico"])
    return (registration < MIN_PLAUSIBLE_DATE) | (opening < MIN_PLAUSIBLE_DATE)


def opening_in_future(df, reference_date):
    return parse_date(df["DataAberturaPublico"]) > reference_date


def opening_before_registration(df):
    registration = parse_date(df["DataRegisto"]).dt.normalize()
    opening = parse_date(df["DataAberturaPublico"]).dt.normalize()
    return opening < registration


def duplicate_registration_number(df):
    return df["NrRNAL"].duplicated(keep=False)


def possible_duplicate_establishment(df):
    keys = ["Denominacao", "Endereco", "CodigoPostal", "Modalidade"]
    normalised = df[keys].fillna("").apply(lambda col: col.str.strip().str.lower().str.replace(r"\s+", " ", regex=True))
    return normalised.duplicated(keep=False) & ~missing_name(df)


def non_standard_latlong_format(df):
    return ~df["LatLong"].fillna("").str.fullmatch(LATLONG_STANDARD)


def coordinates_disagree(df, tolerance_km=1.0):
    """The register stores the location twice (X/Y and LatLong). Both should match."""
    lat_xy, lon_xy = web_mercator_to_wgs84(pd.to_numeric(df["X"]), pd.to_numeric(df["Y"]))
    latlong = parse_latlong(df["LatLong"])
    return distance_km(lat_xy, lon_xy, latlong["lat"], latlong["lon"]).fillna(np.inf) > tolerance_km


def postal_code_in_other_municipality(df, min_peers=5, majority=0.9):
    """A postal code normally belongs to one municipality.

    For each 7-digit postal code with enough records, the municipality of the
    majority is taken as the reference; records located in another municipality
    are flagged. The municipality comes from the record's coordinates (DTMNFR).
    """
    valid = ~missing_postal_code(df) & ~invalid_postal_code(df)
    frame = pd.DataFrame({
        "cp": df["CodigoPostal"].where(valid).str.strip(),
        "municipality": df["DTMNFR"].str[:4],
    })
    counts = frame.dropna().groupby(["cp", "municipality"]).size().rename("n").reset_index()
    totals = counts.groupby("cp")["n"].transform("sum")
    counts["share"] = counts["n"] / totals
    reference = counts[(totals >= min_peers) & (counts["share"] >= majority)].set_index("cp")["municipality"]
    expected = frame["cp"].map(reference)
    return expected.notna() & (expected != frame["municipality"])


def is_reliable_geocoding(df):
    """Reliable geocoding, accepting the accent variant flagged separately by V03."""
    return df["FiabilidadeGeo"].isin({"Fiavel", "Fiável"})


def unreliable_geocoding(df):
    return ~is_reliable_geocoding(df)


def build_rules(reference_date):
    return [
        Rule("C01", "Completeness", "Postal code missing", missing_postal_code),
        Rule("C02", "Completeness", "Establishment name missing or placeholder (e.g. 'N/A')", missing_name),
        Rule("V01", "Validity", "Postal code not in NNNN-NNN format", invalid_postal_code),
        Rule("V02", "Validity", "Modality outside the official list", invalid_modality),
        Rule("V03", "Validity", "Geocoding reliability outside the domain (e.g. accent variant 'Fiável')", invalid_geo_reliability),
        Rule("V04", "Validity", "Capacity (NrUtentes) zero or negative", non_positive_capacity),
        Rule("V05", "Validity", "Placeholder or implausible date (before 1950, e.g. 1900-01-01)", placeholder_or_implausible_date),
        Rule("V06", "Validity", "Opening date in the future", lambda df: opening_in_future(df, reference_date)),
        Rule("V07", "Validity", "Coordinates text (LatLong) in a non-standard format", non_standard_latlong_format),
        Rule("U01", "Uniqueness", "Registration number (NrRNAL) duplicated", duplicate_registration_number),
        Rule("U02", "Uniqueness", "Same name, address, postal code and modality in more than one record",
             possible_duplicate_establishment, "confirm with data owner"),
        Rule("K01", "Consistency", "X/Y and LatLong more than 1 km apart", coordinates_disagree),
        Rule("K02", "Consistency", "Located in a different municipality from the rest of its postal code",
             postal_code_in_other_municipality),
        Rule("K03", "Consistency", "Opening date before registration date", opening_before_registration,
             "confirm with data owner"),
        Rule("A01", "Accuracy", "Capacity far above the norm for its modality (median + 10 MAD)",
             capacity_outlier, "confirm with data owner"),
        Rule("A02", "Accuracy", "Geocoding not classified as reliable", unreliable_geocoding,
             "confirm with data owner"),
    ]
