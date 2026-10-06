"""
Central configuration for the POS Activation Status application.

Keeping every "magic number" and label in one place means a future division
rename, threshold change, or reference-file relocation touches this file only.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# BASE_DIR resolves relative to this file, so the app works identically on a
# developer laptop, inside a Docker container, or on Streamlit Community
# Cloud, without ever hard-coding a machine-specific path.
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

OFFICE_MASTER_CSV = DATA_DIR / "office_master.csv"
DEVICE_SUPPLY_CSV = DATA_DIR / "device_supply.csv"
REFERENCE_META_JSON = DATA_DIR / "reference_data_meta.json"

# ---------------------------------------------------------------------------
# Divisions (authoritative order used throughout the app and the workbook)
# ---------------------------------------------------------------------------
DIVISIONS = [
    "Secunderabad",
    "Hyderabad South East",
    "Hyderabad City",
    "Sangareddy",
    "Medak",
    "Hyderabad GPO",
    "Hyderabad Sorting",
]

# Sheet name actually used in the Office Master "division-office-name" column
# maps onto the short division names above. Kept as a normalisation table so
# that stray spelling/spacing in source files ("Medak ", "Secunderebad ")
# never silently creates a phantom 8th division.
DIVISION_NORMALISATION = {
    "medak division": "Medak",
    "medak": "Medak",
    "secunderabad division": "Secunderabad",
    "secunderabad": "Secunderabad",
    "secunderebad": "Secunderabad",  # known misspelling in source data
    "hyderabad sorting division": "Hyderabad Sorting",
    "hyderabad sorting": "Hyderabad Sorting",
    "hyderabad gpo division": "Hyderabad GPO",
    "hyderabad gpo": "Hyderabad GPO",
    "hyderabad city division": "Hyderabad City",
    "hyderabad city": "Hyderabad City",
    "hyderabad south east division": "Hyderabad South East",
    "hyderabad south east": "Hyderabad South East",
    "sangareddy division": "Sangareddy",
    "sangareddy": "Sangareddy",
}

DIVISION_SHEET_NAMES = {d: d for d in DIVISIONS}
CONSOLIDATION_SHEET_NAME = "Division-wise Consolidation"

# Sheet order exactly as specified: consolidation first, then the 7 divisions.
WORKBOOK_SHEET_ORDER = [CONSOLIDATION_SHEET_NAME] + DIVISIONS

# ---------------------------------------------------------------------------
# Activation thresholds (per single device)
# ---------------------------------------------------------------------------
COUNT_THRESHOLD_PER_DEVICE = 10
AMOUNT_THRESHOLD_PER_DEVICE = 5000

# Division-level activation criteria: a division's OWN Activation % (devices
# activated / devices supplied) at or above this is "meeting criteria" for
# the consolidation sheet's row coloring. This is a separate concept from
# the per-office GREEN/ORANGE/RED thresholds above.
DIVISION_ACTIVATION_CRITERIA_PCT = 0.50

# ---------------------------------------------------------------------------
# Activation status labels
# ---------------------------------------------------------------------------
STATUS_GREEN = "GREEN"
STATUS_ORANGE = "ORANGE"
STATUS_RED = "RED"

# Office-level device/eligibility classification labels (distinct from the
# GREEN/ORANGE/RED activation status, which only applies to eligible offices).
# There is no "Pending Whitelisting" or "Closed Office" status: an office is
# either device-supplied (eligible) or not. An Office ID that no longer
# exists in the Master simply never appears at all — see reference_builder.py.
DEVICE_STATUS_WORKING_ELIGIBLE = "Working / Eligible"
DEVICE_STATUS_NOT_SUPPLIED = "Device Not Supplied"

# ---------------------------------------------------------------------------
# Raw transaction file column identification
# ---------------------------------------------------------------------------
# Office ID is still identified by POSITION — it's always the first column.
COL_OFFICE_ID = 0

# The four transaction metric columns are identified by SEARCHING THE HEADER
# ROW for these exact names (matched after normalising case/spacing/hyphens —
# see raw_transaction._normalise_header), NOT by a fixed column position.
# Whatever position they actually sit at in a given export is used.
RAW_METRIC_COLUMN_NAMES = {
    "sbipos_card_count": "SBIPOS-CARD (Cnt)",
    "sbipos_card_amount": "SBIPOS-CARD (Amt)",
    "sbipos_bharatqr_count": "SBIPOS BHARATQR (Cnt)",
    "sbipos_bharatqr_amount": "SBIPOS BHARATQR (Amt)",
}

MIN_RAW_COLUMNS = 2  # Office ID + at least one other column

SUMMARY_ROW_OFFICE_NAME_MARKERS = ["summary (all offices)", "summary(all offices)", "all offices"]

# ---------------------------------------------------------------------------
# Excel formatting
# ---------------------------------------------------------------------------
FONT_NAME = "Calibri"

FILL_GREEN = "C6EFCE"
FONT_GREEN = "006100"
FILL_ORANGE = "FFE0B2"
FONT_ORANGE = "8A4B00"
FILL_RED = "FFC7CE"
FONT_RED = "9C0006"

FILL_HEADER = "1F4E78"
FONT_HEADER = "FFFFFF"

FILL_TOTAL_ROW = "D9E1F2"

# Consolidation-sheet division-row banding, keyed to DIVISION_ACTIVATION_CRITERIA_PCT
# (distinct from the office-level GREEN/ORANGE/RED fills above).
FILL_DIVISION_MEETS_CRITERIA = "C6EFCE"     # sea-green-ish, matches the reference proforma
FILL_DIVISION_BELOW_CRITERIA = "FFC7CE"     # light pink
