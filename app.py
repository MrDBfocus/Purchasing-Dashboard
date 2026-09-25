import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import glob
import os
from datetime import datetime

# ==========================================================================
# CPJ PURCHASING DASHBOARD — REVISED
#
# WHAT WAS BROKEN AND WHY (see chat write-up for full detail):
#   1. ROOT CAUSE of almost every blank report: the "open 20-40 / closed
#      50-85 / deleted 99" status codes described in the revision notes
#      live in the PO Dates "Hst" column, NOT the "Status" text column
#      ("1-Not Departed", "6-Stripped", etc.). The old code extracted a
#      digit from "Status" (which only ever yields 1-6) and then filtered
#      for values between 20 and 85 — that can never match, so the PO
#      table was filtered down to ZERO rows on every load. Every tab that
#      depends on PO data (Executive Spend, GIT, Cash Flow, Price Index,
#      Supplier Scorecard, Bid Performance's on-order figures) was blank
#      because of this one line.
#   2. Bid Performance was fully broken: the code looked for columns
#      'Item ', 'Description', 'Customer' that don't exist in BIDS.xlsx.
#      The real columns are 'CPJ Code', 'CPJ Item Description',
#      'Customer Name', 'CPJ Class', 'Bid Status', etc. This raised a
#      KeyError that was silently swallowed, leaving bids as an empty
#      DataFrame.
#   3. The Sales/Purchase-Analysis category mapping referenced a column
#      ('Item Group') that doesn't exist in that file — the equivalent
#      field is 'Item Class'. The old code's fallback silently overwrote
#      the real 'Category' column with the literal string "UNCLASSIFIED"
#      for every row.
#   4. "In the Yard" containers are coded '5-Yard' in the data, not
#      '4-In the Yard' — the GIT status list didn't match, so Yard
#      containers were dropped from the pending/expediting report.
#   5. Filenames: the files you uploaded use underscores
#      (PO_Dates.xlsx, CPJ_FORECAST.xlsx, Purchase_Report_Sales_...csv);
#      the code looked for the space-separated versions. Added a small
#      file-finder so the app works with either naming convention.
#   6. A few reports were requested in the revision notes but never
#      actually built (Reorder Exception Summary, Safety Stock vs
#      Current Stock, Forecast-change safety-stock alerts, Inventory
#      Turnover ratios, PO Spend by Month 2025-present) — these are now
#      implemented.
#
# DATA-QUALITY NOTES (flagged, not hidden):
#   - One item ("SAMPLE MEAT", #9000912) carries a $4.6M inventory value
#     with a conversion factor of 1, which massively distorts the MEATS
#     category. Rows whose description contains "SAMPLE" are excluded
#     from the inventory analysis (they are clearly placeholder/test
#     items, not real stock). Flag this file for your team to check.
#   - There is no explicit "Safety Stock" or "Reorder Point" field in any
#     source file. Safety Stock here is *computed* as
#     (Lead Time in days / 30) x 3-Month-Avg-Depletion — a standard
#     lead-time coverage formula — using each item's 'Lead time' field
#     (defaults to 30 days where missing). Adjust the multiplier/formula
#     in SAFETY_STOCK section below if your team uses a different rule.
#   - "Committed" volume for the bid stock-vs-committed chart uses each
#     item's 3-Month-Avg-Depletion (in cases) as a practical proxy,
#     because BIDS.xlsx's 'Avg Monthly Usage' is in the *customer's* UOM
#     (e.g. KG) and the 'Customer to CPJ UOM' conversion column is empty
#     in this file, so it can't be reliably converted to cases.
#   - Rounding: per your instruction all values are shown with 0
#     decimals, EXCEPT "Months of Stock / Months Cover" figures, which
#     are kept to 1 decimal — rounding e.g. 0.2 and 0.4 months both to
#     "0" would erase exactly the urgency signal those reports exist to
#     show. Say the word and I'll force those to whole numbers too.
# ==========================================================================

st.set_page_config(page_title="CPJ Purchasing Dashboard", layout="wide", initial_sidebar_state="expanded")
st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")

DATA_DIR = "."  # change to wherever the source files live in your deployment

# ----------------------------------------
# FILE RESOLUTION (works with either naming convention)
# ----------------------------------------
def find_file(candidates):
    """Return the first existing path among candidates; fall back to a
    fuzzy glob match on the first keyword of each candidate name."""
    for c in candidates:
        p = os.path.join(DATA_DIR, c)
        if os.path.exists(p):
            return p
    for c in candidates:
        key = c.split('.')[0].split(' ')[0].split('_')[0]
        matches = glob.glob(os.path.join(DATA_DIR, f"*{key}*"))
        if matches:
            return matches[0]
    return None

# ----------------------------------------
# CATEGORY MAPPING DICTIONARY
# ----------------------------------------
cat_map = {
    'GR_SNACK': 'DRY GROCERY', 'OP_PROCM': 'OPERATIONS', 'CN_F&B': 'OPERATIONS', 'GR_COND': 'DRY GROCERY',
    'FF_MEAT': 'MEATS', 'BV_NONAL': 'BEVERAGE', 'GR_PASTA': 'DRY GROCERY', 'GR_BAKIN': 'DRY GROCERY',
    'FF_DAIRY': 'DAIRY', 'GR_FRUIT': 'DRY GROCERY', 'NF_F&B': 'NON-FOODS', 'OP_MAINT': 'OPERATIONS',
    'FF_GROCE': 'FRZ GROCERY', 'GR_BEVER': 'DRY GROCERY', 'BV_SPIRI': 'BEVERAGE', 'RM_MP': 'MEAT PLANT',
    'RM_JP': 'JUICE PLANT', 'OP_ITQ': 'OPERATIONS', 'GR_MEAT&': 'DRY GROCERY', 'NF_HOUSE': 'NON-FOOD',
    'OP_OVERH': 'OPERATIONS', 'OP_SALES': 'OPERATIONS', 'PO_BEV': 'OPERATIONS', 'BV_BEER': 'BEVERAGE',
    'FF_SEAFO': 'SEAFOOD', 'OP_PROCJ': 'OPERATIONS', 'BV_WINE': 'BEVERAGE', 'OP_MPASS': 'OPERATIONS',
    'NF_OTHER': 'NON-FOOD', 'BS_JUICE': 'BEVERAGE SYSTEM', 'BS_SOFTS': 'BEVERAGE SYSTEM',
    'BS_SLUSH': 'BEVERAGE SYSTEM', 'PO_CORP': 'OPERATIONS', 'PO_FOOD&': 'OPERATIONS',
    'OP_PASS': 'OPERATIONS', 'OP_CHARG': 'OPERATIONS'
}
EXCLUDED_CATS = ['OPERATIONS', 'BEVERAGE SYSTEM', 'JUICE PLANT', 'MEAT PLANT']

