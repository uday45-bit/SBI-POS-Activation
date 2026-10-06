"""
Regression tests for src/reference_builder.py.

Covers:
    * Office Master loading — both the original .xlsx layout and the newer
      flat CSV layout (with or without a 'division-office-id' column).
    * Device Supply loading and cleaning — exact-duplicate-row removal,
      confirmed TID-conflict resolution (mirrors the real 30660758 vs
      30660784 case for TID 21530944), and orphaned-device detection for
      Office IDs no longer present in the Master (mirrors the real 30680009
      case for TID 21573521).

Run with:  python -m unittest discover -s tests -v
"""

import csv
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reference_builder import (
    build_device_supply, load_office_master, CONFIRMED_TID_OFFICE,
)


FIXTURE_DIR = Path(__file__).resolve().parent / "_fixtures"


def write_master_csv(path, rows, with_division_office_id=True):
    header = (
        ["division-office-id", "division-office-name", "office-id", "office-name", "office-type-code"]
        if with_division_office_id
        else ["division-office-name", "office-id", "office-name", "office-type-code"]
    )
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row if with_division_office_id else row[1:])


class TestCsvMasterLoading(unittest.TestCase):
    def setUp(self):
        FIXTURE_DIR.mkdir(exist_ok=True)
        self.path = FIXTURE_DIR / "test_master.csv"

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_loads_new_five_column_format(self):
        write_master_csv(self.path, [
            ["30530017", "Secunderabad Division", "1001", "Alpha SO", "SPO"],
            ["30530015", "Medak Division", "1002", "Beta SO", "SPO"],
        ])
        df = load_office_master(self.path)
        self.assertEqual(len(df), 2)
        row = df[df.office_id == "1001"].iloc[0]
        self.assertEqual(row["office_name"], "Alpha SO")
        self.assertEqual(row["division"], "Secunderabad")
        self.assertEqual(row["division_office_id"], "30530017")

    def test_loads_legacy_four_column_format(self):
        write_master_csv(self.path, [
            ["", "Secunderabad Division", "1001", "Alpha SO", "SPO"],
        ], with_division_office_id=False)
        df = load_office_master(self.path)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["office_name"], "Alpha SO")

    def test_duplicate_office_id_raises(self):
        write_master_csv(self.path, [
            ["30530017", "Secunderabad Division", "1001", "Alpha SO", "SPO"],
            ["30530017", "Secunderabad Division", "1001", "Alpha SO Duplicate", "SPO"],
        ])
        with self.assertRaises(ValueError):
            load_office_master(self.path)

    def test_office_id_with_asterisk_is_cleaned(self):
        write_master_csv(self.path, [
            ["30530017", "Secunderabad Division", "1001*", "Alpha SO", "SPO"],
        ])
        df = load_office_master(self.path)
        self.assertEqual(df.iloc[0]["office_id"], "1001")


def make_master(rows):
    """rows: list of (office_id, office_name, division)"""
    return pd.DataFrame([
        {"office_id": oid, "office_name": name, "division": div, "office_type_code": "SPO"}
        for oid, name, div in rows
    ])


def make_device_raw(rows):
    """rows: list of (division_raw, office_id, office_name, tid)"""
    return pd.DataFrame([
        {"division_raw": div, "office_id": oid, "office_name": name, "tid": tid}
        for div, oid, name, tid in rows
    ])


