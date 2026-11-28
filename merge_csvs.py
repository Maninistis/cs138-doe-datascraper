import difflib
import re
from pathlib import Path
import pandas as pd

EXTRACTED_DIR = Path("extracted")
OUTPUT_CSV = Path("doe_north_luzon_prices1.csv")
KEY_COLS = ["Region", "Province", "City", "Product"]

REGION_ORDER = [
    "CAR",
    "Region I",
    "Region II",
    "Region III",
]

REGION_PATTERNS = [
    (re.compile(r"\bREGION\s*III\b", re.IGNORECASE), "Region III"),
    (re.compile(r"\bREGION\s*II\b", re.IGNORECASE), "Region II"),
    (re.compile(r"\bREGION\s*I\b", re.IGNORECASE), "Region I"),
    (re.compile(r"\bCAR\b", re.IGNORECASE), "CAR"),
]

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


def normalize_region(value):
    text = str(value).strip()
    for pattern, canonical in REGION_PATTERNS:
        if pattern.search(text):
            return canonical
    return text


def week_sort_key(col_name):
    upper = col_name.upper()
    year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", upper)
    year = int(year_match.group(1)) if year_match else 9999

    month = 99
    for token in re.findall(r"[A-Z]+", upper):
        if token in MONTH_MAP:
            month = MONTH_MAP[token]
            break

    day_match = re.search(r"\b(\d{1,2})\b", upper)
    day = int(day_match.group(1)) if day_match else 99

    return (year, month, day, col_name)


def canonical_week_label(text):
    upper = str(text).upper()
    year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", upper)
    if not year_match:
        return str(text).strip()

    year = int(year_match.group(1))
    month_pattern = re.compile(
        r"(?<![A-Z])("
        r"JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|"
        r"MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:TEMBER)?|"
        r"OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?"
        r")(?![A-Z])"
    )
    month_matches = list(month_pattern.finditer(upper))
    if not month_matches:
        return str(text).strip()

    def month_number(token):
        token = token[:3]
        return MONTH_MAP.get(token, 99)

    def day_after(match):
        tail = upper[match.end():year_match.start()]
        numbers = re.findall(r"(?<!\d)(\d{1,2})(?!\d)", tail)
        for number in numbers:
            day = int(number)
            if 1 <= day <= 31:
                return day
        return None

    def days_before(match):
        head = upper[:match.start()]
        numbers = [
            int(number)
            for number in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", head)
            if 1 <= int(number) <= 31
        ]
        if len(numbers) >= 2:
            return numbers[-2], numbers[-1]
        if numbers:
            return numbers[-1], numbers[-1]
        return None, None

    first_match = month_matches[0]
    start_month = month_number(first_match.group(1))
    start_day = day_after(first_match)
    if start_day is None:
        start_day, end_day_before = days_before(first_match)
        if start_day is None:
            return str(text).strip()
    else:
        end_day_before = None

    distinct_month_matches = []
    for match in month_matches:
        if not distinct_month_matches or match.group(1)[:3] != distinct_month_matches[-1].group(1)[:3]:
            distinct_month_matches.append(match)

    if len(distinct_month_matches) >= 2:
        end_match = distinct_month_matches[1]
        end_month = month_number(end_match.group(1))
        end_day = day_after(end_match)
    else:
        if end_day_before is not None:
            end_day = end_day_before
        else:
            tail = upper[first_match.end():year_match.start()]
            numbers = [
                int(number)
                for number in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", tail)
                if 1 <= int(number) <= 31
            ]
            end_day = numbers[-1] if len(numbers) > 1 else start_day
        end_month = start_month

    if end_day is None:
        return str(text).strip()

    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }
    if start_month == end_month:
        if end_day == start_day:
            return f"{month_names[start_month]} {start_day}, {year}"
        return f"{month_names[start_month]} {start_day}-{end_day}, {year}"
    return (
        f"{month_names[start_month]} {start_day}-"
        f"{month_names[end_month]} {end_day}, {year}"
    )


