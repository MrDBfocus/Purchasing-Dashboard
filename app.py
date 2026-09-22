import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
from datetime import datetime

# ----------------------------------------
# 1. PAGE SETUP
# ----------------------------------------
st.set_page_config(page_title="CPJ Purchasing Dashboard", layout="wide", initial_sidebar_state="expanded")
st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")

# ----------------------------------------
# 2. DATA LOADING & ENGINEERING
# ----------------------------------------
@st.cache_data
def load_data():
    # --- A. Sales / Item Master ---
    # UPDATED FILE NAME:
    sales = pd.read_csv("Purchase Report Sales Analysis with Raw Depletions.csv")
    sales.rename(columns={'\ufeffItem Number': 'Item Number'}, inplace=True)
    sales['Item Number'] = sales['Item Number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Clean Numbers
    sales['Conversion Factor'] = pd.to_numeric(sales['Conversion Factor'].astype(str).str.replace(',', ''), errors='coerce').fillna(1).replace(0, 1)
    sales['Inventory Value'] = pd.to_numeric(sales['Inventory Value'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    sales['Last Price'] = pd.to_numeric(sales['Last Price'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    # Calculate 6-Month Historical Depletion Average (April - Sept 2026)
    recent_depletions = ['April 2026 Depletion', 'May 2026 Depletion', 'June 2026 Depletion', 
                         'July 2026 Depletion', 'August 2026 Depletion', 'September 2026 Depletion']
    for col in recent_depletions:
         if col in sales.columns:
             sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
             
    sales['6M_Avg_Depletion_Base'] = sales[recent_depletions].mean(axis=1)
    sales['6M_Avg_Depletion_Cases'] = sales['6M_Avg_Depletion_Base'] / sales['Conversion Factor']

    # --- B. PO Dates (GIT) ---
    # UPDATED FILE NAME:
    po = pd.read_excel("PO Dates.xlsx", sheet_name="GIT Report")
    
    # 1. Exclude P01 from Otp column
    po = po[po['Otp'] != 'P01']
    
    po['Item number'] = po['Item number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Enrich PO with Sales Master Data
    po = po.merge(sales[['Item Number', 'Conversion Factor', 'Category', 'Shelf Life', 'Inventory Value']], 
                  left_on='Item number', right_on='Item Number', how='left')
    po['Conversion Factor'] = po['Conversion Factor'].fillna(1)
    po['Buyer'] = po['Buyer'].fillna('Unassigned')
    
    # 2. Use USD $ for Value Calculations
    po['USD $'] = pd.to_numeric(po['USD $'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    po['Order qty'] = pd.to_numeric(po['Order qty'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    po['Order Qty (Cases)'] = po['Order qty'] / po['Conversion Factor']
    
    # 3. Clean Corrupted Date Formats (e.g. converting float 20250912.0 to actual date)
    def parse_mixed_dates(series):
        if pd.api.types.is_datetime64_any_dtype(series):
            return series
        s_clean = series.astype(str).str.split('.').str[0]
        s_clean = s_clean.replace({'nan': None, 'NaT': None, 'None': None, '': None})
        return pd.to_datetime(s_clean, format='%Y%m%d', errors='coerce')

    po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
    po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
    po['Arrival'] = parse_mixed_dates(po['Arrival'])
    po['ETA'] = parse_mixed_dates(po['ETA'])
        
    po['Actual Lead Time (Days)'] = (po['Arrival'] - po['Ord dt']).dt.days
    po.loc[po['Actual Lead Time (Days)'] < 0, 'Actual Lead Time (Days)'] = np.nan # Clears out errors
    
    po['Order Month'] = po['Ord dt'].dt.to_period('M').astype(str)
    
    # 4. GIT Expediting Flags based on Today's Date
    today = pd.Timestamp.today().normalize()
    po['Delivery Status'] = 'On Time / Tracking'
    
    # Flag logic: Overdue ETA takes priority, followed by Past Requested Date
    po.loc[po['Req dt'] < today, 'Delivery Status'] = 'Late (Past Req Date)'
    po.loc[po['ETA'] < today, 'Delivery Status'] = 'Late (Past ETA)'

    # --- C. Forecast ---
    # UPDATED FILE NAME:
    fc_q4 = pd.read_excel("CPJ FORECAST.xlsx", sheet_name="Sept - Dec FCST 2026", header=3)
    fc_q1 = pd.read_excel("CPJ FORECAST.xlsx", sheet_name="Jan-Mar 2027 FCST", header=3)
    
    fc_q4['Item Code'] = fc_q4['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    fc_q1['Item Code'] = fc_q1['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    fc = pd.merge(fc_q4, fc_q1[['Item Code', 'Sum of Jan-27 vol', 'Sum of Feb-27 vol', 'Sum of Mar-27 vol']], on='Item Code', how='outer')
    fc = fc.merge(sales[['Item Number', 'Conversion Factor', 'Supplier Name']], left_on='Item Code', right_on='Item Number', how='left')
    fc['Conversion Factor'] = fc['Conversion Factor'].fillna(1)
    
    if 'Item Description_x' in fc.columns:
        fc.rename(columns={'Item Description_x': 'Item Description'}, inplace=True)
    
    forecast_cols = ['Sum of September Vol', 'Sum of October Vol', 'Sum of November Vol', 'Sum of December Vol', 'Sum of Jan-27 vol', 'Sum of Feb-27 vol', 'Sum of Mar-27 vol']
    for col in forecast_cols:
        if col in fc.columns:
            fc[col] = pd.to_numeric(fc[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            fc[col + ' (Cases)'] = fc[col] / fc['Conversion Factor']

    return sales, po, fc

sales_df, po_df, fc_df = load_data()

# ----------------------------------------
# 3. GLOBAL SLICERS (SIDEBAR)
# ----------------------------------------
st.sidebar.header("Global Filters")
slicer_supplier = st.sidebar.multiselect("Supplier", options=sorted(po_df['Supplier'].dropna().unique()))
slicer_buyer = st.sidebar.multiselect("Buyer", options=sorted(po_df['Buyer'].dropna().unique()))
slicer_status = st.sidebar.multiselect("PO Status", options=sorted(po_df['Status'].dropna().unique()))

def filter_data(po, sales, fc):
    if slicer_supplier:
        po = po[po['Supplier'].isin(slicer_supplier)]
        sales = sales[sales['Supplier Name'].isin(slicer_supplier)]
        fc = fc[fc['Supplier Name'].isin(slicer_supplier)]
    if slicer_buyer:
        po = po[po['Buyer'].isin(slicer_buyer)]
    if slicer_status:
        po = po[po['Status'].isin(slicer_status)]
    return po, sales, fc

po_filtered, sales_filtered, fc_filtered = filter_data(po_df, sales_df, fc_df)

# ----------------------------------------
# 4. DASHBOARD TABS
# ----------------------------------------
tabs = st.tabs([
    "📊 Executive & Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", 
    "📈 Demand Pipeline", "🤝 Supplier Scorecard", "💵 Cash Flow"
])

# -- TAB 1: EXECUTIVE & SPEND --
with tabs[0]:
    st.header("Executive Summary: Spend & Buyer Analytics")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Open PO Spend", f"${po_filtered['USD $'].sum():,.0f}")
    col2.metric("Total PO Volume (CS)", f"{po_filtered['Order Qty (Cases)'].sum():,.0f}")
    col3.metric("Current Inventory Value", f"${sales_filtered['Inventory Value'].sum():,.0f}")
    
    late_orders = po_filtered[po_filtered['Delivery Status'].str.contains('Late', na=False)]['PO no'].nunique()
    col4.metric("POs Flagged Late", f"{late_orders}", delta_color="inverse")
    
    st.divider()
    
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.subheader("PO Spend by Month")
        po_month = po_filtered[po_filtered['Order Month'] != 'NaT'].groupby('Order Month')['USD $'].sum().reset_index().sort_values('Order Month')
        fig_month = px.bar(po_month, x='Order Month', y='USD $', title='Purchasing Value Output (USD) by Order Date', text_auto='.2s')
        st.plotly_chart(fig_month, use_container_width=True)
        
    with row1_col2:
        st.subheader("Spend by Buyer")
        buyer_summ = po_filtered.groupby('Buyer')['USD $'].sum().reset_index()
        # 5. Exclude buyers with < 5% of total spend
        total_spend = buyer_summ['USD $'].sum()
        buyer_summ['Spend %'] = buyer_summ['USD $'] / total_spend
        buyer_summ = buyer_summ[buyer_summ['Spend %'] >= 0.05].sort_values('USD $', ascending=False)
        
        fig_buyer = px.pie(buyer_summ, values='USD $', names='Buyer', hole=0.4, title='Total Commitments (Excluding <5% Shares)')
        st.plotly_chart(fig_buyer, use_container_width=True)

# -- TAB 2: GIT & EXPEDITING --
with tabs[1]:
    st.header("Goods In Transit & Expediting Action Board")
    st.markdown("**Focus:** Open containers flagged as Late because their Request Date or ETA is older than today.")
    
    open_git = po_filtered[~po_filtered['Status'].str.contains('Stripped|Yard', na=False)]
    late_git = open_git[open_git['Delivery Status'].str.contains('Late', na=False)]
    
    col1, col2 = st.columns([1, 2])
    with col1:
        fig_late = px.histogram(open_git, x='Delivery Status', color='Delivery Status', title="Open Orders: Tracking vs Late")
        st.plotly_chart(fig_late, use_container_width=True)
    with col2:
        st.subheader("Critical Expedite List")
        st.dataframe(late_git[['PO no', 'Buyer', 'Supplier', 'ItemDescription', 'Req dt', 'ETA', 'Delivery Status', 'Status']])

# -- TAB 3: INVENTORY Health --
with tabs[2]:
    st.header("Inventory Health: Dead Stock & Stockout Risks")
    
    row3_col1, row3_col2 = st.columns(2)
    with row3_col1:
        st.subheader("💀 Dead Stock Warning")
        dead_stock = sales_filtered[(sales_filtered['Inventory Value'] > 500) & (sales_filtered['6M_Avg_Depletion_Cases'] == 0)]
        dead_stock = dead_stock.sort_values('Inventory Value', ascending=False)
        st.dataframe(dead_stock[['Item Number', 'Item Description', 'Inventory Value', 'Last Price']].head(15))
        
    with row3_col2:
        st.subheader("🚨 Critical Stockouts")
        stockouts = sales_filtered[(sales_filtered['Inventory Value'] == 0) & (sales_filtered['6M_Avg_Depletion_Cases'] > 10)]
        stockouts = stockouts.sort_values('6M_Avg_Depletion_Cases', ascending=False)
        st.dataframe(stockouts[['Item Number', 'Item Description', '6M_Avg_Depletion_Cases', 'Last Purchase Date']].head(15))

# -- TAB 4: DEMAND PIPELINE --
with tabs[3]:
    st.header("7-Month Demand Forecasting")
    case_cols = [c for c in fc_filtered.columns if '(Cases)' in c]
    
    fc_melt = fc_filtered.melt(id_vars=['Item Code', 'Item Description'], value_vars=case_cols, var_name='Month', value_name='Forecasted Cases')
    fc_melt['Month'] = fc_melt['Month'].str.replace('Sum of ', '').str.replace(' (Cases)', '')
    
    fc_trend = fc_melt.groupby('Month')['Forecasted Cases'].sum().reindex(['September Vol', 'October Vol', 'November Vol', 'December Vol', 'Jan-27 vol', 'Feb-27 vol', 'Mar-27 vol']).reset_index()
    fig_fc = px.line(fc_trend, x='Month', y='Forecasted Cases', markers=True, title='Overall Forecasted Volume Pipeline (Cases)')
    st.plotly_chart(fig_fc, use_container_width=True)
    st.dataframe(fc_filtered[['Item Code', 'Item Description'] + case_cols])

# -- TAB 5: SUPPLIER SCORECARD --
with tabs[4]:
    st.header("Supplier Performance & Reliability")
    
    scorecard = po_filtered.groupby('Supplier').agg(
        Total_Orders=('PO no', 'count'),
        Total_Spend=('USD $', 'sum'),
        Avg_Lead_Time_Days=('Actual Lead Time (Days)', 'mean'),
        Late_Deliveries=('Delivery Status', lambda x: (x.str.contains('Late')).sum())
    ).reset_index()
    
    scorecard['% Late'] = (scorecard['Late_Deliveries'] / scorecard['Total_Orders']) * 100
    scorecard = scorecard.sort_values('Total_Spend', ascending=False)
    
    st.dataframe(scorecard.style.format({
        "Total_Spend": "${:,.2f}", 
        "Avg_Lead_Time_Days": "{:.1f}", 
        "% Late": "{:.1f}%"
    }))

# -- TAB 6: CASH FLOW --
with tabs[5]:
    st.header("Cash Flow Commitments by Arrival")
    
    cf_df = po_filtered.dropna(subset=['ETA', 'USD $'])
    cf_df['ETA Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
    
    cf_summ = cf_df.groupby('ETA Month')['USD $'].sum().reset_index().sort_values('ETA Month')
    
    fig_cf = px.bar(cf_summ, x='ETA Month', y='USD $', title='Capital Required Based on Expected Arrival (USD)', text_auto='.2s')
    st.plotly_chart(fig_cf, use_container_width=True)
