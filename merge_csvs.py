
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
    Sort weekly columns by monitoring period END date.

    Supports:
    - petro_vis_2025-mar-18-24
    - petro_vis_2025-feb-25-mar-3
    - petro_vis_2024-dec-31-jan-6
    - December 31, 2024 to January 6, 2025
    - October 27 to November 2, 2026
    - November 10-16, 2026
    - DOE numeric filenames such as 102726

    Returns a date for chronological sorting.
    """

    text = str(col_name).upper()

    # -----------------------------------------------------
    # 1. New DOE filename format:
    #    YYYY-MON-DD-DD
    #    YYYY-MON-DD-MON-DD
    # -----------------------------------------------------

    filename_pattern = (
        r"(?<!\d)(20\d{2})[-_ ]"
        r"([A-Z]{3,9})[-_ ](\d{1,2})"
        r"(?:[-_ ]([A-Z]{3,9})[-_ ](\d{1,2})"
        r"|[-_ ](\d{1,2}))"
    )

    match = re.search(filename_pattern, text)

    if match:
        start_year = int(match.group(1))
        start_month = MONTH_MAP.get(match.group(2))
        end_month_name = match.group(4)
        end_day_same_month = match.group(6)

        if end_month_name:
            end_month = MONTH_MAP.get(end_month_name)
            end_day = int(match.group(5))
        else:
            end_month = start_month
            end_day = int(end_day_same_month)

        if start_month and end_month:
            end_year = start_year

            # Handle year-crossing weeks:
            # December 31 to January 6.
            if end_month < start_month:
                end_year += 1

            try:
                return date(end_year, end_month, end_day)
            except ValueError:
                pass

    # -----------------------------------------------------
    # 2. Month-name date formats:
    #    October 27 to November 2, 2026
    #    November 10-16, 2026
    #    December 31, 2024 to January 6, 2025
    # -----------------------------------------------------

    month_pattern = (
        r"JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|"
        r"MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|"
        r"SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?"
    )

    month_day_pattern = (
        rf"({month_pattern})\s+(\d{{1,2}})"
        rf"(?:,?\s*(20\d{{2}}))?"
    )

    matches = list(re.finditer(month_day_pattern, text))

    if matches:
        last = matches[-1]

        month = MONTH_MAP[last.group(1)]
        day = int(last.group(2))

        # Prefer the year explicitly attached to the ending date.
        if last.group(3):
            year = int(last.group(3))
        else:
            years = re.findall(r"\b(20\d{2})\b", text)
            year = int(years[-1]) if years else DEFAULT_YEAR

            # If the week crosses December into January.
            if len(matches) >= 2:
                first_month = MONTH_MAP[matches[0].group(1)]

                if month < first_month:
                    year += 1

        try:
            return date(year, month, day)
        except ValueError:
            pass

        # Handle single-month ranges such as November 10-16.
        remaining = text[last.end():]

        day_match = re.match(
            r"\s*(?:-|TO|–|—)\s*(\d{1,2})",
            remaining
        )

        if day_match:
            try:
                return date(year, month, int(day_match.group(1)))
            except ValueError:
                pass

    # -----------------------------------------------------
    # 3. Numeric DOE filename format: MMDDYY
    #    Example: 102726 = October 27, 2026
    # -----------------------------------------------------

    numeric_match = re.search(
        r"(?<!\d)(\d{6})(?!\d)",
        text
    )

    if numeric_match:
        raw = numeric_match.group(1)

        month = int(raw[:2])
        day = int(raw[2:4])
        year = 2000 + int(raw[4:])

        try:
            return date(year, month, day)
        except ValueError:
            pass

    # -----------------------------------------------------
    # 4. Unrecognized dates go last in descending order.
    # -----------------------------------------------------

    return date.min


def load_master_template():
    if not MASTER_ORDER_CSV.exists():
        raise FileNotFoundError(
            f"Missing {MASTER_ORDER_CSV}; keep it beside this script."
        )

    template = pd.read_csv(
        MASTER_ORDER_CSV,
        dtype=str
    ).fillna("")

    missing = [
        c for c in KEY_COLS
        if c not in template.columns
    ]

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
