import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import glob
import os

# ==========================================================================
# CPJ PURCHASING DASHBOARD  --  REVISION 3
#
# Key logic decisions (all editable in the CONFIG block below):
#   * PO Otp = P01 is excluded everywhere by default (sidebar toggle to bring it back).
#   * Open PO = Hst 20-40.  Closed/received = Hst 75-85.  Hst 99 = deleted / dropped line.
#   * Item Status (Purchase Report): 10 new, 20 active, 50 soft-disc, 80 full-disc, 90-99 deleted.
#     Bid / forecast / replenishment reports ignore 50, 80 and 90-99 items.
#   * Warehouse quantities (MDC, SEP, MJM, Stores, Bonded, Plants, OM, Allocated) are stored in the
#     Purchase Report in base UOM. Each is divided by 'Conversion Factor' to report CASES.
#   * Depletion figures are in base UOM (EA) and are ALSO divided by the Conversion Factor to get cases.
# ==========================================================================

st.set_page_config(page_title="CPJ Purchasing Dashboard", layout="wide", initial_sidebar_state="expanded")
st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")

# ----------------------------------------
# CONFIG
# ----------------------------------------
DATA_DIR = "."

CAT_MAP = {
    'GR_SNACK': 'DRY GROCERY', 'OP_PROCM': 'OPERATIONS', 'CN_F&B': 'OPERATIONS', 'GR_COND': 'DRY GROCERY',
    'FF_MEAT': 'MEATS', 'BV_NONAL': 'BEVERAGE', 'GR_PASTA': 'DRY GROCERY', 'GR_BAKIN': 'DRY GROCERY',
    'FF_DAIRY': 'DAIRY', 'GR_FRUIT': 'DRY GROCERY', 'NF_F&B': 'NON-FOOD', 'OP_MAINT': 'OPERATIONS',
    'FF_GROCE': 'FRZ GROCERY', 'GR_BEVER': 'DRY GROCERY', 'BV_SPIRI': 'BEVERAGE', 'RM_MP': 'MEAT PLANT',
    'RM_JP': 'JUICE PLANT', 'OP_ITQ': 'OPERATIONS', 'GR_MEAT&': 'DRY GROCERY', 'NF_HOUSE': 'NON-FOOD',
    'OP_OVERH': 'OPERATIONS', 'OP_SALES': 'OPERATIONS', 'PO_BEV': 'OPERATIONS', 'BV_BEER': 'BEVERAGE',
    'FF_SEAFO': 'SEAFOOD', 'OP_PROCJ': 'OPERATIONS', 'BV_WINE': 'BEVERAGE', 'OP_MPASS': 'OPERATIONS',
    'NF_OTHER': 'NON-FOOD', 'BS_JUICE': 'BEVERAGE SYSTEM', 'BS_SOFTS': 'BEVERAGE SYSTEM',
    'BS_SLUSH': 'BEVERAGE SYSTEM', 'PO_CORP': 'OPERATIONS', 'PO_FOOD&': 'OPERATIONS',
    'OP_PASS': 'OPERATIONS', 'OP_CHARG': 'OPERATIONS'
}
# Responsible buyer by item group (from revision notes). Also defines the "saleable" item groups.
BUYER_MAP = {
    'GR_SNACK': 'SOMMMONE', 'GR_COND': 'WILLLATO', 'FF_MEAT': 'SHIMKADI', 'BV_NONAL': 'SEWEKEVI',
    'GR_PASTA': 'WILLLATO', 'GR_BAKIN': 'WILLLATO', 'FF_DAIRY': 'KHATSAME', 'GR_FRUIT': 'WILLLATO',
    'NF_F&B': 'SHIMKADI', 'FF_GROCE': 'KHATSAME', 'GR_BEVER': 'WILLLATO', 'BV_SPIRI': 'SEWEKEVI',
    'RM_MP': 'SHIMKADI', 'RM_JP': 'SAMUJODI', 'GR_MEAT&': 'WILLLATO', 'NF_HOUSE': 'SHIMKADI',
    'BV_BEER': 'SEWEKEVI', 'FF_SEAFO': 'SHIMKADI', 'BV_WINE': 'SHIMKADI'
}
SALEABLE_GROUPS = list(BUYER_MAP.keys())

EXCLUDED_CATS = ['OPERATIONS', 'BEVERAGE SYSTEM', 'JUICE PLANT', 'MEAT PLANT']  # dead/slow-stock views
TRANSIT_STATUSES = ['1-Not Departed', '2-On the Water', '3-At the Port', '5-Yard']
PORT_STATUSES = ['3-At the Port', '5-Yard']
STATUS_RANK = {'3-At the Port': 0, '5-Yard': 1, '2-On the Water': 2, '1-Not Departed': 3}

LIVE_STATUSES = [10, 20]                 # new + active
DISCONTINUED = [50, 80]
DELETED = list(range(90, 100))
MIN_SUB_MONTHS = 0.5                     # substitute must hold > this many months of stock
DEAD_STOCK_INCLUDES_DISCONTINUED = True  # dead-stock $ is most useful on discontinued items

SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM', 'Allocated quantity']
SITE_CS = ['MDC_Cs', 'SEP_Cs', 'MJM_Cs', 'Stores_Cs', 'Bonded_Cs', 'Plants_Cs', 'OM_Cs', 'Allocated_Cs']
ONHAND_SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM']   # Allocated is shown separately
WAREHOUSE_SITES = ['MDC', 'SEP', 'MJM', 'Bonded']                          # replenishment-relevant stock
AVAIL_SITES = ['MDC']                                                        # bid availability = MDC stock
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
               'September', 'October', 'November', 'December']

today = pd.Timestamp.today().normalize()


# ----------------------------------------
# HELPERS
# ----------------------------------------
from contextlib import contextmanager
import traceback


@contextmanager
def guard():
    """Keep one tab's error from blanking every tab after it."""
    try:
        yield
    except Exception as e:
        st.error(f"This tab hit an error: {type(e).__name__}: {e}")
        st.code(traceback.format_exc())


def find_file(candidates):
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


def parse_mixed_dates(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str).str.split('.').str[0].replace({'nan': None, 'NaT': None, 'None': None, '': None})
    return pd.to_datetime(s, format='%Y%m%d', errors='coerce')


def clean_id(series):
    return series.fillna('').astype(str).str.replace(r'\.0$', '', regex=True).str.strip()


def num(series):
    return pd.to_numeric(series.astype(str).str.replace(',', '', regex=False), errors='coerce').fillna(0.0).astype(float)


def clean_txt(series):
    s = series.fillna('').astype(str).str.strip()
    bad = s.str.lower().str.startswith('usr-def') | s.isin(['', '-', 'nan', 'None'])
    return s.where(~bad, '')


def style0(df, one_dec=(), money=(), pct=(), money2=()):
    """0-decimal formatting for every numeric column; 1-decimal / $ / % overrides as listed."""
    f = {c: "{:,.0f}" for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])}
    for c in one_dec: f[c] = "{:,.1f}"
    for c in money: f[c] = "${:,.0f}"
    for c in money2: f[c] = "${:,.2f}"
    for c in pct: f[c] = "{:,.0f}%"
    f = {k: v for k, v in f.items() if k in df.columns}
    return df.style.format(f, na_rep="")


def csv_button(df, label, filename, key):
    st.download_button(label, df.to_csv(index=False).encode('utf-8'), file_name=filename, mime='text/csv', key=key)


def join_unique(s, n=3):
    u = [str(x) for x in pd.unique(s.dropna())]
    return ', '.join(u[:n]) + (f' +{len(u) - n}' if len(u) > n else '')


