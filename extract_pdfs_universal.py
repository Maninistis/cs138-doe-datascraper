"""DOE Visayas price extractor with native-text and OCR fallbacks.

Extraction order:
1. PyMuPDF native word boxes (preferred; existing path for text PDFs).
2. pdfplumber text extraction (fallback for unusual text PDFs).
3. OCR (RapidOCR if installed, otherwise Tesseract).
4. Ruled-grid OCR fallback for scanned DOE-VFO sheets.

The first two paths preserve the established extraction behavior. OCR is used
only when the PDF has no usable text layer or its text layer is incomplete.
"""
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
    ("RON 100", re.compile(r"\b(?:R[O0]N|[O0]N|OM|R)\s*100\b")),
    ("RON 97", re.compile(r"\b(?:R[O0]N|[O0]N|OM|R)\s*97\b")),
    ("RON 95", re.compile(r"\b(?:R[O0]N|[O0]N|OM|R)\s*9[456]\b")),
    ("RON 91", re.compile(r"\b(?:R[O0]N|[O0]N|OM|R)\s*91\b")),
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
    "MOUNTAINPROVINCE": "MOUNTAIN PROVINCE",
}

ocr_engine = None


def get_ocr_engine():
    """Prefer RapidOCR, but fall back to Tesseract when it is installed."""
    global ocr_engine
    if ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr_engine = ("rapidocr", RapidOCR())
        except ImportError:
            try:
                import pytesseract  # noqa: F401
                ocr_engine = ("tesseract", None)
            except ImportError as exc:
                raise RuntimeError(
                    "Scanned PDFs require RapidOCR or pytesseract plus the "
                    "Tesseract OCR application."
                ) from exc
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


