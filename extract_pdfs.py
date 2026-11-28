import difflib
import os
import re
import shutil
import subprocess
from pathlib import Path
import fitz
import pandas as pd
import pdfplumber
from rapidocr_onnxruntime import RapidOCR

INPUT_FOLDER = Path("pdfs")
MASTER_ORDER = Path("master_order.csv")
OUTPUT_DIRS = {
    "auto": Path("extracted"),
    "text": Path("extracted1"),
    "ocr": Path("extracted2"),
    "rapidocr": Path("extracted_ocr"),
    "tesseract": Path("extracted_ocr_t"),
}
SKIP_EXISTING = True
KEY_COLS = ["Region", "Province", "City", "Product"]

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
REGION_CODES = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
    "IX", "X", "XI", "XII", "XIII", "IVA", "IVB",
}

PRODUCT_PATTERNS = [
    ("RON 100", re.compile(r"\bR[O0QD][NNMH]?\s*[1IL][O0ODD][O0ODD]\b")),
    ("RON 97", re.compile(r"\bR[O0QD][NNMH]?\s*9[7T]\b")),
    ("RON 95", re.compile(r"\bR[O0QD][NNMH]?\s*9[56S]\b")),
    ("RON 91", re.compile(r"\bR[O0QD][NNMH]?\s*9[1IL]\b")),
    ("DIESEL PLUS", re.compile(r"\bDIESE[L1I]\s*[PBF]L[UO]S\b")),
    ("DIESEL", re.compile(r"\bDIESE[L1I]\b")),
    ("KEROSENE", re.compile(r"\bKER[O0]SE[NM][E3]\b")),
]

CYRILLIC_MAP = str.maketrans("АВЕКМНОРСТХавекмнорстх", "ABEKMHOPCTXabekmhopctx")

PROVINCE_FIXES = {
    "ILOCOSNORTE": "ILOCOS NORTE",
    "ILOCOSSUR": "ILOCOS SUR",
    "LAUNION": "LA UNION",
    "NUEVAVIZCAYA": "NUEVA VIZCAYA",
    "NUEVAECIJA": "NUEVA ECIJA",
    "MTPROVINCE": "MOUNTAIN PROVINCE",
}

ocr_engine = None
OCR_ENGINE = os.getenv("OCR_ENGINE", "rapidocr").strip().lower()
TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)


def get_ocr_engine():
    global ocr_engine
    if ocr_engine is None:
        ocr_engine = RapidOCR()
    return ocr_engine


def get_tesseract_command():
    if os.path.isfile(TESSERACT_CMD):
        return TESSERACT_CMD
    return shutil.which("tesseract") or ""


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


def place_match_key(text):
    return re.sub(r"[^A-Z0-9]", "", clean_text(text).upper())


def place_match_keys(text):
    key = place_match_key(text)
    if not key:
        return set()

    keys = {key, re.sub(r"([A-Z])\1+", r"\1", key)}
    # OCR commonly swaps letters and digits in place names.
    for old, new in (
        ("0", "O"), ("O", "0"),
        ("1", "I"), ("I", "1"),
        ("5", "S"), ("S", "5"),
        ("8", "B"), ("B", "8"),
    ):
        keys.update(
            candidate_key.replace(old, new)
            for candidate_key in tuple(keys)
            if old in candidate_key
        )
    for candidate_key in tuple(keys):
        for prefix in (
            "CITYMUNICIPALITY",
            "MUNICIPALITYOF",
            "MUNICIPALITY",
            "SCIENCECITYOF",
            "CITYOF",
            "SCIENCECITY",
            "CITY",
        ):
            if candidate_key.startswith(prefix):
                keys.add(candidate_key[len(prefix):])
        for suffix in ("MUNICIPALITY", "CITY"):
            if candidate_key.endswith(suffix):
                keys.add(candidate_key[:-len(suffix)])
        keys.add(candidate_key.replace("MUNICIPALITY", "").replace("CITY", ""))
    return {match_key for match_key in keys if match_key}


def place_match_tokens(text):
    ignored = {"CITY", "MUNICIPAL", "MUNICIPALITY", "OF", "SCIENCE"}
    return {
        token
        for token in re.findall(r"[A-Z0-9]+", clean_text(text).upper())
        if token not in ignored
    }


def normalized_region_code(text):
    key = place_match_key(text)
    match = re.search(r"REGION(IV[AB]|[IVXL1]+)", key)
    if not match:
        return ""

    code = match.group(1).replace("L", "I").replace("1", "I")
    return code if code in REGION_CODES else ""


def region_match_key(text):
    key = place_match_key(text)
    if key == "CAR" or "CORDILLERA" in key:
        return "CAR"

    code = normalized_region_code(text)
    return f"REGION {code}" if code else key


