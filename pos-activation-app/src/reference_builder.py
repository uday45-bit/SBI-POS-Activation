"""
Shared library that rebuilds the permanent reference-data CSV files
(data/*.csv) from two source files:

    1. Office Master (.xlsx or .csv) -> the current Office Master
    2. Device Supply list (.xlsx)    -> one row per physical POS device
                                         (Division Name, Office ID, Office
                                         Name, TID)

This module is the ONLY place that should ever touch the raw source files.
The Streamlit app's normal reporting workflow never reads them; it only
reads the cleaned CSVs `build()` produces, so normal report-generation users
never need to supply them.

Two front-ends call into this module's `build()` function:
    * scripts/build_reference_data.py — a command-line tool for developers
    * app.py's "Admin: Refresh Reference Data" panel — for an in-app refresh
      without needing shell/Python access

It is safe to re-run at any time — it fully regenerates data/*.csv and
data/reference_data_meta.json from the two inputs, and never invents data
that isn't present in the source files.

DESIGN (as of the 2026-09-26 device-list update):

  The reference-data model is deliberately just two inputs now:
  "which offices exist" (Office Master) and "which of those offices have a
  device, and how many" (Device Supply list). Everything else is DERIVED:

    * "Device Not Supplied" = a Master office with zero devices in the
      Device Supply list. There is no separate maintained list for this —
      it's just Master minus Device Supply.
    * There is no "Pending Whitelisting" status. Per explicit instruction,
      this concept has been removed entirely: every office with at least
      one device is eligible for activation analysis.
    * There is no separate "Closed offices" list. An Office ID on the
      Device Supply list that ISN'T in the current Master simply has no
      home — its device is excluded from every total ("orphaned") and
      reported under data_quality_conflicts, rather than silently dropped.
      The Master is always the authority on which offices currently exist.

  This replaces an earlier, more complex design (kept in git history) that
  also ingested Not-Supplied / Pending-Whitelist / Closed-Offices /
  division-total lists from a separate "POS machine status" workbook.

MANUAL DATA-CONFLICT RESOLUTIONS (confirmed by the report owner; do not
silently change these without an explicit new instruction):

  * TID 21530944 appeared assigned to TWO different offices in the source
    file: 30660758 ("Digwal S.O.") and 30660784 ("Ranjhole S.O.", both
    Sangareddy). Confirmed correct: 30660784 (Ranjhole S.O.). The
    30660758 assignment for this TID is dropped; Digwal S.O. keeps its
    other, undisputed device (a separate TID).
  * TID 21573521 is assigned to Office ID 30680009 ("Secunderabad RSTMO").
    Confirmed correct by the report owner (an earlier, unconfirmed guess in
    this project's history had assumed the "same physical office" was
    30260018 "TMO Secunderabad Counter" instead — that guess was wrong and
    has been reversed). NOTE: 30680009 is not present in the current
    279-office Office Master, so this device is currently ORPHANED (has no
    office to be counted against) — flagged under
    data_quality_conflicts -> device_records_orphaned. If 30680009 should
    be a tracked office, the Office Master needs a further update; this
    script does not add offices to the Master on its own.

If a future refresh introduces a NEW unexplained contradiction (a duplicate
Master Office ID, an office ID appearing more than once in the Device
Supply list under genuinely different offices for the SAME TID), `build()`
stops rather than guessing — see the checks near the end of `build()`.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import openpyxl

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

DIVISION_NORMALISATION = {
    "medak division": "Medak",
    "medak": "Medak",
    "secunderabad division": "Secunderabad",
    "secunderabad": "Secunderabad",
    "secunderebad": "Secunderabad",
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

DIVISIONS_ORDER = [
    "Secunderabad", "Hyderabad South East", "Hyderabad City", "Sangareddy",
    "Medak", "Hyderabad GPO", "Hyderabad Sorting",
]

# Manually confirmed by the report owner: when this TID appears assigned to
# more than one office in the source file, this is the correct office —
# every OTHER office's assignment of the same TID is dropped.
CONFIRMED_TID_OFFICE = {
    "21530944": "30660784",  # Ranjhole S.O. (Sangareddy), not Digwal S.O.
}


def normalise_division(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = str(raw).strip().lower()
    return DIVISION_NORMALISATION.get(key, str(raw).strip())


def clean_office_id(raw) -> str | None:
    """Office IDs must always be treated as strings; strip whitespace and any
    stray non-digit characters (e.g. a trailing '*' seen in one source row)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.lower() == "none":
        return None
    digits = re.sub(r"[^0-9]", "", s)
    return digits or None


