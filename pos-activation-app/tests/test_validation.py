"""
Unit tests for src/raw_transaction.py (header-name-based column lookup for
the four metric columns, summary-row removal, duplicate aggregation,
non-numeric handling) and the reconciliation checks in src/validation.py.

Run with:  python -m unittest discover -s tests -v
"""

import io
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.raw_transaction import load_raw_transaction_file
from src.validation import validate_reconciliation
from src import config


def make_csv_bytes(rows: list[str]) -> io.BytesIO:
    buf = io.BytesIO("\n".join(rows).encode("utf-8"))
    buf.name = "test_raw.csv"
    return buf


# Standard 6-column layout: Office ID, Office Name, then the four metric
# columns in the order given in config.RAW_METRIC_COLUMN_NAMES. Several
# tests below deliberately use a DIFFERENT layout to prove the lookup is by
# header name, not position.
HEADER = "Office ID,Office Name,SBIPOS-CARD (Cnt),SBIPOS-CARD (Amt),SBIPOS BHARATQR (Cnt),SBIPOS BHARATQR (Amt)"


def csv_row(office_id, name="Some Office", card_count=0, card_amount=0, qr_count=0, qr_amount=0):
    cells = [str(office_id), name, str(card_count), str(card_amount), str(qr_count), str(qr_amount)]
    return ",".join(cells)


class TestRawTransactionLoading(unittest.TestCase):
    def test_basic_load_with_header(self):
        rows = [HEADER, csv_row("1001", card_count=12, card_amount=1500)]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.raw_row_count, 1)
        self.assertEqual(len(result.cleaned), 1)
        self.assertEqual(result.cleaned.iloc[0]["office_id"], "1001")
        self.assertEqual(result.cleaned.iloc[0]["sbipos_card_count"], 12)

    def test_summary_row_is_dropped(self):
        rows = [
            HEADER,
            csv_row("1001", card_count=5),
            csv_row("", name="Summary (All Offices)", card_count=999),
        ]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.dropped_summary_rows, 1)
        self.assertEqual(len(result.cleaned), 1)
        self.assertNotIn(999, result.cleaned["sbipos_card_count"].values)

    def test_blank_office_id_row_dropped(self):
        rows = [HEADER, csv_row("1001", card_count=5), csv_row("", name="Some Row", card_count=3)]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.dropped_blank_office_id_rows, 1)
        self.assertEqual(len(result.cleaned), 1)

    def test_duplicate_office_id_aggregated(self):
        # Case 9 (spec section 28): multi-device office reported across
        # multiple raw rows for the same Office ID -> must be summed, not
        # overwritten or duplicated.
        rows = [
            HEADER,
            csv_row("1002", card_count=8, card_amount=1000),
            csv_row("1002", card_count=7, card_amount=500),
        ]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.duplicate_office_id_count, 1)
        self.assertEqual(len(result.cleaned), 1)
        row = result.cleaned.iloc[0]
        self.assertEqual(row["sbipos_card_count"], 15)
        self.assertEqual(row["sbipos_card_amount"], 1500)

    def test_non_numeric_amount_treated_as_zero(self):
        rows = [HEADER, csv_row("1001", card_count="N/A", card_amount="")]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.cleaned.iloc[0]["sbipos_card_count"], 0)
        self.assertEqual(result.cleaned.iloc[0]["sbipos_card_amount"], 0)
        self.assertGreaterEqual(result.non_numeric_cells_zeroed, 1)

    def test_combined_card_and_qr_fields_present(self):
        rows = [HEADER, csv_row("1001", card_count=3, qr_count=4, card_amount=100, qr_amount=200)]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        row = result.cleaned.iloc[0]
        self.assertEqual(row["sbipos_card_count"], 3)
        self.assertEqual(row["sbipos_bharatqr_count"], 4)
        self.assertEqual(row["sbipos_card_amount"], 100)
        self.assertEqual(row["sbipos_bharatqr_amount"], 200)

    def test_missing_required_column_raises_clear_error(self):
        bad_header = "Office ID,Office Name,SBIPOS-CARD (Cnt),SBIPOS-CARD (Amt),SBIPOS BHARATQR (Cnt)"  # missing Amt
        rows = [bad_header, "1001,Some Office,5,100,2"]
        with self.assertRaises(ValueError) as ctx:
            load_raw_transaction_file(make_csv_bytes(rows))
        self.assertIn("SBIPOS BHARATQR (Amt)", str(ctx.exception))

    def test_no_header_row_at_all_raises(self):
        # Header-name lookup requires an actual header row now.
        rows = [csv_row("1001", card_count=9)]
        with self.assertRaises(ValueError):
            load_raw_transaction_file(make_csv_bytes(rows))

    def test_columns_found_by_name_regardless_of_position(self):
        # Office ID must stay in column A (position is fixed by design), but
        # the four metric columns are deliberately reordered/moved — proves
        # they're found by header text, not column index.
        header = "Office ID,SBIPOS BHARATQR (Amt),Office Name,SBIPOS-CARD (Amt),SBIPOS BHARATQR (Cnt),SBIPOS-CARD (Cnt)"
        row = "1001,200,Some Office,100,4,3"
        result = load_raw_transaction_file(make_csv_bytes([header, row]))
        self.assertEqual(result.cleaned.iloc[0]["office_id"], "1001")
        r = result.cleaned.iloc[0]
        self.assertEqual(r["sbipos_card_count"], 3)
        self.assertEqual(r["sbipos_card_amount"], 100)
        self.assertEqual(r["sbipos_bharatqr_count"], 4)
        self.assertEqual(r["sbipos_bharatqr_amount"], 200)

    def test_header_matching_ignores_case_spacing_and_hyphens(self):
        header = "Office ID,Office Name,sbipos card (cnt),SBIPOS - CARD (AMT),sbiposbharatqr(cnt),SBIPOS-BHARATQR (Amt)"
        row = csv_row("1001", card_count=7, card_amount=250, qr_count=1, qr_amount=50)
        result = load_raw_transaction_file(make_csv_bytes([header, row]))
        r = result.cleaned.iloc[0]
        self.assertEqual(r["sbipos_card_count"], 7)
        self.assertEqual(r["sbipos_card_amount"], 250)

    def test_matched_columns_reports_actual_header_text(self):
        rows = [HEADER, csv_row("1001", card_count=5)]
        result = load_raw_transaction_file(make_csv_bytes(rows))
        self.assertEqual(result.matched_columns["sbipos_card_count"], "SBIPOS-CARD (Cnt)")

    def test_too_few_columns_raises(self):
        buf = io.BytesIO(b"Office ID\n1001\n")
        buf.name = "bad.csv"
        with self.assertRaises(ValueError):
            load_raw_transaction_file(buf)