# Containers actually "in transit" per the data (note: the yard code is
# '5-Yard', not '4-In the Yard' as originally assumed)
TRANSIT_STATUSES = ['1-Not Departed', '2-On the Water', '3-At the Port', '5-Yard']
PORT_STATUSES = ['3-At the Port', '5-Yard']

def parse_mixed_dates(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s_clean = series.astype(str).str.split('.').str[0].replace({'nan': None, 'NaT': None, 'None': None, '': None})
    return pd.to_datetime(s_clean, format='%Y%m%d', errors='coerce')

def clean_id(series):
    return series.astype(str).str.replace(r'\.0$', '', regex=True).str.strip()

# ----------------------------------------
# 2. DATA LOADING & ENGINEERING
# ----------------------------------------
@st.cache_data
def load_data():
    # ---------------- SALES / PURCHASE ANALYSIS ----------------
    sales = pd.DataFrame()
    sales_path = find_file([
        "Purchase Report Sales Analysis with Raw Depletions.csv",
        "Purchase_Report_Sales_Analysis_with_Raw_Depletions.csv",
    ])
    try:
        sales = pd.read_csv(sales_path)
        sales.columns = [c.replace('\ufeff', '') for c in sales.columns]  # strip BOM

        # Remove the trailing subtotal row (blank Item Description) and the
        # leading placeholder row that some exports include (Item Number '-')
        sales = sales[~sales['Item Description'].astype(str).str.contains('Total', case=False, na=False)]
        sales.dropna(subset=['Item Description'], inplace=True)
        sales = sales[sales['Item Number'].astype(str) != '-']
        # Placeholder/test items (see data-quality note at top of file)
        sales = sales[~sales['Item Description'].astype(str).str.contains('SAMPLE', case=False, na=False)]

        sales['Item Number'] = clean_id(sales['Item Number'])

        # FIX: the category code lives in 'Item Class', not 'Item Group'
        # (which doesn't exist in this file). Written to a NEW column so
        # the file's own 'Category' field is preserved untouched.
        if 'Item Class' in sales.columns:
            sales['Custom Category'] = sales['Item Class'].map(cat_map).fillna('UNCLASSIFIED')
        else:
            sales['Custom Category'] = 'UNCLASSIFIED'

        for col in ['Inventory Value', 'Allocated quantity', 'On Order', 'Plants', 'Stores', 'OM',
                    'Conversion Factor', 'Last Price', 'Lead time']:
            if col in sales.columns:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)

        sales['Warehouse Inventory Value'] = (sales['Inventory Value'] - sales['Plants'] - sales['Stores'] - sales['OM']).clip(lower=0)
        sales['Conversion Factor'] = sales['Conversion Factor'].replace(0, 1)

        recent_depletions = [c for c in sales.columns if 'Depletion' in c][-3:]
        if recent_depletions:
            for col in recent_depletions:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)
            sales['3M_Avg_Depletion'] = sales[recent_depletions].to_numpy().mean(axis=1).round(0).astype(int)
            # Clipped-at-zero version for use as a "demand rate" in safety
            # stock / turnover math, where a handful of negative (return/
            # adjustment) depletion values would otherwise produce
            # nonsensical negative thresholds and ratios. Raw figure above
            # is kept untouched for anything just being displayed as-is.
            sales['3M_Avg_Depletion_Demand'] = sales['3M_Avg_Depletion'].clip(lower=0)
        else:
            sales['3M_Avg_Depletion'] = 0
            sales['3M_Avg_Depletion_Demand'] = 0

        sales['Current_Stock_Cases'] = (sales['Warehouse Inventory Value'] / sales['Conversion Factor']).round(0).astype(int)
        sales['Lead time'] = sales['Lead time'].replace(0, np.nan).fillna(30)  # 30-day default where missing
    except Exception as e:
        st.error(f"Error loading Sales Analysis file: {e}")
        sales = pd.DataFrame()

    # ---------------- PO DATES ----------------
    po = pd.DataFrame()
    po_path = find_file(["PO Dates.xlsx", "PO_Dates.xlsx"])
    try:
        po = pd.read_excel(po_path, sheet_name="GIT Report")

        # FIX (root cause of nearly every blank report): open/closed status
        # codes (20-40 open, 50-85 closed, 99 deleted) live in 'Hst', not in
        # the 'Status' text column.
        po['Status Code'] = pd.to_numeric(po['Hst'], errors='coerce').fillna(0)
        po = po[(po['Status Code'] >= 20) & (po['Status Code'] <= 85) & (po['Status Code'] != 99)]

        po['Item number'] = clean_id(po['Item number'])
        po['Custom Category'] = po['Item grp'].map(cat_map).fillna('UNCLASSIFIED')

        if not sales.empty:
            po = po.merge(sales[['Item Number', 'Conversion Factor']], left_on='Item number', right_on='Item Number', how='left')
        po['Conversion Factor'] = po.get('Conversion Factor', 1)
        po['Conversion Factor'] = po['Conversion Factor'].fillna(1).replace(0, 1)
        po['Buyer'] = po['Buyer'].fillna('Unassigned')
        po['Supplier'] = po['Supplier'].fillna('Unknown Supplier')

        for col in ['USD $', 'Order qty', 'Recd qty', 'Purch price']:
            if col in po.columns:
                po[col] = pd.to_numeric(po[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)

        po['Order Qty (Cases)'] = (po['Order qty'] / po['Conversion Factor']).round(0).astype(int)
        po['Recd Qty (Cases)'] = (po['Recd qty'] / po['Conversion Factor']).round(0).astype(int)

        po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
        po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
        po['Arrival'] = parse_mixed_dates(po['Arrival'])
        po['ETA'] = parse_mixed_dates(po['ETA'])
        po['Recd dt'] = parse_mixed_dates(po['Rec dt'])
        po['Stripped Date'] = parse_mixed_dates(po['Stripped'])
        po['Yard Date'] = parse_mixed_dates(po['Yard'])

        po['Order Month'] = po['Ord dt'].dt.to_period('M').astype(str)
        po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)

        # Only compute a day-variance where we actually have an Arrival date;
        # leaving unarrived POs as NaN (rather than 0) keeps the "early vs
        # late" histogram from being flooded with a fake spike at day 0.
        po['Arrival Variance (Days)'] = (po['Arrival'] - po['Req dt']).dt.days

        today = pd.Timestamp.today().normalize()
        po['Delivery Status'] = 'Not Tracked'
        in_transit = po['Status'].isin(TRANSIT_STATUSES)
        open_not_received = po['Recd dt'].isna() & (po['Status Code'] <= 49)

        po.loc[in_transit & open_not_received, 'Delivery Status'] = 'On Track'
        po.loc[in_transit & open_not_received & (po['Req dt'] < today), 'Delivery Status'] = 'Late (Past Req Date)'
        po.loc[in_transit & open_not_received & (po['ETA'] < today), 'Delivery Status'] = 'Late (Past ETA)'
        po.loc[po['Status'] == '6-Stripped', 'Delivery Status'] = 'Stripped'
        # A Recd date present, or a closed status code, means it's in — no
        # longer "late" regardless of ETA/Req date (per revision notes).
        po.loc[~po['Recd dt'].isna() | (po['Status Code'] > 49), 'Delivery Status'] = 'Received'
    except Exception as e:
        st.error(f"Error loading PO Dates file: {e}")
        po = pd.DataFrame()

    # ---------------- FORECAST ----------------
    fc = pd.DataFrame()
    fc_path = find_file(["CPJ FORECAST.xlsx", "CPJ_FORECAST.xlsx"])
    try:
        fc = pd.read_excel(fc_path, sheet_name="Sept - Mar FCST")
        fc['Item Code'] = clean_id(fc['Item Code'])
        # Forecast is already in cases — do NOT apply any conversion factor.
        fc_num_cols = [c for c in fc.columns if c not in
                       ['Brand', 'S Item Group', 'S Item Class', 'Item Code', 'Item Description', 'Supplier Name']]
        for col in fc_num_cols:
            fc[col] = pd.to_numeric(fc[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).round(0).astype(int)
        if len(fc_num_cols) >= 3:
            fc['3M_Avg_Forecast_Cases'] = fc[fc_num_cols[:3]].to_numpy().mean(axis=1).round(0).astype(int)
        else:
            fc['3M_Avg_Forecast_Cases'] = 0
        fc.attrs['forecast_cols'] = fc_num_cols
    except Exception as e:
        st.error(f"Error loading CPJ Forecast file: {e}")
        fc = pd.DataFrame()

    # ---------------- BIDS ----------------
    bids = pd.DataFrame()
    bids_path = find_file(["BIDS.xlsx"])
    try:
        bids = pd.read_excel(bids_path)
        # FIX: the old code looked for columns ('Item ', 'Description',
        # 'Customer') that don't exist in this file. Real columns below.
        bids = bids.rename(columns={
            'CPJ Code': 'Item Code',
            'CPJ Item Description': 'Item Description',
            'Customer Name': 'Customer',
            'CPJ Class': 'Item Class',
            'Avg Monthly Usage ': 'Avg Monthly Usage',  # trailing space in source
        })
        bids['Item Code'] = clean_id(bids['Item Code'])
        bids['Custom Category'] = bids['Item Class'].map(cat_map).fillna('UNCLASSIFIED')
        bids['Item Status'] = pd.to_numeric(bids['Item Status'], errors='coerce')
        bids['Avg Monthly Usage'] = pd.to_numeric(bids.get('Avg Monthly Usage', 0), errors='coerce').fillna(0)
    except Exception as e:
        bids = pd.DataFrame()

    return sales, po, fc, bids

sales_df, po_df, fc_df, bids_df = load_data()

if po_df.empty or sales_df.empty:
    st.error("Core data (PO Dates / Sales Analysis) failed to load — check that the source files are in the app's working directory. See error(s) above.")
    st.stop()

# Buyer most recently associated with each item (based on full PO history)
last_buyer_df = po_df.sort_values('Ord dt').groupby('Item number')['Buyer'].last().reset_index().rename(columns={'Buyer': 'Last Buyer'})

# Full current open pipeline — deliberately NOT limited by the sidebar
# Year/Month filters, because "what's currently on order" needs to reflect
# true present state for Bid Performance, Reorder Exceptions, and Forecast
# Coverage, regardless of which order-year the Executive tab happens to be
# scoped to.
po_active_all = po_df[(po_df['Status Code'] >= 20) & (po_df['Status Code'] <= 40)]

# ----------------------------------------
# 3. GLOBAL SLICERS (SIDEBAR)
# ----------------------------------------
st.sidebar.header("Global Filters")

valid_years = sorted([int(y) for y in po_df['Order Year'].dropna().unique().tolist() if y >= 2020])
default_year = [2026] if 2026 in valid_years else valid_years[-1:]
slicer_year = st.sidebar.multiselect("Order Year", valid_years, default=default_year)

clean_months = sorted([str(m) for m in po_df['Order Month'].dropna().unique() if str(m) not in ['NaT', 'nan', 'None']])
slicer_month = st.sidebar.multiselect("Order Month", clean_months)

clean_suppliers = sorted([str(s) for s in po_df['Supplier'].dropna().unique() if str(s) not in ['nan', 'None']])
slicer_supplier = st.sidebar.multiselect("Supplier", options=clean_suppliers)

clean_categories = sorted([str(c) for c in po_df['Custom Category'].dropna().unique() if str(c) not in ['nan', 'None']])
slicer_category = st.sidebar.multiselect("Custom Category", options=clean_categories)

def filter_data(po, sales):
    if not po.empty:
        if slicer_year: po = po[po['Order Year'].isin(slicer_year)]
        if slicer_month: po = po[po['Order Month'].isin(slicer_month)]
        if slicer_supplier: po = po[po['Supplier'].isin(slicer_supplier)]
        if slicer_category: po = po[po['Custom Category'].isin(slicer_category)]
    if not sales.empty and slicer_category:
        sales = sales[sales['Custom Category'].isin(slicer_category)]
    return po, sales

po_filtered, sales_filtered = filter_data(po_df, sales_df)
fc_filtered = fc_df  # forecast is a point-in-time snapshot; not date-filtered
po_active = po_filtered[(po_filtered['Status Code'] >= 20) & (po_filtered['Status Code'] <= 40)] if not po_filtered.empty else pd.DataFrame()

def round0(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].round(0)
    return df

# ----------------------------------------
# 4. DASHBOARD TABS
# ----------------------------------------
tabs = st.tabs([
    "📊 Executive Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", "🏆 Bid Performance",
    "📈 Forecast", "🤝 Supplier Scorecard", "💵 Cash Flow", "📉 Price Index (PPV)"
])

# -- TAB 1: EXECUTIVE & SPEND --
with tabs[0]:
    st.header("Executive Summary: Category & Buyer Spend")

    total_open_spend = po_active['USD $'].sum() if not po_active.empty else 0
    total_open_vol = po_active['Order Qty (Cases)'].sum() if not po_active.empty else 0
    total_inv_val = sales_filtered['Warehouse Inventory Value'].sum() if not sales_filtered.empty else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Open PO Spend", f"${total_open_spend:,.0f}")
    col2.metric("Total Open PO Volume (Cases)", f"{total_open_vol:,.0f}")
    col3.metric("Current Warehouse Inventory", f"${total_inv_val:,.0f}")

    st.divider()

    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.subheader("PO Volume (Cases) by Month — Current Year")
        if not po_filtered.empty:
            vol_summ = po_filtered.groupby('Order Month')['Order Qty (Cases)'].sum().reset_index().sort_values('Order Month')
            vol_summ['3M Moving Avg'] = vol_summ['Order Qty (Cases)'].rolling(window=3).mean().round(0)

            fig_vol = px.bar(vol_summ, x='Order Month', y='Order Qty (Cases)', title="Volume (Cases) with 3M Trend", text_auto=',.0f')
            fig_vol.add_trace(go.Scatter(x=vol_summ['Order Month'], y=vol_summ['3M Moving Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("No PO data available for the selected filters.")

    with row1_col2:
        st.subheader("Total Spend by Custom Category")
        if not po_filtered.empty:
            cat_summ = po_filtered.groupby('Custom Category')['USD $'].sum().reset_index().sort_values('USD $', ascending=True)
            cat_summ['USD $'] = cat_summ['USD $'].round(0)
            fig_cat = px.bar(cat_summ, x='USD $', y='Custom Category', orientation='h', title="Spend by Operational Category", text_auto=',.0f')
            st.plotly_chart(fig_cat, use_container_width=True)
        else:
            st.info("No spend data available.")

    st.subheader("PO Spend by Month — 2025 to Current")
    # Deliberately spans 2025-present regardless of the sidebar Year filter,
    # per the revision notes. Still respects the Category filter.
    spend_base = po_df if not slicer_category else po_df[po_df['Custom Category'].isin(slicer_category)]
    spend_trend = spend_base[spend_base['Order Year'] >= 2025]
    if not spend_trend.empty:
        sp_summ = spend_trend.groupby('Order Month')['USD $'].sum().reset_index().sort_values('Order Month')
        sp_summ['USD $'] = sp_summ['USD $'].round(0)
        sp_summ['3M Moving Avg'] = sp_summ['USD $'].rolling(window=3).mean().round(0)
        fig_sp = px.bar(sp_summ, x='Order Month', y='USD $', title="PO Spend by Month (2025–Present) with 3M Trend", text_auto=',.0f')
        fig_sp.add_trace(go.Scatter(x=sp_summ['Order Month'], y=sp_summ['3M Moving Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
        fig_sp.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig_sp, use_container_width=True)
    else:
        st.info("No PO spend data available since 2025.")

    st.subheader("Spend by Buyer (Consolidated <5%)")
    if not po_filtered.empty:
        buyer_summ = po_filtered.groupby('Buyer')['USD $'].sum().reset_index()
        total_spend = buyer_summ['USD $'].sum()
        if total_spend > 0:
            buyer_summ['Spend %'] = buyer_summ['USD $'] / total_spend
            buyer_summ.loc[buyer_summ['Spend %'] < 0.05, 'Buyer'] = 'Other Buyers'
            buyer_cons = buyer_summ.groupby('Buyer')['USD $'].sum().reset_index()
            buyer_cons['USD $'] = buyer_cons['USD $'].round(0)

            fig_buyer = px.pie(buyer_cons, values='USD $', names='Buyer', hole=0.4)
            fig_buyer.update_traces(textposition='inside', textinfo='value+percent+label', texttemplate="$%{value:,.0f}<br>%{percent}")
            fig_buyer.update_layout(height=650, title_text='Total Commitments by Buyer')
            st.plotly_chart(fig_buyer, use_container_width=True)
        else:
            st.info("Zero spend recorded for selected filters.")

# -- TAB 2: GIT & EXPEDITING --
with tabs[1]:
    st.header("Goods In Transit: Clearance Tracking & Exceptions")

    st.subheader("Clearance Metrics: Stripped vs Cleared (Yard) by Month")
    if not po_filtered.empty:
        po_git = po_filtered.copy()
        po_git['Stripped Month'] = po_git['Stripped Date'].dt.to_period('M').astype(str)
        po_git['Yard Month'] = po_git['Yard Date'].dt.to_period('M').astype(str)

        strip_cnt = po_git[po_git['Stripped Month'] != 'NaT'].groupby('Stripped Month')['Container Numb'].nunique().reset_index(name='Stripped Count')
        yard_cnt = po_git[po_git['Yard Month'] != 'NaT'].groupby('Yard Month')['Container Numb'].nunique().reset_index(name='Cleared Count')

        cnt_merged = pd.merge(strip_cnt, yard_cnt, left_on='Stripped Month', right_on='Yard Month', how='outer')
        cnt_merged['Month'] = cnt_merged['Stripped Month'].fillna(cnt_merged['Yard Month'])
        cnt_merged = cnt_merged.dropna(subset=['Month']).sort_values('Month')
        cnt_merged['Stripped Count'] = cnt_merged['Stripped Count'].fillna(0).astype(int)
        cnt_merged['Cleared Count'] = cnt_merged['Cleared Count'].fillna(0).astype(int)

        fig_clear = go.Figure()
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Stripped Count'], name='Stripped', marker_color='royalblue', text=cnt_merged['Stripped Count']))
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Cleared Count'], name='Cleared (Yard)', marker_color='darkorange', text=cnt_merged['Cleared Count']))
        fig_clear.add_trace(go.Scatter(x=cnt_merged['Month'], y=cnt_merged['Stripped Count'], mode='lines', name='Stripped Trend', line=dict(color='blue', dash='dot')))
        fig_clear.add_trace(go.Scatter(x=cnt_merged['Month'], y=cnt_merged['Cleared Count'], mode='lines', name='Cleared Trend', line=dict(color='orange', dash='dot')))
        fig_clear.update_layout(barmode='group', title="Container Processing Counts (Year to Month)", xaxis_tickangle=-45)
        st.plotly_chart(fig_clear, use_container_width=True)
    else:
        st.info("No container data for selected filters.")

    st.subheader("Pending Containers / Open Orders")
    st.caption("Containers Not Departed, On the Water, At the Port, or In the Yard — excluded once received (Recd date present) or Hst status > 49.")
    if not po_filtered.empty:
        pending_git = po_filtered[
            po_filtered['Status'].isin(TRANSIT_STATUSES) &
            po_filtered['Recd dt'].isna() &
            (po_filtered['Status Code'] <= 49)
        ]
        display_cols = ['Container Numb', 'PO no', 'Status', 'Supplier', 'Req dt', 'ETA', 'Delivery Status']
        st.dataframe(pending_git[display_cols].drop_duplicates(), use_container_width=True)
        late_count = pending_git['Delivery Status'].isin(['Late (Past Req Date)', 'Late (Past ETA)']).sum()
        st.caption(f"{len(pending_git):,} pending containers — {late_count:,} currently flagged late.")
    else:
        st.info("No pending containers for the selected filters.")

    st.subheader("Early vs. Late Delivery Distribution")
    if not po_filtered.empty:
        arr_df = po_filtered.dropna(subset=['Arrival Variance (Days)'])
        if not arr_df.empty:
            fig_arr = px.histogram(arr_df, x='Arrival Variance (Days)', nbins=40,
                                    title="Distribution of Delivery Timing (Negative = Early, Positive = Late Days)")
            st.plotly_chart(fig_arr, use_container_width=True)
        else:
            st.info("No arrival data available yet for the selected filters.")

# -- TAB 3: INVENTORY HEALTH --
with tabs[2]:
    st.header("Inventory Health: Turnover, Dead Stock & Slow Moving")

    # Shared evaluation frame used by several reports below
    inv_eval = pd.DataFrame()
    if not sales_filtered.empty and not fc_filtered.empty:
        inv_eval = sales_filtered.merge(fc_filtered[['Item Code', '3M_Avg_Forecast_Cases']],
                                         left_on='Item Number', right_on='Item Code', how='left')
        inv_eval['3M_Avg_Forecast_Cases'] = inv_eval['3M_Avg_Forecast_Cases'].fillna(0).astype(int)
        pipeline = po_active_all.groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order (Pipeline)')
        inv_eval = inv_eval.merge(pipeline, left_on='Item Number', right_on='Item number', how='left')
        inv_eval['Cases On Order (Pipeline)'] = inv_eval['Cases On Order (Pipeline)'].fillna(0).astype(int)
        inv_eval['Lead Time (Months)'] = (inv_eval['Lead time'] / 30).round(2)
        inv_eval['Safety Stock (Cases)'] = (inv_eval['Lead Time (Months)'] * inv_eval['3M_Avg_Depletion_Demand']).round(0).astype(int)

    st.subheader("Inventory Turnover & Sell-Through Ratio by Category")
    if not sales_filtered.empty:
        turn = sales_filtered.groupby('Custom Category').agg(
            Total_Inventory_Cases=('Current_Stock_Cases', 'sum'),
            Total_3M_Avg_Depletion=('3M_Avg_Depletion_Demand', 'sum'),
        ).reset_index()
        turn['Annualized Turnover (x)'] = np.where(turn['Total_Inventory_Cases'] > 0,
                                                     (turn['Total_3M_Avg_Depletion'] * 4) / turn['Total_Inventory_Cases'], 0).round(0)
        turn['Sell-Through %'] = np.where((turn['Total_3M_Avg_Depletion'] + turn['Total_Inventory_Cases']) > 0,
                                           turn['Total_3M_Avg_Depletion'] / (turn['Total_3M_Avg_Depletion'] + turn['Total_Inventory_Cases']) * 100, 0).round(0)
        turn = turn.sort_values('Annualized Turnover (x)', ascending=False)

        fig_turn = px.bar(turn, x='Custom Category', y='Annualized Turnover (x)', title="Annualized Inventory Turnover by Category", text_auto=',.0f')
        fig_turn.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig_turn, use_container_width=True)
        st.dataframe(turn.style.format({
            "Total_Inventory_Cases": "{:,.0f}", "Total_3M_Avg_Depletion": "{:,.0f}",
            "Annualized Turnover (x)": "{:,.0f}x", "Sell-Through %": "{:,.0f}%"
        }), use_container_width=True)

    if not inv_eval.empty:
        inv_eval_filtered = inv_eval[~inv_eval['Custom Category'].isin(EXCLUDED_CATS)]

        row3_col1, row3_col2 = st.columns(2)
        with row3_col1:
            st.subheader("💀 Dead Stock by Category")
            st.caption("Inventory on hand with no movement over the trailing 3 months.")
            dead_stock = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 100) & (inv_eval_filtered['3M_Avg_Depletion'] <= 0)]
            if not dead_stock.empty:
                dead_cat = dead_stock.groupby('Custom Category')['Warehouse Inventory Value'].sum().round(0).reset_index()
                fig_dead = px.bar(dead_cat, x='Custom Category', y='Warehouse Inventory Value', title="Dead Stock Value by Category", text_auto=',.0f')
                fig_dead.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig_dead, use_container_width=True)
            else:
                st.info("No dead stock identified.")

        with row3_col2:
            st.subheader("🐢 Slow Moving Stock by Category")
            st.caption("Actual sales trailing >30% below the forecast (i.e. below 70% of forecast).")
            slow = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 0) & (inv_eval_filtered['3M_Avg_Forecast_Cases'] > 0)].copy()
            slow['Sales vs Forecast'] = slow['3M_Avg_Depletion'] / slow['3M_Avg_Forecast_Cases']
            slow_moving = slow[slow['Sales vs Forecast'] < 0.70]
            if not slow_moving.empty:
                slow_cat = slow_moving.groupby('Custom Category')['Warehouse Inventory Value'].sum().round(0).reset_index()
                fig_slow = px.bar(slow_cat, x='Custom Category', y='Warehouse Inventory Value', title="Slow Moving Stock Value by Category", text_auto=',.0f')
                fig_slow.update_layout(xaxis_tickangle=-45)
                st.plotly_chart(fig_slow, use_container_width=True)
            else:
                st.info("No slow moving stock identified.")

    st.divider()
    st.subheader("Safety Stock vs. Current Stock Levels")
    st.caption("Safety Stock is computed as (Lead Time ÷ 30 days) × 3-Month Avg Depletion — no explicit safety-stock field exists in the source files. Showing items currently at or below their computed threshold.")
    if not inv_eval.empty:
        at_risk_safety = inv_eval[(inv_eval['Safety Stock (Cases)'] > 0) & (inv_eval['Current_Stock_Cases'] <= inv_eval['Safety Stock (Cases)'] * 1.2)]
        at_risk_safety = at_risk_safety.sort_values('Current_Stock_Cases').head(25)
        if not at_risk_safety.empty:
            fig_safety = go.Figure()
            fig_safety.add_trace(go.Bar(x=at_risk_safety['Item Description'], y=at_risk_safety['Current_Stock_Cases'], name='Current Stock (Cases)', marker_color='teal'))
            fig_safety.add_trace(go.Scatter(x=at_risk_safety['Item Description'], y=at_risk_safety['Safety Stock (Cases)'], name='Safety Stock Threshold', mode='lines+markers', line=dict(color='red', width=3)))
            fig_safety.update_layout(title="Items At or Below Safety Stock (Top 25 by Lowest Stock)", xaxis_tickangle=-45)
            st.plotly_chart(fig_safety, use_container_width=True)
            st.dataframe(at_risk_safety[['Item Number', 'Item Description', 'Custom Category', 'Current_Stock_Cases', 'Safety Stock (Cases)', 'Lead Time (Months)']]
                         .style.format({"Current_Stock_Cases": "{:,.0f}", "Safety Stock (Cases)": "{:,.0f}", "Lead Time (Months)": "{:,.1f}"}),
                         use_container_width=True)
        else:
            st.info("No items currently at or below their computed safety stock threshold.")

    st.subheader("Safety Stock Alerts — Significant Forecast Changes")
    st.caption("Items where the 6-month forecast's near-term average differs from trailing actual depletion by more than 30% — buyers should reassess safety stock for these.")
    if not inv_eval.empty:
        fchg = inv_eval[inv_eval['3M_Avg_Depletion_Demand'] > 0].copy()
        fchg['Forecast Change %'] = ((fchg['3M_Avg_Forecast_Cases'] - fchg['3M_Avg_Depletion_Demand']) / fchg['3M_Avg_Depletion_Demand'] * 100).round(0)
        sig_change = fchg[fchg['Forecast Change %'].abs() > 30].sort_values('Forecast Change %', ascending=False)
        if not sig_change.empty:
            st.dataframe(sig_change[['Item Number', 'Item Description', 'Custom Category', '3M_Avg_Depletion_Demand',
                                      '3M_Avg_Forecast_Cases', 'Forecast Change %', 'Safety Stock (Cases)']]
                         .rename(columns={'3M_Avg_Depletion_Demand': 'Recent Avg Depletion (Cases)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cases)'})
                         .style.format({"Recent Avg Depletion (Cases)": "{:,.0f}", "Near-Term Forecast (Cases)": "{:,.0f}",
                                         "Forecast Change %": "{:+,.0f}%", "Safety Stock (Cases)": "{:,.0f}"}),
                         use_container_width=True)
        else:
            st.info("No items with a significant forecast shift this period.")

    st.divider()
    st.subheader("Reorder Exception Summary")
    st.caption("Items where on-hand stock + everything already on order won't outlast that item's own lead time against the near-term forecast — i.e. at risk of stocking out before a fresh order could arrive.")
    if not inv_eval.empty:
        reorder_base = inv_eval[inv_eval['3M_Avg_Forecast_Cases'] > 0].copy()
        reorder_base['Months Cover (On Hand + Pipeline)'] = (
            (reorder_base['Current_Stock_Cases'] + reorder_base['Cases On Order (Pipeline)']) / reorder_base['3M_Avg_Forecast_Cases']
        ).round(1)
        reorder_flag = reorder_base[reorder_base['Months Cover (On Hand + Pipeline)'] < reorder_base['Lead Time (Months)']]
        reorder_flag = reorder_flag.merge(last_buyer_df, left_on='Item Number', right_on='Item number', how='left')
        reorder_flag = reorder_flag.sort_values('Months Cover (On Hand + Pipeline)')
        if not reorder_flag.empty:
            st.dataframe(reorder_flag[['Item Number', 'Item Description', 'Custom Category', 'Current_Stock_Cases',
                                        'Cases On Order (Pipeline)', '3M_Avg_Forecast_Cases', 'Lead Time (Months)',
                                        'Months Cover (On Hand + Pipeline)', 'Last Buyer']]
                         .rename(columns={'Current_Stock_Cases': 'Current Stock (Cases)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cases)'})
                         .style.format({"Current Stock (Cases)": "{:,.0f}", "Cases On Order (Pipeline)": "{:,.0f}",
                                         "Near-Term Forecast (Cases)": "{:,.0f}", "Lead Time (Months)": "{:,.1f}",
                                         "Months Cover (On Hand + Pipeline)": "{:,.1f}"}),
                         use_container_width=True, height=400)
            st.caption(f"{len(reorder_flag):,} items flagged for reorder attention.")
        else:
            st.info("No items currently flagged for reorder.")