def ocr_page_to_words(fitz_page, zoom=3.5):
    """Extract individual OCR word boxes in the PDF page coordinate system."""
    from PIL import Image

    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    engine_name, engine = get_ocr_engine()
    words = []

    if engine_name == "rapidocr":
        result, _ = engine(pix.tobytes("png"))
        for box, text, score in (result or []):
            text = text.strip()
            if not text:
                continue
            xs = [pt[0] / zoom for pt in box]
            ys = [pt[1] / zoom for pt in box]
            words.append({"text": text, "x0": min(xs), "x1": max(xs),
                          "top": min(ys), "bottom": max(ys),
                          "confidence": float(score) if score is not None else 0.0})
        return words

    import pytesseract
    from pytesseract import Output

    # Sparse-text modes handle ruled tables better than full-page paragraph mode.
    for psm in (6, 12, 11):
        data = pytesseract.image_to_data(
            image, config=f"--oem 3 --psm {psm}", output_type=Output.DICT
        )
        candidate = []
        for i, raw in enumerate(data["text"]):
            text = raw.strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][i])
            except (TypeError, ValueError):
                confidence = -1
            if confidence < 8:
                continue
            x0 = data["left"][i] / zoom
            y0 = data["top"][i] / zoom
            x1 = (data["left"][i] + data["width"][i]) / zoom
            y1 = (data["top"][i] + data["height"][i]) / zoom
            if x1 - x0 > fitz_page.rect.width * 0.30 or y1 - y0 > 30:
                continue
            candidate.append({"text": text, "x0": x0, "x1": x1,
                              "top": y0, "bottom": y1,
                              "confidence": confidence})
        # Keep complementary detections from each segmentation mode. PSM 6
        # often recovers merged city labels; PSM 12 is better for isolated
        # price tokens. Duplicate boxes are removed by approximate geometry.
        for item in candidate:
            duplicate = any(
                item["text"].upper() == old["text"].upper()
                and abs(item["x0"] - old["x0"]) < 2.5
                and abs(item["top"] - old["top"]) < 2.5
                for old in words
            )
            if not duplicate:
                words.append(item)
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
        header_band = [w for w in words if abs(w["top"] - hy) <= 45]
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

    if fallback_bounds:
        return fallback_bounds

    # OCR fallback for older scanned Visayas DOE tables. The table's vertical
    # rules are consistently positioned relative to page width in this layout.
    # This branch is used only when header OCR failed; native text layouts keep
    # using their detected header coordinates.
    if words:
        page_left = min(w["x0"] for w in words)
        page_right = max(w["x1"] for w in words)
        width = page_right - page_left
        first_product_top = min(
            (w["top"] for w in words
             if 0.12 * width < w["x0"] - page_left < 0.23 * width
             and re.search(r"RON|DIESEL|KEROSENE|100|97|91", w["text"].upper())),
            default=0.0,
        )
        if width > 300 and first_product_top > 0:
            return {
                "prov_max": page_left + width * 0.078,
                "city_max": page_left + width * 0.146,
                "prod_max": page_left + width * 0.204,
                "overall_min_x": page_left + width * 0.785,
                "common_min_x": page_left + width * 0.907,
                "header_bottom": first_product_top - 2.0,
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
    text_key = normalize_city_key(" ".join(w["text"] for w in words))
    if not text_key:
        return ""
    cities = sorted(
        {str(v).strip().upper() for v in master_template["City"].tolist() if str(v).strip()},
        key=lambda value: len(normalize_key(value)),
        reverse=True,
    )
    matches = [
        city for city in cities
        if normalize_city_key(city) and normalize_city_key(city) in text_key
    ]
    return matches[0] if matches else join_place(words, is_province=False)


def normalize_key(value):
    """Normalize a location/product label for matching against the master template."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_city_key(value):
    """Normalize common city variants: Baguio/Baguio City and City of X/X City."""
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    text = re.sub(r"^CITY\s+OF\s+", "", text)
    text = re.sub(r"\s+CITY$", "", text)
    return normalize_key(text)


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
        city_groups.setdefault(normalize_city_key(row["City"]), []).append((row["Region"], row["Province"], row["City"]))

    canonical_rows = []
    for _, row in df.iterrows():
        city_key = normalize_city_key(row.get("City", ""))
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
    matched_count = len(working)
    expected_count = len(template)
    print(f"  Canonical records matched: {matched_count}/{expected_count}")
    if matched_count < expected_count * 0.95:
        print(
            "  WARNING: Extraction coverage is below 95%. The output will still "
            "retain all master rows, but many values may be missing. Review the "
            "PDF's OCR/grid extraction before using this week in the dataset."
        )
    result = template.merge(working, on=KEY_COLS, how="left", sort=False)
    result[week_col] = result[week_col].fillna("-").replace("", "-")
    return result[KEY_COLS + [week_col]]


PROVINCE_TO_REGION = {
    "ABRA": "CAR", "APAYAO": "CAR", "BENGUET": "CAR",
    "IFUGAO": "CAR", "KALINGA": "CAR", "MOUNTAIN PROVINCE": "CAR",
    "ILOCOS NORTE": "Region I", "ILOCOS SUR": "Region I",
    "LA UNION": "Region I", "PANGASINAN": "Region I",
    "BATANES": "Region II", "CAGAYAN": "Region II",
    "ISABELA": "Region II", "NUEVA VIZCAYA": "Region II",
    "QUIRINO": "Region II",
    "AURORA": "Region III", "BATAAN": "Region III",
    "BULACAN": "Region III", "NUEVA ECIJA": "Region III",
    "PAMPANGA": "Region III", "TARLAC": "Region III",
    "ZAMBALES": "Region III",
}

def region_for_province(province):
    key = clean_text(province).upper()
    key = re.sub(r"\s+", " ", key).strip()
    return PROVINCE_TO_REGION.get(key, "")




def _ocr_crop_text(image, zoom, x1, x2, y1, y2, psm=7):
    """OCR one isolated table cell; coordinates are in PDF points."""
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter

    left = max(0, int((x1 + 0.8) * zoom))
    right = min(image.width, int((x2 - 0.8) * zoom))
    top = max(0, int((y1 + 0.8) * zoom))
    bottom = min(image.height, int((y2 - 0.8) * zoom))
    if right <= left or bottom <= top:
        return ""
    crop = image.crop((left, top, right, bottom)).convert("L")
    crop = crop.resize((crop.width * 2, crop.height * 2))
    crop = ImageEnhance.Contrast(crop).enhance(1.8)
    crop = crop.filter(ImageFilter.SHARPEN)
    return clean_text(pytesseract.image_to_string(
        crop, config=f"--oem 3 --psm {psm}"
    ))


def _grid_horizontal_lines(fitz_page, zoom=4.0):
    """Find ruled table-row lines and interpolate occasional broken rules."""
    import cv2
    import numpy as np
    from PIL import Image

    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    arr = np.asarray(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 12
    )
    kernel_width = max(20, int(binary.shape[1] * 0.25))
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    )
    # The data grid begins to the right of the merged province/city cells.
    x_start = int(binary.shape[1] * 0.18)
    x_end = int(binary.shape[1] * 0.97)
    counts = np.sum(horizontal[:, x_start:x_end] > 0, axis=1)
    ys = np.where(counts > (x_end - x_start) * 0.10)[0]
    groups = []
    for y in ys:
        if not groups or y > groups[-1][-1] + 1:
            groups.append([int(y)])
        else:
            groups[-1].append(int(y))
    lines = [float(np.mean(g)) / zoom for g in groups if len(g) >= 2]
    # Keep only the main table area, excluding page borders and footer rules.
    lines = [y for y in lines if fitz_page.rect.height * 0.18 < y < fitz_page.rect.height * 0.90]
    if len(lines) < 9:
        return image, []

    # Collapse duplicate detections of a thick rule.
    collapsed = []
    for y in lines:
        if not collapsed or y - collapsed[-1] > 3.0:
            collapsed.append(y)
        else:
            collapsed[-1] = (collapsed[-1] + y) / 2

    diffs = [b - a for a, b in zip(collapsed, collapsed[1:]) if 10 <= b - a <= 23]
    if not diffs:
        return image, []
    pitch = float(np.median(diffs))
    filled = [collapsed[0]]
    for y in collapsed[1:]:
        gap = y - filled[-1]
        steps = max(1, int(round(gap / pitch)))
        if steps > 1 and gap / steps >= pitch * 0.72 and gap / steps <= pitch * 1.30:
            for k in range(1, steps):
                filled.append(filled[-1] + gap / steps)
        filled.append(y)
    return image, filled


def _resolve_city_fuzzy(text, master_template):
    """Resolve noisy OCR city labels against the authoritative master list."""
    from difflib import SequenceMatcher

    key = normalize_key(text)
    if not key:
        return None
    candidates = []
    for _, row in master_template[["Province", "City"]].drop_duplicates().iterrows():
        city = str(row["City"]).strip().upper()
        city_key = normalize_key(city)
        if not city_key:
            continue
        if city_key in key or key in city_key:
            score = 0.94
        else:
            score = SequenceMatcher(None, key, city_key).ratio()
            # Compare without common suffixes to tolerate OCR loss of CITY.
            for suffix in ("CITY", "MUNICIPALITY"):
                if city_key.endswith(suffix):
                    score = max(score, SequenceMatcher(None, key, city_key[:-len(suffix)]).ratio())
        candidates.append((score, str(row["Province"]).strip().upper(), city))
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < 0.70:
        return None
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.035:
        return None
    return candidates[0][1], candidates[0][2]


def _numbers_from_cell(text):
    """Read decimal prices from one isolated cell, tolerating OCR dash noise."""
    normalized = text.replace(",", ".").replace("O", "0")
    # DOE values are normally two decimal places; don't accept row numbers or
    # stray one-digit OCR artifacts as prices.
    values = re.findall(r"(?<!\d)(\d{1,3}\.\d{1,2})(?!\d)", normalized)
    result = []
    for value in values:
        try:
            number = float(value)
            if 10 <= number <= 200:
                result.append(number)
        except ValueError:
            pass
    return result


def extract_scanned_grid_page(fitz_page, master_template, words=None, zoom=3.5):
    """Cell-based fallback for image-only DOE reports with visible table rules.

    It reads each seven-row city block from its product/common/overall cells.
    This is intentionally isolated from the native-text parser so newer PDFs
    retain their existing extraction path.
    """
    image, ylines = _grid_horizontal_lines(fitz_page, zoom)
    if len(ylines) < 8:
        return []

    # Coordinates for the older DOE-VFO ruled layout. They are relative to the
    # page width and are used only by this scanned-grid fallback.
    page_w = fitz_page.rect.width
    x_province = page_w * 0.078
    x_city = page_w * 0.146
    x_product = page_w * 0.204
    x_overall = page_w * 0.785
    x_common = page_w * 0.907
    x_right = page_w * 0.965

    # Start at the first actual product row, not at the header's horizontal
    # rules. OCR product labels/numbers live in the PRODUCT column.
    product_x1, product_x2 = page_w * 0.13, page_w * 0.23
    product_word_tops = [
        w["top"] for w in (words or [])
        if product_x1 <= (w["x0"] + w["x1"]) / 2 <= product_x2
        and w["top"] > fitz_page.rect.height * 0.20
        and re.search(r"RON|ROM|DIESEL|KEROSENE|100|97|96|95|94|91|PLUS", w["text"].upper())
    ]
    if product_word_tops:
        first_y = min(product_word_tops)
        start_index = max((i for i, y in enumerate(ylines) if y <= first_y), default=0)
        ylines = ylines[start_index:]
    intervals = [(a, b) for a, b in zip(ylines, ylines[1:]) if 10 <= b - a <= 23]
    # A city is represented by seven consecutive product rows. Drop a partial
    # trailing block rather than shifting the product sequence.
    usable = (len(intervals) // 7) * 7
    records = []
    for block_start in range(0, usable, 7):
        block = intervals[block_start:block_start + 7]
        top, bottom = block[0][0], block[-1][1]
        city_words = [w for w in (words or [])
                      if x_province <= (w["x0"] + w["x1"]) / 2 < x_city
                      and top <= (w["top"] + w["bottom"]) / 2 < bottom]
        city_text = " ".join(w["text"] for w in sorted(city_words, key=lambda z: z["x0"]))
        resolved = _resolve_city_fuzzy(city_text, master_template)
        if not resolved:
            city_text = _ocr_crop_text(image, zoom, x_province, x_city, top, bottom, psm=6)
            resolved = _resolve_city_fuzzy(city_text, master_template)
        if not resolved:
            city_text = _ocr_crop_text(image, zoom, x_province, x_city, top, bottom, psm=7)
            resolved = _resolve_city_fuzzy(city_text, master_template)
        if not resolved:
            continue
        province, city = resolved

        for i, (row_top, row_bottom) in enumerate(block):
            # Use merged full-page OCR detections first for speed. Only OCR an
            # isolated cell when the full-page pass did not detect anything in
            # that cell; row bands still come from the physical table grid.
            row_words = [w for w in (words or [])
                         if row_top <= (w["top"] + w["bottom"]) / 2 < row_bottom]
            common_text = " ".join(
                w["text"] for w in row_words
                if x_common <= (w["x0"] + w["x1"]) / 2 < x_right
            )
            common_values = _numbers_from_cell(common_text)
            if not common_values and not common_text.strip():
                common_values = _numbers_from_cell(_ocr_crop_text(
                    image, zoom, x_common, x_right, row_top, row_bottom, psm=7
                ))
            if common_values:
                price = common_values[-1]
            else:
                overall_text = " ".join(
                    w["text"] for w in row_words
                    if x_overall <= (w["x0"] + w["x1"]) / 2 < x_common
                )
                range_values = _numbers_from_cell(overall_text)
                if not range_values and not overall_text.strip():
                    range_values = _numbers_from_cell(_ocr_crop_text(
                        image, zoom, x_overall, x_common, row_top, row_bottom, psm=7
                    ))
                if not range_values:
                    price = "-"
                elif len(range_values) == 1:
                    price = range_values[0]
                else:
                    price = round((min(range_values) + max(range_values)) / 2, 2)

            records.append({
                "Region": "", "Province": province, "City": city,
                "Product": PRODUCT_ORDER[i], "__PRICE__": price,
            })
    return records

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
                    words = ocr_page_to_words(fitz_doc[idx], zoom=3.5)
                    source = "ocr"

            # Some PDFs have an incomplete text layer. Retry with OCR when
            # native extraction yields too few recognizable product rows.
            if source != "ocr":
                probe_lines = group_words_into_lines(words, y_tol=3.5)
                probe_count = sum(
                    1 for ln in probe_lines
                    if match_product_from_words(ln, 0, fitz_doc[idx].rect.width)
                )
                if probe_count < 5:
                    ocr_words = ocr_page_to_words(fitz_doc[idx], zoom=3.5)
                    if len(ocr_words) > len(words):
                        words = ocr_words
                        source = "ocr"

            if not words:
                print(f"  p{idx + 1} [{source}]: 0 words")
                continue

            if source == "ocr":
                grid_rows = extract_scanned_grid_page(fitz_doc[idx], MASTER_TEMPLATE, words=words, zoom=4.0)
                if grid_rows:
                    for item in grid_rows:
                        records.append({
                            "Region": "", "Province": item["Province"],
                            "City": item["City"], "Product": item["Product"],
                            week_col: item["__PRICE__"],
                        })
                    print(f"  p{idx + 1} [ocr-grid]: {len(grid_rows) // 7} city blocks, {len(grid_rows)} rows")
                    continue

            # Native PDF text coordinates are already in the document's page
            # coordinate system. Applying OCR-style deskew to them can introduce
            # a false vertical offset across the wide table, separating prices
            # from their product labels. Deskew only raster/OCR-derived words.
            # Keep OCR coordinates as measured. Deskewing from imperfect OCR
            # header anchors can move cell text across the actual row boundary.
            y_tol = 4.0 if source == "ocr" else 3.5
            lines = group_words_into_lines(words, y_tol=y_tol)

            # Region is assigned from the canonical province after city matching;
            # page headers such as "REGIONS 6-8 & NIR" are not a valid region.

            bounds = find_column_bounds(words, fallback_bounds=None if source == "ocr" else last_bounds)
            if not bounds and last_bounds:
                bounds = last_bounds
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

            # First detect every product label on the page. Do not extract prices
            # yet: the price text boxes can sit a little above/below the label's
            # baseline, so a fixed +/- tolerance can accidentally take a value
            # from the adjacent product row.
            detected_rows = []
            for line in lines:
                y_top = min(w["top"] for w in line)
                y_bottom = max(w["bottom"] for w in line)
                if y_top <= header_bottom + 1:
                    continue

                prod = match_product_from_words(line, city_max, prod_max)
                if not prod:
                    continue

                # Anchor only on the product-name tokens. The first price
                # column begins close to the end of the PRODUCT column, so
                # including every word in that column can pull the median
                # toward the price text and shift the row anchor downward.
                label_token = re.compile(
                    r"^(?:R[O0]N|100|97|95|96|91|DIESEL|PLUS|KEROSENE)$",
                    re.IGNORECASE,
                )
                label_words = [
                    w for w in line
                    if label_token.fullmatch(w["text"].strip())
                    and city_max <= (w["x0"] + w["x1"]) / 2 <= prod_max
                ]
                if not label_words:
                    continue

                row_center = statistics.median(
                    (w["top"] + w["bottom"]) / 2 for w in label_words
                )
                detected_rows.append({
                    "top": y_top,
                    "bottom": y_bottom,
                    "center": row_center,
                    "product": prod,
                    "line": line,
                })

            # Use the midpoint between adjacent product-label centers as the
            # boundary between rows. Each price token can belong to exactly one
            # row, even if its text box is vertically offset from the label.
            detected_rows.sort(key=lambda row: row["center"])
            product_centers = [row["center"] for row in detected_rows]
            product_rows = []

            for i, row in enumerate(detected_rows):
                if i > 0:
                    upper_boundary = (product_centers[i - 1] + row["center"]) / 2
                else:
                    # Extrapolate the first boundary using the next row spacing.
                    spacing = (product_centers[i + 1] - row["center"]) if len(product_centers) > 1 else 12.0
                    upper_boundary = row["center"] - spacing / 2

                if i + 1 < len(detected_rows):
                    lower_boundary = (row["center"] + product_centers[i + 1]) / 2
                else:
                    # Extrapolate the last boundary using the previous spacing.
                    spacing = (row["center"] - product_centers[i - 1]) if i > 0 else 12.0
                    lower_boundary = row["center"] + spacing / 2

                row_words = [
                    w for w in body_words
                    if upper_boundary <= ((w["top"] + w["bottom"]) / 2) < lower_boundary
                ]
                price = extract_common_price_from_band(
                    row_words, row["line"], overall_min_x, common_min_x
                )

                product_rows.append({
                    "top": row["top"],
                    "bottom": row["bottom"],
                    "product": row["product"],
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