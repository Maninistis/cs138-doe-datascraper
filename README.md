# DOE VISAYAS PRICE MONITORING SCRAPER

This repository contains the shared workflow for extracting weekly DOE fuel
prices and organizing them into the four Visayas/NIR work assignments.

IMPORTANT SCOPE
---------------
The current master_order.csv covers ONLY these four assignments:
- Region VI
- Region VII
- Region VIII
- NIR

It contains 68 reference locations and 7 products per location (476 rows).
It is not a nationwide master list. If your team is assigned a region outside
these four, do not assume it is covered by this template. Coordinate with the
project lead before changing master_order.csv or adding locations.

FILES
-----
- extract_pdfs.py
  Extracts prices from text-based or scanned DOE PDFs. It uses PDF text
  extraction first and OCR as a fallback, then aligns recognized locations
  and products to master_order.csv.
- merge_csvs.py
  Combines the weekly extracted CSV files using the master template, preserves
  its row order, and creates the combined and regional outputs.
- master_order.csv
  The shared canonical list of Region/Province/City/Product rows. Do not edit
  this file for an individual run. It controls the accepted locations, region
  labels, product rows, and output order.
- requirements.txt
  Python dependencies.

REGION ASSIGNMENTS
------------------
Region VI (140 rows; 20 locations)
  Aklan, Antique, Capiz, Guimaras, Iloilo

Region VII (147 rows; 21 locations)
  Bohol, Cebu

Region VIII (105 rows; 15 locations)
  Biliran, Eastern Samar, Leyte, Northern Samar, Samar, Southern Leyte

NIR (84 rows; 12 locations)
  Negros Occidental, Negros Oriental, Siquijor


AI PROMPT FOR TEAMMATES ASSIGNED TO A DIFFERENT REGION
------------------------------------------------------
Each teammate should do this work on their OWN branch, not directly on
arizbranch or another teammate's branch. Start from the shared scripts, then
ask an AI assistant to adapt the location template and extraction workflow to
your assigned region using the actual DOE PDF as the source of truth.

Before using the prompt:
- Switch to your own branch and make sure you are not on arizbranch:

    git fetch origin
    git switch -c <your-branch-name> origin/arizbranch

  If your branch already exists, use `git switch <your-branch-name>` instead.
- Attach/provide the original DOE PDF(s) for your assigned region.
- Also provide the current `extract_pdfs.py`, `merge_csvs.py`, and
  `master_order.csv` from the repository.
- Tell the AI the exact region label you are assigned (for example, `Region V`
  or another region). Do not assume the four Visayas/NIR regions in the current
  master template apply to your assignment.

COPY-AND-PASTE PROMPT
---------------------
I am assigned to process DOE weekly fuel-price monitoring data for
[INSERT MY REGION LABEL]. This is a separate Git branch for my region; do not
modify or overwrite another teammate's branch or the shared arizbranch.

I have attached:
1. The original DOE PDF(s) for my assigned region.
2. The current extract_pdfs.py and merge_csvs.py.
3. The current master_order.csv, which is a template for a different/current
   set of regions and may not contain all of my assigned locations.

Please inspect the actual PDF(s) and the existing scripts before changing
anything. Then adapt the workflow for [INSERT MY REGION LABEL] with these
requirements:

A. Build my region's master_order.csv from the source PDF(s), not by guessing.
- Include every province and city/municipality that appears in the PDF for my
  assigned region.
- Preserve the order in which locations appear in the DOE document unless I
  provide a different official ordering template.
- Assign each city/municipality to the correct province based on the PDF.
- Use the exact region label `[INSERT MY REGION LABEL]` in the Region column.
- Include the seven products for every location in this exact order:
  RON 100, RON 97, RON 95, RON 91, DIESEL, DIESEL PLUS, KEROSENE.
- Do not add locations from other regions. Do not silently drop locations.
- If the PDF is unclear or a location cannot be confidently identified, list it
  as an unresolved item for my review instead of inventing a province/city.

B. Correct the price extraction logic using the layout of MY attached PDF.
- Match each price to the correct product row using the PDF's actual row
  coordinates/boundaries. Do not use a vertical tolerance that can mix
  adjacent product rows.
- Read only the intended OVERALL RANGE and COMMON columns. Do not accidentally
  include values from INDEPENDENT, JETTI, or other neighboring columns.
- If COMMON contains a valid numeric price, use COMMON.
- Otherwise, if OVERALL RANGE contains one numeric price, use that price.
- Otherwise, if OVERALL RANGE contains a lower and upper bound, use their
  midpoint: (lower + upper) / 2, rounded to two decimal places.
- If COMMON and OVERALL RANGE are both empty or contain no valid price, output
  `-`. Never infer a price from another column, another product row, or a
  neighboring location. Never create a value for a blank source cell.
- Preserve genuine zero values if present; do not treat zero as blank.

C. Adapt the scripts without breaking the workflow.
- Keep PDF text extraction and OCR fallback where applicable.
- Keep the merge script based on a left join from my master_order.csv so that
  every expected location/product row remains present.
- Ignore extracted locations outside my assigned master template.
- Ensure missing weekly prices are `-`, not blank or fabricated.
- Generate the combined output and a regional output for my assigned region.
- Do not hard-code the four Visayas/NIR labels if that would prevent my region
  from working. Make the region label configurable or correctly set it for
  this branch.