class TestBuildDeviceSupply(unittest.TestCase):
    def test_simple_single_device_per_office(self):
        master = make_master([("1001", "Alpha SO", "Secunderabad")])
        raw = make_device_raw([("Secunderabad Division", "1001", "Alpha SO", "TID1")])
        supply, diag = build_device_supply(raw, master)
        self.assertEqual(len(supply), 1)
        self.assertEqual(supply.iloc[0]["devices_supplied"], 1)
        self.assertEqual(diag["exact_duplicate_rows_removed"], 0)

    def test_exact_duplicate_row_removed(self):
        master = make_master([("1001", "Alpha SO", "Secunderabad")])
        raw = make_device_raw([
            ("Secunderabad Division", "1001", "Alpha SO", "TID1"),
            ("Secunderebad ", "1001", "Alpha SO", "TID1"),  # same office+TID, messy division label
        ])
        supply, diag = build_device_supply(raw, master)
        self.assertEqual(supply.iloc[0]["devices_supplied"], 1)
        self.assertEqual(diag["exact_duplicate_rows_removed"], 1)

    def test_multi_device_office_counted_correctly(self):
        master = make_master([("1001", "Alpha SO", "Secunderabad")])
        raw = make_device_raw([
            ("Secunderabad Division", "1001", "Alpha SO", "TID1"),
            ("Secunderabad Division", "1001", "Alpha SO", "TID2"),
        ])
        supply, diag = build_device_supply(raw, master)
        self.assertEqual(supply.iloc[0]["devices_supplied"], 2)

    def test_confirmed_tid_conflict_resolved_correctly(self):
        # Mirrors the real case: TID 21530944 assigned to both Digwal S.O.
        # (30660758) and Ranjhole S.O. (30660784); confirmed correct owner
        # is 30660784. Digwal keeps its OTHER, undisputed device.
        self.assertIn("21530944", CONFIRMED_TID_OFFICE)  # sanity-check the real rule is present
        master = make_master([
            ("30660758", "Digwal S.O", "Sangareddy"),
            ("30660784", "Ranjhole S.O", "Sangareddy"),
        ])
        raw = make_device_raw([
            ("Sangareddy Division", "30660758", "Digwal S.O", "21530944"),
            ("Sangareddy Division", "30660758", "Digwal S.O", "21569660"),  # Digwal's own device
            ("Sangareddy", "30660784", "Ranjhole S.O", "21530944"),
        ])
        supply, diag = build_device_supply(raw, master)
        digwal = supply[supply.office_id == "30660758"].iloc[0]
        ranjhole = supply[supply.office_id == "30660784"].iloc[0]
        self.assertEqual(digwal["devices_supplied"], 1)  # kept its own device, lost the disputed one
        self.assertEqual(ranjhole["devices_supplied"], 1)  # got the disputed device
        self.assertEqual(len(diag["tid_conflicts_resolved"]), 1)
        self.assertEqual(diag["tid_conflicts_resolved"][0]["kept_office_id"], "30660784")
        self.assertEqual(diag["unresolved_tid_conflicts"], [])

    def test_unresolved_tid_conflict_is_flagged_not_guessed(self):
        master = make_master([
            ("2001", "Office A", "Medak"),
            ("2002", "Office B", "Medak"),
        ])
        raw = make_device_raw([
            ("Medak Division", "2001", "Office A", "NEWTID"),
            ("Medak Division", "2002", "Office B", "NEWTID"),  # same TID, no confirmed rule
        ])
        supply, diag = build_device_supply(raw, master)
        self.assertEqual(len(diag["unresolved_tid_conflicts"]), 1)
        self.assertEqual(diag["unresolved_tid_conflicts"][0]["tid"], "NEWTID")

    def test_orphaned_device_office_not_in_master_excluded(self):
        # Mirrors the real 30680009 case: device evidence exists, but the
        # office isn't in the current Master -> excluded, not invented.
        master = make_master([("1001", "Alpha SO", "Secunderabad")])
        raw = make_device_raw([
            ("Secunderabad Division", "1001", "Alpha SO", "TID1"),
            ("Hyderabad Sorting Division", "30680009", "Secunderabad RSTMO", "21573521"),
        ])
        supply, diag = build_device_supply(raw, master)
        self.assertNotIn("30680009", set(supply["office_id"]))
        self.assertEqual(diag["orphaned_device_ids"], ["30680009"])
        self.assertEqual(diag["orphaned_device_count"], 1)

    def test_office_with_no_device_row_absent_from_supply(self):
        master = make_master([
            ("1001", "Alpha SO", "Secunderabad"),
            ("1002", "Beta SO", "Secunderabad"),  # no device row at all
        ])
        raw = make_device_raw([("Secunderabad Division", "1001", "Alpha SO", "TID1")])
        supply, diag = build_device_supply(raw, master)
        self.assertEqual(set(supply["office_id"]), {"1001"})


if __name__ == "__main__":
    unittest.main()
