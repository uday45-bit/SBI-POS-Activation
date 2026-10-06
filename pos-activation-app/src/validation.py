"""
Validation checks performed after the raw file is cleaned and matched, but
before the workbook is generated. Anything appended to `errors` stops report
generation (per spec section 25: "If a serious data problem exists, stop
report generation and display a clear error."). Anything in `warnings` is
shown to the user but does not block generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config


@dataclass
class MatchResult:
    matched: pd.DataFrame          # raw rows whose Office ID is a Master office id
    unmatched: pd.DataFrame        # raw rows whose Office ID is not in the Master


@dataclass
class ValidationReport:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return len(self.errors) > 0


def match_raw_to_universe(raw_cleaned: pd.DataFrame, universe: pd.DataFrame) -> MatchResult:
    master_ids = set(universe["office_id"])
    is_master = raw_cleaned["office_id"].isin(master_ids)

    matched = raw_cleaned[is_master].copy()
    unmatched = raw_cleaned[~is_master].copy()

    return MatchResult(matched=matched, unmatched=unmatched)


def validate_raw_load(raw_result, universe: pd.DataFrame) -> ValidationReport:
    report = ValidationReport()

    if raw_result.raw_row_count == 0:
        report.errors.append("The uploaded file contains no data rows. Please check the file and try again.")
        return report

    if raw_result.header_warnings:
        report.warnings.extend(raw_result.header_warnings)

    if raw_result.duplicate_office_id_count > 0:
        report.warnings.append(
            f"{raw_result.duplicate_office_id_count} Office ID(s) appeared more than once in the raw "
            "file and were aggregated (summed) before matching: "
            + ", ".join(raw_result.duplicate_office_ids[:20])
            + (" ..." if len(raw_result.duplicate_office_ids) > 20 else "")
        )

    if raw_result.dropped_blank_office_id_rows > 0:
        report.warnings.append(
            f"{raw_result.dropped_blank_office_id_rows} row(s) had a blank Office ID and were ignored."
        )

    if raw_result.non_numeric_cells_zeroed > 0:
        report.warnings.append(
            f"{raw_result.non_numeric_cells_zeroed} transaction count/amount cell(s) were blank or "
            "non-numeric and were treated as 0."
        )

    return report


def validate_reconciliation(consolidation_df: pd.DataFrame, division_sheets: dict, devices_supplied_total: int) -> ValidationReport:
    """Cross-check that consolidation totals reconcile with the division
    sheets and the reference-data device count."""
    report = ValidationReport()

    sum_devices_activated = int(consolidation_df.loc[consolidation_df["division"] != "Total", "devices_activated"].sum())
    devices_activated_from_sheets = sum(
        int(df.loc[df["activation_status"] == config.STATUS_GREEN, "devices_supplied"].sum())
        for df in division_sheets.values()
    )
    if sum_devices_activated != devices_activated_from_sheets:
        report.errors.append(
            "Reconciliation error: total devices activated in the consolidation sheet "
            f"({sum_devices_activated}) does not match the sum of devices at GREEN offices "
            f"across division sheets ({devices_activated_from_sheets})."
        )

    sum_devices_supplied = int(consolidation_df.loc[consolidation_df["division"] != "Total", "devices_supplied"].sum())
    if sum_devices_supplied != devices_supplied_total:
        report.errors.append(
            "Reconciliation error: total devices supplied in the consolidation sheet "
            f"({sum_devices_supplied}) does not match the total device count from "
            f"reference data ({devices_supplied_total})."
        )

    rows_in_sheets = sum(len(df) for df in division_sheets.values())
    devices_in_sheets = sum(int(df["devices_supplied"].sum()) for df in division_sheets.values())
    if devices_in_sheets != sum_devices_supplied:
        report.errors.append(
            "Reconciliation error: total devices across division-sheet office rows "
            f"({devices_in_sheets}, across {rows_in_sheets} offices) does not match the "
            f"consolidation sheet's devices-supplied total ({sum_devices_supplied})."
        )

    return report
