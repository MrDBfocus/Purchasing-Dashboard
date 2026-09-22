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

EXCLUDED_CATS = ['OPERATIONS', 'BEVERAGE SYSTEM', 'JUICE PLANT', 'MEAT PLANT']

# ----------------------------------------
# 2. DATA LOADING & ENGINEERING (WITH ERROR HANDLING)
# ----------------------------------------
@st.cache_data
def load_data():
    try:
        sales = pd.read_csv("Purchase Report Sales Analysis with Raw Depletions.csv")
        sales.dropna(subset=['Item Description'], inplace=True)
        sales.rename(columns={'﻿Item Number': 'Item Number'}, inplace=True)
        sales['Item Number'] = sales['Item Number'].astype(str).str.replace(r'\.0$', '', regex=True)
    except Exception as e:
        st.error(f"Error loading Sales Analysis CSV: {e}")
        sales = pd.DataFrame()

    try:
        po = pd.read_excel("PO Dates.xlsx", sheet_name="GIT Report")
        po = po[(po['Otp'] != 'P01') & (po['Hst'] != 99)]
        po['Item number'] = po['Item number'].astype(str).str.replace(r'\.0$', '', regex=True)
        po['Custom Category'] = po['Item grp'].map(cat_map).fillna('UNCLASSIFIED')
    except Exception as e:
        st.error(f"Error loading PO Dates Excel: {e}")
        po = pd.DataFrame()

    try:
        fc = pd.read_excel("CPJ FORECAST.xlsx", sheet_name="Sept - Mar FCST")
        fc['Item Code'] = fc['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    except Exception as e:
        st.error(f"Error loading CPJ Forecast Excel: {e}")
        fc = pd.DataFrame()

    try:
        bids = pd.read_excel("BIDS.xlsx")
        bids['Item '] = bids['Item '].astype(str).str.replace(r'\.0$', '', regex=True)
    except Exception as e:
        bids = pd.DataFrame()

    # Data Engineering & Cleaning
    if not sales.empty:
        for col in ['Inventory Value', 'Allocated quantity', 'On Order', 'Plants', 'Stores', 'OM', 'Conversion Factor', 'Last Price']:
            if col in sales.columns:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)
        sales['Warehouse Inventory Value'] = (sales['Inventory Value'] - sales['Plants'] - sales['Stores'] - sales['OM']).clip(lower=0)
        sales['Conversion Factor'] = sales['Conversion Factor'].replace(0, 1)
        
        recent_depletions = [c for c in sales.columns if 'Depletion' in c][-3:]
        if recent_depletions:
            for col in recent_depletions:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)
            sales['3M_Avg_Depletion'] = sales[recent_depletions].to_numpy().mean(axis=1)
        else:
            sales['3M_Avg_Depletion'] = 0.0
            
        sales['Current_Stock_Cases'] = sales['Warehouse Inventory Value'] / sales['Conversion Factor']

    if not po.empty and not sales.empty:
        po = po.merge(sales[['Item Number', 'Conversion Factor']], left_on='Item number', right_on='Item Number', how='left')
        po['Conversion Factor'] = po['Conversion Factor'].fillna(1)
        po['Buyer'] = po['Buyer'].fillna('Unassigned')
        
        for col in ['USD $', 'Order qty', 'Recd qty', 'Purch price']:
            if col in po.columns:
                po[col] = pd.to_numeric(po[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)
        
        po['Order Qty (Cases)'] = po['Order qty'] / po['Conversion Factor']
        po['Recd Qty (Cases)'] = po['Recd qty'] / po['Conversion Factor']
        po['Status Code'] = po['Status'].astype(str).str.extract(r'(\d+)').astype(float).fillna(0)
        
        def parse_mixed_dates(series):
            if pd.api.types.is_datetime64_any_dtype(series): return series
            s_clean = series.astype(str).str.split('.').str[0].replace({'nan': None, 'NaT': None, 'None': None, '': None})
            return pd.to_datetime(s_clean, format='%Y%m%d', errors='coerce')

        po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
        po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
        po['Arrival'] = parse_mixed_dates(po['Arrival'])
        po['ETA'] = parse_mixed_dates(po['ETA'])
        po['Recd dt'] = parse_mixed_dates(po['Rec dt'])  # Uses exact column name 'Rec dt' from spreadsheet
        po['Stripped Date'] = parse_mixed_dates(po['Stripped'])
        po['Yard Date'] = parse_mixed_dates(po['Yard'])
            
        po['Order Month'] = po['Ord dt'].dt.to_period('M').astype(str)
        po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)
        po['Arrival Variance (Days)'] = (po['Arrival'] - po['Req dt']).dt.days
        
        today = pd.Timestamp.today().normalize()
        po['Delivery Status'] = 'Tracking'
        late_mask = (po['Recd dt'].isna()) & (po['Status Code'] <= 49)
        po.loc[(po['Req dt'] < today) & late_mask, 'Delivery Status'] = 'Late (Past Req Date)'
        po.loc[(po['ETA'] < today) & late_mask, 'Delivery Status'] = 'Late (Past ETA)'

    if not fc.empty:
        fc_num_cols = [c for c in fc.columns if c not in ['Brand', 'S Item Group', 'S Item Class', 'Item Code', 'Item Description']]
        for col in fc_num_cols:
            fc[col] = pd.to_numeric(fc[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(float)
        if len(fc_num_cols) >= 3:
            fc['3M_Avg_Forecast_Cases'] = fc[fc_num_cols[:3]].to_numpy().mean(axis=1)
        else:
            fc['3M_Avg_Forecast_Cases'] = 0.0

    return sales, po, fc, bids

sales_df, po_df, fc_df, bids_df = load_data()

# ----------------------------------------
# 3. GLOBAL SLICERS (SIDEBAR)
# ----------------------------------------
st.sidebar.header("Global Filters")

valid_years = [int(y) for y in po_df['Order Year'].dropna().unique().tolist() if y >= 2025] if not po_df.empty else []
slicer_year = st.sidebar.multiselect("Order Year", sorted(valid_years))

clean_months = [str(m) for m in po_df['Order Month'].dropna().unique() if str(m) not in ['NaT', 'nan', 'None']] if not po_df.empty else []
slicer_month = st.sidebar.multiselect("Order Month", sorted(clean_months))

clean_suppliers = [str(s) for s in po_df['Supplier'].dropna().unique() if str(s) not in ['nan', 'None']] if not po_df.empty else []
slicer_supplier = st.sidebar.multiselect("Supplier", options=sorted(clean_suppliers))

clean_categories = [str(c) for c in po_df['Custom Category'].dropna().unique() if str(c) not in ['nan', 'None']] if not po_df.empty else []
slicer_category = st.sidebar.multiselect("Custom Category", options=sorted(clean_categories))

def filter_data(po, sales, fc):
    if not po.empty:
        if slicer_year: po = po[po['Order Year'].isin(slicer_year)]
        if slicer_month: po = po[po['Order Month'].isin(slicer_month)]
        if slicer_supplier: po = po[po['Supplier'].isin(slicer_supplier)]
        if slicer_category: po = po[po['Custom Category'].isin(slicer_category)]
    return po, sales, fc

po_filtered, sales_filtered, fc_filtered = filter_data(po_df, sales_df, fc_df)
po_active = po_filtered[(po_filtered['Status Code'] >= 20) & (po_filtered['Status Code'] <= 40)] if not po_filtered.empty else pd.DataFrame()

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
    total_po_vol = po_active['Order Qty (Cases)'].sum() if not po_active.empty else 0
    total_inv_val = sales_filtered['Warehouse Inventory Value'].sum() if not sales_filtered.empty else 0
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Open PO Spend", f"${total_open_spend:,.0f}")
    col2.metric("Total PO Volume (Cases)", f"{total_po_vol:,.0f}")
    col3.metric("Current Warehouse Inventory", f"${total_inv_val:,.0f}")
    
    st.divider()
    
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.subheader("PO Volume (Cases) by Month (Trend)")
        if not po_filtered.empty:
            po_vol = po_filtered[(po_filtered['Order Year'] >= 2025) & (po_filtered['Order Month'] != 'NaT')]
            vol_summ = po_vol.groupby('Order Month')['Order Qty (Cases)'].sum().reset_index().sort_values('Order Month')
            vol_summ['3M Moving Avg'] = vol_summ['Order Qty (Cases)'].rolling(window=3).mean()
            
            fig_vol = px.bar(vol_summ, x='Order Month', y='Order Qty (Cases)', title="Volume (Cases) with 3M Trend", text_auto=',.0f')
            fig_vol.add_trace(go.Scatter(x=vol_summ['Order Month'], y=vol_summ['3M Moving Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("No PO data available for volume trend.")
        
    with row1_col2:
        st.subheader("Total Spend by Custom Category")
        if not po_filtered.empty:
            cat_summ = po_filtered.groupby('Custom Category')['USD $'].sum().reset_index().sort_values('USD $', ascending=True)
            fig_cat = px.bar(cat_summ, x='USD $', y='Custom Category', orientation='h', title="Spend by Operational Category", text_auto=',.0f')
            st.plotly_chart(fig_cat, use_container_width=True)
        else:
            st.info("No spend data available.")

    st.subheader("Spend by Buyer (Consolidated <5%)")
    if not po_filtered.empty:
        buyer_summ = po_filtered.groupby('Buyer')['USD $'].sum().reset_index()
        total_spend = buyer_summ['USD $'].sum()
        if total_spend > 0:
            buyer_summ['Spend %'] = buyer_summ['USD $'] / total_spend
            buyer_summ.loc[buyer_summ['Spend %'] < 0.05, 'Buyer'] = 'Other Buyers'
            buyer_cons = buyer_summ.groupby('Buyer')['USD $'].sum().reset_index()
            
            fig_buyer = px.pie(buyer_cons, values='USD $', names='Buyer', hole=0.4, title='Total Commitments by Buyer')
            fig_buyer.update_traces(textposition='inside', textinfo='percent+label+value')
            st.plotly_chart(fig_buyer, use_container_width=True)
        else:
            st.info("Zero spend recorded for selected filters.")

# -- TAB 2: GIT & EXPEDITING --
with tabs[1]:
    st.header("Goods In Transit: Clearance Tracking & Exceptions")
    
    st.subheader("Clearance Metrics: Stripped vs Cleared (Yard) by Month")
    if not po_filtered.empty:
        po_filtered['Stripped Month'] = po_filtered['Stripped Date'].dt.to_period('M').astype(str)
        po_filtered['Yard Month'] = po_filtered['Yard Date'].dt.to_period('M').astype(str)
        
        strip_cnt = po_filtered[po_filtered['Stripped Month'] != 'NaT'].groupby('Stripped Month')['Container Numb'].nunique().reset_index(name='Stripped Count')
        yard_cnt = po_filtered[po_filtered['Yard Month'] != 'NaT'].groupby('Yard Month')['Container Numb'].nunique().reset_index(name='Cleared (Yard) Count')
        
        cnt_merged = pd.merge(strip_cnt, yard_cnt, left_on='Stripped Month', right_on='Yard Month', how='outer').fillna(0)
        cnt_merged['Month'] = np.where(cnt_merged['Stripped Month'] != 0, cnt_merged['Stripped Month'], cnt_merged['Yard Month'])
        cnt_merged = cnt_merged.sort_values('Month')
        
        fig_clear = go.Figure()
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Stripped Count'], name='Stripped', marker_color='royalblue', text=cnt_merged['Stripped Count']))
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Cleared (Yard) Count'], name='Cleared (Yard)', marker_color='darkorange', text=cnt_merged['Cleared (Yard) Count']))
        fig_clear.update_layout(barmode='group', title="Container Processing Counts (Year to Month)")
        st.plotly_chart(fig_clear, use_container_width=True)

    st.subheader("Pending Containers / Open Orders")
    if not po_filtered.empty:
        target_statuses = ['1-Not Departed', '2-On the Water', '3-At the Port', '4-In the Yard']
        git_open = po_filtered[po_filtered['Status'].isin(target_statuses)]
        pending_git = git_open[(git_open['Recd dt'].isna()) & (git_open['Status Code'] <= 49)]
        st.dataframe(pending_git[['Container Numb', 'PO no', 'Status', 'Supplier', 'Req dt', 'ETA', 'Delivery Status']].drop_duplicates())

    st.subheader("Early vs. Late Delivery Distribution")
    if not po_filtered.empty:
        arr_df = po_filtered.dropna(subset=['Arrival Variance (Days)'])
        fig_arr = px.histogram(arr_df, x='Arrival Variance (Days)', nbins=40, title="Distribution of Delivery Timing (Negative = Early, Positive = Late Days)")
        st.plotly_chart(fig_arr, use_container_width=True)

# -- TAB 3: INVENTORY HEALTH --
with tabs[2]:
    st.header("Inventory Health: Turnover, Dead Stock & Slow Moving")
    
    if not sales_filtered.empty:
        st.subheader("Inventory Turnover & Sell-Through Ratio by Category")
        turnover_summary = sales_filtered.groupby('Item Class').agg(
            Total_Inv_Value=('Warehouse Inventory Value', 'sum'),
            Total_Depletion=('3M_Avg_Depletion', 'sum')
        ).reset_index()
        st.dataframe(turnover_summary.style.format({"Total_Inv_Value": "${:,.0f}", "Total_Depletion": "{:,.0f}"}))

    if not sales_filtered.empty and not fc_filtered.empty:
        inv_eval = sales_filtered.merge(fc_filtered, left_on='Item Number', right_on='Item Code', how='left')
        inv_eval['3M_Avg_Forecast_Cases'] = inv_eval['3M_Avg_Forecast_Cases'].fillna(0)
        
        inv_eval_filtered = inv_eval[~inv_eval['Item Class'].isin(EXCLUDED_CATS)]
        
        row3_col1, row3_col2 = st.columns(2)
        with row3_col1:
            st.subheader("💀 Dead Stock by Category")
            dead_stock = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 100) & (inv_eval_filtered['3M_Avg_Depletion'] == 0)]
            if not dead_stock.empty:
                dead_cat = dead_stock.groupby('Item Class')['Warehouse Inventory Value'].sum().reset_index()
                fig_dead = px.bar(dead_cat, x='Item Class', y='Warehouse Inventory Value', title="Dead Stock Value by Category", text_auto=',.0f')
                st.plotly_chart(fig_dead, use_container_width=True)
            else:
                st.info("No dead stock identified.")
                
        with row3_col2:
            st.subheader("🐢 Slow Moving Stock by Category")
            slow = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 0) & (inv_eval_filtered['3M_Avg_Forecast_Cases'] > 0)].copy()
            slow['Sales vs Forecast'] = slow['3M_Avg_Depletion'] / slow['3M_Avg_Forecast_Cases']
            slow_moving = slow[slow['Sales vs Forecast'] < 0.70]
            if not slow_moving.empty:
                slow_cat = slow_moving.groupby('Item Class')['Warehouse Inventory Value'].sum().reset_index()
                fig_slow = px.bar(slow_cat, x='Item Class', y='Warehouse Inventory Value', title="Slow Moving Stock Value by Category", text_auto=',.0f')
                st.plotly_chart(fig_slow, use_container_width=True)
            else:
                st.info("No slow moving stock identified.")

    st.subheader("Safety Stock vs. Current Stock Levels")
    if not sales_filtered.empty:
        safety_df = sales_filtered.head(30)
        fig_safety = go.Figure()
        fig_safety.add_trace(go.Bar(x=safety_df['Item Description'], y=safety_df['Current_Stock_Cases'], name='Current Stock (Cases)', marker_color='teal'))
        fig_safety.update_layout(title="Actual Stock Levels (Sample Items)", xaxis_tickangle=-45)
        st.plotly_chart(fig_safety, use_container_width=True)

# -- TAB 4: BID PERFORMANCE --
with tabs[3]:
    st.header("🏆 Bid Customer Fulfillment & Coverage")
    
    if not bids_df.empty and not sales_filtered.empty:
        bids_merged = bids_df.merge(sales_filtered[['Item Number', 'Current_Stock_Cases', 'Category']], left_on='Item ', right_on='Item Number', how='left')
        
        port_statuses = ['3-At the Port', '4-In the Yard']
        po_port = po_active[po_active['Status'].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases At Port') if not po_active.empty else pd.DataFrame(columns=['Item number', 'Cases At Port'])
        po_order = po_active[~po_active['Status'].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order') if not po_active.empty else pd.DataFrame(columns=['Item number', 'Cases On Order'])
        
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
            st.dataframe(critical[['Item ', 'Description', 'Customer']])

        st.subheader("All Bid Item Coverages")
        st.dataframe(bids_merged[['Item ', 'Description', 'Customer', 'Current_Stock_Cases', 'Cases At Port', 'Cases On Order']])
    else:
        st.info("Bid data not loaded.")

# -- TAB 5: FORECAST --
with tabs[4]:
    st.header("6-Month Forecast Pipeline")
    
    search_term = st.text_input("🔍 Search Item Code or Description:", "")
    
    if not fc_filtered.empty:
        fc_display = fc_filtered.copy()
        if search_term:
            fc_display = fc_display[fc_display['Item Code'].str.contains(search_term, case=False, na=False) | 
                                    fc_display['Item Description'].str.contains(search_term, case=False, na=False)]
        
        forecast_cols = [c for c in fc_display.columns if c not in ['Brand', 'S Item Group', 'S Item Class', 'Item Code', 'Item Description', 'Supplier Name', '3M_Avg_Forecast_Cases']]
        
        high_vol = fc_display[fc_display['3M_Avg_Forecast_Cases'] >= 10].sort_values(by='3M_Avg_Forecast_Cases', ascending=False)
        
        if not high_vol.empty and forecast_cols:
            high_vol_melt = high_vol.melt(id_vars=['Item Code', 'Item Description'], value_vars=forecast_cols[:6], var_name='Month', value_name='Forecasted Cases')
            
            fig_fc = px.line(high_vol_melt.groupby('Month')['Forecasted Cases'].sum().reset_index(), 
                             x='Month', y='Forecasted Cases', markers=True, title='6-Month Forecast Pipeline (Items ≥ 10 Cases Avg)', text='Forecasted Cases')
            fig_fc.update_traces(textposition='top center', line=dict(width=4))
            st.plotly_chart(fig_fc, use_container_width=True)
            
            st.dataframe(high_vol[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + forecast_cols[:6]].style.format("{:,.0f}", subset=forecast_cols[:6]))
        else:
            st.info("No forecast items meeting the criteria.")

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
        fill_summ = po_recd.groupby('Supplier')['Fill Deviation %'].apply(lambda x: abs(x).mean()).reset_index().sort_values('Fill Deviation %', ascending=False).head(10)
        fig_fill = px.bar(fill_summ, x='Fill Deviation %', y='Supplier', orientation='h', title="Top 10 Inconsistent Vendors by Avg % Deviation", text_auto=',.0f')
        st.plotly_chart(fig_fill, use_container_width=True)

# -- TAB 7: CASH FLOW --
with tabs[6]:
    st.header("Cash Flow Commitments by Arrival")
    if not po_active.empty:
        cf_df = po_active.dropna(subset=['ETA', 'USD $']).copy()
        cf_df['ETA Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
        
        cf_summ = cf_df.groupby('ETA Month')['USD $'].sum().reset_index().sort_values('ETA Month')
        fig_cf = px.bar(cf_summ, x='ETA Month', y='USD $', title='Capital Required Based on Expected Arrival (USD)', text_auto=',.0f')
        st.plotly_chart(fig_cf, use_container_width=True)
    else:
        st.info("No active PO cash flow data available.")

# -- TAB 8: PRICE INDEX (PPV) --
with tabs[7]:
    st.header("Procurement & Price Index (PPV Trend)")
    
    if not po_active.empty and not sales_filtered.empty:
        ppv_df = po_active[po_active['Purch price'] > 0].merge(sales_filtered[['Item Number', 'Last Price']], left_on='Item number', right_on='Item Number', how='left')
        ppv_df['Variance ($)'] = ppv_df['Purch price'] - ppv_df['Last Price']
        ppv_df['Variance (%)'] = np.where(ppv_df['Last Price'] > 0, (ppv_df['Variance ($)'] / ppv_df['Last Price']) * 100, 0)
        
        colX, colY = st.columns(2)
        with colX:
            st.subheader("Inflation: Active Price Increases")
            inc = ppv_df[ppv_df['Variance (%)'] > 5].sort_values('Variance (%)', ascending=False)
            st.dataframe(inc[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15).style.format({'Purch price': "${:,.2f}", 'Last Price': "${:,.2f}", 'Variance (%)': "{:.1f}%"}))
        with colY:
            st.subheader("Savings: Secured Cost Reductions")
            dec = ppv_df[ppv_df['Variance (%)'] < -5].sort_values('Variance (%)', ascending=True)
            st.dataframe(dec[['PO no', 'Supplier', 'ItemDescription', 'Purch price', 'Last Price', 'Variance (%)']].head(15).style.format({'Purch price': "${:,.2f}", 'Last Price': "${:,.2f}", 'Variance (%)': "{:.1f}%"}))
    else:
        st.info("Insufficient PO price data for PPV calculation.")
