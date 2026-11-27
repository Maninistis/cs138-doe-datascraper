import re
import statistics
from pathlib import Path
import fitz
import pandas as pd
import pdfplumber

INPUT_FOLDER = Path("pdfs")
OUTPUT_DIR = Path("extracted")
SKIP_EXISTING = False
MASTER_ORDER_CSV = Path("master_order.csv")
KEY_COLS = ["Region", "Province", "City", "Product"]

REGION_ORDER = [
    "National Capital Region (NCR)",
    "Cordillera Administrative Region (CAR)",
    "Ilocos Region (REGION I)",
    "Cagayan Valley (REGION II)",
    "Central Luzon (REGION III)",
    "CALABARZON (REGION IV-A)",
    "MIMAROPA (REGION IV-B)",
    "Bicol Region (REGION V)",
    "Western Visayas (REGION VI)",
    "Negros Island Region (NIR)",
    "Central Visayas (REGION VII)",
    "Eastern Visayas (REGION VIII)",
    "Zamboanga Peninsula (REGION IX)",
    "Northern Mindanao (REGION X)",
    "Davao Region (REGION XI)",
    "SOCCSKSARGEN (REGION XII)",
    "Caraga (REGION XIII)",
    "Bangsamoro Autonomous Region in Muslim Mindanao (BARMM)",
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

PRODUCT_RANK = {p: i for i, p in enumerate(PRODUCT_ORDER)}

PRODUCT_PATTERNS = [
    ("RON 100", re.compile(r"\bR[O0]N\s*100\b")),
    ("RON 97", re.compile(r"\bR[O0]N\s*97\b")),
    ("RON 95", re.compile(r"\bR[O0]N\s*9[56]\b")),
    ("RON 91", re.compile(r"\bR[O0]N\s*91\b")),
    ("DIESEL PLUS", re.compile(r"\bDIESEL\s*PLUS\b")),
    ("DIESEL", re.compile(r"\bDIESEL\b")),
    ("KEROSENE", re.compile(r"\bKEROSENE\b")),
]

CYRILLIC_MAP = str.maketrans("АВЕКМНОРСТХавекмнорстх", "ABEKMHOPCTXabekmhopctx")

PROVINCE_FIXES = {
    "ILOCOSNORTE": "ILOCOS NORTE",
    "ILOCOSSUR": "ILOCOS SUR",
    "LAUNION": "LA UNION",
    "NUEVAVIZCAYA": "NUEVA VIZCAYA",
    "NUEVAECIJA": "NUEVA ECIJA",
}

ocr_engine = None


def get_ocr_engine():
    global ocr_engine
    if ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "OCR fallback is required for this scanned page, but "
                "rapidocr-onnxruntime is not installed. Install it with: "
                "pip install rapidocr-onnxruntime"
            ) from exc
        ocr_engine = RapidOCR()
    return ocr_engine


def clean_text(text):
    if not text:
        return ""
    return " ".join(text.translate(CYRILLIC_MAP).split()).strip()


def extract_week_label(pdf_path):
    stem = pdf_path.stem
    match = re.search(
        r"(\d{1,2}\s*-\s*\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2}\s*-\s*\d{1,2},?\s+\d{4})",
        stem,
    )
    if match:
        return clean_text(match.group(1))
    return stem


def safe_filename(label):
    return re.sub(r'[\\/:*?"<>|]', "_", label).strip()


