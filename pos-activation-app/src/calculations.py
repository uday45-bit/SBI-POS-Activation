"""
Core business calculations, implemented exactly per the authoritative spec:

    Count Threshold  = 10   x Devices Supplied
    Amount Threshold = 5000 x Devices Supplied

    GREEN  = Combined Count >= Count Threshold  OR  Combined Amount >= Amount Threshold
    ORANGE = not GREEN AND (Combined Count > 0 OR Combined Amount > 0)
    RED    = Combined Count == 0 AND Combined Amount == 0

Sorting is a SEPARATE concern from status (see module docstring in report.py):
    Group 1 = Combined Count  >= Count Threshold
    Group 2 = (not Group 1) AND Combined Amount >= Amount Threshold
    Group 3 = everything else
Within a group: Combined Count desc, then Combined Amount desc, then Office Name asc.
"""

from __future__ import annotations

import pandas as pd

from . import config


def merge_transactions_onto_eligible(eligible: pd.DataFrame, raw_cleaned: pd.DataFrame) -> pd.DataFrame:
    """Left-join the cleaned/aggregated raw transaction data onto the eligible
    offices. Any eligible office absent from the raw upload simply gets zero
    transactions (it is still eligible — it just had no activity yet)."""
    merged = eligible.merge(raw_cleaned, on="office_id", how="left")
    for col in ["sbipos_card_count", "sbipos_card_amount", "sbipos_bharatqr_count", "sbipos_bharatqr_amount"]:
        merged[col] = merged[col].fillna(0)
    return merged


def compute_combined_and_thresholds(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["combined_count"] = df["sbipos_card_count"] + df["sbipos_bharatqr_count"]
    df["combined_amount"] = df["sbipos_card_amount"] + df["sbipos_bharatqr_amount"]
    df["count_threshold"] = config.COUNT_THRESHOLD_PER_DEVICE * df["devices_supplied"]
    df["amount_threshold"] = config.AMOUNT_THRESHOLD_PER_DEVICE * df["devices_supplied"]
    return df


def assign_activation_status(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def status_for(row):
        is_green = (row["combined_count"] >= row["count_threshold"]) or (
            row["combined_amount"] >= row["amount_threshold"]
        )
        if is_green:
            return config.STATUS_GREEN
        if row["combined_count"] > 0 or row["combined_amount"] > 0:
            return config.STATUS_ORANGE
        return config.STATUS_RED

    df["activation_status"] = df.apply(status_for, axis=1)
    return df


def assign_sort_group(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def group_for(row):
        if row["combined_count"] >= row["count_threshold"]:
            return 1
        if row["combined_amount"] >= row["amount_threshold"]:
            return 2
        return 3

    df["sort_group"] = df.apply(group_for, axis=1)
    return df


def sort_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Priority group ascending, then Combined Count desc, Combined Amount
    desc, Office Name asc as the final stable tie-breaker."""
    return df.sort_values(
        by=["sort_group", "combined_count", "combined_amount", "office_name"],
        ascending=[True, False, False, True],
        kind="mergesort",  # stable sort
    ).reset_index(drop=True)


def run_full_calculation(eligible: pd.DataFrame, raw_cleaned: pd.DataFrame) -> pd.DataFrame:
    """Full pipeline for eligible offices: merge transactions -> combined
    totals -> thresholds -> activation status -> sort group -> sorted order.
    Does not sort by division; caller should filter to a division first if a
    per-division sheet order is required."""
    merged = merge_transactions_onto_eligible(eligible, raw_cleaned)
    merged = compute_combined_and_thresholds(merged)
    merged = assign_activation_status(merged)
    merged = assign_sort_group(merged)
    return merged