# ----------------------------------------
# DATA LOADING
# ----------------------------------------
@st.cache_data
def load_data():
    # ---------------- PURCHASE REPORT / SALES ANALYSIS ----------------
    sales = pd.DataFrame()
    try:
        path = find_file(["Purchase Report Sales Analysis with Raw Depletions.csv",
                          "Purchase_Report_Sales_Analysis_with_Raw_Depletions.csv"])
        sales = pd.read_csv(path, encoding='utf-8-sig', low_memory=False)
        sales.columns = [c.replace('\ufeff', '').strip() for c in sales.columns]
        sales['Item Number'] = clean_id(sales['Item Number'])
        sales = sales[sales['Item Number'].str.fullmatch(r'\d+', na=False)]          # drops '-', FREIGHT, blank total row
        sales = sales[sales['Item Description'].notna()]
        sales = sales[~sales['Item Description'].astype(str).str.contains('SAMPLE', case=False, na=False)]
        sales = sales.drop_duplicates('Item Number').copy()

        sales['Item Status'] = pd.to_numeric(sales['Item Status'], errors='coerce').fillna(0).astype(int)
        sales['Custom Category'] = sales['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
        sales['Item Category'] = clean_txt(sales['Category']).replace('', 'UNCATEGORIZED')   # Purchase Report col AB
        sales['Fmt'] = clean_txt(sales['Format'])
        sales['Flv'] = clean_txt(sales['Flavor'])

        cf = num(sales['Conversion Factor']).replace(0, 1)
        sales['Conversion Factor'] = cf
        for s_ in SITES:
            sales[s_] = num(sales[s_])
        sales['Allocated_Cs'] = sales['Allocated quantity'] / cf
        for s_ in ONHAND_SITES:
            sales[f'{s_}_Cs'] = sales[s_] / cf
        sales['Total_Cases'] = sales[[f'{s_}_Cs' for s_ in ONHAND_SITES]].sum(axis=1).clip(lower=0)
        sales['WH_Cases'] = sales[[f'{s_}_Cs' for s_ in WAREHOUSE_SITES]].sum(axis=1).clip(lower=0)
        sales['Avail_Cases'] = sales[[f'{s_}_Cs' for s_ in AVAIL_SITES]].sum(axis=1).clip(lower=0)

        # Inventory Value is a TOTAL $ figure -> apportion to warehouse by unit share
        sales['Inventory Value'] = num(sales['Inventory Value'])
        tot_q = sales[ONHAND_SITES].clip(lower=0).sum(axis=1)
        wh_q = sales[WAREHOUSE_SITES].clip(lower=0).sum(axis=1)
        sales['WH Value'] = sales['Inventory Value'] * np.where(tot_q > 0, wh_q / tot_q.replace(0, 1), 1.0)

        for c in ['Last Price', 'On Order']:
            if c in sales.columns:
                sales[c] = num(sales[c])
        dep_cols = [c for c in sales.columns if c.endswith('Depletion')]
        for c in dep_cols:
            sales[c] = num(sales[c])
        recent = dep_cols[-3:]
        # Depletions are reported in BASE UOM (EA); verified against the forecast (already in cases) —
        # dividing by the Conversion Factor makes depletion/forecast ratios consistent across pack sizes.
        sales['3M_Avg_Depletion'] = (sales[recent].mean(axis=1) / cf) if recent else 0.0
        sales['3M_Avg_Depletion_Demand'] = sales['3M_Avg_Depletion'].clip(lower=0)
        lt = num(sales['Lead time']).replace(0, np.nan)
        sales['Lead time'] = lt.fillna(30)
    except Exception as e:
        st.error(f"Error loading Purchase Report / Sales Analysis file: {e}")
        sales = pd.DataFrame()

    # ---------------- PO DATES ----------------
    po = pd.DataFrame()
    try:
        po = pd.read_excel(find_file(["PO Dates.xlsx", "PO_Dates.xlsx"]), sheet_name="GIT Report")
        po = po[po['Hst'].notna()].copy()
        po['Status Code'] = pd.to_numeric(po['Hst'], errors='coerce').fillna(0)
        po['Otp'] = po['Otp'].astype(str).str.strip()
        po['Item number'] = clean_id(po['Item number'])
        po['Custom Category'] = po['Item grp'].map(CAT_MAP).fillna('UNCLASSIFIED')
        po['Buyer'] = po['Buyer'].fillna('Unassigned')
        po['Supplier'] = po['Supplier'].fillna('Unknown Supplier')
        po['ItemDescription'] = po['ItemDescription'].fillna('')
        for c in ['USD $', 'Order qty', 'Recd qty', 'Purch price', 'Line amount']:
            po[c] = num(po[c])

        if not sales.empty:
            lk = sales[['Item Number', 'Conversion Factor', 'Item Status', 'Item Category']].rename(columns={'Item Number': '_k'})
            po = po.merge(lk, left_on='Item number', right_on='_k', how='left').drop(columns='_k')
        else:
            po['Conversion Factor'], po['Item Status'], po['Item Category'] = 1, np.nan, 'UNCATEGORIZED'
        po['Conversion Factor'] = po['Conversion Factor'].fillna(1).replace(0, 1)
        po['Item Category'] = po['Item Category'].fillna('UNCATEGORIZED')

        # Case conversion: EA-priced lines divide by conversion factor; CS (and KG/LB/GAL) lines are used as-is
        per_case = np.where(po['POu'].astype(str).str.upper().eq('EA'), 1 / po['Conversion Factor'], 1.0)
        po['Order Qty (Cases)'] = po['Order qty'] * per_case
        po['Recd Qty (Cases)'] = po['Recd qty'] * per_case

        # FX (file already carries USD $ per line; derive the implied rate by currency for unit prices)
        ratio = (po['USD $'] / po['Line amount']).replace([np.inf, -np.inf], np.nan)
        fx = ratio.groupby(po['Curr']).median()
        po['FX'] = po['Curr'].map(fx).fillna(1.0)
        po['Price USD'] = po['Purch price'] * po['FX']

        po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
        po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
        po['Arrival'] = parse_mixed_dates(po['Arrival'])
        po['ETA'] = parse_mixed_dates(po['ETA'])
        po['Recd dt'] = parse_mixed_dates(po['Rec dt'])
        po['Stripped Date'] = parse_mixed_dates(po['Stripped'])
        po['Yard Date'] = parse_mixed_dates(po['Yard'])
        po['Order Month'] = po['Ord dt'].dt.strftime('%Y-%m').fillna('NaT')
        po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)
        po['Recd Month'] = po['Recd dt'].dt.strftime('%Y-%m').fillna('NaT')
        po['Var Days'] = (po['Recd dt'] - po['Req dt']).dt.days       # received vs requested date

        po['Delivery Status'] = 'Not Tracked'
        in_transit = po['Status'].isin(TRANSIT_STATUSES)
        open_nr = po['Recd dt'].isna() & (po['Status Code'] <= 49)
        po.loc[in_transit & open_nr, 'Delivery Status'] = 'On Track'
        po.loc[in_transit & open_nr & (po['Req dt'] < today), 'Delivery Status'] = 'Late (Past Req Date)'
        po.loc[po['Status'] == '6-Stripped', 'Delivery Status'] = 'Stripped'
        po.loc[po['Recd dt'].notna() | (po['Status Code'] > 49), 'Delivery Status'] = 'Received'
    except Exception as e:
        st.error(f"Error loading PO Dates file: {e}")
        po = pd.DataFrame()

    # ---------------- FORECAST ----------------
    fc, fc_meta = pd.DataFrame(), {}
    try:
        fc = pd.read_excel(find_file(["CPJ FORECAST.xlsx", "CPJ_FORECAST.xlsx"]), sheet_name="Sept - Mar FCST")
        fc['Item Code'] = clean_id(fc['Item Code'])
        mcols = [c for c in fc.columns if str(c) in MONTH_NAMES]
        for c in mcols:
            fc[c] = num(fc[c]).round(0)
        nxt = MONTH_NAMES[today.month % 12]              # month after the current one
        start = mcols.index(nxt) if nxt in mcols else 0
        cols3 = mcols[start:start + 3]
        if len(cols3) < 3:
            start, cols3 = 0, mcols[:3]
        fc['3M_Avg_Forecast_Cases'] = fc[cols3].mean(axis=1).round(0)
        fc_meta = {'cols6': mcols[start:start + 6], 'cols3': cols3, 'all': mcols}
    except Exception as e:
        st.error(f"Error loading CPJ Forecast file: {e}")
        fc = pd.DataFrame()

    # ---------------- BIDS ----------------
    bids = pd.DataFrame()
    try:
        bids = pd.read_excel(find_file(["BIDS.xlsx"]))
        bids.columns = [str(c).strip() for c in bids.columns]
        bids = bids.rename(columns={'CPJ Code': 'Item Code', 'CPJ Item Description': 'Item Description',
                                    'Customer Name': 'Customer', 'CPJ Class': 'Item Class'})
        bids['Item Code'] = clean_id(bids['Item Code'])
        bids['Custom Category'] = bids['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
        bids['Item Status'] = pd.to_numeric(bids['Item Status'], errors='coerce')
    except Exception:
        bids = pd.DataFrame()

    return sales, po, fc, fc_meta, bids


sales_df, po_raw, fc_df, fc_meta, bids_df = load_data()
if po_raw.empty or sales_df.empty:
    st.error("Core data (PO Dates / Purchase Report) failed to load — check the files are in the app's working directory.")
    st.stop()

# ----------------------------------------
# SIDEBAR / GLOBAL FILTERS
# ----------------------------------------
st.sidebar.header("Global Filters")
include_p01 = st.sidebar.checkbox("Include Otp = P01 orders", value=False,
                                  help="P01 is excluded from all spend/PO analytics by default.")
po_all = po_raw if include_p01 else po_raw[po_raw['Otp'] != 'P01']
po_df = po_all[po_all['Status Code'].between(20, 85)]                     # non-deleted PO lines

valid_years = sorted([int(y) for y in po_df['Order Year'].unique() if y >= 2020])
slicer_year = st.sidebar.multiselect("Order Year", valid_years, default=[2026] if 2026 in valid_years else valid_years[-1:])
clean_months = sorted([m for m in po_df['Order Month'].unique() if m not in ['NaT', 'nan', 'None']])
slicer_month = st.sidebar.multiselect("Order Month", clean_months)
slicer_supplier = st.sidebar.multiselect("Supplier", sorted(po_df['Supplier'].astype(str).unique()))
slicer_category = st.sidebar.multiselect("Custom Category", sorted(po_df['Custom Category'].astype(str).unique()))


def by_supplier_cat(df):
    if slicer_supplier: df = df[df['Supplier'].isin(slicer_supplier)]
    if slicer_category: df = df[df['Custom Category'].isin(slicer_category)]
    return df


def by_all_slicers(df):
    df = by_supplier_cat(df)
    if slicer_year: df = df[df['Order Year'].isin(slicer_year)]
    if slicer_month: df = df[df['Order Month'].isin(slicer_month)]
    return df


po_filtered = by_all_slicers(po_df)
po_active = po_filtered[po_filtered['Status Code'].between(20, 40)]
po_active_all = po_df[po_df['Status Code'].between(20, 40)]        # full open pipeline, not limited by year/month

last_buyer = (po_df.sort_values('Ord dt').groupby('Item number')['Buyer'].last())

# ----------------------------------------
# INVENTORY MASTER (sales + forecast + pipeline)
# ----------------------------------------
pipe_port = po_active_all[po_active_all['Status'].isin(PORT_STATUSES)].groupby('Item number')['Order Qty (Cases)'].sum()
pipe_ord = po_active_all[~po_active_all['Status'].isin(PORT_STATUSES)].groupby('Item number')['Order Qty (Cases)'].sum()
inv = sales_df.copy()
inv['Cases At Port'] = inv['Item Number'].map(pipe_port).fillna(0.0)
inv['Cases On Order'] = inv['Item Number'].map(pipe_ord).fillna(0.0)
inv['Cases Inbound'] = inv['Cases At Port'] + inv['Cases On Order']
if not fc_df.empty:
    inv['3M_Avg_Forecast_Cases'] = inv['Item Number'].map(fc_df.groupby('Item Code')['3M_Avg_Forecast_Cases'].sum()).fillna(0.0)
else:
    inv['3M_Avg_Forecast_Cases'] = 0.0
inv['Lead Time (Months)'] = inv['Lead time'] / 30
inv['Safety Stock (Cases)'] = inv['Lead Time (Months)'] * inv['3M_Avg_Depletion_Demand']
inv['Responsible Buyer'] = inv['Item Class'].map(BUYER_MAP).fillna(inv['Item Number'].map(last_buyer)).fillna('Unassigned')
inv['Is Live'] = inv['Item Status'].isin(LIVE_STATUSES)
inv_f = inv[inv['Custom Category'].isin(slicer_category)] if slicer_category else inv
inv_live = inv_f[inv_f['Is Live']]


def add_sub_key(df, level):
    keys = ['Item Category', 'Fmt'] + (['Flv'] if level.startswith('Category + Format + Flavor') else [])
    key = df[keys].astype(str).agg(' | '.join, axis=1)
    valid = (df['Item Category'] != 'UNCATEGORIZED') & (df['Fmt'] != '')
    return key.where(valid, '')


tabs = st.tabs(["📊 Executive Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", "🏆 Bid Performance",
                "📈 Forecast", "🤝 Supplier Scorecard", "💵 Cash Flow", "📉 Price Index (PPV)"])

# ==========================================================================
# TAB 1 — EXECUTIVE SPEND
# ==========================================================================
with tabs[0], guard():
    st.header("Executive Summary: Category & Buyer Spend")
    st.caption("Otp = P01 orders are excluded." if not include_p01 else "Otp = P01 orders are INCLUDED (sidebar toggle).")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total PO Spend", f"${po_filtered['USD $'].sum():,.0f}")
    c2.metric("Total Open PO Spend", f"${po_active['USD $'].sum():,.0f}")
    c3.metric("Total Open PO Volume (Cases)", f"{po_active['Order Qty (Cases)'].sum():,.0f}")
    c4.metric("Current Warehouse Inventory", f"${inv_f['WH Value'].sum():,.0f}")
    st.divider()

    r1a, r1b = st.columns(2)
    with r1a:
        st.subheader("PO Volume (Cases) by Month")
        if not po_filtered.empty:
            v = po_filtered.groupby('Order Month')['Order Qty (Cases)'].sum().reset_index().sort_values('Order Month')
            v['3M Avg'] = v['Order Qty (Cases)'].rolling(3).mean()
            fig = px.bar(v, x='Order Month', y='Order Qty (Cases)', text_auto=',.0f')
            fig.add_trace(go.Scatter(x=v['Order Month'], y=v['3M Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
            st.plotly_chart(fig, use_container_width=True)
    with r1b:
        st.subheader("Total Spend by Custom Category")
        if not po_filtered.empty:
            cs = pd.DataFrame({'Total Spend': po_filtered.groupby('Custom Category')['USD $'].sum(),
                               'Open PO Spend': po_active.groupby('Custom Category')['USD $'].sum()}
                              ).fillna(0).rename_axis('Custom Category').reset_index().sort_values('Total Spend')
            fig = go.Figure()
            fig.add_trace(go.Bar(y=cs['Custom Category'], x=cs['Total Spend'], name='Total Spend', orientation='h',
                                 marker_color='#1f77b4', text=[f"${x:,.0f}" for x in cs['Total Spend']], textposition='outside'))
            fig.add_trace(go.Bar(y=cs['Custom Category'], x=cs['Open PO Spend'], name='Open PO Spend', orientation='h',
                                 marker_color='#ff7f0e', text=[f"${x:,.0f}" for x in cs['Open PO Spend']], textposition='outside'))
            fig.update_layout(barmode='group', height=520, xaxis_title="USD", margin=dict(r=90))
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("PO Spend by Month — 2025 to Current")
    st_base = po_df if not slicer_category else po_df[po_df['Custom Category'].isin(slicer_category)]
    trend = st_base[st_base['Order Year'] >= 2025]
    if not trend.empty:
        sp = trend.groupby('Order Month')['USD $'].sum().reset_index().sort_values('Order Month')
        sp['3M Avg'] = sp['USD $'].rolling(3).mean()
        fig = px.bar(sp, x='Order Month', y='USD $', text_auto='$,.0f')
        fig.add_trace(go.Scatter(x=sp['Order Month'], y=sp['3M Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Spend by Buyer")
    st.caption("Current open POs (Hst 20-40). Buyers under 5% of open spend are grouped as 'Other Buyers'.")
    if not po_active.empty and po_active['USD $'].sum() > 0:
        b = po_active.groupby('Buyer')['USD $'].sum().reset_index()
        b.loc[b['USD $'] / b['USD $'].sum() < 0.05, 'Buyer'] = 'Other Buyers'
        b = b.groupby('Buyer')['USD $'].sum().reset_index()
        fig = px.pie(b, values='USD $', names='Buyer', hole=0.4)
        fig.update_traces(textposition='inside', texttemplate="%{label}<br>$%{value:,.0f}<br>%{percent}")
        fig.update_layout(height=600)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No open PO spend for the selected filters.")

# ==========================================================================
# TAB 2 — GIT & EXPEDITING
# ==========================================================================
with tabs[1], guard():
    st.header("Goods In Transit: Clearance Tracking & Exceptions")

    st.subheader("Clearance Metrics: Stripped vs Cleared (Yard) by Month")
    if not po_filtered.empty:
        g = po_filtered.copy()
        g['Stripped Month'] = g['Stripped Date'].dt.strftime('%Y-%m').fillna('NaT')
        g['Yard Month'] = g['Yard Date'].dt.strftime('%Y-%m').fillna('NaT')
        s_c = g[g['Stripped Month'] != 'NaT'].groupby('Stripped Month')['Container Numb'].nunique().rename('Stripped')
        y_c = g[g['Yard Month'] != 'NaT'].groupby('Yard Month')['Container Numb'].nunique().rename('Cleared (Yard)')
        cm = pd.concat([s_c, y_c], axis=1).fillna(0).astype(int).sort_index().reset_index(names='Month')
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cm['Month'], y=cm['Stripped'], name='Stripped', marker_color='royalblue', text=cm['Stripped']))
        fig.add_trace(go.Bar(x=cm['Month'], y=cm['Cleared (Yard)'], name='Cleared (Yard)', marker_color='darkorange', text=cm['Cleared (Yard)']))
        fig.add_trace(go.Scatter(x=cm['Month'], y=cm['Stripped'], mode='lines', name='Stripped Trend', line=dict(color='blue', dash='dot')))
        fig.add_trace(go.Scatter(x=cm['Month'], y=cm['Cleared (Yard)'], mode='lines', name='Cleared Trend', line=dict(color='orange', dash='dot')))
        fig.update_layout(barmode='group', xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

    # ---------------- Pending containers ----------------
    st.subheader("Pending Containers / Open Orders")
    git = by_supplier_cat(po_active_all)
    pipe_lines = git[git['Status'].isin(TRANSIT_STATUSES) & git['Recd dt'].isna()]
    nd = pipe_lines[pipe_lines['Status'] == '1-Not Departed']
    cont = pipe_lines[pipe_lines['Container Numb'].notna()].copy()
    cont['Container Numb'] = cont['Container Numb'].astype(str).str.strip()

    agg = pd.DataFrame()
    if not cont.empty:
        agg = cont.groupby('Container Numb').agg(
            Status=('Status', 'first'), Buyer=('Buyer', join_unique), Supplier=('Supplier', join_unique),
            Description=('Description', join_unique), POs=('PO no', 'nunique'), USD=('USD $', 'sum'),
            Cases=('Order Qty (Cases)', 'sum'), Req=('Req dt', 'min'), ETA=('ETA', 'min'), Port=('Port of Disp', 'first')
        ).reset_index()

        def _contents(g):
            t = g.groupby('ItemDescription')['USD $'].sum().sort_values(ascending=False)
            return '; '.join(t.index[:3]) + (f' (+{len(t) - 3} more)' if len(t) > 3 else '')
        cmap = {k: _contents(gr) for k, gr in cont.groupby('Container Numb')}
        agg['Contents'] = agg['Container Numb'].map(cmap)
        past = agg['Req'] < today
        early_leg = agg['Status'].isin(['1-Not Departed', '2-On the Water'])
        agg['Timing'] = np.where(past, 'Past Req Date',
                                 np.where(early_leg & agg['ETA'].notna() & (agg['ETA'] < today), 'Past ETA', 'On Track'))
        agg['Days Past Req'] = np.where(past, (today - agg['Req']).dt.days, 0)
        agg['Rank'] = agg['Status'].map(STATUS_RANK)

    # KPI strip
    k1, k2, k3, k4 = st.columns(4)
    if not agg.empty:
        port_c = agg[agg['Status'].isin(PORT_STATUSES)]
        wat_c = agg[agg['Status'] == '2-On the Water']
        k1.metric("Containers at Port / Yard", f"{len(port_c):,}", f"{(port_c['Timing'] == 'Past Req Date').sum():,} past Req dt", delta_color="inverse")
        k2.metric("On the Water", f"{len(wat_c):,}", f"{(wat_c['Timing'] == 'On Track').sum():,} on track", delta_color="normal")
        k3.metric("Open $ in Numbered Containers", f"${agg['USD'].sum():,.0f}")
    else:
        k1.metric("Containers at Port / Yard", "0"); k2.metric("On the Water", "0"); k3.metric("Open $ in Numbered Containers", "$0")
    k4.metric("Not Departed (PO lines, no container yet)", f"{len(nd):,}",
              f"{(nd['Req dt'] < today).sum():,} past Req dt · ${nd['USD $'].sum():,.0f}", delta_color="off")

    if not agg.empty:
        fa, fb, fc_, fd = st.columns([2, 2, 2, 1])
        stat_sel = fa.multiselect("Container status", list(STATUS_RANK.keys())[:3], default=list(STATUS_RANK.keys())[:3])
        buyer_sel = fb.multiselect("Buyer", sorted(cont['Buyer'].unique()))
        text_sel = fc_.text_input("Search container / supplier / description")
        only_past = fd.checkbox("Only past Req dt", value=False)

        view = agg[agg['Status'].isin(stat_sel)]
        if buyer_sel:
            keep = set(cont[cont['Buyer'].isin(buyer_sel)]['Container Numb'])
            view = view[view['Container Numb'].isin(keep)]
        if text_sel:
            m = (view['Container Numb'].str.contains(text_sel, case=False, na=False) | view['Supplier'].str.contains(text_sel, case=False, na=False)
                 | view['Description'].astype(str).str.contains(text_sel, case=False, na=False) | view['Contents'].str.contains(text_sel, case=False, na=False))
            view = view[m]
        if only_past:
            view = view[view['Timing'] == 'Past Req Date']
        view = view.sort_values(['Rank', 'Req'])

        cc1, cc2 = st.columns([1, 2])
        with cc1:
            summ = view.groupby(['Status', 'Timing']).size().reset_index(name='Containers')
            if not summ.empty:
                fig = px.bar(summ, x='Status', y='Containers', color='Timing', text='Containers',
                             color_discrete_map={'On Track': '#2ca02c', 'Past ETA': '#ff7f0e', 'Past Req Date': '#d62728'},
                             category_orders={'Status': list(STATUS_RANK.keys())})
                fig.update_layout(height=380, title="Containers by stage & timing")
                st.plotly_chart(fig, use_container_width=True)
        with cc2:
            show = view[['Container Numb', 'Status', 'Timing', 'Buyer', 'Supplier', 'Description', 'Contents', 'POs',
                         'USD', 'Cases', 'Req', 'ETA', 'Days Past Req']].copy()
            show['Req'] = show['Req'].dt.strftime('%Y-%m-%d')
            show['ETA'] = show['ETA'].dt.strftime('%Y-%m-%d')
            show = show.rename(columns={'Req': 'Req dt', 'USD': 'Open USD'})
            red = (view['Timing'] == 'Past Req Date').values

            def _hl(row):
                i = show.index.get_loc(row.name)
                return ['background-color:#f8d7da;color:#842029' if red[i] else '' for _ in row]
            st.dataframe(style0(show.reset_index(drop=True), money=['Open USD']).apply(_hl, axis=1),
                         use_container_width=True, hide_index=True, height=380)
        st.caption("Red rows = Req dt has passed. Port / Yard containers are listed first, then On the Water; sorted by earliest Req dt. "
                   "Lines with no container number are summarized in the 'Not Departed' tile above, not listed.")
    else:
        st.info("No numbered pending containers for the selected filters.")

    # ---------------- Delivery performance ----------------
    st.divider()
    st.subheader("Vendor Delivery Performance — On Time vs Late")
    dp = by_supplier_cat(po_df)
    dp = dp[dp['Status Code'].isin([75, 85]) & dp['Recd dt'].notna() & dp['Req dt'].notna()].copy()
    d1, d2, d3 = st.columns(3)
    months_back = d1.slider("Received in last N months", 3, 24, 12)
    grace = d2.slider("On-time grace (days after Req dt)", 0, 7, 0)
    min_lines = d3.slider("Min lines per vendor (table)", 1, 50, 10)
    dp = dp[dp['Recd dt'] >= today - pd.DateOffset(months=months_back)]
    if not dp.empty:
        dp['Days Late'] = dp['Var Days'] - grace
        dp['Bucket'] = pd.cut(dp['Days Late'], bins=[-np.inf, 0, 7, 14, np.inf],
                              labels=['On time / early', '1-7 days late', '8-14 days late', '15+ days late']).astype(str)
        b_cols = {'On time / early': '#2ca02c', '1-7 days late': '#f2c94c', '8-14 days late': '#ff7f0e', '15+ days late': '#d62728'}
        on_pct = (dp['Days Late'] <= 0).mean() * 100
        late_only = dp[dp['Days Late'] > 0]
        m1, m2, m3 = st.columns(3)
        m1.metric("On-time lines", f"{on_pct:,.0f}%")
        m2.metric("Avg days late (late lines)", f"{late_only['Var Days'].mean():,.0f}" if len(late_only) else "0")
        m3.metric("Lines 15+ days late", f"{(dp['Bucket'] == '15+ days late').mean() * 100:,.0f}%")

        p1, p2 = st.columns(2)
        with p1:
            mo = dp.groupby(['Recd Month', 'Bucket']).size().reset_index(name='Lines')
            fig = px.bar(mo, x='Recd Month', y='Lines', color='Bucket', color_discrete_map=b_cols,
                         category_orders={'Bucket': list(b_cols.keys())}, title="Delivery timing mix by month received (% of lines)")
            fig.update_layout(barnorm='percent', yaxis_title="% of lines", xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
        with p2:
            cat = dp.groupby('Custom Category').agg(Lines=('PO no', 'size'), OnTime=('Days Late', lambda s: (s <= 0).mean() * 100)).reset_index().sort_values('OnTime')
            fig = px.bar(cat, x='OnTime', y='Custom Category', orientation='h', text=cat['OnTime'].map(lambda v: f"{v:,.0f}%"),
                         color='OnTime', color_continuous_scale='RdYlGn', range_color=[50, 100], title="On-time % by category")
            fig.update_layout(coloraxis_showscale=False, xaxis_title="On-time %")
            st.plotly_chart(fig, use_container_width=True)

        vt = dp.groupby('Supplier').agg(Lines=('PO no', 'size'), OnTime=('Days Late', lambda s: (s <= 0).mean() * 100),
                                        AvgLate=('Var Days', lambda s: s[s > grace].mean()),
                                        Pct15=('Bucket', lambda s: (s == '15+ days late').mean() * 100)).reset_index()
        vt = vt[vt['Lines'] >= min_lines].sort_values('OnTime')
        vt.columns = ['Supplier', 'Lines', 'On-time %', 'Avg days late (when late)', '% lines 15+ days late']
        st.dataframe(style0(vt, pct=['On-time %', '% lines 15+ days late']), use_container_width=True, hide_index=True, height=350)
    else:
        st.info("No received lines with both a Req dt and Rec dt in this window.")

    st.subheader("Open Orders Already Past Req Dt — Aging by Category")
    ov = by_supplier_cat(po_active_all).copy()
    ov = ov[ov['Req dt'] < today]
    if not ov.empty:
        ov['Days Past'] = (today - ov['Req dt']).dt.days
        ov['Aging'] = pd.cut(ov['Days Past'], bins=[0, 7, 30, 90, np.inf], labels=['1-7 days', '8-30 days', '31-90 days', '90+ days']).astype(str)
        ag = ov.groupby(['Custom Category', 'Aging'])['USD $'].sum().reset_index()
        fig = px.bar(ag, x='Custom Category', y='USD $', color='Aging', text_auto='$,.0f',
                     color_discrete_map={'1-7 days': '#f2c94c', '8-30 days': '#ff7f0e', '31-90 days': '#d62728', '90+ days': '#7f0000'})
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No open orders are past their Req dt.")

# ==========================================================================
# TAB 3 — INVENTORY HEALTH
# ==========================================================================
with tabs[2], guard():
    st.header("Inventory Health: Turnover, Dead Stock & Slow Moving")

    st.subheader("Inventory Turnover & Sell-Through by Category")
    st.caption("Every site quantity is converted to cases with the Purchase Report Conversion Factor. Total On-Hand = MDC + SEP + MJM + Stores + Bonded + Plants + OM; "
               "Allocated is shown separately (not added). Turnover = (3-month avg monthly depletion × 12) ÷ Total On-Hand cases. Deleted items (90-99) excluded.")
    tv_src = inv_f[~inv_f['Item Status'].isin(DELETED)]
    tv = tv_src.groupby('Custom Category')[SITE_CS + ['Total_Cases', '3M_Avg_Depletion_Demand']].sum().reset_index()
    tv['Annualized Turnover (x)'] = np.where(tv['Total_Cases'] > 0, tv['3M_Avg_Depletion_Demand'] * 12 / tv['Total_Cases'].replace(0, 1), 0)
    tv['Days on Hand'] = np.where(tv['3M_Avg_Depletion_Demand'] > 0, tv['Total_Cases'] / (tv['3M_Avg_Depletion_Demand'].replace(0, 1) * 12 / 365), np.nan)
    tv['Sell-Through %'] = np.where((tv['3M_Avg_Depletion_Demand'] + tv['Total_Cases']) > 0,
                                    tv['3M_Avg_Depletion_Demand'] / (tv['3M_Avg_Depletion_Demand'] + tv['Total_Cases']).replace(0, 1) * 100, 0)
    tv = tv.sort_values('Annualized Turnover (x)', ascending=False)
    tv = tv.rename(columns={**{c: c.replace('_Cs', '') for c in SITE_CS}, 'Total_Cases': 'Total On-Hand (Cs)',
                            '3M_Avg_Depletion_Demand': 'Avg Monthly Depletion (Cs)'})
    t1, t2 = st.columns(2)
    with t1:
        fig = px.bar(tv, x='Custom Category', y='Annualized Turnover (x)', text_auto=',.0f', title="Annualized turnover (x)")
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
    with t2:
        site_long = tv.melt(id_vars='Custom Category', value_vars=['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM'], var_name='Site', value_name='Cases')
        fig = px.bar(site_long, x='Custom Category', y='Cases', color='Site', title="Where the cases sit (by site)")
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(style0(tv, pct=['Sell-Through %']), use_container_width=True, hide_index=True)

    # ---------------- Dead & slow ----------------
    ds_src = inv_f[~inv_f['Item Status'].isin(DELETED + ([] if DEAD_STOCK_INCLUDES_DISCONTINUED else DISCONTINUED))]
    ds_src = ds_src[~ds_src['Custom Category'].isin(EXCLUDED_CATS)]
    r3a, r3b = st.columns(2)
    with r3a:
        st.subheader("Dead Stock by Category")
        st.caption("Inventory value on hand with no movement over the trailing 3 months (includes discontinued items — that is where dead stock usually sits).")
        dead = ds_src[(ds_src['Inventory Value'] > 100) & (ds_src['3M_Avg_Depletion'] <= 0)]
        if not dead.empty:
            dc = dead.groupby('Custom Category')['Inventory Value'].sum().reset_index()
            fig = px.bar(dc, x='Custom Category', y='Inventory Value', text_auto='$,.0f')
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No dead stock identified.")
    with r3b:
        st.subheader("🐢 Slow Moving Stock by Category")
        st.caption("Actual depletion below 70% of forecast (active items only).")
        sl = inv_live[~inv_live['Custom Category'].isin(EXCLUDED_CATS)]
        sl = sl[(sl['WH Value'] > 0) & (sl['3M_Avg_Forecast_Cases'] > 0)]
        sl = sl[sl['3M_Avg_Depletion'] / sl['3M_Avg_Forecast_Cases'] < 0.70]
        if not sl.empty:
            sc_ = sl.groupby('Custom Category')['WH Value'].sum().reset_index()
            fig = px.bar(sc_, x='Custom Category', y='WH Value', text_auto='$,.0f')
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No slow moving stock identified.")

    # ---------------- Safety stock ----------------
    st.divider()
    st.subheader("Safety Stock vs. Current Stock Levels")
    st.caption("Safety Stock = (Lead Time ÷ 30) × 3-month avg depletion (no safety-stock field exists in the source files). Stock = warehouse cases (MDC+SEP+MJM+Bonded). Active / new items only.")
    ss = inv_live[(inv_live['Safety Stock (Cases)'] > 0) & (~inv_live['Custom Category'].isin(['OPERATIONS', 'BEVERAGE SYSTEM']))].copy()
    ss['Below SS'] = ss['WH_Cases'] <= ss['Safety Stock (Cases)']
    ss['Inbound Status'] = np.where(ss['Cases At Port'] > 0, 'Stock at port / yard',
                             np.where(ss['Cases On Order'] > 0, 'On order (not yet at port)', 'Nothing inbound'))
    below = ss[ss['Below SS']].copy()
    if not below.empty:
        st.markdown("**Items at or below safety stock, by category — and whether replenishment is already coming**")
        cnt = below.groupby(['Custom Category', 'Inbound Status']).size().reset_index(name='Items')
        fig = px.bar(cnt, x='Custom Category', y='Items', color='Inbound Status', text='Items',
                     color_discrete_map={'Nothing inbound': '#d62728', 'On order (not yet at port)': '#f2c94c', 'Stock at port / yard': '#2ca02c'},
                     category_orders={'Inbound Status': ['Nothing inbound', 'On order (not yet at port)', 'Stock at port / yard']})
        fig.update_layout(xaxis_tickangle=-45, yaxis_title="# items at/below safety stock")
        st.plotly_chart(fig, use_container_width=True)
        tot = ss.groupby('Custom Category').size().rename('Active items').reset_index()
        bl = below.groupby('Custom Category').size().rename('At/below SS').reset_index()
        nothing = below[below['Inbound Status'] == 'Nothing inbound'].groupby('Custom Category').size().rename('Nothing inbound').reset_index()
        cs_tab = tot.merge(bl, how='left').merge(nothing, how='left').fillna(0)
        cs_tab['% at/below SS'] = cs_tab['At/below SS'] / cs_tab['Active items'] * 100
        st.dataframe(style0(cs_tab.sort_values('At/below SS', ascending=False), pct=['% at/below SS']), use_container_width=True, hide_index=True)

        st.markdown("**25 items furthest below safety stock** — bar = % of safety stock covered (stock, then what is at port, then on order). Dashed line = 100% of safety stock.")
        worst = below.assign(Cov=below['WH_Cases'] / below['Safety Stock (Cases)']).sort_values('Cov').head(25).iloc[::-1]
        ssv = worst['Safety Stock (Cases)']
        lab = worst['Item Number'] + ' ' + worst['Item Description'].str.slice(0, 32)
        fig = go.Figure()
        for nm, col, colr in [('On hand', 'WH_Cases', '#1f77b4'), ('At port / yard', 'Cases At Port', '#2ca02c'), ('On order', 'Cases On Order', '#f2c94c')]:
            fig.add_trace(go.Bar(y=lab, x=(worst[col] / ssv * 100).clip(upper=300), name=nm, orientation='h', marker_color=colr,
                                 customdata=worst[col], hovertemplate=nm + ": %{customdata:,.0f} cases<br>%{x:,.0f}% of safety stock<extra></extra>"))
        fig.add_vline(x=100, line_dash='dash', line_color='red')
        fig.update_layout(barmode='stack', height=680, xaxis_title="% of safety stock (capped at 300%)")
        st.plotly_chart(fig, use_container_width=True)
        det = below.sort_values('WH_Cases')[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases At Port', 'Cases On Order',
                                             'Safety Stock (Cases)', 'Lead Time (Months)', 'Inbound Status', 'Responsible Buyer']]
        det = det.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)'})
        st.dataframe(style0(det, one_dec=['Lead Time (Months)']), use_container_width=True, hide_index=True, height=350)
    else:
        st.info("No items currently at or below their computed safety stock.")

    st.subheader("Safety Stock Alerts — Significant Forecast Changes")
    st.caption("Near-term forecast average vs trailing actual depletion differs by more than 30% — buyers should reassess safety stock.")
    fch = inv_live[inv_live['3M_Avg_Depletion_Demand'] > 0].copy()
    fch['Forecast Change %'] = (fch['3M_Avg_Forecast_Cases'] - fch['3M_Avg_Depletion_Demand']) / fch['3M_Avg_Depletion_Demand'] * 100
    sig = fch[fch['Forecast Change %'].abs() > 30].sort_values('Forecast Change %', ascending=False)
    if not sig.empty:
        sig = sig[['Item Number', 'Item Description', 'Custom Category', '3M_Avg_Depletion_Demand', '3M_Avg_Forecast_Cases', 'Forecast Change %', 'Safety Stock (Cases)']]
        sig = sig.rename(columns={'3M_Avg_Depletion_Demand': 'Recent Avg Depletion (Cs)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cs)'})
        st.dataframe(style0(sig).format({"Forecast Change %": "{:+,.0f}%"}), use_container_width=True, hide_index=True, height=350)
    else:
        st.info("No items with a significant forecast shift.")

    st.divider()
    st.subheader("Reorder Exception Summary")
    st.caption("On-hand warehouse stock + everything on order won't outlast the item's lead time against the near-term forecast.")
    rb = inv_live[inv_live['3M_Avg_Forecast_Cases'] > 0].copy()
    rb['Months Cover (On Hand + Pipeline)'] = (rb['WH_Cases'] + rb['Cases Inbound']) / rb['3M_Avg_Forecast_Cases']
    rf = rb[rb['Months Cover (On Hand + Pipeline)'] < rb['Lead Time (Months)']].sort_values('Months Cover (On Hand + Pipeline)')
    if not rf.empty:
        rf = rf[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases Inbound', '3M_Avg_Forecast_Cases', 'Lead Time (Months)',
                 'Months Cover (On Hand + Pipeline)', 'Responsible Buyer']]
        rf = rf.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cs)'})
        st.dataframe(style0(rf, one_dec=['Lead Time (Months)', 'Months Cover (On Hand + Pipeline)']), use_container_width=True, hide_index=True, height=400)
        st.caption(f"{len(rf):,} items flagged.")
    else:
        st.info("No items currently flagged for reorder.")

    # ---------------- Item replacement / substitutes ----------------
    st.divider()
    st.subheader("Item Replacement & Substitute Item Performance")
    st.caption("Items are treated as substitutes when they share the same item Category + Format (+ Flavor for the stricter option) in the Purchase Report — "
               "e.g. the four BAKING / POWDER items. Only active (status 20) items count as usable substitutes; stock = MDC cases.")
    sub_level = st.radio("Substitute match level", ["Category + Format + Flavor (tighter)", "Category + Format (broader)"], horizontal=True)
    pool = inv.copy()
    pool['Sub Key'] = add_sub_key(pool, sub_level)
    act = pool[(pool['Item Status'] == 20) & (pool['Sub Key'] != '')]
    if slicer_category:
        act_scope = act[act['Custom Category'].isin(slicer_category)]
    else:
        act_scope = act
    gsize = act.groupby('Sub Key')['Item Number'].transform('size')
    act = act[gsize >= 2]
    act_scope = act_scope[act_scope['Sub Key'].isin(act['Sub Key'])]

    oos = act_scope[(act_scope['Avail_Cases'] <= 0) & (act_scope['3M_Avg_Depletion_Demand'] > 0)]
    sibs = act[act['Avail_Cases'] > 0][['Sub Key', 'Item Number', 'Item Description', 'Avail_Cases', '3M_Avg_Depletion_Demand']]
    pr = oos[['Item Number', 'Item Description', 'Custom Category', 'Sub Key', '3M_Avg_Depletion_Demand', 'Responsible Buyer']].merge(
        sibs, on='Sub Key', suffixes=('', ' (Sub)'))
    pr = pr[pr['Item Number'] != pr['Item Number (Sub)']]
    if not pr.empty:
        pr = pr.sort_values('Avail_Cases', ascending=False)
        best = pr.groupby('Item Number').first().reset_index()
        n_subs = pr.groupby('Item Number').size().rename('# Subs In Stock').reset_index()
        best = best.merge(n_subs)
        best['Sub Sold Last 3M (avg Cs/mo)'] = best['3M_Avg_Depletion_Demand (Sub)']
        best['Flag'] = np.where(best['Sub Sold Last 3M (avg Cs/mo)'] <= 0, 'Sub in stock but NOT being sold', 'Sub selling')
        so = best[['Item Number', 'Item Description', 'Custom Category', '3M_Avg_Depletion_Demand', 'Item Number (Sub)', 'Item Description (Sub)',
                   'Avail_Cases', '# Subs In Stock', 'Sub Sold Last 3M (avg Cs/mo)', 'Flag', 'Responsible Buyer']]
        so = so.rename(columns={'3M_Avg_Depletion_Demand': 'Out-of-Stock Item Demand (Cs/mo)', 'Avail_Cases': 'Best Sub MDC Stock (Cs)',
                                'Item Number': 'Out-of-Stock Item', 'Item Description': 'Out-of-Stock Description',
                                'Item Number (Sub)': 'Best Sub Item', 'Item Description (Sub)': 'Best Sub Description'})
    else:
        so = pd.DataFrame()

    idle_i = act_scope[(act_scope['Avail_Cases'] > 0) & (act_scope['3M_Avg_Depletion_Demand'] <= 0)]
    gdem = act.groupby('Sub Key')['3M_Avg_Depletion_Demand'].sum()
    idle_i = idle_i.assign(**{'Group Demand (Cs/mo)': idle_i['Sub Key'].map(gdem)})
    idle_i = idle_i[idle_i['Group Demand (Cs/mo)'] > 0]
    idle_tab = idle_i[['Item Number', 'Item Description', 'Custom Category', 'Avail_Cases', 'Inventory Value', 'Group Demand (Cs/mo)', 'Responsible Buyer']].rename(
        columns={'Avail_Cases': 'MDC Stock (Cs)'}).sort_values('Inventory Value', ascending=False)

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Substitute groups (2+ active items)", f"{act['Sub Key'].nunique():,}")
    q2.metric("Out-of-stock selling items with a sub in stock", f"{so['Out-of-Stock Item'].nunique() if not so.empty else 0:,}")
    q3.metric("…where the sub is NOT being sold", f"{(so['Flag'] != 'Sub selling').sum() if not so.empty else 0:,}")
    q4.metric("Idle in-stock items (group is selling)", f"{len(idle_tab):,}", f"${idle_tab['Inventory Value'].sum():,.0f} on hand", delta_color="off")

    if not so.empty:
        st.markdown("**Out-of-stock items that have an active substitute in stock** — flag to Sales")
        cf_ = so.groupby(['Custom Category', 'Flag']).size().reset_index(name='Items')
        fig = px.bar(cf_, x='Custom Category', y='Items', color='Flag', text='Items',
                     color_discrete_map={'Sub in stock but NOT being sold': '#d62728', 'Sub selling': '#2ca02c'})
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(style0(so.sort_values(['Flag', 'Out-of-Stock Item Demand (Cs/mo)'], ascending=[False, False])), use_container_width=True, hide_index=True, height=350)
        csv_button(so, "⬇️ Export sub-available list for Sales", "substitute_opportunities.csv", "dl_sub_so")
    else:
        st.info("No out-of-stock selling items with an in-stock substitute at this match level.")
    if not idle_tab.empty:
        st.markdown("**In-stock items with no recent sales while their substitute group is selling**")
        st.dataframe(style0(idle_tab, money=['Inventory Value']), use_container_width=True, hide_index=True, height=300)
        csv_button(idle_tab, "⬇️ Export idle-substitute list for Sales", "idle_substitutes.csv", "dl_sub_idle")

# ==========================================================================
# TAB 4 — BID PERFORMANCE
# ==========================================================================
with tabs[3], guard():
    st.header("🏆 Bid Customer Fulfillment & Coverage")
    if bids_df.empty:
        st.info("Bid data not loaded.")
    else:
        st.caption("Availability uses MDC stock (cases). Items at status 50 / 80 (discontinued) and 90-99 (deleted) are not counted in the base availability view. "
                   "Discontinued bid codes appear only in the substitute-adjusted view and replacement table below.")
        bsub_level = st.radio("Substitute match level", ["Category + Format + Flavor (tighter)", "Category + Format (broader)"], horizontal=True, key="bid_sub_level")
        bc = bids_df[bids_df['Bid Status'] == 'Current']
        bi = bc.groupby('Item Code').agg(Description=('Item Description', 'first'), Bid_Class=('Item Class', 'first'),
                                         Bid_Category=('Custom Category', 'first'), Bid_Item_Status=('Item Status', 'first'),
                                         Customer_Count=('Customer', 'nunique')).reset_index()
        pool_b = inv.copy()
        pool_b['Sub Key'] = add_sub_key(pool_b, bsub_level)
        cols_b = ['Item Number', 'Item Status', 'Avail_Cases', 'Cases At Port', 'Cases On Order', '3M_Avg_Depletion_Demand', 'Sub Key', 'Responsible Buyer']
        bi = bi.merge(pool_b[cols_b], left_on='Item Code', right_on='Item Number', how='left')
        bi['Status'] = bi['Item Status'].fillna(bi['Bid_Item_Status']).fillna(0).astype(int)
        for c in ['Avail_Cases', 'Cases At Port', 'Cases On Order', '3M_Avg_Depletion_Demand']:
            bi[c] = bi[c].fillna(0.0)
        bi['Sub Key'] = bi['Sub Key'].fillna('')
        bi['Category'] = bi['Bid_Category']
        bi['Responsible Buyer'] = bi['Bid_Class'].map(BUYER_MAP).fillna(bi['Item Code'].map(last_buyer)).fillna('Unassigned')
        bi = bi[~bi['Status'].isin(DELETED)]
        bi['Discontinued'] = bi['Status'].isin(DISCONTINUED)

        # substitute candidates: active, in stock, >= MIN_SUB_MONTHS of cover
        cand = pool_b[(pool_b['Item Status'] == 20) & (pool_b['Sub Key'] != '') & (pool_b['Avail_Cases'] > 0)][
            ['Item Number', 'Item Description', 'Sub Key', 'Avail_Cases', '3M_Avg_Depletion_Demand']].rename(
            columns={'Item Number': 'Sub Item', 'Item Description': 'Sub Description', 'Avail_Cases': 'Sub MDC Stock (Cs)', '3M_Avg_Depletion_Demand': 'Sub Demand'})
        pairs = bi[bi['Sub Key'] != ''][['Item Code', 'Sub Key', '3M_Avg_Depletion_Demand']].merge(cand, on='Sub Key')
        pairs = pairs[pairs['Item Code'] != pairs['Sub Item']]
        ref = pairs[['3M_Avg_Depletion_Demand', 'Sub Demand']].max(axis=1)
        pairs['Sub Months'] = np.where(ref > 0, pairs['Sub MDC Stock (Cs)'] / ref.replace(0, 1), np.inf)
        pairs = pairs[pairs['Sub Months'] > MIN_SUB_MONTHS].sort_values('Sub MDC Stock (Cs)', ascending=False)
        best_sub = pairs.groupby('Item Code').first()[['Sub Item', 'Sub Description', 'Sub MDC Stock (Cs)', 'Sub Months']]
        bi = bi.merge(best_sub, left_on='Item Code', right_index=True, how='left')
        bi['Has Sub'] = bi['Sub Item'].notna()

        # ----- base availability (live items only)
        base = bi[~bi['Discontinued']].copy()
        base['Availability'] = 'Not In Stock'
        base.loc[base['Cases On Order'] > 0, 'Availability'] = 'On Order'
        base.loc[base['Cases At Port'] > 0, 'Availability'] = 'Inventory @ Port'
        base.loc[base['Avail_Cases'] > 0, 'Availability'] = 'Available'
        base['Months of Stock'] = np.where(base['3M_Avg_Depletion_Demand'] > 0, base['Avail_Cases'] / base['3M_Avg_Depletion_Demand'].replace(0, 1), np.nan)

        # ----- substitute-adjusted availability (live + discontinued bid codes that have a sub)
        adj = bi[(~bi['Discontinued']) | bi['Has Sub']].copy()
        adj['Availability'] = 'Not In Stock'
        adj.loc[adj['Cases On Order'] > 0, 'Availability'] = 'On Order'
        adj.loc[adj['Cases At Port'] > 0, 'Availability'] = 'Inventory @ Port'
        adj.loc[adj['Has Sub'], 'Availability'] = 'Available via Substitute'
        adj.loc[adj['Avail_Cases'] > 0, 'Availability'] = 'Available'
        adj.loc[adj['Discontinued'] & adj['Has Sub'], 'Availability'] = 'Available via Substitute'
        colors = {'Available': '#2ca02c', 'Available via Substitute': '#17becf', 'Inventory @ Port': '#ff7f0e', 'On Order': '#f2c94c', 'Not In Stock': '#d62728'}

        st.subheader("Bid Item Stock Coverage by Category")
        cov = base.groupby('Category').agg(Items=('Item Code', 'nunique'), MDC=('Avail_Cases', 'sum'), Port=('Cases At Port', 'sum'),
                                           Ord=('Cases On Order', 'sum'), Months=('Months of Stock', 'mean')).reset_index().sort_values('MDC', ascending=False)
        cov.columns = ['Category', 'Items', 'MDC Stock (Cases)', 'Cases at Port', 'Cases On Order', 'Avg Months of Stock']
        st.dataframe(style0(cov, one_dec=['Avg Months of Stock']), use_container_width=True, hide_index=True)

        pa, pb = st.columns(2)
        with pa:
            st.subheader("Overall Bid Item Availability")
            st.caption("Current conditions — the bid code itself must have stock, or stock incoming.")
            fig = px.pie(base, names='Availability', color='Availability', color_discrete_map=colors, title=f"{len(base):,} unique bid items")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            st.plotly_chart(fig, use_container_width=True)
        with pb:
            st.subheader("Availability incl. Substitutes / Replacements")
            st.caption(f"Adds active (status 20) substitutes with more than {MIN_SUB_MONTHS} months of MDC stock; discontinued bid codes count only when a substitute exists.")
            fig = px.pie(adj, names='Availability', color='Availability', color_discrete_map=colors, title=f"{len(adj):,} unique bid items")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            st.plotly_chart(fig, use_container_width=True)
        m_a, m_b, m_c = st.columns(3)
        av0 = (base['Availability'] == 'Available').mean() * 100
        av1 = adj['Availability'].isin(['Available', 'Available via Substitute']).mean() * 100
        m_a.metric("Item availability — bid codes only", f"{av0:,.0f}%")
        m_b.metric("Item availability — incl. substitutes", f"{av1:,.0f}%", f"{av1 - av0:+,.0f} pts", delta_color="normal")
        m_c.metric("Bid items rescued by a substitute", f"{(adj['Availability'] == 'Available via Substitute').sum():,}")

        st.subheader("Stock vs. Demand")
        cat_f = st.selectbox("Category focus", ['All'] + sorted(base['Category'].unique()))
        sc_src = base if cat_f == 'All' else base[base['Category'] == cat_f]
        v1, v2 = st.columns([3, 2])
        with v1:
            pts = sc_src[(sc_src['Avail_Cases'] > 0) & (sc_src['3M_Avg_Depletion_Demand'] > 0)]
            if not pts.empty:
                fig = px.scatter(pts, x='3M_Avg_Depletion_Demand', y='Avail_Cases', color='Availability', size='Customer_Count',
                                 hover_name='Description', hover_data={'Item Code': True, 'Category': True, 'Months of Stock': ':.1f', 'Customer_Count': True},
                                 log_x=True, log_y=True, color_discrete_map=colors,
                                 labels={'3M_Avg_Depletion_Demand': 'Avg monthly demand (cases)', 'Avail_Cases': 'MDC stock (cases)'},
                                 title="Each dot = a bid item. Below the red line = under 1 month of stock")
                xr = np.array([pts['3M_Avg_Depletion_Demand'].min(), pts['3M_Avg_Depletion_Demand'].max()])
                for mth, cl in [(0.5, '#d62728'), (1, '#ff7f0e'), (3, '#2ca02c')]:
                    fig.add_trace(go.Scatter(x=xr, y=xr * mth, mode='lines', name=f'{mth:g} mo cover', line=dict(dash='dash', color=cl, width=1)))
                fig.update_layout(height=520)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"{len(sc_src) - len(pts):,} items with zero stock or zero demand are not plotted (log scale) — see the at-risk table below.")
            else:
                st.info("No items with both stock and demand.")
        with v2:
            mc = base.groupby('Category').agg(S=('Avail_Cases', 'sum'), D=('3M_Avg_Depletion_Demand', 'sum')).reset_index()
            mc['Months of Cover'] = np.where(mc['D'] > 0, mc['S'] / mc['D'].replace(0, 1), np.nan)
            mc = mc.dropna().sort_values('Months of Cover')
            fig = px.bar(mc, x='Months of Cover', y='Category', orientation='h', text=mc['Months of Cover'].map(lambda v: f"{v:,.1f}"),
                         color='Months of Cover', color_continuous_scale='RdYlGn', range_color=[0, 3], title="Months of cover by category")
            fig.update_layout(coloraxis_showscale=False, height=520)
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("⚠️ At-Risk Bid Items — No Stock, Not On Order, Not At Port")
        risk = base[(base['Avail_Cases'] <= 0) & (base['Cases On Order'] <= 0) & (base['Cases At Port'] <= 0)]
        rk = risk[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Has Sub', 'Sub Item', 'Sub Description']].rename(
            columns={'Customer_Count': '# Bid Customers', 'Has Sub': 'Substitute Available'})
        st.dataframe(style0(rk), use_container_width=True, hide_index=True)
        st.caption(f"{len(risk):,} of {len(base):,} unique bid items are at risk; {int(risk['Has Sub'].sum()):,} of them have a usable substitute.")
        csv_button(rk, "⬇️ Export at-risk bid items", "at_risk_bid_items.csv", "dl_risk")

        st.subheader("Bid Items on Discontinued Codes — Replacement Needed")
        dtab = bi[bi['Discontinued']][['Item Code', 'Description', 'Status', 'Category', 'Customer_Count', 'Responsible Buyer', 'Sub Item', 'Sub Description', 'Sub MDC Stock (Cs)']]
        dtab = dtab.rename(columns={'Customer_Count': '# Bid Customers', 'Sub Item': 'Suggested Replacement', 'Sub Description': 'Replacement Description'})
        dtab['Suggested Replacement'] = dtab['Suggested Replacement'].fillna('No replacement found')
        st.dataframe(style0(dtab), use_container_width=True, hide_index=True)
        csv_button(dtab, "⬇️ Export discontinued bid items", "discontinued_bid_items.csv", "dl_disc")

        with st.expander("All Bid Item Coverage (detail)"):
            det = base[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Avail_Cases', 'Cases At Port', 'Cases On Order',
                        'Months of Stock', 'Availability']].rename(columns={'Customer_Count': '# Bid Customers', 'Avail_Cases': 'MDC Stock (Cs)'})
            st.dataframe(style0(det, one_dec=['Months of Stock']), use_container_width=True, hide_index=True)

# ==========================================================================
# TAB 5 — FORECAST
# ==========================================================================
with tabs[4], guard():
    st.header("6-Month Forecast")
    if fc_df.empty:
        st.info("Forecast data not loaded.")
    else:
        st.caption(f"Forecast window: {', '.join(fc_meta['cols6'])}. Near-term average = {', '.join(fc_meta['cols3'])}. Discontinued (50/80) and deleted items excluded.")
        q = st.text_input("🔍 Search Item Code or Description:", "")
        bad_codes = set(sales_df[sales_df['Item Status'].isin(DISCONTINUED + DELETED)]['Item Number'])
        fd = fc_df[~fc_df['Item Code'].isin(bad_codes)].copy()
        if q:
            fd = fd[fd['Item Code'].str.contains(q, case=False, na=False) | fd['Item Description'].astype(str).str.contains(q, case=False, na=False)]
        c6 = fc_meta['cols6']
        hv = fd[fd['3M_Avg_Forecast_Cases'] >= 10].sort_values('3M_Avg_Forecast_Cases', ascending=False)
        if not hv.empty:
            agg6 = hv[c6].sum().reset_index()
            agg6.columns = ['Month', 'Forecasted Cases']
            fig = px.bar(agg6, x='Month', y='Forecasted Cases', text_auto=',.0f', title='Forecast by month (items averaging ≥ 10 cases)')
            fig.update_traces(marker_color='darkmagenta')
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(style0(hv[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + c6].rename(columns={'3M_Avg_Forecast_Cases': 'Near-Term Avg (Cs)'})),
                         use_container_width=True, hide_index=True, height=350)
        else:
            st.info("No forecast items meeting the criteria (≥ 10 cases avg).")

        st.subheader("Category Forecast Coverage")
        st.caption("Warehouse stock (MDC+SEP+MJM+Bonded) and total inbound (on order + at port, full open pipeline) ÷ near-term forecast average, by category.")
        cv = fd.merge(inv[['Item Number', 'Custom Category', 'WH_Cases', 'Cases On Order', 'Cases At Port']], left_on='Item Code', right_on='Item Number', how='left')
        cv['Custom Category'] = cv['Custom Category'].fillna('UNCLASSIFIED')
        cv = cv[~cv['Custom Category'].isin(['OPERATIONS', 'BEVERAGE SYSTEM', 'UNCLASSIFIED'])]
        cs2 = cv.groupby('Custom Category').agg(Stock=('WH_Cases', 'sum'), Port=('Cases At Port', 'sum'), Ord=('Cases On Order', 'sum'),
                                                Fcst=('3M_Avg_Forecast_Cases', 'sum')).reset_index()
        cs2['Inbound'] = cs2['Port'] + cs2['Ord']
        cs2['Stock Coverage (Months)'] = np.where(cs2['Fcst'] > 0, cs2['Stock'] / cs2['Fcst'].replace(0, 1), 0)
        cs2['Coverage w/ Inbound (Months)'] = np.where(cs2['Fcst'] > 0, (cs2['Stock'] + cs2['Inbound']) / cs2['Fcst'].replace(0, 1), 0)
        target = st.slider("Target months of cover", 1, 6, 3)
        cs2 = cs2.sort_values('Coverage w/ Inbound (Months)')
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cs2['Custom Category'], y=cs2['Stock Coverage (Months)'], name='On-hand cover', marker_color='#1f77b4',
                             text=cs2['Stock Coverage (Months)'].map(lambda v: f"{v:,.1f}"), textposition='outside'))
        fig.add_trace(go.Bar(x=cs2['Custom Category'], y=cs2['Coverage w/ Inbound (Months)'], name='On-hand + inbound cover', marker_color='#2ca02c',
                             text=cs2['Coverage w/ Inbound (Months)'].map(lambda v: f"{v:,.1f}"), textposition='outside'))
        fig.add_hline(y=target, line_dash='dash', line_color='red', annotation_text=f"Target {target} mo")
        fig.update_layout(barmode='group', yaxis_title="Months of forecast covered", title="Stock coverage by category vs near-term forecast")
        st.plotly_chart(fig, use_container_width=True)
        out = cs2.rename(columns={'Custom Category': 'Category', 'Stock': 'Warehouse Stock (Cs)', 'Port': 'Cases at Port', 'Ord': 'Cases On Order',
                                  'Fcst': 'Near-Term Forecast (Cs)'}).drop(columns='Inbound')
        st.dataframe(style0(out, one_dec=['Stock Coverage (Months)', 'Coverage w/ Inbound (Months)']), use_container_width=True, hide_index=True)

