"""
Assembles the two output shapes the rest of the app (Streamlit UI, Excel
export) consumes:

    1. `build_consolidation()`   -> one row per division + a Total row, with
                                     the 8 consolidation columns from spec
                                     section 11.
    2. `build_division_sheets()` -> {division_name: DataFrame} with one row
                                     per eligible office, sorted per spec
                                     section 14, with the 13 division-sheet
                                     columns from spec section 13.

Both are built from the SAME `calculated` DataFrame (eligible offices with
activation status already assigned) and the SAME `universe` DataFrame
(all 280 Master offices with device_status already assigned), so the two
sheets can never silently drift apart.
"""

from __future__ import annotations

import pandas as pd

from . import config
from .calculations import sort_for_display


DIVISION_SHEET_COLUMNS = [
    "sl_no", "office_id", "office_name", "devices_supplied",
    "sbipos_card_count", "sbipos_card_amount",
    "sbipos_bharatqr_count", "sbipos_bharatqr_amount",
    "combined_count", "combined_amount",
    "count_threshold", "amount_threshold",
    "activation_status", "sort_group",
]

DIVISION_SHEET_HEADERS = {
    "sl_no": "Sl.No.",
    "office_id": "Office ID",
    "office_name": "Office Name",
    "devices_supplied": "No. of POS Devices Supplied",
    "sbipos_card_count": "SBI POS Card Count",
    "sbipos_card_amount": "SBI POS Card Amount",
    "sbipos_bharatqr_count": "SBI POS Bharat QR Count",
    "sbipos_bharatqr_amount": "SBI POS Bharat QR Amount",
    "combined_count": "Combined Transaction Count",
    "combined_amount": "Combined Transaction Amount",
    "count_threshold": "Count Threshold",
    "amount_threshold": "Amount Threshold",
    "activation_status": "Activation Status",
    "sort_group": "Sort Group",
}

CONSOLIDATION_HEADERS = {
    "sl_no": "Sl.No",
    "division": "Division Name",
    "devices_supplied": "No. of POS Machines Supplied",
    "devices_activated": "No. of SBI POS \u2013 Activation Done",
    "activation_pct": "Activation %",
}


def build_division_sheets(calculated: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sheets = {}
    for division in config.DIVISIONS:
        div_df = calculated[calculated["division"] == division].copy()
        div_df = sort_for_display(div_df)
        div_df.insert(0, "sl_no", range(1, len(div_df) + 1))
        sheets[division] = div_df[DIVISION_SHEET_COLUMNS].reset_index(drop=True)
    return sheets


def build_consolidation(universe: pd.DataFrame, calculated: pd.DataFrame) -> pd.DataFrame:
    """Division-wise consolidation, in DEVICE units throughout (not office
    counts) so 'devices supplied', 'devices activated' and the activation
    percentage are all directly comparable:

        devices_supplied  = sum of devices_supplied across every Master
                             office in the division (0 for offices with no
                             device, so this is exactly the division's total
                             device count)
        devices_activated = sum of devices_supplied for offices in the
                             division whose activation_status is GREEN
                             (an office's whole device count counts once its
                             combined activity clears that office's
                             device-adjusted threshold — activation is
                             decided per OFFICE, since that's the level the
                             raw transaction data is reported at, but the
                             devices at a GREEN office all count as active)
        activation_pct    = devices_activated / devices_supplied * 100
    """
    rows = []
    for division in config.DIVISIONS:
        div_universe = universe[universe["division"] == division]
        devices_supplied = int(div_universe["devices_supplied"].sum())

        div_calc = calculated[calculated["division"] == division]
        green = div_calc[div_calc["activation_status"] == config.STATUS_GREEN]
        devices_activated = int(green["devices_supplied"].sum())

        activation_pct = (devices_activated / devices_supplied) if devices_supplied > 0 else 0.0

        rows.append({
            "division": division,
            "devices_supplied": devices_supplied,
            "devices_activated": devices_activated,
            "activation_pct": activation_pct,
        })

    df = pd.DataFrame(rows)
    # Divisions ranked by Activation % descending (best-performing first);
    # the Total row is appended afterward and always stays last.
    df = df.sort_values("activation_pct", ascending=False, kind="mergesort").reset_index(drop=True)

    total_row = {
        "division": "Total",
        "devices_supplied": int(df["devices_supplied"].sum()),
        "devices_activated": int(df["devices_activated"].sum()),
        "activation_pct": (
            df["devices_activated"].sum() / df["devices_supplied"].sum()
            if df["devices_supplied"].sum() > 0 else 0.0
        ),
    }
    df = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    df.insert(0, "sl_no", list(range(1, len(config.DIVISIONS) + 1)) + [""])
    return df
