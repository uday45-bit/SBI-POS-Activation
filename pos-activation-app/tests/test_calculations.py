"""
Unit tests for src/calculations.py — activation status, device-adjusted
thresholds, and the Group 1/2/3 sort ordering.

Run with:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.calculations import (
    assign_activation_status, assign_sort_group, compute_combined_and_thresholds,
    sort_for_display,
)
from src import config


def _office(office_id, office_name, devices_supplied, card_count=0, card_amount=0,
            qr_count=0, qr_amount=0):
    return {
        "office_id": office_id, "office_name": office_name, "devices_supplied": devices_supplied,
        "sbipos_card_count": card_count, "sbipos_card_amount": card_amount,
        "sbipos_bharatqr_count": qr_count, "sbipos_bharatqr_amount": qr_amount,
    }


def pipeline(rows):
    df = pd.DataFrame(rows)
    df = compute_combined_and_thresholds(df)
    df = assign_activation_status(df)
    df = assign_sort_group(df)
    return df


class TestThresholds(unittest.TestCase):
    def test_single_device_thresholds(self):
        df = pipeline([_office("1", "A", 1)])
        self.assertEqual(df.loc[0, "count_threshold"], 10)
        self.assertEqual(df.loc[0, "amount_threshold"], 5000)

    def test_multi_device_thresholds(self):
        # Case 2 (spec section 28): office with 4 devices -> thresholds scale.
        df = pipeline([_office("1", "A", 4)])
        self.assertEqual(df.loc[0, "count_threshold"], 40)
        self.assertEqual(df.loc[0, "amount_threshold"], 20000)


class TestActivationStatus(unittest.TestCase):
    def test_zero_transactions_is_red(self):
        # Case 3
        df = pipeline([_office("1", "A", 1, card_count=0, card_amount=0)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_RED)

    def test_count_only_orange_when_below_threshold(self):
        # Case 4: 5 transactions, Rs 0 -> ORANGE (below threshold of 10)
        df = pipeline([_office("1", "A", 1, card_count=5)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_ORANGE)

    def test_amount_only_green(self):
        # Case 5: 0 transactions but Rs 6000 with 1 device -> GREEN
        df = pipeline([_office("1", "A", 1, card_amount=6000)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_count_only_green_at_exact_threshold(self):
        # Uses >=, not >
        df = pipeline([_office("1", "A", 1, card_count=10)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_amount_only_green_at_exact_threshold(self):
        df = pipeline([_office("1", "A", 1, card_amount=5000)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_two_device_office_15_transactions_is_orange(self):
        # Case 6: 15 transactions, 2 devices -> threshold 20 -> ORANGE (amount below 10000)
        df = pipeline([_office("1", "A", 2, card_count=15, card_amount=0)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_ORANGE)

    def test_two_device_office_20_transactions_is_green(self):
        # Case 7
        df = pipeline([_office("1", "A", 2, card_count=20)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_two_device_office_low_count_high_amount_is_green(self):
        # Case 8: 5 transactions, 2 devices, Rs 10,000 -> GREEN via amount
        df = pipeline([_office("1", "A", 2, card_count=5, card_amount=10000)])
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_combined_card_and_qr_count(self):
        df = pipeline([_office("1", "A", 1, card_count=4, qr_count=6)])  # combined = 10 -> GREEN
        self.assertEqual(df.loc[0, "combined_count"], 10)
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)

    def test_combined_card_and_qr_amount(self):
        df = pipeline([_office("1", "A", 1, card_amount=2000, qr_amount=3000)])  # combined = 5000
        self.assertEqual(df.loc[0, "combined_amount"], 5000)
        self.assertEqual(df.loc[0, "activation_status"], config.STATUS_GREEN)


class TestSortGroupAndOrdering(unittest.TestCase):
    def test_group_assignment(self):
        df = pipeline([
            _office("1", "CountWinner", 1, card_count=10),    # Group 1 (count>=10)
            _office("2", "AmountWinner", 1, card_amount=5000),  # Group 1 too! (amount also could trigger count>=10 check first)
            _office("3", "NeitherOffice", 1),                  # Group 3
        ])
        # office 2 has count=0 (<10) but amount>=5000 -> Group 2
        g = dict(zip(df["office_id"], df["sort_group"]))
        self.assertEqual(g["1"], 1)
        self.assertEqual(g["2"], 2)
        self.assertEqual(g["3"], 3)

    def test_group1_office_not_moved_to_group2_even_if_amount_also_qualifies(self):
        df = pipeline([_office("1", "Both", 1, card_count=10, card_amount=5000)])
        self.assertEqual(df.loc[0, "sort_group"], 1)

    def test_sort_ordering_group_then_count_then_amount_then_name(self):
        rows = [
            _office("3", "Zeta", 1, card_count=0, card_amount=0),       # group 3
            _office("2", "Beta", 1, card_count=0, card_amount=6000),    # group 2
            _office("1a", "Alpha", 1, card_count=15, card_amount=0),    # group 1, count 15
            _office("1b", "Bravo", 1, card_count=20, card_amount=0),    # group 1, count 20 (higher)
        ]
        df = pipeline(rows)
        df = sort_for_display(df)
        self.assertEqual(list(df["office_id"]), ["1b", "1a", "2", "3"])

    def test_tie_break_by_office_name_ascending(self):
        rows = [
            _office("1", "Zebra", 1, card_count=20),
            _office("2", "Alpha", 1, card_count=20),
        ]
        df = pipeline(rows)
        df = sort_for_display(df)
        self.assertEqual(list(df["office_name"]), ["Alpha", "Zebra"])


if __name__ == "__main__":
    unittest.main()
