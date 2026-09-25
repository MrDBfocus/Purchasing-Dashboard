import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from datetime import datetime

# ----------------------------------------
# 1. PAGE SETUP
# ----------------------------------------
st.set_page_config(
    page_title="CPJ Purchasing Dashboard", 
    layout="wide", 
    initial_sidebar_state="expanded"
)
st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")

# ----------------------------------------
# CATEGORY MAPPING DICTIONARY
# ----------------------------------------
CAT_MAP = {
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
# HELPER DATA CLEANING FUNCTIONS
# ----------------------------------------
def clean_item_code(series):
    """Standardize item codes across all source files."""
    if series is None:
        return pd.Series(dtype=str)
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
        .str.upper()
    )

def parse_mixed_dates(series):
    """Robustly parse mixed date formats (YYYYMMDD, ISO strings, Excel serials)."""
    if series is None or series.empty:
        return pd.Series(dtype='datetime64[ns]')
    
    # Handle direct datetime
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    
    # Clean string format
    s_clean = series.astype(str).str.split('.').str[0].str.strip()
    s_clean = s_clean.replace({'nan': None, 'NaT': None, 'None': None, '': None, '0': None})
    
    # Attempt parsing YYYYMMDD format
    parsed_dt = pd.to_datetime(s_clean, format='%Y%m%d', errors='coerce')
    
    # Fallback to standard flexible date parser for non-matching rows
    mask_null = parsed_dt.isna() & s_clean.notna()
    if mask_null.any():
        parsed_dt.loc[mask_null] = pd.to_datetime(s_clean[mask_null], errors='coerce')
        
    return parsed_dt