def build_master_places(master_order_path):
    if not master_order_path.exists():
        return {}

    master_df = pd.read_csv(master_order_path, dtype=str)
    required = {"Region", "Province", "City"}
    missing = required.difference(master_df.columns)
    if missing:
        raise ValueError(
            f"{master_order_path} is missing required columns: {', '.join(sorted(missing))}"
        )

    master_df[["Region", "Province", "City"]] = master_df[
        ["Region", "Province", "City"]
    ].fillna("")
    places_by_region = {}
    for region, province, city in master_df[
        ["Region", "Province", "City"]
    ].itertuples(index=False, name=None):
        region_key = region_match_key(region)
        city = clean_text(city or "")
        if region_key and province and city:
            places_by_region.setdefault(region_key, set()).add(
                (clean_text(province), city)
            )
    return places_by_region


def edit_distance_at_most_two(left, right):
    if abs(len(left) - len(right)) > 2:
        return 3

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            ))
        if min(current) > 2:
            return 3
        previous = current
    return min(previous[-1], 3)


def match_master_city(city, candidates):
    city_keys = place_match_keys(city)
    if not city_keys or not candidates:
        return None

    exact_matches = {
        place
        for place in candidates
        if place_match_keys(place[1]).intersection(city_keys)
        or place_match_tokens(place[1]) == place_match_tokens(city)
    }
    if len(exact_matches) == 1:
        return next(iter(exact_matches))
    if exact_matches:
        return None

    pattern_matches = {
        place
        for place in candidates
        if any(
            len(candidate_key) >= 5 and candidate_key in city_key
            for candidate_key in place_match_keys(place[1])
            for city_key in city_keys
        )
    }
    if len(pattern_matches) == 1:
        return next(iter(pattern_matches))
    if pattern_matches:
        return None

    scored_matches = []
    for candidate in candidates:
        candidate_keys = place_match_keys(candidate[1])
        best_score = None
        for city_key in city_keys:
            for candidate_key in candidate_keys:
                distance = edit_distance_at_most_two(city_key, candidate_key)
                similarity = difflib.SequenceMatcher(
                    None, city_key, candidate_key
                ).ratio()
                if distance <= 2 and similarity >= 0.72:
                    score = (distance, -similarity)
                    if best_score is None or score < best_score:
                        best_score = score
        if best_score is not None:
            scored_matches.append((best_score, candidate))

    if not scored_matches:
        return None

    best_score = min(score for score, _ in scored_matches)
    best_matches = {
        candidate for score, candidate in scored_matches if score == best_score
    }
    return next(iter(best_matches)) if len(best_matches) == 1 else None


def match_master_place(city, region, province, master_places):
    candidates = master_places.get(region_match_key(region), set())
    if not candidates:
        return province, ""

    province_key = place_match_key(province)
    province_candidates = {
        place for place in candidates if place_match_key(place[0]) == province_key
    }
    matched_place = match_master_city(city, province_candidates)
    if matched_place is None:
        matched_place = match_master_city(city, candidates)
    if matched_place is not None:
        return matched_place

    if province_candidates:
        return next(iter(province_candidates))[0], ""
    return province, ""


def match_master_province(province, region, fallback=""):
    """Canonicalize OCR province text and never invent a province."""
    candidates = {
        candidate_province
        for candidate_province, _ in MASTER_PLACES.get(region_match_key(region), set())
    }
    if not candidates:
        return fallback or province

    province_keys = place_match_keys(province)
    exact = {
        candidate
        for candidate in candidates
        if place_match_keys(candidate).intersection(province_keys)
        or place_match_tokens(candidate) == place_match_tokens(province)
    }
    if len(exact) == 1:
        return next(iter(exact))

    scored = []
    for candidate in candidates:
        for candidate_key in place_match_keys(candidate):
            for province_key in province_keys:
                distance = edit_distance_at_most_two(province_key, candidate_key)
                similarity = difflib.SequenceMatcher(
                    None, province_key, candidate_key
                ).ratio()
                if distance <= 2 and similarity >= 0.72:
                    scored.append((distance, -similarity, candidate))

    if scored:
        best_score = min(scored, key=lambda item: (item[0], item[1]))
        best_matches = {
            item[2] for item in scored
            if (item[0], item[1]) == (best_score[0], best_score[1])
        }
        if len(best_matches) == 1:
            return next(iter(best_matches))

    return fallback or province


def infer_page_province(words, region, fallback=""):
    """Find at most one province clearly visible on the page."""
    candidates = {
        candidate_province
        for candidate_province, _ in MASTER_PLACES.get(region_match_key(region), set())
    }
    if not candidates:
        return fallback

    page_key = place_match_key(" ".join(word["text"] for word in words))
    found = set()
    for candidate in candidates:
        candidate_keys = place_match_keys(candidate)
        if any(len(key) >= 5 and key in page_key for key in candidate_keys):
            found.add(candidate)

    if len(found) == 1:
        return next(iter(found))
    return fallback