- Keep the scripts compatible with the repository's requirements.txt and
  document any new dependencies.

D. Validate before returning files.
- Compare the generated master template against all locations in the PDF.
- Confirm the expected number of locations and rows (locations multiplied by
  seven products).
- Confirm there are no duplicate Region/Province/City/Product keys.
- Confirm every location has exactly the seven products in the required order.
- Spot-check extracted prices against the original PDF, including COMMON
  values, single overall prices, ranges, and blank cells.
- Show examples of source PDF rows and their extracted results so I can verify
  the row alignment.
- Run Python syntax checks and tests for the price-selection rules.
- Do not claim full PDF validation unless you actually ran the extractor on
  the attached PDF(s) and checked the results.

Return the complete updated extract_pdfs.py, merge_csvs.py, master_order.csv,
requirements.txt (if changed), and README instructions for my region. Explain
exactly which files changed and any unresolved PDF ambiguities. Do not change
files unrelated to my region assignment.

AFTER THE AI RETURNS THE FILES
------------------------------
1. Review the generated master_order.csv and unresolved items against the PDF.
2. Replace only the files in your own branch that are relevant to your region.
3. Run extraction and merging, then manually spot-check prices against the PDF.
4. Check the changes before committing:

     git status
     git diff --stat
     git diff

5. Commit and push your own branch:

     git add extract_pdfs.py merge_csvs.py master_order.csv README.txt
     git commit -m "Add DOE extraction support for [REGION]"
     git push -u origin <your-branch-name>

Do not push your region-specific master_order.csv or script changes to
arizbranch unless the project lead explicitly asks to consolidate them.

TEAMMATE WORKFLOW
-----------------
1. Clone the repository and switch to the shared working branch:

     git clone <repository-url>
     cd <repository-folder>
     git switch arizbranch

   If you already cloned the repository:

     git fetch origin
     git switch arizbranch
     git pull

2. Create and activate a Python virtual environment (recommended):

   Windows PowerShell:
     py -m venv .venv
     .\.venv\Scripts\Activate.ps1

   macOS/Linux:
     python3 -m venv .venv
     source .venv/bin/activate

3. Install dependencies:

     python -m pip install -r requirements.txt

4. Put the original DOE PDF(s) for the monitoring week in the `pdfs/` folder.
   Keep the original PDFs unchanged. The date/week in each PDF filename should
   be recognizable by the script so the extracted CSV gets a useful week
   column name. Do not put previously generated CSVs in `pdfs/`.

5. Run the extraction from the repository root:

     python extract_pdfs.py

   Extracted weekly CSV files are saved in `extracted/`.

6. Run the merger:

     python merge_csvs.py

   The combined file is `doe_visayas_prices.csv`. Regional files are written
   to `regional_outputs/`.

7. Open the output for your assigned region and review it against the original
   DOE PDF before submitting or using the prices. A structurally complete row
   does not prove that the numeric price was read correctly from the PDF.

OUTPUTS AND EXPECTED ROW COUNTS
-------------------------------
Combined:
  doe_visayas_prices.csv                         476 rows

Regional outputs:
  Region VI:   regional_outputs/region_vi.csv    140 rows
  Region VII:  regional_outputs/region_vii.csv   147 rows
  Region VIII: regional_outputs/region_viii.csv  105 rows
  NIR:         regional_outputs/nir.csv            84 rows

Each location should have these seven product rows, in this order:
  RON 100, RON 97, RON 95, RON 91, DIESEL, DIESEL PLUS, KEROSENE

A missing price must be represented by `-`, not an invented or inferred value.
For a price range, follow the extraction rule implemented in the current
extractor: use COMMON when present; otherwise use the midpoint of OVERALL RANGE;
if neither contains a valid value, retain `-`.

IMPORTANT TEAM RULES
--------------------
- Do not edit the source DOE PDFs.
- Do not manually move a price to another product row just to fill a blank.
- Do not replace a blank source value with a calculated or guessed price.
- Do not change the Region/Province/City/Product labels in master_order.csv
  without coordinating with the project lead. All teammates must use the same
  master template to keep outputs merge-compatible.
- If a location is missing, assigned to the wrong province, or absent from the
  master, report the PDF page and location instead of guessing.
- Before committing, check `git status` and commit only the files relevant to
  your task. Avoid committing generated PDFs or large intermediate files
  unless the team specifically requests them.

VALIDATION CHECKLIST
--------------------
- The combined output has 476 rows.
- Your regional output has the expected row count listed above.
- Region labels are exactly `Region VI`, `Region VII`, `Region VIII`, or `NIR`.
- Each location has exactly 7 products in the expected order.
- Missing source prices remain `-`.
- Spot-check extracted values against the original DOE PDF, especially rows
  containing ranges, COMMON prices, and blank cells.

TROUBLESHOOTING
---------------
- `ModuleNotFoundError`: activate the virtual environment and reinstall with
  `python -m pip install -r requirements.txt`.
- No PDFs found: confirm that the PDFs are inside `pdfs/` and run the scripts
  from the repository root.
- Wrong/empty regional output: confirm that your master_order.csv uses the
  exact region labels above and that your scripts are the latest shared copies.
- Wrong product prices: do not edit the merged CSV as a workaround. Record the
  source PDF page and row, then report it so the extraction logic can be fixed.