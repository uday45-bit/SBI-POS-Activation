"""
POS Activation Status Analysis — Streamlit application.

Normal end-user workflow (see README.md for full details):
    1. Open the app.
    2. Select the Reporting Date.
    3. Upload the raw transaction file (CSV or Excel).
    4. Click "Generate Activation Report".
    5. Review the division-wise consolidation.
    6. Download the formatted Excel workbook.

Reference data (Office Master + Device Supply) is loaded from data/*.csv —
committed to the repository — so the normal user never has to upload it. An
"Admin: Refresh Reference Data" panel in the sidebar lets an administrator
regenerate those files if the Office Master or device list changes in the
future.

There is no "Pending Whitelisting" or "Closed Office" status in this
version: an office is either device-supplied (eligible for activation
analysis) or not. See src/reference_builder.py for the full design note.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config
from src.calculations import run_full_calculation
from src.data_loader import (
    build_office_universe, eligible_offices,
    load_reference_data, reference_files_present,
)
from src.excel_export import build_workbook, workbook_to_bytes
from src.raw_transaction import load_raw_transaction_file
from src.report import build_consolidation, build_division_sheets
from src.reference_builder import build as rebuild_reference_data
from src.validation import match_raw_to_universe, validate_raw_load, validate_reconciliation


st.set_page_config(
    page_title="POS Activation Status Analysis",
    page_icon="\U0001F4B3",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def dot_date(d) -> str:
    """dd.mm.yyyy, as used in the report heading."""
    return d.strftime("%d.%m.%Y")


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def show_admin_panel():
    with st.sidebar.expander("\u2699\ufe0f Admin: Refresh Reference Data", expanded=False):
        st.caption(
            "Only needed when the Office Master or the device-supply list "
            "changes. Normal reporting does not require this step."
        )
        master_up = st.file_uploader("Office Master (.xlsx or .csv)", type=["xlsx", "csv"], key="admin_master")
        devices_up = st.file_uploader("Device Supply list (.xlsx or .csv)", type=["xlsx", "csv"], key="admin_devices")
        if st.button("Rebuild Reference Data", key="admin_rebuild"):
            if not master_up or not devices_up:
                st.error("Please upload both files before rebuilding.")
            else:
                tmp_dir = Path("/tmp/pos_admin_upload")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                master_path = tmp_dir / f"master{Path(master_up.name).suffix}"
                devices_path = tmp_dir / f"devices{Path(devices_up.name).suffix}"
                master_path.write_bytes(master_up.getvalue())
                devices_path.write_bytes(devices_up.getvalue())
                try:
                    meta = rebuild_reference_data(master_path, devices_path, config.DATA_DIR)
                    st.success("Reference data rebuilt successfully.")
                    st.json(meta)
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as e:
                    st.error(f"Stopped — data contradiction detected:\n\n{e}")

        if config.REFERENCE_META_JSON.exists():
            import json
            with open(config.REFERENCE_META_JSON) as f:
                meta = json.load(f)
            st.caption(f"Reference data last generated: {meta.get('generated_at_utc', 'unknown')}")
            conflicts = meta.get("data_quality_conflicts", {})
            n_conflicts = sum(len(v) for v in conflicts.values())
            if n_conflicts:
                st.caption(f"\u26a0\ufe0f {n_conflicts} known reference-data conflict(s) — see below.")


@st.cache_data(show_spinner=False)
def _cached_load_reference_data(_version_marker: float):
    ref = load_reference_data()
    universe = build_office_universe(ref)
    return ref, universe


def get_reference_data():
    mtime = config.REFERENCE_META_JSON.stat().st_mtime if config.REFERENCE_META_JSON.exists() else 0.0
    return _cached_load_reference_data(mtime)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("POS Activation Status Analysis")
st.caption("Division-wise POS Card + Bharat QR Activation Monitoring")

show_admin_panel()

if not reference_files_present():
    st.error(
        "Reference data has not been set up yet. Use the **Admin: Refresh Reference Data** "
        "panel in the sidebar to upload the Office Master and Device Supply list once, "
        "and the app will remember them from then on."
    )
    st.stop()

ref, universe = get_reference_data()

# ---------------------------------------------------------------------------
# Input section
# ---------------------------------------------------------------------------
st.subheader("1. Report Inputs")
col1, col2 = st.columns([1, 2])
with col1:
    report_date = st.date_input("Report Date", value=pd.Timestamp.today().date())
    period_start = month_start(report_date)
    st.caption(f"Period shown on the report: **{dot_date(period_start)} to {dot_date(report_date)}**")
with col2:
    uploaded_file = st.file_uploader(
        "Upload Raw Transaction File (CSV or Excel)",
        type=["csv", "xlsx", "xls"],
        help=(
            "Column A must be Office ID; Columns T, U, V, W must be SBI POS Card Count, "
            "SBI POS Card Amount, SBI POS Bharat QR Count, SBI POS Bharat QR Amount respectively."
        ),
    )

generate_clicked = st.button("Generate Activation Report", type="primary", disabled=uploaded_file is None)

if uploaded_file is None:
    st.info("Select a reporting date and upload the raw transaction file to begin.")
    st.stop()

if not generate_clicked and "last_result" not in st.session_state:
    st.stop()

# ---------------------------------------------------------------------------
# Load + clean the raw file
# ---------------------------------------------------------------------------
if generate_clicked:
    try:
        with st.spinner("Reading and cleaning the uploaded file..."):
            raw_result = load_raw_transaction_file(uploaded_file)
    except ValueError as e:
        st.error(f"Could not read the uploaded file: {e}")
        st.stop()

    load_validation = validate_raw_load(raw_result, universe)
    if load_validation.is_blocking:
        st.error("The file could not be processed:")
        for err in load_validation.errors:
            st.error(f"\u2022 {err}")
        st.stop()

    match_result = match_raw_to_universe(raw_result.cleaned, universe)

    calculated = run_full_calculation(eligible_offices(universe), match_result.matched)
    consolidation_df = build_consolidation(universe, calculated)
    division_sheets = build_division_sheets(calculated)

    devices_supplied_total = int(universe["devices_supplied"].sum())
    recon_validation = validate_reconciliation(consolidation_df, division_sheets, devices_supplied_total)

    workbook = build_workbook(consolidation_df, division_sheets, dot_date(period_start), dot_date(report_date))
    workbook_bytes = workbook_to_bytes(workbook)

    st.session_state["last_result"] = dict(
        raw_result=raw_result,
        load_validation=load_validation,
        match_result=match_result,
        calculated=calculated,
        consolidation_df=consolidation_df,
        division_sheets=division_sheets,
        recon_validation=recon_validation,
        workbook_bytes=workbook_bytes,
        report_date_filename=report_date.strftime("%Y%m%d"),
    )

state = st.session_state["last_result"]
raw_result = state["raw_result"]
load_validation = state["load_validation"]
match_result = state["match_result"]
calculated = state["calculated"]
consolidation_df = state["consolidation_df"]
division_sheets = state["division_sheets"]
recon_validation = state["recon_validation"]
workbook_bytes = state["workbook_bytes"]

if recon_validation.is_blocking:
    st.error("Internal reconciliation failed — report generation stopped to avoid showing an incorrect report.")
    for err in recon_validation.errors:
        st.error(f"\u2022 {err}")
    st.stop()

# ---------------------------------------------------------------------------
# Validation section
# ---------------------------------------------------------------------------
st.subheader("2. Validation Summary")

eligible_count = len(eligible_offices(universe))
not_supplied_count = int((universe["device_status"] == config.DEVICE_STATUS_NOT_SUPPLIED).sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Raw Rows Read", raw_result.raw_row_count)
m2.metric("Unique Office IDs (after cleaning)", len(raw_result.cleaned))
m3.metric("Matched Office IDs", len(match_result.matched))
m4.metric("Unmatched Office IDs", len(match_result.unmatched))

m5, m6 = st.columns(2)
m5.metric("Device-Supplied Eligible Offices", eligible_count)
m6.metric("Device-Not-Supplied Offices", not_supplied_count)

with st.expander("\U0001F3AF Matched transaction columns", expanded=False):
    st.caption("These four columns were located by searching the header row (not fixed position):")
    match_df = pd.DataFrame([
        {"Field": field, "Expected Header": config.RAW_METRIC_COLUMN_NAMES[field], "Found Header Text": found}
        for field, found in raw_result.matched_columns.items()
    ])
    st.dataframe(match_df, width='stretch', hide_index=True)

if load_validation.warnings:
    with st.expander(f"\u26a0\ufe0f {len(load_validation.warnings)} validation warning(s)", expanded=False):
        for w in load_validation.warnings:
            st.warning(w)

if len(match_result.unmatched) > 0:
    with st.expander(f"\U0001F50D Unmatched Office IDs ({len(match_result.unmatched)}) — not in Office Master", expanded=False):
        st.caption("These Office IDs were not found in the Office Master and were excluded from all division totals.")
        st.dataframe(match_result.unmatched, width='stretch', hide_index=True)

with st.expander("\u2139\ufe0f Reference-data diagnostics (device-not-supplied offices)", expanded=False):
    diag_cols = ["office_id", "office_name", "division", "devices_supplied", "device_status"]
    diag_df = universe[universe["device_status"] != config.DEVICE_STATUS_WORKING_ELIGIBLE][diag_cols]
    st.dataframe(diag_df.sort_values(["division", "office_name"]), width='stretch', hide_index=True)
    if ref.meta.get("data_quality_conflicts"):
        st.caption("Known data-quality conflicts resolved during reference-data preparation:")
        st.json(ref.meta["data_quality_conflicts"])

# ---------------------------------------------------------------------------
# Output section
# ---------------------------------------------------------------------------
st.subheader("3. Division-wise Consolidation")

display_df = consolidation_df.copy()
display_df["sl_no"] = display_df["sl_no"].astype(str)  # avoid mixed int/"" column (Total row)
display_df["activation_pct"] = (display_df["activation_pct"] * 100).round(2).astype(str) + "%"
display_df.columns = ["Sl.No", "Division Name", "No. of POS Machines Supplied", "No. of SBI POS \u2013 Activation Done", "Activation %"]
st.dataframe(display_df, width='stretch', hide_index=True)

total_row = consolidation_df[consolidation_df["division"] == "Total"].iloc[0]
devices_activated_total = int(total_row["devices_activated"])
devices_supplied_total_row = int(total_row["devices_supplied"])
green_offices_total = sum(int((df["activation_status"] == config.STATUS_GREEN).sum()) for df in division_sheets.values())
orange_total = sum(int((df["activation_status"] == config.STATUS_ORANGE).sum()) for df in division_sheets.values())
red_total = sum(int((df["activation_status"] == config.STATUS_RED).sum()) for df in division_sheets.values())

g1, g2, g3, g4 = st.columns(4)
g1.metric("\U0001F7E2 GREEN Offices (Activated)", green_offices_total)
g2.metric("\U0001F7E0 ORANGE Offices (Partial Activity)", orange_total)
g3.metric("\U0001F534 RED Offices (No Activity)", red_total)
g4.metric("Overall Activation % (by devices)", f"{total_row['activation_pct'] * 100:.2f}%")

with st.expander("View division-wise office detail", expanded=False):
    tabs = st.tabs(config.DIVISIONS)
    for tab, division in zip(tabs, config.DIVISIONS):
        with tab:
            df = division_sheets[division].copy()
            if len(df):
                df.columns = [
                    "Sl.No.", "Office ID", "Office Name", "Devices Supplied",
                    "Card Count", "Card Amount", "Bharat QR Count", "Bharat QR Amount",
                    "Combined Count", "Combined Amount", "Count Threshold", "Amount Threshold",
                    "Activation Status", "Sort Group",
                ]
                st.dataframe(df, width='stretch', hide_index=True)
            else:
                st.caption("No eligible offices in this division.")

st.subheader("4. Download")
st.download_button(
    label="\U0001F4E5 Download Activation Report (Excel)",
    data=workbook_bytes,
    file_name=f"POS_Activation_Report_{state['report_date_filename']}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
