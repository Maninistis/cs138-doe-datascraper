import re
from pathlib import Path

import pandas as pd

INPUT_CSVS = [
    Path("doe_north_luzon_prices1.csv"),
    Path("doe_north_luzon_prices.csv"),
]
OUTPUT_CSV = Path("doe_north_luzon_prices_all_years.csv")
KEY_COLS = ["Region", "Province", "City", "Product"]

REGION_ORDER = ["CAR", "Region I", "Region II", "Region III"]
PRODUCT_ORDER = [
    "RON 100",
    "RON 97",
    "RON 95",
    "RON 91",
    "DIESEL",
    "DIESEL PLUS",
    "KEROSENE",
]
MONTH_MAP = {
    "JAN": 1,
    "JANUARY": 1,
    "FEB": 2,
    "FEBRUARY": 2,
    "MAR": 3,
    "MARCH": 3,
    "APR": 4,
    "APRIL": 4,
    "MAY": 5,
    "JUN": 6,
    "JUNE": 6,
    "JUL": 7,
    "JULY": 7,
    "AUG": 8,
    "AUGUST": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCT": 10,
    "OCTOBER": 10,
    "NOV": 11,
    "NOVEMBER": 11,
    "DEC": 12,
    "DECEMBER": 12,
}


def normalize_week_column(name):
    if re.fullmatch(r"nluz_(?:car|regi|regii|regiii)_mar-11-17-pdf", name, re.I):
        return "March 11-17, 2025"
    return name


def week_sort_key(name):
    upper = name.upper()
    year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", upper)
    year = int(year_match.group(1)) if year_match else 9999

    month = 99
    for token in re.findall(r"[A-Z]+", upper):
        if token in MONTH_MAP:
            month = MONTH_MAP[token]
            break

    day_match = re.search(r"\b(\d{1,2})\b", upper)
    day = int(day_match.group(1)) if day_match else 99
    return year, month, day, name


def sort_rows(df):
    region_rank = {region: index for index, region in enumerate(REGION_ORDER)}
    product_rank = {product: index for index, product in enumerate(PRODUCT_ORDER)}
    result = df.copy()
    result["_region_rank"] = result["Region"].map(lambda value: region_rank.get(value, 999))
    result["_product_rank"] = result["Product"].map(lambda value: product_rank.get(value, 999))
    return (
        result.sort_values(
            ["_region_rank", "Region", "Province", "City", "_product_rank"],
            kind="stable",
        )
        .drop(columns=["_region_rank", "_product_rank"])
        .reset_index(drop=True)
    )


def load_master(path):
    if not path.exists():
        raise FileNotFoundError(f"Required compiled CSV not found: {path}")

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [column for column in KEY_COLS if column not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    for column in KEY_COLS:
        df[column] = df[column].str.strip()

    week_columns = [column for column in df.columns if column not in KEY_COLS]
    normalized = {}
    for column in week_columns:
        week_name = normalize_week_column(column)
        values = df[column].replace({"-": pd.NA, "": pd.NA})
        if week_name in normalized:
            conflicts = normalized[week_name].notna() & values.notna() & normalized[week_name].ne(values)
            if conflicts.any():
                raise ValueError(f"{path} has conflicting values in duplicate week columns: {week_name}")
            normalized[week_name] = normalized[week_name].combine_first(values)
        else:
            normalized[week_name] = values

    return pd.concat([df[KEY_COLS], pd.DataFrame(normalized)], axis=1)


def main():
    frames = []
    for path in INPUT_CSVS:
        df = load_master(path)
        frames.append(df)
        print(f"Loaded {path} ({len(df)} rows)")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    week_columns = [column for column in combined.columns if column not in KEY_COLS]
    conflicts = combined.groupby(KEY_COLS, dropna=False)[week_columns].nunique(dropna=True).gt(1)
    if conflicts.any(axis=None):
        raise ValueError("The compiled files contain conflicting prices for the same location, product, and week.")

    merged = combined.groupby(KEY_COLS, as_index=False, dropna=False)[week_columns].first()
    merged[week_columns] = merged[week_columns].fillna("-")
    week_columns = sorted(week_columns, key=week_sort_key)
    merged = merged[KEY_COLS + week_columns]
    merged = sort_rows(merged)
    merged.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {OUTPUT_CSV} ({len(merged)} rows, {len(week_columns)} weeks)")

    unparsed = [column for column in week_columns if week_sort_key(column)[0] == 9999]
    if unparsed:
        print("Week labels preserved without date sorting:", ", ".join(unparsed))


if __name__ == "__main__":
    main()