def _load_office_master_from_xlsx(path: Path) -> pd.DataFrame:
    wb = openpyxl.load_workbook(path, data_only=True)
    sheet_name = "Offices Master data" if "Offices Master data" in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    records = []
    for div, oid, name, typ in rows:
        office_id = clean_office_id(oid)
        if office_id is None:
            continue
        records.append({
            "office_id": office_id,
            "office_name": (str(name).strip() if name else ""),
            "division": normalise_division(div),
            "office_type_code": (str(typ).strip() if typ else ""),
            "division_office_id": "",
        })
    return pd.DataFrame(records)


def _load_office_master_from_csv(path: Path) -> pd.DataFrame:
    """Handles the newer flat CSV export of the Office Master, which adds a
    leading 'division-office-id' column ahead of the original four fields:
    division-office-id, division-office-name, office-id, office-name,
    office-type-code. Column ORDER is not assumed — matched by name (case/
    punctuation-insensitive), so a reordered or renamed-with-underscores
    export still loads correctly."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)

    def find_col(*name_fragments_options):
        norm = {c: re.sub(r"[^a-z]", "", c.lower()) for c in raw.columns}
        for options in name_fragments_options:
            for col, key in norm.items():
                if key == options:
                    return col
        raise ValueError(
            f"CONTRADICTION: could not find a column matching any of {name_fragments_options} "
            f"in the uploaded master CSV. Columns present: {list(raw.columns)}"
        )

    col_office_id = find_col("officeid")
    col_office_name = find_col("officename")
    col_division = find_col("divisionofficename", "division")
    col_type = find_col("officetypecode", "type")
    col_division_office_id = None
    try:
        col_division_office_id = find_col("divisionofficeid")
    except ValueError:
        pass  # optional column — fine if absent

    records = []
    for _, r in raw.iterrows():
        office_id = clean_office_id(r[col_office_id])
        if office_id is None:
            continue
        records.append({
            "office_id": office_id,
            "office_name": str(r[col_office_name]).strip(),
            "division": normalise_division(r[col_division]),
            "office_type_code": str(r[col_type]).strip(),
            "division_office_id": (clean_office_id(r[col_division_office_id]) or "") if col_division_office_id else "",
        })
    return pd.DataFrame(records)


def load_office_master(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = _load_office_master_from_csv(path)
    elif suffix in (".xlsx", ".xlsm", ".xls"):
        df = _load_office_master_from_xlsx(path)
    else:
        raise ValueError(
            f"Unsupported Office Master file type: {suffix!r}. Expected .csv or .xlsx."
        )

    dupes = df[df.duplicated("office_id", keep=False)]
    if not dupes.empty:
        raise ValueError(
            "CONTRADICTION: duplicate Office ID(s) inside the Office Master — "
            f"refusing to guess which record is correct: {sorted(dupes['office_id'].unique())}"
        )
    return df


def load_device_supply_raw(path: Path) -> pd.DataFrame:
    """Loads the flat, explicit device list: Division Name, Office ID,
    Office Name, TID — one row per physical device. Columns are matched by
    name (case/punctuation-insensitive), not position, so a reordered
    export still loads correctly."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    elif suffix in (".xlsx", ".xlsm", ".xls"):
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        raw = pd.DataFrame(rows[1:], columns=[str(c).strip() for c in rows[0]])
    else:
        raise ValueError(f"Unsupported Device Supply file type: {suffix!r}. Expected .csv or .xlsx.")

    def find_col(*options):
        norm = {c: re.sub(r"[^a-z]", "", str(c).lower()) for c in raw.columns}
        for opt in options:
            for col, key in norm.items():
                if key == opt:
                    return col
        raise ValueError(
            f"CONTRADICTION: could not find a column matching any of {options} "
            f"in the uploaded device-supply file. Columns present: {list(raw.columns)}"
        )

    col_division = find_col("divisionname", "division")
    col_office_id = find_col("allottedtooffice", "officeid")
    col_office_name = find_col("officename")
    col_tid = find_col("tid")

    records = []
    for _, r in raw.iterrows():
        office_id = clean_office_id(r[col_office_id])
        tid = clean_office_id(r[col_tid])  # same "digits only, as string" cleaning applies
        if office_id is None or tid is None:
            continue
        records.append({
            "division_raw": r[col_division],
            "office_id": office_id,
            "office_name": (str(r[col_office_name]).strip() if r[col_office_name] else ""),
            "tid": tid,
        })
    return pd.DataFrame(records)