def normalize_region(text):
    upper = clean_text(text).upper()
    if "NATIONAL CAPITAL" in upper or "(NCR)" in upper:
        return "National Capital Region (NCR)"
    if "CORDILLERA" in upper or "(CAR)" in upper:
        return "Cordillera Administrative Region (CAR)"
    if "BANGSAMORO" in upper or "BARMM" in upper or "ARMM" in upper:
        return "Bangsamoro Autonomous Region in Muslim Mindanao (BARMM)"
    if "NEGROS ISLAND" in upper or "(NIR)" in upper:
        return "Negros Island Region (NIR)"
    if "CALABARZON" in upper or "IV-A" in upper or "IVA" in upper:
        return "CALABARZON (REGION IV-A)"
    if "MIMAROPA" in upper or "IV-B" in upper or "IVB" in upper:
        return "MIMAROPA (REGION IV-B)"
    if "BICOL" in upper or "REGION V)" in upper or "REGION 5" in upper:
        return "Bicol Region (REGION V)"
    if "WESTERN VISAYAS" in upper or "REGION VI)" in upper or "REGION 6" in upper:
        return "Western Visayas (REGION VI)"
    if "CENTRAL VISAYAS" in upper or "REGION VII)" in upper or "REGION 7" in upper:
        return "Central Visayas (REGION VII)"
    if "EASTERN VISAYAS" in upper or "REGION VIII)" in upper or "REGION 8" in upper:
        return "Eastern Visayas (REGION VIII)"
    if "ZAMBOANGA" in upper or "REGION IX)" in upper or "REGION 9" in upper:
        return "Zamboanga Peninsula (REGION IX)"
    if "NORTHERN MINDANAO" in upper or "REGION X)" in upper or "REGION 10" in upper:
        return "Northern Mindanao (REGION X)"
    if "DAVAO" in upper or "REGION XI)" in upper or "REGION 11" in upper:
        return "Davao Region (REGION XI)"
    if "SOCCSKSARGEN" in upper or "REGION XII)" in upper or "REGION 12" in upper:
        return "SOCCSKSARGEN (REGION XII)"
    if "CARAGA" in upper or "REGION XIII)" in upper or "REGION 13" in upper:
        return "Caraga (REGION XIII)"
    if "CENTRAL LUZON" in upper or "REGION III" in upper:
        return "Central Luzon (REGION III)"
    if "CAGAYAN" in upper or "REGION II" in upper:
        return "Cagayan Valley (REGION II)"
    if "ILOCOS" in upper or "LLOCOS" in upper or "LOCOS" in upper or "REGION I)" in upper:
        return "Ilocos Region (REGION I)"
    if "REGION" in upper and "MONITORING" not in upper:
        return clean_text(text)
    return ""


def extract_region(lines):
    for line in lines:
        region = normalize_region(" ".join(w["text"] for w in line))
        if region:
            return region
    return ""


def group_words_into_lines(words, y_tol=4.5):
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    current = [sorted_words[0]]
    current_y = sorted_words[0]["top"]

    for word in sorted_words[1:]:
        if abs(word["top"] - current_y) <= y_tol:
            current.append(word)
        else:
            lines.append(sorted(current, key=lambda w: w["x0"]))
            current = [word]
            current_y = word["top"]

    lines.append(sorted(current, key=lambda w: w["x0"]))
    return lines


def extract_fitz_words(fitz_page):
    raw_words = fitz_page.get_text("words")
    return [
        {
            "text": w[4].strip(),
            "x0": w[0],
            "top": w[1],
            "x1": w[2],
            "bottom": w[3],
        }
        for w in raw_words
        if w[4].strip()
    ]


def ocr_page_to_words(fitz_page, zoom=2.0):
    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    result, _ = get_ocr_engine()(pix.tobytes("png"))
    words = []
    if not result:
        return words

    for box, text, _ in result:
        cleaned = text.strip()
        if not cleaned:
            continue
        xs = [pt[0] / zoom for pt in box]
        ys = [pt[1] / zoom for pt in box]
        words.append({
            "text": cleaned,
            "x0": min(xs),
            "x1": max(xs),
            "top": min(ys),
            "bottom": max(ys),
        })
    return words


def deskew_words(words):
    prov_word = next((w for w in words if "PROVINCE" in w["text"].upper()), None)
    if not prov_word:
        return words

    hy = prov_word["top"]
    header_band = [w for w in words if abs(w["top"] - hy) <= 30]

    left_anchors = [
        w for w in header_band
        if any(k in w["text"].upper() for k in ("PROVINCE", "PRODUCT"))
    ]
    right_anchors = [
        w for w in header_band
        if any(k in w["text"].upper() for k in ("INDEPENDENT", "RANGE", "OVERALL"))
        and w["x0"] > 450
    ]

    if not left_anchors or not right_anchors:
        return words

    x_left = sum((w["x0"] + w["x1"]) / 2 for w in left_anchors) / len(left_anchors)
    y_left = sum((w["top"] + w["bottom"]) / 2 for w in left_anchors) / len(left_anchors)
    x_right = sum((w["x0"] + w["x1"]) / 2 for w in right_anchors) / len(right_anchors)
    y_right = sum((w["top"] + w["bottom"]) / 2 for w in right_anchors) / len(right_anchors)

    if x_right - x_left < 200:
        return words

    slope = (y_right - y_left) / (x_right - x_left)
    if abs(slope) > 0.05:
        return words

    deskewed = []
    for w in words:
        dy = slope * (w["x0"] - x_left)
        deskewed.append({
            **w,
            "top": w["top"] - dy,
            "bottom": w["bottom"] - dy,
        })
    return deskewed