# ==========================================================================
# TAB 6 — SUPPLIER SCORECARD
# ==========================================================================
with tabs[5], guard():
    st.header("Supplier Scorecard: Reliability, Fill & Timeliness")
    st.caption("Fill outcome per line: received/closed lines (Hst 75-85) use received ÷ ordered. Lines dropped to Hst 99 on a PO that still has live or closed lines "
               "(vendor didn't deliver, line was killed) count as 0% filled.")
    sc_all = by_supplier_cat(po_all)
    live_po = sc_all.groupby('PO no')['Status Code'].apply(lambda s: s.isin([20, 40, 75, 85]).any())
    fl = sc_all[(sc_all['Status Code'].isin([75, 85]) & (sc_all['Order qty'] > 0)) |
                ((sc_all['Status Code'] == 99) & sc_all['PO no'].map(live_po).fillna(False) & (sc_all['Order qty'] > 0))].copy()
    fl['Dropped'] = fl['Status Code'] == 99
    fl.loc[fl['Dropped'], ['Recd qty', 'Recd Qty (Cases)']] = 0
    fl['Fill %'] = fl['Recd qty'] / fl['Order qty'] * 100
    fl['Short'] = (fl['Fill %'] < 99.5) & ~fl['Dropped']
    fl['Deviation %'] = fl['Fill %'] - 100
    fl['Short USD'] = np.where(fl['Dropped'], fl['USD $'], np.where(fl['Short'], fl['USD $'] * (1 - fl['Fill %'] / 100), 0))
    if slicer_year: fl = fl[fl['Order Year'].isin(slicer_year)]
    if slicer_month: fl = fl[fl['Order Month'].isin(slicer_month)]

    if fl.empty:
        st.info("No completed / dropped lines for the selected filters.")
    else:
        k1, k2, k3, k4 = st.columns(4)
        fill_all = fl['Recd Qty (Cases)'].sum() / fl['Order Qty (Cases)'].sum() * 100 if fl['Order Qty (Cases)'].sum() > 0 else 0
        k1.metric("Overall fill rate (cases)", f"{fill_all:,.0f}%")
        k2.metric("Lines dropped to 99 (vendor didn't deliver)", f"{int(fl['Dropped'].sum()):,}", f"${fl.loc[fl['Dropped'], 'USD $'].sum():,.0f}", delta_color="off")
        k3.metric("Lines short-shipped", f"{int(fl['Short'].sum()):,}")
        k4.metric("Short $ (dropped + short)", f"${fl['Short USD'].sum():,.0f}")

        st.subheader("Where Vendors Fail to Deliver — Dropped & Short Lines by Category")
        pr_ = fl[fl['Dropped'] | fl['Short']].copy()
        pr_['Type'] = np.where(pr_['Dropped'], 'Dropped (Hst 99)', 'Short-shipped')
        if not pr_.empty:
            cc = pr_.groupby(['Custom Category', 'Type']).size().reset_index(name='Lines')
            fig = px.bar(cc, x='Custom Category', y='Lines', color='Type', text='Lines', color_discrete_map={'Dropped (Hst 99)': '#d62728', 'Short-shipped': '#ff7f0e'})
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)
            ic = pr_.groupby('Item Category').size().reset_index(name='Lines').sort_values('Lines', ascending=False).head(15)
            fig = px.bar(ic, x='Lines', y='Item Category', orientation='h', text='Lines', title="Top 15 item categories with dropped / short lines")
            fig.update_layout(yaxis={'categoryorder': 'total ascending'})
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Vendor Line Fill Detail")
        ms = st.slider("Min completed lines per vendor", 1, 100, 10, key="fill_min")
        vf = fl.groupby('Supplier').agg(Lines=('PO no', 'size'), Dropped=('Dropped', 'sum'), Short=('Short', 'sum'),
                                        Ord=('Order Qty (Cases)', 'sum'), Rec=('Recd Qty (Cases)', 'sum'), ShortUSD=('Short USD', 'sum')).reset_index()
        vf['Fill Rate %'] = np.where(vf['Ord'] > 0, vf['Rec'] / vf['Ord'].replace(0, 1) * 100, 0)
        vf['Dropped % of Lines'] = vf['Dropped'] / vf['Lines'] * 100
        vf['Short/Dropped % of Lines'] = (vf['Dropped'] + vf['Short']) / vf['Lines'] * 100
        vf = vf[vf['Lines'] >= ms].sort_values('Short/Dropped % of Lines', ascending=False)
        vf = vf.rename(columns={'Ord': 'Ordered (Cs)', 'Rec': 'Received (Cs)', 'ShortUSD': 'Short $'})
        st.dataframe(style0(vf, money=['Short $'], pct=['Fill Rate %', 'Dropped % of Lines', 'Short/Dropped % of Lines']),
                     use_container_width=True, hide_index=True, height=380)
        st.caption("High-SKU vendors (e.g. Dot Foods, KeHE) naturally show more dropped lines — compare the % columns as well as counts.")

        st.subheader("Fill Deviation Score — Month over Month")
        fm_src = by_supplier_cat(po_all)
        fm_src = fm_src[fm_src['Order Year'] >= 2025]
        fm = fm_src[(fm_src['Status Code'].isin([75, 85]) & (fm_src['Order qty'] > 0)) |
                    ((fm_src['Status Code'] == 99) & fm_src['PO no'].map(live_po).fillna(False) & (fm_src['Order qty'] > 0))].copy()
        fm.loc[fm['Status Code'] == 99, 'Recd Qty (Cases)'] = 0
        fm['Dropped'] = fm['Status Code'] == 99
        mo = fm.groupby('Order Month').agg(Ord=('Order Qty (Cases)', 'sum'), Rec=('Recd Qty (Cases)', 'sum'), Dropped=('Dropped', 'sum')).reset_index()
        mo = mo[mo['Order Month'] != 'NaT']
        mo['Fill Rate %'] = np.where(mo['Ord'] > 0, mo['Rec'] / mo['Ord'].replace(0, 1) * 100, np.nan)
        mo['Deviation (pts)'] = mo['Fill Rate %'] - 100
        fig = go.Figure()
        fig.add_trace(go.Bar(x=mo['Order Month'], y=mo['Dropped'], name='Dropped lines (Hst 99)', marker_color='#d62728', yaxis='y2', opacity=0.5))
        fig.add_trace(go.Scatter(x=mo['Order Month'], y=mo['Fill Rate %'], name='Fill rate %', mode='lines+markers+text', line=dict(color='#1f77b4', width=3),
                                 text=mo['Fill Rate %'].map(lambda v: f"{v:,.0f}%"), textposition='top center'))
        fig.update_layout(yaxis=dict(title='Fill rate %', range=[max(0, np.nanmin(mo['Fill Rate %']) - 5) if len(mo) else 0, 105]),
                          yaxis2=dict(title='Dropped lines', overlaying='y', side='right'), xaxis_tickangle=-45,
                          title="Fill rate (by order month, cases) with dropped-line counts")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Vendor Composite Scorecard")
        st.caption("Score = 50% fill rate + 50% on-time % (received lines, on or before Req dt). Higher is better.")
        ot = by_supplier_cat(po_df)
        ot = ot[ot['Status Code'].isin([75, 85]) & ot['Recd dt'].notna() & ot['Req dt'].notna()]
        if slicer_year: ot = ot[ot['Order Year'].isin(slicer_year)]
        if slicer_month: ot = ot[ot['Order Month'].isin(slicer_month)]
        ots = ot.groupby('Supplier').agg(OL=('PO no', 'size'), OnTime=('Var Days', lambda s: (s <= 0).mean() * 100),
                                         PlanLT=('Req dt', lambda s: np.nan), ).reset_index()
        lt = ot.assign(Plan=(ot['Req dt'] - ot['Ord dt']).dt.days, Act=(ot['Recd dt'] - ot['Ord dt']).dt.days).groupby('Supplier')[['Plan', 'Act']].mean().reset_index()
        comp = vf.merge(ots[['Supplier', 'OnTime']], on='Supplier', how='left').merge(lt, on='Supplier', how='left')
        comp['Score'] = 0.5 * comp['Fill Rate %'] + 0.5 * comp['OnTime'].fillna(comp['Fill Rate %'])
        comp['Lead Time Slip (days)'] = comp['Act'] - comp['Plan']
        comp = comp.rename(columns={'OnTime': 'On-time %', 'Plan': 'Planned LT (days)', 'Act': 'Actual LT (days)'})
        comp = comp[['Supplier', 'Lines', 'Fill Rate %', 'On-time %', 'Score', 'Planned LT (days)', 'Actual LT (days)', 'Lead Time Slip (days)']].sort_values('Score')
        st.dataframe(style0(comp, pct=['Fill Rate %', 'On-time %', 'Score']), use_container_width=True, hide_index=True, height=380)
        top_sp = po_active.groupby('Supplier')['USD $'].sum().sort_values(ascending=False)
        if top_sp.sum() > 0:
            t10 = top_sp.head(10).reset_index()
            t10['Share'] = t10['USD $'] / top_sp.sum() * 100
            fig = px.bar(t10, x='USD $', y='Supplier', orientation='h', text=t10.apply(lambda r: f"${r['USD $']:,.0f} ({r['Share']:.0f}%)", axis=1),
                         title="Open PO concentration — top 10 vendors")
            fig.update_layout(yaxis={'categoryorder': 'total ascending'})
            st.plotly_chart(fig, use_container_width=True)