def canonicalize_week_columns(df):
    df = df.copy()
    week_cols = [c for c in df.columns if c not in KEY_COLS]
    renames = {}
    for week_col in week_cols:
        canonical = canonical_week_label(week_col)
        renames.setdefault(week_col, canonical)

    return df.rename(columns=renames)


def build_known_places(dfs):
    known = {}
    for df in dfs:
        valid = df[(df["Province"] != "") & (df["City"] != "")]
        for region, prov, city in valid[["Region", "Province", "City"]].drop_duplicates().itertuples(index=False):
            known.setdefault(region, set()).add((prov, city))
    return known


def repair_place_columns(df, known_places):
    df = df.copy()

    for idx, row in df.iterrows():
        region = row["Region"]
        prov = row["Province"]
        city = row["City"]

        if prov or not city or region not in known_places:
            continue

        candidates = known_places[region]
        combined_map = {f"{p} {c}": (p, c) for p, c in candidates}

        if city in combined_map:
            df.at[idx, "Province"], df.at[idx, "City"] = combined_map[city]
            continue

        suffix_matches = [(p, c) for p, c in candidates if city.endswith(f" {c}") or city == c]
        if len(suffix_matches) == 1:
            df.at[idx, "Province"], df.at[idx, "City"] = suffix_matches[0]
            continue

        close = difflib.get_close_matches(city, combined_map.keys(), n=1, cutoff=0.8)
        if close:
            df.at[idx, "Province"], df.at[idx, "City"] = combined_map[close[0]]

    return df


def coalesce_by_keys(df, week_cols):
    cleaned = df.copy()
    for col in week_cols:
        cleaned[col] = cleaned[col].replace({"-": pd.NA, "": pd.NA})

    grouped = cleaned.groupby(KEY_COLS, as_index=False, dropna=False)[week_cols].first()
    return grouped.fillna("-")


def sort_master_df(df):
    region_rank = {r: i for i, r in enumerate(REGION_ORDER)}
    product_rank = {p: i for i, p in enumerate(PRODUCT_ORDER)}

    df = df.copy()
    df["_r"] = df["Region"].map(lambda x: region_rank.get(x, 999))
    df["_p"] = df["Product"].map(lambda x: product_rank.get(x, 999))
    df = df.sort_values(
        by=["_r", "Region", "Province", "City", "_p"],
        kind="stable",
    )
    return df.drop(columns=["_r", "_p"]).reset_index(drop=True)


csv_files = sorted(EXTRACTED_DIR.glob("*.csv"))
if not csv_files:
    raise SystemExit(f"No CSV files found in {EXTRACTED_DIR}")

loaded_dfs = []
for csv_path in csv_files:
    df = pd.read_csv(csv_path, dtype=str)
    for col in KEY_COLS:
        if col in df.columns:
            df[col] = df[col].fillna("").str.strip()
    if "Region" in df.columns:
        df["Region"] = df["Region"].map(normalize_region)
    df = canonicalize_week_columns(df)
    week_cols = [c for c in df.columns if c not in KEY_COLS]
    if week_cols:
        loaded_dfs.append((csv_path.name, df[KEY_COLS + week_cols], week_cols))

known_places = build_known_places([df for _, df, _ in loaded_dfs])
prepared_dfs = []

for name, df, week_cols in loaded_dfs:
    df = repair_place_columns(df, known_places)
    df = coalesce_by_keys(df, week_cols)
    prepared_dfs.append(df)
    print(f"Prepared {name} ({len(df)} rows)")

if prepared_dfs:
    master_df = pd.concat(prepared_dfs, ignore_index=True, sort=False)
else:
    master_df = pd.DataFrame(columns=KEY_COLS)

all_week_cols = sorted(
    [c for c in master_df.columns if c not in KEY_COLS],
    key=week_sort_key,
)
master_df = coalesce_by_keys(master_df, all_week_cols)

master_df = master_df[KEY_COLS + all_week_cols]
master_df = sort_master_df(master_df)
master_df = master_df.fillna("-")

master_df.to_csv(OUTPUT_CSV, index=False)
print(f"Saved {OUTPUT_CSV} ({len(master_df)} rows, {len(all_week_cols)} weeks)")