# -- TAB 4: BID PERFORMANCE --
with tabs[3]:
    st.header("🏆 Bid Customer Fulfillment & Coverage")

    if not bids_df.empty and not sales_df.empty:
        bids_current = bids_df[(bids_df['Bid Status'] == 'Current') & (bids_df['Item Status'] != 99)].copy()

        bid_items = bids_current.groupby('Item Code').agg(
            Item_Description=('Item Description', 'first'),
            Custom_Category=('Custom Category', 'first'),
            Customer_Count=('Customer', 'nunique'),
        ).reset_index()

        bid_items = bid_items.merge(sales_df[['Item Number', 'Current_Stock_Cases', '3M_Avg_Depletion_Demand']],
                                     left_on='Item Code', right_on='Item Number', how='left')
        bid_items['Current_Stock_Cases'] = bid_items['Current_Stock_Cases'].fillna(0).astype(int)
        bid_items['3M_Avg_Depletion_Demand'] = bid_items['3M_Avg_Depletion_Demand'].fillna(0).astype(int)

        po_port = po_active_all[po_active_all['Status'].isin(PORT_STATUSES)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases At Port')
        po_order = po_active_all[~po_active_all['Status'].isin(PORT_STATUSES)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order')

        bid_items = bid_items.merge(po_port, left_on='Item Code', right_on='Item number', how='left')
        bid_items = bid_items.merge(po_order, left_on='Item Code', right_on='Item number', how='left', suffixes=('', '_ord'))
        bid_items = bid_items.merge(last_buyer_df, left_on='Item Code', right_on='Item number', how='left', suffixes=('', '_buyer'))
        bid_items['Cases At Port'] = bid_items['Cases At Port'].fillna(0).astype(int)
        bid_items['Cases On Order'] = bid_items['Cases On Order'].fillna(0).astype(int)

        bid_items['Months of Stock'] = np.where(bid_items['3M_Avg_Depletion_Demand'] > 0,
                                                 (bid_items['Current_Stock_Cases'] / bid_items['3M_Avg_Depletion_Demand']).round(1), np.nan)

        bid_items['Availability Status'] = 'Not In Stock'
        bid_items.loc[bid_items['Cases On Order'] > 0, 'Availability Status'] = 'On Order'
        bid_items.loc[bid_items['Cases At Port'] > 0, 'Availability Status'] = 'Inventory @ Port'
        bid_items.loc[bid_items['Current_Stock_Cases'] > 0, 'Availability Status'] = 'Available'

        st.subheader("Bid Item Stock Coverage by Category")
        st.caption("Unique bid items, summarized by category — current stock, on order, and months of stock on hand.")
        cov_by_cat = bid_items.groupby('Custom_Category').agg(
            Item_Count=('Item Code', 'nunique'),
            Current_Stock_Cases=('Current_Stock_Cases', 'sum'),
            Cases_At_Port=('Cases At Port', 'sum'),
            Cases_On_Order=('Cases On Order', 'sum'),
            Avg_Months_of_Stock=('Months of Stock', 'mean'),
        ).reset_index().sort_values('Current_Stock_Cases', ascending=False)
        cov_by_cat['Avg_Months_of_Stock'] = cov_by_cat['Avg_Months_of_Stock'].round(1)
        st.dataframe(cov_by_cat.rename(columns={
            'Custom_Category': 'Category', 'Item_Count': 'Items', 'Current_Stock_Cases': 'Current Stock (Cases)',
            'Cases_At_Port': 'Cases at Port', 'Cases_On_Order': 'Cases On Order', 'Avg_Months_of_Stock': 'Avg Months of Stock'
        }).style.format({"Current Stock (Cases)": "{:,.0f}", "Cases at Port": "{:,.0f}", "Cases On Order": "{:,.0f}",
                          "Avg Months of Stock": "{:,.1f}", "Items": "{:,.0f}"}),
                     use_container_width=True)

        colA, colB = st.columns([1, 1])
        with colA:
            st.subheader("Overall Bid Item Availability")
            fig_bids = px.pie(bid_items, names='Availability Status', title="Availability Breakdown (Unique Bid Items)",
                               color='Availability Status',
                               color_discrete_map={'Available': 'green', 'Inventory @ Port': 'orange', 'On Order': 'gold', 'Not In Stock': 'red'})
            st.plotly_chart(fig_bids, use_container_width=True)

        with colB:
            st.subheader("Stock vs. Ongoing Demand, by Category")
            st.caption("'Committed' uses 3-month avg depletion (cases) as a practical demand proxy — the bid file's own usage figures are in customer units (e.g. KG), not cases, and can't be reliably converted.")
            demand_cat = bid_items.groupby('Custom_Category').agg(
                Stock=('Current_Stock_Cases', 'sum'), Demand=('3M_Avg_Depletion_Demand', 'sum')
            ).reset_index()
            fig_demand = go.Figure()
            fig_demand.add_trace(go.Bar(x=demand_cat['Custom_Category'], y=demand_cat['Stock'], name='Current Stock (Cases)'))
            fig_demand.add_trace(go.Bar(x=demand_cat['Custom_Category'], y=demand_cat['Demand'], name='3M Avg Demand (Cases)'))
            fig_demand.update_layout(barmode='group', xaxis_tickangle=-45, title="Stock vs. Demand by Category")
            st.plotly_chart(fig_demand, use_container_width=True)

        st.subheader("⚠️ At-Risk Bid Items — No Stock, Not On Order, Not At Port")
        st.caption("Requires buyer attention to replenish.")
        critical = bid_items[(bid_items['Current_Stock_Cases'] == 0) & (bid_items['Cases On Order'] == 0) & (bid_items['Cases At Port'] == 0)]
        st.dataframe(critical[['Item Code', 'Item_Description', 'Custom_Category', 'Customer_Count', 'Last Buyer']]
                     .rename(columns={'Item_Description': 'Description', 'Custom_Category': 'Category', 'Customer_Count': '# Bid Customers'}),
                     use_container_width=True)
        st.caption(f"{len(critical):,} of {len(bid_items):,} unique bid items are currently at risk.")

        with st.expander("All Bid Item Coverage (detail)"):
            st.dataframe(bid_items[['Item Code', 'Item_Description', 'Custom_Category', 'Customer_Count', 'Last Buyer',
                                     'Current_Stock_Cases', 'Cases At Port', 'Cases On Order', 'Months of Stock', 'Availability Status']]
                         .rename(columns={'Item_Description': 'Description', 'Custom_Category': 'Category', 'Customer_Count': '# Bid Customers',
                                           'Current_Stock_Cases': 'Current Stock (Cases)'})
                         .style.format({"Current Stock (Cases)": "{:,.0f}", "Cases At Port": "{:,.0f}", "Cases On Order": "{:,.0f}",
                                         "Months of Stock": "{:,.1f}"}),
                         use_container_width=True)
    else:
        st.info("Bid data not loaded.")

# -- TAB 5: FORECAST --
with tabs[4]:
    st.header("6-Month Forecast")

    search_term = st.text_input("🔍 Search Item Code or Description:", "")

    if not fc_filtered.empty:
        fc_display = fc_filtered.copy()
        if search_term:
            fc_display = fc_display[fc_display['Item Code'].str.contains(search_term, case=False, na=False) |
                                     fc_display['Item Description'].str.contains(search_term, case=False, na=False)]

        forecast_cols = fc_filtered.attrs.get('forecast_cols', [])[:6]  # first 6 months only

        high_vol = fc_display[fc_display['3M_Avg_Forecast_Cases'] >= 10].sort_values(by='3M_Avg_Forecast_Cases', ascending=False)

        if not high_vol.empty and forecast_cols:
            high_vol_melt = high_vol.melt(id_vars=['Item Code', 'Item Description'], value_vars=forecast_cols, var_name='Month', value_name='Forecasted Cases')
            agg_forecast = high_vol_melt.groupby('Month', sort=False)['Forecasted Cases'].sum().reset_index()
            agg_forecast['Month'] = pd.Categorical(agg_forecast['Month'], categories=forecast_cols, ordered=True)
            agg_forecast = agg_forecast.sort_values('Month')

            fig_fc = px.bar(agg_forecast, x='Month', y='Forecasted Cases', title='6-Month Forecast', text_auto=',.0f')
            fig_fc.update_traces(marker_color='darkmagenta', textfont=dict(size=14, color='white'))
            fig_fc.update_layout(xaxis_title="Forecast Month", yaxis_title="Total Cases")
            st.plotly_chart(fig_fc, use_container_width=True)

            st.dataframe(high_vol[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + forecast_cols]
                         .rename(columns={'3M_Avg_Forecast_Cases': 'Near-Term Avg (Cases)'})
                         .style.format("{:,.0f}", subset=forecast_cols + ['Near-Term Avg (Cases)']),
                         use_container_width=True)
        else:
            st.info("No forecast items meeting the criteria (≥ 10 Cases Avg).")

        st.subheader("Category Forecast Coverage")
        st.caption("Uses the full current open-order pipeline (not limited by the sidebar Year/Month filters), so coverage reflects true present state.")
        if not sales_df.empty:
            po_order_agg = po_active_all.groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order')

            fc_cov = fc_display.merge(sales_df[['Item Number', 'Current_Stock_Cases', 'Custom Category']], left_on='Item Code', right_on='Item Number', how='left')
            fc_cov = fc_cov.merge(po_order_agg, left_on='Item Code', right_on='Item number', how='left')
            fc_cov['Current_Stock_Cases'] = fc_cov['Current_Stock_Cases'].fillna(0)
            fc_cov['Cases On Order'] = fc_cov['Cases On Order'].fillna(0)

            cov_summary = fc_cov.groupby('Custom Category').agg({
                'Current_Stock_Cases': 'sum', 'Cases On Order': 'sum', '3M_Avg_Forecast_Cases': 'sum'
            }).reset_index()

            cov_summary['Stock Coverage (Months)'] = np.where(cov_summary['3M_Avg_Forecast_Cases'] > 0,
                                                                cov_summary['Current_Stock_Cases'] / cov_summary['3M_Avg_Forecast_Cases'], 0).round(1)
            cov_summary['Coverage w/ On-Order (Months)'] = np.where(cov_summary['3M_Avg_Forecast_Cases'] > 0,
                                                                      (cov_summary['Current_Stock_Cases'] + cov_summary['Cases On Order']) / cov_summary['3M_Avg_Forecast_Cases'], 0).round(1)

            st.dataframe(cov_summary.rename(columns={'Custom Category': 'Category', 'Current_Stock_Cases': 'Current Stock (Cases)',
                                                       '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cases)'})
                         .style.format({"Current Stock (Cases)": "{:,.0f}", "Cases On Order": "{:,.0f}", "Near-Term Forecast (Cases)": "{:,.0f}",
                                         "Stock Coverage (Months)": "{:,.1f}", "Coverage w/ On-Order (Months)": "{:,.1f}"}),
                         use_container_width=True)

# -- TAB 6: SUPPLIER SCORECARD --
with tabs[5]:
    st.header("Supplier Scorecard: Reliability & Fill Rates")

    if not po_filtered.empty:
        vendor_search = st.text_input("🔍 Search Vendor / Supplier:", "")
        po_score_df = po_filtered.copy()
        if vendor_search:
            po_score_df = po_score_df[po_score_df['Supplier'].str.contains(vendor_search, case=False, na=False)]

        po_recd = po_score_df[(po_score_df['Order qty'] > 0) & (po_score_df['Recd qty'] > 0)].copy()
        po_recd['Fill Deviation %'] = ((po_recd['Recd qty'] / po_recd['Order qty']) - 1) * 100

        st.subheader("Top 10 Inconsistent Vendors (Ordered vs Received Deviation)")
        if not po_recd.empty:
            fill_summ = po_recd.groupby('Supplier')['Fill Deviation %'].apply(lambda x: abs(x).mean()).reset_index().sort_values('Fill Deviation %', ascending=False).head(10)
            fill_summ['Fill Deviation %'] = fill_summ['Fill Deviation %'].round(0)

            fig_fill = px.bar(fill_summ, x='Fill Deviation %', y='Supplier', orientation='h', title="Top 10 Vendors by Absolute Avg % Deviation", text_auto=',.0f')
            fig_fill.update_layout(yaxis={'categoryorder': 'total ascending'})
            st.plotly_chart(fig_fill, use_container_width=True)
        else:
            st.info("No received-quantity data available for the selected filters.")

# -- TAB 7: CASH FLOW --
with tabs[6]:
    st.header("Cash Flow Commitments by Expected Arrival")
    if not po_active.empty:
        cf_df = po_active.dropna(subset=['ETA', 'USD $']).copy()
        if not cf_df.empty:
            cf_df['ETA Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
            cf_summ = cf_df.groupby('ETA Month')['USD $'].sum().reset_index().sort_values('ETA Month')
            cf_summ['USD $'] = cf_summ['USD $'].round(0)

            fig_cf = px.bar(cf_summ, x='ETA Month', y='USD $', title='Capital Required Based on Expected Arrival (USD)', text_auto=',.0f')
            fig_cf.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig_cf, use_container_width=True)
        else:
            st.info("No active POs with both an ETA and a USD value for the selected filters.")
    else:
        st.info("No active PO cash flow data available for the selected filters.")

# -- TAB 8: PRICE INDEX (PPV) --
with tabs[7]:
    st.header("Procurement & Price Index (PPV Trend)")
    st.markdown("Tracks wholesale unit price fluctuations over time for key commodities to evaluate supplier price creep.")

    hist_prices = po_df[(po_df['Purch price'] > 0) & (po_df['Item number'].notna())].copy()
    hist_prices = hist_prices.sort_values('Ord dt')

    item_search = st.text_input("🔍 Search Item for Price Trend (Code or Description):")
    if item_search:
        trend_df = hist_prices[hist_prices['Item number'].str.contains(item_search, case=False, na=False) |
                                hist_prices['ItemDescription'].str.contains(item_search, case=False, na=False)]
        if not trend_df.empty:
            fig_trend = px.line(trend_df, x='Ord dt', y='Purch price', color='Supplier', markers=True, title=f"Historical Price Trend for {item_search}")
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.warning("No price history found for this item.")
    else:
        st.subheader("Overall Purchase Price Trend (All Items)")
        monthly_price = hist_prices.copy()
        monthly_price['Order Month'] = monthly_price['Ord dt'].dt.to_period('M').astype(str)
        avg_price_trend = monthly_price.groupby('Order Month')['Purch price'].mean().round(0).reset_index()
        avg_price_trend = avg_price_trend.sort_values('Order Month')
        fig_overall = px.line(avg_price_trend, x='Order Month', y='Purch price', markers=True, title="Average PO Unit Price by Month (All Items)")
        fig_overall.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig_overall, use_container_width=True)
        st.caption("Search an item above to drill into its individual price history by supplier.")

    if not po_active.empty and not sales_filtered.empty:
        ppv_df = po_active[po_active['Purch price'] > 0].merge(sales_filtered[['Item Number', 'Last Price']], left_on='Item number', right_on='Item Number', how='left')
        ppv_df = ppv_df.dropna(subset=['Last Price'])
        ppv_df['Variance ($)'] = ppv_df['Purch price'] - ppv_df['Last Price']
        ppv_df['Variance (%)'] = np.where(ppv_df['Last Price'] > 0, (ppv_df['Variance ($)'] / ppv_df['Last Price']) * 100, 0)

        colX, colY = st.columns(2)
        with colX:
            st.subheader("Inflation: Active Price Increases")
            inc = ppv_df[ppv_df['Variance (%)'] > 5].sort_values('Variance (%)', ascending=False)
            st.dataframe(inc[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15)
                         .style.format({'Purch price': "${:,.0f}", 'Last Price': "${:,.0f}", 'Variance (%)': "{:.0f}%"}),
                         use_container_width=True)
        with colY:
            st.subheader("Savings: Secured Cost Reductions")
            dec = ppv_df[ppv_df['Variance (%)'] < -5].sort_values('Variance (%)', ascending=True)
            st.dataframe(dec[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15)
                         .style.format({'Purch price': "${:,.0f}", 'Last Price': "${:,.0f}", 'Variance (%)': "{:.0f}%"}),
                         use_container_width=True)
    else:
        st.info("Insufficient PO historical data for PPV calculation with current filters.")
