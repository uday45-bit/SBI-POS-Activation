"""
Unit tests for src/data_loader.py and src/validation.py — Office ID matching
against the Master, and the (now 2-state) device_status classification:
Working / Eligible vs Device Not Supplied. There is no Pending or Closed
status in this model — see src/reference_builder.py's design note.

Run with:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_loader import ReferenceData, build_office_universe, eligible_offices
from src.validation import match_raw_to_universe
from src import config


def make_reference_data():
    office_master = pd.DataFrame([
        {"office_id": "1001", "office_name": "Alpha SO", "division": "Secunderabad", "office_type_code": "SPO"},
        {"office_id": "1002", "office_name": "Beta SO", "division": "Secunderabad", "office_type_code": "SPO"},
        {"office_id": "1003", "office_name": "Gamma SO", "division": "Medak", "office_type_code": "SPO"},
        {"office_id": "1004", "office_name": "Delta SO", "division": "Medak", "office_type_code": "SPO"},
        {"office_id": "1005", "office_name": "Epsilon SO", "division": "Sangareddy", "office_type_code": "SPO"},
    ])
    device_supply = pd.DataFrame([
        {"office_id": "1001", "office_name": "Alpha SO", "division": "Secunderabad", "devices_supplied": 1},
        {"office_id": "1002", "office_name": "Beta SO", "division": "Secunderabad", "devices_supplied": 3},
        # 1003, 1004, 1005: no device record -> Device Not Supplied
    ])
    return ReferenceData(office_master=office_master, device_supply=device_supply, meta={})


class TestOfficeUniverseClassification(unittest.TestCase):
    def setUp(self):
        self.ref = make_reference_data()
        self.universe = build_office_universe(self.ref)

    def test_universe_has_one_row_per_master_office(self):
        self.assertEqual(len(self.universe), 5)

    def test_working_eligible_office(self):
        row = self.universe[self.universe.office_id == "1001"].iloc[0]
        self.assertEqual(row["device_status"], config.DEVICE_STATUS_WORKING_ELIGIBLE)
        self.assertEqual(row["devices_supplied"], 1)

    def test_multi_device_office(self):
        row = self.universe[self.universe.office_id == "1002"].iloc[0]
        self.assertEqual(row["devices_supplied"], 3)
        self.assertEqual(row["device_status"], config.DEVICE_STATUS_WORKING_ELIGIBLE)

    def test_device_not_supplied_office_with_no_record(self):
        row = self.universe[self.universe.office_id == "1004"].iloc[0]
        self.assertEqual(row["device_status"], config.DEVICE_STATUS_NOT_SUPPLIED)
        self.assertEqual(row["devices_supplied"], 0)

    def test_no_pending_or_closed_status_exists(self):
        # There is no third/fourth status in this model.
        statuses = set(self.universe["device_status"].unique())
        self.assertEqual(statuses, {config.DEVICE_STATUS_WORKING_ELIGIBLE, config.DEVICE_STATUS_NOT_SUPPLIED})

    def test_eligible_offices_excludes_not_supplied(self):
        eligible = eligible_offices(self.universe)
        self.assertEqual(set(eligible["office_id"]), {"1001", "1002"})


class TestRawOfficeIdMatching(unittest.TestCase):
    def setUp(self):
        self.ref = make_reference_data()
        self.universe = build_office_universe(self.ref)

    def test_matched_offices(self):
        raw = pd.DataFrame([
            {"office_id": "1001", "sbipos_card_count": 12, "sbipos_card_amount": 0,
             "sbipos_bharatqr_count": 0, "sbipos_bharatqr_amount": 0},
        ])
        result = match_raw_to_universe(raw, self.universe)
        self.assertEqual(len(result.matched), 1)
        self.assertEqual(len(result.unmatched), 0)

    def test_unmatched_office_id(self):
        # Case 11 (spec section 28): Office ID not found in Master.
        raw = pd.DataFrame([
            {"office_id": "9999999", "sbipos_card_count": 5, "sbipos_card_amount": 0,
             "sbipos_bharatqr_count": 0, "sbipos_bharatqr_amount": 0},
        ])
        result = match_raw_to_universe(raw, self.universe)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)
        self.assertEqual(result.unmatched.iloc[0]["office_id"], "9999999")

    def test_office_not_in_master_is_unmatched_even_with_real_device_evidence(self):
        # Mirrors the real 30680009 situation: an office with device evidence
        # that isn't in the current Master is simply unmatched, not special-cased.
        raw = pd.DataFrame([
            {"office_id": "30680009", "sbipos_card_count": 3, "sbipos_card_amount": 0,
             "sbipos_bharatqr_count": 0, "sbipos_bharatqr_amount": 0},
        ])
        result = match_raw_to_universe(raw, self.universe)
        self.assertEqual(len(result.matched), 0)
        self.assertEqual(len(result.unmatched), 1)

    def test_office_id_treated_as_string_preserves_leading_zero(self):
        ref = make_reference_data()
        ref.office_master.loc[len(ref.office_master)] = {
            "office_id": "0007", "office_name": "Leading Zero SO", "division": "Medak", "office_type_code": "SPO",
        }
        universe = build_office_universe(ref)
        self.assertIn("0007", set(universe["office_id"]))
        self.assertTrue(all(isinstance(x, str) for x in universe["office_id"]))


if __name__ == "__main__":
    unittest.main()
