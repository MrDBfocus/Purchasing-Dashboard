import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from datetime import datetime

# ----------------------------------------
# 1. PAGE SETUP
# ----------------------------------------
st.set_page_config(page_title="CPJ Purchasing Dashboard", layout="wide", initial_sidebar_state="expanded")
st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")

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

# ----------------------------------------
# 2. DATA LOADING & ENGINEERING
# ----------------------------------------
@st.cache_data
def load_data():
    # --- A. Sales / Item Master ---
    sales = pd.read_csv("Purchase Report Sales Analysis with Raw Depletions.csv")
    sales.dropna(subset=['Item Description'], inplace=True) # Drops trailing subtotal rows
    sales.rename(columns={'\ufeffItem Number': 'Item Number'}, inplace=True)
    sales['Item Number'] = sales['Item Number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Calculate Base Inventory minus restricted locations
    for col in ['Inventory Value', 'Allocated quantity', 'On Order', 'Plants', 'Stores', 'OM']:
        if col in sales.columns:
            sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    sales['Warehouse Inventory Value'] = sales['Inventory Value'] - sales['Plants'] - sales['Stores'] - sales['OM']
    sales['Warehouse Inventory Value'] = sales['Warehouse Inventory Value'].clip(lower=0)

    sales['Conversion Factor'] = pd.to_numeric(sales['Conversion Factor'].astype(str).str.replace(',', ''), errors='coerce').fillna(1).replace(0, 1)
    sales['Last Price'] = pd.to_numeric(sales['Last Price'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    # 3-Month Historical Depletion Average
    recent_depletions = ['July 2026 Depletion', 'August 2026 Depletion', 'September 2026 Depletion']
    for col in recent_depletions:
         if col in sales.columns:
             sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
             
    sales['3M_Avg_Depletion_Base'] = sales[recent_depletions].mean(axis=1)
    sales['3M_Avg_Depletion_Cases'] = sales['3M_Avg_Depletion_Base'] / sales['Conversion Factor']
    sales['Current_Stock_Cases'] = sales['Warehouse Inventory Value'] / sales['Conversion Factor']

    # --- B. PO Dates (GIT) ---
    po = pd.read_excel("PO Dates.xlsx", sheet_name="GIT Report")
    
    # Hard Exclusions: Otp != P01, Hst != 99
    po = po[(po['Otp'] != 'P01') & (po['Hst'] != 99)]
    po['Item number'] = po['Item number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    po['Custom Category'] = po['Item grp'].map(cat_map).fillna('UNCLASSIFIED')
    
    po = po.merge(sales[['Item Number', 'Conversion Factor']], left_on='Item number', right_on='Item Number', how='left')
    po['Conversion Factor'] = po['Conversion Factor'].fillna(1)
    po['Buyer'] = po['Buyer'].fillna('Unassigned')
    
    for col in ['USD $', 'Order qty', 'Recd qty', 'Purch price']:
        po[col] = pd.to_numeric(po[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    po['Order Qty (Cases)'] = po['Order qty'] / po['Conversion Factor']
    po['Recd Qty (Cases)'] = po['Recd qty'] / po['Conversion Factor']
    
    po['Status Code'] = po['Status'].astype(str).str.extract(r'(\d+)').astype(float)
    
    def parse_mixed_dates(series):
        if pd.api.types.is_datetime64_any_dtype(series): return series
        s_clean = series.astype(str).str.split('.').str[0].replace({'nan': None, 'NaT': None, 'None': None, '': None})
        return pd.to_datetime(s_clean, format='%Y%m%d', errors='coerce')

    po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
    po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
    po['Arrival'] = parse_mixed_dates(po['Arrival'])
    po['ETA'] = parse_mixed_dates(po['ETA'])
    po['Stripped Date'] = parse_mixed_dates(po['Stripped'])
    po['Yard Date'] = parse_mixed_dates(po['Yard'])
        
    po['Order Month'] = po['Ord dt'].dt.to_period('M').astype(str)
    po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)
    po['Arrival Variance (Days)'] = (po['Arrival'] - po['Req dt']).dt.days
    
    today = pd.Timestamp.today().normalize()
    po['Delivery Status'] = 'Tracking'
    po.loc[(po['Req dt'] < today) & (po['Recd qty'] == 0), 'Delivery Status'] = 'Late (Past Req Date)'
    po.loc[(po['ETA'] < today) & (po['Recd qty'] == 0), 'Delivery Status'] = 'Late (Past ETA)'

    # --- C. Forecast ---
    fc = pd.read_excel("CPJ FORECAST.xlsx", sheet_name="Sept - Mar FCST")
    fc['Item Code'] = fc['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    fc = fc.merge(sales[['Item Number', 'Conversion Factor', 'Supplier Name']], left_on='Item Code', right_on='Item Number', how='left')
    fc['Conversion Factor'] = fc['Conversion Factor'].fillna(1)
    
    forecast_cols = ['September', 'October', 'November', 'December', 'January', 'February', 'March']
    for col in forecast_cols:
        if col in fc.columns:
            fc[col] = pd.to_numeric(fc[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            fc[col + ' (Cases)'] = fc[col] / fc['Conversion Factor']
            
    fc['3M_Avg_Forecast_Cases'] = fc[['October (Cases)', 'November (Cases)', 'December (Cases)']].mean(axis=1)

    # --- D. Bids ---
    bids = pd.read_excel("BIDS.xlsx")
    bids['Item '] = bids['Item '].astype(str).str.replace(r'\.0$', '', regex=True)
    
    return sales, po, fc, bids

sales_df, po_df, fc_df, bids_df = load_data()

# ----------------------------------------
# 3. GLOBAL SLICERS (SIDEBAR)
# ----------------------------------------
st.sidebar.header("Global Filters")

valid_years = [int(y) for y in po_df[po_df['Order Year'] >= 2025]['Order Year'].dropna().unique().tolist()]
slicer_year = st.sidebar.multiselect("Order Year", sorted(valid_years))

clean_months = [str(m) for m in po_df['Order Month'].dropna().unique() if str(m) not in ['NaT', 'nan', 'None']]
slicer_month = st.sidebar.multiselect("Order Month", sorted(clean_months))

clean_suppliers = [str(s) for s in po_df['Supplier'].dropna().unique() if str(s) not in ['nan', 'None']]
slicer_supplier = st.sidebar.multiselect("Supplier", options=sorted(clean_suppliers))

clean_categories = [str(c) for c in po_df['Custom Category'].dropna().unique() if str(c) not in ['nan', 'None']]
slicer_category = st.sidebar.multiselect("Custom Category", options=sorted(clean_categories))

def filter_data(po, sales, fc):
    if slicer_year: po = po[po['Order Year'].isin(slicer_year)]
    if slicer_month: po = po[po['Order Month'].isin(slicer_month)]
    if slicer_supplier:
        po = po[po['Supplier'].isin(slicer_supplier)]
    if slicer_category:
        po = po[po['Custom Category'].isin(slicer_category)]
    return po, sales, fc

po_filtered, sales_filtered, fc_filtered = filter_data(po_df, sales_df, fc_df)
po_active = po_filtered[(po_filtered['Status Code'] >= 20) & (po_filtered['Status Code'] <= 40)]

# ----------------------------------------
# 4. DASHBOARD TABS
# ----------------------------------------
tabs = st.tabs([
    "📊 Executive Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", "🏆 Bid Performance",
    "📈 6-Month Demand", "🤝 Supplier Scorecard", "💵 Cash Flow", "📉 Price Index (PPV)"
])

# -- TAB 1: EXECUTIVE & SPEND --
with tabs[0]:
    st.header("Executive Summary: Category & Buyer Spend")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Open PO Spend (Status 20-40)", f"${po_active['USD $'].sum():,.0f}")
    col2.metric("Total PO Volume (Cases)", f"{po_active['Order Qty (Cases)'].sum():,.0f}")
    col3.metric("Current Warehouse Inventory", f"${sales_filtered['Warehouse Inventory Value'].sum():,.0f}")
    
    st.divider()
    
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.subheader("PO Volume (Cases) by Month (Trend)")
        po_vol = po_filtered[(po_filtered['Order Year'] >= 2025) & (po_filtered['Order Month'] != 'NaT')]
        vol_summ = po_vol.groupby('Order Month')['Order Qty (Cases)'].sum().reset_index().sort_values('Order Month')
        vol_summ['3M Moving Avg'] = vol_summ['Order Qty (Cases)'].rolling(window=3).mean()
        
        fig_vol = px.bar(vol_summ, x='Order Month', y='Order Qty (Cases)', title="Volume (Cases) with 3M Trend")
        fig_vol.add_trace(go.Scatter(x=vol_summ['Order Month'], y=vol_summ['3M Moving Avg'], mode='lines', name='3M Avg', line=dict(color='red')))
        st.plotly_chart(fig_vol, use_container_width=True)
        
    with row1_col2:
        st.subheader("Total Spend by Custom Category")
        cat_summ = po_filtered.groupby('Custom Category')['USD $'].sum().reset_index().sort_values('USD $', ascending=True)
        fig_cat = px.bar(cat_summ, x='USD $', y='Custom Category', orientation='h', title="Spend by Operational Category")
        st.plotly_chart(fig_cat, use_container_width=True)

    st.subheader("Spend by Buyer (Consolidated <5%)")
    buyer_summ = po_filtered.groupby('Buyer')['USD $'].sum().reset_index()
    total_spend = buyer_summ['USD $'].sum()
    buyer_summ['Spend %'] = buyer_summ['USD $'] / total_spend
    
    buyer_summ.loc[buyer_summ['Spend %'] < 0.05, 'Buyer'] = 'Other Buyers'
    buyer_cons = buyer_summ.groupby('Buyer')['USD $'].sum().reset_index()
    
    fig_buyer = px.pie(buyer_cons, values='USD $', names='Buyer', hole=0.4, title='Total Commitments by Buyer')
    st.plotly_chart(fig_buyer, use_container_width=True)


# -- TAB 2: GIT & EXPEDITING --
with tabs[1]:
    st.header("Goods In Transit: Clearance Tracking & Exceptions")
    
    st.subheader("Clearance Metrics: Stripped vs Cleared (Yard) by Month")
    po_filtered['Stripped Month'] = po_filtered['Stripped Date'].dt.to_period('M').astype(str)
    po_filtered['Yard Month'] = po_filtered['Yard Date'].dt.to_period('M').astype(str)
    
    strip_cnt = po_filtered[po_filtered['Stripped Month'] != 'NaT'].groupby('Stripped Month')['Container Numb'].nunique().reset_index(name='Stripped Count')
    yard_cnt = po_filtered[po_filtered['Yard Month'] != 'NaT'].groupby('Yard Month')['Container Numb'].nunique().reset_index(name='Cleared (Yard) Count')
    
    cnt_merged = pd.merge(strip_cnt, yard_cnt, left_on='Stripped Month', right_on='Yard Month', how='outer').fillna(0)
    cnt_merged['Month'] = np.where(cnt_merged['Stripped Month'] != 0, cnt_merged['Stripped Month'], cnt_merged['Yard Month'])
    cnt_merged = cnt_merged.sort_values('Month')
    
    fig_clear = go.Figure()
    fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Stripped Count'], name='Stripped', marker_color='royalblue'))
    fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Cleared (Yard) Count'], name='Cleared (Yard)', marker_color='darkorange'))
    fig_clear.update_layout(barmode='group', title="Container Processing Counts (Year to Month)")
    st.plotly_chart(fig_clear, use_container_width=True)

    st.subheader("Critical Expedite List (Open Status Containers)")
    target_statuses = ['1-Not Departed', '2-On the Water', '3-At the Port', '4-In the Yard']
    git_open = po_filtered[po_filtered['Status'].isin(target_statuses)]
    late_git = git_open[git_open['Delivery Status'].str.contains('Late', na=False)]
    
    st.dataframe(late_git[['Container Numb', 'PO no', 'Status', 'Supplier', 'Req dt', 'ETA', 'Delivery Status']].drop_duplicates())


# -- TAB 3: INVENTORY HEALTH --
with tabs[2]:
    st.header("Inventory Health: Dead Stock & Slow Moving")
    
    inv_eval = sales_filtered.merge(fc_filtered[['Item Code', '3M_Avg_Forecast_Cases']], left_on='Item Number', right_on='Item Code', how='left')
    inv_eval['3M_Avg_Forecast_Cases'] = inv_eval['3M_Avg_Forecast_Cases'].fillna(0)
    
    row3_col1, row3_col2 = st.columns(2)
    with row3_col1:
        st.subheader("💀 Dead Stock Warning")
        st.markdown("Items with inventory value but **0 sales in the last 3 months**.")
        dead_stock = inv_eval[(inv_eval['Warehouse Inventory Value'] > 100) & (inv_eval['3M_Avg_Depletion_Cases'] == 0)]
        st.dataframe(dead_stock[['Item Number', 'Item Description', 'Warehouse Inventory Value', '3M_Avg_Depletion_Cases']].sort_values('Warehouse Inventory Value', ascending=False).head(15))
        
    with row3_col2:
        st.subheader("🐢 Slow Moving Stock")
        st.markdown("Stocked items where 3-month sales are **>30% lower** than 3-month forecast.")
        slow = inv_eval[(inv_eval['Warehouse Inventory Value'] > 0) & (inv_eval['3M_Avg_Forecast_Cases'] > 0)].copy()
        slow['Sales vs Forecast'] = slow['3M_Avg_Depletion_Cases'] / slow['3M_Avg_Forecast_Cases']
        slow_moving = slow[slow['Sales vs Forecast'] < 0.70]
        st.dataframe(slow_moving[['Item Number', 'Item Description', '3M_Avg_Depletion_Cases', '3M_Avg_Forecast_Cases', 'Sales vs Forecast']].sort_values('Sales vs Forecast').head(15))


# -- TAB 4: BID PERFORMANCE --
with tabs[3]:
    st.header("🏆 Bid Customer Fulfillment & Coverage")
    
    bids_merged = bids_df.merge(sales_filtered[['Item Number', 'Current_Stock_Cases', 'Category']], left_on='Item ', right_on='Item Number', how='left')
    
    port_statuses = ['3-At the Port', '4-In the Yard']
    po_port = po_active[po_active['Status'].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases At Port')
    po_order = po_active[~po_active['Status'].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order')
    
    bids_merged = bids_merged.merge(po_port, left_on='Item ', right_on='Item number', how='left').fillna(0)
    bids_merged = bids_merged.merge(po_order, left_on='Item ', right_on='Item number', how='left').fillna(0)
    
    bids_merged['Availability Status'] = 'Not In Stock'
    bids_merged.loc[bids_merged['Cases At Port'] > 0, 'Availability Status'] = 'Inventory @ Port'
    bids_merged.loc[bids_merged['Current_Stock_Cases'] > 0, 'Availability Status'] = 'Available'
    
    colA, colB = st.columns([1, 2])
    with colA:
        fig_bids = px.pie(bids_merged, names='Availability Status', title="Bid Item Availability Breakdown", color='Availability Status',
                          color_discrete_map={'Available': 'green', 'Inventory @ Port': 'orange', 'Not In Stock': 'red'})
        st.plotly_chart(fig_bids, use_container_width=True)
    with colB:
        st.subheader("Critical Bid Items (0 Stock & 0 On Order)")
        critical = bids_merged[(bids_merged['Current_Stock_Cases'] == 0) & (bids_merged['Cases On Order'] == 0) & (bids_merged['Cases At Port'] == 0)]
        st.dataframe(critical[['Item ', 'Description', 'Customer', 'Category']])

    st.subheader("All Bid Item Coverages")
    st.dataframe(bids_merged[['Item ', 'Description', 'Customer', 'Category', 'Current_Stock_Cases', 'Cases At Port', 'Cases On Order']])


# -- TAB 5: DEMAND PIPELINE --
with tabs[4]:
    st.header("6-Month Demand Forecasting")
    
    search_term = st.text_input("🔍 Search Item Code or Description:", "")
    
    fc_display = fc_filtered.copy()
    if search_term:
        fc_display = fc_display[fc_display['Item Code'].str.contains(search_term, case=False, na=False) | 
                                fc_display['Item Description'].str.contains(search_term, case=False, na=False)]
    
    forecast_cols = ['September', 'October', 'November', 'December', 'January', 'February', 'March']
    case_cols = [c + ' (Cases)' for c in forecast_cols]
    
    fc_melt = fc_display.melt(id_vars=['Item Code', 'Item Description'], value_vars=case_cols, var_name='Month', value_name='Forecasted Cases')
    fc_melt['Month'] = fc_melt['Month'].str.replace(' (Cases)', '')
    
    fc_trend = fc_melt.groupby('Month')['Forecasted Cases'].sum().reindex(forecast_cols).reset_index()
    
    high_vol = fc_display[fc_display['3M_Avg_Forecast_Cases'] > 10]
    high_vol_melt = high_vol.melt(id_vars=['Item Code', 'Item Description'], value_vars=case_cols[:6], var_name='Month', value_name='Forecasted Cases')
    high_vol_melt['Month'] = high_vol_melt['Month'].str.replace(' (Cases)', '')
    
    fig_fc = px.line(high_vol_melt.groupby('Month')['Forecasted Cases'].sum().reindex(forecast_cols[:6]).reset_index(), 
                     x='Month', y='Forecasted Cases', markers=True, title='6-Month Pipeline (Only >10 Avg Cases)')
    st.plotly_chart(fig_fc, use_container_width=True)
    st.dataframe(fc_display[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + case_cols[:6]])


# -- TAB 6: SUPPLIER SCORECARD --
with tabs[5]:
    st.header("Supplier Scorecard: Reliability & Fill Rates")
    
    po_recd = po_filtered[(po_filtered['Order qty'] > 0) & (po_filtered['Recd qty'] > 0)].copy()
    po_recd['Fill Deviation %'] = ((po_recd['Recd qty'] / po_recd['Order qty']) - 1) * 100
    
    st.subheader("Order Fill Rate Deviations (Over/Under Shipments)")
    fill_summ = po_recd.groupby('Supplier')['Fill Deviation %'].mean().reset_index().sort_values('Fill Deviation %')
    fig_fill = px.bar(fill_summ, x='Fill Deviation %', y='Supplier', orientation='h', title="Average % Deviation from Ordered Qty")
    st.plotly_chart(fig_fill, use_container_width=True)
    
    st.subheader("Early vs Late Delivery Spread")
    arr_df = po_filtered.dropna(subset=['Arrival Variance (Days)'])
    fig_arr = px.histogram(arr_df, x='Arrival Variance (Days)', nbins=50, title="Distribution of Delivery Timing (Negative = Early, Positive = Late Days)")
    st.plotly_chart(fig_arr, use_container_width=True)


# -- TAB 7: CASH FLOW --
with tabs[6]:
    st.header("Cash Flow Commitments by Arrival")
    cf_df = po_active.dropna(subset=['ETA', 'USD $'])
    cf_df['ETA Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
    
    cf_summ = cf_df.groupby('ETA Month')['USD $'].sum().reset_index().sort_values('ETA Month')
    fig_cf = px.bar(cf_summ, x='ETA Month', y='USD $', title='Capital Required Based on Expected Arrival (USD)', text_auto='.2s')
    st.plotly_chart(fig_cf, use_container_width=True)


# -- TAB 8: PRICE INDEX (PPV) --
with tabs[7]:
    st.header("Procurement & Price Index (PPV Trend)")
    
    ppv_df = po_active[po_active['Purch price'] > 0].merge(sales_filtered[['Item Number', 'Last Price']], left_on='Item number', right_on='Item Number', how='left')
    ppv_df['Variance ($)'] = ppv_df['Purch price'] - ppv_df['Last Price']
    ppv_df['Variance (%)'] = np.where(ppv_df['Last Price'] > 0, (ppv_df['Variance ($)'] / ppv_df['Last Price']) * 100, 0)
    
    colX, colY = st.columns(2)
    with colX:
        st.subheader("Inflation: Active Price Increases")
        inc = ppv_df[ppv_df['Variance (%)'] > 5].sort_values('Variance (%)', ascending=False)
        st.dataframe(inc[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15).style.format({'Variance (%)': "{:.1f}%"}))
    with colY:
        st.subheader("Savings: Secured Cost Reductions")
        dec = ppv_df[ppv_df['Variance (%)'] < -5].sort_values('Variance (%)', ascending=True)
        st.dataframe(dec[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15).style.format({'Variance (%)': "{:.1f}%"}))