# ==========================================================================
# TAB 7 — CASH FLOW
# ==========================================================================
with tabs[6], guard():
    st.header("Cash Flow Commitments — 6-Month Forward View")
    st.caption("Open PO lines (Hst 20-40) on active items, Otp = P01 excluded, timed by Req dt. Past months are excluded from the forward view. "
               "Ignores the Order Year/Month sidebar filters (all open orders are included); Supplier / Category filters apply.")
    lag = st.number_input("Payment lag after Req dt (days) — use your vendor terms", 0, 180, 0, 15)
    cf = by_supplier_cat(po_df[po_df['Status Code'].between(20, 40)]).copy()
    cf = cf[~cf['Item Status'].isin(DISCONTINUED + DELETED)]
    cf['Cash Date'] = cf['Req dt'] + pd.to_timedelta(lag, unit='D')
    m0 = today.replace(day=1)
    m_end = m0 + pd.DateOffset(months=6)
    months = [p.strftime('%Y-%m') for p in pd.date_range(m0, periods=6, freq='MS')]
    no_date = cf[cf['Cash Date'].isna()]
    past = cf[cf['Cash Date'] < m0]
    fwd = cf[(cf['Cash Date'] >= m0) & (cf['Cash Date'] < m_end)].copy()
    beyond = cf[cf['Cash Date'] >= m_end]
    fwd['Cash Month'] = fwd['Cash Date'].dt.strftime('%Y-%m')

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Next 6 months", f"${fwd['USD $'].sum():,.0f}")
    k2.metric("This month", f"${fwd.loc[fwd['Cash Month'] == months[0], 'USD $'].sum():,.0f}")
    k3.metric("Next 30 days", f"${fwd.loc[fwd['Cash Date'] <= today + pd.Timedelta(days=30), 'USD $'].sum():,.0f}")
    k4.metric("Past-due open POs (excluded)", f"${past['USD $'].sum():,.0f}", f"{len(past):,} lines", delta_color="off")
    k5.metric("Beyond 6 months", f"${beyond['USD $'].sum():,.0f}")
    if len(no_date):
        st.caption(f"{len(no_date):,} open lines have no Req dt (${no_date['USD $'].sum():,.0f}) and are not in the timeline.")

    if fwd.empty:
        st.info("No open PO commitments fall in the next 6 months.")
    else:
        piv = fwd.pivot_table(index='Custom Category', columns='Cash Month', values='USD $', aggfunc='sum', fill_value=0).reindex(columns=months, fill_value=0)
        long = piv.reset_index().melt(id_vars='Custom Category', var_name='Month', value_name='USD')
        totals = piv.sum(axis=0)
        fig = px.bar(long, x='Month', y='USD', color='Custom Category', title="Projected cash out by month and category")
        fig.add_trace(go.Scatter(x=months, y=totals.values, mode='text', text=[f"${v:,.0f}" for v in totals.values], textposition='top center', showlegend=False))
        fig.add_trace(go.Scatter(x=months, y=totals.cumsum().values, mode='lines+markers', name='Cumulative', yaxis='y2', line=dict(color='black', width=3)))
        fig.update_layout(yaxis_title="USD per month", yaxis2=dict(title='Cumulative USD', overlaying='y', side='right'), height=520)
        st.plotly_chart(fig, use_container_width=True)

        w1, w2 = st.columns(2)
        with w1:
            wk = fwd[fwd['Cash Date'] <= today + pd.Timedelta(days=56)].copy()
            wk['Week Starting'] = (wk['Cash Date'] - pd.to_timedelta(wk['Cash Date'].dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
            wk = wk.groupby('Week Starting')['USD $'].sum().reset_index()
            fig = px.bar(wk, x='Week Starting', y='USD $', text_auto='$,.0f', title="Next 8 weeks (weekly view)")
            st.plotly_chart(fig, use_container_width=True)
        with w2:
            fwd['Shipment Stage'] = np.where(fwd['Status'].isin(TRANSIT_STATUSES) & (fwd['Status'] != '1-Not Departed'), 'Shipped / in transit', 'Not yet shipped')
            sg = fwd.groupby(['Cash Month', 'Shipment Stage'])['USD $'].sum().reset_index()
            fig = px.bar(sg, x='Cash Month', y='USD $', color='Shipment Stage', title="Committed cash: already shipped vs not yet shipped",
                         color_discrete_map={'Shipped / in transit': '#2ca02c', 'Not yet shipped': '#ff7f0e'})
            st.plotly_chart(fig, use_container_width=True)

        b1, b2 = st.columns(2)
        with b1:
            bb = fwd.groupby('Buyer')['USD $'].sum().reset_index().sort_values('USD $')
            fig = px.bar(bb, x='USD $', y='Buyer', orientation='h', text_auto='$,.0f', title="Next 6 months by buyer")
            st.plotly_chart(fig, use_container_width=True)
        with b2:
            pv = piv.copy()
            pv['Total'] = pv.sum(axis=1)
            st.markdown("**Month × category ($)**")
            st.dataframe(style0(pv.sort_values('Total', ascending=False).reset_index(), money=list(pv.columns)), use_container_width=True, hide_index=True)

        sel = st.selectbox("Drill into a month", months)
        dm = fwd[fwd['Cash Month'] == sel].groupby('Supplier').agg(USD=('USD $', 'sum'), Lines=('PO no', 'size'), POs=('PO no', 'nunique'),
                                                                     Buyer=('Buyer', join_unique)).reset_index().sort_values('USD', ascending=False).head(25)
        st.dataframe(style0(dm, money=['USD']), use_container_width=True, hide_index=True)

    if not past.empty:
        with st.expander(f"Past-due open POs excluded from the forward view (${past['USD $'].sum():,.0f})"):
            pd_ = past.groupby('Supplier').agg(USD=('USD $', 'sum'), Lines=('PO no', 'size'), Oldest_Req=('Req dt', 'min')).reset_index().sort_values('USD', ascending=False).head(25)
            pd_['Oldest_Req'] = pd_['Oldest_Req'].dt.strftime('%Y-%m-%d')
            st.dataframe(style0(pd_, money=['USD']), use_container_width=True, hide_index=True)

# ==========================================================================
# TAB 8 — PRICE INDEX (PPV)
# ==========================================================================
with tabs[7], guard():
    st.header("Procurement & Price Index (PPV Trend)")
    st.caption("Saleable item groups only. Price = PO 'Purch price' converted to USD, compared with the same item's previous order price from the same supplier (same UOM). "
               "Single-order jumps above ±100% are dropped as probable UOM / data errors.")

    pp = po_df[(po_df['Purch price'] > 0) & po_df['Item grp'].isin(SALEABLE_GROUPS) & po_df['Ord dt'].notna()].copy()
    pp = pp.sort_values(['Ord dt', 'PO no', 'Ln'])
    pp['Prev Price'] = pp.groupby(['Item number', 'POu', 'Supplier'])['Price USD'].shift(1)
    cmpx = pp[pp['Prev Price'].notna() & (pp['Prev Price'] > 0)].copy()
    cmpx['Chg %'] = (cmpx['Price USD'] / cmpx['Prev Price'] - 1) * 100
    n_out = int((cmpx['Chg %'].abs() > 100).sum())
    cmpx = cmpx[cmpx['Chg %'].abs() <= 100]
    cmpx['Base Value'] = cmpx['Prev Price'] * cmpx['Order qty']
    cmpx['Chg Value'] = (cmpx['Price USD'] - cmpx['Prev Price']) * cmpx['Order qty']
    cmpx['Savings'] = (-cmpx['Chg Value']).clip(lower=0)
    cmpx['Increase $'] = cmpx['Chg Value'].clip(lower=0)
    if slicer_category:
        cmpx = cmpx[cmpx['Custom Category'].isin(slicer_category)]
    if n_out:
        st.caption(f"{n_out:,} price comparisons above ±100% were excluded.")

    if cmpx.empty:
        st.info("Not enough repeat-order price history.")
    else:
        cur_m = today.strftime('%Y-%m')
        recent_m = sorted([m for m in cmpx['Order Month'].unique() if m != 'NaT' and m <= cur_m])[-12:]
        cm = cmpx[cmpx['Order Month'].isin(recent_m)].groupby(['Custom Category', 'Order Month']).agg(
            Chg=('Chg Value', 'sum'), Base=('Base Value', 'sum')).reset_index()
        cm['Price Move %'] = np.where(cm['Base'] > 0, cm['Chg'] / cm['Base'].replace(0, 1) * 100, np.nan)

        st.subheader("Category Price Direction — Month over Month")
        st.caption("Spend-weighted average price change vs the previous order of the same items. Red = costs rising, green = costs falling.")
        hm = cm.pivot(index='Custom Category', columns='Order Month', values='Price Move %').reindex(columns=recent_m)
        fig = go.Figure(go.Heatmap(z=hm.values, x=hm.columns, y=hm.index, colorscale='RdYlGn_r', zmid=0, zmin=-10, zmax=10,
                                   text=[[("" if pd.isna(v) else f"{v:+.0f}%") for v in row] for row in hm.values], texttemplate="%{text}",
                                   colorbar=dict(title="% move")))
        fig.update_layout(height=420)
        st.plotly_chart(fig, use_container_width=True)

        sel_m = st.selectbox("Month for at-a-glance view", recent_m[::-1])
        cur = cm[cm['Order Month'] == sel_m].dropna(subset=['Price Move %']).sort_values('Price Move %')
        if not cur.empty:
            fig = px.bar(cur, x='Price Move %', y='Custom Category', orientation='h', color='Price Move %', color_continuous_scale='RdYlGn_r',
                         range_color=[-10, 10], text=cur['Price Move %'].map(lambda v: f"{'▲' if v > 0 else '▼'} {v:+.0f}%"),
                         title=f"Average price direction by category — {sel_m}")
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Cost Savings Secured by Category — Month over Month")
        sv = cmpx[cmpx['Order Month'].isin(recent_m)].groupby(['Order Month', 'Custom Category'])['Savings'].sum().reset_index()
        sv = sv[sv['Savings'] > 0]
        if not sv.empty:
            fig = px.bar(sv, x='Order Month', y='Savings', color='Custom Category', title="USD saved vs previous order price (price decreases × quantity)")
            tots = sv.groupby('Order Month')['Savings'].sum()
            fig.add_trace(go.Scatter(x=tots.index, y=tots.values, mode='text', text=[f"${v:,.0f}" for v in tots.values], textposition='top center', showlegend=False))
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(style0(sv.pivot_table(index='Custom Category', columns='Order Month', values='Savings', aggfunc='sum', fill_value=0).reset_index(),
                                money=list(recent_m)), use_container_width=True, hide_index=True)
        else:
            st.info("No price decreases in the period.")

        st.subheader("Top 20 Item Categories with Price Increases — Last 6 Months")
        st.caption("Item category = Purchase Report 'Category' (column AB), e.g. CHOCOLATE, FISH, CEREAL.")
        r1, r2 = st.columns(2)
        rank_by = r1.selectbox("Rank by", ["Weighted avg % increase", "Total $ impact of increases", "# items with increases"])
        min_ev = r2.slider("Min price-increase events per category", 1, 20, 3)
        c6 = cmpx[(cmpx['Ord dt'] >= today - pd.DateOffset(months=6)) & (cmpx['Chg Value'] > 0)]
        if not c6.empty:
            tc = c6.groupby('Item Category').agg(Events=('PO no', 'size'), Items=('Item number', 'nunique'), Impact=('Chg Value', 'sum'), Base=('Base Value', 'sum')).reset_index()
            tc['Weighted avg % increase'] = tc['Impact'] / tc['Base'].replace(0, 1) * 100
            tc = tc[tc['Events'] >= min_ev]
            key = {"Weighted avg % increase": 'Weighted avg % increase', "Total $ impact of increases": 'Impact', "# items with increases": 'Items'}[rank_by]
            tc = tc.sort_values(key, ascending=False).head(20)
            tc = tc.rename(columns={'Impact': 'Total $ impact of increases', 'Items': '# items with increases', 'Events': 'Increase events'}).drop(columns='Base')
            fig = px.bar(tc.iloc[::-1], x=key.replace('Impact', 'Total $ impact of increases').replace('Items', '# items with increases'), y='Item Category', orientation='h',
                         text=tc.iloc[::-1]['Weighted avg % increase'].map(lambda v: f"+{v:,.0f}%"), title=f"Top 20 item categories — ranked by {rank_by.lower()}")
            fig.update_layout(height=620)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(style0(tc, pct=['Weighted avg % increase'], money=['Total $ impact of increases']), use_container_width=True, hide_index=True)
        else:
            st.info("No price increases in the last 6 months.")

        st.subheader("Biggest Line-Level Moves (last 6 months)")
        rec = cmpx[cmpx['Ord dt'] >= today - pd.DateOffset(months=6)]
        show_cols = ['PO no', 'Supplier', 'ItemDescription', 'Prev Price', 'Price USD', 'Chg %']
        cx, cy = st.columns(2)
        with cx:
            st.markdown("**Inflation — largest price increases**")
            st.dataframe(style0(rec.sort_values('Chg %', ascending=False)[show_cols].head(15), money2=['Prev Price', 'Price USD'], pct=['Chg %']),
                         use_container_width=True, hide_index=True)
        with cy:
            st.markdown("**Savings — largest price decreases**")
            st.dataframe(style0(rec.sort_values('Chg %')[show_cols].head(15), money2=['Prev Price', 'Price USD'], pct=['Chg %']),
                         use_container_width=True, hide_index=True)

    st.subheader("Item Price History")
    item_q = st.text_input("🔍 Search item (code or description) to see its price trend by supplier:")
    if item_q:
        hist = po_df[(po_df['Purch price'] > 0) & (po_df['Item number'].str.contains(item_q, case=False, na=False) |
                                                   po_df['ItemDescription'].str.contains(item_q, case=False, na=False))].sort_values('Ord dt')
        if not hist.empty:
            fig = px.line(hist, x='Ord dt', y='Price USD', color='Supplier', markers=True, hover_data=['ItemDescription', 'POu'], title=f"Price history (USD per PO unit) — {item_q}")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("No price history found for this item.")
