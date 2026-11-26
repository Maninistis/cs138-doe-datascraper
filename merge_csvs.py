
import re
from datetime import date
from pathlib import Path

import pandas as pd

EXTRACTED_DIR = Path("extracted")
MASTER_ORDER_CSV = Path("master_order.csv")
OUTPUT_CSV = Path("doe_visayas_prices.csv")
REGIONAL_OUTPUT_DIR = Path("regional_outputs")

KEY_COLS = ["Region", "Province", "City", "Product"]

DEFAULT_YEAR = 2026

MONTH_MAP = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12,
}

MONTH_PATTERN = (
    r"JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|"
    r"MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|"
    r"SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?"
)

REGION_FILES = {
    "Region VI": "region_vi.csv",
    "Region VII": "region_vii.csv",
    "Region VIII": "region_viii.csv",
    "NIR": "nir.csv",
}


def normalize_key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def week_sort_key(col_name):
    """
    Return the monitoring period's ending date.

    Supported examples:
        October 27 to November 2, 2026
        September 29 to Oct 5, 2026
        November 10-16, 2026
        2026 November 10-16
        10-16 November 2026
        DOE filename containing 102726

    Numeric DOE filenames contain the starting date only.
    For those, the starting date is used as a fallback because
    it preserves chronological ordering between weekly reports.
    """

    text = str(col_name).upper()

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else DEFAULT_YEAR

    # ---------------------------------------------------------
    # 1. Month-name formats
    # ---------------------------------------------------------

    month_matches = list(re.finditer(MONTH_PATTERN, text))

    if month_matches:
        # Use the final month mentioned.
        # This handles ranges crossing months:
        # October 27 to November 2 -> November 2.
        last_month = month_matches[-1]
        month = MONTH_MAP[last_month.group()]

        remaining = text[last_month.end():]

        # Format: November 10-16
        # Format: October 27 to November 2
        day_match = re.match(
            r"\s*(\d{1,2})(?:\s*(?:-|TO|–|—)\s*(\d{1,2}))?",
            remaining
        )

        if day_match:
            day = int(day_match.group(2) or day_match.group(1))

            try:
                return date(year, month, day)
            except ValueError:
                pass

        # Format: 10-16 NOVEMBER 2026
        prefix = text[:last_month.start()]
        prefix_match = re.search(
            r"(\d{1,2})\s*(?:-|TO|–|—)\s*(\d{1,2})\s*$",
            prefix
        )

        if prefix_match:
            day = int(prefix_match.group(2))

            try:
                return date(year, month, day)
            except ValueError:
                pass

    # ---------------------------------------------------------
    # 2. Numeric DOE filename format: MMDDYY
    # ---------------------------------------------------------

    numeric_match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)

    if numeric_match:
        raw = numeric_match.group(1)

        month = int(raw[:2])
        day = int(raw[2:4])
        numeric_year = 2000 + int(raw[4:])

        try:
            return date(numeric_year, month, day)
        except ValueError:
            pass

    # ---------------------------------------------------------
    # 3. Unrecognized dates go last when sorting descending.
    # ---------------------------------------------------------

    return date.min


def load_master_template():
    if not MASTER_ORDER_CSV.exists():
        raise FileNotFoundError(
            f"Missing {MASTER_ORDER_CSV}; keep it beside this script."
        )

    template = pd.read_csv(MASTER_ORDER_CSV, dtype=str).fillna("")

    missing = [c for c in KEY_COLS if c not in template.columns]

    if missing:
        raise ValueError(
            f"{MASTER_ORDER_CSV} is missing columns: {missing}"
        )

    template = template[KEY_COLS].apply(
        lambda s: s.astype(str).str.strip()
    )

    if template.duplicated(KEY_COLS).any():
        raise ValueError(
            "master_order.csv contains duplicate "
            "Region/Province/City/Product keys"
        )

    return template.reset_index(drop=True)


def load_week_csv(csv_path):
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    missing = [c for c in KEY_COLS if c not in df.columns]

    if missing:
        raise ValueError(
            f"{csv_path} is missing key columns: {missing}"
        )

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
        print(
            f"WARNING: {csv_path.name}: "
            f"{duplicates.sum()} duplicate key rows; keeping first"
        )
        df = df.drop_duplicates(key_cols, keep="first")

    return df, week_cols


def main():
    if not EXTRACTED_DIR.exists():
        raise SystemExit(
            f"Missing extracted folder: {EXTRACTED_DIR}"
        )

    csv_files = list(EXTRACTED_DIR.glob("*.csv"))

    if not csv_files:
        raise SystemExit(
            f"No CSV files found in {EXTRACTED_DIR}"
        )

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

        output = output.merge(
            week_df[key_cols + week_cols],
            on=key_cols,
            how="left",
            sort=False,
            validate="one_to_one"
        )

        all_week_cols.extend(
            c for c in week_cols if c not in all_week_cols
        )

        matched = output[week_cols].notna().any(axis=1).sum()

        print(
            f"Merged {csv_path.name}: "
            f"{matched}/{len(master)} template rows matched"
        )

    # ---------------------------------------------------------
    # Sort weekly columns by ending date, newest to oldest.
    # ---------------------------------------------------------

    ordered_weeks = sorted(
        all_week_cols,
        key=week_sort_key,
        reverse=True
    )

    print("\nWeekly column order:")

    for col in ordered_weeks:
        print(f"  {col} -> {week_sort_key(col)}")

    # Arrange final output.
    output = output[KEY_COLS + ordered_weeks]

    output[ordered_weeks] = (
        output[ordered_weeks]
        .fillna("")
        .replace("", "-")
    )

    output.to_csv(OUTPUT_CSV, index=False)

    print(
        f"\nSaved {OUTPUT_CSV}: "
        f"{len(output)} rows, {len(ordered_weeks)} week columns"
    )

    # ---------------------------------------------------------
    # Generate regional CSV files.
    # ---------------------------------------------------------

    REGIONAL_OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    for region, filename in REGION_FILES.items():
        regional = output[
            output["Region"] == region
        ].copy()

        regional.to_csv(
            REGIONAL_OUTPUT_DIR / filename,
            index=False
        )

        print(
            f"Saved {REGIONAL_OUTPUT_DIR / filename}: "
            f"{len(regional)} rows"
        )


if __name__ == "__main__":
    main()