def find_column_bounds(words, fallback_bounds=None):
    prov_word = next((w for w in words if "PROVINCE" in w["text"].upper()), None)
    header_bottom = 0
    prov_max = None
    common_min_x = None
    overall_min_x = None

    if prov_word:
        hy = prov_word["top"]
        header_band = [w for w in words if abs(w["top"] - hy) <= 25]
        city_words = [
            w for w in header_band
            if "CITY" in w["text"].upper() or "MUNICIPAL" in w["text"].upper()
        ]
        prod_words = [w for w in header_band if "PRODUCT" in w["text"].upper()]
        common_words = [
            w for w in header_band
            if "COMMON" in w["text"].upper()
        ]
        overall_words = [
            w for w in header_band
            if w["text"].upper() in {"OVERALL", "RANGE"}
        ]

        header_bottom = max(
            w["bottom"] for w in [prov_word] + city_words + prod_words + common_words
        )
        if city_words:
            prov_max = (prov_word["x1"] + min(w["x0"] for w in city_words)) / 2
        if common_words:
            # Start of the COMMON column, slightly left of its header text.
            # Estimate the actual cell boundary from the gap between the
            # OVERALL RANGE and COMMON PRICE header labels.
            common_header_left = min(w["x0"] for w in common_words)
            overall_header_right = max(
                (w["x1"] for w in header_band
                 if w["text"].upper() in {"OVERALL", "RANGE"}),
                default=common_header_left - 20,
            )
            common_min_x = (overall_header_right + common_header_left) / 2
        if overall_words:
            # The OVERALL RANGE column is immediately to the left of COMMON.
            overall_header_left = min(w["x0"] for w in overall_words)
            independent_right = max(
                (w["x1"] for w in header_band
                 if "INDEPENDENT" in w["text"].upper()),
                default=overall_header_left - 20,
            )
            overall_min_x = (independent_right + overall_header_left) / 2

    body_prod_x0 = [
        w["x0"] for w in words
        if w["top"] > header_bottom
        and re.match(r"^(R[O0]N|DIESEL|KEROSENE)\b", w["text"].upper())
    ]

    if body_prod_x0:
        prod_x0 = sorted(body_prod_x0)[len(body_prod_x0) // 2]
        if prov_max is None:
            prov_max = fallback_bounds["prov_max"] if fallback_bounds else prod_x0 * 0.45
        if common_min_x is None:
            max_x = max(w["x1"] for w in words)
            common_min_x = fallback_bounds["common_min_x"] if fallback_bounds else max_x - 75
        if overall_min_x is None:
            overall_min_x = fallback_bounds["overall_min_x"] if fallback_bounds else common_min_x - 85
        return {
            "prov_max": prov_max,
            "city_max": prod_x0 - 4,
            "prod_max": prod_x0 + 85,
            "overall_min_x": overall_min_x,
            "common_min_x": common_min_x,
            "header_bottom": header_bottom,
        }

    return fallback_bounds


def match_product_from_words(band_words, city_max, prod_max):
    pw = [
        w["text"] for w in sorted(band_words, key=lambda x: x["x0"])
        if city_max <= (w["x0"] + w["x1"]) / 2 <= prod_max
    ]
    text = " ".join(pw).upper()
    for name, pattern in PRODUCT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def parse_price_token(token):
    t = token.strip()
    if not t or "#" in t or "N/A" in t.upper():
        return None
    matches = re.findall(r"\d+\.\d{2}", t)
    if matches:
        return float(matches[-1])
    match = re.search(r"\d+\.\d+", t)
    return float(match.group()) if match else None


def extract_common_price_from_band(band_words, line, overall_min_x, common_min_x):
    """Choose COMMON first; otherwise use the midpoint of OVERALL RANGE.

    A blank/dash cell is missing data. Never fall back to unrelated numbers
    elsewhere on the product row (e.g. brand or independent-station columns).
    """
    ordered = sorted(band_words, key=lambda x: x["x0"])

    # 1) COMMON overrides all other values when it contains a valid price.
    common_values = []
    for w in ordered:
        center = (w["x0"] + w["x1"]) / 2
        if center >= common_min_x:
            value = parse_price_token(w["text"])
            if value is not None:
                common_values.append(value)
    if common_values:
        return common_values[-1]

    # 2) Otherwise, derive a value only from the OVERALL RANGE column.
    range_values = []
    for w in ordered:
        center = (w["x0"] + w["x1"]) / 2
        if overall_min_x <= center < common_min_x:
            # A range may be split across separate text boxes, e.g. 82.15 - 89.60.
            found = re.findall(r"\d+(?:\.\d+)?", w["text"].replace(",", ""))
            range_values.extend(float(v) for v in found)

    if not range_values:
        return "-"
    if len(range_values) == 1:
        return range_values[0]
    return round((min(range_values) + max(range_values)) / 2, 2)


def join_place(words, is_province=False):
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: (round(w["top"] / 6), w["x0"]))
    s = clean_text(" ".join(w["text"] for w in ordered)).upper()
    s = re.sub(r"([A-Z])CITY\b", r"\1 CITY", s)
    s = re.sub(r"\bCITYOF", "CITY OF ", s)
    s = re.sub(r"\bSCIENCECITY", "SCIENCE CITY", s)
    s = re.sub(r"\bSAN(FERNANDO|JOSE|CARLOS|RAFAEL)\b", r"SAN \1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if is_province:
        compact = s.replace(" ", "")
        if compact in PROVINCE_FIXES:
            return PROVINCE_FIXES[compact]
    return s




def resolve_city_name(words, master_template):
    """Resolve a city from text inside the city column using master-order names.

    Merged province cells and repeated table headers can leak into the city
    column's vertical band. Matching against known city names prevents those
    labels from being concatenated into invalid locations.
    """
    text_key = normalize_key(" ".join(w["text"] for w in words))
    if not text_key:
        return ""
    cities = sorted(
        {str(v).strip().upper() for v in master_template["City"].tolist() if str(v).strip()},
        key=lambda value: len(normalize_key(value)),
        reverse=True,
    )
    matches = [
        city for city in cities
        if normalize_key(city) and normalize_key(city) in text_key
    ]
    return matches[0] if matches else join_place(words, is_province=False)


def normalize_key(value):
    """Normalize a location/product label for matching against the master template."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def load_master_template():
    if not MASTER_ORDER_CSV.exists():
        raise FileNotFoundError(
            f"Missing {MASTER_ORDER_CSV}. Keep master_order.csv beside this script."
        )
    template = pd.read_csv(MASTER_ORDER_CSV, dtype=str).fillna("")
    required = KEY_COLS
    missing = [c for c in required if c not in template.columns]
    if missing:
        raise ValueError(f"{MASTER_ORDER_CSV} is missing columns: {missing}")
    template = template[KEY_COLS].copy()
    for col in KEY_COLS:
        template[col] = template[col].astype(str).str.strip().str.upper()
    return template.drop_duplicates(KEY_COLS, keep="first").reset_index(drop=True)


def apply_master_order(df, week_col, master_template):
    """Canonicalize extracted city names globally, classify by province, and
    return only master-template keys in the exact master order.

    City is the strongest location signal in this Visayas sheet: OCR can shift
    province labels vertically or carry a previous province into the next block.
    Therefore, a unique city match in the master overrides the extracted province.
    Unknown/ambiguous locations are excluded rather than guessed.
    """
    template = master_template.copy()
    if df.empty:
        return template.assign(**{week_col: "-"})[KEY_COLS + [week_col]]

    def norm(value):
        return normalize_key(value)

    # Build city -> canonical location lookup. Only unique city names are safe
    # to resolve without a reliable province label.
    city_groups = {}
    for _, row in template.drop_duplicates(["Province", "City"]).iterrows():
        city_groups.setdefault(norm(row["City"]), []).append((row["Region"], row["Province"], row["City"]))

    canonical_rows = []
    for _, row in df.iterrows():
        city_key = norm(row.get("City", ""))
        province_key = norm(row.get("Province", ""))
        candidates = city_groups.get(city_key, [])
        chosen = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1:
            matches = [x for x in candidates if norm(x[1]) == province_key]
            if len(matches) == 1:
                chosen = matches[0]
        if chosen is None:
            continue
        region, province, city = chosen
        canonical_rows.append({
            "Region": region, "Province": province, "City": city,
            "Product": str(row.get("Product", "")).strip().upper(),
            week_col: row.get(week_col, "-"),
        })

    working = pd.DataFrame(canonical_rows, columns=KEY_COLS + [week_col])
    if working.empty:
        return template.assign(**{week_col: "-"})[KEY_COLS + [week_col]]
    working = working.drop_duplicates(KEY_COLS, keep="first")
    result = template.merge(working, on=KEY_COLS, how="left", sort=False)
    result[week_col] = result[week_col].fillna("-").replace("", "-")
    return result[KEY_COLS + [week_col]]


PROVINCE_TO_REGION = {
    "AKLAN": "Western Visayas (REGION VI)", "ANTIQUE": "Western Visayas (REGION VI)",
    "CAPIZ": "Western Visayas (REGION VI)", "GUIMARAS": "Western Visayas (REGION VI)",
    "ILOILO": "Western Visayas (REGION VI)", "ILOILO CITY": "Western Visayas (REGION VI)",
    "BOHOL": "Central Visayas (REGION VII)", "CEBU": "Central Visayas (REGION VII)",
    "BILIRAN": "Eastern Visayas (REGION VIII)", "EASTERN SAMAR": "Eastern Visayas (REGION VIII)",
    "LEYTE": "Eastern Visayas (REGION VIII)", "NORTHERN SAMAR": "Eastern Visayas (REGION VIII)",
    "SAMAR": "Eastern Visayas (REGION VIII)", "WESTERN SAMAR": "Eastern Visayas (REGION VIII)",
    "SOUTHERN LEYTE": "Eastern Visayas (REGION VIII)",
    "NEGROS OCCIDENTAL": "Negros Island Region (NIR)",
    "NEGROS ORIENTAL": "Negros Island Region (NIR)", "SIQUIJOR": "Negros Island Region (NIR)",
}

def region_for_province(province):
    key = clean_text(province).upper()
    key = re.sub(r"\s+", " ", key).strip()
    return PROVINCE_TO_REGION.get(key, "")



def parse_pdf(pdf_path, week_col):
    records = []
    fitz_doc = fitz.open(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        last_region = ""
        last_province = ""
        last_bounds = None

        for idx, page in enumerate(pdf.pages):
            # Prefer PyMuPDF's native word boxes. For DOE PDFs with an embedded
            # text layer, these boxes align more consistently with the visible
            # table cells than pdfplumber's reconstructed word grouping.
            fitz_words = extract_fitz_words(fitz_doc[idx])
            if len(fitz_words) >= 15:
                words = fitz_words
                source = "fitz"
            else:
                words = page.extract_words(
                    x_tolerance=1.5,
                    y_tolerance=2,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
                source = "pdfplumber"
                if len(words) < 15:
                    words = ocr_page_to_words(fitz_doc[idx], zoom=3.0)
                    source = "ocr"

            if not words:
                print(f"  p{idx + 1} [{source}]: 0 words")
                continue

            words = deskew_words(words)

            y_tol = 5.0 if source == "ocr" else 3.5
            lines = group_words_into_lines(words, y_tol=y_tol)

            # Region is assigned from the canonical province after city matching;
            # page headers such as "REGIONS 6-8 & NIR" are not a valid region.

            bounds = find_column_bounds(words, fallback_bounds=last_bounds)
            if not bounds:
                print(f"  p{idx + 1} [{source}]: no bounds")
                continue
            last_bounds = bounds

            prov_max = bounds["prov_max"]
            city_max = bounds["city_max"]
            prod_max = bounds["prod_max"]
            common_min_x = bounds["common_min_x"]
            overall_min_x = bounds["overall_min_x"]
            header_bottom = bounds["header_bottom"]

            body_words = [w for w in words if w["top"] > header_bottom + 1]
            product_rows = []

            for line in lines:
                y_top = min(w["top"] for w in line)
                y_bot = max(w["bottom"] for w in line)
                if y_top <= header_bottom + 1:
                    continue

                prod = match_product_from_words(line, city_max, prod_max)
                if not prod:
                    continue

                # Use the product-label center as the row anchor. Numeric cells
                # can have slightly different text-box heights/vertical offsets
                # from the product label, especially in these table PDFs.
                label_words = [
                    w for w in line
                    if city_max <= (w["x0"] + w["x1"]) / 2 <= prod_max
                ]
                row_center = statistics.median(
                    (w["top"] + w["bottom"]) / 2 for w in label_words
                ) if label_words else (y_top + y_bot) / 2

                # Estimate row spacing from neighboring product labels on this
                # page. Keep the band well inside half a row to avoid borrowing
                # prices from the product immediately above/below.
                product_centers = []
                for candidate_line in lines:
                    cy_top = min(w["top"] for w in candidate_line)
                    if cy_top <= header_bottom + 1:
                        continue
                    if match_product_from_words(candidate_line, city_max, prod_max):
                        product_centers.append(statistics.median(
                            (w["top"] + w["bottom"]) / 2
                            for w in candidate_line
                            if city_max <= (w["x0"] + w["x1"]) / 2 <= prod_max
                        ))
                neighbor_distances = [
                    abs(other - row_center) for other in product_centers
                    if abs(other - row_center) > 0.5
                ]
                nearest_spacing = min(neighbor_distances) if neighbor_distances else 12.0
                row_center_tolerance = min(5.5, max(2.5, nearest_spacing * 0.32))
                band_words = [
                    w for w in body_words
                    if abs(((w["top"] + w["bottom"]) / 2) - row_center)
                    <= row_center_tolerance
                ]
                price = extract_common_price_from_band(
                    band_words, line, overall_min_x, common_min_x
                )

                product_rows.append({
                    "top": y_top,
                    "bottom": y_bot,
                    "product": prod,
                    "price": price,
                })

            blocks = []
            current_block = []
            for row in product_rows:
                if current_block and PRODUCT_RANK[row["product"]] <= PRODUCT_RANK[current_block[-1]["product"]]:
                    blocks.append(current_block)
                    current_block = [row]
                else:
                    current_block.append(row)
            if current_block:
                blocks.append(current_block)

            raw_block_provs = []
            for block in blocks:
                y_start = block[0]["top"] - 10
                y_end = block[-1]["bottom"] + 10
                p_words = [
                    w for w in body_words
                    if y_start <= w["top"] <= y_end and (w["x0"] + w["x1"]) / 2 < prov_max
                ]
                raw_block_provs.append(join_place(p_words, is_province=True))

            unique_page_provs = [p for p in dict.fromkeys(raw_block_provs) if p]

            for block, raw_prov in zip(blocks, raw_block_provs):
                y_start = block[0]["top"] - 10
                y_end = block[-1]["bottom"] + 10
                c_words = [
                    w for w in body_words
                    if y_start <= w["top"] <= y_end
                    and prov_max <= (w["x0"] + w["x1"]) / 2 < city_max
                ]
                city_name = resolve_city_name(c_words, MASTER_TEMPLATE)

                if len(unique_page_provs) == 1:
                    prov_name = unique_page_provs[0]
                    last_province = prov_name
                elif raw_prov:
                    prov_name = raw_prov
                    last_province = prov_name
                else:
                    prov_name = last_province

                for row in block:
                    records.append({
                        # Filled from the canonical city/province in apply_master_order.
                        "Region": "",
                        "Province": prov_name,
                        "City": city_name,
                        "Product": row["product"],
                        week_col: row["price"],
                    })

            print(f"  p{idx + 1} [{source}]: {len(blocks)} cities, {len(product_rows)} rows")

    fitz_doc.close()
    df = pd.DataFrame(records, columns=KEY_COLS + [week_col])
    df = df.drop_duplicates(subset=KEY_COLS, keep="first")
    return apply_master_order(df, week_col, MASTER_TEMPLATE)


MASTER_TEMPLATE = load_master_template()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
pdf_files = sorted(INPUT_FOLDER.glob("*.pdf"))

for pdf_path in pdf_files:
    week_col = extract_week_label(pdf_path)
    out_csv = OUTPUT_DIR / f"{safe_filename(week_col)}.csv"

    if SKIP_EXISTING and out_csv.exists():
        print(f"Skipped (already extracted): {out_csv.name}")
        continue

    print(f"Extracting {pdf_path.name} -> {out_csv.name}")
    week_df = parse_pdf(pdf_path, week_col)

    if week_df.empty:
        print(f"  No rows extracted from {pdf_path.name}")
        continue

    week_df.to_csv(out_csv, index=False)
    print(f"  Saved {len(week_df)} rows to {out_csv}")