import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np

# ----------------------------------------
# 1. PAGE SETUP
# ----------------------------------------
st.set_page_config(page_title="F&B Purchasing Manager Suite", layout="wide", initial_sidebar_state="expanded")
st.title("📊 Purchasing Manager Command Center")

# ----------------------------------------
# 2. DATA LOADING & CLEANING
# ----------------------------------------
@st.cache_data
def load_data():
    # 1. Sales / Item Master
    sales = pd.read_csv("Purchase Report Sales Analysis with Raw Depletions (15).csv")
    sales.rename(columns={'\ufeffItem Number': 'Item Number'}, inplace=True)
    sales['Item Number'] = sales['Item Number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Clean Numbers
    sales['Conversion Factor'] = pd.to_numeric(sales['Conversion Factor'].astype(str).str.replace(',', ''), errors='coerce').fillna(1).replace(0, 1)
    sales['Inventory Value'] = pd.to_numeric(sales['Inventory Value'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    sales['Last Price'] = pd.to_numeric(sales['Last Price'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    # 2. PO Dates
    po = pd.read_excel("PO DATES.xlsx", sheet_name="GIT Report")
    po['Item number'] = po['Item number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    # Merge PO with Sales to get Conversion Factors and Categories
    po = po.merge(sales[['Item Number', 'Conversion Factor', 'Category', 'Shelf Life']], left_on='Item number', right_on='Item Number', how='left')
    po['Conversion Factor'] = po['Conversion Factor'].fillna(1)
    
    # Clean Numbers & Dates
    po['Order qty'] = pd.to_numeric(po['Order qty'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    po['Order Qty (Cases)'] = po['Order qty'] / po['Conversion Factor']
    po['Line amount'] = pd.to_numeric(po['Line amount'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    po['Purch price'] = pd.to_numeric(po['Purch price'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    date_cols = ['Ord dt', 'Req dt', 'Rec dt', 'ETD', 'ETA', 'Arrival', 'Clearance Paid']
    for col in date_cols:
        po[col] = pd.to_datetime(po[col], errors='coerce')
        
    # Lead Time Calculation (Order Date to Arrival)
    po['Actual Lead Time (Days)'] = (po['Arrival'] - po['Ord dt']).dt.days

    # 3. Forecast
    fc_q4 = pd.read_excel("CPJ FORECAST - SEP 2026-MAR 2027.xlsx", sheet_name="Sept - Dec FCST 2026", header=3)
    fc_q1 = pd.read_excel("CPJ FORECAST - SEP 2026-MAR 2027.xlsx", sheet_name="Jan-Mar 2027 FCST", header=3)
    
    fc_q4['Item Code'] = fc_q4['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    fc_q1['Item Code'] = fc_q1['Item Code'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    fc = pd.merge(fc_q4, fc_q1[['Item Code', 'Sum of Jan-27 vol', 'Sum of Feb-27 vol', 'Sum of Mar-27 vol']], on='Item Code', how='outer')
    fc = fc.merge(sales[['Item Number', 'Conversion Factor', 'Supplier Name']], left_on='Item Code', right_on='Item Number', how='left')
    fc['Conversion Factor'] = fc['Conversion Factor'].fillna(1)
    
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

# Slicers
slicer_supplier = st.sidebar.multiselect("Supplier", options=sorted(po_df['Supplier'].dropna().unique()))
slicer_category = st.sidebar.multiselect("Category", options=sorted(sales_df['Category'].dropna().unique()))
slicer_status = st.sidebar.multiselect("PO Status", options=sorted(po_df['Status'].dropna().unique()))

# Apply Filters
def filter_data(po, sales, fc):
    if slicer_supplier:
        po = po[po['Supplier'].isin(slicer_supplier)]
        sales = sales[sales['Supplier Name'].isin(slicer_supplier)]
        fc = fc[fc['Supplier Name'].isin(slicer_supplier)]
    if slicer_category:
        po = po[po['Category'].isin(slicer_category)]
        sales = sales[sales['Category'].isin(slicer_category)]
        fc = fc[fc['Item Number'].isin(sales['Item Number'])]
    if slicer_status:
        po = po[po['Status'].isin(slicer_status)]
    return po, sales, fc

po_filtered, sales_filtered, fc_filtered = filter_data(po_df, sales_df, fc_df)

# ----------------------------------------
# 4. NAVIGATION TABS (THE 10 REPORTS)
# ----------------------------------------
tabs = st.tabs([
    "1. PO / GIT", "2. Forecast", "3. Spend Analysis", "4. Supplier Scorecard", 
    "5. Inventory Val", "6. Price Variance", "7. Shelf Life", "8. Stockouts", 
    "9. Lead Time", "10. Cash Flow"
])

# -- 1. PO Status & GIT Report --
with tabs[0]:
    st.header("1. Purchase Order Status & Goods-in-Transit")
    col1, col2 = st.columns([1, 2])
    with col1:
        status_summ = po_filtered.groupby('Status')['Order Qty (Cases)'].sum().reset_index()
        fig1 = px.pie(status_summ, values='Order Qty (Cases)', names='Status', title='PO Volume by Status (CS)')
        st.plotly_chart(fig1, use_container_width=True)
    with col2:
        st.dataframe(po_filtered[['PO no', 'Supplier', 'Item number', 'ItemDescription', 'Status', 'Order Qty (Cases)', 'ETD', 'ETA', 'Container Numb']].sort_values('ETA'))

# -- 2. Demand Forecasting --
with tabs[1]:
    st.header("2. 7-Month Demand Forecasting & Pipeline")
    case_cols = [c for c in fc_filtered.columns if '(Cases)' in c]
    fc_melt = fc_filtered.melt(id_vars=['Item Code', 'Item Description_x'], value_vars=case_cols, var_name='Month', value_name='Forecasted Cases')
    fc_melt['Month'] = fc_melt['Month'].str.replace('Sum of ', '').str.replace(' (Cases)', '')
    
    fc_trend = fc_melt.groupby('Month')['Forecasted Cases'].sum().reindex(['September Vol', 'October Vol', 'November Vol', 'December Vol', 'Jan-27 vol', 'Feb-27 vol', 'Mar-27 vol']).reset_index()
    fig2 = px.bar(fc_trend, x='Month', y='Forecasted Cases', title='Forward Pipeline (Cases)', text_auto='.2s')
    st.plotly_chart(fig2, use_container_width=True)
    st.dataframe(fc_filtered[['Item Code', 'Item Description_x'] + case_cols])

# -- 3. Spend Analysis --
with tabs[2]:
    st.header("3. Spend Analysis Report")
    spend_summ = po_filtered.groupby('Supplier')['Line amount'].sum().sort_values(ascending=False).reset_index()
    fig3 = px.bar(spend_summ.head(15), x='Supplier', y='Line amount', title='Top 15 Suppliers by Total Spend (USD)')
    st.plotly_chart(fig3, use_container_width=True)

# -- 4. Supplier Scorecard --
with tabs[3]:
    st.header("4. Supplier Performance Scorecard")
    st.write("Evaluating Suppliers by On-Time Delivery (Arrival vs Requested Date)")
    po_filtered['Days Late'] = (po_filtered['Arrival'] - po_filtered['Req dt']).dt.days
    
    scorecard = po_filtered.groupby('Supplier').agg(
        Total_Orders=('PO no', 'count'),
        Avg_Days_Late=('Days Late', 'mean'),
        Total_Spend=('Line amount', 'sum')
    ).reset_index().sort_values('Total_Orders', ascending=False)
    
    st.dataframe(scorecard.style.format({"Avg_Days_Late": "{:.1f}", "Total_Spend": "${:,.2f}"}))

# -- 5. Inventory Valuation --
with tabs[4]:
    st.header("5. Inventory Valuation & Depletion")
    total_inv = sales_filtered['Inventory Value'].sum()
    st.metric("Total Filtered Inventory Value", f"${total_inv:,.2f}")
    
    inv_summ = sales_filtered.sort_values('Inventory Value', ascending=False)[['Item Number', 'Item Description', 'Category', 'Inventory Value']]
    st.dataframe(inv_summ.head(50).style.format({"Inventory Value": "${:,.2f}"}))

# -- 6. Purchase Price Variance (PPV) --
with tabs[5]:
    st.header("6. Purchase Price Variance (PPV)")
    st.write("Compares PO 'Purch price' vs Item Master 'Last Price'")
    ppv_df = po_filtered[['PO no', 'Supplier', 'Item number', 'ItemDescription', 'Purch price']].merge(
        sales_filtered[['Item Number', 'Last Price']], left_on='Item number', right_on='Item Number', how='left'
    )
    ppv_df['Variance ($)'] = ppv_df['Purch price'] - ppv_df['Last Price']
    ppv_df['Variance (%)'] = np.where(ppv_df['Last Price'] > 0, (ppv_df['Variance ($)'] / ppv_df['Last Price']) * 100, 0)
    
    # Highlight lines where we paid more than the last price
    bad_ppv = ppv_df[ppv_df['Variance ($)'] > 0].sort_values('Variance ($)', ascending=False)
    st.dataframe(bad_ppv.style.format({"Purch price": "${:.2f}", "Last Price": "${:.2f}", "Variance ($)": "${:.2f}", "Variance (%)": "{:.1f}%"}))

# -- 7. Shelf Life --
with tabs[6]:
    st.header("7. Shelf Life Analysis")
    st.write("Items currently on order with their designated Shelf Life.")
    shelf_df = po_filtered[['PO no', 'Supplier', 'Item number', 'ItemDescription', 'Order Qty (Cases)', 'Shelf Life']].dropna(subset=['Shelf Life'])
    fig7 = px.histogram(shelf_df, x='Shelf Life', y='Order Qty (Cases)', nbins=20, title='Inbound Volume by Shelf Life (Days)')
    st.plotly_chart(fig7, use_container_width=True)

# -- 8. Stockouts --
with tabs[7]:
    st.header("8. Stockout & Backorder Report")
    st.write("Items showing 0 Inventory Value but have historical depletion or future forecast.")
    out_of_stock = sales_filtered[sales_filtered['Inventory Value'] == 0]
    st.dataframe(out_of_stock[['Item Number', 'Item Description', 'Category', 'Last Purchase Date', 'Last Price']])

# -- 9. Lead Time --
with tabs[8]:
    st.header("9. Lead Time Analysis")
    st.write("Actual Days from Order Creation to Port Arrival.")
    lt_df = po_filtered.dropna(subset=['Actual Lead Time (Days)'])
    lt_summ = lt_df.groupby('Supplier')['Actual Lead Time (Days)'].mean().reset_index().sort_values('Actual Lead Time (Days)')
    
    fig9 = px.bar(lt_summ, x='Supplier', y='Actual Lead Time (Days)', title='Average Fulfillment Lead Time by Supplier')
    st.plotly_chart(fig9, use_container_width=True)

# -- 10. Cash Flow --
with tabs[9]:
    st.header("10. Open Commitments & Cash Flow")
    st.write("Upcoming financial obligations based on Estimated Arrival (ETA).")
    cf_df = po_filtered.dropna(subset=['ETA', 'Line amount'])
    cf_df['Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
    
    cf_summ = cf_df.groupby('Month')['Line amount'].sum().reset_index().sort_values('Month')
    fig10 = px.bar(cf_summ, x='Month', y='Line amount', title='Estimated Capital Required by Arrival Month (USD)', text_auto='.2s')
    st.plotly_chart(fig10, use_container_width=True)
