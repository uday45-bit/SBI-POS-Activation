"""
Loads the permanent reference data (data/*.csv, produced by
scripts/build_reference_data.py) and builds the single "office universe"
table that every other part of the app works from.

This module is the ONLY place that decides, for every Master office,
whether it's eligible for activation analysis:

    Working / Eligible  (has at least one device)
    Device Not Supplied (no device on record)

Keeping that decision in one function means the Streamlit UI, the Excel
export, and any future report type all see exactly the same classification.

There is no "Pending Whitelisting" or "Closed Office" status — see the
design note at the top of src/reference_builder.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from . import config


def _read_csv_str_office_id(path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"office_id": str})
    df["office_id"] = df["office_id"].astype(str).str.strip()
    return df


@dataclass
class ReferenceData:
    office_master: pd.DataFrame
    device_supply: pd.DataFrame
    meta: dict

    @property
    def device_supply_map(self) -> dict:
        return dict(zip(self.device_supply["office_id"], self.device_supply["devices_supplied"]))


def reference_files_present() -> bool:
    required = [config.OFFICE_MASTER_CSV, config.DEVICE_SUPPLY_CSV]
    return all(p.exists() for p in required)


def load_reference_data() -> ReferenceData:
    if not reference_files_present():
        missing = [
            str(p) for p in [config.OFFICE_MASTER_CSV, config.DEVICE_SUPPLY_CSV] if not p.exists()
        ]
        raise FileNotFoundError(
            "Reference data is missing. Run "
            "'python scripts/build_reference_data.py --master ... --devices ...' first. "
            f"Missing file(s): {missing}"
        )

    office_master = _read_csv_str_office_id(config.OFFICE_MASTER_CSV)
    device_supply = _read_csv_str_office_id(config.DEVICE_SUPPLY_CSV)

    meta = {}
    if config.REFERENCE_META_JSON.exists():
        with open(config.REFERENCE_META_JSON) as f:
            meta = json.load(f)

    return ReferenceData(office_master=office_master, device_supply=device_supply, meta=meta)


def build_office_universe(ref: ReferenceData) -> pd.DataFrame:
    """Return one row per Master office with its device count and its
    classification. This is the authoritative table every downstream
    calculation (eligibility, consolidation, division sheets) is built from."""
    df = ref.office_master.copy()
    df = df.merge(
        ref.device_supply[["office_id", "devices_supplied"]],
        on="office_id", how="left",
    )
    df["devices_supplied"] = df["devices_supplied"].fillna(0).astype(int)
    df["device_status"] = df["devices_supplied"].apply(
        lambda n: config.DEVICE_STATUS_WORKING_ELIGIBLE if n > 0 else config.DEVICE_STATUS_NOT_SUPPLIED
    )
    return df


def eligible_offices(universe: pd.DataFrame) -> pd.DataFrame:
    """Offices eligible for activation analysis: at least one device supplied."""
    return universe[universe["device_status"] == config.DEVICE_STATUS_WORKING_ELIGIBLE].copy()