def fill_missing_city_gaps(block_places):
    for block_index, place in enumerate(block_places):
        if place["city"] or place["raw_city"]:
            continue

        previous = None
        for earlier in reversed(block_places[:block_index]):
            if earlier["city"]:
                previous = earlier
                break
            if earlier["raw_city"]:
                break

        following = None
        for later in block_places[block_index + 1:]:
            if later["city"]:
                following = later
                break
            if later["raw_city"]:
                break

        if (
            previous
            and following
            and previous["city"] == following["city"]
            and place_match_key(previous["province"])
            == place_match_key(following["province"])
        ):
            place["province"] = previous["province"]
            place["city"] = previous["city"]
    return block_places


def extraction_matches_master_order(csv_path, master_places):
    if not master_places:
        return True

    extracted = pd.read_csv(csv_path, dtype=str)
    required = {"Region", "Province", "City"}
    if not required.issubset(extracted.columns):
        return False

    extracted[["Region", "Province", "City"]] = extracted[
        ["Region", "Province", "City"]
    ].fillna("")
    if extracted.duplicated(["Region", "Province", "City", "Product"]).any():
        return False
    for _, group in extracted.groupby(
        ["Region", "Province", "City"], dropna=False
    ):
        if (
            len(group) != len(PRODUCT_ORDER)
            or set(group["Product"]) != set(PRODUCT_ORDER)
        ):
            return False

    for region, province, city in extracted[
        ["Region", "Province", "City"]
    ].drop_duplicates().itertuples(index=False, name=None):
        if not city.strip():
            return False
        canonical_province, canonical_city = match_master_place(
            city, region, province, master_places
        )
        if canonical_city != city or canonical_province != province:
            return False
    return True


def has_table_data(words):
    upper = " ".join(w["text"] for w in words).upper()
    return any(k in upper for k in ("RON", "DIESEL", "KEROSENE"))


def has_broken_encoding(words):
    if not words:
        return False
    full_text = " ".join(w["text"] for w in words)
    lower = full_text.lower()
    if "(cid:" in lower or "\ufffd" in lower:
        return True
    upper = full_text.upper()
    if len(words) >= 15 and not any(
        k in upper for k in ("PROVINCE", "PRODUCT", "RON", "DIESEL", "KEROSENE")
    ):
        return True
    return False


def normalize_region(text):
    upper = clean_text(text).upper()
    region_code = normalized_region_code(upper)
    if region_code and f"REGION {region_code}" not in upper:
        upper = f"{upper} REGION {region_code}"

    if "NATIONAL CAPITAL" in upper or "(NCR)" in upper:
        return "National Capital Region (NCR)"
    if "CORDILLERA" in upper or "(CAR)" in upper or re.search(r"\bCAR\b", upper):
        return "Cordillera Administrative Region (CAR)"
    if "ILOCOS" in upper or "LLOCOS" in upper or "LOCOS" in upper:
        return "Ilocos Region (REGION I)"
    if "CAGAYAN" in upper:
        return "Cagayan Valley (REGION II)"
    if "CENTRAL LUZON" in upper:
        return "Central Luzon (REGION III)"
    if "CALABARZON" in upper:
        return "CALABARZON (REGION IV-A)"
    if "MIMAROPA" in upper:
        return "MIMAROPA (REGION IV-B)"
    if "BICOL" in upper:
        return "Bicol Region (REGION V)"
    if "WESTERN VISAYAS" in upper:
        return "Western Visayas (REGION VI)"
    if "NEGROS ISLAND" in upper or "(NIR)" in upper:
        return "Negros Island Region (NIR)"
    if "CENTRAL VISAYAS" in upper:
        return "Central Visayas (REGION VII)"
    if "EASTERN VISAYAS" in upper:
        return "Eastern Visayas (REGION VIII)"
    if "ZAMBOANGA" in upper:
        return "Zamboanga Peninsula (REGION IX)"
    if "NORTHERN MINDANAO" in upper:
        return "Northern Mindanao (REGION X)"
    if "DAVAO" in upper:
        return "Davao Region (REGION XI)"
    if "SOCCSKSARGEN" in upper:
        return "SOCCSKSARGEN (REGION XII)"
    if "CARAGA" in upper:
        return "Caraga (REGION XIII)"
    if "BANGSAMORO" in upper or "BARMM" in upper or "ARMM" in upper:
        return "Bangsamoro Autonomous Region in Muslim Mindanao (BARMM)"

    if "IVA" in upper and not (
        "CALABARZON" in upper or re.search(r"\bIV[\s-]?A\b", upper)
    ):
        return ""
    if "IV-A" in upper or "IVA" in upper:
        return "CALABARZON (REGION IV-A)"
    if "IV-B" in upper or "IVB" in upper:
        return "MIMAROPA (REGION IV-B)"
    if "REGION XIII)" in upper or "REGION 13" in upper:
        return "Caraga (REGION XIII)"
    if "REGION XII)" in upper or "REGION 12" in upper:
        return "SOCCSKSARGEN (REGION XII)"
    if "REGION XI)" in upper or "REGION 11" in upper:
        return "Davao Region (REGION XI)"
    if "REGION X)" in upper or "REGION 10" in upper:
        return "Northern Mindanao (REGION X)"
    if "REGION IX)" in upper or "REGION 9" in upper:
        return "Zamboanga Peninsula (REGION IX)"
    if "REGION VIII)" in upper or "REGION 8" in upper:
        return "Eastern Visayas (REGION VIII)"
    if "REGION VII)" in upper or "REGION 7" in upper:
        return "Central Visayas (REGION VII)"
    if "REGION VI)" in upper or "REGION 6" in upper:
        return "Western Visayas (REGION VI)"
    if "REGION V)" in upper or "REGION 5" in upper:
        return "Bicol Region (REGION V)"
    if "REGION III" in upper:
        return "Central Luzon (REGION III)"
    if "REGION II" in upper:
        return "Cagayan Valley (REGION II)"
    if re.search(r"\bREGION I\b", upper):
        return "Ilocos Region (REGION I)"

    return ""