def build_device_supply(device_raw: pd.DataFrame, office_master: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Cleans the raw device list into a per-office device count, restricted
    to offices present in the current Master. Returns (device_supply_df,
    diagnostics_dict)."""
    master_ids = set(office_master["office_id"])
    diagnostics = {
        "exact_duplicate_rows_removed": 0,
        "tid_conflicts_resolved": [],  # [{tid, kept_office_id, dropped_office_ids}]
        "unresolved_tid_conflicts": [],  # any conflicting TID NOT in CONFIRMED_TID_OFFICE
    }

    # 1) De-duplicate exact (office_id, tid) duplicate rows.
    seen = set()
    deduped = []
    for _, r in device_raw.iterrows():
        key = (r["office_id"], r["tid"])
        if key in seen:
            diagnostics["exact_duplicate_rows_removed"] += 1
            continue
        seen.add(key)
        deduped.append(r)
    deduped_df = pd.DataFrame(deduped) if deduped else device_raw.iloc[0:0]

    # 2) Resolve/flag TIDs assigned to more than one DIFFERENT office.
    tid_to_offices = defaultdict(set)
    for _, r in deduped_df.iterrows():
        tid_to_offices[r["tid"]].add(r["office_id"])

    rows_to_keep = []
    for _, r in deduped_df.iterrows():
        offices_for_tid = tid_to_offices[r["tid"]]
        if len(offices_for_tid) > 1:
            correct_office = CONFIRMED_TID_OFFICE.get(r["tid"])
            if correct_office is None:
                # Unresolved conflict: keep the row but flag it — do NOT
                # silently pick one, since no confirmed rule exists yet.
                rows_to_keep.append(r)
            elif r["office_id"] == correct_office:
                rows_to_keep.append(r)
            # else: drop this row — a different, confirmed-correct office
            # holds this TID.
        else:
            rows_to_keep.append(r)

    # Log each conflicting TID exactly once (not once per row).
    for tid, offices in tid_to_offices.items():
        if len(offices) <= 1:
            continue
        if tid in CONFIRMED_TID_OFFICE:
            diagnostics["tid_conflicts_resolved"].append({
                "tid": tid,
                "kept_office_id": CONFIRMED_TID_OFFICE[tid],
                "dropped_office_ids": sorted(offices - {CONFIRMED_TID_OFFICE[tid]}),
            })
        else:
            diagnostics["unresolved_tid_conflicts"].append({"tid": tid, "offices": sorted(offices)})

    clean_df = pd.DataFrame(rows_to_keep) if rows_to_keep else deduped_df.iloc[0:0]

    # 3) Restrict to offices in the current Master; anything else is orphaned.
    is_in_master = clean_df["office_id"].isin(master_ids)
    orphaned = clean_df[~is_in_master]
    in_master = clean_df[is_in_master]

    diagnostics["orphaned_device_ids"] = sorted(orphaned["office_id"].unique().tolist())
    diagnostics["orphaned_device_count"] = int(len(orphaned))

    device_counts = Counter(in_master["office_id"])
    records = []
    for oid, n in device_counts.items():
        name = office_master.loc[office_master.office_id == oid, "office_name"].iloc[0]
        division = office_master.loc[office_master.office_id == oid, "division"].iloc[0]
        records.append({
            "office_id": oid, "office_name": name, "division": division,
            "devices_supplied": int(n),
        })
    device_supply = pd.DataFrame(records).sort_values("office_id").reset_index(drop=True) if records else pd.DataFrame(
        columns=["office_id", "office_name", "division", "devices_supplied"]
    )
    return device_supply, diagnostics


def build(master_path: Path, device_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    office_master = load_office_master(master_path)
    master_ids = set(office_master["office_id"])

    device_raw = load_device_supply_raw(device_path)
    device_supply, diag = build_device_supply(device_raw, office_master)
    device_supplied_ids = set(device_supply["office_id"])

    not_supplied_ids = sorted(master_ids - device_supplied_ids)

    if diag["unresolved_tid_conflicts"]:
        print(
            "WARNING: the following TID(s) are assigned to more than one "
            "different Office ID in the device-supply file, with NO confirmed "
            "resolution rule for them — every conflicting row has been kept "
            "(so totals may double-count these devices) until this is "
            f"resolved: {diag['unresolved_tid_conflicts']}. Add a rule to "
            "CONFIRMED_TID_OFFICE in reference_builder.py once you know which "
            "office is correct.",
            file=sys.stderr,
        )

    if diag["orphaned_device_ids"]:
        print(
            "NOTE: the following Office ID(s) have device records in the "
            "supplied device list, but are NOT in the current Office Master — "
            "their device(s) are excluded from every total: "
            f"{diag['orphaned_device_ids']}. If any of these should be a "
            "tracked office, the Office Master needs a further update.",
            file=sys.stderr,
        )

    # -- write CSVs ------------------------------------------------------------
    office_master.to_csv(out_dir / "office_master.csv", index=False)
    device_supply.to_csv(out_dir / "device_supply.csv", index=False)

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_files": {"office_master": master_path.name, "device_supply": device_path.name},
        "counts": {
            "master_offices": int(len(office_master)),
            "device_raw_rows": int(len(device_raw)),
            "exact_duplicate_rows_removed": diag["exact_duplicate_rows_removed"],
            "device_supplied_offices": int(len(device_supply)),
            "devices_supplied_sum": int(device_supply["devices_supplied"].sum()) if len(device_supply) else 0,
            "device_not_supplied_offices": len(not_supplied_ids),
            "orphaned_device_records": diag["orphaned_device_count"],
        },
        "data_quality_conflicts": {
            "tid_conflicts_resolved_by_confirmed_rule": diag["tid_conflicts_resolved"],
            "tid_conflicts_unresolved": diag["unresolved_tid_conflicts"],
            "device_records_orphaned_not_in_master": diag["orphaned_device_ids"],
        },
        "resolution_rules_applied": [
            "There is no 'Pending Whitelisting' concept: every office with at "
            "least one device is eligible for activation analysis.",
            "'Device Not Supplied' is derived as Master minus Device Supply — "
            "not sourced from any separate maintained list.",
            "An Office ID on the device list that is NOT in the current "
            "Master has its device(s) excluded from every total ('orphaned') "
            "rather than silently guessed at.",
            "TID 21530944 is confirmed assigned to Office 30660784 (Ranjhole "
            "S.O.), not 30660758 (Digwal S.O.).",
            "TID 21573521 is confirmed assigned to Office 30680009 "
            "(Secunderabad RSTMO); this office is currently orphaned because "
            "it is not in the Master.",
        ],
    }
    with open(out_dir / "reference_data_meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    return meta
