"""
Unit tests for src/report.py (consolidation sheet: device-weighted counts,
descending Activation % sort order) and the title-text generator in
src/excel_export.py.

Run with:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.report import build_consolidation
from src.excel_export import consolidation_title_text


def make_universe(rows):
    """rows: list of (office_id, division, devices_supplied)"""
    return pd.DataFrame([
        {"office_id": oid, "division": div, "devices_supplied": n}
        for oid, div, n in rows
    ])


def make_calculated(rows):
    """rows: list of (office_id, division, devices_supplied, activation_status)"""
    return pd.DataFrame([
        {"office_id": oid, "division": div, "devices_supplied": n, "activation_status": status}
        for oid, div, n, status in rows
    ])


class TestBuildConsolidation(unittest.TestCase):
    def test_devices_supplied_is_summed_per_division_including_not_supplied(self):
        universe = make_universe([
            ("1", "Secunderabad", 2), ("2", "Secunderabad", 0),  # office 2 has no device
        ])
        calculated = make_calculated([("1", "Secunderabad", 2, config.STATUS_GREEN)])
        df = build_consolidation(universe, calculated)
        row = df[df.division == "Secunderabad"].iloc[0]
        self.assertEqual(row["devices_supplied"], 2)  # 2 + 0

    def test_devices_activated_counts_devices_not_offices(self):
        # A single GREEN office with 4 devices contributes 4 to devices_activated.
        universe = make_universe([("1", "Medak", 4)])
        calculated = make_calculated([("1", "Medak", 4, config.STATUS_GREEN)])
        df = build_consolidation(universe, calculated)
        row = df[df.division == "Medak"].iloc[0]
        self.assertEqual(row["devices_activated"], 4)

    def test_orange_and_red_offices_do_not_count_as_activated(self):
        universe = make_universe([("1", "Medak", 1), ("2", "Medak", 1)])
        calculated = make_calculated([
            ("1", "Medak", 1, config.STATUS_ORANGE),
            ("2", "Medak", 1, config.STATUS_RED),
        ])
        df = build_consolidation(universe, calculated)
        row = df[df.division == "Medak"].iloc[0]
        self.assertEqual(row["devices_activated"], 0)

    def test_activation_pct_is_devices_activated_over_devices_supplied(self):
        universe = make_universe([("1", "Sangareddy", 4)])
        calculated = make_calculated([("1", "Sangareddy", 4, config.STATUS_GREEN)])
        df = build_consolidation(universe, calculated)
        row = df[df.division == "Sangareddy"].iloc[0]
        self.assertAlmostEqual(row["activation_pct"], 1.0)

    def test_zero_devices_supplied_division_has_zero_pct_not_crash(self):
        universe = make_universe([("1", "Hyderabad GPO", 0)])
        calculated = pd.DataFrame(columns=["office_id", "division", "devices_supplied", "activation_status"])
        df = build_consolidation(universe, calculated)
        row = df[df.division == "Hyderabad GPO"].iloc[0]
        self.assertEqual(row["activation_pct"], 0.0)

    def test_rows_sorted_by_activation_pct_descending_total_last(self):
        rows = []
        for div, pct_devices, total_devices in [
            ("Secunderabad", 1, 4),           # 25%
            ("Hyderabad South East", 4, 4),   # 100%
            ("Hyderabad City", 2, 4),         # 50%
        ]:
            rows.append((f"office-{div}", div, total_devices))
        universe = make_universe(rows)
        calculated_rows = []
        for div, pct_devices, total_devices in [
            ("Secunderabad", 1, 4), ("Hyderabad South East", 4, 4), ("Hyderabad City", 2, 4),
        ]:
            calculated_rows.append((f"office-{div}", div, pct_devices, config.STATUS_GREEN))
        calculated = make_calculated(calculated_rows)

        # Fill the remaining 4 configured divisions with zero-supply rows so
        # build_consolidation's full DIVISIONS loop has something for each.
        extra_divs = [d for d in config.DIVISIONS if d not in {"Secunderabad", "Hyderabad South East", "Hyderabad City"}]
        universe = pd.concat([universe, make_universe([(f"o-{d}", d, 0) for d in extra_divs])], ignore_index=True)

        df = build_consolidation(universe, calculated)
        non_total = df[df.division != "Total"]
        pcts = non_total["activation_pct"].tolist()
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertEqual(non_total.iloc[0]["division"], "Hyderabad South East")  # 100%
        self.assertEqual(df.iloc[-1]["division"], "Total")  # Total always last
        # Sl.No renumbered 1..7 in the new order, blank for Total
        self.assertEqual(list(df["sl_no"])[:3], [1, 2, 3])
        self.assertEqual(df.iloc[-1]["sl_no"], "")


class TestConsolidationTitleText(unittest.TestCase):
    def test_title_contains_period_and_thresholds(self):
        text = consolidation_title_text("01.09.2026", "30.09.2026")
        self.assertIn("01.09.2026 to 30.09.2026", text)
        self.assertIn(str(config.COUNT_THRESHOLD_PER_DEVICE), text)
        self.assertIn(str(config.AMOUNT_THRESHOLD_PER_DEVICE), text)
        self.assertIn("50%", text)
        self.assertTrue(text.startswith('"SBI POS Machine Activation Status'))
        self.assertTrue(text.endswith(']"'))
        self.assertEqual(text.count("\n"), 1)  # exactly two lines


if __name__ == "__main__":
    unittest.main()