def extract_region_headers(lines):
    headers = []
    table_header_ys = [
        sum((word["top"] + word["bottom"]) / 2 for word in line) / len(line)
        for line in lines
        if any(place_match_key(word["text"]) == "PROVINCE" for word in line)
        and any(place_match_key(word["text"]) in {"CITY", "CITIES"} for word in line)
    ]
    for line in lines:
        text = " ".join(w["text"] for w in line)
        upper = clean_text(text).upper()
        has_region_code = bool(normalized_region_code(upper))
        is_car_header = (
            re.search(r"\bCAR\b", upper)
            and any(
                abs(
                    sum((word["top"] + word["bottom"]) / 2 for word in line)
                    / len(line)
                    - header_y
                ) <= 15
                for header_y in table_header_ys
            )
        )
        if not has_region_code and not is_car_header:
            continue

        region = normalize_region(text)
        if region:
            header_y = sum(
                (word["top"] + word["bottom"]) / 2 for word in line
            ) / len(line)
            headers.append((header_y, region))
    return headers


def group_words_into_lines(words, y_tol=3.0):
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


def ocr_normalize_token(text):
    text = clean_text(text).upper()
    text = re.sub(r"[^A-Z0-9 %./+-]+", " ", text)
    text = re.sub(r"\b([A-Z])\|([A-Z])\b", r"\1I\2", text)
    text = re.sub(r"\bRON\b", "RON", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ocr_page_to_words(fitz_page, zoom=3.0):
    pix = fitz_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    png_bytes = pix.tobytes("png")

    tesseract = get_tesseract_command() if OCR_ENGINE == "tesseract" else ""
    use_tesseract = OCR_ENGINE == "tesseract"
    if OCR_ENGINE == "tesseract" and not tesseract:
        raise RuntimeError(
            "Tesseract mode was selected, but tesseract.exe was not found. "
            "Set TESSERACT_CMD to its full path or add it to PATH."
        )
    if use_tesseract and tesseract:
        try:
            result = subprocess.run(
                [
                    tesseract,
                    "stdin",
                    "stdout",
                    "--psm",
                    "6",
                    "-l",
                    "eng",
                    "tsv",
                ],
                input=png_bytes,
                capture_output=True,
                check=False,
                timeout=60,
            )
            if result.returncode != 0 and OCR_ENGINE == "tesseract":
                error = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Tesseract OCR failed: {error}")
            if result.returncode == 0:
                words = []
                for line in result.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
                    columns = line.split("\t")
                    if len(columns) < 12:
                        continue
                    (
                        level,
                        _page,
                        _block,
                        _paragraph,
                        _line,
                        _word,
                        left,
                        top,
                        width,
                        height,
                        confidence,
                        text,
                    ) = columns[:12]
                    if level != "5" or not text.strip():
                        continue
                    try:
                        confidence_value = float(confidence)
                    except ValueError:
                        continue
                    if confidence_value < 30:
                        continue
                    cleaned = ocr_normalize_token(text)
                    if not cleaned:
                        continue
                    words.append({
                        "text": cleaned,
                        "x0": int(left) / zoom,
                        "x1": (int(left) + int(width)) / zoom,
                        "top": int(top) / zoom,
                        "bottom": (int(top) + int(height)) / zoom,
                    })
                if words:
                    return words
                if OCR_ENGINE == "tesseract":
                    return words
        except (OSError, subprocess.SubprocessError) as exc:
            if OCR_ENGINE == "tesseract":
                raise RuntimeError(f"Could not run Tesseract OCR: {exc}") from exc

    result, _ = get_ocr_engine()(pix.tobytes("png"))
    words = []
    if not result:
        return words

    for box, text, confidence in result:
        if confidence is not None and confidence < 0.30:
            continue
        cleaned = ocr_normalize_token(text)
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
    overall_min_x = None
    common_min_x = None

    if prov_word:
        hy = prov_word["top"]
        header_band = [w for w in words if abs(w["top"] - hy) <= 25]
        city_words = [
            w for w in header_band
            if any(
                marker in w["text"].upper()
                for marker in ("CITY", "CITIES", "MUNICIPAL")
            )
        ]
        prod_words = [w for w in header_band if "PRODUCT" in w["text"].upper()]
        overall_words = [
            w for w in header_band
            if ("OVERALL" in w["text"].upper() or "RANGE" in w["text"].upper())
            and w["x0"] > 450
        ]
        common_words = [
            w for w in header_band
            if any(k in w["text"].upper() for k in ("COMMON", "COMMUN", "CUMM", "PRICE"))
            and w["x0"] > 500
        ]

        header_bottom = max(
            w["bottom"]
            for w in [prov_word] + city_words + prod_words + overall_words + common_words
        )
        has_products_above_header = any(
            word["top"] < header_bottom
            and re.match(r"^(R[O0]N|DIESEL|KEROSENE)\b", word["text"].upper())
            for word in words
        )
        if has_products_above_header:
            header_bottom = (
                fallback_bounds.get("header_bottom", 0)
                if fallback_bounds
                else 0
            )
        if city_words:
            prov_max = (prov_word["x1"] + min(w["x0"] for w in city_words)) / 2
        if overall_words and common_words:
            common_min_x = (
                max(w["x1"] for w in overall_words) + min(w["x0"] for w in common_words)
            ) / 2
            overall_min_x = min(w["x0"] for w in overall_words) - 10
        elif common_words:
            common_min_x = min(w["x0"] for w in common_words) - 4
        elif overall_words:
            overall_min_x = min(w["x0"] for w in overall_words) - 10

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
            overall_min_x = (
                fallback_bounds["overall_min_x"]
                if fallback_bounds and "overall_min_x" in fallback_bounds
                else common_min_x - 95
            )
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
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = text.replace("0N", "ON").replace("R0N", "RON")
    text = text.replace("DIESELPLUS", "DIESEL PLUS")
    text = re.sub(r"\bR\s*[O0QD]\s*[NMH]\b", "RON", text)
    text = re.sub(r"\bD1ES(E)?L\b", "DIESEL", text)
    for name, pattern in PRODUCT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def extract_prices_from_token(token):
    t = token.strip()
    upper = t.upper()
    if not t or "#" in t or "N/A" in upper or "NIA" in upper or "NONE" in upper:
        return []
    upper = upper.replace(",", ".").replace(";", ".")
    upper = re.sub(r"(?<=\d)[,\s]+(?=\d{2}\b)", ".", upper)
    upper = upper.replace("O", "0").replace("o", "0")
    upper = upper.replace("S", "5").replace("s", "5")
    matches = re.findall(r"\d+\.\d{2}", upper)
    if not matches:
        matches = re.findall(r"\d+\.\d+", upper)
    if not matches:
        for compact in re.findall(r"\b\d{4,5}\b", upper):
            if len(compact) >= 4:
                value = f"{compact[:-2]}.{compact[-2:]}"
                if float(value) > 0:
                    matches.append(value)
    return [float(m) for m in matches if float(m) > 0]


def extract_common_price_from_band(band_words, overall_min_x, common_min_x):
    sorted_band = sorted(band_words, key=lambda x: x["x0"])

    common_words = [
        w for w in sorted_band
        if (w["x0"] + w["x1"]) / 2 >= common_min_x
    ]
    common_prices = []
    for w in common_words:
        common_prices.extend(extract_prices_from_token(w["text"]))

    if common_prices:
        return common_prices[-1]

    range_words = [
        w for w in sorted_band
        if overall_min_x <= (w["x0"] + w["x1"]) / 2 < common_min_x
    ]
    range_prices = []
    for w in range_words:
        range_prices.extend(extract_prices_from_token(w["text"]))

    if len(range_prices) >= 2:
        return round((range_prices[0] + range_prices[-1]) / 2, 2)
    if len(range_prices) == 1:
        return range_prices[0]

    return "-"


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
        compact = re.sub(r"[^A-Z0-9]", "", s)
        if compact in PROVINCE_FIXES:
            return PROVINCE_FIXES[compact]
    return s


def master_place_label_score(words):
    text_key = place_match_key(" ".join(word["text"] for word in words))
    labels = {
        place_match_key(label)
        for places in MASTER_PLACES.values()
        for province, city in places
        for label in (province, city)
    }
    return sum(
        1 for label in labels
        if len(label) >= 5 and label in text_key
    )


def master_city_label_score(words):
    bounds = find_column_bounds(words)
    if not bounds:
        return 0

    city_words = [
        word for word in words
        if word["bottom"] > bounds["header_bottom"]
        and bounds["prov_max"]
        <= (word["x0"] + word["x1"]) / 2
        < bounds["city_max"]
    ]
    text_key = place_match_key(" ".join(word["text"] for word in city_words))
    return sum(
        1
        for places in MASTER_PLACES.values()
        for _, city in places
        if any(
            len(label) >= 5 and label in text_key
            for label in place_match_keys(city)
        )
    )


def has_unmatched_city_blocks(words):
    product_rows = page_product_row_count(words)
    expected_blocks = (
        product_rows + len(PRODUCT_ORDER) - 1
    ) // len(PRODUCT_ORDER)
    return expected_blocks > 0 and (
        master_city_label_score(words) != expected_blocks
        or product_rows % len(PRODUCT_ORDER) != 0
    )


def city_block_score_error(words):
    product_rows = page_product_row_count(words)
    expected_blocks = (
        product_rows + len(PRODUCT_ORDER) - 1
    ) // len(PRODUCT_ORDER)
    if not expected_blocks:
        return 0
    return abs(master_city_label_score(words) - expected_blocks)


def infer_region_from_city_labels(words, current_region):
    text_key = place_match_key(" ".join(word["text"] for word in words))
    scores = {
        region: sum(
            1 for city in {place_match_key(city) for _, city in places}
            if len(city) >= 5 and city in text_key
        )
        for region, places in MASTER_PLACES.items()
    }
    best_score = max(scores.values(), default=0)
    if best_score < 2 or best_score <= scores.get(
        region_match_key(current_region), 0
    ):
        return ""

    best_regions = [
        region for region, score in scores.items()
        if score == best_score
    ]
    if len(best_regions) != 1:
        return ""
    return MASTER_REGION_NAMES.get(best_regions[0], "")


def page_product_row_count(words):
    bounds = find_column_bounds(words)
    if not bounds:
        return 0

    product_rows = 0
    for line in group_words_into_lines(words, y_tol=3.0):
        product_words = [
            word for word in line
            if bounds["city_max"]
            <= (word["x0"] + word["x1"]) / 2
            <= bounds["prod_max"]
        ]
        if not product_words:
            continue
        if min(word["bottom"] for word in product_words) <= bounds["header_bottom"]:
            continue
        if match_product_from_words(
            product_words, bounds["city_max"], bounds["prod_max"]
        ):
            product_rows += 1
    return product_rows


def prefer_fitz_text_words(words, fitz_words):
    return (
        len(fitz_words) >= 15
        and not has_broken_encoding(fitz_words)
        and master_place_label_score(fitz_words) > master_place_label_score(words)
        and page_product_row_count(fitz_words) >= page_product_row_count(words)
    )


def extract_page_words(page, fitz_page, mode):
    if mode == "ocr":
        return ocr_page_to_words(fitz_page), "ocr"

    words = page.extract_words(
        x_tolerance=1.5,
        y_tolerance=2,
        keep_blank_chars=False,
        use_text_flow=False,
    )

    if mode == "text":
        if len(words) < 15:
            fitz_words = extract_fitz_words(fitz_page)
            if len(fitz_words) >= 15:
                return fitz_words, "fitz"
        else:
            fitz_words = extract_fitz_words(fitz_page)
            if prefer_fitz_text_words(words, fitz_words):
                return fitz_words, "fitz (master-order labels)"
        return words, "pdfplumber"

    if (
        has_broken_encoding(words)
        or not has_table_data(words)
        or has_fragmented_product_labels(words)
    ):
        return ocr_page_to_words(fitz_page), "ocr (fallback)"
    if len(words) < 15:
        fitz_words = extract_fitz_words(fitz_page)
        if len(fitz_words) >= 15 and not has_broken_encoding(fitz_words):
            words = fitz_words
            source = "fitz"
        else:
            return ocr_page_to_words(fitz_page), "ocr"
    else:
        fitz_words = extract_fitz_words(fitz_page)
        if prefer_fitz_text_words(words, fitz_words):
            words = fitz_words
            source = "fitz (master-order labels)"
        else:
            source = "pdfplumber"

    if has_unmatched_city_blocks(words):
        ocr_words = ocr_page_to_words(fitz_page)
        text_city_score = master_city_label_score(words)
        ocr_city_score = master_city_label_score(ocr_words)
        text_product_rows = page_product_row_count(words)
        ocr_product_rows = page_product_row_count(ocr_words)
        text_block_error = city_block_score_error(words)
        ocr_block_error = city_block_score_error(ocr_words)
        if (
            ocr_product_rows >= text_product_rows
            and (
                ocr_block_error < text_block_error
                or (
                    ocr_block_error == text_block_error
                    and (
                        ocr_city_score > text_city_score
                        or ocr_product_rows > text_product_rows
                    )
                )
            )
        ):
            return ocr_words, "ocr (city-label fallback)"
    return words, source


def has_fragmented_product_labels(words):
    bounds = find_column_bounds(words)
    if not bounds:
        return False

    product_words = [
        word for word in words
        if word["top"] > bounds["header_bottom"]
        and bounds["city_max"]
        <= (word["x0"] + word["x1"]) / 2
        <= bounds["prod_max"]
    ]
    return any(
        sum(word["text"].isalpha() and len(word["text"]) == 1 for word in line) >= 4
        for line in group_words_into_lines(product_words, y_tol=3.0)
    )


def is_province_cities_header_line(line, bounds):
    has_province = any(
        place_match_key(word["text"]) == "PROVINCE"
        and (word["x0"] + word["x1"]) / 2 < bounds["prov_max"]
        for word in line
    )
    has_cities = any(
        place_match_key(word["text"]) in {"CITY", "CITIES"}
        and bounds["prov_max"]
        <= (word["x0"] + word["x1"]) / 2
        < bounds["city_max"]
        for word in line
    )
    return has_province and has_cities


def parse_pdf(pdf_path, week_col, mode="auto"):
    place_groups = []

    with fitz.open(pdf_path) as fitz_doc, pdfplumber.open(pdf_path) as pdf:
        last_region = ""
        last_province = ""
        last_bounds = None

        for idx, page in enumerate(pdf.pages):
            words, source = extract_page_words(page, fitz_doc[idx], mode)

            if not words:
                print(f"  p{idx + 1} [{source}]: 0 words")
                continue

            words = deskew_words(words)
            lines = group_words_into_lines(words, y_tol=3.0)

            region_headers = extract_region_headers(lines)
            previous_region = last_region
            if region_headers:
                region = previous_region or region_headers[0][1]
            else:
                region = infer_region_from_city_labels(words, previous_region) or previous_region
                if region:
                    last_region = region

            page_province = infer_page_province(words, region, last_province)

            bounds = find_column_bounds(words, fallback_bounds=last_bounds)
            if not bounds:
                print(f"  p{idx + 1} [{source}]: no bounds")
                continue
            last_bounds = bounds

            header_word_ids = {
                id(word)
                for line in lines
                if is_province_cities_header_line(line, bounds)
                for word in line
            }
            prov_max = bounds["prov_max"]
            city_max = bounds["city_max"]
            prod_max = bounds["prod_max"]
            overall_min_x = bounds["overall_min_x"]
            common_min_x = bounds["common_min_x"]
            header_bottom = bounds["header_bottom"]

            body_words = [
                word for word in words
                if word["bottom"] > header_bottom and id(word) not in header_word_ids
            ]
            product_rows = []

            for line in lines:
                prod_words = [
                    w for w in line
                    if city_max <= (w["x0"] + w["x1"]) / 2 <= prod_max
                ]
                if not prod_words:
                    continue

                y_top = min(w["top"] for w in prod_words)
                y_bot = max(w["bottom"] for w in prod_words)
                if y_bot <= header_bottom:
                    continue

                prod = match_product_from_words(prod_words, city_max, prod_max)
                if not prod:
                    continue

                prod_cy = (y_top + y_bot) / 2
                band_words = [
                    w for w in body_words
                    if abs(((w["top"] + w["bottom"]) / 2) - prod_cy) <= 3.5
                ]
                price = extract_common_price_from_band(
                    band_words, overall_min_x, common_min_x
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

            block_places = []
            for block, raw_prov in zip(blocks, raw_block_provs):
                block_region = region
                if region_headers:
                    block_region = previous_region or region_headers[0][1]
                    for header_y, header_region in region_headers:
                        if header_y > block[0]["top"]:
                            break
                        block_region = header_region

                y_start = block[0]["top"] - 10
                y_end = block[-1]["bottom"] + 10
                # Product-order splits can place the city label above the block.
                city_y_start = block[0]["top"] - 25
                c_words = [
                    w for w in body_words
                    if city_y_start <= w["top"] <= y_end
                    and prov_max <= (w["x0"] + w["x1"]) / 2 < city_max
                ]
                raw_city = join_place(c_words, is_province=False)

                if len(unique_page_provs) == 1:
                    prov_name = match_master_province(
                        unique_page_provs[0],
                        block_region,
                        page_province or last_province,
                    )
                    last_province = prov_name
                elif raw_prov:
                    prov_name = match_master_province(
                        raw_prov,
                        block_region,
                        page_province or last_province,
                    )
                    last_province = prov_name
                else:
                    prov_name = match_master_province(
                        page_province,
                        block_region,
                        last_province,
                    )
                    if prov_name:
                        last_province = prov_name

                city_labels = []
                for city_line in group_words_into_lines(c_words, y_tol=12):
                    label = join_place(city_line, is_province=False)
                    label_place = match_master_place(
                        label, block_region, prov_name, MASTER_PLACES
                    )
                    if label_place[1]:
                        label_y = sum(
                            (word["top"] + word["bottom"]) / 2
                            for word in city_line
                        ) / len(city_line)
                        city_labels.append((label_y, label_place))

                distinct_labels = {place for _, place in city_labels}
                if len(distinct_labels) > 1:
                    rows_by_place = {place: [] for place in distinct_labels}
                    for row in block:
                        row_y = (row["top"] + row["bottom"]) / 2
                        nearest_place = min(
                            city_labels,
                            key=lambda label: abs(label[0] - row_y),
                        )[1]
                        rows_by_place[nearest_place].append(row)

                    for label_place in sorted(
                        (
                            place for place in distinct_labels
                            if rows_by_place[place]
                        ),
                        key=lambda place: min(
                            row["top"] for row in rows_by_place[place]
                        ),
                    ):
                        assigned_rows = rows_by_place[label_place]
                        block_places.append({
                            "block": assigned_rows,
                            "province": label_place[0],
                            "city": label_place[1],
                            "raw_city": raw_city,
                            "region": block_region,
                        })
                    continue

                prov_name, city_name = match_master_place(
                    raw_city, block_region, prov_name, MASTER_PLACES
                )
                if distinct_labels:
                    prov_name, city_name = next(iter(distinct_labels))
                if prov_name:
                    last_province = prov_name
                block_places.append({
                    "block": block,
                    "province": prov_name,
                    "city": city_name,
                    "raw_city": raw_city,
                    "region": block_region,
                })

            fill_missing_city_gaps(block_places)

            for place in block_places:
                block_prices = {
                    row["product"]: row["price"] for row in place["block"]
                }
                place_groups.append({
                    "region": place["region"],
                    "province": place["province"],
                    "city": place["city"],
                    "prices": block_prices,
                })

            print(f"  p{idx + 1} [{source}]: {len(block_places)} cities, {len(product_rows)} rows")
            if region_headers:
                last_region = region_headers[-1][1]

    groups_with_prices = {
        (
            region_match_key(group["region"]),
            place_match_key(group["province"]),
            place_match_key(group["city"]),
        )
        for group in place_groups
        if group["city"]
        and any(
            str(group["prices"].get(product, "-")).strip() not in {"", "-"}
            for product in PRODUCT_ORDER
        )
    }

    records = []
    for group in place_groups:
        has_price = any(
            str(group["prices"].get(product, "-")).strip() not in {"", "-"}
            for product in PRODUCT_ORDER
        )
        place_key = (
            region_match_key(group["region"]),
            place_match_key(group["province"]),
            place_match_key(group["city"]),
        )
        if not group["city"] and not has_price:
            continue
        if not has_price and place_key in groups_with_prices:
            continue

        for prod_name in PRODUCT_ORDER:
            records.append({
                "Region": group["region"],
                "Province": group["province"],
                "City": group["city"],
                "Product": prod_name,
                week_col: group["prices"].get(prod_name, "-"),
            })

    df = pd.DataFrame(records, columns=KEY_COLS + [week_col])
    return df.reset_index(drop=True)


MASTER_PLACES = build_master_places(MASTER_ORDER)
if MASTER_ORDER.exists():
    master_regions = pd.read_csv(
        MASTER_ORDER, usecols=["Region"], dtype=str
    )["Region"].dropna().drop_duplicates()
    MASTER_REGION_NAMES = {
        region_match_key(region): clean_text(region)
        for region in master_regions
    }
else:
    MASTER_REGION_NAMES = {}
if not MASTER_PLACES:
    print(f"Warning: {MASTER_ORDER} is missing or has no usable cities; city matching is disabled.")


mode = input(
    "Select mode (auto / text / ocr / rapidocr / tesseract): "
).strip().lower()
while mode not in OUTPUT_DIRS:
    mode = input(
        "Invalid mode. Choose auto, text, ocr, rapidocr, or tesseract: "
    ).strip().lower()

if mode == "tesseract":
    if not get_tesseract_command():
        raise SystemExit(
            "Tesseract mode selected, but tesseract.exe was not found. "
            "Add it to PATH or set TESSERACT_CMD to its full path."
        )
    OCR_ENGINE = "tesseract"
elif mode == "rapidocr":
    OCR_ENGINE = "rapidocr"

out_dir = OUTPUT_DIRS[mode]
out_dir.mkdir(parents=True, exist_ok=True)
all_pdf_files = sorted(INPUT_FOLDER.glob("*.pdf"))
pdf_files = [
    path for path in all_pdf_files
    if not path.stem.lower().startswith("petro_ncr_")
]
if len(pdf_files) != len(all_pdf_files):
    print(f"Skipping {len(all_pdf_files) - len(pdf_files)} NCR PDF(s).")

print(f"\n=== MODE: {mode.upper()} -> {out_dir}/ ===")

for pdf_path in pdf_files:
    week_col = extract_week_label(pdf_path)
    out_csv = out_dir / f"{safe_filename(week_col)}.csv"

    if SKIP_EXISTING and out_csv.exists():
        if extraction_matches_master_order(out_csv, MASTER_PLACES):
            print(f"Skipped (already matches master order): {out_csv}")
            continue
        print(f"Re-extracting (city names do not match master order): {out_csv}")

    print(f"Extracting {pdf_path.name} -> {out_csv}")
    week_df = parse_pdf(pdf_path, week_col, mode=mode)

    if week_df.empty:
        print(f"  No rows extracted from {pdf_path.name}")
        continue

    week_df.to_csv(out_csv, index=False)
    print(f"  Saved {len(week_df)} rows to {out_csv}")