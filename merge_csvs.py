import re
from pathlib import Path
import pandas as pd

EXTRACTED_DIR = Path("extracted")
MASTER_ORDER_CSV = Path("master_order.csv")
OUTPUT_CSV = Path("doe_visayas_prices.csv")
REGIONAL_OUTPUT_DIR = Path("regional_outputs")
KEY_COLS = ["Region", "Province", "City", "Product"]

MONTH_MAP = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7,
    "JULY": 7, "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9,
    "SEPTEMBER": 9, "OCT": 10, "OCTOBER": 10, "NOV": 11,
    "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}
REGION_FILES = {
    "Western Visayas (REGION VI)": "region_vi_western_visayas.csv",
    "Central Visayas (REGION VII)": "region_vii_central_visayas.csv",
    "Eastern Visayas (REGION VIII)": "region_viii_eastern_visayas.csv",
    "Negros Island Region (NIR)": "nir_negros_island_region.csv",
}


def normalize_key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def week_sort_key(col_name):
    upper = col_name.upper()
    year_match = re.search(r"\b(20\d{2})\b", upper)
    year = int(year_match.group(1)) if year_match else 9999
    month = next((MONTH_MAP[t] for t in re.findall(r"[A-Z]+", upper) if t in MONTH_MAP), 99)
    day_match = re.search(r"\b(\d{1,2})\b", upper)
    day = int(day_match.group(1)) if day_match else 99
    return year, month, day, col_name


def load_master_template():
    if not MASTER_ORDER_CSV.exists():
        raise FileNotFoundError(f"Missing {MASTER_ORDER_CSV}; keep it beside this script.")
    template = pd.read_csv(MASTER_ORDER_CSV, dtype=str).fillna("")
    missing = [c for c in KEY_COLS if c not in template.columns]
    if missing:
        raise ValueError(f"{MASTER_ORDER_CSV} is missing columns: {missing}")
    template = template[KEY_COLS].apply(lambda s: s.astype(str).str.strip())
    if template.duplicated(KEY_COLS).any():
        raise ValueError("master_order.csv contains duplicate Region/Province/City/Product keys")
    return template.reset_index(drop=True)


def load_week_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    missing = [c for c in KEY_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{csv_path} is missing key columns: {missing}")
    week_cols = [c for c in df.columns if c not in KEY_COLS]
    if not week_cols:
        print(f"Skipped {csv_path.name}: no price column")
        return None, []
    df = df[KEY_COLS + week_cols].copy()
    for col in KEY_COLS:
        df[col + "_key"] = df[col].map(normalize_key)
    key_cols = [c + "_key" for c in KEY_COLS]
    duplicates = df.duplicated(key_cols, keep=False)
    if duplicates.any():
        print(f"WARNING: {csv_path.name}: {duplicates.sum()} duplicate key rows; keeping first")
        df = df.drop_duplicates(key_cols, keep="first")
    return df, week_cols


def main():
    if not EXTRACTED_DIR.exists():
        raise SystemExit(f"Missing extracted folder: {EXTRACTED_DIR}")
    csv_files = sorted(EXTRACTED_DIR.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"No CSV files found in {EXTRACTED_DIR}")

    master = load_master_template()
    key_cols = [c + "_key" for c in KEY_COLS]
    output = master.copy()
    for col in KEY_COLS:
        output[col + "_key"] = output[col].map(normalize_key)
    all_week_cols = []

    for csv_path in csv_files:
        week_df, week_cols = load_week_csv(csv_path)
        if week_df is None:
            continue
        # Re-extraction of the same week replaces its old column.
        for col in week_cols:
            if col in output.columns:
                output = output.drop(columns=[col])
        output = output.merge(week_df[key_cols + week_cols], on=key_cols, how="left", sort=False, validate="one_to_one")
        all_week_cols.extend(c for c in week_cols if c not in all_week_cols)
        matched = output[week_cols].notna().any(axis=1).sum()
        print(f"Merged {csv_path.name}: {matched}/{len(master)} template rows matched")

    ordered_weeks = sorted(all_week_cols, key=week_sort_key)
    output = output[KEY_COLS + ordered_weeks]
    output[ordered_weeks] = output[ordered_weeks].fillna("").replace("", "-")
    output.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {OUTPUT_CSV}: {len(output)} rows, {len(ordered_weeks)} week columns")

    REGIONAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for region, filename in REGION_FILES.items():
        regional = output[output["Region"] == region].copy()
        regional.to_csv(REGIONAL_OUTPUT_DIR / filename, index=False)
        print(f"Saved {REGIONAL_OUTPUT_DIR / filename}: {len(regional)} rows")


if __name__ == "__main__":
    main()