# ----------------------------------------
# 2. DATA LOADING & ENGINEERING
# ----------------------------------------
@st.cache_data
def load_data():
    # --- 1. Load Sales Analysis ---
    try:
        sales = pd.read_csv("Purchase Report Sales Analysis with Raw Depletions.csv", encoding="utf-8-sig")
        sales.columns = sales.columns.str.strip().str.replace('\ufeff', '')
        
        # Standardize Item Number
        item_col = [c for c in sales.columns if 'item' in c.lower() and 'group' not in c.lower() and 'class' not in c.lower()]
        item_col = item_col[0] if item_col else 'Item Number'
        sales.rename(columns={item_col: 'Item Number'}, inplace=True)
        
        # Filter totals & NaNs
        sales = sales[~sales['Item Description'].astype(str).str.contains('Total', case=False, na=False)]
        sales.dropna(subset=['Item Description'], inplace=True)
        sales['Item Number'] = clean_item_code(sales['Item Number'])
        
        # Map Category
        grp_col = [c for c in sales.columns if 'group' in c.lower()]
        if grp_col:
            sales['Category'] = sales[grp_col[0]].astype(str).str.strip().map(CAT_MAP).fillna('UNCLASSIFIED')
        else:
            sales['Category'] = 'UNCLASSIFIED'
            
    except Exception as e:
        st.error(f"Error loading Sales Analysis CSV: {e}")
        sales = pd.DataFrame()

    # --- 2. Load PO Dates ---
    try:
        po = pd.read_excel("PO Dates.xlsx", sheet_name="GIT Report")
        po.columns = po.columns.str.strip()
        
        # Identify key columns dynamically
        po_item_col = [c for c in po.columns if 'item' in c.lower() and 'grp' not in c.lower()][0]
        po.rename(columns={po_item_col: 'Item number'}, inplace=True)
        po['Item number'] = clean_item_code(po['Item number'])
        
        # Clean Status & Filter active codes (20-85), exclude deleted (HST=99)
        status_col = [c for c in po.columns if 'status' in c.lower() and 'code' not in c.lower()][0]
        po['Status Code'] = po[status_col].astype(str).str.extract(r'(\d+)').astype(float).fillna(0).astype(int)
        
        hst_col = [c for c in po.columns if 'hst' in c.lower()]
        if hst_col:
            po = po[po[hst_col[0]].astype(str).str.strip() != '99']
            
        po = po[(po['Status Code'] >= 20) & (po['Status Code'] <= 85)]
        
        grp_col_po = [c for c in po.columns if 'grp' in c.lower()]
        if grp_col_po:
            po['Custom Category'] = po[grp_col_po[0]].astype(str).str.strip().map(CAT_MAP).fillna('UNCLASSIFIED')
        else:
            po['Custom Category'] = 'UNCLASSIFIED'
            
    except Exception as e:
        st.error(f"Error loading PO Dates Excel: {e}")
        po = pd.DataFrame()

    # --- 3. Load Forecast ---
    try:
        fc = pd.read_excel("CPJ FORECAST.xlsx", sheet_name="Sept - Mar FCST")
        fc.columns = fc.columns.str.strip()
        fc_item_col = [c for c in fc.columns if 'code' in c.lower() or 'item' in c.lower()][0]
        fc.rename(columns={fc_item_col: 'Item Code'}, inplace=True)
        fc['Item Code'] = clean_item_code(fc['Item Code'])
    except Exception as e:
        st.error(f"Error loading CPJ Forecast Excel: {e}")
        fc = pd.DataFrame()

    # --- 4. Load Bids ---
    try:
        bids = pd.read_excel("BIDS.xlsx")
        bids.columns = bids.columns.str.strip()
        bid_item_col = [c for c in bids.columns if 'item' in c.lower()][0]
        bids.rename(columns={bid_item_col: 'Item Number'}, inplace=True)
        bids['Item Number'] = clean_item_code(bids['Item Number'])
    except Exception as e:
        st.error(f"Error loading BIDS Excel: {e}")
        bids = pd.DataFrame()

    # ---------------- Data Engineering ----------------
    if not sales.empty:
        num_cols_sales = ['Inventory Value', 'Allocated quantity', 'On Order', 'Plants', 'Stores', 'OM', 'Conversion Factor', 'Last Price']
        for col in num_cols_sales:
            if col in sales.columns:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            else:
                sales[col] = 1.0 if col == 'Conversion Factor' else 0.0

        # Safe Warehouse Inventory Calculation
        sales['Warehouse Inventory Value'] = (sales['Inventory Value'] - sales['Plants'] - sales['Stores'] - sales['OM']).clip(lower=0)
        sales['Conversion Factor'] = sales['Conversion Factor'].replace(0, 1.0).fillna(1.0)
        
        # Calculate Depletions & Stock Cases (Integer Output)
        recent_depletions = [c for c in sales.columns if 'Depletion' in c][-3:]
        if recent_depletions:
            for col in recent_depletions:
                sales[col] = pd.to_numeric(sales[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            sales['3M_Avg_Depletion'] = sales[recent_depletions].mean(axis=1).round(0).astype(int)
        else:
            sales['3M_Avg_Depletion'] = 0

        sales['Current_Stock_Cases'] = (sales['Warehouse Inventory Value'] / sales['Conversion Factor']).round(0).astype(int)

    if not po.empty:
        if not sales.empty:
            po = po.merge(sales[['Item Number', 'Conversion Factor']], left_on='Item number', right_on='Item Number', how='left')
            po['Conversion Factor'] = po['Conversion Factor'].fillna(1.0).replace(0, 1.0)
        else:
            po['Conversion Factor'] = 1.0

        buyer_col = [c for c in po.columns if 'buyer' in c.lower()]
        po['Buyer'] = po[buyer_col[0]].fillna('Unassigned') if buyer_col else 'Unassigned'
        
        for col in ['USD $', 'Order qty', 'Recd qty', 'Purch price']:
            if col in po.columns:
                po[col] = pd.to_numeric(po[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            else:
                po[col] = 0.0

        po['Order Qty (Cases)'] = (po['Order qty'] / po['Conversion Factor']).round(0).astype(int)
        po['Recd Qty (Cases)'] = (po['Recd qty'] / po['Conversion Factor']).round(0).astype(int)

        # Date Standardization
        ord_col = [c for c in po.columns if 'ord' in c.lower() and 'dt' in c.lower()][0]
        req_col = [c for c in po.columns if 'req' in c.lower() and 'dt' in c.lower()][0]
        recd_col = [c for c in po.columns if 'rec' in c.lower() and 'dt' in c.lower()]
        
        po['Ord dt'] = pd.to_datetime(po[ord_col], errors='coerce')
        po['Req dt'] = pd.to_datetime(po[req_col], errors='coerce')
        po['Recd dt'] = parse_mixed_dates(po[recd_col[0]]) if recd_col else pd.NaT
        
        arr_col = [c for c in po.columns if 'arrival' in c.lower()]
        eta_col = [c for c in po.columns if 'eta' in c.lower()]
        strip_col = [c for c in po.columns if 'strip' in c.lower()]
        yard_col = [c for c in po.columns if 'yard' in c.lower()]

        po['Arrival'] = parse_mixed_dates(po[arr_col[0]]) if arr_col else pd.NaT
        po['ETA'] = parse_mixed_dates(po[eta_col[0]]) if eta_col else pd.NaT
        po['Stripped Date'] = parse_mixed_dates(po[strip_col[0]]) if strip_col else pd.NaT
        po['Yard Date'] = parse_mixed_dates(po[yard_col[0]]) if yard_col else pd.NaT

        po['Order Month'] = po['Ord dt'].dt.to_period('M').astype(str).replace('NaT', 'Unassigned')
        po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)
        po['Arrival Variance (Days)'] = (po['Arrival'] - po['Req dt']).dt.days.fillna(0).astype(int)

        # Delivery Status Categorization
        today = pd.Timestamp.today().normalize()
        po['Delivery Status'] = 'Tracking'
        late_mask = (po['Recd dt'].isna()) & (po['Status Code'] <= 49)
        po.loc[(po['Req dt'] < today) & late_mask, 'Delivery Status'] = 'Late (Past Req Date)'
        po.loc[(po['ETA'] < today) & late_mask, 'Delivery Status'] = 'Late (Past ETA)'
        po.loc[~po['Recd dt'].isna() | (po['Status Code'] > 49), 'Delivery Status'] = 'Received'

    if not fc.empty:
        non_fc_cols = ['Brand', 'S Item Group', 'S Item Class', 'Item Code', 'Item Description', 'Supplier Name']
        fc_num_cols = [c for c in fc.columns if c not in non_fc_cols]
        for col in fc_num_cols:
            fc[col] = pd.to_numeric(fc[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0).round(0).astype(int)
            
        if len(fc_num_cols) >= 3:
            fc['3M_Avg_Forecast_Cases'] = fc[fc_num_cols[:3]].mean(axis=1).round(0).astype(int)
        else:
            fc['3M_Avg_Forecast_Cases'] = 0

    return sales, po, fc, bids

sales_df, po_df, fc_df, bids_df = load_data()

# Identify Last Buyer per Item
if not po_df.empty:
    last_buyer_df = po_df.sort_values('Ord dt').groupby('Item number')['Buyer'].last().reset_index()
    last_buyer_df.rename(columns={'Buyer': 'Last Buyer'}, inplace=True)
else:
    last_buyer_df = pd.DataFrame(columns=['Item number', 'Last Buyer'])

# ----------------------------------------
# 3. GLOBAL SLICERS (SIDEBAR)
# ----------------------------------------
st.sidebar.header("Global Filters")

valid_years = sorted([int(y) for y in po_df['Order Year'].unique() if y >= 2020]) if not po_df.empty else []
slicer_year = st.sidebar.multiselect("Order Year", valid_years, default=[])

clean_months = sorted([str(m) for m in po_df['Order Month'].unique() if str(m) not in ['NaT', 'nan', 'Unassigned']]) if not po_df.empty else []
slicer_month = st.sidebar.multiselect("Order Month", clean_months)

supplier_col = [c for c in po_df.columns if 'supplier' in c.lower() or 'vendor' in c.lower()] if not po_df.empty else []
clean_suppliers = sorted([str(s) for s in po_df[supplier_col[0]].dropna().unique() if str(s) not in ['nan', 'None']]) if supplier_col else []
slicer_supplier = st.sidebar.multiselect("Supplier", options=clean_suppliers)

clean_categories = sorted([str(c) for c in po_df['Custom Category'].dropna().unique() if str(c) not in ['nan', 'None']]) if not po_df.empty else []
slicer_category = st.sidebar.multiselect("Custom Category", options=clean_categories)

def filter_data(po, sales, fc):
    po_out = po.copy() if not po.empty else pd.DataFrame()
    sales_out = sales.copy() if not sales.empty else pd.DataFrame()
    fc_out = fc.copy() if not fc.empty else pd.DataFrame()

    if not po_out.empty:
        if slicer_year: po_out = po_out[po_out['Order Year'].isin(slicer_year)]
        if slicer_month: po_out = po_out[po_out['Order Month'].isin(slicer_month)]
        if slicer_supplier and supplier_col: po_out = po_out[po_out[supplier_col[0]].isin(slicer_supplier)]
        if slicer_category: po_out = po_out[po_out['Custom Category'].isin(slicer_category)]

    if slicer_category:
        if not sales_out.empty: sales_out = sales_out[sales_out['Category'].isin(slicer_category)]

    return po_out, sales_out, fc_out

po_filtered, sales_filtered, fc_filtered = filter_data(po_df, sales_df, fc_df)

# Active Open POs (Status 20 to 40)
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
    
    total_open_spend = int(round(po_active['USD $'].sum())) if not po_active.empty else 0
    total_po_vol = int(round(po_active['Order Qty (Cases)'].sum())) if not po_active.empty else 0
    total_inv_val = int(round(sales_filtered['Warehouse Inventory Value'].sum())) if not sales_filtered.empty else 0
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Open PO Spend", f"${total_open_spend:,.0f}")
    col2.metric("Total PO Volume (Cases)", f"{total_po_vol:,.0f}")
    col3.metric("Current Warehouse Inventory", f"${total_inv_val:,.0f}")
    
    st.divider()
    
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        st.subheader("PO Volume (Cases) by Month (Trend)")
        if not po_filtered.empty:
            vol_summ = po_filtered.groupby('Order Month')['Order Qty (Cases)'].sum().reset_index().sort_values('Order Month')
            vol_summ['Order Qty (Cases)'] = vol_summ['Order Qty (Cases)'].astype(int)
            vol_summ['3M Moving Avg'] = vol_summ['Order Qty (Cases)'].rolling(window=3, min_periods=1).mean().round(0).astype(int)
            
            fig_vol = px.bar(vol_summ, x='Order Month', y='Order Qty (Cases)', title="Volume (Cases) with 3M Trend", text_auto=',.0f')
            fig_vol.add_trace(go.Scatter(x=vol_summ['Order Month'], y=vol_summ['3M Moving Avg'], mode='lines+markers', name='3M Avg', line=dict(color='red', width=3)))
            fig_vol.update_layout(yaxis_title="Cases")
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("No PO data available for volume trend.")
            
    with row1_col2:
        st.subheader("Total Spend by Custom Category")
        if not po_filtered.empty:
            cat_summ = po_filtered.groupby('Custom Category')['USD $'].sum().reset_index().sort_values('USD $', ascending=True)
            cat_summ['USD $'] = cat_summ['USD $'].round(0).astype(int)
            fig_cat = px.bar(cat_summ, x='USD $', y='Custom Category', orientation='h', title="Spend by Operational Category", text_auto=',.0f')
            fig_cat.update_layout(xaxis_title="USD ($)")
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
            buyer_cons['USD $'] = buyer_cons['USD $'].round(0).astype(int)
            
            fig_buyer = px.pie(buyer_cons, values='USD $', names='Buyer', hole=0.4)
            fig_buyer.update_traces(textposition='inside', textinfo='value+percent+label', texttemplate="$%{value:,.0f}<br>%{percent:.0%}")
            fig_buyer.update_layout(height=500, title_text='Total Commitments by Buyer')
            st.plotly_chart(fig_buyer, use_container_width=True)
        else:
            st.info("Zero spend recorded for selected filters.")

# -- TAB 2: GIT & EXPEDITING --
with tabs[1]:
    st.header("Goods In Transit: Clearance Tracking & Exceptions")
    
    st.subheader("Clearance Metrics: Stripped vs Cleared (Yard) by Month")
    if not po_filtered.empty:
        container_col = [c for c in po_filtered.columns if 'container' in c.lower()]
        cnt_field = container_col[0] if container_col else 'PO no'

        po_filtered['Stripped Month'] = po_filtered['Stripped Date'].dt.to_period('M').astype(str)
        po_filtered['Yard Month'] = po_filtered['Yard Date'].dt.to_period('M').astype(str)
        
        strip_cnt = po_filtered[po_filtered['Stripped Month'] != 'NaT'].groupby('Stripped Month')[cnt_field].nunique().reset_index(name='Stripped Count')
        yard_cnt = po_filtered[po_filtered['Yard Month'] != 'NaT'].groupby('Yard Month')[cnt_field].nunique().reset_index(name='Cleared Count')
        
        cnt_merged = pd.merge(strip_cnt, yard_cnt, left_on='Stripped Month', right_on='Yard Month', how='outer').fillna(0)
        cnt_merged['Month'] = np.where(cnt_merged['Stripped Month'] != '0', cnt_merged['Stripped Month'], cnt_merged['Yard Month'])
        cnt_merged = cnt_merged[cnt_merged['Month'] != '0'].sort_values('Month')
        
        cnt_merged['Stripped Count'] = cnt_merged['Stripped Count'].astype(int)
        cnt_merged['Cleared Count'] = cnt_merged['Cleared Count'].astype(int)
        
        fig_clear = go.Figure()
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Stripped Count'], name='Stripped', marker_color='royalblue', text=cnt_merged['Stripped Count'], textposition='auto'))
        fig_clear.add_trace(go.Bar(x=cnt_merged['Month'], y=cnt_merged['Cleared Count'], name='Cleared (Yard)', marker_color='darkorange', text=cnt_merged['Cleared Count'], textposition='auto'))
        fig_clear.update_layout(barmode='group', title="Container Processing Counts", yaxis_title="Containers")
        st.plotly_chart(fig_clear, use_container_width=True)

    st.subheader("Pending Containers / Open Orders")
    if not po_filtered.empty:
        status_str_col = [c for c in po_filtered.columns if 'status' in c.lower() and 'code' not in c.lower()][0]
        po_no_col = [c for c in po_filtered.columns if 'po' in c.lower() and 'no' in c.lower()][0]
        supp_col = supplier_col[0] if supplier_col else 'Supplier'

        pending_git = po_filtered[(po_filtered['Recd dt'].isna()) & (po_filtered['Status Code'] <= 49)].copy()
        
        display_cols = [c for c in [cnt_field, po_no_col, status_str_col, supp_col, 'Req dt', 'ETA', 'Delivery Status'] if c in pending_git.columns]
        pending_display = pending_git[display_cols].drop_duplicates()
        
        # Format dates without decimals/times
        for d_col in ['Req dt', 'ETA']:
            if d_col in pending_display.columns:
                pending_display[d_col] = pending_display[d_col].dt.strftime('%Y-%m-%d').fillna('')

        st.dataframe(pending_display)

    st.subheader("Early vs. Late Delivery Distribution")
    if not po_filtered.empty:
        arr_df = po_filtered.dropna(subset=['Arrival Variance (Days)'])
        fig_arr = px.histogram(arr_df, x='Arrival Variance (Days)', nbins=30, title="Delivery Timing Spread (Negative = Days Early, Positive = Days Late)")
        fig_arr.update_layout(yaxis_title="PO Count")
        st.plotly_chart(fig_arr, use_container_width=True)

# -- TAB 3: INVENTORY HEALTH --
with tabs[2]:
    st.header("Inventory Health: Turnover, Dead Stock & Slow Moving")
    
    if not sales_filtered.empty:
        st.subheader("Inventory Turnover & Sell-Through Summary by Category")
        turnover_summary = sales_filtered.groupby('Category').agg(
            Total_Inv_Value=('Warehouse Inventory Value', 'sum'),
            Total_Depletion=('3M_Avg_Depletion', 'sum')
        ).reset_index()
        
        turnover_summary['Total_Inv_Value'] = turnover_summary['Total_Inv_Value'].round(0).astype(int)
        turnover_summary['Total_Depletion'] = turnover_summary['Total_Depletion'].astype(int)
        
        st.dataframe(turnover_summary.style.format({
            "Total_Inv_Value": "${:,.0f}", 
            "Total_Depletion": "{:,.0f}"
        }))

    if not sales_filtered.empty and not fc_filtered.empty:
        inv_eval = sales_filtered.merge(fc_filtered, left_on='Item Number', right_on='Item Code', how='left')
        inv_eval['3M_Avg_Forecast_Cases'] = inv_eval['3M_Avg_Forecast_Cases'].fillna(0).astype(int)
        inv_eval_filtered = inv_eval[~inv_eval['Category'].isin(EXCLUDED_CATS)]
        
        row3_col1, row3_col2 = st.columns(2)
        with row3_col1:
            st.subheader("💀 Dead Stock by Category")
            dead_stock = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 100) & (inv_eval_filtered['3M_Avg_Depletion'] == 0)]
            if not dead_stock.empty:
                dead_cat = dead_stock.groupby('Category')['Warehouse Inventory Value'].sum().reset_index()
                dead_cat['Warehouse Inventory Value'] = dead_cat['Warehouse Inventory Value'].round(0).astype(int)
                fig_dead = px.bar(dead_cat, x='Category', y='Warehouse Inventory Value', title="Dead Stock Value ($)", text_auto=',.0f')
                fig_dead.update_layout(yaxis_title="USD ($)")
                st.plotly_chart(fig_dead, use_container_width=True)
            else:
                st.info("No dead stock identified.")
                
        with row3_col2:
            st.subheader("🐢 Slow Moving Stock by Category")
            slow = inv_eval_filtered[(inv_eval_filtered['Warehouse Inventory Value'] > 0) & (inv_eval_filtered['3M_Avg_Forecast_Cases'] > 0)].copy()
            slow['Sales vs Forecast'] = slow['3M_Avg_Depletion'] / slow['3M_Avg_Forecast_Cases']
            slow_moving = slow[slow['Sales vs Forecast'] < 0.70]
            if not slow_moving.empty:
                slow_cat = slow_moving.groupby('Category')['Warehouse Inventory Value'].sum().reset_index()
                slow_cat['Warehouse Inventory Value'] = slow_cat['Warehouse Inventory Value'].round(0).astype(int)
                fig_slow = px.bar(slow_cat, x='Category', y='Warehouse Inventory Value', title="Slow Moving Stock Value ($)", text_auto=',.0f')
                fig_slow.update_layout(yaxis_title="USD ($)")
                st.plotly_chart(fig_slow, use_container_width=True)
            else:
                st.info("No slow moving stock identified.")

    st.subheader("Current Stock Levels (Top Active Items)")
    if not sales_filtered.empty:
        viz_safety_df = sales_filtered[sales_filtered['Current_Stock_Cases'] >= 10].head(25)
        fig_safety = go.Figure()
        fig_safety.add_trace(go.Bar(x=viz_safety_df['Item Description'], y=viz_safety_df['Current_Stock_Cases'], name='Current Stock (Cases)', marker_color='teal', text=viz_safety_df['Current_Stock_Cases'], textposition='auto'))
        fig_safety.update_layout(title="Actual Stock Levels (Sample Items ≥ 10 Cases)", xaxis_tickangle=-45, yaxis_title="Cases")
        st.plotly_chart(fig_safety, use_container_width=True)

# -- TAB 4: BID PERFORMANCE --
with tabs[3]:
    st.header("🏆 Bid Customer Fulfillment & Coverage")
    
    if not bids_df.empty and not sales_filtered.empty:
        bids_merged = bids_df.merge(sales_filtered[['Item Number', 'Current_Stock_Cases', 'Category']], on='Item Number', how='left')
        bids_merged['Current_Stock_Cases'] = bids_merged['Current_Stock_Cases'].fillna(0).astype(int)
        
        status_str_col = [c for c in po_active.columns if 'status' in c.lower() and 'code' not in c.lower()] if not po_active.empty else []
        if status_str_col:
            port_statuses = [s for s in po_active[status_str_col[0]].unique() if 'port' in str(s).lower() or 'yard' in str(s).lower()]
            po_port = po_active[po_active[status_str_col[0]].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases At Port')
            po_order = po_active[~po_active[status_str_col[0]].isin(port_statuses)].groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order')
        else:
            po_port = pd.DataFrame(columns=['Item number', 'Cases At Port'])
            po_order = pd.DataFrame(columns=['Item number', 'Cases On Order'])
        
        bids_merged = bids_merged.merge(po_port, left_on='Item Number', right_on='Item number', how='left').fillna(0)
        bids_merged = bids_merged.merge(po_order, left_on='Item Number', right_on='Item number', how='left').fillna(0)
        bids_merged = bids_merged.merge(last_buyer_df, left_on='Item Number', right_on='Item number', how='left')
        
        for col in ['Cases At Port', 'Cases On Order']:
            bids_merged[col] = bids_merged[col].astype(int)
        
        bids_merged['Availability Status'] = 'Not In Stock'
        bids_merged.loc[bids_merged['Cases At Port'] > 0, 'Availability Status'] = 'Inventory @ Port'
        bids_merged.loc[bids_merged['Current_Stock_Cases'] > 0, 'Availability Status'] = 'Available'
        
        colA, colB = st.columns([1, 2])
        with colA:
            fig_bids = px.pie(
                bids_merged, names='Availability Status', title="Bid Item Availability Breakdown", color='Availability Status',
                color_discrete_map={'Available': 'green', 'Inventory @ Port': 'orange', 'Not In Stock': 'red'}
            )
            st.plotly_chart(fig_bids, use_container_width=True)
            
        with colB:
            st.subheader("Critical Bid Items (No Stock & No Pipeline)")
            critical = bids_merged[(bids_merged['Current_Stock_Cases'] == 0) & (bids_merged['Cases On Order'] == 0) & (bids_merged['Cases At Port'] == 0)]
            desc_col = [c for c in critical.columns if 'desc' in c.lower()]
            cust_col = [c for c in critical.columns if 'cust' in c.lower()]
            disp_crit_cols = ['Item Number'] + ([desc_col[0]] if desc_col else []) + ([cust_col[0]] if cust_col else []) + ['Last Buyer']
            st.dataframe(critical[[c for c in disp_crit_cols if c in critical.columns]])

        st.subheader("All Bid Item Coverages")
        disp_bid_cols = ['Item Number'] + ([desc_col[0]] if desc_col else []) + ([cust_col[0]] if cust_col else []) + ['Last Buyer', 'Current_Stock_Cases', 'Cases At Port', 'Cases On Order']
        bids_disp = bids_merged[[c for c in disp_bid_cols if c in bids_merged.columns]].copy()
        st.dataframe(bids_disp.style.format({
            "Current_Stock_Cases": "{:,.0f}", 
            "Cases At Port": "{:,.0f}", 
            "Cases On Order": "{:,.0f}"
        }))
    else:
        st.info("Bid data not loaded or Sales Analysis missing.")

# -- TAB 5: FORECAST --
with tabs[4]:
    st.header("6-Month Forecast")
    
    search_term = st.text_input("🔍 Search Item Code or Description:", "")
    
    if not fc_filtered.empty:
        fc_display = fc_filtered.copy()
        if search_term:
            fc_display = fc_display[
                fc_display['Item Code'].str.contains(search_term, case=False, na=False) | 
                fc_display['Item Description'].str.contains(search_term, case=False, na=False)
            ]
        
        non_fc_cols = ['Brand', 'S Item Group', 'S Item Class', 'Item Code', 'Item Description', 'Supplier Name', '3M_Avg_Forecast_Cases']
        forecast_cols = [c for c in fc_display.columns if c not in non_fc_cols]
        
        high_vol = fc_display[fc_display['3M_Avg_Forecast_Cases'] >= 10].sort_values(by='3M_Avg_Forecast_Cases', ascending=False)
        
        if not high_vol.empty and forecast_cols:
            high_vol_melt = high_vol.melt(id_vars=['Item Code', 'Item Description'], value_vars=forecast_cols[:6], var_name='Month', value_name='Forecasted Cases')
            agg_forecast = high_vol_melt.groupby('Month', sort=False)['Forecasted Cases'].sum().reset_index()
            agg_forecast['Forecasted Cases'] = agg_forecast['Forecasted Cases'].astype(int)
            
            fig_fc = px.area(agg_forecast, x='Month', y='Forecasted Cases', title='6-Month Forecast Pipeline', text='Forecasted Cases')
            fig_fc.update_traces(textposition='top center', line=dict(width=3, color='darkmagenta'), fillcolor='rgba(139, 0, 139, 0.3)')
            fig_fc.update_layout(xaxis_title="Forecast Month", yaxis_title="Total Cases")
            st.plotly_chart(fig_fc, use_container_width=True)
            
            disp_fc_cols = ['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + forecast_cols[:6]
            st.dataframe(high_vol[[c for c in disp_fc_cols if c in high_vol.columns]].style.format("{:,.0f}"))
        else:
            st.info("No forecast items meeting the criteria (≥ 10 Cases Avg).")

        st.subheader("Category Forecast Coverage Summary")
        if not sales_filtered.empty:
            po_order_agg = po_active.groupby('Item number')['Order Qty (Cases)'].sum().reset_index(name='Cases On Order') if not po_active.empty else pd.DataFrame(columns=['Item number', 'Cases On Order'])
            
            fc_cov = fc_display.merge(sales_filtered[['Item Number', 'Current_Stock_Cases', 'Category']], left_on='Item Code', right_on='Item Number', how='left')
            fc_cov = fc_cov.merge(po_order_agg, left_on='Item Code', right_on='Item number', how='left').fillna(0)
            
            cov_summary = fc_cov.groupby('Category').agg({
                'Current_Stock_Cases': 'sum',
                'Cases On Order': 'sum',
                '3M_Avg_Forecast_Cases': 'sum'
            }).reset_index()
            
            cov_summary['Current_Stock_Cases'] = cov_summary['Current_Stock_Cases'].astype(int)
            cov_summary['Cases On Order'] = cov_summary['Cases On Order'].astype(int)
            cov_summary['3M_Avg_Forecast_Cases'] = cov_summary['3M_Avg_Forecast_Cases'].astype(int)
            
            cov_summary['MDC Stock Coverage (Months)'] = np.where(cov_summary['3M_Avg_Forecast_Cases'] > 0, (cov_summary['Current_Stock_Cases'] / cov_summary['3M_Avg_Forecast_Cases']).round(0), 0).astype(int)
            cov_summary['Total Coverage w/ On-Order (Months)'] = np.where(cov_summary['3M_Avg_Forecast_Cases'] > 0, ((cov_summary['Current_Stock_Cases'] + cov_summary['Cases On Order']) / cov_summary['3M_Avg_Forecast_Cases']).round(0), 0).astype(int)
            
            st.dataframe(cov_summary[['Category', 'Current_Stock_Cases', 'Cases On Order', '3M_Avg_Forecast_Cases', 'MDC Stock Coverage (Months)', 'Total Coverage w/ On-Order (Months)']].style.format("{:,.0f}"))

# -- TAB 6: SUPPLIER SCORECARD --
with tabs[5]:
    st.header("Supplier Scorecard: Reliability & Fill Rates")
    
    if not po_filtered.empty and supplier_col:
        vendor_search = st.text_input("🔍 Search Vendor / Supplier:", "")
        po_score_df = po_filtered.copy()
        if vendor_search:
            po_score_df = po_score_df[po_score_df[supplier_col[0]].str.contains(vendor_search, case=False, na=False)]
            
        po_recd = po_score_df[(po_score_df['Order qty'] > 0) & (po_score_df['Recd qty'] > 0)].copy()
        if not po_recd.empty:
            po_recd['Fill Deviation %'] = ((po_recd['Recd qty'] / po_recd['Order qty']) - 1) * 100
            
            st.subheader("Top 10 Inconsistent Vendors (Ordered vs Received Deviation)")
            fill_summ = po_recd.groupby(supplier_col[0])['Fill Deviation %'].apply(lambda x: abs(x).mean()).reset_index().sort_values('Fill Deviation %', ascending=False).head(10)
            fill_summ['Fill Deviation %'] = fill_summ['Fill Deviation %'].round(0).astype(int)
            
            fig_fill = px.bar(fill_summ, x='Fill Deviation %', y=supplier_col[0], orientation='h', title="Top 10 Vendors by Absolute Avg % Deviation", text_auto=',.0f')
            fig_fill.update_layout(yaxis={'categoryorder': 'total ascending'}, xaxis_title="Avg Deviation (%)")
            st.plotly_chart(fig_fill, use_container_width=True)
        else:
            st.info("No completed receipt records available for supplier scorecard calculation.")

# -- TAB 7: CASH FLOW --
with tabs[6]:
    st.header("Cash Flow Commitments by Expected Arrival")
    if not po_active.empty:
        cf_df = po_active.dropna(subset=['ETA', 'USD $']).copy()
        if not cf_df.empty:
            cf_df['ETA Month'] = cf_df['ETA'].dt.to_period('M').astype(str)
            cf_summ = cf_df.groupby('ETA Month')['USD $'].sum().reset_index().sort_values('ETA Month')
            cf_summ['USD $'] = cf_summ['USD $'].round(0).astype(int)
            
            fig_cf = px.bar(cf_summ, x='ETA Month', y='USD $', title='Capital Required Based on Expected Arrival (USD)', text_auto=',.0f')
            fig_cf.update_layout(yaxis_title="USD ($)")
            st.plotly_chart(fig_cf, use_container_width=True)
        else:
            st.info("No active POs found with valid ETA dates.")
    else:
        st.info("No active PO cash flow data available with ETA routing.")

# -- TAB 8: PRICE INDEX (PPV) --
with tabs[7]:
    st.header("Procurement & Price Index (PPV Trend)")
    st.markdown("Tracks wholesale unit price fluctuations over time for key commodities to evaluate supplier price creep.")
    
    if not po_df.empty:
        hist_prices = po_df[(po_df['Purch price'] > 0) & (po_df['Item number'].notna())].copy()
        hist_prices = hist_prices.sort_values('Ord dt')
        
        desc_po_col = [c for c in po_df.columns if 'desc' in c.lower()]
        item_search = st.text_input("🔍 Search Item for Price Trend (Code or Description):")
        
        if item_search:
            mask = hist_prices['Item number'].str.contains(item_search, case=False, na=False)
            if desc_po_col:
                mask = mask | hist_prices[desc_po_col[0]].str.contains(item_search, case=False, na=False)
            
            trend_df = hist_prices[mask]
            if not trend_df.empty:
                supp_col = supplier_col[0] if supplier_col else 'Supplier'
                fig_trend = px.line(trend_df, x='Ord dt', y='Purch price', color=supp_col, markers=True, title=f"Historical Unit Price Trend for '{item_search}'")
                fig_trend.update_layout(yaxis_title="Unit Price ($)")
                st.plotly_chart(fig_trend, use_container_width=True)
            else:
                st.warning("No price history found for this item.")
                
        if not po_active.empty and not sales_filtered.empty:
            ppv_df = po_active[po_active['Purch price'] > 0].merge(sales_filtered[['Item Number', 'Last Price']], left_on='Item number', right_on='Item Number', how='left')
            ppv_df['Last Price'] = ppv_df['Last Price'].fillna(0)
            ppv_df['Variance ($)'] = (ppv_df['Purch price'] - ppv_df['Last Price']).round(0).astype(int)
            
            ppv_df['Variance (%)'] = np.where(
                ppv_df['Last Price'] > 0, 
                ((ppv_df['Purch price'] - ppv_df['Last Price']) / ppv_df['Last Price'] * 100).round(0), 
                0
            ).astype(int)
            
            po_no_col = [c for c in po_active.columns if 'po' in c.lower() and 'no' in c.lower()][0]
            supp_col = supplier_col[0] if supplier_col else 'Supplier'
            item_desc_field = desc_po_col[0] if desc_po_col else 'Item number'

            disp_ppv_cols = [po_no_col, supp_col, item_desc_field, 'Purch price', 'Last Price', 'Variance (%)']
            
            colX, colY = st.columns(2)
            with colX:
                st.subheader("Inflation: Active Price Increases (>5%)")
                inc = ppv_df[ppv_df['Variance (%)'] > 5].sort_values('Variance (%)', ascending=False)
                if not inc.empty:
                    inc_disp = inc[[c for c in disp_ppv_cols if c in inc.columns]].head(15)
                    st.dataframe(inc_disp.style.format({
                        'Purch price': "${:,.0f}", 
                        'Last Price': "${:,.0f}", 
                        'Variance (%)': "{:,.0f}%"
                    }))
                else:
                    st.info("No significant price increases identified.")

            with colY:
                st.subheader("Savings: Secured Cost Reductions (<-5%)")
                dec = ppv_df[ppv_df['Variance (%)'] < -5].sort_values('Variance (%)', ascending=True)
                if not dec.empty:
                    dec_disp = dec[[c for c in disp_ppv_cols if c in dec.columns]].head(15)
                    st.dataframe(dec_disp.style.format({
                        'Purch price': "${:,.0f}", 
                        'Last Price': "${:,.0f}", 
                        'Variance (%)': "{:,.0f}%"
                    }))
                else:
                    st.info("No significant price reductions identified.")
    else:
        st.info("Insufficient PO historical data for PPV Calculation.")