class TestReconciliationValidation(unittest.TestCase):
    def test_consistent_totals_pass(self):
        # 1 device GREEN (2 devices), 1 device RED (1 device) -> supplied=3, activated=2
        consolidation = pd.DataFrame([
            {"division": "Secunderabad", "devices_supplied": 3, "devices_activated": 2},
            {"division": "Total", "devices_supplied": 3, "devices_activated": 2},
        ])
        sec_sheet = pd.DataFrame([
            {"activation_status": config.STATUS_GREEN, "devices_supplied": 2},
            {"activation_status": config.STATUS_RED, "devices_supplied": 1},
        ])
        report = validate_reconciliation(consolidation, {"Secunderabad": sec_sheet}, devices_supplied_total=3)
        self.assertFalse(report.is_blocking)

    def test_mismatched_activated_count_flagged(self):
        consolidation = pd.DataFrame([
            {"division": "Secunderabad", "devices_supplied": 3, "devices_activated": 99},  # wrong
            {"division": "Total", "devices_supplied": 3, "devices_activated": 99},
        ])
        sec_sheet = pd.DataFrame([
            {"activation_status": config.STATUS_GREEN, "devices_supplied": 2},
            {"activation_status": config.STATUS_RED, "devices_supplied": 1},
        ])
        report = validate_reconciliation(consolidation, {"Secunderabad": sec_sheet}, devices_supplied_total=3)
        self.assertTrue(report.is_blocking)

    def test_mismatched_supplied_total_flagged(self):
        consolidation = pd.DataFrame([
            {"division": "Secunderabad", "devices_supplied": 3, "devices_activated": 2},
            {"division": "Total", "devices_supplied": 3, "devices_activated": 2},
        ])
        sec_sheet = pd.DataFrame([
            {"activation_status": config.STATUS_GREEN, "devices_supplied": 2},
            {"activation_status": config.STATUS_RED, "devices_supplied": 1},
        ])
        # reference-data device total doesn't match consolidation's own sum
        report = validate_reconciliation(consolidation, {"Secunderabad": sec_sheet}, devices_supplied_total=999)
        self.assertTrue(report.is_blocking)


if __name__ == "__main__":
    unittest.main()
