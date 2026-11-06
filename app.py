import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import io
import os
import re
import datetime
import json
import hmac
import hashlib
import time
import traceback
from contextlib import contextmanager

# ==========================================================================
# CPJ PURCHASING DASHBOARD  --  REVISION 5
#
# What changed in this revision (plain English):
#   * Bid customers and bid items come from the Bids file (BIDS.xlsx) only. The headline total is the number of DIFFERENT CPJ Codes on that file,
#     broken out by customer (each item counted once per customer, so a customer's total is the same on every tab). A discontinued item
#     (status 50 / 80 in the item master) is rated on its direct / strong substitute; with no strong substitute it is LEFT OUT of every bid
#     calculation (still listed, with its stock). New tab: "Bid Availability" (per customer, MDC stocked / out of stock on order /
#     out of stock with no order, with the item lists by category)
#   * OM stock is never counted as available (Total On-Hand, bids, cover, At-Risk list)
#   * Containers: only real container numbers (4 letters + 7 digits) are counted; SEZ, internal sales and typed notes are not.
#     Counts are compared with your own monthly counts
#   * Price Index rebuilt: $0 / $1 placeholders and unit/currency slips are held back for review, a monthly line-item
#     savings / increase section was added, sample items are excluded everywhere
#   * Inventory Health: plants, beverage systems and unclassified are left out of turnover, site chart and cover; low-value retail-site
#     items are hidden from safety stock; the "furthest below safety stock" chart was removed
#   * New forecast file layout (month headers are real dates) and new PO Dates layout are read automatically
#   * Notes and explanations fold away (click to open); Check 1 / Check 2 charts were redesigned
#   * Data Checks is now a working tool for the team: a buyer action list (what to fix, by buyer, with an export), a similar-items
#     finder for the item master, typo / data-entry checks, status rules and early warnings (items about to run out,
#     bids that cannot be filled, orders that look wrong)
#
# Earlier revisions (4): sign-in screen, new PO Dates layout, Open PO = Hst 20-49, KG / catch-weight conversion from the description,
# safety stock on the confirmed forecast, a Data Checks tab and protected tabs.
#
# Key logic decisions (all editable in the CONFIG block below):
#   * PO Otp = P01 is excluded everywhere by default (sidebar toggle to bring it back).
#   * Open PO = Hst 20-49.  Closed/received = Hst 75-85.  Hst 99 = deleted / dropped line.
#   * Item Status (Purchase Report): 20 active, 50 sell-only (still sold, not bought), 80 no selling / buying / calculation, 0-14 and 86-99 never calculated.
# ==========================================================================

st.set_page_config(page_title="CPJ Purchasing Dashboard", layout="wide", initial_sidebar_state="expanded")
try:
    pd.set_option("styler.render.max_elements", 10_000_000)   # allow big tables to be formatted
except Exception:
    pass

# ----------------------------------------
# CONFIG  (everything a business owner might want to change lives here)
# ----------------------------------------
DATA_DIR = "."                                   # folder that holds the four source files
USERS_FILE = os.environ.get("CPJ_USERS_FILE", "users.json")   # created with manage_users.py
MAX_FAILED_LOGINS = 5                            # lock a user name after this many wrong passwords ...
LOCKOUT_MINUTES = 10                             # ... for this many minutes
SESSION_HOURS = 12                               # automatic sign-out after this long

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
PLANT_GROUPS = ['RM_MP', 'RM_JP']                # raw materials / packaging used by the plants

EXCLUDED_CATS = ['OPERATIONS', 'BEVERAGE SYSTEM', 'JUICE PLANT', 'MEAT PLANT']          # not "saleable" stock
NO_SS_CATS = EXCLUDED_CATS + ['UNCLASSIFIED']                                           # left out of safety stock
COVER_CATS = ['BEVERAGE', 'DAIRY', 'DRY GROCERY', 'FRZ GROCERY', 'MEATS', 'NON-FOOD', 'SEAFOOD']   # months-of-cover categories

# Purchase-order status (the "Hst" code)
OPEN_PO = (20, 49)          # open order = Hst 20-49
CLOSED_PO = (75, 85)        # received / closed
DELETED_PO = 99
PO_LIVE = (20, 85)          # every non-deleted line we analyse

# Shipment stages (the "Status" text built from the shipping dates)
PENDING_STAGES = ['1-Not Departed', '2-On the Water', '3-At the Port', '4-Costed', '5-Yard']    # not yet emptied
LANDED_STAGES = ['3-At the Port', '4-Costed', '5-Yard', '6-Stripped']                            # arrived in Jamaica
STAGE_RANK = {'3-At the Port': 0, '4-Costed': 1, '5-Yard': 2, '2-On the Water': 3, '1-Not Departed': 4}
TRACKED_STAGES = ['3-At the Port', '4-Costed', '5-Yard', '2-On the Water']                       # shown in container list

# Item status (Purchase Report)
# 20 = active.  50 = soft-discontinued (still SOLD, no longer purchased).  80 = hard-discontinued (no selling, no buying, never calculated).
# 0-14 (incl. 10 = new, not ready) and 86-99 (deleted) are never calculated anywhere.
LIVE_STATUSES = [20]                     # the only status that counts as a normal, buyable, active item
SELL_ONLY_STATUSES = [50]                # can still be sold (sales logic stays), but is not purchased
HARD_DISC_STATUSES = [80]                # no sell / no buy / no calculation
DISCONTINUED = SELL_ONLY_STATUSES + HARD_DISC_STATUSES
DELETED = list(range(0, 15)) + list(range(86, 100))    # "never calculate" statuses: 0-14 and 86-99 (name kept from earlier versions)
MIN_SUB_MONTHS = 0.5                     # a substitute must hold more than this many months of stock
DEAD_STOCK_INCLUDES_DISCONTINUED = True
DEFAULT_LEAD_DAYS = 30                   # used only when the item has no lead time in the Purchase Report
COVER_CAP_MONTHS = 6                     # one overstocked item cannot count for more than this many months
INCLUDE_CURRENT_MONTH_SALES = False      # False = sales averages use the last 3 COMPLETE months only

SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM', 'Allocated quantity']
SITE_CS = ['MDC_Cs', 'SEP_Cs', 'MJM_Cs', 'Stores_Cs', 'Bonded_Cs', 'Plants_Cs', 'OM_Cs', 'Allocated_Cs']
ONHAND_SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants']            # Allocated is shown separately. OM is NOT counted anywhere (see NOT_COUNTED_SITES)
NOT_COUNTED_SITES = ['OM']                                                     # OM stock is never treated as available inventory
WAREHOUSE_SITES = ['MDC', 'SEP', 'MJM', 'Bonded']                              # replenishment-relevant stock (no Stores, no OM)
AVAIL_SITES = ['MDC']                                                          # bid availability = MDC stock
BID_AVAIL_SITES = ['MDC', 'SEP', 'Bonded']                                     # for bids: SEP and the bonded SEZ warehouse count as MDC stock
# Warehouse codes on the PO file -> friendly names. Add names here, e.g. {'MR1': 'Mandeville Retail'}.
WHS_LABELS = {}
# Exchange rates to US dollars. They are used when the PO Dates file has no 'USD $' column (the corrected file does not).
# 161.5 Jamaican dollars per US dollar and 1.20 US dollars per euro are the rates the earlier PO file used.
FX_TO_USD = {'USD': 1.0, 'JMD': 1.0 / 161.5, 'EUR': 1.20}

# ---- Vendor terms (VendorTerms.xlsx) --------------------------------------------------------------------------
# Terms codes such as N07, N30, N60 are read as "pay N days after the goods arrive" automatically.
# Codes that are not "net days" are listed here (days after arrival; 0 = pay when the goods arrive).
TERMS_DAYS = {'COD': 0, 'CC': 0, 'CSD': 0}
# Codes that are in the file but do not describe a real payment period (N99 is on about 180 suppliers). Their cash is timed with
# DEFAULT_TERMS_DAYS and shown as "terms not confirmed" so the buyer can fix the vendor record.
TERMS_NOT_REAL = ['N99']
DEFAULT_TERMS_DAYS = 30
# Force the terms for a supplier when the Vendor Terms file is wrong or ambiguous: {'KIMBERLY C': 'N30'} (use the 10-letter search key).
VENDOR_TERMS_OVERRIDE = {}

# ---- Price Index (PPV): which PO lines may be used, and when two prices are not comparable
PRICE_STATUS_CODES = list(range(20, 41)) + [75, 85]   # 20-40 = on order, 75 / 85 = received and stocked. Nothing else is used for prices.
PRICE_PLACEHOLDER_USD = 1.0       # a price of $1 or less ($0, $1) may be a placeholder typed in until the real price is known ...
PRICE_PLACEHOLDER_JUMP = 3.0      # ... so it is never compared with a price this many times bigger (or smaller): $1 -> $20 is not a real 1,900% increase
PRICE_MAX_CHANGE_PCT = 100.0      # any single move above +/- this percent is held back for review instead of counted
PRICE_MAX_GAP_DAYS = 730          # the earlier price must be from within this many days (older prices say nothing about this month)

# ---- Retail-site items (Safety Stock view): items mostly bought into these warehouses are 'retail items'; the cheap ones can be hidden
RETAIL_WHS = ['MR1', 'DH1', 'KR1']               # warehouse codes on the PO file for the retail sites (MR, DR/DH, KR)
RETAIL_SHARE_MIN = 0.5                           # an item is a retail item when at least this share of its PO lines go to those warehouses
RETAIL_LOW_VALUE_USD = 50.0                      # 'low value' = latest PO cost per case below this (US dollars)

# ---- Your own container counts, used as a yardstick on the GIT tab (month: (stripped, cleared)). Add a line each month.
MANUAL_CONTAINER_COUNTS = {
    '2026-01': (69, 58), '2026-02': (54, 44), '2026-03': (66, 60), '2026-04': (64, 70), '2026-05': (41, 51),
    '2026-06': (58, 55), '2026-07': (70, 70), '2026-08': (68, 65), '2026-09': (87, 80), '2026-10': (51, 45),
}

# ---- Bid Purchasing tab ---------------------------------------------------------------------------------------
BID_SITES = ['MDC', 'MJM', 'SEP', 'Bonded']              # the sites the Bid Purchasing tab counts as "our stock" (Bonded is shown as SEZ). OM is never counted.
BID_OTHER_SITES = ['Stores', 'Plants', 'OM']              # shown for information only, never counted
SITE_LABEL = {'Bonded': 'SEZ', 'Plants': 'Plants'}
BID_WATCH_MONTHS = 1.0                                    # "watch" band: covered, but within this many months of the lead time
BID_ORDER_UP_TO_MONTHS = 1.0                              # a suggested order buys lead time + this many months of demand
BID_GAP_DAYS = 7                                          # an order arriving more than this many days after stock runs out counts as a gap
MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
               'September', 'October', 'November', 'December']

today = pd.Timestamp.today().normalize()


class DataError(Exception):
    """A problem with a source file that can be explained in plain words."""


# ----------------------------------------
# SHOWING THINGS (works on old and new Streamlit versions)
# ----------------------------------------
def _st_version():
    try:
        return tuple(int(x) for x in str(st.__version__).split('.')[:2])
    except Exception:
        return (0, 0)


_WIDTH_STRETCH = _st_version() >= (1, 50)


def _wide(fn, obj, **kw):
    if _WIDTH_STRETCH:
        try:
            return fn(obj, width='stretch', **kw)
        except TypeError:
            pass
    return fn(obj, use_container_width=True, **kw)


def show_chart(fig, key=None):
    kw = {'key': key} if key else {}
    return _wide(st.plotly_chart, fig, **kw)


def show_table(obj, height=None):
    kw = {'hide_index': True}
    if height:
        kw['height'] = height
    return _wide(st.dataframe, obj, **kw)


CAP_COLLAPSE_OVER = 130                # notes longer than this many characters fold away into a click-to-open box


def cap(text, label="ℹ️ About this section (click to open)"):
    """A note under a heading. Short notes stay visible; longer explanations fold away so the page stays easy to read."""
    text = str(text)
    if len(text) <= CAP_COLLAPSE_OVER:
        return st.caption(text)
    try:
        box = st.expander(label, expanded=False)
    except Exception:                  # e.g. already inside another click-to-open box
        return st.caption(text)
    with box:
        st.markdown(text if '\\$' in text else text.replace('$', '\\$'))


# ----------------------------------------
# LOGIN
# ----------------------------------------
_DUMMY_HASH = "pbkdf2_sha256$200000$00112233445566778899aabbccddeeff$" + "0" * 64


def make_password_hash(password, iterations=200_000):
    """Used by manage_users.py. Format: pbkdf2_sha256$iterations$salt$hash"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        scheme, iters, salt_hex, hash_hex = str(stored).split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def load_users():
    """Users come from users.json (made with manage_users.py) and/or the [users] section of Streamlit secrets."""
    users = {}
    try:
        sec = st.secrets.get("users", None)
        if sec:
            for k, v in dict(sec).items():
                users[str(k).strip().lower()] = {"name": str(k), "hash": str(v)}
    except Exception:
        pass
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            for k, v in (data or {}).items():
                if isinstance(v, dict) and v.get("hash"):
                    users[str(k).strip().lower()] = {"name": v.get("name") or str(k), "hash": v["hash"]}
    except Exception:
        pass
    return users


@st.cache_resource
def _failed_logins():
    return {}                                      # user name -> list of failure times (shared by all sessions)


def _locked(username):
    now = time.time()
    fails = [t for t in _failed_logins().get(username, []) if now - t < LOCKOUT_MINUTES * 60]
    _failed_logins()[username] = fails
    return len(fails) >= MAX_FAILED_LOGINS


def require_login():
    """Show the sign-in form until a valid user signs in. Nothing else on the page loads before this."""
    ss = st.session_state
    if ss.get("auth_user") and time.time() - ss.get("auth_time", 0) < SESSION_HOURS * 3600:
        return ss["auth_user"]
    ss.pop("auth_user", None)

    users = load_users()
    _left, mid, _right = st.columns([1, 1.2, 1])
    with mid:
        st.title("📊 CPJ Purchasing Dashboard")
        st.caption("Please sign in to continue.")
        if not users:
            st.error("No users have been set up yet.")
            st.markdown("Ask your administrator to run `python manage_users.py add <user name>` "
                        "in the dashboard folder, then refresh this page.")
            st.stop()
        with st.form("login_form"):
            username = st.text_input("User name")
            password = st.text_input("Password", type="password")
            go_in = st.form_submit_button("Sign in")
        if go_in:
            key = (username or "").strip().lower()
            if _locked(key):
                st.error(f"Too many wrong passwords. Please wait {LOCKOUT_MINUTES} minutes and try again.")
            else:
                rec = users.get(key)
                good = verify_password(password or "", rec["hash"] if rec else _DUMMY_HASH)   # same effort for unknown names
                if rec and good:
                    _failed_logins().pop(key, None)
                    ss["auth_user"] = rec["name"]
                    ss["auth_time"] = time.time()
                    st.rerun()
                else:
                    _failed_logins().setdefault(key, []).append(time.time())
                    st.error("That user name or password is not correct.")
    st.stop()


current_user = require_login()
st.sidebar.markdown(f"👤 Signed in as **{current_user}**")
if st.sidebar.button("Sign out"):
    for _k in ("auth_user", "auth_time"):
        st.session_state.pop(_k, None)
    st.rerun()

st.title("📊 CPJ Purchasing Dashboard")
st.markdown("Comprehensive oversight for F&B procurement, GIT expediting, and capital allocation.")


# ----------------------------------------
# GENERAL HELPERS
# ----------------------------------------
@contextmanager
def guard():
    """Keep one tab's error from blanking every tab after it - and say it in plain words."""
    try:
        yield
    except Exception as e:
        if type(e).__name__ in ("StopException", "RerunException"):
            raise
        st.error("This section could not be displayed. The other tabs are not affected. "
                 "Please send the technical note below to your administrator.")
        with st.expander("Technical note"):
            st.code(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


def _norm_name(s):
    return re.sub(r'[^a-z0-9]+', '', str(s).lower())


def find_file(candidates):
    """Find a source file by name. Ignores Excel's temporary '~$' files and picks the newest match."""
    ok_ext = ('.xlsx', '.xlsm', '.xls', '.csv')
    try:
        files = [f for f in os.listdir(DATA_DIR) if f.lower().endswith(ok_ext) and not f.startswith('~$')]
    except Exception:
        return None
    for c in candidates:                                   # exact (ignoring case / spaces / underscores)
        exact = [f for f in files if _norm_name(os.path.splitext(f)[0]) == _norm_name(os.path.splitext(c)[0])]
        if exact:
            return os.path.join(DATA_DIR, max(exact, key=lambda f: os.path.getmtime(os.path.join(DATA_DIR, f))))
    for c in candidates:                                   # "contains" (e.g. 'PO Dates (1).xlsx')
        key = _norm_name(os.path.splitext(c)[0])
        part = [f for f in files if key and key in _norm_name(os.path.splitext(f)[0])]
        if part:
            return os.path.join(DATA_DIR, max(part, key=lambda f: os.path.getmtime(os.path.join(DATA_DIR, f))))
    return None


def parse_mixed_dates(series):
    """Dates arrive either as real dates or as 20260315-style numbers."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    n = pd.to_numeric(series, errors='coerce')
    txt = n.round().astype('Int64').astype(str)
    out = pd.to_datetime(txt, format='%Y%m%d', errors='coerce')
    rest = out.isna() & series.notna()
    if rest.any():
        out = out.where(~rest, pd.to_datetime(series.where(rest), errors='coerce'))
    return out


def pick_date(df, name):
    """New PO layout has each date twice (raw number + converted date). Use the converted one, fall back to the raw one."""
    out = None
    for col in (f'{name}.1', name):
        if col in df.columns:
            p = parse_mixed_dates(df[col])
            out = p if out is None else out.combine_first(p)
    if out is None:
        out = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')
    return out


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


def pct_of(a, b):
    """a as a percentage of b, 0 when b is 0."""
    return float(a) / float(b) * 100 if b else 0.0


def month_range(start, end):
    """Every month label 'YYYY-MM' from start to end (both Timestamps)."""
    return [p.strftime('%Y-%m') for p in pd.period_range(start, end, freq='M')]


def whs_label(code):
    return WHS_LABELS.get(code, code)


def category_axis(fig, angle=None):
    """Months are labels, not timestamps - stops charts from inventing fractional-second axes."""
    fig.update_xaxes(type='category')
    if angle is not None:
        fig.update_layout(xaxis_tickangle=angle)
    return fig


# ----------------------------------------
# UNIT & CASE-SIZE LOGIC
# ----------------------------------------
LB_PER_KG = 2.20462262
_NUM = r'(\d+(?:\.\d+)?)'
_UNIT_KG = {'KG': 1.0, 'KGS': 1.0, 'KILO': 1.0, 'KILOS': 1.0, 'K': 1.0,
            'LB': 0.45359237, 'LBS': 0.45359237, 'G': 0.001, 'GR': 0.001, 'OZ': 0.028349523}
_UNIT_RE = r'(KGS?|KILOS?|K|LBS?|GR|G|OZ)'
_AVG_RE = re.compile(r'AVG\.?\s*/?\s*(?:' + _NUM + r'\s*-\s*' + _NUM + r'|' + _NUM + r')\s*' + _UNIT_RE + r'(?![A-Z])', re.I)
_PACK_RE = re.compile(r'(?<![\d/\-.])([2-9]|\d{2,3})\s*[/xX]\s*' + _NUM + r'\s*' + _UNIT_RE + r'(?![A-Z])', re.I)
_TOKEN_RE = re.compile(_NUM + r'\s*' + _UNIT_RE + r'(?![A-Z])', re.I)
_CUT_RE = re.compile(r'\bAV(?:G)?\.?\s*/?\s*[\d.\-]*\s*$')          # description cut off in the middle of "AVG 2..."
_SZ_UNIT = {'ML': 1.0, 'L': 1000.0, 'LT': 1000.0, 'G': 1.0, 'GR': 1.0, 'KG': 1000.0, 'OZ': 28.35, 'LB': 453.6, 'LBS': 453.6, 'GAL': 3785.0}
_SZ_PACK = re.compile(r'(?<![\d/\-.])(\d{1,3})\s*[/xX]\s*' + _NUM + r'\s*(ML|LT|L|OZ|LBS|LB|GR|G|KG|GAL)\b', re.I)
_SZ_ONE = re.compile(r'(?<![\d/\-.])' + _NUM + r'\s*(ML|LT|L|OZ|LBS|LB|GR|G|KG|GAL)\b', re.I)
_PACKCOUNT = re.compile(r'(?<![\d/\-.])(\d{1,3})\s*[/xX]\s*\d', re.I)


def _kg_ok(kg):
    return 0.05 <= kg <= 2000


def parse_case_weight_kg(desc):
    """Weight of ONE CASE in KG as written in the item description, e.g. '...SMOKED 2-3 AVG 20KG' -> 20.
    Returns NaN when the description has no readable weight."""
    if not isinstance(desc, str) or not desc.strip():
        return np.nan
    d = desc.upper().replace('KG.', 'KG ').strip()
    if _CUT_RE.search(d):
        return np.nan                                       # text cut off -> let the conversion factor decide
    avgs = []
    for m in _AVG_RE.finditer(d):                           # "AVG 20KG", "AVG 28-36 KG"
        lo, hi, one, unit = m.groups()
        val = (float(lo) + float(hi)) / 2 if lo else float(one)
        avgs.append((unit.startswith('K'), val * _UNIT_KG[unit]))
    kg_avgs = [v for isk, v in avgs if isk and _kg_ok(v)]
    if kg_avgs:
        return kg_avgs[-1]
    m = _PACK_RE.search(d)                                  # "8/2.5KG" -> 20 KG
    if m:
        kg = int(m.group(1)) * float(m.group(2)) * _UNIT_KG[m.group(3)]
        if _kg_ok(kg):
            return kg
    cands = []                                              # single weights; skip piece-size ranges like "2-3LBS"
    for t in _TOKEN_RE.finditer(d):
        before = d[max(0, t.start() - 3):t.start()]
        if re.search(r'\d\s*-\s*$', before) or re.search(r'\d/\s*$', before):
            continue
        kg = float(t.group(1)) * _UNIT_KG[t.group(2)]
        if _kg_ok(kg):
            cands.append((t.group(2).startswith('K'), kg))
    kgs = [k for isk, k in cands if isk]
    if kgs:
        return kgs[-1]
    if avgs and _kg_ok(avgs[-1][1]):
        return avgs[-1][1]
    if cands:
        return cands[-1][1]
    return np.nan


def parse_unit_size(desc):
    """Size of one selling unit in grams/ml (e.g. '12/750ML' -> 750). Used only to judge substitutes."""
    if not isinstance(desc, str):
        return np.nan
    d = desc.upper()
    m = _SZ_PACK.search(d)
    if m:
        return float(m.group(2)) * _SZ_UNIT[m.group(3)]
    m = _SZ_ONE.search(d)
    if m:
        return float(m.group(1)) * _SZ_UNIT[m.group(2)]
    return np.nan


def build_unit_table(df):
    """Works out, for every item, how many stock units make one CASE.

    How the Purchase Report stores quantities (checked against inventory cost, PO prices and the forecast):
      * EA-based items with a CS alternate  -> stock & sales in EACH; case = Conversion Factor each
      * EA-based items with any other alt   -> each is the selling unit (Conversion Factor is a weight, not a pack count)
      * CS-based items                      -> sales are already in cases
      * CS-based items whose alternate is KG/LB (catch weight) -> STOCK is held in KG/LB, sales are in cases
      * KG/LB-based items                   -> stock and sales in KG/LB
    For weight-held items the weight written in the DESCRIPTION is the first choice for the case size
    (e.g. '...2-3 AVG 20KG' -> 20 KG per case); the Conversion Factor is only used when the description has no readable weight.
    """
    base = df['Base U/M'].astype(str).str.upper().str.strip()
    alt = df['Alt U/M'].astype(str).str.upper().str.strip()
    cf = pd.to_numeric(df['Conversion Factor'], errors='coerce').fillna(1.0).replace(0, 1.0)
    desc_kg = df['Item Description'].apply(parse_case_weight_kg).astype('float64')

    stock_uom = pd.Series(np.where(base.eq('CS') & alt.isin(['KG', 'LB']), alt, base), index=df.index)
    case_size = pd.Series(np.nan, index=df.index, dtype='float64')
    basis = pd.Series('No case size available', index=df.index)

    m = stock_uom.eq('CS')
    case_size[m] = 1.0
    basis[m] = 'Already in cases'

    m = stock_uom.eq('EA')
    packed = m & alt.eq('CS') & (cf > 1)
    case_size[m] = 1.0
    basis[m] = 'Each (1 per case)'
    case_size[packed] = cf[packed]
    basis[packed] = 'Pack count (conversion factor)'

    for u, mult in (('KG', 1.0), ('LB', LB_PER_KG)):         # weight-held items
        m = stock_uom.eq(u)
        d = m & desc_kg.notna()
        case_size[d] = desc_kg[d] * mult
        basis[d] = 'Description weight'
        f = m & desc_kg.isna() & (cf > 1)
        case_size[f] = cf[f]
        basis[f] = 'Conversion factor weight'

    m = stock_uom.eq('GAL') & alt.eq('CS') & (cf > 1)
    case_size[m] = cf[m]
    basis[m] = 'Pack count (conversion factor)'

    each_kg = base.eq('EA') & alt.eq('KG')                    # sold by the piece, weight per piece = conversion factor
    out = df.copy()
    out['Stock UOM'] = stock_uom
    out['Case Size'] = case_size
    out['Case Basis'] = basis
    out['Desc KG'] = desc_kg
    out['Case KG'] = np.where(stock_uom.eq('KG'), case_size,
                     np.where(stock_uom.eq('LB'), case_size / LB_PER_KG,
                     np.where(each_kg, cf, desc_kg)))
    out['Stock Divisor'] = case_size
    out['Depl Divisor'] = np.where(base.eq('CS'), 1.0, case_size)         # sales of CS items are already cases
    out['EA per Case'] = np.where(alt.eq('CS') & (cf > 1), cf, 1.0)
    out['Gal per Case'] = np.where(stock_uom.eq('GAL'), case_size, np.nan)
    return out
# ----------------------------------------
# SUBSTITUTE MATCHING (match score)
# ----------------------------------------
# Two items are first paired because they share the same Category + Format in the Purchase Report.
# Each pair then gets a 0-100 "match score" from extra checks, so a buyer can see HOW sure we are:
#   30  same Category + Format (how the pair was found)
#   15  same item group (e.g. both FF_DAIRY)  -- a different group can never rate above "Low"
#   25  same flavor
#   10  same brand
#   10  same unit size (e.g. 12/750ML and 6/750ML both have 750ML units)
#   10  similar wording in the description
#  -15  clearly different unit size (e.g. 200ML vs 1L)  /  -5 for somewhat different
# High = 80+, Medium = 60-79, Low = below 60 (or a different item group).
SUB_LEVELS = {'High only': 80, 'Medium or better': 60, 'Any (broadest)': 0}
_STOP = {'THE', 'AND', 'FOR', 'WITH', 'STD', 'AVG', 'CPJ', 'PCS', 'PKG', 'CASE', 'BOX', 'PACK', 'KG', 'KGS', 'LBS',
         'OZ', 'CHILL', 'FRZN', 'PREM', 'PRM', 'ASST', 'ASSORTED', 'BULK', 'UNDER', 'UP'}


def desc_tokens(desc):
    """Meaningful words of a description (numbers, sizes and filler words removed)."""
    t = re.sub(r'[^A-Z ]+', ' ', str(desc).upper())
    return frozenset(w for w in t.split() if len(w) > 2 and w not in _STOP)


def sub_match_scores(a, b):
    """Compare row i of `a` with row i of `b` (same length). Needs columns:
    Item Class, Flv, Brand, Unit Size, Tokens.  Returns a DataFrame with 'Match Score', 'Match Confidence', 'Why Matched'."""
    n = len(a)
    if n == 0:
        return pd.DataFrame({'Match Score': [], 'Match Confidence': [], 'Why Matched': []})
    score = np.full(n, 30.0)
    why = [['same category & format'] for _ in range(n)]

    same_cls = (a['Item Class'].values == b['Item Class'].values)
    score += np.where(same_cls, 15, 0)

    fa, fb = a['Flv'].astype(str).values, b['Flv'].astype(str).values
    same_flv = (fa == fb) & (fa != '')
    score += np.where(same_flv, 25, 0)

    ba, bb = a['Brand'].astype(str).values, b['Brand'].astype(str).values
    same_brand = (ba == bb) & (ba != '')
    score += np.where(same_brand, 10, 0)

    sa = pd.to_numeric(a['Unit Size'], errors='coerce').values
    sb = pd.to_numeric(b['Unit Size'], errors='coerce').values
    with np.errstate(divide='ignore', invalid='ignore'):
        ratio = np.minimum(sa, sb) / np.maximum(sa, sb)
    same_size = np.isfinite(ratio) & (ratio >= 0.9)
    near_size = np.isfinite(ratio) & (ratio >= 0.75) & ~same_size
    score += np.where(same_size, 10, np.where(near_size, 5, 0))
    far_size = np.isfinite(ratio) & (ratio < 0.5)
    mid_size = np.isfinite(ratio) & (ratio >= 0.5) & (ratio < 0.75)
    score -= np.where(far_size, 15, np.where(mid_size, 5, 0))

    jac = np.zeros(n)
    for i, (x, y) in enumerate(zip(a['Tokens'].values, b['Tokens'].values)):
        if x and y:
            jac[i] = len(x & y) / len(x | y)
    score += np.where(jac >= 0.5, 10, np.where(jac >= 0.3, 5, 0))

    for i in range(n):
        w = why[i]
        if same_cls[i]: w.append('same item group')
        else: w.append('DIFFERENT item group')
        if same_flv[i]: w.append('same flavor')
        if same_brand[i]: w.append('same brand')
        if same_size[i]: w.append('same unit size')
        elif near_size[i]: w.append('similar unit size')
        elif far_size[i]: w.append('DIFFERENT unit size')
        if jac[i] >= 0.5: w.append('similar wording')

    score = np.clip(score, 0, 100)
    score = np.where(same_cls, score, np.minimum(score, 55.0))          # different item group = Low at best
    conf = np.where(score >= 80, 'High', np.where(score >= 60, 'Medium', 'Low'))
    return pd.DataFrame({'Match Score': score, 'Match Confidence': conf, 'Why Matched': [', '.join(w) for w in why]},
                        index=a.index)



# ----------------------------------------
# DATA LOADING
# ----------------------------------------
def read_sheet(path, preferred, must_have):
    """Open an Excel file and return the first sheet that has the columns we need (the preferred sheet name goes first)."""
    try:
        xls = pd.ExcelFile(path)
    except PermissionError:
        raise DataError(f"'{os.path.basename(path)}' is open in another program (probably Excel). Close it and reload.")
    except Exception as e:
        raise DataError(f"Could not open '{os.path.basename(path)}'. It may be damaged or may not be a real Excel (.xlsx) file - "
                        f"try opening it in Excel and saving it again. (Technical detail: {type(e).__name__}: {e})")
    names = list(xls.sheet_names)
    order = [n for n in names if n.strip().lower() == preferred.strip().lower()] + \
            [n for n in names if n.strip().lower() != preferred.strip().lower()]
    for n in order:
        try:
            head = xls.parse(n, nrows=0)
        except Exception:
            continue
        cols = {str(c).strip() for c in head.columns}
        if set(must_have).issubset(cols):
            return xls.parse(n), n
    raise DataError(f"'{os.path.basename(path)}' has no sheet with the expected columns ({', '.join(sorted(must_have))}). "
                    f"Sheets found: {', '.join(names)}.")


def need_columns(df, cols, label):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise DataError(f"{label} is missing these columns: {', '.join(missing)}. The file layout may have changed.")


def load_sales(path):
    try:
        raw = pd.read_csv(path, encoding='utf-8-sig', low_memory=False)
    except PermissionError:
        raise DataError(f"'{os.path.basename(path)}' is open in another program. Close it and reload.")
    except Exception as e:
        raise DataError(f"Could not read the Purchase Report. It may be damaged or not a CSV file - try exporting it again. "
                        f"(Technical detail: {type(e).__name__}: {e})")
    return prepare_sales(raw)


def prepare_sales(raw):
    """Clean a Purchase Report table and work out cases, demand and stock. Used for the Purchase Report CSV and, in the same way,
    for the 'Purchase Report' sheet of the M3 workbook, so both give identical numbers for identical data."""
    raw = raw.copy()
    raw.columns = [str(c).replace('\ufeff', '').strip() for c in raw.columns]
    raw = raw.loc[:, ~raw.columns.duplicated()]
    need_columns(raw, ['Item Number', 'Item Description', 'Conversion Factor', 'Item Class', 'Item Status', 'Inventory Value'] + SITES,
                 "The Purchase Report")
    meta = {'warnings': [], 'rows_raw': len(raw)}
    s = raw.copy()
    s['Item Number'] = clean_id(s['Item Number'])
    s = s[s['Item Number'].str.fullmatch(r'\d+', na=False)]                       # drops '-', FREIGHT, blank total row
    s = s[s['Item Description'].notna()]
    _is_sample = s['Item Description'].astype(str).str.contains('SAMPLE', case=False, na=False)
    sample_codes = set(s.loc[_is_sample, 'Item Number'])                           # sample items are left out of every analysis
    s = s[~_is_sample]
    meta['sample_codes'] = sample_codes
    meta['duplicates'] = int(s['Item Number'].duplicated().sum())
    s = s.drop_duplicates('Item Number').copy()
    meta['rows'] = len(s)

    if 'Lead time' not in s.columns:
        s['Lead time'] = np.nan
        meta['warnings'].append(f"The Purchase Report has no 'Lead time' column, so every item uses the default of {DEFAULT_LEAD_DAYS} days for safety stock.")
    for col, default in (('Base U/M', 'EA'), ('Alt U/M', 'CS')):
        if col not in s.columns:
            s[col] = default
            meta['warnings'].append(f"The Purchase Report has no '{col}' column, so case conversion falls back to the Conversion Factor only.")
    for col in ('Category', 'Format', 'Flavor', 'Brand', 'Item Type'):
        if col not in s.columns:
            s[col] = ''

    s['Item Status'] = pd.to_numeric(s['Item Status'], errors='coerce').fillna(0).astype(int)
    s['Custom Category'] = s['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
    s['Item Category'] = clean_txt(s['Category']).replace('', 'UNCATEGORIZED')   # Purchase Report col AB
    s['Fmt'] = clean_txt(s['Format'])
    s['Flv'] = clean_txt(s['Flavor'])
    s['Brand'] = clean_txt(s['Brand'])
    s['Conversion Factor'] = num(s['Conversion Factor']).replace(0, 1.0)
    for site in SITES:
        s[site] = num(s[site])
    s = build_unit_table(s)
    s['Unit Size'] = s['Item Description'].apply(parse_unit_size)
    s['Tokens'] = s['Item Description'].apply(desc_tokens)

    # ---- every site quantity -> cases
    div = s['Stock Divisor']
    for site in ONHAND_SITES + NOT_COUNTED_SITES:
        s[f'{site}_Cs'] = (s[site] / div).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    s['Allocated_Cs'] = (s['Allocated quantity'] / div).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    s['Total_Cases'] = s[[f'{x}_Cs' for x in ONHAND_SITES]].sum(axis=1).clip(lower=0)
    s['WH_Cases'] = s[[f'{x}_Cs' for x in WAREHOUSE_SITES]].sum(axis=1).clip(lower=0)
    s['Avail_Cases'] = s[[f'{x}_Cs' for x in AVAIL_SITES]].sum(axis=1).clip(lower=0)
    s['No Case Size'] = s['Stock Divisor'].isna()

    # ---- inventory value (a TOTAL $ figure) shared out by each site's share of units
    s['Inventory Value'] = num(s['Inventory Value'])
    pos = s[ONHAND_SITES + NOT_COUNTED_SITES].clip(lower=0)            # OM still takes its share of the value, it just is not counted as stock
    tot_q = pos.sum(axis=1)
    wh_q = pos[WAREHOUSE_SITES].sum(axis=1)
    safe_tot = tot_q.where(tot_q > 0, 1.0)
    s['WH Value'] = np.where(tot_q > 0, s['Inventory Value'] * wh_q / safe_tot, 0.0)
    s['Unplaced Value'] = np.where(tot_q > 0, 0.0, s['Inventory Value'])             # dollars on items that show no stock quantity anywhere
    for site in ONHAND_SITES + NOT_COUNTED_SITES:
        s[f'Val_{site}'] = np.where(tot_q > 0, s['Inventory Value'] * pos[site] / safe_tot, 0.0)

    for c in ['Last Price', 'On Order']:
        if c in s.columns:
            s[c] = num(s[c])
        else:
            s[c] = 0.0

    # ---- sales (depletions): by default the last 3 COMPLETE months - never the month still in progress
    dep = []
    for c in s.columns:
        m = re.fullmatch(r'([A-Za-z]+ \d{4}) Depletion', c)
        if m:
            try:
                dep.append((pd.to_datetime(m.group(1), format='%B %Y'), c))
            except Exception:
                pass
    dep.sort()
    if not dep:
        raise DataError("The Purchase Report has no monthly 'Depletion' columns.")
    for _, c in dep:
        s[c] = num(s[c])
    this_month = today.replace(day=1)
    if INCLUDE_CURRENT_MONTH_SALES:
        complete = [c for d, c in dep]
    else:
        complete = [c for d, c in dep if d < this_month]
    recent = complete[-3:] if complete else [dep[-1][1]]
    meta['dep_months'] = [c.replace(' Depletion', '') for c in recent]
    meta['dep_cols'] = [c for _, c in dep]
    sales_in_cases = (s[recent].mean(axis=1) / s['Depl Divisor']).replace([np.inf, -np.inf], np.nan)
    s['3M_Avg_Depletion'] = sales_in_cases.fillna(0.0)
    s['3M_Avg_Depletion_Demand'] = s['3M_Avg_Depletion'].clip(lower=0)
    # the same sales average, ignoring months with no sales at all (this is how the weekly Excel report averages)
    _rec = s[recent].where(s[recent] != 0)
    s['3M_Avg_Depletion_NonZero'] = (_rec.mean(axis=1) / s['Depl Divisor']).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0)

    # forecast months that are inside the Purchase Report itself ("November 2026 Forecast" ...): the next (up to) 3 months, in cases
    fcols = []
    for c in s.columns:
        m = re.fullmatch(r'([A-Za-z]+ \d{4}) Forecast', c)
        if m:
            try:
                fcols.append((pd.to_datetime(m.group(1), format='%B %Y'), c))
            except Exception:
                pass
    fcols.sort()
    nxt = [c for d, c in fcols if d > this_month][:3]
    for _, c in fcols:
        s[c] = num(s[c])
    if nxt:
        s['PR_Fcst_Avg_Cases'] = (s[nxt].clip(lower=0).mean(axis=1) / s['Depl Divisor']).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    else:
        s['PR_Fcst_Avg_Cases'] = 0.0
    meta['pr_fcst_months'] = [c.replace(' Forecast', '') for c in nxt]

    lt = num(s['Lead time']).replace(0, np.nan)
    s['Lead Time Missing'] = lt.isna()
    s['Lead time'] = lt.fillna(DEFAULT_LEAD_DAYS)
    return s, meta


# ---- container numbers ---------------------------------------------------------------------------------------
# A real shipping container number is 4 letters + 7 digits (e.g. SMLU8514940); the 7th digit is a check digit.
# Anything else typed into the container field (SEZ transfers, internal sales, air waybill numbers, notes) is NOT a container.
CONTAINER_RE = re.compile(r'(?<![A-Z])([A-Z]{4})(\d{7})(?!\d)')
NOT_A_CONTAINER_WORDS = re.compile(r'SEZ|INTERNAL|TRANSFER|TRANFER|TRANSFR|\bPULL\b|\bMBS\b')
_ISO_LETTER = {}
_v = 10
for _ch in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    if _v % 11 == 0:
        _v += 1
    _ISO_LETTER[_ch] = _v
    _v += 1


def container_check_ok(cid):
    """ISO 6346 check digit. A container number that fails it has almost certainly been mistyped."""
    try:
        total = sum((_ISO_LETTER[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(cid[:10]))
        return (total % 11) % 10 == int(cid[10])
    except Exception:
        return False


def classify_container(text):
    """Container field -> (valid container number or '', flag). Flags: '' (fine), 'SEZ / transfer / internal', 'Wrong length (typo?)',
    'Not a container number', 'Several containers in one cell'."""
    t = str(text).strip().upper() if text is not None else ''
    if t in ('', 'NAN', 'NONE', 'NAT', '0', '-'):
        return '', ''
    if NOT_A_CONTAINER_WORDS.search(t):
        return '', 'SEZ / transfer / internal'
    found = CONTAINER_RE.findall(t)
    if found:
        return found[0][0] + found[0][1], ('Several containers in one cell' if len(found) > 1 else '')
    if re.search(r'(?<![A-Z])[A-Z]{3,4}\s?\d{5,9}(?!\d)', t):
        return '', 'Wrong length (typo?)'
    return '', 'Not a container number'



def stage_from_dates(po):
    """Same rule the Excel Status formula uses."""
    st_ = pd.Series('1-Not Departed', index=po.index)
    st_[po['Departed Date'].notna()] = '2-On the Water'
    st_[po['Arrival'].notna()] = '3-At the Port'
    st_[po['Costed Date'].notna()] = '4-Costed'
    st_[po['Yard Date'].notna()] = '5-Yard'
    st_[po['Stripped Date'].notna()] = '6-Stripped'
    return st_


def load_po(path, units):
    raw, sheet = read_sheet(path, 'GIT Report', {'PO no', 'Hst', 'Item number', 'Order qty'})
    raw.columns = [str(c).strip() for c in raw.columns]
    meta = {'warnings': [], 'sheet': sheet, 'rows_raw': len(raw)}
    meta['layout'] = 'converted date columns found' if 'Departed.1' in raw.columns else 'dates held as yyyymmdd numbers'

    # FX rates typed into the header row of the new layout (USD>JMD  161.5 | EUR>USD  1.2)
    cols = list(raw.columns)
    fx_header = {}
    for i, c in enumerate(cols[:-1]):
        if c in ('USD>JMD', 'EUR>USD'):
            try:
                fx_header[c] = float(cols[i + 1])
            except Exception:
                pass
    meta['fx_header'] = fx_header

    need_columns(raw, ['PO no', 'Otp', 'Ln', 'Hst', 'Supplier', 'Item number', 'Ord dt', 'Req dt', 'Item grp',
                       'Order qty', 'Recd qty', 'POu', 'Purch price', 'Line amount', 'Curr', 'Buyer'], "The PO Dates file")
    po = raw[raw['Hst'].notna() & raw['PO no'].notna()].copy()                 # drops blank / formula-only rows
    meta['rows'] = len(po)
    for col, default in (('Sub', 0), ('Whs', ''), ('Container Numb', ''), ('BL Number', ''), ('Description', ''),
                         ('Port of Disp', ''), ('Rec dt', pd.NaT), ('Status', ''), ('ItemDescription', ''), ('Ppu', None)):
        if col not in po.columns:
            po[col] = default
    # The corrected PO file keeps the shipping details (container, bill of lading, ports...) in the PO-header columns that end in ' H'
    # and leaves the line columns mostly blank. Use the line value when there is one, otherwise the header value.
    shipping_pairs = [('Container Numb', 'Container Num H'), ('BL Number', 'BL Number H'), ('Port of Disp', 'Port of Disp H'),
                      ('Port of Load', 'Port of Load H'), ('Ocean carrier', 'Ocean carrier H'), ('Cont Type Temp', 'Cont Type Tem H'),
                      ('Container Seal', 'Container Sea H'), ('Country of orig', 'Country of or H'), ('Broker assigned', 'Broker assign H')]
    filled_from_header = 0
    for line_col, hdr_col in shipping_pairs:
        if hdr_col in po.columns:
            base = po[line_col] if line_col in po.columns else pd.Series(np.nan, index=po.index)
            blank = base.isna() | base.astype(str).str.strip().str.lower().isin(['', 'nan', 'none', 'nat'])
            if line_col == 'Container Numb':
                filled_from_header = int((blank & po[hdr_col].notna()).sum())
            po[line_col] = base.where(~blank, po[hdr_col])
    meta['shipping_from_header'] = filled_from_header
    po['Status Code'] = pd.to_numeric(po['Hst'], errors='coerce').fillna(0)
    po['PO no'] = clean_id(po['PO no'])
    po['Otp'] = po['Otp'].fillna('').astype(str).str.strip()
    po['Whs'] = po['Whs'].fillna('').astype(str).str.strip().replace('', 'Unknown')
    po['Item number'] = clean_id(po['Item number'])
    po['Custom Category'] = po['Item grp'].map(CAT_MAP).fillna('UNCLASSIFIED')
    po['Buyer'] = po['Buyer'].fillna('Unassigned')
    po['Supplier'] = po['Supplier'].fillna('Unknown Supplier')
    po['ItemDescription'] = po['ItemDescription'].fillna('').astype(str)
    po['Curr'] = po['Curr'].fillna('USD').astype(str).str.strip().str.upper()
    for c in ['Order qty', 'Recd qty', 'Purch price', 'Line amount']:
        po[c] = num(po[c])
    po['POu'] = po['POu'].fillna('').astype(str).str.upper().str.strip()
    po['Ppu'] = po['Ppu'].where(po['Ppu'].notna(), po['POu']).fillna('').astype(str).str.upper().str.strip()

    # ---- dates (new layout has every date twice; use the converted one, fall back to the raw one)
    d = {name: pick_date(po, name) for name in ['ETD', 'Departed', 'ETA', 'Arrival', 'Costed', 'Yard', 'Stripped', 'Clearance Paid']}
    po['ETD Date'], po['Departed Date'], po['ETA'], po['Arrival'] = d['ETD'], d['Departed'], d['ETA'], d['Arrival']
    po['Costed Date'], po['Yard Date'], po['Stripped Date'] = d['Costed'], d['Yard'], d['Stripped']
    po['Clearance Paid Date'] = d['Clearance Paid']
    po['Ord dt'] = pd.to_datetime(po['Ord dt'], errors='coerce')
    po['Req dt'] = pd.to_datetime(po['Req dt'], errors='coerce')
    po['Recd dt'] = parse_mixed_dates(po['Rec dt'])
    po['Order Month'] = po['Ord dt'].dt.strftime('%Y-%m').fillna('NaT')
    po['Order Year'] = po['Ord dt'].dt.year.fillna(0).astype(int)
    po['Recd Month'] = po['Recd dt'].dt.strftime('%Y-%m').fillna('NaT')
    po['Var Days'] = (po['Recd dt'] - po['Req dt']).dt.days                      # received vs requested date

    status = po['Status'].fillna('').astype(str).str.strip()
    computed = stage_from_dates(po)
    po['Status'] = status.where(status.str.match(r'^[1-6]-'), computed)         # blank / broken Status cells are rebuilt from the dates

    # ---- money in USD. Order of preference: the file's own 'USD $' column -> rates typed in the file header -> FX_TO_USD in the settings.
    line = po['Line amount']
    usd_file = num(po['USD $']) if 'USD $' in po.columns else pd.Series(0.0, index=po.index)
    have_usd = bool((usd_file.fillna(0) != 0).any())
    fx_default = {k: float(v) for k, v in FX_TO_USD.items()}
    if fx_header.get('USD>JMD'):
        fx_default['JMD'] = 1.0 / fx_header['USD>JMD']
    if fx_header.get('EUR>USD'):
        fx_default['EUR'] = fx_header['EUR>USD']
    if have_usd:
        ratio = (usd_file / line.where(line != 0)).replace([np.inf, -np.inf], np.nan)
        fx_file = ratio.groupby(po['Curr']).median()
        fx_file = fx_file[fx_file > 0]
        fx = po['Curr'].map(fx_file).fillna(po['Curr'].map(fx_default))
        po['USD $'] = np.where(usd_file.fillna(0) != 0, usd_file, line * fx.fillna(0.0))
        meta['fx_source'] = "the 'USD $' column in the PO file"
    else:
        fx = po['Curr'].map(fx_default)
        po['USD $'] = line * fx.fillna(0.0)
        meta['fx_source'] = "the exchange rates in the dashboard settings (FX_TO_USD)"
    unknown = sorted(set(po.loc[fx.isna(), 'Curr']))
    meta['unknown_currency'] = unknown
    if unknown:
        meta['warnings'].append("These currencies have no exchange rate, so their lines count as $0: " + ", ".join(unknown) +
                                ". Add them to FX_TO_USD at the top of the app file.")
    po['FX'] = fx.fillna(0.0)
    po['Price USD'] = po['Purch price'] * po['FX']
    meta['fx_used'] = {k: float(v) for k, v in po.groupby('Curr')['FX'].first().items() if v > 0}

    # ---- item lookup & case conversion
    cols_lk = ['Item Number', 'Conversion Factor', 'Item Status', 'Item Category', 'Case KG', 'EA per Case', 'Gal per Case']
    if units is not None and not units.empty:
        lk = units[cols_lk].copy()
    else:
        lk = pd.DataFrame(columns=cols_lk)
    lk['_found'] = True
    lk = lk.rename(columns={'Item Number': '_k'})
    po = po.merge(lk, left_on='Item number', right_on='_k', how='left').drop(columns='_k')
    po['In Purchase Report'] = po['_found'].fillna(False).astype(bool)
    po = po.drop(columns='_found')
    po['Item Category'] = po['Item Category'].fillna('UNCATEGORIZED')

    pou = po['POu']
    div = pd.Series(np.nan, index=po.index, dtype='float64')
    div[pou.isin(['CS', 'DR'])] = 1.0                                           # case / drum count as one
    m = pou.eq('EA'); div[m] = po.loc[m, 'EA per Case'].astype('float64')
    m = pou.eq('KG'); div[m] = po.loc[m, 'Case KG'].astype('float64')           # KG lines: KG per case (description first)
    m = pou.eq('LB'); div[m] = po.loc[m, 'Case KG'].astype('float64') * LB_PER_KG
    m = pou.eq('GAL'); div[m] = po.loc[m, 'Gal per Case'].astype('float64')
    div = div.where(div > 0)
    po['Case Divisor'] = div
    po['Order Qty (Cases)'] = po['Order qty'] / div
    po['Recd Qty (Cases)'] = po['Recd qty'] / div
    po['Convertible'] = div.notna()
    po['Catch Weight'] = po['Ppu'].isin(['KG', 'LB']) | pou.isin(['KG', 'LB'])

    # independent check of catch-weight lines: KG per case implied by the money on the line
    chk = pou.eq('CS') & po['Ppu'].isin(['KG', 'LB']) & (po['Purch price'] > 0) & (po['Order qty'] > 0)
    implied = (po['Line amount'] / (po['Purch price'] * po['Order qty']).where(chk)) * np.where(po['Ppu'].eq('LB'), 0.45359237, 1.0)
    po['Implied KG per Case'] = implied.replace([np.inf, -np.inf], np.nan)
    case_kg = po['Case KG'].astype('float64')
    po['Weight Dev %'] = ((po['Implied KG per Case'] / case_kg.where(case_kg > 0)) - 1) * 100

    # ---- delivery status
    po['Delivery Status'] = 'Not Tracked'
    in_transit = po['Status'].isin(PENDING_STAGES)
    open_nr = po['Recd dt'].isna() & (po['Status Code'] <= OPEN_PO[1])
    po.loc[in_transit & open_nr, 'Delivery Status'] = 'On Track'
    po.loc[in_transit & open_nr & (po['Req dt'] < today), 'Delivery Status'] = 'Late (Past Req Date)'
    po.loc[po['Status'] == '6-Stripped', 'Delivery Status'] = 'Stripped'
    po.loc[po['Recd dt'].notna() | (po['Status Code'] > OPEN_PO[1]), 'Delivery Status'] = 'Received'

    # ---- one key per physical shipment (a container number can be re-used on a later voyage).
    # Only a real container number (4 letters + 7 digits) counts as a container; SEZ transfers, internal sales, air waybills etc. do not.
    cont = po['Container Numb'].fillna('').astype(str).str.strip()
    cont = cont.where(~cont.str.lower().isin(['nan', 'none', 'nat']), '')
    po['Container Raw'] = cont
    cl = cont.map(classify_container)
    po['Container ID'] = cl.map(lambda x: x[0])
    po['Container Flag'] = cl.map(lambda x: x[1])
    po['Container Check Digit OK'] = po['Container ID'].map(lambda c: container_check_ok(c) if c else True)
    bl = po['BL Number'].fillna('').astype(str).str.strip()
    bl = bl.where(~bl.str.lower().isin(['nan', 'none', 'nat']), '')
    alt_key = po['Arrival'].dt.strftime('%Y%m%d').fillna('')
    po['Shipment Key'] = (po['Container ID'] + '|' + bl.where(bl != '', alt_key)).where(po['Container ID'] != '', '')
    meta['container_lines'] = int((po['Container ID'] != '').sum())
    meta['container_rejected'] = po.loc[po['Container Flag'].isin(['SEZ / transfer / internal', 'Wrong length (typo?)', 'Not a container number']), 'Container Flag'].value_counts().to_dict()
    return po, meta


_MON_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTH_NAMES)}
_MONTH_WORDS = set(_MON_ABBR) | {m.lower() for m in MONTH_NAMES} | {'sept'}


def forecast_month_key(c):
    """A forecast column header -> (year, month), or None if the header is not a month.
    Understands: real Excel dates (the new file shows them as 'Nov-26'), 'Nov-26' / 'Nov 2026' text, and plain month names ('January', year unknown -> None year)."""
    if isinstance(c, (pd.Timestamp, datetime.datetime, datetime.date)):
        try:
            return (int(c.year), int(c.month))
        except Exception:
            return None
    t = str(c).strip()
    if not t or t.lower().startswith('unnamed'):
        return None
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?[-\s/']*(\d{2}|\d{4})?", t)
    if m and m.group(1).lower() in _MONTH_WORDS:
        yr = m.group(2)
        mo = _MON_ABBR[m.group(1)[:3].lower()]
        if yr is None:
            return (None, mo)
        yr = int(yr)
        return (2000 + yr if yr < 100 else yr, mo)
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-\d{2})?(?:[ T].*)?", t)             # '2026-11-26 00:00:00' written as text
    if m and 1 <= int(m.group(2)) <= 12:
        return (int(m.group(1)), int(m.group(2)))
    return None


def load_forecast(path):
    xls_name = os.path.basename(path)
    try:
        xls = pd.ExcelFile(path)
    except PermissionError:
        raise DataError(f"'{xls_name}' is open in another program. Close it and reload.")
    except Exception as e:
        raise DataError(f"Could not open '{xls_name}'. It may be damaged or may not be a real Excel (.xlsx) file - "
                        f"try opening it in Excel and saving it again. (Technical detail: {type(e).__name__}: {e})")
    names = list(xls.sheet_names)
    order = [n for n in names if 'fcst' in n.lower() or 'forecast' in n.lower()] + [n for n in names if not ('fcst' in n.lower() or 'forecast' in n.lower())]
    fc, used = None, None
    for n in order:
        try:
            t = xls.parse(n)
        except Exception:
            continue
        t.columns = [c if isinstance(c, (pd.Timestamp, datetime.datetime, datetime.date)) else str(c).strip() for c in t.columns]
        if 'Item Code' in t.columns and any(forecast_month_key(c) for c in t.columns):
            fc, used = t, n
            break
    if fc is None:
        raise DataError(f"'{xls_name}' has no sheet with 'Item Code' and month columns (for example Nov-26, Dec-26 ... or January ... December).")
    meta = {'warnings': [], 'sheet': used, 'rows_raw': len(fc)}

    # ---- month columns -> (year, month); a header with no year (plain 'January') gets the year it must belong to
    keyed = []
    for c in fc.columns:
        k = forecast_month_key(c)
        if k:
            keyed.append([c, k[0], k[1]])
    if any(y is None for _, y, _m in keyed):
        yr, prev = today.year, 0
        for row in keyed:
            if row[1] is None:
                if row[2] < prev:
                    yr += 1
                row[1] = yr
            else:
                yr = row[1]
            prev = row[2]
    seen, month_cols, labels = set(), [], {}
    for c, y, mo in keyed:
        if (y, mo) in seen:
            meta['warnings'].append(f"The forecast has two columns for {MONTH_NAMES[mo - 1][:3]}-{y % 100:02d}; only the first is used.")
            continue
        seen.add((y, mo))
        lab = f"{MONTH_NAMES[mo - 1][:3]}-{y % 100:02d}"
        month_cols.append((c, lab, y * 12 + mo))
        labels[c] = lab
    fc = fc[['Item Code'] + [c for c, _l, _k in month_cols] + (['Item Description'] if 'Item Description' in fc.columns else [])].rename(columns=labels)
    mcols = [l for _c, l, _k in month_cols]
    mindex = {l: k for _c, l, k in month_cols}

    fc['Item Code'] = clean_id(fc['Item Code'])
    fc = fc[fc['Item Code'].str.fullmatch(r'\d+', na=False)].copy()
    neg_cells = 0
    for c in mcols:
        fc[c] = num(fc[c])                                  # NOT rounded: slow items forecast fractions of a case
        neg_cells += int((fc[c] < 0).sum())
        fc[c] = fc[c].clip(lower=0)                         # a negative forecast means "no demand", never negative demand
    if 'Item Description' not in fc.columns:
        fc['Item Description'] = ''
    meta['negative_cells'] = neg_cells
    meta['duplicate_codes'] = int(fc['Item Code'].duplicated().sum())
    agg = {c: 'sum' for c in mcols}
    agg['Item Description'] = 'first'
    fc = fc.groupby('Item Code', as_index=False).agg(agg)

    this_k = today.year * 12 + today.month                  # months AFTER the current one, in order, until the file runs out
    upcoming = [l for l in sorted(mcols, key=mindex.get) if mindex[l] > this_k]
    run = []                                                 # keep only an unbroken run of months starting right after this month
    for l in upcoming:
        if not run or mindex[l] == mindex[run[-1]] + 1:
            run.append(l)
        else:
            break
    if len(run) >= 3 and mindex[run[0]] == this_k + 1:
        cols3, cols6 = run[:3], run[:6]
    elif len(run) >= 3:
        cols3, cols6 = run[:3], run[:6]
        meta['warnings'].append(f"The forecast file starts at {run[0]}, not next month - those months are used as the near-term forecast. Please check the file is current.")
    else:
        ordered = sorted(mcols, key=mindex.get)
        cols3, cols6 = ordered[-3:], ordered[-6:]
        meta['warnings'].append("The forecast file does not cover the next 3 months - the latest months in the file are used instead. Please load a newer forecast.")
    fc['3M_Avg_Forecast_Cases'] = fc[cols3].mean(axis=1)
    meta.update({'cols3': cols3, 'cols6': cols6, 'all': sorted(mcols, key=mindex.get), 'first_month': min(mcols, key=mindex.get), 'last_month': max(mcols, key=mindex.get)})
    return fc, meta


def load_bids(path):
    """The Bids file (BIDS.xlsx) is the one place that says WHO bids and WHICH items each customer was set up with.
    One row per customer / bid / item. Customer names are tidied (spaces, capital letters) so one customer is never split in two;
    rows whose CPJ Code is not an item number are kept aside (meta['dropped']) so nothing disappears without a trace."""
    raw, sheet = read_sheet(path, 'Sheet2', {'CPJ Code', 'CPJ Item Description', 'Customer Name', 'CPJ Class', 'Bid Status'})
    raw.columns = [str(c).strip() for c in raw.columns]
    b = raw.rename(columns={'CPJ Code': 'Item Code', 'CPJ Item Description': 'Item Description',
                            'Customer Name': 'Customer', 'CPJ Class': 'Item Class'})
    b = b.loc[:, ~b.columns.duplicated()]
    n_file = len(b)
    # customer names: trim, collapse double spaces, and treat 'RIU' / 'Riu' / 'Riu ' as one customer (the most common spelling is shown)
    cust = b['Customer'].fillna('').astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    key = cust.str.lower()
    shown = cust.groupby(key).agg(lambda s: s.value_counts().index[0])
    b['Customer'] = key.map(shown).where(key != '', '')
    blank_cust = b['Customer'] == ''
    raw_code = b['Item Code'].astype(str).str.strip()
    b['Item Code'] = clean_id(b['Item Code'])
    ok = b['Item Code'].str.fullmatch(r'\d{4,9}', na=False) & ~blank_cust
    dropped = b.loc[~ok, [c for c in ('Customer', 'Bid Name', 'Item Code', 'Item Description') if c in b.columns]].copy()
    dropped['Code As Typed'] = raw_code[~ok]
    dropped['Why'] = np.where(blank_cust[~ok], 'No customer name', 'CPJ Code is blank or not an item number')
    b = b[ok].copy()
    b['Custom Category'] = b['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
    b['Item Status'] = pd.to_numeric(b['Item Status'], errors='coerce')            # the status typed on the bid sheet (the item master is the one that counts)
    b['Bid Status'] = b['Bid Status'].fillna('').astype(str).str.strip()
    if 'Bid Name' not in b.columns:
        b['Bid Name'] = ''
    b['Bid Name'] = b['Bid Name'].fillna('').astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    for src, dst in (('BID Start Date (mm/dd/yyyy)', 'Bid Start'), ('BID End Date', 'Bid End')):
        b[dst] = pd.to_datetime(b[src], errors='coerce') if src in b.columns else pd.NaT
    b['Quotes Won'] = pd.to_numeric(b['Quotes Won'], errors='coerce') if 'Quotes Won' in b.columns else np.nan
    warns = []
    if len(dropped):
        warns.append(f"{len(dropped):,} row(s) of the Bids file were skipped because the CPJ Code is not an item number (listed on the Data Checks tab).")
    return b, {'warnings': warns, 'sheet': sheet, 'rows': len(b), 'rows_in_file': n_file, 'dropped': dropped}


def _sheet_named(names, wanted):
    k = _norm_name(wanted)
    for n in names:
        if _norm_name(n) == k:
            return n
    return None


_ITEM_HEADS = {'item', 'itemnumber', 'itemcode', 'vendoritem', 'cpjcode', 'itemno'}


def _item_ids_on_sheet(xls, sheet):
    """Item codes listed on a sheet: looks for a column headed 'Item' (or similar) in the first rows and returns the codes below it."""
    t = xls.parse(sheet, header=None)
    top = t.head(12)
    for r in range(len(top)):
        for c in range(top.shape[1]):
            if _norm_name(top.iat[r, c]) in _ITEM_HEADS:
                ids = clean_id(t.iloc[r + 1:, c])
                return set(ids[ids.str.fullmatch(r'\d{5,9}', na=False)])
    return set()


M3_LIST_SHEETS = [('Master-All', 'Weekly report list'), ('Sandals Report', 'Sandals'), ('RIU Report', 'RIU'), ('Sandals Bid', 'Sandals bid list')]


BID_UNIVERSE_KEEP = {'Customer Name': 'Customer', 'Bid Name': 'Bid Name', 'Bid Status': 'Bid Status', 'CPJ Code': 'Item Code', 'CPJ Item Description': 'Item Description',
                     'CPJ Class': 'Item Class', 'Item Status': 'Item Status', 'Quotes Won': 'Quotes Won', 'Avg Monthly Usage': 'Avg Monthly Usage',
                     'Customer Est. Usage': 'Customer Est. Usage', 'Customer Description': 'Customer Description', 'CPJ UOM': 'CPJ UOM',
                     'Primary Vendor': 'Primary Vendor', 'BID End Date': 'Bid End Date'}


def load_bid_universe(xls, sheet):
    """The 'Bid Universe' sheet: every item quoted to every customer. Returns (clean table, rows whose CPJ Code is not an item number, meta)."""
    t = xls.parse(sheet, header=None)
    hdr = None
    for r in range(min(len(t), 60)):
        vals = [str(x).strip() for x in t.iloc[r].tolist()]
        if 'Customer Name' in vals and 'CPJ Code' in vals:
            hdr = r
            break
    if hdr is None:
        raise DataError("The 'Bid Universe' sheet has no header row containing 'Customer Name' and 'CPJ Code'.")
    u = t.iloc[hdr + 1:].copy()
    u.columns = [str(x).strip() for x in t.iloc[hdr].tolist()]
    u = u.loc[:, ~u.columns.duplicated()]
    missing = [c for c in ('Customer Name', 'CPJ Code', 'Item Status', 'Bid Status') if c not in u.columns]
    if missing:
        raise DataError("The 'Bid Universe' sheet is missing these columns: " + ", ".join(missing))
    for c in BID_UNIVERSE_KEEP:
        if c not in u.columns:
            u[c] = np.nan
    u = u[list(BID_UNIVERSE_KEEP)].rename(columns=BID_UNIVERSE_KEEP)
    u['Customer'] = clean_txt(u['Customer'])
    u = u[u['Customer'] != ''].copy()
    raw_code = u['Item Code'].astype(str).str.strip()
    u['Item Code'] = clean_id(u['Item Code'])
    ok = u['Item Code'].str.fullmatch(r'\d{5,9}', na=False)
    dirty = u[~ok].copy()
    dirty['Code As Typed'] = raw_code[~ok]
    u = u[ok].copy()
    u['Item Status'] = pd.to_numeric(u['Item Status'], errors='coerce')
    u['Quotes Won'] = pd.to_numeric(u['Quotes Won'], errors='coerce').fillna(0)
    u['Avg Monthly Usage'] = pd.to_numeric(u['Avg Monthly Usage'], errors='coerce')
    u['Bid Status'] = clean_txt(u['Bid Status'])
    u['Bid Name'] = clean_txt(u['Bid Name'])
    u['Item Description'] = clean_txt(u['Item Description'])
    u['Custom Category'] = u['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
    meta = {'rows': len(u), 'dirty_rows': len(dirty), 'customers': int(u['Customer'].nunique()), 'sheet': sheet}
    return u.reset_index(drop=True), dirty.reset_index(drop=True), meta



def load_m3(path):
    """The M3 'Bid Purchasing' workbook. Needs the 'Purchase Report' sheet; WMS, manual and the item lists are optional extras."""
    name = os.path.basename(path)
    try:
        xls = pd.ExcelFile(path)
    except PermissionError:
        raise DataError(f"'{name}' is open in another program (probably Excel). Close it and reload.")
    except Exception as e:
        raise DataError(f"Could not open '{name}'. It may be damaged or may not be a real Excel (.xlsx) file - "
                        f"try opening it in Excel and saving it again. (Technical detail: {type(e).__name__}: {e})")
    names = list(xls.sheet_names)
    sh = _sheet_named(names, 'Purchase Report')
    if sh is None:
        raise DataError(f"'{name}' has no sheet called 'Purchase Report'. Sheets found: {', '.join(names)}.")
    try:
        raw = xls.parse(sh)
    except Exception as e:
        raise DataError(f"The 'Purchase Report' sheet in '{name}' could not be read ({type(e).__name__}: {e}).")
    pr, pmeta = prepare_sales(raw)
    meta = {'warnings': list(pmeta['warnings']), 'rows': pmeta['rows'], 'dep_months': pmeta['dep_months'], 'sheets': names,
            'pr_fcst_months': pmeta['pr_fcst_months'], 'lists_found': [], 'extras_missing': []}
    out = {'pr': pr, 'wms': pd.DataFrame(), 'manual': pd.DataFrame(), 'lists': {}}

    # ---- WMS: stock on the warehouse floor, split by quality status (OK / HOLD)
    wsh = _sheet_named(names, 'WMS')
    if wsh:
        try:
            w = xls.parse(wsh)
            w.columns = [str(c).strip() for c in w.columns]
            need_columns(w, ['Item', 'On Hand', 'Status'], "The WMS sheet")
            w = w.loc[:, ~w.columns.duplicated()].copy()
            w['Item'] = clean_id(w['Item'])
            w = w[w['Item'].str.fullmatch(r'\d+', na=False)]
            w['On Hand'] = num(w['On Hand'])
            w['Status'] = w['Status'].fillna('').astype(str).str.strip().str.upper()
            piv = w.pivot_table(index='Item', columns='Status', values='On Hand', aggfunc='sum', fill_value=0.0)
            wm = pd.DataFrame(index=piv.index)
            wm['WMS OK Units'] = piv['OK'] if 'OK' in piv.columns else 0.0
            wm['WMS HOLD Units'] = piv['HOLD'] if 'HOLD' in piv.columns else 0.0
            other = [c for c in piv.columns if c not in ('OK', 'HOLD')]
            wm['WMS Other Units'] = piv[other].sum(axis=1) if other else 0.0
            out['wms'] = wm.reset_index().rename(columns={'Item': 'Item Number'})
            meta['wms_rows'] = len(w)
            meta['wms_other_status'] = other
        except DataError as e:
            meta['warnings'].append(f"{e} The Purchase Report's own MDC stock is used instead.")
        except Exception as e:
            meta['warnings'].append(f"The WMS sheet could not be read ({type(e).__name__}: {e}). The Purchase Report's own MDC stock is used instead.")
    else:
        meta['extras_missing'].append('WMS')

    # ---- manual: vendor name and lead time typed in by the buyers
    msh = _sheet_named(names, 'manual')
    if msh:
        try:
            m = xls.parse(msh)
            m.columns = [str(c).strip() for c in m.columns]
            need_columns(m, ['Item'], "The manual sheet")
            m['Item Number'] = clean_id(m['Item'])
            m = m[m['Item Number'].str.fullmatch(r'\d+', na=False)].drop_duplicates('Item Number')
            m['Manual Vendor'] = clean_txt(m['Vendor Update']) if 'Vendor Update' in m.columns else ''
            m['Manual Lead Time'] = pd.to_numeric(m['Leadtime'], errors='coerce') if 'Leadtime' in m.columns else np.nan
            out['manual'] = m[['Item Number', 'Manual Vendor', 'Manual Lead Time']].reset_index(drop=True)
        except Exception as e:
            meta['warnings'].append(f"The 'manual' sheet could not be read ({type(e).__name__}: {e}); its vendor names and lead times are not used.")
    else:
        meta['extras_missing'].append('manual')

    # ---- Bid Universe: every item quoted to every customer (used for Bid Product Availability and Bid Performance)
    out['bid_universe'], out['bid_universe_dirty'] = pd.DataFrame(), pd.DataFrame()
    bsh = _sheet_named(names, 'Bid Universe')
    if bsh:
        try:
            out['bid_universe'], out['bid_universe_dirty'], meta['bid_universe'] = load_bid_universe(xls, bsh)
        except DataError as e:
            meta['warnings'].append(f"{e} Bid Product Availability falls back to the Bids file.")
        except Exception as e:
            meta['warnings'].append(f"The 'Bid Universe' sheet could not be read ({type(e).__name__}: {e}). Bid Product Availability falls back to the Bids file.")
    else:
        meta['extras_missing'].append('Bid Universe')

    # ---- item lists (which items the weekly report / Sandals / RIU track)
    for sheet, label in M3_LIST_SHEETS:
        real = _sheet_named(names, sheet)
        if not real:
            continue
        try:
            ids = _item_ids_on_sheet(xls, real)
        except Exception as e:
            meta['warnings'].append(f"The '{sheet}' sheet could not be read ({type(e).__name__}: {e}).")
            continue
        if ids:
            out['lists'][label] = ids
            meta['lists_found'].append(f"{label} ({len(ids):,})")
    return out, meta


def load_vendor_terms(path):
    """VendorTerms.xlsx -> one row per supplier search key with the payment terms (code, days)."""
    raw, sheet = read_sheet(path, 'Sheet0', {'Search key', 'Ptm'})
    raw.columns = [str(c).strip() for c in raw.columns]
    v = raw.copy()
    v['Supplier Key'] = clean_txt(v['Search key']).str.upper()
    v = v[v['Supplier Key'] != ''].copy()
    v['Terms Code'] = clean_txt(v['Ptm']).str.upper()
    v['Supplier Name'] = clean_txt(v['Supplier name']) if 'Supplier name' in v.columns else ''
    v['Active'] = pd.to_numeric(v['Sts'], errors='coerce').eq(20) if 'Sts' in v.columns else True
    meta = {'warnings': [], 'sheet': sheet, 'rows': len(v)}

    def days_of(code):
        if code in TERMS_DAYS:
            return float(TERMS_DAYS[code]), True
        if code in TERMS_NOT_REAL:
            return float(DEFAULT_TERMS_DAYS), False
        m = re.fullmatch(r'N(\d{1,3})', code or '')
        if m:
            return float(m.group(1)), True
        return float(DEFAULT_TERMS_DAYS), False

    v['_days'] = v['Terms Code'].map(lambda c: days_of(c)[0])
    v['_known'] = v['Terms Code'].map(lambda c: days_of(c)[1])
    meta['no_code'] = int((v['Terms Code'] == '').sum())
    meta['unknown_codes'] = v.loc[~v['_known'] & (v['Terms Code'] != ''), 'Terms Code'].value_counts().to_dict()

    rows, conflicts = [], []
    for key, g in v.groupby('Supplier Key', sort=False):
        if key in VENDOR_TERMS_OVERRIDE:
            code = str(VENDOR_TERMS_OVERRIDE[key]).upper()
            d, k = days_of(code)
            rows.append((key, g['Supplier Name'].iloc[0], code, d, k, False, len(g)))
            continue
        gg = g[g['Active']] if g['Active'].any() else g
        gg = gg[gg['Terms Code'] != ''] if (gg['Terms Code'] != '').any() else gg
        codes = gg['Terms Code'].value_counts()
        top = codes[codes == codes.max()].index.tolist()
        pick = min(top, key=lambda c: days_of(c)[0])              # tie -> the shorter period (earlier cash out = the cautious answer)
        d, k = days_of(pick)
        conflict = gg['Terms Code'].nunique() > 1
        rows.append((key, gg['Supplier Name'].iloc[0], pick, d, k, conflict, len(g)))
        if conflict:
            conflicts.append(key)
    t = pd.DataFrame(rows, columns=['Supplier Key', 'Supplier Name', 'Terms Code', 'Terms Days', 'Terms Confirmed', 'Terms Conflict', 'VT Rows'])
    t['Terms Label'] = np.where(t['Terms Code'].isin(TERMS_DAYS), t['Terms Code'].map({'COD': 'Cash on delivery', 'CC': 'Credit card', 'CSD': 'Cash against shipping documents'}).fillna(t['Terms Code']),
                        np.where(t['Terms Confirmed'], 'Net ' + t['Terms Days'].astype(int).astype(str) + ' days',
                                 'Not set (' + t['Terms Code'].replace('', 'blank') + ') - ' + str(DEFAULT_TERMS_DAYS) + ' days assumed'))
    meta['keys'] = len(t)
    meta['conflict_keys'] = conflicts
    meta['duplicate_keys'] = int(v['Supplier Key'].duplicated().sum())
    return t, meta


FILE_SPEC = {
    'sales': ["Purchase Report Sales Analysis with Raw Depletions.csv", "Purchase_Report_Sales_Analysis_with_Raw_Depletions.csv"],
    'po': ["PO Dates.xlsx", "PO_Dates.xlsx"],
    'fc': ["CPJ FORECAST.xlsx", "CPJ_FORECAST.xlsx"],
    'bids': ["BIDS.xlsx"],
    'm3': ["M3 Purchasing Report - Bid.xlsx", "M3_Purchasing_Report-_Bid.xlsx", "M3 Purchasing Report.xlsx"],
    'vt': ["VendorTerms.xlsx", "Vendor Terms.xlsx"],
}
FILE_LABEL = {'sales': 'Purchase Report (sales & stock)', 'po': 'PO Dates (purchase orders & shipments)',
              'fc': 'CPJ Forecast', 'bids': 'Bids', 'm3': 'M3 Bid Purchasing workbook', 'vt': 'Vendor Terms'}


def source_signature():
    """Changes whenever a source file is replaced -> the dashboard reloads by itself."""
    sig = []
    for k, cands in FILE_SPEC.items():
        p = find_file(cands)
        try:
            sig.append((k, p or '', os.path.getmtime(p) if p else 0.0, os.path.getsize(p) if p else 0))
        except OSError:
            sig.append((k, p or '', 0.0, 0))
    return tuple(sig)


@st.cache_data(show_spinner="Loading data (the large Excel files can take about a minute)...")
def load_all(sig):
    paths = {k: p for k, p, _, _ in sig}
    out = {'sales': pd.DataFrame(), 'po': pd.DataFrame(), 'fc': pd.DataFrame(), 'bids': pd.DataFrame(),
           'm3': {}, 'vt': pd.DataFrame(), 'fc_meta': {}, 'meta': {}, 'errors': {}, 'sample_codes': set(), 'sample_lines': pd.DataFrame()}

    def attempt(key, fn):
        if not paths.get(key):
            out['errors'][key] = f"File not found. Expected something like: {FILE_SPEC[key][0]}"
            return None
        try:
            return fn(paths[key])
        except DataError as e:
            out['errors'][key] = str(e)
        except Exception as e:
            out['errors'][key] = f"Unexpected problem while reading this file: {type(e).__name__}: {e}"
        return None

    r = attempt('sales', load_sales)
    if r:
        out['sales'], out['meta']['sales'] = r
    units = out['sales'] if not out['sales'].empty else None
    r = attempt('po', lambda p: load_po(p, units))
    if r:
        out['po'], out['meta']['po'] = r
        # sample items: by code (a sample code in the Purchase Report) or by the word SAMPLE in the PO line's own description
        sc = set(out['meta'].get('sales', {}).get('sample_codes', set()))
        is_s = out['po']['Item number'].isin(sc) | out['po']['ItemDescription'].astype(str).str.contains('SAMPLE', case=False, na=False)
        out['meta']['po']['sample_lines'] = int(is_s.sum())
        out['meta']['po']['sample_items'] = int(out['po'].loc[is_s, 'Item number'].nunique())
        out['meta']['po']['sample_usd'] = float(out['po'].loc[is_s, 'USD $'].sum())
        out['sample_lines'] = out['po'][is_s][['PO no', 'Ln', 'Hst', 'Supplier', 'Item number', 'ItemDescription', 'USD $']].copy()
        out['po'] = out['po'][~is_s].copy()
        out['sample_codes'] = sc | set(out['sample_lines']['Item number'])
    r = attempt('fc', load_forecast)
    if r:
        out['fc'], out['meta']['fc'] = r
        out['fc_meta'] = r[1]
    r = attempt('bids', load_bids)
    if r:
        out['bids'], out['meta']['bids'] = r
    r = attempt('m3', load_m3)                                  # optional: only the Bid Purchasing tab needs it
    if r:
        out['m3'], out['meta']['m3'] = r
    r = attempt('vt', load_vendor_terms)                        # optional: payment terms for cash flow and the supplier views
    if r:
        out['vt'], out['meta']['vt'] = r
    out['files'] = [(k, os.path.basename(p) if p else '(not found)', m) for k, p, m, _ in sig]
    return out


# ---- load, show problems, stop if the two core files are unusable
try:
    DATA = load_all(source_signature())
except Exception as _e:
    st.error(f"The data could not be loaded: {type(_e).__name__}: {_e}")
    st.stop()

sales_df, po_raw, fc_df, bids_df, fc_meta = DATA['sales'], DATA['po'], DATA['fc'], DATA['bids'], DATA['fc_meta']
m3_data, vt_df = DATA['m3'], DATA['vt']

with st.sidebar.expander("Data files"):
    for _k, _name, _mt in DATA['files']:
        _when = pd.Timestamp(_mt, unit='s').strftime('%Y-%m-%d %H:%M') if _mt else '-'
        st.caption(f"**{FILE_LABEL[_k]}**  \n{_name}  \nUpdated: {_when}")
    if 'sales' in DATA['meta']:
        st.caption("Sales averages use " + ", ".join(DATA['meta']['sales']['dep_months']) + ".")
    if 'm3' in DATA['meta']:
        st.caption("Bid Purchasing tab: Purchase Report sheet of the M3 workbook" + (" + WMS stock" if not m3_data['wms'].empty else "") + ".")
    if 'po' in DATA['meta']:
        _fx = {k: v for k, v in (DATA['meta']['po'].get('fx_used') or {}).items() if k != 'USD' and v}
        if _fx:
            st.caption("PO dollars: " + ", ".join(f"1 {k} = ${v:,.4g}" for k, v in sorted(_fx.items())) + " (" + DATA['meta']['po'].get('fx_source', '') + ").")
    if st.button("Reload data files"):
        st.cache_data.clear()
        st.rerun()

for _k, _msg in DATA['errors'].items():
    if _k in ('m3', 'vt') and _msg.startswith('File not found'):
        continue                                                # optional files: the Bid Purchasing tab / terms views explain what is missing
    (st.error if _k in ('sales', 'po') else st.warning)(f"{FILE_LABEL[_k]}: {_msg}")
for _k, _m in DATA['meta'].items():
    for _w in _m.get('warnings', []):
        st.warning(f"{FILE_LABEL.get(_k, _k)}: {_w}")
if sales_df.empty or po_raw.empty:
    st.error("The two core files (Purchase Report and PO Dates) must load before the dashboard can be shown. "
             "Check that both files are in the dashboard folder, closed in Excel, then use 'Reload data files' in the sidebar.")
    st.stop()

# ----------------------------------------
# SIDEBAR / GLOBAL FILTERS
# ----------------------------------------
st.sidebar.header("Global Filters")
include_p01 = st.sidebar.checkbox("Include Otp = P01 orders", value=False,
                                  help="P01 is excluded from all spend/PO analytics by default.")
po_all = po_raw if include_p01 else po_raw[po_raw['Otp'] != 'P01']
po_df = po_all[po_all['Status Code'].between(*PO_LIVE)]                    # non-deleted PO lines
if po_df.empty:
    st.warning("The PO Dates file loaded, but it has no live purchase-order lines (Hst 20-85) to analyse.")
    st.stop()

valid_years = sorted([int(y) for y in po_df['Order Year'].unique() if y >= 2020])
slicer_year = st.sidebar.multiselect("Order Year", valid_years, default=[today.year] if today.year in valid_years else valid_years[-1:])
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
po_active = po_filtered[po_filtered['Status Code'].between(*OPEN_PO)]                 # open POs = Hst 20-49
po_active_all = po_df[po_df['Status Code'].between(*OPEN_PO)]                          # full open pipeline, not limited by year/month

last_buyer = (po_df.sort_values('Ord dt').groupby('Item number')['Buyer'].last())

# ----------------------------------------
# INVENTORY MASTER (sales + forecast + pipeline)
# ----------------------------------------
_landed = po_active_all['Status'].isin(LANDED_STAGES)
pipe_port = po_active_all[_landed].groupby('Item number')['Order Qty (Cases)'].sum()          # arrived: port / yard / stripped
pipe_ord = po_active_all[~_landed].groupby('Item number')['Order Qty (Cases)'].sum()          # still on order or on the water


_cost_lines = po_df[po_df['Convertible'] & (po_df['Order Qty (Cases)'] > 0) & (po_df['USD $'] > 0)].sort_values('Ord dt')
last_case_cost = (_cost_lines['USD $'] / _cost_lines['Order Qty (Cases)']).groupby(_cost_lines['Item number']).last()       # latest PO price per case
retail_share = po_df['Whs'].isin(RETAIL_WHS).groupby(po_df['Item number']).mean()                                            # share of an item's PO lines going to retail sites


def build_inv(base):
    """Purchase Report table (already cleaned by prepare_sales) + pipeline from the PO file + forecast -> the inventory master."""
    d = base.copy()
    d['Cases At Port'] = d['Item Number'].map(pipe_port).fillna(0.0)
    d['Cases On Order'] = d['Item Number'].map(pipe_ord).fillna(0.0)
    d['Cases Inbound'] = d['Cases At Port'] + d['Cases On Order']
    if not fc_df.empty:
        d['3M_Avg_Forecast_Cases'] = d['Item Number'].map(fc_df.groupby('Item Code')['3M_Avg_Forecast_Cases'].sum()).fillna(0.0)
    else:
        d['3M_Avg_Forecast_Cases'] = 0.0
    d['Lead Time (Months)'] = d['Lead time'] / 30
    # Safety stock = lead time x monthly demand. Demand = the confirmed forecast; only when an item has no forecast do we fall back to recent sales.
    d['Has Forecast'] = d['3M_Avg_Forecast_Cases'] > 0
    d['SS Basis'] = np.where(d['Has Forecast'], 'Forecast', 'Recent sales')
    d['Monthly Demand Used (Cases)'] = np.where(d['Has Forecast'], d['3M_Avg_Forecast_Cases'], d['3M_Avg_Depletion_Demand'])
    d['Safety Stock (Cases)'] = d['Lead Time (Months)'] * d['Monthly Demand Used (Cases)']
    d['Responsible Buyer'] = d['Item Class'].map(BUYER_MAP).fillna(d['Item Number'].map(last_buyer)).fillna('Unassigned')
    d['Is Live'] = d['Item Status'].isin(LIVE_STATUSES)
    d['Retail PO Share'] = d['Item Number'].map(retail_share).fillna(0.0)
    d['Last PO Cost per Case (USD)'] = d['Item Number'].map(last_case_cost)
    return d


inv = build_inv(sales_df)
inv_f = inv[inv['Custom Category'].isin(slicer_category)] if slicer_category else inv
inv_live = inv_f[inv_f['Is Live']]

# ----------------------------------------
# VENDOR TERMS + EXPECTED ARRIVAL (used by Cash Flow, Supplier Scorecard and Bid Purchasing)
# ----------------------------------------
HAS_TERMS = not vt_df.empty
_terms_map = vt_df.set_index('Supplier Key') if HAS_TERMS else None


def add_terms(df, supplier_col='Supplier'):
    """Adds Terms Code / Terms Days / Terms Confirmed / Terms Label to any table that has a supplier search key.
    A supplier that is not in the Vendor Terms file (or a missing file) gets DEFAULT_TERMS_DAYS and is marked 'not confirmed'."""
    out = df.copy()
    if HAS_TERMS and supplier_col in out.columns:
        key = out[supplier_col].astype(str).str.strip().str.upper()
        out['Terms Code'] = key.map(_terms_map['Terms Code'])
        out['Terms Days'] = key.map(_terms_map['Terms Days'])
        out['Terms Confirmed'] = key.map(_terms_map['Terms Confirmed']).fillna(False).astype(bool)
        out['Terms Label'] = key.map(_terms_map['Terms Label'])
    else:
        out['Terms Code'], out['Terms Days'], out['Terms Confirmed'], out['Terms Label'] = np.nan, np.nan, False, np.nan
    miss = out['Terms Days'].isna()
    out['Terms Label'] = out['Terms Label'].where(~miss, 'No terms on file - ' + str(DEFAULT_TERMS_DAYS) + ' days assumed')
    out['Terms Code'] = out['Terms Code'].fillna('?')
    out['Terms Days'] = out['Terms Days'].fillna(float(DEFAULT_TERMS_DAYS))
    return out


def expected_receipt(df):
    """When an open order line is expected to be in the warehouse: stripped -> yard -> arrival -> ETA -> requested date (first one known)."""
    d = pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')
    for c in ('Stripped Date', 'Yard Date', 'Arrival', 'ETA', 'Req dt'):
        if c in df.columns:
            d = d.combine_first(pd.to_datetime(df[c], errors='coerce'))
    return d


def payment_anchor(df):
    """The date the payment clock starts. Normally the expected receipt; 'cash against shipping documents' starts when it ships."""
    d = expected_receipt(df)
    if 'Terms Code' in df.columns:
        ship = pd.to_datetime(df['Departed Date'], errors='coerce').combine_first(pd.to_datetime(df['ETD Date'], errors='coerce'))
        d = d.where(~(df['Terms Code'].eq('CSD') & ship.notna()), ship)
    return d


def add_sub_key(df, level):
    keys = ['Item Category', 'Fmt'] + (['Flv'] if level.startswith('Category + Format + Flavor') else [])
    key = df[keys].astype(str).agg(' | '.join, axis=1)
    valid = (df['Item Category'] != 'UNCATEGORIZED') & (df['Fmt'] != '')
    return key.where(valid, '')


def cover_table(df, demand_col='3M_Avg_Depletion_Demand', stock_col='WH_Cases'):
    """MONTHS OF COVER by category - the one definition used on every tab.
      * active items only (Item Status 20) that actually sell (demand above zero)
      * stock = warehouse cases (MDC + SEP + MJM + Bonded); Stores, Plants and OM stock are NOT counted
      * every quantity is in cases (packs by Conversion Factor, KG items by the weight in the description)
      * one overstocked item can never count for more than COVER_CAP_MONTHS months, so a few huge stocks
        cannot hide shortages on the other items in the category
    """
    cols = ['Category', 'Items Selling', 'Stock (Cs)', 'Monthly Demand (Cs)', 'Months of Cover', 'Months (no cap)',
            'Under 1 Month %', '1-3 Months %', 'Over 3 Months %', 'Overstocked Items']
    d = df[(df['Item Status'] == 20) & df['Custom Category'].isin(COVER_CATS)].copy()
    d = d[d[demand_col] > 0]
    if d.empty:
        return pd.DataFrame(columns=cols)
    d['_cover'] = d[stock_col] / d[demand_col]
    d['_counted'] = np.minimum(d[stock_col], COVER_CAP_MONTHS * d[demand_col])
    d['_u1'] = d['_cover'] < 1
    d['_m3'] = (d['_cover'] >= 1) & (d['_cover'] < 3)
    d['_o3'] = d['_cover'] >= 3
    d['_over'] = d['_cover'] > COVER_CAP_MONTHS
    g = d.groupby('Custom Category').agg(n=('_cover', 'size'), stock=(stock_col, 'sum'), counted=('_counted', 'sum'), dem=(demand_col, 'sum'),
                                         u1=('_u1', 'sum'), m3=('_m3', 'sum'), o3=('_o3', 'sum'), over=('_over', 'sum')).reset_index()
    out = pd.DataFrame({
        'Category': g['Custom Category'], 'Items Selling': g['n'], 'Stock (Cs)': g['stock'], 'Monthly Demand (Cs)': g['dem'],
        'Months of Cover': g['counted'] / g['dem'], 'Months (no cap)': g['stock'] / g['dem'],
        'Under 1 Month %': g['u1'] / g['n'] * 100, '1-3 Months %': g['m3'] / g['n'] * 100, 'Over 3 Months %': g['o3'] / g['n'] * 100,
        'Overstocked Items': g['over']})
    return out[cols]


def cover_charts(ct, key):
    """Two plain pictures: months of cover, and how many items are short / ok / well stocked."""
    if ct is None or ct.empty:
        st.info("No active selling items to measure cover for.")
        return
    a, b = st.columns(2)
    with a:
        d = ct.sort_values('Months of Cover')
        fig = px.bar(d, x='Months of Cover', y='Category', orientation='h', text=d['Months of Cover'].map(lambda v: f"{v:,.1f}"),
                     color='Months of Cover', color_continuous_scale='RdYlGn', range_color=[0, 3], title="Months of cover by category")
        fig.update_layout(coloraxis_showscale=False, height=420)
        show_chart(fig, key=f"{key}_cover")
    with b:
        prof = ct.melt(id_vars='Category', value_vars=['Under 1 Month %', '1-3 Months %', 'Over 3 Months %'], var_name='Band', value_name='Share')
        prof['Band'] = prof['Band'].str.replace(' %', '', regex=False)
        order = ct.sort_values('Under 1 Month %')['Category'].tolist()
        fig = px.bar(prof, x='Share', y='Category', color='Band', orientation='h', text=prof['Share'].map(lambda v: f"{v:,.0f}%"),
                     color_discrete_map={'Under 1 Month': '#d62728', '1-3 Months': '#f2c94c', 'Over 3 Months': '#2ca02c'},
                     category_orders={'Category': order, 'Band': ['Under 1 Month', '1-3 Months', 'Over 3 Months']},
                     title="Share of selling items by months of cover")
        fig.update_layout(height=420, xaxis_title="% of selling items")
        show_chart(fig, key=f"{key}_profile")


_all_tabs = st.tabs(["📊 Executive Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", "🏆 Bid Performance", "📦 Bid Availability", "🎯 Bid Purchasing",
                     "📈 Forecast", "🤝 Supplier Scorecard", "💵 Cash Flow", "📉 Price Index (PPV)", "✅ Data Checks"])
# tabs[0..8] are the original nine; tabs[9] is Bid Purchasing and tabs[10] is Bid Availability (both are shown right after Bid Performance)
tabs = list(_all_tabs[:4]) + list(_all_tabs[6:]) + [_all_tabs[5], _all_tabs[4]]

# ----------------------------------------
# SUBSTITUTE FINDER (used by Inventory Health and Bid Performance)
# ----------------------------------------
SUB_FEATS = ['Item Class', 'Flv', 'Brand', 'Unit Size', 'Tokens']
SUB_BASE_LEVEL = 'Category + Format (broader)'          # pairs are found on Category + Format; the match score then grades them


def sub_pool():
    """Items that can act as substitutes: saleable item groups only (no plant raw materials, operations or beverage-system items)."""
    p = inv[inv['Item Class'].isin(SALEABLE_GROUPS) & ~inv['Item Class'].isin(PLANT_GROUPS)].copy()
    p['Sub Key'] = add_sub_key(p, SUB_BASE_LEVEL)
    return p


def find_substitutes(targets, candidates, min_score, min_months=None, max_pairs=400_000):
    """Pair every target item with every candidate that shares its Category + Format, then grade each pair 0-100.

    targets / candidates need: Item Number, Item Description, Sub Key, Item Class, Flv, Brand, Unit Size, Tokens.
    Returns one row per pair (target columns plain, candidate columns ending in ' (Sub)') with Match Score,
    Match Confidence, Why Matched and 'Sub Months' (candidate MDC stock / the larger of the two demands),
    keeping only pairs scoring at least `min_score` (and, when given, holding more than `min_months` of stock).
    """
    base_cols = ['Item Number', 'Item Description', 'Sub Key'] + SUB_FEATS
    t = targets[base_cols + ['3M_Avg_Depletion_Demand']].drop_duplicates('Item Number').rename(columns={'3M_Avg_Depletion_Demand': 'Target Demand'})
    c = candidates[base_cols + [x for x in ('Avail_Cases', '3M_Avg_Depletion_Demand', 'Inventory Value') if x in candidates.columns]].drop_duplicates('Item Number')
    t = t[t['Sub Key'] != '']
    c = c[c['Sub Key'] != '']
    c = c.rename(columns={k: f'{k} (Sub)' for k in c.columns if k != 'Sub Key'})
    # guard against a huge category: count the pairs before building them
    sizes = t.groupby('Sub Key').size().mul(c.groupby('Sub Key').size(), fill_value=0)
    if sizes.sum() > max_pairs:
        keep = sizes.sort_values().cumsum()
        ok_keys = set(keep[keep <= max_pairs].index)
        t = t[t['Sub Key'].isin(ok_keys)]
    pairs = t.merge(c, on='Sub Key', how='inner')
    pairs = pairs[pairs['Item Number'] != pairs['Item Number (Sub)']]
    empty = pd.DataFrame(columns=list(pairs.columns) + ['Match Score', 'Match Confidence', 'Why Matched', 'Sub Months'])
    if pairs.empty:
        return empty
    pairs = pairs.reset_index(drop=True)
    a = pairs[SUB_FEATS]
    b = pairs[[f'{k} (Sub)' for k in SUB_FEATS]].copy()
    b.columns = SUB_FEATS
    sc = sub_match_scores(a, b)
    pairs = pairs.join(sc)
    ref = pairs[['Target Demand', '3M_Avg_Depletion_Demand (Sub)']].max(axis=1)
    pairs['Sub Months'] = np.where(ref > 0, pairs['Avail_Cases (Sub)'] / ref.where(ref > 0, 1.0), np.inf)
    pairs = pairs[pairs['Match Score'] >= min_score]
    if min_months is not None:
        pairs = pairs[pairs['Sub Months'] > min_months]
    return pairs.copy()


def best_substitute(pairs, sort_stock=True):
    """For every target keep the strongest candidate: highest match score first, then the most stock."""
    if pairs.empty:
        return pairs
    p = pairs.sort_values(['Match Score', 'Avail_Cases (Sub)'] if 'Avail_Cases (Sub)' in pairs.columns else ['Match Score'], ascending=False)
    best = p.groupby('Item Number', sort=False).head(1).copy()
    best['# Subs'] = best['Item Number'].map(p.groupby('Item Number').size())
    return best

# ----------------------------------------
# BID AVAILABILITY ENGINE  (shared by the Bid Product Availability tab, Bid Performance and the Data Checks)
# ----------------------------------------
# Rules:
#   * WHO and WHICH items: the Bids file (BIDS.xlsx) only. Every customer on that file, and every CPJ Code listed for that customer in any bid period, is a bid item.
#     The headline total is the number of DIFFERENT CPJ Codes on the file; it is broken out by customer, and each item is counted ONCE per customer,
#     so a customer's total is the number of different CPJ Codes the file lists for it - the same number on every tab.
#   * Item status comes from the item master (the Purchase Report), never from the status typed on the bid sheet.
#   * Status 50 or 80 (discontinued): if a direct / strong substitute exists (match rated High) the item stays in the calculation and is rated on that substitute
#     (stocked = green, on order = yellow, otherwise red). With NO strong substitute it is LEFT OUT of the calculation (it is still listed, with its stock, so nothing is hidden).
#   * Status 20 items: MDC stocked = more than BID_MDC_MIN_CASES cases at MDC. SEP and SEZ (bonded) stock count as MDC. OM never counts.
#     A close substitute that is MDC stocked counts the item as stocked. On order = the item (or a close substitute) is on a PO with status 20-40. No order = neither.
#   * A code that cannot be rated (status 0-14 or 86-99, not in the item master, a sample item) is also left out of the calculation.
#     Left-out codes stay in the customer's total (grey bar) and are listed so the Bids file can be corrected.
BID_MDC_MIN_CASES = 3.0
BID_ORDER_STATUS = (20, 40)
B_STOCK, B_ORDER, B_NONE, B_DISC, B_OUT = 'MDC stocked items', 'OOS items on order', 'OOS with no order', 'Discontinued no Replacement', 'Code not usable'
BID_BUCKETS = [B_STOCK, B_ORDER, B_NONE]                  # the three bars of the weekly slide
BID_LEFT_OUT = [B_DISC, B_OUT]                            # the two groups that are left out of the calculation
BID_ALL_BUCKETS = BID_BUCKETS + BID_LEFT_OUT              # + the items left out, so every customer's bar adds up to its total on the Bids file
BID_BUCKET_COLORS = {B_STOCK: '#2ca02c', B_ORDER: '#f2c94c', B_NONE: '#d62728', B_DISC: '#8c8c8c', B_OUT: '#d0d0d0'}
BID_SRC_ALL = "Every item on the Bids file (all bid periods)"
BID_SRC_CUR = "Only bids marked Current on the Bids file"
BID_SRC_WON = "Only items we won (Quotes Won above 0)"

st.sidebar.header("Bid analysis settings")
bid_src_mode = st.sidebar.radio("Bid items to analyse", [BID_SRC_ALL, BID_SRC_CUR, BID_SRC_WON], index=0, key="bid_src_mode",
                                help="Used by the Bid Performance and Bid Product Availability tabs. The Bids file (BIDS.xlsx) says which customers bid and which items each one was set up with. "
                                     "The first choice uses every item on the file; each customer's items are counted once even when they appear in two bid periods.")
bid_incl_expired = bid_src_mode != BID_SRC_CUR            # kept for older code: True unless only current bids are wanted
bid_stock_min = st.sidebar.slider("MDC stocked = more than this many cases", 0.0, 20.0, float(BID_MDC_MIN_CASES), 0.5, key="bid_stock_min",
                                  help="An item with this many cases or fewer at MDC (+ SEP + SEZ) counts as out of stock. KG items are worked out in cases from the weight in the description.")
bid_sub_pick = st.sidebar.radio("Substitutes counted", list(SUB_LEVELS.keys()), index=0, key="bid_sub_pick",
                                help="High only = almost exact replacements. Medium or better also accepts a likely replacement.")
bid_min_score = SUB_LEVELS[bid_sub_pick]


def bid_stock_table():
    """Inventory master + the two numbers a bid cares about: stock at MDC/SEP/SEZ (cases) and PO cases with status 20-40."""
    b = inv.copy()
    b['Bid Stock (Cs)'] = b[[f'{x}_Cs' for x in BID_AVAIL_SITES]].clip(lower=0).sum(axis=1)
    lines = po_df[po_df['Status Code'].between(*BID_ORDER_STATUS)].copy()
    lines['_exp'] = expected_receipt(lines)
    g = lines.groupby('Item number')
    b['Bid On Order (Cs)'] = b['Item Number'].map(g['Order Qty (Cases)'].sum()).fillna(0.0)
    b['Open PO Lines'] = b['Item Number'].map(g.size()).fillna(0).astype(int)
    b['Next Arrival'] = b['Item Number'].map(g['_exp'].min())
    return b


def bid_items_table(mode, include_expired=False):
    """One row per (customer, item code), taken from the Bids file only. Returns (table, note).
    An item that appears in two bid periods of the same customer (or twice in one bid) is ONE row, so a customer's total equals its number of different CPJ Codes."""
    cols = ['Customer', 'Item Code', 'Bid Name', 'Item Description', 'Quotes Won', 'Bid Lines']
    if bids_df.empty:
        return pd.DataFrame(columns=cols), "No bid data is loaded. The Bids file (BIDS.xlsx) says which customers bid and which items each one was set up with."
    t = bids_df
    if mode == BID_SRC_CUR:
        t = t[t['Bid Status'].astype(str).str.strip().str.lower() == 'current']
    elif mode == BID_SRC_WON:
        t = t[t['Quotes Won'].fillna(0) > 0]
    if t.empty:
        return pd.DataFrame(columns=cols), "The Bids file has no rows for this choice. Pick another item source in the sidebar under 'Bid analysis settings'."
    t = t.groupby(['Customer', 'Item Code'], as_index=False).agg(**{'Bid Name': ('Bid Name', join_unique), 'Item Description': ('Item Description', 'first'),
                                                                   'Quotes Won': ('Quotes Won', 'max'), 'Bid Lines': ('Item Code', 'size')})
    what = {BID_SRC_ALL: "every bid period on the file", BID_SRC_CUR: "only bids marked Current", BID_SRC_WON: "only items with Quotes Won above 0"}[mode]
    note = (f"Bid customers and items come from the Bids file (BIDS.xlsx): {what}. {t['Customer'].nunique():,} customers, {len(t):,} customer / item pairs "
            "(each item counted once per customer, even when it is listed in more than one bid period).")
    return t[cols], note


def _best_pairs(pairs, sort_cols=('Match Score', 'Sub Stock (Cs)')):
    p = pairs.sort_values(list(sort_cols), ascending=False)
    return p.groupby('Item Number', sort=False).head(1)


def bid_classify(codes, min_score, stock_min):
    """One row per unique item code: MDC stocked / OOS on order / OOS with no order / Not counted, and why (own stock, substitute ...)."""
    tab = bid_stock_table()
    tab_i = tab.drop_duplicates('Item Number')
    keep = ['Item Number', 'Item Description', 'Item Status', 'Custom Category', 'Item Class', 'Responsible Buyer', 'MDC_Cs', 'SEP_Cs', 'Bonded_Cs', 'OM_Cs',
            'Bid Stock (Cs)', 'Bid On Order (Cs)', 'Open PO Lines', 'Next Arrival', '3M_Avg_Depletion_Demand', 'Cases At Port']
    u = pd.DataFrame({'Item Code': sorted(set(codes))}).merge(tab_i[keep], left_on='Item Code', right_on='Item Number', how='left')
    u['In Purchase Report'] = u['Item Number'].notna()
    status = pd.to_numeric(u['Item Status'], errors='coerce')
    u['Item Status'] = status
    active = status.eq(20)
    ignored = status.isin(DELETED)
    disc = status.isin(DISCONTINUED)                        # 50 / 80 in the item master: rated only when a strong substitute exists
    stock = u['Bid Stock (Cs)'].fillna(0.0)
    ordc = u['Bid On Order (Cs)'].fillna(0.0)
    own_stock = stock > stock_min
    own_order = ordc > 0

    # ---- substitutes: close look-alikes that are active (status 20), found the same way as everywhere else in the dashboard
    pool = sub_pool()
    pool['Avail_Cases'] = pool['Item Number'].map(tab_i.set_index('Item Number')['Bid Stock (Cs)']).fillna(0.0)
    ord_map = tab_i.set_index('Item Number')['Bid On Order (Cs)']
    arr_map = tab_i.set_index('Item Number')['Next Arrival']
    cand = pool[(pool['Item Status'] == 20) & (pool['Sub Key'] != '')]
    tgt_mask = u['In Purchase Report'] & ~ignored & (~own_stock | ~active)
    tgt = pool[pool['Item Number'].isin(u.loc[tgt_mask, 'Item Code']) & (pool['Sub Key'] != '')]
    pairs = find_substitutes(tgt, cand, min_score) if (len(tgt) and len(cand)) else pd.DataFrame()
    # a discontinued item (50 / 80) only keeps a replacement that is direct / strong (High match), whatever the sidebar says for the other items
    if not pairs.empty:
        _dset = set(u.loc[disc, 'Item Code'])
        pairs = pairs[~(pairs['Item Number'].isin(_dset) & (pairs['Match Confidence'] != 'High'))]
    sub_cols = ['Sub Item', 'Sub Description', 'Sub Stock (Cs)', 'Sub On Order (Cs)', 'Sub Next Arrival', 'Sub Score', 'Sub Confidence']
    for c in sub_cols:
        u[c] = np.nan
    u['Sub Role'] = ''
    if not pairs.empty:
        pairs = pairs.copy()
        pairs['Sub Stock (Cs)'] = pairs['Avail_Cases (Sub)']
        pairs['Sub On Order (Cs)'] = pairs['Item Number (Sub)'].map(ord_map).fillna(0.0)
        pairs['Sub Next Arrival'] = pairs['Item Number (Sub)'].map(arr_map)
        by_stock = _best_pairs(pairs[pairs['Sub Stock (Cs)'] > stock_min])
        by_order = _best_pairs(pairs[pairs['Sub On Order (Cs)'] > 0], ('Match Score', 'Sub On Order (Cs)'))
        by_any = _best_pairs(pairs)

        def _fill(best, role, only_empty):
            b = best.set_index('Item Number')
            for dst, src in (('Sub Item', 'Item Number (Sub)'), ('Sub Description', 'Item Description (Sub)'), ('Sub Stock (Cs)', 'Sub Stock (Cs)'),
                             ('Sub On Order (Cs)', 'Sub On Order (Cs)'), ('Sub Next Arrival', 'Sub Next Arrival'), ('Sub Score', 'Match Score'),
                             ('Sub Confidence', 'Match Confidence')):
                v = u['Item Code'].map(b[src])
                u[dst] = u[dst].where(u[dst].notna(), v) if only_empty else v
            hit = u['Item Code'].isin(b.index)
            u.loc[hit & (u['Sub Role'] == ''), 'Sub Role'] = role

        _fill(by_stock, 'Substitute stocked', False)
        has_sub_stock = u['Item Code'].isin(by_stock['Item Number'])
        # only fill the order/any substitute where no stocked one was found, keeping the stocked substitute's details
        keep_mask = has_sub_stock
        saved = u.loc[keep_mask, sub_cols + ['Sub Role']].copy()
        _fill(by_order, 'Substitute on order', True)
        _fill(by_any, 'Replacement exists', True)
        u.loc[keep_mask, sub_cols + ['Sub Role']] = saved
        has_sub_order = u['Item Code'].isin(by_order['Item Number'])
        has_repl = u['Item Code'].isin(by_any['Item Number'])
        # independent stock-substitute columns (used by the at-risk table): the best substitute that IS stocked, whatever the item's own bucket
        sb = by_stock.set_index('Item Number')
        u['Stocked Sub Item'] = u['Item Code'].map(sb['Item Number (Sub)'])
        u['Stocked Sub Description'] = u['Item Code'].map(sb['Item Description (Sub)'])
        u['Stocked Sub (Cs)'] = u['Item Code'].map(sb['Sub Stock (Cs)'])
        u['Stocked Sub Confidence'] = u['Item Code'].map(sb['Match Confidence'])
    else:
        has_sub_stock = pd.Series(False, index=u.index)
        has_sub_order = pd.Series(False, index=u.index)
        has_repl = pd.Series(False, index=u.index)
        for c in ('Stocked Sub Item', 'Stocked Sub Description', 'Stocked Sub (Cs)', 'Stocked Sub Confidence'):
            u[c] = np.nan
    u['Has Replacement'] = has_repl.to_numpy()

    is_sample = u['Item Code'].isin(DATA.get('sample_codes', set()))
    # a discontinued item (50 / 80) is rated only when a direct / strong substitute exists; without one it is left out of the calculation
    d_strong = (disc & has_repl).to_numpy()
    considered = u['In Purchase Report'] & ~ignored & ~is_sample & (active | has_repl)
    conds = [own_stock, has_sub_stock, own_order, has_sub_order]
    u['Bucket'] = np.select(conds, [B_STOCK, B_STOCK, B_ORDER, B_ORDER], default=B_NONE)
    u['Basis'] = np.select(conds, ['Own stock', 'Substitute stock', 'Own order', 'Substitute order'], default='Nothing in stock or on order')
    u['How'] = u['Basis']
    # a discontinued code that has a strong substitute says so in the detail
    d_how = np.select(conds, ['Discontinued - own stock', 'Discontinued - strong substitute in stock', 'Discontinued - own order', 'Discontinued - strong substitute on order'],
                      default='Discontinued - strong substitute exists, none in stock or on order')
    u.loc[d_strong, 'How'] = d_how[d_strong]
    st_txt = status.fillna(-1).astype(int).astype(str)
    reason = np.select([is_sample, ~u['In Purchase Report'], ignored & status.between(0, 14), ignored, disc & ~has_repl, ~active & ~has_repl],
                       ['Sample item - left out of every analysis', 'Item code not found in the Purchase Report',
                        'Status ' + st_txt + ' - not set up yet (0-14, never calculated)', 'Status ' + st_txt + ' - deleted code (86-99, never calculated)',
                        'Status ' + st_txt + ' - discontinued, no direct / strong substitute: left out of the calculation',
                        'Status ' + st_txt + ' - not active and no replacement'], default='')
    u.loc[~considered, 'Bucket'] = B_OUT
    u.loc[(disc & ~considered).to_numpy(), 'Bucket'] = B_DISC             # discontinued (50 / 80) with no direct / strong replacement
    u.loc[~considered, 'How'] = reason[~considered.to_numpy()]
    u['Basis'] = np.where(considered.to_numpy(), u['Basis'], 'Left out')
    u['Discontinued'] = disc.to_numpy()                                   # every bid code the item master shows as 50 / 80
    u['Disc Rated'] = (disc & considered).to_numpy()                      # ... of which a strong substitute exists, so it is rated
    u['Disc Left Out'] = (disc & ~considered).to_numpy()                  # ... of which there is none, so it is left out
    u['Is Active'] = active
    return u


def bid_customer_view(mode, include_expired, min_score, stock_min):
    """Bid items per customer joined to their classification. Returns (customer_items, unique_items, note)."""
    items, note = bid_items_table(mode)
    if items.empty:
        return items, pd.DataFrame(), note
    cls = bid_classify(items['Item Code'], min_score, stock_min)
    ci = items.merge(cls.drop(columns=['Item Description']), on='Item Code', how='left')
    desc_pr = inv.drop_duplicates('Item Number').set_index('Item Number')['Item Description']
    ci['Item Description'] = ci['Item Code'].map(desc_pr).fillna(ci['Item Description']).fillna('')
    return ci, cls, note


try:
    BID_CI, BID_CLS, BID_NOTE = bid_customer_view(bid_src_mode, bid_incl_expired, bid_min_score, bid_stock_min)
except Exception as _e:                                   # a problem here must never blank the other tabs
    BID_CI, BID_CLS, BID_NOTE = pd.DataFrame(), pd.DataFrame(), f"The bid availability could not be worked out ({type(_e).__name__}: {_e})."
    BID_ENGINE_ERROR = f"{type(_e).__name__}: {_e}"
else:
    BID_ENGINE_ERROR = ''

# ==========================================================================
# TAB 1 — EXECUTIVE SPEND
# ==========================================================================
with tabs[0], guard():
    st.header("Executive Summary: Category & Buyer Spend")
    cap("Otp = P01 orders are excluded." if not include_p01 else "Otp = P01 orders are INCLUDED (sidebar toggle).")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total PO Spend", f"${po_filtered['USD $'].sum():,.0f}")
    c2.metric("Total Open PO Spend", f"${po_active['USD $'].sum():,.0f}")
    c3.metric("Total Open PO Volume (Cases)", f"{po_active['Order Qty (Cases)'].sum():,.0f}")
    c4.metric("Current Warehouse Inventory", f"${inv_f['WH Value'].sum():,.0f}")
    if len(po_filtered):
        _cl = pct_of(po_filtered['Convertible'].sum(), len(po_filtered))
        _cu = pct_of(po_filtered.loc[po_filtered['Convertible'], 'USD $'].sum(), po_filtered['USD $'].sum())
        cap(f"Case volumes cover {_cl:,.0f}% of PO lines ({_cu:,.0f}% of the dollars). The rest are mostly plant raw materials bought by the pound "
                   f"or gallon, which have no case size, and are left out of case totals. Warehouse inventory = MDC + SEP + MJM + Bonded (Stores, Plants and OM not included).")
    st.divider()

    # ---------------- Warehouse breakdown ----------------
    st.subheader("Breakdown by Warehouse")
    cap("Left: the receiving warehouse code ('Whs') on each PO line. Right: where today's inventory value sits, by site, from the Purchase Report. "
               "To show friendly names instead of codes, fill in WHS_LABELS at the top of the app file.")
    wa, wb = st.columns(2)
    with wa:
        if not po_filtered.empty:
            ws = pd.DataFrame({'Total PO Spend': po_filtered.groupby('Whs')['USD $'].sum(),
                               'Open PO Spend': po_active.groupby('Whs')['USD $'].sum(),
                               'Open PO Cases': po_active.groupby('Whs')['Order Qty (Cases)'].sum()}).fillna(0)
            ws = ws.rename_axis('Warehouse').reset_index()
            ws['Warehouse'] = ws['Warehouse'].map(whs_label)
            ws = ws.sort_values('Total PO Spend')
            fig = go.Figure()
            fig.add_trace(go.Bar(y=ws['Warehouse'], x=ws['Total PO Spend'], name='Total PO Spend', orientation='h', marker_color='#1f77b4',
                                 text=[f"${x:,.0f}" for x in ws['Total PO Spend']], textposition='outside'))
            fig.add_trace(go.Bar(y=ws['Warehouse'], x=ws['Open PO Spend'], name='Open PO Spend', orientation='h', marker_color='#ff7f0e',
                                 text=[f"${x:,.0f}" for x in ws['Open PO Spend']], textposition='outside'))
            fig.update_layout(barmode='group', height=max(380, 34 * len(ws) + 120), xaxis_title="USD", margin=dict(r=90),
                              title="PO spend by receiving warehouse")
            show_chart(fig, key="t1_whs_po")
        else:
            st.info("No PO lines for the selected filters.")
    with wb:
        site_rows = [{'Site': s, 'Inventory Value': float(inv_f[f'Val_{s}'].sum()), 'Cases On Hand': float(inv_f[f'{s}_Cs'].sum()),
                      'Counted in Warehouse Inventory': 'Yes' if s in WAREHOUSE_SITES else 'No'} for s in ONHAND_SITES]
        sv_ = pd.DataFrame(site_rows).sort_values('Inventory Value')
        fig = px.bar(sv_, x='Inventory Value', y='Site', orientation='h', color='Counted in Warehouse Inventory',
                     color_discrete_map={'Yes': '#1f77b4', 'No': '#b0b0b0'}, text=sv_['Inventory Value'].map(lambda v: f"${v:,.0f}"),
                     title="Inventory value by site")
        fig.update_layout(height=max(380, 34 * len(sv_) + 120), xaxis_title="USD", margin=dict(r=90))
        show_chart(fig, key="t1_whs_inv")
    if not po_filtered.empty:
        ws_t = ws.copy()
        ws_t['% of Total PO Spend'] = ws_t['Total PO Spend'] / ws_t['Total PO Spend'].sum() * 100 if ws_t['Total PO Spend'].sum() else 0
        show_table(style0(ws_t.sort_values('Total PO Spend', ascending=False), money=['Total PO Spend', 'Open PO Spend'], pct=['% of Total PO Spend']))
    st.divider()

    # ---------------- Volume (year to date) and category spend ----------------
    r1a, r1b = st.columns(2)
    with r1a:
        yr = max(slicer_year) if slicer_year else today.year
        last_m = today.month if yr == today.year else 12
        st.subheader(f"PO Volume (Cases) by Month — {yr} Year to Date")
        base_v = by_supplier_cat(po_df)
        mlist = [f"{yr}-{m:02d}" for m in range(1, last_m + 1)]
        cur_v = base_v[base_v['Order Year'] == yr].groupby('Order Month')['Order Qty (Cases)'].sum().reindex(mlist, fill_value=0.0)
        prv_v = base_v[base_v['Order Year'] == yr - 1].groupby('Order Month')['Order Qty (Cases)'].sum().reindex(
            [f"{yr - 1}-{m:02d}" for m in range(1, last_m + 1)], fill_value=0.0)
        dv = pd.DataFrame({'Order Month': mlist, 'Cases': cur_v.values, 'Last Year': prv_v.values})
        dv['3M Avg'] = dv['Cases'].rolling(3).mean()
        if dv['Cases'].sum() > 0:
            fig = px.bar(dv, x='Order Month', y='Cases', text_auto=',.0f')
            fig.add_trace(go.Scatter(x=dv['Order Month'], y=dv['3M Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
            if dv['Last Year'].sum() > 0:
                fig.add_trace(go.Scatter(x=dv['Order Month'], y=dv['Last Year'], mode='markers', name=f'Same month {yr - 1}',
                                         marker=dict(symbol='diamond', size=9, color='#444444')))
            category_axis(fig)
            show_chart(fig, key="t1_vol")
            ytd, ly = dv['Cases'].sum(), dv['Last Year'].sum()
            cap(f"{yr} year to date (Jan-{MONTH_NAMES[last_m - 1][:3]}): {ytd:,.0f} cases" +
                       (f", {(ytd / ly - 1) * 100:+,.0f}% vs the same months of {yr - 1}." if ly > 0 else ".") +
                       (" The current month is month-to-date." if yr == today.year else "") +
                       " Uses the Supplier / Category filters; the Month filter does not apply to a year-to-date view.")
        else:
            st.info(f"No PO volume found for {yr}.")
    with r1b:
        st.subheader("Total Spend by Custom Category")
        if not po_filtered.empty:
            cs_all = pd.DataFrame({'Total Spend': po_filtered.groupby('Custom Category')['USD $'].sum(),
                                   'Open PO Spend': po_active.groupby('Custom Category')['USD $'].sum()}
                                  ).fillna(0).rename_axis('Custom Category').reset_index()
            hidden = cs_all.loc[cs_all['Custom Category'] == 'UNCLASSIFIED', 'Total Spend'].sum()
            cs = cs_all[cs_all['Custom Category'] != 'UNCLASSIFIED'].sort_values('Total Spend')
            fig = go.Figure()
            fig.add_trace(go.Bar(y=cs['Custom Category'], x=cs['Total Spend'], name='Total Spend', orientation='h',
                                 marker_color='#1f77b4', text=[f"${x:,.0f}" for x in cs['Total Spend']], textposition='outside'))
            fig.add_trace(go.Bar(y=cs['Custom Category'], x=cs['Open PO Spend'], name='Open PO Spend', orientation='h',
                                 marker_color='#ff7f0e', text=[f"${x:,.0f}" for x in cs['Open PO Spend']], textposition='outside'))
            fig.update_layout(barmode='group', height=520, xaxis_title="USD", margin=dict(r=90))
            show_chart(fig, key="t1_cat")
            if hidden:
                cap(f"Unclassified items (${hidden:,.0f}) are not shown in this chart.")
        else:
            st.info("No PO lines for the selected filters.")

    # ---------------- Spend by month + conversion accuracy ----------------
    st.subheader("PO Spend by Month — 2025 to Current")
    st_base = po_df if not slicer_category else po_df[po_df['Custom Category'].isin(slicer_category)]
    trend = st_base[st_base['Order Year'] >= 2025]
    if not trend.empty:
        sp = trend.groupby('Order Month')['USD $'].sum().reset_index().sort_values('Order Month')
        sp['3M Avg'] = sp['USD $'].rolling(3).mean()
        fig = px.bar(sp, x='Order Month', y='USD $', text_auto='$,.0f')
        fig.add_trace(go.Scatter(x=sp['Order Month'], y=sp['3M Avg'], mode='lines', name='3M Avg', line=dict(color='red', width=3)))
        category_axis(fig, -45)
        show_chart(fig, key="t1_spend")

        st.markdown("**Can we trust the dollars-to-cases conversion?  Two simple health checks**")
        cap("The PO file counts items in different units (cases, each, kilos, pounds, gallons). The dashboard turns everything into cases so months and categories can be compared. "
            "These two checks show whether that conversion is behaving. "
            "CHECK 1 asks: is the average cost of a case steady from month to month? A sudden jump or drop (a point outside the green band) usually means some items were converted with the wrong case size. "
            "CHECK 2 asks, for items priced per kilo but ordered by the case: does the case weight in the item description agree with the weight the PO dollars imply? "
            "A tall green bar = the weights agree. Natural weight variation on 'AVG' items is expected, so 70-90% is normal.",
            label="ℹ️ What these two checks mean (click to open)")
        cw = pd.DataFrame()
        ck1, ck2 = st.columns(2)
        with ck1:
            st.markdown("**Check 1 - is the cost per case steady?**")
            sale = trend[trend['Item grp'].isin(SALEABLE_GROUPS) & ~trend['Item grp'].isin(PLANT_GROUPS)]
            cv_ = sale[sale['Convertible'] & (sale['Order Qty (Cases)'] > 0)]
            if not cv_.empty:
                m1 = cv_.groupby('Order Month').agg(Spend=('USD $', 'sum'), Cases=('Order Qty (Cases)', 'sum')).reset_index()
                m1['Cost per Case'] = np.where(m1['Cases'] > 0, m1['Spend'] / m1['Cases'].replace(0, 1), np.nan)
                m1['Share'] = [pct_of(cv_.loc[cv_['Order Month'] == m, 'USD $'].sum(), sale.loc[sale['Order Month'] == m, 'USD $'].sum()) for m in m1['Order Month']]
                m1c = m1[m1['Order Month'] != today.strftime('%Y-%m')]               # the month still in progress has too few orders to judge
                if m1c.empty:
                    m1c = m1
                ref = m1c['Cost per Case'].tail(12)
                med = float(ref.median()) if ref.notna().any() else 0.0
                lo, hi = med * 0.75, med * 1.25
                m1['Outside'] = (m1['Cost per Case'] < lo) | (m1['Cost per Case'] > hi)
                n_out = int(m1.loc[m1c.index, 'Outside'].tail(12).sum())
                last = m1c.iloc[-1]
                share_all = pct_of(cv_['USD $'].sum(), sale['USD $'].sum())
                c1a, c1b = st.columns(2)
                c1a.metric("Spend turned into cases", f"{share_all:,.1f}%", "target: 95% or more", delta_color="off")
                c1b.metric(f"Cost per case, {last['Order Month']}", f"${last['Cost per Case']:,.2f}", f"typical ${med:,.2f}", delta_color="off")
                if n_out == 0 and share_all >= 95:
                    st.success("Looks healthy: the cost per case has stayed inside the normal band and almost all spend could be turned into cases.")
                else:
                    msg = []
                    if n_out:
                        msg.append(f"{n_out} of the last 12 months fall outside the normal band (red dots)")
                    if share_all < 95:
                        msg.append(f"only {share_all:,.0f}% of spend could be turned into cases")
                    st.warning("Worth a look: " + " and ".join(msg) + ".")
                fig = go.Figure()
                fig.add_shape(type='rect', xref='paper', x0=0, x1=1, y0=lo, y1=hi, fillcolor='rgba(44,160,44,0.14)', line_width=0)
                fig.add_trace(go.Scatter(x=m1['Order Month'], y=m1['Cost per Case'], mode='lines+markers', name='Average $ per case', line=dict(color='#1f77b4', width=3),
                                         marker=dict(size=9, color=np.where(m1['Outside'], '#d62728', '#1f77b4').tolist())))
                fig.update_layout(title="Average cost per case by month (green band = normal range, red dot = outside it)", yaxis=dict(title='$ per case', rangemode='tozero'),
                                  showlegend=False, height=380)
                category_axis(fig, -45)
                show_chart(fig, key="t1_chk1")
                cap("Saleable items only (plant raw materials are bought in bags, drums and gallons and would distort an average). The band is the typical cost per case +/- 25% (the month still in progress is shown but not judged).")
            else:
                st.info("No convertible saleable lines to check.")
        with ck2:
            st.markdown("**Check 2 - do case weights agree with the PO dollars?**")
            cw = trend[trend['Implied KG per Case'].notna() & trend['Weight Dev %'].notna()].copy()
            if not cw.empty:
                cw['Within 10%'] = cw['Weight Dev %'].abs() <= 10
                m2 = cw.groupby('Order Month').agg(Lines=('PO no', 'size'), Ok=('Within 10%', 'mean')).reset_index()
                m2['Ok'] = m2['Ok'] * 100
                overall = float(cw['Within 10%'].mean() * 100)
                few = m2['Lines'] < 10
                c2a, c2b = st.columns(2)
                c2a.metric("Lines that agree (within 10%)", f"{overall:,.0f}%", f"{len(cw):,} lines checked", delta_color="off")
                c2b.metric("Typical gap", f"{cw['Weight Dev %'].abs().median():,.1f}%", "natural variation on AVG items", delta_color="off")
                if overall >= 70:
                    st.success("Looks healthy: most catch-weight lines agree with the weight in the description.")
                else:
                    st.warning("Worth a look: many catch-weight lines disagree with the weight in the description. See the review list below.")
                cols_ = np.where(few, '#b0b0b0', np.where(m2['Ok'] >= 70, '#2ca02c', np.where(m2['Ok'] >= 50, '#f2c94c', '#d62728'))).tolist()
                fig = go.Figure()
                fig.add_trace(go.Bar(x=m2['Order Month'], y=m2['Ok'], marker_color=cols_, text=[f"{v:,.0f}%" for v in m2['Ok']], textposition='outside',
                                     customdata=m2['Lines'], hovertemplate="%{x}<br>%{y:,.0f}% agree<br>%{customdata} lines checked<extra></extra>"))
                fig.add_hline(y=70, line_dash='dash', line_color='#555555', annotation_text="70% = normal")
                fig.update_layout(title="Share of catch-weight lines that agree with the PO dollars (grey = fewer than 10 lines that month)",
                                  yaxis=dict(title='% of lines within 10%', range=[0, 115]), showlegend=False, height=380)
                category_axis(fig, -45)
                show_chart(fig, key="t1_chk2")
                cap(f"{len(cw):,} catch-weight lines checked; {overall:,.0f}% agree within 10%.")
            else:
                st.info("No catch-weight lines (ordered by the case, priced per KG) in this period.")
        if not cw.empty:
            off = cw[cw['Weight Dev %'].abs() > 25].copy()
            if not off.empty:
                with st.expander(f"Catch-weight lines to review - description weight is more than 25% away from the PO dollars ({len(off):,} lines)"):
                    off['Gap $'] = off['USD $'].abs()
                    t_ = off.sort_values('Gap $', ascending=False).head(200)[['PO no', 'Supplier', 'Item number', 'ItemDescription', 'Case KG', 'Implied KG per Case', 'Weight Dev %', 'USD $']]
                    t_ = t_.rename(columns={'Case KG': 'KG per case used', 'Implied KG per Case': 'KG per case from PO dollars', 'ItemDescription': 'Item'})
                    show_table(style0(t_, one_dec=['KG per case used', 'KG per case from PO dollars'], money=['USD $'], pct=['Weight Dev %']), height=320)
                    csv_button(t_, "⬇️ Export catch-weight review list", "catch_weight_review.csv", "dl_cw")
    else:
        st.info("No PO lines from 2025 onward.")

    # ---------------- Buyer ----------------
    st.subheader("Spend by Buyer")
    cap(f"Current open POs (Hst {OPEN_PO[0]}-{OPEN_PO[1]}). Buyers under 5% of open spend are grouped as 'Other Buyers'.")
    if not po_active.empty and po_active['USD $'].sum() > 0:
        b = po_active.groupby('Buyer')['USD $'].sum().reset_index()
        b = b[b['USD $'] > 0]
        small = b['USD $'] / b['USD $'].sum() < 0.05
        others = sorted(b.loc[small, 'Buyer'].astype(str).tolist())
        b.loc[small, 'Buyer'] = 'Other Buyers'
        b = b.groupby('Buyer', as_index=False)['USD $'].sum()
        fig = px.pie(b, values='USD $', names='Buyer', hole=0.4)
        fig.update_traces(textposition='inside', texttemplate="%{label}<br>$%{value:,.0f}<br>%{percent}")
        fig.update_layout(height=600)
        show_chart(fig, key="t1_buyer")
        if others:
            cap("Other Buyers = " + ", ".join(others))
    else:
        st.info("No open PO spend for the selected filters.")

# ==========================================================================
# TAB 2 — GIT & EXPEDITING
# ==========================================================================
with tabs[1], guard():
    st.header("Goods In Transit: Clearance Tracking & Exceptions")

    # ---------------- Clearance metrics (rebuilt) ----------------
    st.subheader("Clearance Metrics: Containers Arrived, Cleared (Yard) and Stripped by Month")
    cap("Only real container numbers are counted (4 letters + 7 digits, e.g. SMLU8514940). SEZ transfers, internal sales, air waybill numbers and typed notes are ignored, "
               "and a container is counted once per month. Arrived = reached the port. Cleared = Yard date. Stripped = Stripped date. "
               "All order types (including P01) are counted because the containers are physical. Supplier / Category filters apply; the Order Year / Month filters do not.")
    ship_src = by_supplier_cat(po_raw[po_raw['Status Code'].between(*PO_LIVE)])
    ship_src = ship_src[ship_src['Shipment Key'] != '']
    if ship_src.empty:
        st.info("No containers found for the selected filters.")
    else:
        ship = ship_src.groupby('Shipment Key').agg(Container=('Container ID', 'first'), Arrival=('Arrival', 'min'), Yard=('Yard Date', 'min'),
                                                    Stripped=('Stripped Date', 'min'), Lines=('PO no', 'size'), USD=('USD $', 'sum')).reset_index()
        ship['Port to Yard (days)'] = (ship['Yard'] - ship['Arrival']).dt.days
        ship['Yard to Stripped (days)'] = (ship['Stripped'] - ship['Yard']).dt.days
        ship.loc[ship['Port to Yard (days)'] < 0, 'Port to Yard (days)'] = np.nan
        ship.loc[ship['Yard to Stripped (days)'] < 0, 'Yard to Stripped (days)'] = np.nan

        back = st.slider("Months to show", 6, 24, 12, key="clear_months")
        m_start = today.replace(day=1) - pd.DateOffset(months=back - 1)
        mlist = month_range(m_start, today)

        def _per_month(col):                           # unique container numbers whose date falls in each month
            d = ship[ship[col].notna()]
            return d.groupby(d[col].dt.strftime('%Y-%m'))['Container'].nunique().reindex(mlist, fill_value=0)

        cm = pd.DataFrame({'Month': mlist, 'Arrived at port': _per_month('Arrival').values, 'Cleared (Yard)': _per_month('Yard').values,
                           'Stripped': _per_month('Stripped').values})
        cm['Your count: cleared'] = [MANUAL_CONTAINER_COUNTS.get(m_, (np.nan, np.nan))[1] for m_ in mlist]
        cm['Your count: stripped'] = [MANUAL_CONTAINER_COUNTS.get(m_, (np.nan, np.nan))[0] for m_ in mlist]
        ship['_ym'] = ship['Yard'].dt.strftime('%Y-%m')
        cm['Avg days port to yard'] = ship.groupby('_ym')['Port to Yard (days)'].mean().reindex(mlist).values

        # containers waiting at the end of each month
        wait_port, wait_yard = [], []
        for ym in mlist:
            end = min(pd.Period(ym, freq='M').end_time.normalize(), today)
            wait_port.append(int(((ship['Arrival'] <= end) & (ship['Yard'].isna() | (ship['Yard'] > end)) & (ship['Stripped'].isna() | (ship['Stripped'] > end))).sum()))
            wait_yard.append(int(((ship['Yard'] <= end) & (ship['Stripped'].isna() | (ship['Stripped'] > end))).sum()))
        cm['Waiting at port (not yet cleared)'] = wait_port
        cm['In yard (not yet stripped)'] = wait_yard

        recent = ship[ship['Yard'] >= today - pd.Timedelta(days=90)]
        recent_s = ship[ship['Stripped'] >= today - pd.Timedelta(days=90)]
        now_port = int((ship['Arrival'].notna() & ship['Yard'].isna() & ship['Stripped'].isna()).sum())
        now_yard = int((ship['Yard'].notna() & ship['Stripped'].isna()).sum())
        stale = ship[ship['Arrival'].notna() & ship['Yard'].isna() & ship['Stripped'].isna() & (ship['Arrival'] < today - pd.Timedelta(days=90))]
        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("Containers at port now", f"{now_port:,}")
        kc2.metric("Cleared, waiting in yard", f"{now_yard:,}")
        kc3.metric("Typical days port to yard (last 90 days)", f"{recent['Port to Yard (days)'].median():,.0f}" if recent['Port to Yard (days)'].notna().any() else "-")
        kc4.metric("Typical days yard to stripped (last 90 days)", f"{recent_s['Yard to Stripped (days)'].median():,.0f}" if recent_s['Yard to Stripped (days)'].notna().any() else "-")

        ga, gb = st.columns([3, 2])
        with ga:
            fig = go.Figure()
            for nm, colr in [('Arrived at port', '#7f7f7f'), ('Cleared (Yard)', 'darkorange'), ('Stripped', 'royalblue')]:
                fig.add_trace(go.Bar(x=cm['Month'], y=cm[nm], name=nm, marker_color=colr, text=cm[nm], textposition='outside'))
            for nm, colr, sym in [('Your count: cleared', '#8c4a00', 'diamond'), ('Your count: stripped', '#12306b', 'diamond')]:
                if cm[nm].notna().any():
                    fig.add_trace(go.Scatter(x=cm['Month'], y=cm[nm], name=nm, mode='markers', marker=dict(symbol=sym, size=11, color=colr, line=dict(color='white', width=1))))
            fig.add_trace(go.Scatter(x=cm['Month'], y=cm['Avg days port to yard'], name='Avg days port to yard', yaxis='y2', mode='lines+markers',
                                     line=dict(color='#d62728', width=3)))
            fig.update_layout(barmode='group', title="Containers per month, and how long clearance takes", yaxis=dict(title='Containers'),
                              yaxis2=dict(title='Avg days (port to yard)', overlaying='y', side='right', rangemode='tozero'), height=440)
            category_axis(fig, -45)
            show_chart(fig, key="t2_clear")
        with gb:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=cm['Month'], y=cm['Waiting at port (not yet cleared)'], name='At port, not yet cleared', marker_color='#d62728'))
            fig.add_trace(go.Bar(x=cm['Month'], y=cm['In yard (not yet stripped)'], name='In yard, not yet stripped', marker_color='#f2c94c'))
            fig.update_layout(barmode='stack', title="Containers waiting at month end", yaxis=dict(title='Containers'), height=440)
            category_axis(fig, -45)
            show_chart(fig, key="t2_wait")
        if len(stale):
            cap(f"⚠️ {len(stale):,} containers show an arrival date more than 90 days ago but no yard or stripped date. "
                       "Either they are still waiting (follow up) or the dates were never entered in the PO file.")
        show_table(style0(cm[['Month', 'Arrived at port', 'Cleared (Yard)', 'Stripped', 'Avg days port to yard', 'Waiting at port (not yet cleared)',
                              'In yard (not yet stripped)']].iloc[::-1], one_dec=['Avg days port to yard']), height=260)

        # ---- how close the dashboard is to the team's own count
        rc = cm[cm['Your count: cleared'].notna()].copy()
        if not rc.empty:
            rc['Cleared: dashboard minus yours'] = rc['Cleared (Yard)'] - rc['Your count: cleared']
            rc['Stripped: dashboard minus yours'] = rc['Stripped'] - rc['Your count: stripped']
            with st.expander("Check against your own container counts (click to open)", expanded=False):
                avg_c = rc['Cleared: dashboard minus yours'].abs().mean()
                avg_s = rc['Stripped: dashboard minus yours'].abs().mean()
                st.markdown(f"Average gap per month: **{avg_c:,.1f} containers** on Cleared and **{avg_s:,.1f}** on Stripped. A gap of a few containers is normal "
                            "(a container counted in the month before or after, or a number typed with a mistake). If the gap is large, look at the Data Checks tab "
                            "for container numbers that do not look valid.")
                swap_c = (rc['Cleared (Yard)'] - rc['Your count: stripped']).abs().mean()
                swap_s = (rc['Stripped'] - rc['Your count: cleared']).abs().mean()
                if swap_c + swap_s + 2 < avg_c + avg_s:
                    st.markdown("⚠️ The dashboard's *Yard* counts are closer to your *Stripped* column, and its *Stripped* counts are closer to your *Cleared* column. "
                                "Please check which of your two columns is which (the dashboard follows your written rule: Cleared = Yard date, Stripped = Stripped date).")
                show_table(style0(rc[['Month', 'Cleared (Yard)', 'Your count: cleared', 'Cleared: dashboard minus yours', 'Stripped', 'Your count: stripped',
                                      'Stripped: dashboard minus yours']].iloc[::-1]))

    # ---------------- Pending containers ----------------
    st.divider()
    st.subheader("Pending Containers / Open Orders")
    git = by_supplier_cat(po_active_all)
    pipe_lines = git[git['Status'].isin(PENDING_STAGES) & git['Recd dt'].isna()]
    nd = pipe_lines[pipe_lines['Status'] == '1-Not Departed']
    cont = pipe_lines[pipe_lines['Status'].isin(TRACKED_STAGES) & (pipe_lines['Shipment Key'] != '')].copy()
    cont['Rank'] = cont['Status'].map(STAGE_RANK)
    if HAS_TERMS:                                    # when the money is due: expected arrival + the supplier's payment terms
        cont = add_terms(cont, 'Supplier')
        cont['Pay Due'] = payment_anchor(cont) + pd.to_timedelta(cont['Terms Days'], unit='D')
    else:
        cont['Pay Due'], cont['Terms Label'] = pd.NaT, ''

    agg = pd.DataFrame()
    if not cont.empty:
        agg = cont.groupby('Shipment Key').agg(
            Container=('Container ID', 'first'), Rank=('Rank', 'min'), Buyer=('Buyer', join_unique), Supplier=('Supplier', join_unique),
            Description=('Description', join_unique), POs=('PO no', 'nunique'), USD=('USD $', 'sum'),
            Cases=('Order Qty (Cases)', 'sum'), Req=('Req dt', 'min'), ETA=('ETA', 'min'), Port=('Port of Disp', 'first'),
            Terms=('Terms Label', 'first'), PayDue=('Pay Due', 'min')).reset_index()
        rank_to_status = {v: k for k, v in STAGE_RANK.items()}
        agg['Status'] = agg['Rank'].map(rank_to_status)

        def _contents(g):
            t = g.groupby('ItemDescription')['USD $'].sum().sort_values(ascending=False)
            return '; '.join(t.index[:3]) + (f' (+{len(t) - 3} more)' if len(t) > 3 else '')
        cmap = {k: _contents(gr) for k, gr in cont.groupby('Shipment Key')}
        agg['Contents'] = agg['Shipment Key'].map(cmap)
        past = agg['Req'] < today
        early_leg = agg['Status'].isin(['1-Not Departed', '2-On the Water'])
        agg['Timing'] = np.where(past, 'Past Req Date',
                                 np.where(early_leg & agg['ETA'].notna() & (agg['ETA'] < today), 'Past ETA', 'On Track'))
        agg['Days Past Req'] = np.where(past, (today - agg['Req']).dt.days, 0)

    k1, k2, k3, k4 = st.columns(4)
    if not agg.empty:
        landed_c = agg[agg['Status'].isin(['3-At the Port', '4-Costed', '5-Yard'])]
        wat_c = agg[agg['Status'] == '2-On the Water']
        k1.metric("Containers at Port / Yard", f"{len(landed_c):,}", f"{(landed_c['Timing'] == 'Past Req Date').sum():,} past Req dt", delta_color="inverse")
        k2.metric("On the Water", f"{len(wat_c):,}", f"{(wat_c['Timing'] == 'On Track').sum():,} on track", delta_color="normal")
        k3.metric("Open $ in Numbered Containers", f"${agg['USD'].sum():,.0f}")
    else:
        k1.metric("Containers at Port / Yard", "0"); k2.metric("On the Water", "0"); k3.metric("Open $ in Numbered Containers", "$0")
    k4.metric("Not Departed (no departure date yet)", f"{len(nd):,}",
              f"{(nd['Req dt'] < today).sum():,} past Req dt · ${nd['USD $'].sum():,.0f}", delta_color="off")

    if not agg.empty:
        fa, fb, fc_, fd = st.columns([2, 2, 2, 1])
        stat_sel = fa.multiselect("Container status", TRACKED_STAGES, default=TRACKED_STAGES)
        buyer_sel = fb.multiselect("Buyer", sorted(cont['Buyer'].astype(str).unique()))
        text_sel = fc_.text_input("Search container / supplier / description")
        only_past = fd.checkbox("Only past Req dt", value=False)

        view = agg[agg['Status'].isin(stat_sel)]
        if buyer_sel:
            keep = set(cont[cont['Buyer'].isin(buyer_sel)]['Shipment Key'])
            view = view[view['Shipment Key'].isin(keep)]
        if text_sel:
            m = (view['Container'].str.contains(text_sel, case=False, na=False, regex=False) | view['Supplier'].str.contains(text_sel, case=False, na=False, regex=False)
                 | view['Description'].astype(str).str.contains(text_sel, case=False, na=False, regex=False) | view['Contents'].str.contains(text_sel, case=False, na=False, regex=False))
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
                             category_orders={'Status': list(STAGE_RANK.keys())})
                fig.update_layout(height=380, title="Containers by stage & timing")
                show_chart(fig, key="t2_stage")
        with cc2:
            show = view[['Container', 'Status', 'Timing', 'Buyer', 'Supplier', 'Description', 'Contents', 'POs',
                         'USD', 'Cases', 'Req', 'ETA', 'Days Past Req'] + (['Terms', 'PayDue'] if HAS_TERMS else [])].copy()
            show['Req'] = show['Req'].dt.strftime('%Y-%m-%d')
            show['ETA'] = show['ETA'].dt.strftime('%Y-%m-%d')
            if HAS_TERMS:
                show['PayDue'] = show['PayDue'].dt.strftime('%Y-%m-%d')
            show = show.rename(columns={'Req': 'Req dt', 'USD': 'Open USD', 'Terms': 'Payment Terms', 'PayDue': 'Payment Due (est.)'})
            red = (view['Timing'] == 'Past Req Date').values

            def _hl(row):
                i = show.index.get_loc(row.name)
                return ['background-color:#f8d7da;color:#842029' if red[i] else '' for _ in row]
            show_table(style0(show.reset_index(drop=True), money=['Open USD']).apply(_hl, axis=1), height=380)
        cap("Red rows = Req dt has passed. Port / Yard containers are listed first, then On the Water; sorted by earliest Req dt. "
                   "Lines with no departure date yet are summarized in the 'Not Departed' tile above, not listed.")
    else:
        st.info("No numbered pending containers for the selected filters.")

    # ---------------- Delivery performance ----------------
    st.divider()
    st.subheader("Vendor Delivery Performance — On Time vs Late")
    dp = by_supplier_cat(po_df)
    dp = dp[dp['Status Code'].between(*CLOSED_PO) & dp['Recd dt'].notna() & dp['Req dt'].notna()].copy()
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
            fig.update_layout(barnorm='percent', yaxis_title="% of lines")
            category_axis(fig, -45)
            show_chart(fig, key="t2_mix")
        with p2:
            cat = dp.groupby('Custom Category').agg(Lines=('PO no', 'size'), OnTime=('Days Late', lambda s: (s <= 0).mean() * 100)).reset_index().sort_values('OnTime')
            fig = px.bar(cat, x='OnTime', y='Custom Category', orientation='h', text=cat['OnTime'].map(lambda v: f"{v:,.0f}%"),
                         color='OnTime', color_continuous_scale='RdYlGn', range_color=[50, 100], title="On-time % by category")
            fig.update_layout(coloraxis_showscale=False, xaxis_title="On-time %")
            show_chart(fig, key="t2_cat")

        vt = dp.groupby('Supplier').agg(Lines=('PO no', 'size'), OnTime=('Days Late', lambda s: (s <= 0).mean() * 100),
                                        AvgLate=('Var Days', lambda s: s[s > grace].mean()),
                                        Pct15=('Bucket', lambda s: (s == '15+ days late').mean() * 100)).reset_index()
        vt = vt[vt['Lines'] >= min_lines].sort_values('OnTime')
        vt.columns = ['Supplier', 'Lines', 'On-time %', 'Avg days late (when late)', '% lines 15+ days late']
        show_table(style0(vt, pct=['On-time %', '% lines 15+ days late']), height=350)
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
        show_chart(fig, key="t2_aging")
    else:
        st.info("No open orders are past their Req dt.")

# ==========================================================================
# TAB 3 — INVENTORY HEALTH
# ==========================================================================
with tabs[2], guard():
    st.header("Inventory Health: Turnover, Dead Stock & Slow Moving")

    # ---------------- Turnover ----------------
    st.subheader("Inventory Turnover & Sell-Through by Category")
    cap("Every site quantity is converted to cases (packs by the Conversion Factor, KG items by the weight written in the item description). "
        "Total On-Hand = MDC + SEP + MJM + Stores + Bonded + Plants; OM stock is not counted and Allocated is shown separately. "
        "Turnover = (3-month avg monthly sales x 12) / Total On-Hand cases. Only saleable categories are shown: meat plant, juice plant, operations, beverage systems and unclassified items are left out, "
        "as are discontinued status 80 and never-calculated statuses (0-14, 86-99).")
    tv_src = inv_f[~inv_f['Item Status'].isin(DELETED + HARD_DISC_STATUSES) & ~inv_f['Custom Category'].isin(NO_SS_CATS)]
    tv = tv_src.groupby('Custom Category')[SITE_CS + ['Total_Cases', '3M_Avg_Depletion_Demand']].sum().reset_index()
    tv['Annualized Turnover (x)'] = np.where(tv['Total_Cases'] > 0, tv['3M_Avg_Depletion_Demand'] * 12 / tv['Total_Cases'].replace(0, 1), 0)
    tv['Days on Hand'] = np.where(tv['3M_Avg_Depletion_Demand'] > 0, tv['Total_Cases'] / (tv['3M_Avg_Depletion_Demand'].replace(0, 1) * 12 / 365), np.nan)
    tv['Sell-Through %'] = np.where((tv['3M_Avg_Depletion_Demand'] + tv['Total_Cases']) > 0,
                                    tv['3M_Avg_Depletion_Demand'] / (tv['3M_Avg_Depletion_Demand'] + tv['Total_Cases']).replace(0, 1) * 100, 0)
    tv = tv.sort_values('Annualized Turnover (x)', ascending=False)
    tv = tv.rename(columns={**{c: c.replace('_Cs', '') for c in SITE_CS}, 'OM_Cs': 'OM (not counted)', 'Total_Cases': 'Total On-Hand (Cs)',
                            '3M_Avg_Depletion_Demand': 'Avg Monthly Depletion (Cs)'})
    t1, t2 = st.columns(2)
    with t1:
        fig = px.bar(tv, x='Custom Category', y='Annualized Turnover (x)', text_auto=',.1f', title="Annualized turnover (x)")
        fig.update_layout(xaxis_tickangle=-45)
        show_chart(fig, key="t3_turn")
    with t2:
        site_long = tv.melt(id_vars='Custom Category', value_vars=ONHAND_SITES, var_name='Site', value_name='Cases')
        fig = px.bar(site_long, x='Custom Category', y='Cases', color='Site', title="Where the cases sit (by site)")
        fig.update_layout(xaxis_tickangle=-45)
        show_chart(fig, key="t3_sites")
    show_table(style0(tv, pct=['Sell-Through %']))
    with st.expander("Plants, operations and other non-sellable stock (kept out of the charts above) - click to open"):
        cap("Plant raw materials and packaging are counted in labels, pouches, kilos and litres, so 'cases' do not describe them and they always dwarf the saleable categories. "
            "They are shown here by dollar value instead.")
        np_src = inv_f[~inv_f['Item Status'].isin(DELETED + HARD_DISC_STATUSES) & inv_f['Custom Category'].isin(NO_SS_CATS)]
        if np_src.empty:
            st.info("No stock in these categories.")
        else:
            npt = np_src.groupby('Custom Category').agg(Items=('Item Number', 'nunique'), ItemsWithStock=('Inventory Value', lambda s_: int((s_ > 0).sum())),
                                                        Value=('Inventory Value', 'sum')).reset_index()
            npt.columns = ['Category', 'Items', 'Items With Stock', 'Inventory Value ($)']
            show_table(style0(npt.sort_values('Inventory Value ($)', ascending=False), money=['Inventory Value ($)']))

    # ---------------- Months of cover ----------------
    st.divider()
    st.subheader("Months of Cover by Category")
    cap("How long today's warehouse stock lasts at the current selling pace. Counted: active items (status 20) that are selling. "
               "Stock = MDC + SEP + MJM + Bonded cases - Stores, Plants and OM stock are NOT counted. "
               f"One heavily stocked item can count for at most {COVER_CAP_MONTHS} months, so a few big stocks cannot hide shortages elsewhere in the category. "
               "The right-hand chart shows the same thing item by item: the red share is items with under a month of stock.")
    ct = cover_table(inv_f)
    cover_charts(ct, "t3")
    if not ct.empty:
        show_table(style0(ct, one_dec=['Months of Cover', 'Months (no cap)'], pct=['Under 1 Month %', '1-3 Months %', 'Over 3 Months %']))

    # ---------------- Dead & slow ----------------
    st.divider()
    ds_src = inv_f[~inv_f['Item Status'].isin(DELETED + HARD_DISC_STATUSES + ([] if DEAD_STOCK_INCLUDES_DISCONTINUED else SELL_ONLY_STATUSES))]
    ds_src = ds_src[~ds_src['Custom Category'].isin(EXCLUDED_CATS)]
    r3a, r3b = st.columns(2)
    with r3a:
        st.subheader("Dead Stock by Category")
        cap("Inventory value on hand with no movement over the trailing 3 months. Includes status 50 (sell-only) items; status 80 items are not calculated.")
        dead = ds_src[(ds_src['Inventory Value'] > 100) & (ds_src['3M_Avg_Depletion'] <= 0)]
        if not dead.empty:
            dc = dead.groupby('Custom Category')['Inventory Value'].sum().reset_index()
            fig = px.bar(dc, x='Custom Category', y='Inventory Value', text_auto='$,.0f')
            fig.update_layout(xaxis_tickangle=-45)
            show_chart(fig, key="t3_dead")
        else:
            st.info("No dead stock identified.")
    with r3b:
        st.subheader("🐢 Slow Moving Stock by Category")
        cap("Actual sales below 70% of forecast (active items only).")
        sl = inv_live[~inv_live['Custom Category'].isin(EXCLUDED_CATS)]
        sl = sl[(sl['WH Value'] > 0) & (sl['3M_Avg_Forecast_Cases'] > 0)]
        sl = sl[sl['3M_Avg_Depletion'] / sl['3M_Avg_Forecast_Cases'] < 0.70]
        if not sl.empty:
            sc_ = sl.groupby('Custom Category')['WH Value'].sum().reset_index()
            fig = px.bar(sc_, x='Custom Category', y='WH Value', text_auto='$,.0f')
            fig.update_layout(xaxis_tickangle=-45)
            show_chart(fig, key="t3_slow")
        else:
            st.info("No slow moving stock identified.")

    # ---------------- Safety stock ----------------
    st.divider()
    st.subheader("Safety Stock vs. Current Stock Levels")
    cap("Safety stock = the item's lead time (in months) × its monthly forecast (average of the next 3 months in the CPJ Forecast file). "
               "Stock = warehouse cases (MDC + SEP + MJM + Bonded). Active items only (status 20). Unclassified, meat plant, juice plant, operations and beverage-system items are left out. "
               "By default only items that have a confirmed forecast are shown.")
    use_calc = st.checkbox("Also include items with no forecast (their safety stock is calculated from the last 3 months of sales)", value=False, key="ss_calc")
    min_fc = st.slider("Ignore items selling or forecast below (cases per month)", 0.0, 20.0, 1.0, 0.5, key="ss_minfc",
                       help="Items that move less than this are not worth a buyer's time. Set to 0 to see everything.")
    rh1, rh2 = st.columns([3, 2])
    hide_retail = rh1.checkbox(f"Hide low-value items bought for the retail sites ({', '.join(RETAIL_WHS)})", value=True, key="ss_retail",
                               help="Items that mostly go to the retail sites and cost little per case can drown out the hospitality items. Untick to see them.")
    retail_cut = rh2.number_input("Low value = latest cost per case under ($)", min_value=0.0, max_value=1000.0, value=float(RETAIL_LOW_VALUE_USD), step=5.0, key="ss_retail_cut")
    ss = inv_live[~inv_live['Custom Category'].isin(NO_SS_CATS)].copy()
    is_retail_low = (ss['Retail PO Share'] >= RETAIL_SHARE_MIN) & (ss['Last PO Cost per Case (USD)'] < retail_cut)
    n_retail = int(is_retail_low.sum())
    if hide_retail:
        ss = ss[~is_retail_low].copy()
    if not use_calc:
        ss = ss[ss['Has Forecast']]
    ss = ss[ss['Safety Stock (Cases)'] > 0].copy()
    n_tiny = int((ss['Monthly Demand Used (Cases)'] < min_fc).sum())
    ss = ss[ss['Monthly Demand Used (Cases)'] >= min_fc].copy()
    ss['Below SS'] = ss['WH_Cases'] <= ss['Safety Stock (Cases)']
    ss['Inbound Status'] = np.where(ss['Cases At Port'] > 0, 'Arrived (port / yard)',
                                    np.where(ss['Cases On Order'] > 0, 'On order (not yet arrived)', 'Nothing inbound'))
    below = ss[ss['Below SS']].copy()
    act_needed = below[below['Inbound Status'] == 'Nothing inbound']
    sm1, sm2, sm3, sm4 = st.columns(4)
    sm1.metric("Items checked against safety stock", f"{len(ss):,}")
    sm2.metric("At or below safety stock", f"{len(below):,}", f"{pct_of(len(below), len(ss)):,.0f}% of items checked", delta_color="off")
    sm3.metric("…and nothing inbound (buyer action)", f"{len(act_needed):,}", "needs a buyer decision", delta_color="inverse")
    sm4.metric("…replenishment already coming", f"{len(below) - len(act_needed):,}", delta_color="off")
    if hide_retail and n_retail:
        cap(f"{n_retail:,} low-value retail-site items (at least {RETAIL_SHARE_MIN:.0%} of their POs go to {', '.join(RETAIL_WHS)} and they cost under ${retail_cut:,.0f} a case) are hidden. Untick the box above to include them.")
    if n_tiny:
        cap(f"{n_tiny:,} very slow items (under {min_fc:g} case per month) are left out. Lower the slider to include them.")
    if not use_calc:
        n_nofc = int(((~inv_live['Has Forecast']) & ~inv_live['Custom Category'].isin(NO_SS_CATS) & (inv_live['Safety Stock (Cases)'] > 0)).sum())
        cap(f"{n_nofc:,} more active items sell but have no forecast, so they are not shown. Tick the box above to include them.")
    if not below.empty:
        st.markdown("**Items at or below safety stock, by category - and whether replenishment is already coming**")
        cnt = below.groupby(['Custom Category', 'Inbound Status']).size().reset_index(name='Items')
        fig = px.bar(cnt, x='Custom Category', y='Items', color='Inbound Status', text='Items',
                     color_discrete_map={'Nothing inbound': '#d62728', 'On order (not yet arrived)': '#f2c94c', 'Arrived (port / yard)': '#2ca02c'},
                     category_orders={'Inbound Status': ['Nothing inbound', 'On order (not yet arrived)', 'Arrived (port / yard)']})
        fig.update_layout(xaxis_tickangle=-45, yaxis_title="# items at/below safety stock")
        show_chart(fig, key="t3_ss_cat")
        tot = ss.groupby('Custom Category').size().rename('Items checked').reset_index()
        bl = below.groupby('Custom Category').size().rename('At/below SS').reset_index()
        nothing = act_needed.groupby('Custom Category').size().rename('Nothing inbound').reset_index()
        cs_tab = tot.merge(bl, how='left').merge(nothing, how='left').fillna(0)
        cs_tab['% at/below SS'] = cs_tab['At/below SS'] / cs_tab['Items checked'] * 100
        show_table(style0(cs_tab.sort_values('At/below SS', ascending=False), pct=['% at/below SS']))

        if not act_needed.empty:
            by_b = act_needed.groupby('Responsible Buyer').size().reset_index(name='Items needing action').sort_values('Items needing action')
            fig = px.bar(by_b, x='Items needing action', y='Responsible Buyer', orientation='h', text='Items needing action',
                         title="Items at/below safety stock with nothing inbound - by buyer")
            show_chart(fig, key="t3_ss_buyer")


        det = below.sort_values('WH_Cases')[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases At Port', 'Cases On Order',
                                             'Monthly Demand Used (Cases)', 'SS Basis', 'Safety Stock (Cases)', 'Lead Time (Months)', 'Inbound Status', 'Responsible Buyer']]
        det = det.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)', 'Cases At Port': 'Arrived (Cs)', 'Monthly Demand Used (Cases)': 'Monthly Demand Used (Cs)',
                                  'SS Basis': 'Demand Based On'})
        show_table(style0(det, one_dec=['Lead Time (Months)', 'Monthly Demand Used (Cs)', 'Safety Stock (Cases)']), height=350)
        csv_button(det, "⬇️ Export items at/below safety stock", "below_safety_stock.csv", "dl_ss")
    else:
        st.info("No items currently at or below their safety stock.")

    st.subheader("Safety Stock Alerts — Significant Forecast Changes")
    cap("Near-term forecast average vs trailing actual sales differs by more than 30% - buyers should reassess safety stock.")
    fch = inv_live[(inv_live['3M_Avg_Depletion_Demand'] > 0) & inv_live['Has Forecast']].copy()
    fch['Forecast Change %'] = (fch['3M_Avg_Forecast_Cases'] - fch['3M_Avg_Depletion_Demand']) / fch['3M_Avg_Depletion_Demand'] * 100
    sig = fch[fch['Forecast Change %'].abs() > 30].sort_values('Forecast Change %', ascending=False)
    if not sig.empty:
        sig = sig[['Item Number', 'Item Description', 'Custom Category', '3M_Avg_Depletion_Demand', '3M_Avg_Forecast_Cases', 'Forecast Change %', 'Safety Stock (Cases)']]
        sig = sig.rename(columns={'3M_Avg_Depletion_Demand': 'Recent Avg Sales (Cs)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cs)'})
        show_table(style0(sig, one_dec=['Recent Avg Sales (Cs)', 'Near-Term Forecast (Cs)', 'Safety Stock (Cases)']).format({"Forecast Change %": "{:+,.0f}%"}), height=350)
    else:
        st.info("No items with a significant forecast shift.")

    st.divider()
    st.subheader("Reorder Exception Summary")
    cap("On-hand warehouse stock + everything on order won't outlast the item's lead time against the near-term forecast.")
    rb = inv_live[inv_live['3M_Avg_Forecast_Cases'] > 0].copy()
    rb['Months Cover (On Hand + Pipeline)'] = (rb['WH_Cases'] + rb['Cases Inbound']) / rb['3M_Avg_Forecast_Cases']
    rf = rb[rb['Months Cover (On Hand + Pipeline)'] < rb['Lead Time (Months)']].sort_values('Months Cover (On Hand + Pipeline)')
    if not rf.empty:
        rf = rf[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases Inbound', '3M_Avg_Forecast_Cases', 'Lead Time (Months)',
                 'Months Cover (On Hand + Pipeline)', 'Responsible Buyer']]
        rf = rf.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cs)'})
        show_table(style0(rf, one_dec=['Lead Time (Months)', 'Months Cover (On Hand + Pipeline)', 'Near-Term Forecast (Cs)']), height=400)
        cap(f"{len(rf):,} items flagged.")
    else:
        st.info("No items currently flagged for reorder.")

    # ---------------- Item replacement / substitutes ----------------
    st.divider()
    st.subheader("Item Replacement & Substitute Item Performance")
    cap("Items are paired when they share the same item Category + Format in the Purchase Report. Each pair then gets a 0-100 match score from extra checks, "
               "so you can see how sure we are. Only active (status 20) saleable items count as substitutes; stock = MDC cases. Plant raw materials are never offered as substitutes.")
    with st.expander("How the match score works"):
        st.markdown(
            "| Check | Points |\n|---|---|\n| Same Category + Format (how the pair was found) | 30 |\n| Same item group (e.g. both FF_DAIRY) | 15 |\n"
            "| Same flavor | 25 |\n| Same brand | 10 |\n| Same unit size (e.g. 750ML) | 10 (5 if close) |\n| Similar wording in the description | 10 (5 if partly) |\n"
            "| Clearly different unit size (e.g. 200ML vs 1L) | −15 (−5 if somewhat different) |\n\n"
            "**High** = 80+, **Medium** = 60-79, **Low** = under 60. A pair from different item groups can never rate above Low.")
    sub_lvl = st.radio("Show substitutes rated", list(SUB_LEVELS.keys()), index=1, horizontal=True, key="inv_sub_level")
    min_score = SUB_LEVELS[sub_lvl]
    pool = sub_pool()
    act = pool[(pool['Item Status'] == 20) & (pool['Sub Key'] != '')]
    act_scope = act[act['Custom Category'].isin(slicer_category)] if slicer_category else act
    gsize = act.groupby('Sub Key')['Item Number'].transform('size')
    act = act[gsize >= 2]
    act_scope = act_scope[act_scope['Sub Key'].isin(act['Sub Key'])]

    oos = act_scope[(act_scope['Avail_Cases'] <= 0) & (act_scope['3M_Avg_Depletion_Demand'] > 0)]
    sibs = act[act['Avail_Cases'] > 0]
    pr = find_substitutes(oos, sibs, min_score)
    best = best_substitute(pr)
    if not best.empty:
        so = best.merge(oos[['Item Number', 'Custom Category', 'Responsible Buyer']], on='Item Number', how='left')
        so['Flag'] = np.where(so['3M_Avg_Depletion_Demand (Sub)'] <= 0, 'Sub in stock but NOT being sold', 'Sub selling')
        so = so[['Item Number', 'Item Description', 'Custom Category', 'Target Demand', 'Item Number (Sub)', 'Item Description (Sub)', 'Match Confidence',
                 'Match Score', 'Why Matched', 'Avail_Cases (Sub)', '# Subs', '3M_Avg_Depletion_Demand (Sub)', 'Flag', 'Responsible Buyer']]
        so = so.rename(columns={'Item Number': 'Out-of-Stock Item', 'Item Description': 'Out-of-Stock Description', 'Target Demand': 'Out-of-Stock Item Demand (Cs/mo)',
                                'Item Number (Sub)': 'Best Sub Item', 'Item Description (Sub)': 'Best Sub Description', 'Avail_Cases (Sub)': 'Best Sub MDC Stock (Cs)',
                                '# Subs': '# Subs In Stock', '3M_Avg_Depletion_Demand (Sub)': 'Sub Sold Last 3M (avg Cs/mo)'})
    else:
        so = pd.DataFrame()

    idle_i = act_scope[(act_scope['Avail_Cases'] > 0) & (act_scope['3M_Avg_Depletion_Demand'] <= 0)]
    selling = act[act['3M_Avg_Depletion_Demand'] > 0]
    pr2 = find_substitutes(idle_i, selling, min_score)
    if not pr2.empty:
        ag2 = pr2.groupby('Item Number').agg(**{'Look-alike Demand (Cs/mo)': ('3M_Avg_Depletion_Demand (Sub)', 'sum'), '# Selling Look-alikes': ('Item Number (Sub)', 'size')}).reset_index()
        b2 = best_substitute(pr2)[['Item Number', 'Item Number (Sub)', 'Item Description (Sub)', 'Match Confidence', 'Match Score']].rename(
            columns={'Item Number (Sub)': 'Best Selling Look-alike', 'Item Description (Sub)': 'Look-alike Description'})
        idle_tab = idle_i.merge(ag2, on='Item Number').merge(b2, on='Item Number')
        idle_tab = idle_tab[['Item Number', 'Item Description', 'Custom Category', 'Avail_Cases', 'Inventory Value', 'Look-alike Demand (Cs/mo)', '# Selling Look-alikes',
                             'Best Selling Look-alike', 'Look-alike Description', 'Match Confidence', 'Match Score', 'Responsible Buyer']].rename(
            columns={'Avail_Cases': 'MDC Stock (Cs)'}).sort_values('Inventory Value', ascending=False)
    else:
        idle_tab = pd.DataFrame(columns=['Inventory Value'])

    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Substitute groups (2+ active items)", f"{act['Sub Key'].nunique():,}")
    q2.metric("Out-of-stock selling items with a sub in stock", f"{so['Out-of-Stock Item'].nunique() if not so.empty else 0:,}")
    q3.metric("…where the sub is NOT being sold", f"{(so['Flag'] != 'Sub selling').sum() if not so.empty else 0:,}")
    q4.metric("Idle in-stock items (look-alike is selling)", f"{len(idle_tab):,}", f"${idle_tab['Inventory Value'].sum():,.0f} on hand", delta_color="off")

    if not so.empty:
        st.markdown("**Out-of-stock items that have an active substitute in stock** - flag to Sales")
        qa, qb = st.columns([3, 2])
        with qa:
            cf_ = so.groupby(['Custom Category', 'Flag']).size().reset_index(name='Items')
            fig = px.bar(cf_, x='Custom Category', y='Items', color='Flag', text='Items',
                         color_discrete_map={'Sub in stock but NOT being sold': '#d62728', 'Sub selling': '#2ca02c'})
            fig.update_layout(xaxis_tickangle=-45)
            show_chart(fig, key="t3_sub_flag")
        with qb:
            mc_ = so.groupby('Match Confidence').size().reset_index(name='Items')
            fig = px.bar(mc_, x='Match Confidence', y='Items', text='Items', color='Match Confidence',
                         color_discrete_map={'High': '#2ca02c', 'Medium': '#f2c94c', 'Low': '#d62728'},
                         category_orders={'Match Confidence': ['High', 'Medium', 'Low']}, title="How sure are the matches?")
            fig.update_layout(showlegend=False)
            show_chart(fig, key="t3_sub_conf")
        show_table(so.sort_values(['Flag', 'Match Score', 'Out-of-Stock Item Demand (Cs/mo)'], ascending=[False, False, False]).pipe(
            style0, one_dec=['Out-of-Stock Item Demand (Cs/mo)', 'Sub Sold Last 3M (avg Cs/mo)']), height=350)
        csv_button(so, "⬇️ Export sub-available list for Sales", "substitute_opportunities.csv", "dl_sub_so")
    else:
        st.info("No out-of-stock selling items with an in-stock substitute at this match level.")
    if not idle_tab.empty:
        st.markdown("**In-stock items with no recent sales while a look-alike is selling**")
        show_table(style0(idle_tab, money=['Inventory Value'], one_dec=['Look-alike Demand (Cs/mo)']), height=300)
        csv_button(idle_tab, "⬇️ Export idle-substitute list for Sales", "idle_substitutes.csv", "dl_sub_idle")
# ==========================================================================
# TAB 4 — BID PERFORMANCE
# ==========================================================================
with tabs[3], guard():
    st.header("🏆 Bid Customer Fulfillment & Coverage")
    if BID_ENGINE_ERROR:
        st.warning("The bid items could not be worked out: " + BID_ENGINE_ERROR + ". The other tabs are not affected.")
    elif BID_CI.empty:
        st.info("Bid data not loaded. " + BID_NOTE)
    else:
        cap("Bid customers and items come from the Bids file (BIDS.xlsx): every CPJ Code listed for a customer, counted once per customer. The headline total is the number of different CPJ Codes on the file, broken out by customer below. "
            f"Stock = MDC + SEP + SEZ (OM is never counted); an item is 'in stock' with more than {bid_stock_min:g} cases. "
            "An item the item master shows as discontinued (status 50 or 80) is rated on its direct / strong replacement: when the replacement is in stock the item counts as MDC stocked, as usual. With no strong replacement it is 'Discontinued no Replacement' and is left out of every calculation on this tab (it is still listed, with its stock, further down). "
            "The sidebar's 'Bid analysis settings' change the item source, the stock threshold and how strict the substitutes are.", label="ℹ️ How bid items are counted (click to open)")
        cap(BID_NOTE)
        cust_ct = BID_CI.groupby('Item Code')['Customer'].nunique().rename('Customer_Count')
        uni = BID_CLS.merge(cust_ct, left_on='Item Code', right_index=True, how='left')
        base = uni[uni['Bucket'].isin(BID_BUCKETS)].copy()                       # rated items (the calculation)
        base['Category'] = base['Custom Category']
        base['Description'] = base['Item Description']
        base['Avail_Cases'] = base['Bid Stock (Cs)'].fillna(0.0)
        base['Cases At Port'] = base['Cases At Port'].fillna(0.0)
        base['Cases On Order'] = base['Bid On Order (Cs)'].fillna(0.0)
        base['3M_Avg_Depletion_Demand'] = base['3M_Avg_Depletion_Demand'].fillna(0.0)
        base['Has Sub'] = base['Stocked Sub Item'].notna()
        own_ok = base['Avail_Cases'] > bid_stock_min
        # the bid code on its own
        base['Own Availability'] = np.select([own_ok, base['Cases At Port'] > 0, base['Cases On Order'] > 0],
                                             ['Available', 'Inventory @ Port', 'On Order'], default='Not In Stock')
        # with substitutes (the same buckets as the Bid Product Availability tab)
        base['Availability'] = np.select([base['Basis'] == 'Own stock', base['Basis'] == 'Substitute stock', base['Cases At Port'] > 0, base['Bucket'] == B_ORDER],
                                         ['Available', 'Available via Substitute', 'Inventory @ Port', 'On Order'], default='Not In Stock')
        base['Months of Stock'] = np.where(base['3M_Avg_Depletion_Demand'] > 0, base['Avail_Cases'] / base['3M_Avg_Depletion_Demand'].replace(0, 1), np.nan)
        colors = {'Available': '#2ca02c', 'Available via Substitute': '#17becf', 'Inventory @ Port': '#ff7f0e', 'On Order': '#f2c94c', 'Not In Stock': '#d62728'}

        # ---- the headline: different CPJ Codes on the Bids file, broken out by customer, and how many of them are in the calculation
        n_uni = len(uni)
        n_rated = len(base)
        n_dleft = int(uni['Disc Left Out'].fillna(False).astype(bool).sum())
        n_other = n_uni - n_rated - n_dleft
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Unique bid items (Bids file)", f"{n_uni:,}")
        h2.metric("In the calculation", f"{n_rated:,}")
        h3.metric("Discontinued no Replacement (left out)", f"{n_dleft:,}")
        h4.metric("Code not usable (left out)", f"{n_other:,}")
        hc = BID_CI.groupby('Customer').agg(**{'Bid items': ('Item Code', 'nunique')})
        hc['In the calculation'] = BID_CI[BID_CI['Bucket'].isin(BID_BUCKETS)].groupby('Customer')['Item Code'].nunique().reindex(hc.index).fillna(0).astype(int)
        hc['Discontinued no Replacement (left out)'] = BID_CI[BID_CI['Disc Left Out'].fillna(False).astype(bool)].groupby('Customer')['Item Code'].nunique().reindex(hc.index).fillna(0).astype(int)
        hc['Code not usable (left out)'] = hc['Bid items'] - hc['In the calculation'] - hc['Discontinued no Replacement (left out)']
        hc = hc.sort_values('Bid items', ascending=False)
        hc.loc['All customers (different items, each counted once)'] = [n_uni, n_rated, n_dleft, n_other]
        show_table(style0(hc.reset_index().rename(columns={'index': 'Customer'}), one_dec=[]))
        cap(f"{n_uni:,} different CPJ Codes on the Bids file = {n_rated:,} in the calculation + {n_dleft:,} 'Discontinued no Replacement' (status 50 / 80, no strong replacement) + {n_other:,} 'Code not usable'. "
            "An item that is on the list of several customers counts once in the total and once for each customer, so the customer rows add up to more than the total. "
            "Every number on this tab below this table is for the items 'in the calculation'.")
        csv_button(uni[['Item Code', 'Item Description', 'Item Status', 'Bucket', 'How', 'Customer_Count']].rename(columns={'Customer_Count': '# Bid Customers', 'Bucket': 'Result'}),
                   "⬇️ Export all unique bid items", "unique_bid_items.csv", "dl_uniq")

        st.subheader("Bid Item Stock Coverage by Category")
        cov = base.groupby('Category').agg(Items=('Item Code', 'nunique'), MDC=('Avail_Cases', 'sum'), Port=('Cases At Port', 'sum'),
                                           Ord=('Cases On Order', 'sum'), Months=('Months of Stock', 'mean')).reset_index().sort_values('MDC', ascending=False)
        cov.columns = ['Category', 'Items', 'MDC + SEP + SEZ Stock (Cases)', 'Cases at Port', 'Cases On Order (PO 20-40)', 'Avg Months of Stock']
        show_table(style0(cov, one_dec=['Avg Months of Stock']))

        pa, pb = st.columns(2)
        with pa:
            st.subheader("Overall Bid Item Availability")
            cap("Current conditions - the bid code itself must have stock, or stock incoming. Items left out of the calculation are not shown.")
            fig = px.pie(base, names='Own Availability', color='Own Availability', color_discrete_map=colors, title=f"{len(base):,} bid items in the calculation")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            show_chart(fig, key="t4_pie0")
        with pb:
            st.subheader("Availability incl. Substitutes / Replacements")
            cap(f"Adds active (status 20) substitutes rated '{bid_sub_pick}' that are in stock. A discontinued item (status 50 / 80) is rated on its strong replacement (in stock = MDC stocked); with none it is 'Discontinued no Replacement' and left out.")
            fig = px.pie(base, names='Availability', color='Availability', color_discrete_map=colors, title=f"{len(base):,} bid items in the calculation")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            show_chart(fig, key="t4_pie1")
        m_a, m_b, m_c = st.columns(3)
        av0 = (base['Own Availability'] == 'Available').mean() * 100 if len(base) else 0
        av1 = base['Availability'].isin(['Available', 'Available via Substitute']).mean() * 100 if len(base) else 0
        m_a.metric("Item availability - bid codes only", f"{av0:,.0f}%", f"of {len(base):,} items in the calculation", delta_color="off")
        m_b.metric("Item availability - incl. substitutes", f"{av1:,.0f}%", f"{av1 - av0:+,.0f} pts", delta_color="normal")
        m_c.metric("Bid items rescued by a substitute", f"{(base['Availability'] == 'Available via Substitute').sum():,}")
        if (base['Availability'] == 'Available via Substitute').any():
            resc = base.loc[base['Availability'] == 'Available via Substitute', 'Sub Confidence'].value_counts()
            cap("Confidence in those substitutes: " + ", ".join(f"{k} {v:,}" for k, v in resc.items()) + ". Change 'Substitutes counted' in the sidebar to be stricter or broader.")

        st.subheader("Stock vs. Demand")
        cat_f = st.selectbox("Category focus", ['All'] + sorted(base['Category'].dropna().unique()))
        sc_src = base if cat_f == 'All' else base[base['Category'] == cat_f]
        pts = sc_src[(sc_src['Avail_Cases'] > 0) & (sc_src['3M_Avg_Depletion_Demand'] > 0)]
        if not pts.empty:
            fig = px.scatter(pts, x='3M_Avg_Depletion_Demand', y='Avail_Cases', color='Availability', size='Customer_Count',
                             hover_name='Description', hover_data={'Item Code': True, 'Category': True, 'Months of Stock': ':.1f', 'Customer_Count': True},
                             log_x=True, log_y=True, color_discrete_map=colors,
                             labels={'3M_Avg_Depletion_Demand': 'Avg monthly demand (cases)', 'Avail_Cases': 'MDC + SEP + SEZ stock (cases)'},
                             title="Each dot = a bid item. Below the red line = under 1 month of stock")
            xr = np.array([pts['3M_Avg_Depletion_Demand'].min(), pts['3M_Avg_Depletion_Demand'].max()])
            for mth, cl in [(0.5, '#d62728'), (1, '#ff7f0e'), (3, '#2ca02c')]:
                fig.add_trace(go.Scatter(x=xr, y=xr * mth, mode='lines', name=f'{mth:g} mo cover', line=dict(dash='dash', color=cl, width=1)))
            fig.update_layout(height=520)
            show_chart(fig, key="t4_scatter")
            cap(f"{len(sc_src) - len(pts):,} items with zero stock or zero demand are not plotted (log scale) - see the at-risk table below.")
        else:
            st.info("No items with both stock and demand.")

        st.markdown("**Months of cover for the bid items, by category**")
        cap("Same definition as the Inventory Health tab: active bid items that are selling, warehouse stock (MDC + SEP + MJM + Bonded) without Stores, all in cases.")
        ctb = cover_table(inv[inv['Item Number'].isin(base['Item Code'])])
        cover_charts(ctb, "t4")

        st.subheader("⚠️ At-Risk Bid Items - No Stock, Not On Order, Not At Port")
        risk = base[(base['Avail_Cases'] <= bid_stock_min) & (base['Cases On Order'] <= 0) & (base['Cases At Port'] <= 0)].copy()
        risk['Substitute Cover'] = np.where(risk['Has Sub'],
                                            'Yes - ' + risk['Stocked Sub Item'].fillna('').astype(str) + ' ' + risk['Stocked Sub Description'].fillna('').astype(str).str.slice(0, 40)
                                            + ' (' + risk['Stocked Sub (Cs)'].fillna(0).map(lambda v: f"{v:,.0f}") + ' cs, ' + risk['Stocked Sub Confidence'].fillna('').astype(str) + ' match)',
                                            'No suitable substitute in stock')
        rk = risk[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Has Sub', 'Substitute Cover', 'Stocked Sub Item', 'Stocked Sub (Cs)',
                   'Stocked Sub Confidence']].rename(columns={'Customer_Count': '# Bid Customers', 'Has Sub': 'Substitute Has Suitable Inventory',
                                                              'Stocked Sub Item': 'Sub Item', 'Stocked Sub (Cs)': 'Sub Stock (Cs)', 'Stocked Sub Confidence': 'Sub Match'})
        show_table(style0(rk, one_dec=['Sub Stock (Cs)']))
        cap(f"{len(risk):,} of {len(base):,} bid items in the calculation are at risk; {int(risk['Has Sub'].sum()):,} of them have a substitute with suitable inventory (rated '{bid_sub_pick}', more than {bid_stock_min:g} cases at MDC / SEP / SEZ).")
        csv_button(rk, "⬇️ Export at-risk bid items", "at_risk_bid_items.csv", "dl_risk")

        st.subheader("Bid Items on Discontinued Codes (status 50 / 80)")
        disc = uni[uni['Discontinued'].fillna(False).astype(bool)].copy()
        dtab = pd.DataFrame({'Item Code': disc['Item Code'], 'Description': disc['Item Description'], 'Status': disc['Item Status'].astype('Int64'),
                             'Category': disc['Custom Category'], '# Bid Customers': disc['Customer_Count'], 'Buyer': disc['Responsible Buyer'],
                             'In the Calculation?': np.where(disc['Disc Left Out'].fillna(False).astype(bool), 'No - Discontinued no Replacement (left out)', 'Yes - rated on its strong substitute'),
                             'Own Stock (Cs)': disc['Bid Stock (Cs)'], 'Result': disc['Bucket'], 'Recorded As': disc['How'],
                             'Strong Substitute': disc['Sub Item'].fillna('None found'), 'Substitute Description': disc['Sub Description'],
                             'Substitute Stock (Cs)': disc['Sub Stock (Cs)'], 'Substitute Match': disc['Sub Confidence']}).sort_values(['In the Calculation?', 'Item Code'])
        show_table(style0(dtab, one_dec=['Own Stock (Cs)', 'Substitute Stock (Cs)']))
        cap(f"{len(disc):,} different bid items are discontinued in the item master: {int(disc['Disc Rated'].fillna(False).astype(bool).sum()):,} have a direct / strong substitute (rated on it) and {n_dleft:,} do not (left out of the calculation). "
            "Status 50 = still sold, no longer purchased. Status 80 = no selling, no buying (not calculated). The status comes from the item master, not from the status typed on the bid sheet. "
            "'Own Stock' is shown so you can see what is left out - an item left out may still have stock on hand.")
        csv_button(dtab, "⬇️ Export discontinued bid items", "discontinued_bid_items.csv", "dl_disc")

        with st.expander("All Bid Item Coverage (detail)"):
            det = base[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Avail_Cases', 'Cases At Port', 'Cases On Order',
                        'Months of Stock', 'Availability']].rename(columns={'Customer_Count': '# Bid Customers', 'Avail_Cases': 'MDC + SEP + SEZ Stock (Cs)'})
            show_table(style0(det, one_dec=['Months of Stock']))

# ==========================================================================
# TAB 11 — BID PRODUCT AVAILABILITY  (shown as the 5th tab)
# For every bid customer: which items are MDC stocked, which are out of stock but on order, and which are out of stock with no order.
# ==========================================================================
def bid_display(df, full=False):
    """The columns a buyer needs, in plain words. full=True adds the customer and the availability group (for the one-file export)."""
    sub = (df['Sub Item'].fillna('').astype(str) + ' ' + df['Sub Description'].fillna('').astype(str)).str.strip()
    out = pd.DataFrame({
        'Item': df['Item Code'].values, 'Description': df['Item Description'].values, 'Category': df['Custom Category'].values,
        'Item Status': df['Item Status'].fillna(0).astype(int).values,
        'MDC + SEP + SEZ Stock (Cs)': df['Bid Stock (Cs)'].fillna(0.0).values, 'On Order, PO status 20-40 (Cs)': df['Bid On Order (Cs)'].fillna(0.0).values,
        'Next Arrival': df['Next Arrival'].dt.strftime('%Y-%m-%d').fillna('').values,
        'Covered By': df['How'].values, 'Substitute': sub.values, 'Sub Match': df['Sub Confidence'].fillna('').values,
        'Sub Stock (Cs)': df['Sub Stock (Cs)'].fillna(0.0).values, 'Buyer': df['Responsible Buyer'].fillna('').values, 'Bid': df['Bid Name'].values})
    if full:
        out.insert(0, 'Customer', df['Customer'].values)
        out.insert(3, 'Availability', df['Bucket'].values)
        return out.sort_values(['Customer', 'Availability', 'Category', 'Description'])
    return out.sort_values(['Category', 'Description'])


BID_FONT = 13                                             # one font size for every label on the bid charts: numbers, axes, legend
BID_LABEL_COLORS = {B_STOCK: ('white', '#1b6e1b'), B_ORDER: ('#3d3100', '#8a6d00'), B_NONE: ('white', '#b01e1e'), B_DISC: ('white', '#4d4d4d'), B_OUT: ('#222222', '#4d4d4d')}


def bid_bar(counts, title, key, horizontal=False, pct=False, height=600):
    """The chart from the weekly slide: green = MDC stocked, yellow = out of stock but on order, red = out of stock with no order.
    Two grey bars hold the items left out of the calculation ('Discontinued no Replacement', 'Code not usable'), so every customer's bar adds up to its total on the Bids file.
    Every number is printed in the same size. A segment tall enough holds its number inside; a thin segment gets its number just right of the bar, in the segment's colour,
    so no value is ever hidden."""
    fig = go.Figure()
    cats = list(counts.index)
    names = [nm for nm in BID_ALL_BUCKETS if nm in counts.columns and float(counts[nm].sum())]
    tot = counts[names].sum(axis=1) if names else pd.Series([0.0] * len(cats))
    ymax = float(tot.max()) if len(tot) and float(tot.max()) > 0 else 1.0
    plot_px = max(height - 170, 200)                      # about how many pixels the bars have
    per_px = ymax * 1.08 / plot_px                        # items per pixel
    fmt = (lambda v: f"{v:,.0f}%") if pct else (lambda v: f"{int(round(v)):,}")
    min_px = BID_FONT + 4                                 # a segment shorter than this cannot hold a number
    bar_w = 0.7 if len(cats) > 1 else 0.35                # bar width in category units, so the side labels sit just outside the bar at any screen width
    for nm in names:
        vals = counts[nm]
        txt_in = [fmt(v) if v > 0 and v / per_px >= min_px else "" for v in vals]
        fig.add_trace(go.Bar(x=cats, y=vals, name=nm, marker_color=BID_BUCKET_COLORS[nm], text=txt_in, textposition='inside', insidetextanchor='middle',
                             width=bar_w, textangle=0, constraintext='none', textfont=dict(size=BID_FONT, color=BID_LABEL_COLORS[nm][0]),
                             hovertemplate="%{x}<br>" + nm + ": %{y:,.0f}" + ("%" if pct else "") + "<extra></extra>"))
    # thin segments: the number goes beside the bar, nudged up where two thin segments sit on top of each other
    gap = (BID_FONT + 3) * per_px
    for ci_, c in enumerate(cats):
        base, last_y = 0.0, None
        for nm in names:
            v = float(counts.loc[c, nm])
            if v > 0 and v / per_px < min_px:
                y = base + v / 2
                if last_y is not None and y - last_y < gap:
                    y = last_y + gap
                last_y = y
                fig.add_annotation(x=ci_ + bar_w / 2 + 0.03, y=y, text=fmt(v), showarrow=False, xanchor='left', font=dict(size=BID_FONT, color=BID_LABEL_COLORS[nm][1]))
            base += v
    fig.update_layout(barmode='stack', title=dict(text=title, font=dict(size=BID_FONT + 3)), yaxis_title="Bid items", height=height,
                      font=dict(size=BID_FONT), uniformtext=dict(minsize=BID_FONT, mode='show'),
                      legend=dict(orientation='h', yanchor='top', y=-0.12, xanchor='left', x=0, font=dict(size=BID_FONT)),
                      margin=dict(l=60, r=30, t=70, b=90))
    fig.update_xaxes(tickfont=dict(size=BID_FONT), range=[-0.5, len(cats) - 0.5 + 0.35])
    fig.update_yaxes(tickfont=dict(size=BID_FONT), title_font=dict(size=BID_FONT))
    show_chart(fig, key=key)


with tabs[10], guard():
    st.header("📦 Bid Product Availability — by customer")
    cap("For each bid customer this shows which items are in stock at MDC, which are out of stock but on order, and which are out of stock with no order. "
        "Rules: the customers and items come from the Bids file (BIDS.xlsx) - every CPJ Code listed for a customer, counted once, so a customer's total is always the number of different items the file lists for it. "
        f"MDC stocked = more than {bid_stock_min:g} cases at MDC, SEP or SEZ (OM is never counted; KG items are converted to cases). "
        "An item the item master shows as discontinued (status 50 or 80) is rated on its direct / strong substitute: when that replacement is in stock the item counts as MDC stocked, as usual (on order = yellow, neither = red). When it has no strong replacement it is shown as 'Discontinued no Replacement' (grey) and left out of the calculation - still listed, with its stock, so nothing is hidden. "
        "A close substitute that is stocked also counts a status 20 item as stocked. Out of stock on order = the item (or a close substitute) is on a PO with status 20-40. "
        "Out of stock with no order = nothing in stock and nothing on order for the item or any close substitute. "
        "A code that cannot be rated (not in the Purchase Report, status 0-14 or 86-99) is also left out and shown as a light grey 'Code not usable' item. Fill rates are worked out on the items in the calculation only (the green, yellow and red items).",
        label="ℹ️ How the items are sorted (click to open)")
    if BID_ENGINE_ERROR:
        st.warning("The bid availability could not be worked out: " + BID_ENGINE_ERROR + ". The other tabs are not affected.")
    elif BID_CI.empty:
        st.info("No bid items to show. " + BID_NOTE)
    else:
        cap(BID_NOTE + f" Substitutes counted: {bid_sub_pick}. Change the item source, stock threshold or substitute strength in the sidebar under 'Bid analysis settings'.")
        cats = sorted(BID_CI['Custom Category'].dropna().astype(str).unique())
        a1, a2 = st.columns([3, 2])
        with a1:
            cat_pick = st.multiselect("Category (applies to every chart and list on this tab)", cats, key="ba_cat")
        with a2:
            cust_all = sorted(BID_CI['Customer'].unique())
            cust_pick = st.multiselect("Customers", cust_all, default=cust_all, key="ba_cust")
        ci = BID_CI[BID_CI['Customer'].isin(cust_pick)]
        if cat_pick:
            ci = ci[ci['Custom Category'].isin(cat_pick)]
        counted = ci[ci['Bucket'].isin(BID_BUCKETS)]
        out_ = ci[ci['Bucket'].isin(BID_LEFT_OUT)]
        if ci.empty:
            st.info("No bid items for these choices.")
        else:
            cnt = ci.groupby(['Customer', 'Bucket'])['Item Code'].nunique().unstack(fill_value=0)
            for nm in BID_ALL_BUCKETS:
                if nm not in cnt.columns:
                    cnt[nm] = 0
            cnt = cnt[BID_ALL_BUCKETS]
            cnt['Total on Bids file'] = cnt.sum(axis=1)
            cnt['In the calculation'] = cnt[BID_BUCKETS].sum(axis=1)
            cnt['Discontinued (status 50 / 80)'] = ci[ci['Discontinued'].fillna(False).astype(bool)].groupby('Customer')['Item Code'].nunique().reindex(cnt.index).fillna(0).astype(int)
            cnt = cnt.sort_values('Total on Bids file', ascending=False)
            u_all = ci.drop_duplicates('Item Code')
            tot_u = len(u_all)
            rated_u = int(u_all['Bucket'].isin(BID_BUCKETS).sum())
            n_st = int((u_all['Bucket'] == B_STOCK).sum())
            fr = n_st / max(rated_u, 1) * 100
            fr2 = u_all['Bucket'].isin([B_STOCK, B_ORDER]).sum() / max(rated_u, 1) * 100
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Customers", f"{len(cnt):,}")
            m2.metric("Unique bid items", f"{tot_u:,}", f"{rated_u:,} in the calculation, {tot_u - rated_u:,} left out", delta_color="off")
            m3.metric("MDC stocked", f"{fr:,.0f}%", f"{n_st:,} of {rated_u:,} items in the calculation", delta_color="off")
            m4.metric("Out of stock, on order", f"{(u_all['Bucket'] == B_ORDER).sum():,}", f"fill rate with incoming supply {fr2:,.0f}%", delta_color="off")
            m5.metric("Out of stock, no order", f"{(u_all['Bucket'] == B_NONE).sum():,}", "needs a buyer decision", delta_color="inverse")

            st.subheader("All customers")
            view_pct = st.checkbox("Show as % of each customer's items", value=False, key="ba_pct")
            if view_pct:
                shown = cnt[BID_ALL_BUCKETS].div(cnt['Total on Bids file'].replace(0, 1), axis=0) * 100
                bid_bar(shown.round(1), "Bid items by availability (% of each customer's items on the Bids file)", "ba_all", pct=True)
            else:
                bid_bar(cnt[BID_ALL_BUCKETS], "Bid inventory availability by customer (each bar = the customer's items on the Bids file)", "ba_all")
            sumt = cnt.reset_index()
            sumt['Fill Rate %'] = sumt[B_STOCK] / sumt['In the calculation'].replace(0, 1) * 100
            sumt['Fill Rate with Incoming Supply %'] = (sumt[B_STOCK] + sumt[B_ORDER]) / sumt['In the calculation'].replace(0, 1) * 100
            tot_row = {'Customer': 'All customers - different items (each counted once)', B_STOCK: int((u_all['Bucket'] == B_STOCK).sum()), B_ORDER: int((u_all['Bucket'] == B_ORDER).sum()),
                       B_NONE: int((u_all['Bucket'] == B_NONE).sum()), B_DISC: int((u_all['Bucket'] == B_DISC).sum()), B_OUT: int((u_all['Bucket'] == B_OUT).sum()), 'Total on Bids file': tot_u, 'In the calculation': rated_u,
                       'Discontinued (status 50 / 80)': int(u_all['Discontinued'].fillna(False).astype(bool).sum()),
                       'Fill Rate %': fr, 'Fill Rate with Incoming Supply %': fr2}
            sumt = pd.concat([sumt, pd.DataFrame([tot_row])], ignore_index=True)
            show_table(style0(sumt, pct=['Fill Rate %', 'Fill Rate with Incoming Supply %']))
            cap(f"The last row is the headline: {tot_u:,} different CPJ Codes on the Bids file for the customers chosen. An item that several customers list counts once in that row and once for each customer, so the customer rows add up to more than the total. "
                "Fill rate = MDC stocked items as a share of the items in the calculation (MDC stocked + on order + no order). "
                "Stocked + on order + no order + discontinued no replacement + code not usable always equals the total on the Bids file.")
            csv_button(sumt, "⬇️ Export the customer summary", "bid_availability_summary.csv", "dl_ba_sum")
            csv_button(u_all[['Item Code', 'Item Description', 'Item Status', 'Bucket', 'How']].rename(columns={'Bucket': 'Result'}).sort_values('Item Code'),
                       "⬇️ Export the list of unique bid items", "unique_bid_items.csv", "dl_ba_uniq")

            # ---------------- one block per customer, laid out like the weekly slide
            st.subheader("Each customer: chart and item lists")
            cap("Open a customer to see its chart (like the weekly slide) and the out-of-stock items beside it, then the full item lists for MDC stocked, on order, no order and codes that need fixing.",
                label="ℹ️ About this list (click to open)")
            for i_, cust in enumerate(cnt.index):
                cc = ci[ci['Customer'] == cust]
                n_s, n_o, n_n, n_d, n_x = (int(cnt.loc[cust, b_]) for b_ in BID_ALL_BUCKETS)
                tot_c = int(cnt.loc[cust, 'Total on Bids file'])
                rated_c = n_s + n_o + n_n
                title_ = (f"{cust}  —  {tot_c:,} items: {n_s:,} MDC stocked · {n_o:,} on order · {n_n:,} no order"
                          + (f" · {n_d:,} discontinued, no replacement" if n_d else "") + (f" · {n_x:,} code not usable" if n_x else ""))
                with st.expander(title_, expanded=(i_ == 0)):
                    L, R = st.columns([3, 2])
                    with L:
                        one = cnt.loc[[cust], BID_ALL_BUCKETS]
                        bid_bar(one, f"Bid inventory availability - {cust}", f"ba_c_{i_}", height=460)
                        k1, k2 = st.columns(2)
                        k1.metric("Fill rate", f"{n_s / max(rated_c, 1) * 100:,.1f}%", f"of {rated_c:,} items in the calculation", delta_color="off")
                        k2.metric("Fill rate with incoming supply", f"{(n_s + n_o) / max(rated_c, 1) * 100:,.1f}%")
                    with R:
                        oos = cc[cc['Bucket'].isin([B_ORDER, B_NONE])].copy()
                        oos['Stage'] = np.where(oos['Bucket'] == B_NONE, 'No order', 'On order')
                        oos = oos.sort_values(['Stage', 'Custom Category', 'Item Description'], ascending=[False, True, True])
                        st.markdown(f"**{cust} out-of-stock items**")
                        if oos.empty:
                            st.success("Nothing is out of stock for this customer.")
                        else:
                            show_table(oos[['Item Code', 'Item Description', 'Stage']].rename(columns={'Item Code': 'Item', 'Item Description': 'Description'}), height=380)
                    ord_ = cc[cc['Bucket'] == B_ORDER]
                    if len(ord_):
                        wh = np.where(ord_['Cases At Port'].fillna(0) > 0, 'At port / yard', ord_['Next Arrival'].dt.strftime('%b %Y').fillna('No date'))
                        arr = pd.Series(wh).value_counts().rename_axis('Arriving').reset_index(name='Items')
                        cap("On-order items by where they are / when they are due: " + "; ".join(f"{r.Arriving}: {r.Items}" for r in arr.itertuples()))
                    t1, t2, t3, t4, t5 = st.tabs([f"🟢 MDC stocked ({n_s:,})", f"🟡 On order ({n_o:,})", f"🔴 No order ({n_n:,})", f"⚪ Discontinued no Replacement ({n_d:,})", f"⚪ Code not usable ({n_x:,})"])
                    for tb, bkt, nm_ in ((t1, B_STOCK, 'stocked'), (t2, B_ORDER, 'onorder'), (t3, B_NONE, 'noorder'), (t4, B_DISC, 'discnorepl'), (t5, B_OUT, 'notusable')):
                        with tb:
                            d_ = cc[cc['Bucket'] == bkt]
                            if d_.empty:
                                st.info("No items.")
                            else:
                                tab_ = bid_display(d_)
                                show_table(style0(tab_, one_dec=['MDC + SEP + SEZ Stock (Cs)', 'On Order, PO status 20-40 (Cs)', 'Sub Stock (Cs)']), height=320)
                                csv_button(tab_, f"⬇️ Export {cust} - {bkt}", f"bid_{cust}_{nm_}.csv".replace(' ', '_'), f"dl_ba_{i_}_{nm_}")

            # ---------------- everything in one file
            csv_button(bid_display(ci, full=True), "⬇️ Export every customer's items (one file)", "bid_availability_all_customers.csv", "dl_ba_all")

        # ---------------- items left out of the calculation
        st.divider()
        st.subheader("Bid items left out of the calculation")
        cap("Left out = (a) 'Discontinued no Replacement': discontinued in the item master (status 50 or 80) with no direct / strong replacement, or (b) 'Code not usable': a code that cannot be rated: not in the Purchase Report, or a status that is never calculated (0-14 or 86-99). "
            "They stay in each customer's total as grey items and are listed here with their stock so nothing is hidden. For group (b) a buyer should correct the code on the Bids file or have the item set up.")
        if out_.empty:
            st.info("Every bid item could be rated.")
        else:
            rec = out_.copy()
            rec_t = pd.DataFrame({'Customer': rec['Customer'], 'Item': rec['Item Code'], 'Description': rec['Item Description'], 'Category': rec['Custom Category'],
                                  'Item Status': rec['Item Status'].fillna(0).astype(int), 'Why Left Out': rec['How'],
                                  'MDC + SEP + SEZ Stock (Cs)': rec['Bid Stock (Cs)'].fillna(0.0), 'On Order, PO status 20-40 (Cs)': rec['Bid On Order (Cs)'].fillna(0.0),
                                  'Buyer': rec['Responsible Buyer'].fillna(''), 'Bid': rec['Bid Name']}).sort_values(['Customer', 'Why Left Out', 'Description'])
            rr1, rr2 = st.columns([2, 3])
            with rr1:
                why = rec_t.groupby('Why Left Out')['Item'].nunique().reset_index(name='Items').sort_values('Items', ascending=False)
                show_table(why)
            with rr2:
                cs_ = rec_t.groupby('Customer')['Item'].nunique().reset_index(name='Items left out').sort_values('Items left out', ascending=False)
                fig = px.bar(cs_, x='Customer', y='Items left out', text='Items left out', title="Items left out of the calculation, by customer")
                show_chart(fig, key="ba_notc")
            show_table(style0(rec_t, one_dec=['MDC + SEP + SEZ Stock (Cs)', 'On Order, PO status 20-40 (Cs)']), height=360)
            csv_button(rec_t, "⬇️ Export the items left out", "bid_items_left_out.csv", "dl_ba_notc")

# ==========================================================================
# TAB 5 — FORECAST
# ==========================================================================
with tabs[4], guard():
    st.header("6-Month Forecast")
    if fc_df.empty:
        st.info("Forecast data not loaded.")
    else:
        c6 = fc_meta['cols6']
        cap(f"Forecast window: {', '.join(c6)}. Near-term average = {', '.join(fc_meta['cols3'])}. Status 80 (hard-discontinued) and never-calculated items (status 0-14, 86-99) are excluded; status 50 (sell-only) stays in. "
                   "Forecast quantities are in cases and are shown unrounded, so slow items keep their fractions of a case.")
        q = st.text_input("🔍 Search Item Code or Description:", "")
        bad_codes = set(sales_df[sales_df['Item Status'].isin(HARD_DISC_STATUSES + DELETED)]['Item Number'])
        fd = fc_df[~fc_df['Item Code'].isin(bad_codes)].copy()
        if q:
            fd = fd[fd['Item Code'].str.contains(q, case=False, na=False, regex=False) | fd['Item Description'].astype(str).str.contains(q, case=False, na=False, regex=False)]
        hv = fd[fd['3M_Avg_Forecast_Cases'] >= 10].sort_values('3M_Avg_Forecast_Cases', ascending=False)
        if not hv.empty:
            agg6 = hv[c6].sum().reset_index()
            agg6.columns = ['Month', 'Forecasted Cases']
            fig = px.bar(agg6, x='Month', y='Forecasted Cases', text_auto=',.0f', title='Forecast by month (items averaging ≥ 10 cases)')
            fig.update_traces(marker_color='darkmagenta')
            category_axis(fig)
            show_chart(fig, key="t5_month")
            show_table(style0(hv[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases'] + c6].rename(columns={'3M_Avg_Forecast_Cases': 'Near-Term Avg (Cs)'}),
                              one_dec=['Near-Term Avg (Cs)'] + c6), height=350)
        else:
            st.info("No forecast items meeting the criteria (≥ 10 cases avg).")

        st.subheader("Category Forecast Coverage")
        cap("Warehouse stock (MDC+SEP+MJM+Bonded) and total inbound (on order + arrived, full open pipeline) ÷ near-term forecast average, by category. Meat plant, juice plant, operations, beverage-system and unclassified items are left out.")
        cv = fd.merge(inv[['Item Number', 'Custom Category', 'WH_Cases', 'Cases On Order', 'Cases At Port']], left_on='Item Code', right_on='Item Number', how='left')
        cv['Custom Category'] = cv['Custom Category'].fillna('UNCLASSIFIED')
        cv = cv[~cv['Custom Category'].isin(NO_SS_CATS)]            # no plants, operations, beverage systems or unclassified items
        for _c in ('WH_Cases', 'Cases On Order', 'Cases At Port'):
            cv[_c] = cv[_c].fillna(0.0)
        cs2 = cv.groupby('Custom Category').agg(Stock=('WH_Cases', 'sum'), Port=('Cases At Port', 'sum'), Ord=('Cases On Order', 'sum'),
                                                Fcst=('3M_Avg_Forecast_Cases', 'sum')).reset_index()
        cs2['Inbound'] = cs2['Port'] + cs2['Ord']
        cs2['Stock Coverage (Months)'] = np.where(cs2['Fcst'] > 0, cs2['Stock'] / cs2['Fcst'].replace(0, 1), 0)
        cs2['Coverage w/ Inbound (Months)'] = np.where(cs2['Fcst'] > 0, (cs2['Stock'] + cs2['Inbound']) / cs2['Fcst'].replace(0, 1), 0)
        target = st.slider("Target months of cover", 1, 6, 3)
        cs2 = cs2.sort_values('Coverage w/ Inbound (Months)')
        if not cs2.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(x=cs2['Custom Category'], y=cs2['Stock Coverage (Months)'], name='On-hand cover', marker_color='#1f77b4',
                                 text=cs2['Stock Coverage (Months)'].map(lambda v: f"{v:,.1f}"), textposition='outside'))
            fig.add_trace(go.Bar(x=cs2['Custom Category'], y=cs2['Coverage w/ Inbound (Months)'], name='On-hand + inbound cover', marker_color='#2ca02c',
                                 text=cs2['Coverage w/ Inbound (Months)'].map(lambda v: f"{v:,.1f}"), textposition='outside'))
            fig.add_hline(y=target, line_dash='dash', line_color='red', annotation_text=f"Target {target} mo")
            fig.update_layout(barmode='group', yaxis_title="Months of forecast covered", title="Stock coverage by category vs near-term forecast")
            show_chart(fig, key="t5_cover")
            out = cs2.rename(columns={'Custom Category': 'Category', 'Stock': 'Warehouse Stock (Cs)', 'Port': 'Cases Arrived', 'Ord': 'Cases On Order',
                                      'Fcst': 'Near-Term Forecast (Cs)'}).drop(columns='Inbound')
            show_table(style0(out, one_dec=['Stock Coverage (Months)', 'Coverage w/ Inbound (Months)']))
        else:
            st.info("No forecast items fall in the saleable categories.")

# ==========================================================================
# TAB 6 — SUPPLIER SCORECARD
# ==========================================================================
def prepare_fill(df):
    """Adds the per-line fill measures. Ordered and received are in the SAME unit on a line, so the ratio needs no conversion.
    A line can never score above 100% (an over-shipment is counted separately), and a dropped line (Hst 99) counts as nothing delivered."""
    d = df.copy()
    d['Dropped'] = d['Status Code'] == DELETED_PO
    rec = np.where(d['Dropped'], 0.0, d['Recd qty'])
    d['Fill Ratio'] = rec / d['Order qty']
    d['Fill Capped'] = d['Fill Ratio'].clip(lower=0.0, upper=1.0)
    d['Short'] = (d['Fill Ratio'] < 0.995) & ~d['Dropped']
    d['Over'] = d['Fill Ratio'] > 1.005
    d['Ord Cs'] = d['Order Qty (Cases)']
    rec_cs = np.where(d['Dropped'], 0.0, d['Recd Qty (Cases)'])
    d['Rec Cs'] = np.where(d['Ord Cs'].notna(), np.minimum(rec_cs, d['Ord Cs']), np.nan)       # received cases, capped at what was ordered
    d['Fill USD'] = d['Fill Capped'] * d['USD $']
    d['Short USD'] = np.where(d['Dropped'], d['USD $'], np.where(d['Short'], d['USD $'] * (1 - d['Fill Capped']), 0.0))
    d['Fill %'] = d['Fill Capped'] * 100
    return d


def fill_rate_pct(frame, basis):
    """Fill rate (0-100) from a table that already holds the summed pieces: Lines, Bad, USD, FillUSD, Ord, Rec."""
    if basis == "Order value ($)":
        return np.where(frame['USD'] > 0, frame['FillUSD'] / frame['USD'].where(frame['USD'] > 0, 1.0) * 100, np.nan)
    if basis == "Cases":
        return np.where(frame['Ord'] > 0, frame['Rec'] / frame['Ord'].where(frame['Ord'] > 0, 1.0) * 100, np.nan)
    return np.where(frame['Lines'] > 0, (1 - frame['Bad'] / frame['Lines'].where(frame['Lines'] > 0, 1)) * 100, np.nan)


def fill_pieces(d, by):
    """Group fill lines and sum the pieces used by fill_rate_pct."""
    return d.groupby(by).agg(Lines=('PO no', 'size'), Dropped=('Dropped', 'sum'), Short=('Short', 'sum'), Over=('Over', 'sum'),
                             USD=('USD $', 'sum'), FillUSD=('Fill USD', 'sum'), Ord=('Ord Cs', 'sum'), Rec=('Rec Cs', 'sum'),
                             ShortUSD=('Short USD', 'sum')).reset_index().assign(Bad=lambda x: x['Dropped'] + x['Short'])


def completed_fill_lines(df, live_po_map):
    """Lines that have a result: received/closed (Hst 75-85), or dropped (Hst 99) on a PO that still has live or closed lines."""
    m = ((df['Status Code'].between(*CLOSED_PO)) & (df['Order qty'] > 0)) | \
        ((df['Status Code'] == DELETED_PO) & df['PO no'].map(live_po_map).fillna(False).astype(bool) & (df['Order qty'] > 0))
    return df[m]


with tabs[5], guard():
    st.header("Supplier Scorecard: Reliability, Fill & Timeliness")
    cap("Fill outcome per line: received/closed lines (Hst 75-85) use received ÷ ordered, never more than 100% per line (extra quantity is counted as an over-shipment, not credit). "
               "Lines dropped to Hst 99 on a PO that still has live or closed lines (vendor didn't deliver, line was killed) count as 0% filled.")
    fill_basis = st.radio("Measure fill rate by", ["Order value ($)", "Cases", "Lines fully filled"], horizontal=True, key="fill_basis",
                          help="Order value weights each line by its dollars (best for comparing vendors). Cases and lines are the alternatives. "
                               "Case counts can be dominated by packaging bought by the thousand, so value is the default.")
    sc_all = by_supplier_cat(po_all)
    live_po = sc_all.groupby('PO no')['Status Code'].apply(lambda s: s.between(*PO_LIVE).any())
    fl = prepare_fill(completed_fill_lines(sc_all, live_po))
    if slicer_year: fl = fl[fl['Order Year'].isin(slicer_year)]
    if slicer_month: fl = fl[fl['Order Month'].isin(slicer_month)]

    if fl.empty:
        st.info("No completed / dropped lines for the selected filters.")
    else:
        tot = fill_pieces(fl.assign(_all=1), '_all')
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric(f"Overall fill rate ({fill_basis.split(' (')[0].lower()})", f"{float(fill_rate_pct(tot, fill_basis)[0]):,.1f}%")
        k2.metric("Lines dropped to 99 (vendor didn't deliver)", f"{int(fl['Dropped'].sum()):,}", f"${fl.loc[fl['Dropped'], 'USD $'].sum():,.0f}", delta_color="off")
        k3.metric("Lines short-shipped", f"{int(fl['Short'].sum()):,}")
        k4.metric("Lines over-shipped", f"{int(fl['Over'].sum()):,}", "not counted as extra credit", delta_color="off")
        k5.metric("Short $ (dropped + short)", f"${fl['Short USD'].sum():,.0f}")

        st.subheader("Where Vendors Fail to Deliver — Dropped & Short Lines by Category")
        pr_ = fl[fl['Dropped'] | fl['Short']].copy()
        pr_['Type'] = np.where(pr_['Dropped'], 'Dropped (Hst 99)', 'Short-shipped')
        if not pr_.empty:
            cc = pr_.groupby(['Custom Category', 'Type']).size().reset_index(name='Lines')
            fig = px.bar(cc, x='Custom Category', y='Lines', color='Type', text='Lines', color_discrete_map={'Dropped (Hst 99)': '#d62728', 'Short-shipped': '#ff7f0e'})
            fig.update_layout(xaxis_tickangle=-45)
            show_chart(fig, key="t6_fail_cat")
            known = pr_[~pr_['Item Category'].isin(['UNCATEGORIZED', '', 'nan'])]
            ic = known.groupby('Item Category').size().reset_index(name='Lines').sort_values('Lines', ascending=False).head(15)
            if not ic.empty:
                fig = px.bar(ic, x='Lines', y='Item Category', orientation='h', text='Lines', title="Top 15 item categories with dropped / short lines")
                fig.update_layout(yaxis={'categoryorder': 'total ascending'})
                show_chart(fig, key="t6_fail_item")
                hidden_n = len(pr_) - len(known)
                if hidden_n:
                    cap(f"{hidden_n:,} dropped / short lines have no item category in the Purchase Report and are not shown in this chart.")

        st.subheader("Vendor Line Fill Detail")
        ms = st.slider("Min completed lines per vendor", 1, 100, 10, key="fill_min")
        vf = fill_pieces(fl, 'Supplier')
        vf['Fill Rate %'] = fill_rate_pct(vf, fill_basis)
        vf['Dropped % of Lines'] = vf['Dropped'] / vf['Lines'] * 100
        vf['Short/Dropped % of Lines'] = (vf['Dropped'] + vf['Short']) / vf['Lines'] * 100
        vf = vf[vf['Lines'] >= ms].sort_values('Short/Dropped % of Lines', ascending=False)
        vf_show = vf[['Supplier', 'Lines', 'Dropped', 'Short', 'Over', 'Ord', 'Rec', 'ShortUSD', 'Fill Rate %', 'Dropped % of Lines', 'Short/Dropped % of Lines']].rename(
            columns={'Ord': 'Ordered (Cs)', 'Rec': 'Received (Cs)', 'ShortUSD': 'Short $', 'Over': 'Over-shipped'})
        show_table(style0(vf_show, money=['Short $'], pct=['Fill Rate %', 'Dropped % of Lines', 'Short/Dropped % of Lines']), height=380)
        cap("High-SKU vendors (e.g. Dot Foods, KeHE) naturally show more dropped lines - compare the % columns as well as counts. "
                   "Ordered / Received (Cs) cover lines that have a case size.")

        # ---------------- Fill deviation (rebuilt) ----------------
        st.subheader("Fill Deviation Score — Month over Month")
        cap("By month the PO was written (2025 onward). Fill rate is capped at 100% per line, so an over-shipment can no longer push a month off the chart or hide a shortfall elsewhere. "
                   "The deviation score is how many points short of 100% the month finished. Months still being delivered are marked, because open lines are not counted yet.")
        fm_src = by_supplier_cat(po_all)
        fm_src = fm_src[fm_src['Order Year'] >= 2025]
        fm = prepare_fill(completed_fill_lines(fm_src, live_po))
        fm = fm[fm['Order Month'] != 'NaT']
        if not fm.empty:
            mo = fill_pieces(fm, 'Order Month').sort_values('Order Month')
            mo['Fill Rate %'] = fill_rate_pct(mo, fill_basis)
            mo['Deviation (pts)'] = 100 - mo['Fill Rate %']
            open_n = fm_src[fm_src['Status Code'].between(*OPEN_PO)].groupby('Order Month').size()
            mo['Open Lines'] = mo['Order Month'].map(open_n).fillna(0)
            mo['Open Share %'] = mo['Open Lines'] / (mo['Open Lines'] + mo['Lines']) * 100
            mo['Incomplete'] = mo['Open Share %'] > 20
            mo = mo[(mo['Lines'] >= 5) & (mo['Order Month'] <= today.strftime('%Y-%m'))]
            lo = float(np.nanmin(mo['Fill Rate %'])) if len(mo) else 90.0
            y_lo = max(0.0, min(lo - 2, 98.0))
            ga, gb = st.columns(2)
            with ga:
                fig = go.Figure()
                fig.add_trace(go.Bar(x=mo['Order Month'], y=mo['Bad'], name='Dropped + short lines', marker_color='#d62728', yaxis='y2', opacity=0.35))
                fig.add_trace(go.Scatter(x=mo['Order Month'], y=mo['Fill Rate %'], name='Fill rate %', mode='lines+markers+text', line=dict(color='#1f77b4', width=3),
                                         marker=dict(size=9, color=np.where(mo['Incomplete'], '#bbbbbb', '#1f77b4')),
                                         text=mo['Fill Rate %'].map(lambda v: f"{v:,.1f}%"), textposition='top center'))
                fig.update_layout(yaxis=dict(title='Fill rate %', range=[y_lo, 100.8]), yaxis2=dict(title='Dropped + short lines', overlaying='y', side='right', rangemode='tozero'),
                                  title=f"Fill rate by order month ({fill_basis.split(' (')[0].lower()}), with problem-line counts", height=420)
                category_axis(fig, -45)
                show_chart(fig, key="t6_fill_rate")
            with gb:
                dev = mo.melt(id_vars='Order Month', value_vars=['Dropped', 'Short', 'Over'], var_name='Type', value_name='Count')
                dev['Type'] = dev['Type'].map({'Dropped': 'Dropped (Hst 99)', 'Short': 'Short-shipped', 'Over': 'Over-shipped'})
                fig = px.bar(dev, x='Order Month', y='Count', color='Type', barmode='stack', title="Where the deviation comes from (lines per month)",
                             color_discrete_map={'Dropped (Hst 99)': '#d62728', 'Short-shipped': '#ff7f0e', 'Over-shipped': '#9467bd'})
                fig.update_layout(height=420)
                category_axis(fig, -45)
                show_chart(fig, key="t6_fill_dev")
            inc = mo[mo['Incomplete']]['Order Month'].tolist()
            if inc:
                cap("Still being delivered (more than 20% of the month's lines are open - grey dots): " + ", ".join(inc) + ". Their fill rate will change as orders close.")
            tbl = mo[['Order Month', 'Lines', 'Dropped', 'Short', 'Over', 'Fill Rate %', 'Deviation (pts)', 'Open Share %']].rename(columns={'Over': 'Over-shipped', 'Open Share %': 'Still open %'})
            show_table(style0(tbl.iloc[::-1], one_dec=['Fill Rate %', 'Deviation (pts)'], pct=['Still open %']), height=300)
        else:
            st.info("Not enough completed lines from 2025 onward.")

        # ---------------- Composite ----------------
        st.subheader("Vendor Composite Scorecard")
        cap("Score = 50% fill rate + 50% on-time % (received lines, on or before Req dt). Higher is better.")
        ot = by_supplier_cat(po_df)
        ot = ot[ot['Status Code'].between(*CLOSED_PO) & ot['Recd dt'].notna() & ot['Req dt'].notna()]
        if slicer_year: ot = ot[ot['Order Year'].isin(slicer_year)]
        if slicer_month: ot = ot[ot['Order Month'].isin(slicer_month)]
        ots = ot.groupby('Supplier').agg(OL=('PO no', 'size'), OnTime=('Var Days', lambda s: (s <= 0).mean() * 100)).reset_index()
        lt = ot.assign(Plan=(ot['Req dt'] - ot['Ord dt']).dt.days, Act=(ot['Recd dt'] - ot['Ord dt']).dt.days).groupby('Supplier')[['Plan', 'Act']].mean().reset_index()
        comp = vf.merge(ots[['Supplier', 'OnTime']], on='Supplier', how='left').merge(lt, on='Supplier', how='left')
        comp['Score'] = 0.5 * comp['Fill Rate %'].fillna(100) + 0.5 * comp['OnTime'].fillna(comp['Fill Rate %'].fillna(100))
        comp['Lead Time Slip (days)'] = comp['Act'] - comp['Plan']
        comp = comp.rename(columns={'OnTime': 'On-time %', 'Plan': 'Planned LT (days)', 'Act': 'Actual LT (days)'})
        _comp_cols = ['Supplier', 'Lines', 'Fill Rate %', 'On-time %', 'Score', 'Planned LT (days)', 'Actual LT (days)', 'Lead Time Slip (days)']
        if HAS_TERMS:
            comp = add_terms(comp, 'Supplier').rename(columns={'Terms Label': 'Payment Terms'})
            _comp_cols = _comp_cols + ['Payment Terms']
        comp = comp[_comp_cols].sort_values('Score')
        show_table(style0(comp, pct=['Fill Rate %', 'On-time %', 'Score']), height=380)
        top_sp = po_active.groupby('Supplier')['USD $'].sum().sort_values(ascending=False)
        if top_sp.sum() > 0:
            t10 = top_sp.head(10).reset_index()
            t10['Share'] = t10['USD $'] / top_sp.sum() * 100
            fig = px.bar(t10, x='USD $', y='Supplier', orientation='h', text=t10.apply(lambda r: f"${r['USD $']:,.0f} ({r['Share']:.0f}%)", axis=1),
                         title="Open PO concentration - top 10 vendors")
            fig.update_layout(yaxis={'categoryorder': 'total ascending'})
            show_chart(fig, key="t6_conc")

# ==========================================================================
# TAB 7 — CASH FLOW
# ==========================================================================
with tabs[6], guard():
    st.header("Cash Flow Commitments — 6-Month Forward View")
    _vendor_modes = ["Vendor payment terms (days after the goods arrive)", "Requested date plus a fixed number of days (old method)"]
    if HAS_TERMS:
        cap(f"Open PO lines (Hst {OPEN_PO[0]}-{OPEN_PO[1]}) on active items, Otp = P01 excluded. Each line is paid when the goods are expected to arrive "
                   "(stripped, yard, arrival, ETA, then Req dt - the first one known) plus the supplier's payment terms from the Vendor Terms file. "
                   "Past months are excluded from the forward view. Ignores the Order Year/Month sidebar filters (all open orders are included); Supplier / Category filters apply.")
        pay_mode = st.radio("Time the payments by", _vendor_modes, index=0, horizontal=True, key="cf_mode")
    else:
        pay_mode = _vendor_modes[1]
        cap(f"Open PO lines (Hst {OPEN_PO[0]}-{OPEN_PO[1]}) on active items, Otp = P01 excluded, timed by Req dt. Past months are excluded from the forward view. "
                   "Ignores the Order Year/Month sidebar filters (all open orders are included); Supplier / Category filters apply.")
        st.info("No Vendor Terms were loaded (VendorTerms.xlsx is missing, unreadable or empty), so payments are timed from the requested date. Put a working copy of the file in the dashboard folder to time them by each supplier's terms.")
    use_terms = pay_mode == _vendor_modes[0]
    lag = 0
    if not use_terms:
        lag = st.number_input("Payment lag after Req dt (days)", 0, 180, 0, 15)
    cfl = by_supplier_cat(po_df[po_df['Status Code'].between(*OPEN_PO)]).copy()
    cfl = cfl[~cfl['Item Status'].isin(HARD_DISC_STATUSES + DELETED)]
    cfl = add_terms(cfl, 'Supplier')
    n_late_open, usd_late_open = 0, 0.0
    if use_terms:
        _anchor = payment_anchor(cfl)
        _late = cfl['Status'].isin(['1-Not Departed', '2-On the Water']) & (_anchor < today)       # late and not here yet: it cannot arrive in the past
        n_late_open, usd_late_open = int(_late.sum()), float(cfl.loc[_late, 'USD $'].sum())
        _anchor = _anchor.where(~_late, today)
        cfl['Cash Date'] = _anchor + pd.to_timedelta(cfl['Terms Days'], unit='D')
    else:
        cfl['Cash Date'] = cfl['Req dt'] + pd.to_timedelta(lag, unit='D')
    m0 = today.replace(day=1)
    m_end = m0 + pd.DateOffset(months=6)
    months = [p.strftime('%Y-%m') for p in pd.date_range(m0, periods=6, freq='MS')]
    no_date = cfl[cfl['Cash Date'].isna()]
    past = cfl[cfl['Cash Date'] < m0]
    fwd = cfl[(cfl['Cash Date'] >= m0) & (cfl['Cash Date'] < m_end)].copy()
    beyond = cfl[cfl['Cash Date'] >= m_end]
    fwd['Cash Month'] = fwd['Cash Date'].dt.strftime('%Y-%m')

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Next 6 months", f"${fwd['USD $'].sum():,.0f}")
    k2.metric("This month", f"${fwd.loc[fwd['Cash Month'] == months[0], 'USD $'].sum():,.0f}")
    k3.metric("Next 30 days", f"${fwd.loc[fwd['Cash Date'] <= today + pd.Timedelta(days=30), 'USD $'].sum():,.0f}")
    k4.metric("Past-due open POs (excluded)", f"${past['USD $'].sum():,.0f}", f"{len(past):,} lines", delta_color="off")
    k5.metric("Beyond 6 months", f"${beyond['USD $'].sum():,.0f}")
    if len(no_date):
        cap(f"{len(no_date):,} open lines have no date to time the payment from (${no_date['USD $'].sum():,.0f}) and are not in the timeline.")
    if use_terms:
        if n_late_open:
            cap(f"{n_late_open:,} lines (${usd_late_open:,.0f}) are past their expected date but have not arrived yet. They are assumed to arrive today at the earliest, so their payment is timed from today.")
        _all_open = cfl['USD $'].sum()
        _assumed = cfl.loc[~cfl['Terms Confirmed'], 'USD $'].sum()
        if _assumed > 0:
            _who = cfl.loc[~cfl['Terms Confirmed'], 'Supplier'].nunique()
            cap(f"${_assumed:,.0f} ({pct_of(_assumed, _all_open):.0f}% of open dollars, {_who:,} suppliers) is timed with an assumed {DEFAULT_TERMS_DAYS}-day term, because the supplier's "
                       "terms code in the Vendor Terms file is not a real payment period (e.g. N99) or the supplier is not in the file. The Data Checks tab lists them.")

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
        category_axis(fig)
        show_chart(fig, key="t7_main")

        if use_terms:
            fwd['Terms Group'] = np.select([~fwd['Terms Confirmed'], fwd['Terms Days'] <= 0, fwd['Terms Days'] <= 15, fwd['Terms Days'] <= 30, fwd['Terms Days'] <= 60],
                                           ['Terms not confirmed (assumed)', 'Pay on arrival (COD / card)', 'Net 1-15 days', 'Net 16-30 days', 'Net 31-60 days'], default='Net 61+ days')
            tg = fwd.groupby(['Cash Month', 'Terms Group'])['USD $'].sum().reset_index()
            fig = px.bar(tg, x='Cash Month', y='USD $', color='Terms Group', title="Projected cash out by month and payment terms",
                         category_orders={'Terms Group': ['Pay on arrival (COD / card)', 'Net 1-15 days', 'Net 16-30 days', 'Net 31-60 days', 'Net 61+ days', 'Terms not confirmed (assumed)']},
                         color_discrete_map={'Terms not confirmed (assumed)': '#9e9e9e'})
            category_axis(fig)
            show_chart(fig, key="t7_terms")

        w1, w2 = st.columns(2)
        with w1:
            wk = fwd[fwd['Cash Date'] <= today + pd.Timedelta(days=56)].copy()
            wk['Week Starting'] = (wk['Cash Date'] - pd.to_timedelta(wk['Cash Date'].dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
            wk = wk.groupby('Week Starting')['USD $'].sum().reset_index()
            fig = px.bar(wk, x='Week Starting', y='USD $', text_auto='$,.0f', title="Next 8 weeks (weekly view)")
            category_axis(fig)
            show_chart(fig, key="t7_weekly")
        with w2:
            fwd['Shipment Stage'] = np.where(fwd['Status'].isin(['1-Not Departed']), 'Not yet shipped', 'Shipped / in transit')
            sg = fwd.groupby(['Cash Month', 'Shipment Stage'])['USD $'].sum().reset_index()
            fig = px.bar(sg, x='Cash Month', y='USD $', color='Shipment Stage', title="Committed cash: already shipped vs not yet shipped",
                         color_discrete_map={'Shipped / in transit': '#2ca02c', 'Not yet shipped': '#ff7f0e'})
            category_axis(fig)
            show_chart(fig, key="t7_stage")

        b1, b2 = st.columns(2)
        with b1:
            bb = fwd.groupby('Buyer')['USD $'].sum().reset_index().sort_values('USD $')
            fig = px.bar(bb, x='USD $', y='Buyer', orientation='h', text_auto='$,.0f', title="Next 6 months by buyer")
            show_chart(fig, key="t7_buyer")
        with b2:
            pv = piv.copy()
            pv['Total'] = pv.sum(axis=1)
            st.markdown("**Month × category ($)**")
            show_table(style0(pv.sort_values('Total', ascending=False).reset_index(), money=list(pv.columns)))

        sel = st.selectbox("Drill into a month", months)
        dm = fwd[fwd['Cash Month'] == sel].groupby('Supplier').agg(USD=('USD $', 'sum'), Lines=('PO no', 'size'), POs=('PO no', 'nunique'),
                                                                     Terms=('Terms Label', 'first'), First_Payment=('Cash Date', 'min'),
                                                                     Buyer=('Buyer', join_unique)).reset_index().sort_values('USD', ascending=False).head(25)
        dm['First_Payment'] = dm['First_Payment'].dt.strftime('%Y-%m-%d')
        if not use_terms:
            dm = dm.drop(columns=['Terms'])
        show_table(style0(dm, money=['USD']))

    if not past.empty:
        with st.expander(f"Open POs already due, or past due, before this month - excluded from the forward view (${past['USD $'].sum():,.0f})"):
            pd_ = past.groupby('Supplier').agg(USD=('USD $', 'sum'), Lines=('PO no', 'size'), Oldest_Req=('Req dt', 'min'), Oldest_Due=('Cash Date', 'min')).reset_index().sort_values('USD', ascending=False).head(25)
            pd_['Oldest_Req'] = pd_['Oldest_Req'].dt.strftime('%Y-%m-%d')
            pd_['Oldest_Due'] = pd_['Oldest_Due'].dt.strftime('%Y-%m-%d')
            show_table(style0(pd_, money=['USD']))

def price_pairs(codes_ok):
    """Compares every PO line with the same item's previous order (same supplier, PO unit and price unit).
    Returns (pairs, n_status_skipped). `pairs` has a 'Held Back Because' column: '' = counted, otherwise the reason it is held back for review."""
    pp_all = po_df[po_df['Item grp'].isin(SALEABLE_GROUPS) & po_df['Ord dt'].notna()]
    n_status_skipped = int((~pp_all['Status Code'].isin(PRICE_STATUS_CODES)).sum())
    pp = pp_all[pp_all['Status Code'].isin(codes_ok) & (pp_all['Purch price'] > 0) & (pp_all['Price USD'] > 0) & np.isfinite(pp_all['Price USD'])].copy()
    pp = pp.sort_values(['Ord dt', 'PO no', 'Ln'])
    gk = ['Item number', 'POu', 'Ppu', 'Supplier']
    grp = pp.groupby(gk)
    pp['Prev Price'] = grp['Price USD'].shift(1)
    pp['Prev Curr'] = grp['Curr'].shift(1)
    pp['Prev Ord dt'] = grp['Ord dt'].shift(1)
    pp['Prev PO'] = grp['PO no'].shift(1)
    cmpx = pp[pp['Prev Price'].notna() & (pp['Prev Price'] > 0)].copy()
    cmpx['Chg %'] = (cmpx['Price USD'] / cmpx['Prev Price'] - 1) * 100
    ratio_ = cmpx['Price USD'] / cmpx['Prev Price']
    r_ph = ((cmpx['Prev Price'] <= PRICE_PLACEHOLDER_USD) | (cmpx['Price USD'] <= PRICE_PLACEHOLDER_USD)) & ((ratio_ >= PRICE_PLACEHOLDER_JUMP) | (ratio_ <= 1 / PRICE_PLACEHOLDER_JUMP))
    r_cur = cmpx['Curr'] != cmpx['Prev Curr']
    r_gap = (cmpx['Ord dt'] - cmpx['Prev Ord dt']).dt.days > PRICE_MAX_GAP_DAYS
    r_big = cmpx['Chg %'].abs() > PRICE_MAX_CHANGE_PCT
    cmpx['Held Back Because'] = np.select([r_ph, r_cur, r_gap, r_big],
                                          [f"A price of ${PRICE_PLACEHOLDER_USD:,.0f} or less was compared with a much bigger one (placeholder?)", "Currency changed between the two orders",
                                           f"The earlier price is more than {PRICE_MAX_GAP_DAYS // 30} months older", f"Move above +/-{PRICE_MAX_CHANGE_PCT:,.0f}% (UOM or typing error?)"], default='')
    return cmpx, n_status_skipped


# ==========================================================================
# TAB 8 — PRICE INDEX (PPV)
# ==========================================================================
with tabs[7], guard():
    st.header("Procurement & Price Index (PPV Trend)")
    cap("Saleable item groups only. Price = PO 'Purch price' converted to USD, compared with the same item's previous order price from the same supplier (same PO unit, same currency). "
        "Only PO lines in status 20-40 (on order) or 75 / 85 (received and stocked) are used, and sample items are left out. "
        "A price of $0 or $1 is treated as a placeholder, so it is never compared with a real price (for example $0 then $10, or $1 then $20, is not a 1,000% increase). "
        f"Any single move above +/-{PRICE_MAX_CHANGE_PCT:,.0f}%, a change of currency, or an earlier price older than {PRICE_MAX_GAP_DAYS // 30} months is held back for review rather than counted.",
        label="ℹ️ How prices are checked (click to open)")
    src_pick = st.radio("PO lines to use for prices", ["Received and on order (status 75 / 85 and 20-40)", "Received only (status 75 / 85)", "On order only (status 20-40)"],
                        index=0, horizontal=True, key="pp_src")
    codes_ok = PRICE_STATUS_CODES if src_pick.startswith("Received and") else ([75, 85] if src_pick.startswith("Received only") else list(range(20, 41)))
    cmpx, n_status_skipped = price_pairs(codes_ok)
    held = cmpx[cmpx['Held Back Because'] != ''].copy()
    n_out = len(held)
    cmpx = cmpx[cmpx['Held Back Because'] == ''].copy()
    cmpx['Price Basis'] = np.where(cmpx['Status Code'] <= 40, 'On order', 'Received')
    cmpx['Base Value'] = cmpx['Prev Price'] * cmpx['Order qty']
    cmpx['Chg Value'] = (cmpx['Price USD'] - cmpx['Prev Price']) * cmpx['Order qty']
    cmpx['Savings'] = (-cmpx['Chg Value']).clip(lower=0)
    cmpx['Increase $'] = cmpx['Chg Value'].clip(lower=0)
    if slicer_category:
        cmpx = cmpx[cmpx['Custom Category'].isin(slicer_category)]
        held = held[held['Custom Category'].isin(slicer_category)]
    cap(f"{len(cmpx):,} price comparisons are counted. {n_out:,} were held back (listed below) and {n_status_skipped:,} saleable PO lines were not used because their status is not 20-40, 75 or 85.")

    if cmpx.empty:
        st.info("Not enough repeat-order price history.")
    else:
        cur_m = today.strftime('%Y-%m')
        recent_m = sorted([m for m in cmpx['Order Month'].unique() if m != 'NaT' and m <= cur_m])[-12:]
        cm = cmpx[cmpx['Order Month'].isin(recent_m)].groupby(['Custom Category', 'Order Month']).agg(
            Chg=('Chg Value', 'sum'), Base=('Base Value', 'sum')).reset_index()
        cm['Price Move %'] = np.where(cm['Base'] > 0, cm['Chg'] / cm['Base'].replace(0, 1) * 100, np.nan)

        st.subheader("Category Price Direction — Month over Month")
        cap("Spend-weighted average price change vs the previous order of the same items. Red = costs rising, green = costs falling.")
        hm = cm.pivot(index='Custom Category', columns='Order Month', values='Price Move %').reindex(columns=recent_m)
        fig = go.Figure(go.Heatmap(z=hm.values, x=list(hm.columns), y=list(hm.index), colorscale='RdYlGn_r', zmid=0, zmin=-10, zmax=10,
                                   text=[[("" if pd.isna(v) else f"{v:+.0f}%") for v in row] for row in hm.values], texttemplate="%{text}",
                                   colorbar=dict(title="% move")))
        fig.update_layout(height=420)
        category_axis(fig)
        show_chart(fig, key="t8_heat")

        sel_m = st.selectbox("Month for at-a-glance view", recent_m[::-1])
        cur = cm[cm['Order Month'] == sel_m].dropna(subset=['Price Move %']).sort_values('Price Move %')
        if not cur.empty:
            fig = px.bar(cur, x='Price Move %', y='Custom Category', orientation='h', color='Price Move %', color_continuous_scale='RdYlGn_r',
                         range_color=[-10, 10], text=cur['Price Move %'].map(lambda v: f"{'▲' if v > 0 else '▼'} {v:+.0f}%"),
                         title=f"Average price direction by category — {sel_m}")
            fig.update_layout(coloraxis_showscale=False)
            show_chart(fig, key="t8_dir")

        st.subheader("Cost Savings Secured by Category — Month over Month")
        sv = cmpx[cmpx['Order Month'].isin(recent_m)].groupby(['Order Month', 'Custom Category'])['Savings'].sum().reset_index()
        sv = sv[sv['Savings'] > 0]
        if not sv.empty:
            fig = px.bar(sv, x='Order Month', y='Savings', color='Custom Category', title="USD saved vs previous order price (price decreases × quantity)")
            tots = sv.groupby('Order Month')['Savings'].sum()
            fig.add_trace(go.Scatter(x=list(tots.index), y=tots.values, mode='text', text=[f"${v:,.0f}" for v in tots.values], textposition='top center', showlegend=False))
            category_axis(fig)
            show_chart(fig, key="t8_sav")
            show_table(style0(sv.pivot_table(index='Custom Category', columns='Order Month', values='Savings', aggfunc='sum', fill_value=0).reset_index(),
                              money=recent_m))
        else:
            st.info("No price decreases in the period.")

        st.subheader("Savings and Increases - Line by Line, Month by Month")
        cap("For each month, every PO line whose price moved against the same item's previous order from the same supplier. "
            "Saving = the price went down x the quantity ordered. Increase = the price went up x the quantity ordered. The month is the month the PO was written. "
            "Pick a month to see exactly which items made the result.", label="ℹ️ How to read this (click to open)")
        ms = cmpx[cmpx['Order Month'].isin(recent_m)].groupby('Order Month').agg(
            Lines=('PO no', 'size'), Savings=('Savings', 'sum'), Increases=('Increase $', 'sum'),
            Down=('Chg Value', lambda s_: int((s_ < 0).sum())), Up=('Chg Value', lambda s_: int((s_ > 0).sum()))).reindex(recent_m).fillna(0).reset_index()
        ms['Net'] = ms['Increases'] - ms['Savings']
        fig = go.Figure()
        fig.add_trace(go.Bar(x=ms['Order Month'], y=ms['Savings'], name='Savings (price went down)', marker_color='#2ca02c'))
        fig.add_trace(go.Bar(x=ms['Order Month'], y=ms['Increases'], name='Increases (price went up)', marker_color='#d62728'))
        fig.add_trace(go.Scatter(x=ms['Order Month'], y=ms['Net'], name='Net (above 0 = costs went up)', mode='lines+markers', line=dict(color='#333333', width=2)))
        fig.update_layout(barmode='group', title="Dollar effect of price changes by month", yaxis_title="USD", height=380)
        category_axis(fig, -45)
        show_chart(fig, key="t8_lm_month")
        mt = ms.rename(columns={'Order Month': 'Month', 'Lines': 'Lines compared', 'Savings': 'Savings ($)', 'Increases': 'Increases ($)', 'Down': 'Lines cheaper', 'Up': 'Lines dearer',
                                'Net': 'Net ($, negative = saved)'})
        show_table(style0(mt.iloc[::-1], money=['Savings ($)', 'Increases ($)', 'Net ($, negative = saved)']), height=260)

        sel_lm = st.selectbox("Month to look at line by line", recent_m[::-1], key="pp_lm")
        mm = cmpx[(cmpx['Order Month'] == sel_lm) & (cmpx['Chg Value'] != 0)].copy()
        if mm.empty:
            st.info("No price changes were recorded in this month.")
        else:
            mm['Line'] = mm['Item number'] + ' ' + mm['ItemDescription'].astype(str).str.slice(0, 34) + ' - ' + mm['Supplier'].astype(str).str.slice(0, 18)
            lm1, lm2, lm3, lm4 = st.columns(4)
            lm1.metric("Savings", f"${mm['Savings'].sum():,.0f}", f"{int((mm['Chg Value'] < 0).sum()):,} lines cheaper", delta_color="off")
            lm2.metric("Increases", f"${mm['Increase $'].sum():,.0f}", f"{int((mm['Chg Value'] > 0).sum()):,} lines dearer", delta_color="off")
            net_ = mm['Increase $'].sum() - mm['Savings'].sum()
            lm3.metric("Net effect", f"${net_:+,.0f}", "costs went up" if net_ > 0 else "costs went down", delta_color="inverse")
            lm4.metric("Lines compared", f"{len(mm):,}")
            lc1, lc2 = st.columns(2)
            with lc1:
                top_s = mm[mm['Chg Value'] < 0].sort_values('Chg Value').head(15).iloc[::-1]
                if not top_s.empty:
                    fig = px.bar(top_s, x='Savings', y='Line', orientation='h', text=top_s['Savings'].map(lambda v: f"${v:,.0f}"), title=f"Biggest savings - {sel_lm}")
                    fig.update_traces(marker_color='#2ca02c')
                    fig.update_layout(height=520, yaxis_title="", xaxis_title="USD saved")
                    show_chart(fig, key="t8_lm_sav")
                else:
                    st.info("No savings this month.")
            with lc2:
                top_i = mm[mm['Chg Value'] > 0].sort_values('Chg Value', ascending=False).head(15).iloc[::-1]
                if not top_i.empty:
                    fig = px.bar(top_i, x='Increase $', y='Line', orientation='h', text=top_i['Increase $'].map(lambda v: f"${v:,.0f}"), title=f"Biggest increases - {sel_lm}")
                    fig.update_traces(marker_color='#d62728')
                    fig.update_layout(height=520, yaxis_title="", xaxis_title="USD extra paid")
                    show_chart(fig, key="t8_lm_inc")
                else:
                    st.info("No increases this month.")
            det = mm.assign(Direction=np.where(mm['Chg Value'] < 0, 'Saving', 'Increase'))
            det = det[['Direction', 'Ord dt', 'PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Custom Category', 'Buyer', 'POu', 'Prev PO', 'Prev Price', 'Price USD',
                       'Chg %', 'Order qty', 'Chg Value', 'Price Basis']].copy()
            det['Ord dt'] = det['Ord dt'].dt.strftime('%Y-%m-%d')
            det = det.rename(columns={'Item number': 'Item Code', 'ItemDescription': 'Item', 'Custom Category': 'Category', 'POu': 'PO Unit', 'Prev PO': 'Earlier PO',
                                      'Prev Price': 'Earlier Price (USD)', 'Price USD': 'New Price (USD)', 'Order qty': 'Qty Ordered', 'Chg Value': 'Dollar Effect (negative = saved)',
                                      'Price Basis': 'PO Status'})
            det = det.reindex(det['Dollar Effect (negative = saved)'].abs().sort_values(ascending=False).index)
            show_table(style0(det, money2=['Earlier Price (USD)', 'New Price (USD)'], money=['Dollar Effect (negative = saved)'], pct=['Chg %']), height=380)
            csv_button(det, f"⬇️ Export {sel_lm} line items", f"price_changes_{sel_lm}.csv", "dl_pp_lm")

        st.subheader("Top 20 Item Categories with Price Increases — Last 6 Months")
        cap("Item category = Purchase Report 'Category' (column AB), e.g. CHOCOLATE, FISH, CEREAL. Items with no category are not shown.")
        r1, r2 = st.columns(2)
        rank_by = r1.selectbox("Rank by", ["Weighted avg % increase", "Total $ impact of increases", "# items with increases"])
        min_ev = r2.slider("Min price-increase events per category", 1, 20, 3)
        c6 = cmpx[(cmpx['Ord dt'] >= today - pd.DateOffset(months=6)) & (cmpx['Chg Value'] > 0) & ~cmpx['Item Category'].isin(['UNCATEGORIZED', '', 'nan'])]
        if not c6.empty:
            tc = c6.groupby('Item Category').agg(Events=('PO no', 'size'), Items=('Item number', 'nunique'), Impact=('Chg Value', 'sum'), Base=('Base Value', 'sum')).reset_index()
            tc['Weighted avg % increase'] = tc['Impact'] / tc['Base'].replace(0, 1) * 100
            tc = tc[tc['Events'] >= min_ev]
            tc = tc.rename(columns={'Impact': 'Total $ impact of increases', 'Items': '# items with increases', 'Events': 'Increase events'}).drop(columns='Base')
            tc = tc.sort_values(rank_by if rank_by in tc.columns else 'Weighted avg % increase', ascending=False).head(20)
            if not tc.empty:
                fig = px.bar(tc.iloc[::-1], x=rank_by, y='Item Category', orientation='h',
                             text=tc.iloc[::-1]['Weighted avg % increase'].map(lambda v: f"+{v:,.0f}%"), title=f"Top 20 item categories — ranked by {rank_by.lower()}")
                fig.update_layout(height=620)
                show_chart(fig, key="t8_top20")
                show_table(style0(tc, pct=['Weighted avg % increase'], money=['Total $ impact of increases']))
            else:
                st.info("No item categories meet the minimum number of increase events.")
        else:
            st.info("No price increases in the last 6 months.")

        st.subheader("Biggest Line-Level Moves (last 6 months)")
        rec = cmpx[cmpx['Ord dt'] >= today - pd.DateOffset(months=6)]
        show_cols = ['PO no', 'Supplier', 'ItemDescription', 'Prev Price', 'Price USD', 'Chg %']
        cx, cy = st.columns(2)
        with cx:
            st.markdown("**Inflation — largest price increases**")
            show_table(style0(rec.sort_values('Chg %', ascending=False)[show_cols].head(15), money2=['Prev Price', 'Price USD'], pct=['Chg %']))
        with cy:
            st.markdown("**Savings — largest price decreases**")
            show_table(style0(rec.sort_values('Chg %')[show_cols].head(15), money2=['Prev Price', 'Price USD'], pct=['Chg %']))

    if n_out:
        with st.expander(f"Price moves held back for review ({n_out:,}) - click to open"):
            cap("These comparisons were NOT counted as savings or increases because the two prices cannot be compared reliably. "
                "Each one is worth a quick look: a typing slip in the price, a PO unit that changed, or a placeholder price.")
            hb = held.assign(**{'Impact ($)': (held['Price USD'] - held['Prev Price']) * held['Order qty']})
            hb = hb[['Held Back Because', 'Ord dt', 'PO no', 'Supplier', 'Item number', 'ItemDescription', 'POu', 'Prev PO', 'Prev Price', 'Price USD', 'Chg %', 'Impact ($)', 'Buyer']].copy()
            hb['Ord dt'] = hb['Ord dt'].dt.strftime('%Y-%m-%d')
            hb = hb.rename(columns={'ItemDescription': 'Item', 'Item number': 'Item Code', 'Price USD': 'New Price (USD)', 'Prev Price': 'Earlier Price (USD)', 'Prev PO': 'Earlier PO'})
            hb = hb.reindex(hb['Impact ($)'].abs().sort_values(ascending=False).index).head(500)
            why_ = hb.groupby('Held Back Because').size().reset_index(name='Lines').sort_values('Lines', ascending=False)
            show_table(why_)
            show_table(style0(hb, money2=['Earlier Price (USD)', 'New Price (USD)'], money=['Impact ($)'], pct=['Chg %']), height=320)
            csv_button(hb, "⬇️ Export held-back price moves", "price_moves_held_back.csv", "dl_pp_held")

    st.subheader("Item Price History")
    item_q = st.text_input("🔍 Search item (code or description) to see its price trend by supplier:")
    if item_q:
        hist = po_df[(po_df['Purch price'] > 0) & (po_df['Item number'].str.contains(item_q, case=False, na=False, regex=False) |
                                                   po_df['ItemDescription'].str.contains(item_q, case=False, na=False, regex=False))].sort_values('Ord dt')
        if not hist.empty:
            fig = px.line(hist, x='Ord dt', y='Price USD', color='Supplier', markers=True, hover_data=['ItemDescription', 'POu'], title=f"Price history (USD per PO unit) — {item_q}")
            show_chart(fig, key="t8_hist")
        else:
            st.warning("No price history found for this item.")

# ==========================================================================
# DATA CHECK HELPERS (used by the Data Checks tab)
# ==========================================================================
# ---- finding items that are "too similar" in the item master -------------------------------------------------
_SIM_TIERS = {1: 'Identical wording', 2: 'Near-identical (spelling, plural or abbreviation)', 3: 'Same product details, different wording'}
_SIM_WHY = {1: 'The description has the same words and the same sizes - only the word order, punctuation or spacing differs.',
            2: 'The description matches except for a spelling slip, plural or short form of one word.',
            3: 'Same item group, category, format, flavor, brand, unit size and case size, but the description is worded differently.'}


def _dc_tokens(desc):
    """Words and size codes of an item description (upper case, no punctuation). Words and numbers are kept apart."""
    d = str(desc).upper().replace("'", "").replace('"', ' IN ')
    d = re.sub(r'[,;:()*\[\]]+', ' ', d)
    ws = [w.strip('.-') for w in d.split()]
    ws = [w for w in ws if w]
    words = frozenset(w for w in ws if not re.search(r'\d', w))
    nums = frozenset(w for w in ws if re.search(r'\d', w))
    return words, nums


def _edit_within(a, b, k):
    """True when a can be turned into b with at most k single-letter changes (insert, delete, change, swap neighbours)."""
    if abs(len(a) - len(b)) > k:
        return False
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1] <= k


def _word_close(a, b):
    """Two different words that are probably the same word typed differently (spelling slip, plural, short form).
    SALTED / UNSALTED, MENS / WOMENS, MALE / FEMALE and size codes (S, M, X-LARGE) are real differences and are NOT close."""
    if a == b:
        return True
    if not (a.isalpha() and b.isalpha()):
        return False
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    if len(lo) < 3 or lo[:2] != hi[:2]:
        return False
    if hi in (lo + 'S', lo + 'ES'):
        return True
    if len(lo) >= 4 and hi.startswith(lo):
        return True
    if len(lo) < 4:
        return False
    return _edit_within(lo, hi, 2 if len(lo) >= 9 else 1)


def find_similar_items(items):
    """items needs: Item Number, Item Description, Item Class, Category, Format, Flavor, Brand, Unit Size, Case Size.
    Returns (pairs, packs):
      pairs = DataFrame(A, B, Tier, Why) for tiers 1-3 (A and B are positions in `items`)
      packs = list of lists of positions: same words, same class, different size / pack (usually real variants)."""
    df = items.reset_index(drop=True)
    tk = df['Item Description'].map(_dc_tokens)
    W = [t[0] for t in tk]
    N = [t[1] for t in tk]
    sigW = [' '.join(sorted(w)) for w in W]
    sigN = [' '.join(sorted(n)) for n in N]
    cls = df['Item Class'].astype(str).tolist()
    pairs = []

    # tier 1: the same words and the same sizes (the order, punctuation and spacing may differ)
    by_sig = {}
    for i, (a, b) in enumerate(zip(sigW, sigN)):
        if a:
            by_sig.setdefault((a, b), []).append(i)
    for members in by_sig.values():
        for k in range(1, len(members)):
            pairs.append((members[0], members[k], 1, _SIM_WHY[1]))

    # tier 2: the same sizes, and every word that differs has a close partner on the other side
    by_n = {}
    for i, b in enumerate(sigN):
        by_n.setdefault(b, []).append(i)
    for members in by_n.values():
        if len(members) < 2:
            continue
        post = {}
        for i in members:
            for w in W[i]:
                if len(w) >= 3:
                    post.setdefault(w, []).append(i)
        seen = set()
        for w, lst in post.items():
            if len(lst) < 2 or len(lst) > 80:
                continue
            for x in range(len(lst)):
                for y in range(x + 1, len(lst)):
                    i, j = lst[x], lst[y]
                    if (i, j) in seen:
                        continue
                    seen.add((i, j))
                    if W[i] == W[j]:
                        continue
                    da, db = W[i] - W[j], W[j] - W[i]
                    if not da or not db or len(da) > 2 or len(db) > 2:
                        continue
                    if all(any(_word_close(p, q) for q in db) for p in da) and all(any(_word_close(p, q) for p in da) for q in db):
                        pairs.append((i, j, 2, "Only spelling differs: " + " / ".join(sorted(da)) + " vs " + " / ".join(sorted(db))))

    # tier 3: the same product details written differently
    def _clean(col):
        s = df[col].astype(str).str.strip()
        return s.where(~(s.str.lower().str.startswith('usr-def') | s.str.upper().isin(['', 'NAN', 'NONE'])), '')
    us = pd.to_numeric(df['Unit Size'], errors='coerce').round(1).astype(str)
    cs = pd.to_numeric(df['Case Size'], errors='coerce').round(2).astype(str)
    key = (df['Item Class'].astype(str) + '|' + _clean('Category') + '|' + _clean('Format') + '|' + _clean('Flavor') + '|' + _clean('Brand') + '|' + us + '|' + cs)
    ok = (_clean('Category') != '') & (_clean('Format') != '') & (_clean('Flavor') != '') & (_clean('Brand') != '') & (us != 'nan') & (cs != 'nan')
    done = {(min(a, b), max(a, b)) for a, b, _, _ in pairs}
    for _, idx in key[ok].groupby(key[ok]).groups.items():
        idx = list(idx)
        if len(idx) < 2 or len(idx) > 12:
            continue
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                if sigW[i] == sigW[j] and sigN[i] == sigN[j]:
                    continue
                if (i, j) in done:
                    continue
                if not (W[i] <= W[j] or W[j] <= W[i]):          # both sides have words of their own (a different flavor, say) = a real variant
                    continue
                pairs.append((i, j, 3, _SIM_WHY[3]))

    # same words, different size / pack (informational)
    by_w = {}
    for i, (a, c) in enumerate(zip(sigW, cls)):
        if a and len(W[i]) >= 3:
            by_w.setdefault((a, c), []).append(i)
    packs = [m for m in by_w.values() if len(m) > 1 and len({sigN[i] for i in m}) > 1]
    pdf = pd.DataFrame(pairs, columns=['A', 'B', 'Tier', 'Why'])
    return pdf, packs


def group_similar(items, pairs, packs=None):
    """Turns pairs into groups (a group can hold 3 or more items). Returns one row per item with a Group number."""
    df = items.reset_index(drop=True)
    parent = list(range(len(df)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    tier_of, why_of = {}, {}
    for a, b, t, w in pairs.itertuples(index=False):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for a, b, t, w in pairs.itertuples(index=False):
        r = find(a)
        if r not in tier_of or t < tier_of[r]:
            tier_of[r], why_of[r] = t, w
    rows = []
    members = {}
    for i in {int(x) for x in list(pairs['A']) + list(pairs['B'])}:
        members.setdefault(find(i), []).append(i)
    order = sorted(members.items(), key=lambda kv: (tier_of[kv[0]], -len(kv[1]), kv[0]))
    for g_no, (root, ids) in enumerate(order, 1):
        for i in ids:
            rows.append({'Group': g_no, 'Match type': _SIM_TIERS[tier_of[root]], 'Why flagged': why_of[root], '_pos': i})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.join(df.drop(columns=[c for c in out.columns if c in df.columns]), on='_pos')


def iso_check_digit(cid):
    """The 11th character a container number should end with (ISO 6346), worked out from the first ten. '' if the number cannot be read."""
    try:
        cid = str(cid).strip().upper()
        total = sum((_ISO_LETTER[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(cid[:10]))
        return str((total % 11) % 10)
    except Exception:
        return ''


# ---- the buyer action list: what each kind of finding means and what to do about it ------------------------------
DC_ACT_NOW, DC_ACT_SOON, DC_FIX = '1 - Act now', '2 - Act soon', '3 - Fix the data'
DC_ISSUES = {
    'Out of stock, selling, nothing inbound': (DC_ACT_NOW, "The warehouse has none left, the item is selling and no order is on its way. Order now or confirm a replacement item."),
    'Bid item: no stock, no order, no substitute': (DC_ACT_NOW, "A bid item with nothing in stock, nothing on order and no suitable substitute. Place an order or agree a replacement with the customer."),
    'Late order on an item about to run out': (DC_ACT_NOW, "The order is past its requested date and the warehouse has under a month of stock. Chase the supplier or the shipping line."),
    'Open order on an item not yet active': (DC_ACT_SOON, "The item is still in set-up status (0-14) but already has an open order. Once the goods arrive they will not show in any report. Activate the item (status 20) before then."),
    'Item in set-up status but already trading': (DC_FIX, "The item is in set-up status (0-14), which every report ignores, yet it holds stock or sells. Activate it (status 20) or move the stock."),
    'Open order on a switched-off item': (DC_ACT_NOW, "An order is open on an item whose status says 'do not buy' (80) or 'not calculated' (0-14). Cancel the order or correct the status."),
    'Below safety stock, nothing inbound': (DC_ACT_SOON, "Stock is at or below safety stock and nothing is on order. Decide whether to reorder before it runs out."),
    'Order quantity looks too large': (DC_ACT_SOON, "The order is more than a year of normal sales. Check the quantity and the PO unit for a typing slip (an extra zero, cases entered as each)."),
    'Order price far from the usual price': (DC_ACT_SOON, "The price on this open order is 3 times higher or lower than what this item usually costs. Check the price, currency and unit."),
    'Price move held back': (DC_ACT_SOON, "The price changed so much since the previous order that it was left out of the Price Index. Confirm the price on the PO (unit of measure, currency, typing)."),
    'Forecast far from recent sales': (DC_ACT_SOON, "The forecast is 10 times higher or lower than what the item sold recently. Check the forecast file for a unit or typing slip."),
    'Order dates out of order': (DC_ACT_SOON, "The requested date is before the order date. Correct the date on the PO line."),
    'Stock on an item switched off': (DC_FIX, "The item status is 80 (no selling, buying or calculation) but the warehouse still holds stock. Sell it through, transfer it or change the status."),
    'Sell-only item on an open order': (DC_FIX, "Status 50 means the item is still sold but no longer bought. Confirm the order is intended, or move the item back to status 20."),
    'Unknown item status code': (DC_FIX, "The status code is not one the dashboard knows. Check it in the item master."),
    'Duplicate item (identical wording)': (DC_FIX, "The same description exists under more than one item number. Keep one and set the others to 50 / 80, or correct the description if they are genuinely different."),
    'Duplicate item (spelling differs)': (DC_FIX, "The descriptions differ only by a spelling slip, plural or short form. Probably the same item entered twice."),
    'Duplicate item (same details, different wording)': (DC_FIX, "Same group, category, format, flavor, brand, unit size and case size. Confirm whether this is one product set up twice."),
    'Category or Format not filled in': (DC_FIX, "The item master still holds a placeholder (usr-def). The item cannot be grouped or matched to substitutes until Category and Format are filled in."),
    'Brand missing': (DC_FIX, "No brand in the item master. Substitute matching and reports use the brand."),
    'Description cut off': (DC_FIX, "The description stops in the middle of a weight (for example 'AVG 11.'), so the case weight cannot be read. Complete the description."),
    'Lead time looks too long': (DC_FIX, "A lead time over 150 days is probably a typing slip (e.g. 245 instead of 24). It inflates safety stock."),
    'No lead time': (DC_FIX, "No lead time in the item master, so a default is assumed. Enter the real lead time."),
    'No case size': (DC_FIX, "The item cannot be shown in cases. Fix the Base U/M, Alt U/M or Conversion Factor in the item master."),
    'Negative stock': (DC_FIX, "A site shows negative stock. A receipt or issue is missing or was entered twice."),
    'Bid code cannot be rated (status 0-14 / 86-99)': (DC_FIX, "The bid lists an item whose status in the item master is never calculated (0-14 or 86-99), so it cannot be rated. Replace the code on the Bids file or have the item set up."),
    'Bid code not in the Purchase Report': (DC_FIX, "The bid lists a code that is not in the Purchase Report. Correct the code in the bid file."),
    'Price placeholder on an open order': (DC_FIX, "The order is priced at $0 or $1, which usually means the real price has not been entered."),
}


@st.cache_data(show_spinner=False)
def similar_items_cached(items):
    """Cached wrapper: (groups of similar items, groups of same-wording-different-pack items) for the active + sell-only items."""
    pairs, packs = find_similar_items(items)
    grp = group_similar(items, pairs) if len(pairs) else pd.DataFrame()
    pk = pd.DataFrame()
    if packs:
        pk_pairs = pd.DataFrame([(m[0], m[k], 4, 'Same words but a different size or pack - usually genuine variants.') for m in packs for k in range(1, len(m))], columns=['A', 'B', 'Tier', 'Why'])
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for a, b, _, _ in pk_pairs.itertuples(index=False):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        members = {}
        for i in {int(x) for x in list(pk_pairs['A']) + list(pk_pairs['B'])}:
            members.setdefault(find(i), []).append(i)
        rows = [{'Group': g_no, 'Match type': 'Same wording, different size or pack', '_pos': i}
                for g_no, (_, ids) in enumerate(sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0])), 1) for i in ids]
        pk = pd.DataFrame(rows)
        pk = pk.join(items.reset_index(drop=True), on='_pos').drop(columns=['_pos'])
    return grp, pk


def style_auto(df):
    """Whole numbers for big values, two decimals for small ones (so 0.19 months of cover does not show as 0)."""
    f = {}
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            col = pd.to_numeric(df[c], errors='coerce')
            has_frac = bool(((col - col.round()).abs() > 0.005).any())
            f[c] = "{:,.2f}" if (has_frac and col.abs().max() < 100) else "{:,.0f}"
    return df.style.format(f, na_rep="")

# ==========================================================================
# TAB 9 — DATA CHECKS
# ==========================================================================
def _data_checks_all():
    """Runs every logic check over the compiled data. Returns (checks, details, issues, extras):
    checks  = list of dicts (Area, Check, Result, Found, What it means)
    details = {title: DataFrame} lists that sit behind the yellow / red rows
    issues  = DataFrame with one row per item that needs a buyer's attention (Priority, Issue, Buyer, Item, Detail, What to do)
    extras  = other tables used by the tab (similar items, container comparison)."""
    checks, details, issue_parts, extras = [], {}, [], {}
    LEVEL = {'OK': '✅ OK', 'Note': 'ℹ️ Note', 'Review': '⚠️ Review', 'Problem': '❌ Problem'}
    RANK = {'Problem': 0, 'Review': 1, 'Note': 2, 'OK': 3}

    def add(area, name, level, found, note):
        checks.append({'Area': area, 'Check': name, 'Result': LEVEL[level], 'Found': found, 'What it means': note, '_rank': RANK[level]})

    def level_for(n, review_at=1, problem_at=None):
        if problem_at is not None and n >= problem_at:
            return 'Problem'
        return 'Review' if n >= review_at else 'OK'

    def group(area, fn):
        try:
            fn()
        except Exception as e:
            add(area, 'Checks in this group could not run', 'Review', type(e).__name__, f"{e}")
            details[f'Technical detail: why the {area} checks could not run'] = pd.DataFrame({'Technical detail': traceback.format_exc().strip().splitlines()[-6:]})

    item_desc_ = inv.set_index('Item Number')['Item Description']
    item_buyer_ = inv.set_index('Item Number')['Responsible Buyer']
    moving_ = set(inv.loc[(inv['Total_Cases'] > 0) | (inv['3M_Avg_Depletion_Demand'] > 0) | (inv['Cases Inbound'] > 0) | (inv['Inventory Value'] > 0), 'Item Number'])

    def flag(issue, df, item_col='Item Number', detail='', buyer=None, desc=None, moving_only=False):
        """Puts the items of `df` on the buyer action list under `issue` (see DC_ISSUES for its priority and the advice)."""
        if df is None or len(df) == 0:
            return
        d = pd.DataFrame({'Item Number': df[item_col].astype(str).values})
        d['Description'] = (list(desc) if desc is not None else d['Item Number'].map(item_desc_).fillna('').tolist())
        d['Buyer'] = (list(buyer) if buyer is not None else d['Item Number'].map(item_buyer_).tolist())
        d['Detail'] = ([detail] * len(d) if isinstance(detail, str) else list(detail))
        d['Buyer'] = d['Buyer'].fillna('Unassigned').astype(str).replace({'': 'Unassigned', 'nan': 'Unassigned'})
        if moving_only:
            d = d[d['Item Number'].isin(moving_)]
        d['Issue'] = issue
        issue_parts.append(d.drop_duplicates(['Item Number']))

    meta_s = DATA['meta'].get('sales', {})
    meta_p = DATA['meta'].get('po', {})
    meta_f = DATA['meta'].get('fc', {})

    # ---------------- Files ----------------
    def files():
        for k, name, mt in DATA['files']:
            lab = FILE_LABEL[k]
            if k in DATA['errors']:
                add('Files', f"{lab} loaded", 'Problem' if k in ('sales', 'po') else 'Review', 'No', DATA['errors'][k])
                continue
            age = (today - pd.Timestamp(mt, unit='s').normalize()).days if mt else None
            limit = {'sales': 7, 'po': 7, 'fc': 40, 'bids': 60, 'm3': 7, 'vt': 120}[k]
            if age is None:
                add('Files', f"{lab} loaded", 'Review', name, "Could not read the file date.")
            else:
                add('Files', f"{lab} is recent", 'OK' if age <= limit else 'Review', f"{name}: {age} days old",
                    "Replace the file with a fresh export." if age > limit else "Loaded and up to date.")
    group('Files', files)

    # ---------------- Purchase report ----------------
    def purchase():
        s = sales_df
        dup = int(meta_s.get('duplicates', 0))
        add('Purchase Report', 'Duplicate item numbers', level_for(dup), f"{dup:,}", "Repeated item numbers were ignored (first one kept)." if dup else "Every item number appears once.")
        live = s[s['Item Status'].isin(LIVE_STATUSES)]
        nocs = live[live['No Case Size']]
        nocs_stock = nocs[nocs[ONHAND_SITES].clip(lower=0).sum(axis=1) > 0]
        is_plant = nocs_stock['Item Class'].isin(PLANT_GROUPS) | ~nocs_stock['Item Class'].isin(SALEABLE_GROUPS)
        sale_bad = nocs_stock[~is_plant]
        add('Purchase Report', 'Saleable active items with stock but no case size', level_for(len(sale_bad), 1, 25), f"{len(sale_bad):,} of {len(live):,}",
            "These items cannot be shown in cases. Fix the Base U/M, Alt U/M or Conversion Factor in the item master." if len(sale_bad) else "Every saleable active item with stock has a case size.")
        add('Purchase Report', 'Plant raw materials with no case size (expected)', 'OK', f"{int(is_plant.sum()):,} items",
            "Plant raw materials are held in gallons, pounds or metres, so they have no case size and are left out of case totals. Their dollar value is still counted.")
        if len(nocs_stock):
            details['Active items with stock but no case size'] = nocs_stock[['Item Number', 'Item Description', 'Item Class', 'Base U/M', 'Alt U/M', 'Conversion Factor', 'Inventory Value']].sort_values('Inventory Value', ascending=False)
            flag('No case size', sale_bad, detail='$' + sale_bad['Inventory Value'].map(lambda v: f"{v:,.0f}").astype(str) + ' of stock cannot be shown in cases')
        basis = s['Case Basis'].value_counts()
        add('Purchase Report', 'How case sizes were worked out', 'OK', "; ".join(f"{k}: {v:,}" for k, v in basis.items()),
            "Description weight = the weight written in the item description (first choice for KG items); Pack count = Conversion Factor; Conversion factor weight = used when the description has no weight.")
        kg = s[(s['Stock UOM'] == 'KG') & (s['Case Basis'] == 'Description weight') & (s['Conversion Factor'] > 1)].copy()
        if len(kg):
            kg['Gap %'] = (kg['Case KG'] / kg['Conversion Factor'] - 1) * 100
            agree = (kg['Gap %'].abs() <= 5).mean() * 100
            add('Purchase Report', 'Description weight agrees with the Conversion Factor (KG items)', 'OK' if agree >= 90 else 'Review', f"{agree:,.0f}% within 5%",
                "The weight in the description and the item master's Conversion Factor should normally match. Differences are listed below (the description weight is used)." if agree < 100 else "Perfect agreement.")
            off = kg[kg['Gap %'].abs() > 25]
            if len(off):
                details['KG items where description weight and Conversion Factor differ by more than 25%'] = off[
                    ['Item Number', 'Item Description', 'Case KG', 'Conversion Factor', 'Gap %', 'Inventory Value']].rename(columns={'Case KG': 'KG per case used'}).sort_values('Inventory Value', ascending=False)
        neg = s[(s[ONHAND_SITES] < 0).any(axis=1)]
        add('Purchase Report', 'Items with negative stock at a site', level_for(len(neg)), f"{len(neg):,}",
            "Negative stock is treated as zero in totals. It usually means a receiving or issuing entry is missing." if len(neg) else "No negative stock.")
        if len(neg):
            details['Items with negative stock'] = neg[['Item Number', 'Item Description'] + ONHAND_SITES]
            flag('Negative stock', neg, detail=neg[ONHAND_SITES].apply(lambda r: ', '.join(f"{k} {v:,.0f}" for k, v in r.items() if v < 0), axis=1))
        nolead = live[live['Lead Time Missing']]
        add('Purchase Report', 'Active items with no lead time', level_for(len(nolead)), f"{len(nolead):,}",
            f"A {DEFAULT_LEAD_DAYS}-day lead time is assumed for them in safety stock." if len(nolead) else "All active items have a lead time.")
        if len(nolead):
            flag('No lead time', nolead, detail='Default ' + str(DEFAULT_LEAD_DAYS) + ' days assumed', moving_only=True)
        tot_val = float(s['Inventory Value'].sum())
        site_val = float(sum(s[f'Val_{x}'].sum() for x in ONHAND_SITES + NOT_COUNTED_SITES))
        unplaced = float(s['Unplaced Value'].sum())
        diff = abs(site_val + unplaced - tot_val) / max(abs(tot_val), 1) * 100
        add('Purchase Report', 'Inventory value adds up across sites', 'OK' if diff < 0.01 else 'Problem', f"${tot_val:,.0f} = ${site_val:,.0f} by site + ${unplaced:,.0f} unplaced",
            "The dollar value shared out to each site (by each site's share of units) equals the item totals." if diff < 0.01 else "The site values do not add up to the total inventory value.")
        nz = s[(s['Unplaced Value'] != 0)]
        add('Purchase Report', 'Inventory dollars on items with no stock quantity', 'OK' if abs(unplaced) < 0.001 * max(abs(tot_val), 1) else 'Review', f"{len(nz):,} items, ${unplaced:,.0f}",
            "Value is reported for these items but no site holds any units, so it cannot be placed in a warehouse." if len(nz) else "None.")
        dm = meta_s.get('dep_months', [])
        add('Purchase Report', 'Sales months used for averages', 'OK' if dm else 'Review', ", ".join(dm) or "none",
            "The month still in progress is left out so partial months do not understate sales." if not INCLUDE_CURRENT_MONTH_SALES else "The current month is included.")
        all_dep = meta_s.get('dep_cols', [])
        if all_dep:
            last_full = (today.replace(day=1) - pd.DateOffset(months=1)).strftime('%B %Y')
            have_last = any(c.startswith(last_full) for c in all_dep)
            add('Purchase Report', 'Last complete month of sales is in the file', 'OK' if have_last else 'Review', last_full + (" found" if have_last else " missing"),
                "Averages may be out of date." if not have_last else "Sales columns are current.")
        negd = s[s['3M_Avg_Depletion'] < 0]
        add('Purchase Report', 'Items with net negative sales (returns)', 'OK' if len(negd) == 0 else 'Review', f"{len(negd):,}", "Counted as zero demand." if len(negd) else "None.")
    group('Purchase Report', purchase)

    # ---------------- PO file ----------------
    def purchase_orders():
        p = po_raw
        add('PO Dates', 'File layout recognised', 'OK', meta_p.get('layout', '?'),
            f"Sheet used: {meta_p.get('sheet', '?')}. All the columns the dashboard needs were found.")
        rates = ", ".join(f"{k} {v:.6g} USD" for k, v in sorted((meta_p.get('fx_used') or {}).items()) if k != 'USD')
        unk = meta_p.get('unknown_currency') or []
        add('PO Dates', 'Every currency has an exchange rate', 'OK' if not unk else 'Problem', ", ".join(unk) if unk else "Yes",
            ("These lines count as $0 until a rate is added to FX_TO_USD at the top of the app file." if unk else
             f"Dollar amounts come from {meta_p.get('fx_source', 'the PO file')}. Rate per 1 unit of currency: {rates or 'all USD'}. Update FX_TO_USD if the rates change."))
        date_cols = ['ETD Date', 'Departed Date', 'ETA', 'Arrival', 'Costed Date', 'Yard Date', 'Stripped Date', 'Clearance Paid Date']
        odd_dates = []
        for dc in date_cols:
            if dc in p.columns:
                badd = p[(p[dc] < pd.Timestamp('2020-01-01')) | (p[dc] > today + pd.Timedelta(days=365))]
                if len(badd):
                    odd_dates.append(badd[['PO no', 'Ln', 'Supplier', dc]].rename(columns={dc: 'Date'}).assign(Field=dc))
        odd_dates = pd.concat(odd_dates) if odd_dates else pd.DataFrame()
        add('PO Dates', 'Shipping dates that look like typos (before 2020 or over a year ahead)', level_for(len(odd_dates)), f"{len(odd_dates):,}",
            "Probably a mistyped year. These dates can distort the shipment timeline." if len(odd_dates) else "None.")
        if len(odd_dates):
            details['Shipping dates that look like typos'] = odd_dates.head(500)
        okset = set(range(20, 50)) | set(range(75, 86)) | {99}
        odd = p[~p['Status Code'].isin(okset)]
        add('PO Dates', 'Lines with other Hst codes', level_for(len(odd)), f"{len(odd):,}" + (" (Hst " + ", ".join(str(int(x)) for x in sorted(odd['Status Code'].unique())) + ")" if len(odd) else ""),
            "Lines with a status outside 20-49, 75-85 or 99 (for example drafts) are ignored in every tab." if len(odd) else "All lines have an expected status.")
        if len(odd):
            details['Lines with an unexpected Hst code'] = odd[['PO no', 'Ln', 'Hst', 'Supplier', 'Item number']].head(500)
        live = po_df
        add('PO Dates', 'Lines analysed', 'OK', f"{len(live):,} of {len(p):,}",
            f"{int((p['Status Code'] == DELETED_PO).sum()):,} dropped (Hst 99) lines are only used for the vendor fill checks; "
            f"{int((po_raw['Otp'] == 'P01').sum()):,} P01 lines are excluded unless you tick the sidebar box.")
        dupe = p.duplicated(['PO no', 'Ln', 'Sub'], keep=False)
        add('PO Dates', 'Duplicate PO lines (same PO, line and sub-line)', level_for(int(dupe.sum()), 1, 1), f"{int(dupe.sum()):,}",
            "The same line appears twice, which double counts spend." if dupe.any() else "No duplicates.")
        if dupe.any():
            details['Duplicate PO lines'] = p[dupe][['PO no', 'Ln', 'Sub', 'Supplier', 'Item number', 'Order qty', 'USD $']].sort_values(['PO no', 'Ln']).head(500)
        nosup = live[live['Supplier'] == 'Unknown Supplier']
        add('PO Dates', 'Live lines with no supplier', level_for(len(nosup)), f"{len(nosup):,}", "Shown as 'Unknown Supplier'." if len(nosup) else "All lines have a supplier.")
        nodt = live[live['Ord dt'].isna()]
        add('PO Dates', 'Live lines with no order date', level_for(len(nodt)), f"{len(nodt):,}", "These do not appear in monthly charts." if len(nodt) else "All lines have an order date.")
        if len(nodt):
            details['Live lines with no order date'] = nodt[['PO no', 'Ln', 'Hst', 'Supplier', 'Item number', 'ItemDescription', 'Curr', 'Line amount']].head(200)
        fut = live[live['Ord dt'] > today + pd.Timedelta(days=7)]
        add('PO Dates', 'Orders dated in the future', level_for(len(fut)), f"{len(fut):,}", "Check the order date on these lines." if len(fut) else "None.")
        if len(fut):
            details['Orders dated in the future'] = fut[['PO no', 'Ln', 'Supplier', 'Ord dt', 'Req dt', 'USD $']].head(500)
        op = live[live['Status Code'].between(*OPEN_PO)]
        add('PO Dates', 'Open lines with no Req dt', level_for(int(op['Req dt'].isna().sum())), f"{int(op['Req dt'].isna().sum()):,} of {len(op):,}",
            "They cannot be placed in the cash-flow timeline." if op['Req dt'].isna().any() else "All open lines have a Req dt.")
        stale = op[op['Req dt'] < today - pd.Timedelta(days=90)]
        add('PO Dates', 'Open lines more than 90 days past Req dt', level_for(len(stale)), f"{len(stale):,} (${stale['USD $'].sum():,.0f})",
            "Either still waiting or never closed. Worth a clean-up." if len(stale) else "None.")
        if len(stale):
            details['Open lines more than 90 days past Req dt'] = stale[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Req dt', 'Status', 'USD $']].sort_values('USD $', ascending=False).head(500)
        miss_item = live[~live['In Purchase Report']]
        add('PO Dates', 'PO items not found in the Purchase Report', level_for(len(miss_item)), f"{len(miss_item):,} lines (${miss_item['USD $'].sum():,.0f})",
            "They cannot be converted to cases or grouped by item category." if len(miss_item) else "Every PO item is in the Purchase Report.")
        if len(miss_item):
            details['PO items not found in the Purchase Report'] = miss_item.groupby(['Item number', 'ItemDescription']).agg(Lines=('PO no', 'size'), USD=('USD $', 'sum')).reset_index().sort_values('USD', ascending=False).head(300)
        conv_l = pct_of(live['Convertible'].sum(), len(live))
        conv_u = pct_of(live.loc[live['Convertible'], 'USD $'].sum(), live['USD $'].sum())
        add('PO Dates', 'PO lines that can be shown in cases', 'OK' if conv_u >= 95 else 'Review', f"{conv_l:,.1f}% of lines, {conv_u:,.1f}% of dollars",
            "The rest (mostly plant raw materials bought by LB or gallon) are left out of case totals." if conv_l < 100 else "Everything converts.")
        unc = live[~live['Convertible']]
        if len(unc):
            details['PO lines with no case size'] = unc.groupby(['Item grp', 'POu', 'Item number', 'ItemDescription']).agg(Lines=('PO no', 'size'), USD=('USD $', 'sum')).reset_index().sort_values('USD', ascending=False).head(300)
        usd = live[live['Curr'] == 'USD']
        bad_usd = usd[(usd['USD $'] - usd['Line amount']).abs() > np.maximum(0.01, usd['Line amount'].abs() * 0.01)]
        add('PO Dates', 'USD lines where the USD amount differs from the line amount', level_for(len(bad_usd), 1, 25), f"{len(bad_usd):,}",
            "A USD line should have the same amount in both columns." if len(bad_usd) else "USD lines agree.")
        if len(bad_usd):
            details['USD lines where the USD amount differs'] = bad_usd[['PO no', 'Ln', 'Supplier', 'Line amount', 'USD $']].head(300)
        fx_bad = 0
        for cur_, g in live[(live['Curr'] != 'USD') & (live['Line amount'] != 0)].groupby('Curr'):
            rate = g['USD $'] / g['Line amount']
            fx_bad += int(((rate / rate.median() - 1).abs() > 0.05).sum()) if rate.median() else 0
        add('PO Dates', 'Foreign-currency lines with an odd exchange rate', level_for(fx_bad), f"{fx_bad:,}",
            "The rate implied by the USD column is more than 5% away from that currency's usual rate." if fx_bad else "Exchange rates are consistent.")
        cw = live[live['Weight Dev %'].notna()]
        if len(cw):
            ok10 = (cw['Weight Dev %'].abs() <= 10).mean() * 100
            add('PO Dates', 'Catch-weight lines: description weight vs PO dollars', 'OK' if ok10 >= 85 else 'Review', f"{ok10:,.0f}% within 10% ({len(cw):,} lines)",
                "Lines priced per KG but ordered by the case. A gap is normal on 'AVG' items; large gaps mean the case size may be wrong. See the Executive Summary tab for the review list.")
        closed = live[live['Status Code'].between(*CLOSED_PO)]
        over = closed[(closed['Order qty'] > 0) & (closed['Recd qty'] > closed['Order qty'] * 1.05)]
        add('PO Dates', 'Closed lines received more than 5% over the order', level_for(len(over)), f"{len(over):,} of {len(closed):,}",
            "Over-shipments never count as extra fill. Large ones are often a unit mismatch." if len(over) else "None.")
        if len(over):
            details['Closed lines received more than 5% over the order'] = over[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'POu', 'Order qty', 'Recd qty']].assign(
                Ratio=lambda x: x['Recd qty'] / x['Order qty']).sort_values('Ratio', ascending=False).head(300)
        no_rec = closed[closed['Recd dt'].isna()]
        add('PO Dates', 'Closed lines (Hst 75-85) with no receive date', level_for(len(no_rec)), f"{len(no_rec):,}", "They are left out of on-time measures." if len(no_rec) else "None.")
        if len(no_rec):
            details['Closed lines with no receive date'] = no_rec[['PO no', 'Ln', 'Hst', 'Supplier', 'Item number', 'ItemDescription', 'Req dt', 'Order qty', 'Recd qty']].head(500)
        open_rec = op[op['Recd dt'].notna()]
        add('PO Dates', 'Open lines (Hst 20-49) that already have a receive date', level_for(len(open_rec)), f"{len(open_rec):,}",
            "Probably received but not closed in the system." if len(open_rec) else "None.")
        bad_seq = []
        for a, b, lab in [('Departed Date', 'Arrival', 'Arrived before it departed'), ('Arrival', 'Yard Date', 'Yard date before arrival'), ('Yard Date', 'Stripped Date', 'Stripped before yard date'),
                          ('Arrival', 'Costed Date', 'Costed before arrival')]:
            m = live[a].notna() & live[b].notna() & (live[b] < live[a])
            if m.any():
                bad_seq.append((lab, live[m]))
        n_seq = sum(len(x) for _, x in bad_seq)
        add('PO Dates', 'Shipping dates out of order', level_for(n_seq), f"{n_seq:,} lines",
            "; ".join(f"{lab}: {len(x):,}" for lab, x in bad_seq) if bad_seq else "Departed, arrival, yard and stripped dates are in sequence.")
        if bad_seq:
            details['Shipping dates out of order'] = pd.concat([x.assign(Problem=lab) for lab, x in bad_seq])[['Problem', 'PO no', 'Ln', 'Container ID', 'Departed Date', 'Arrival', 'Costed Date', 'Yard Date', 'Stripped Date']].head(500)
        ship = live[live['Shipment Key'] != '']
        conflict = ship.groupby('Shipment Key').agg(a=('Arrival', 'nunique'), y=('Yard Date', 'nunique'), s=('Stripped Date', 'nunique'))
        n_conf = int(((conflict > 1).any(axis=1)).sum())
        add('PO Dates', 'Containers whose lines carry different dates', level_for(n_conf), f"{n_conf:,} of {len(conflict):,}",
            "Lines of one container should share arrival, yard and stripped dates. The earliest date is used." if n_conf else "All lines of a container agree.")
        shp = ship.groupby('Shipment Key').agg(Arr=('Arrival', 'min'), Yard=('Yard Date', 'min'), Strip=('Stripped Date', 'min'), Container=('Container ID', 'first'))
        stale_c = shp[shp['Arr'].notna() & shp['Yard'].isna() & shp['Strip'].isna() & (shp['Arr'] < today - pd.Timedelta(days=90))]
        add('PO Dates', 'Containers arrived 90+ days ago with no yard or stripped date', level_for(len(stale_c)), f"{len(stale_c):,}",
            "Either still waiting or the dates were never entered." if len(stale_c) else "None.")
        if len(stale_c):
            details['Containers arrived 90+ days ago with no yard or stripped date'] = stale_c.reset_index()[['Container', 'Arr']].rename(columns={'Arr': 'Arrival'})
        landed_open = op[op['Status'] == '6-Stripped']
        add('PO Dates', 'Stripped containers whose PO is still open', 'OK' if len(landed_open) == 0 else 'Review', f"{len(landed_open):,} lines",
            "Counted as arrived stock (not 'on order') until the PO is received in the system." if len(landed_open) else "None.")
        if len(landed_open):
            details['Stripped containers whose PO is still open'] = landed_open[['PO no', 'Ln', 'Hst', 'Supplier', 'Item number', 'ItemDescription', 'Container ID', 'Stripped Date', 'USD $']].sort_values('Stripped Date').head(500)
    group('PO Dates', purchase_orders)

    # ---------------- Forecast ----------------
    def forecast():
        if fc_df.empty:
            add('Forecast', 'Forecast loaded', 'Review', 'No', DATA['errors'].get('fc', 'Forecast file not loaded.'))
            return
        for w in meta_f.get('warnings', []):
            add('Forecast', 'Forecast covers the next 3 months', 'Review', 'No', w)
        if not meta_f.get('warnings'):
            add('Forecast', 'Forecast covers the next 3 months', 'OK', ", ".join(fc_meta['cols3']), "Near-term average uses these months.")
        d = int(meta_f.get('duplicate_codes', 0))
        add('Forecast', 'Duplicate item codes in the forecast', level_for(d), f"{d:,}", "Duplicates were added together." if d else "None.")
        known = set(sales_df['Item Number'])
        nf = fc_df[~fc_df['Item Code'].isin(known)]
        add('Forecast', 'Forecast items not found in the Purchase Report', level_for(len(nf)), f"{len(nf):,} of {len(fc_df):,}", "Left out of category coverage." if len(nf) else "All forecast items exist.")
        if len(nf):
            details['Forecast items not found in the Purchase Report'] = nf[['Item Code', 'Item Description', '3M_Avg_Forecast_Cases']].sort_values('3M_Avg_Forecast_Cases', ascending=False).head(300)
        neg = int(meta_f.get('negative_cells', 0))
        add('Forecast', 'Negative forecast values', level_for(neg), f"{neg:,}", "A forecast below zero is treated as zero demand." if neg else "None.")
        both = inv[(inv['3M_Avg_Forecast_Cases'] > 0) & (inv['3M_Avg_Depletion_Demand'] > 0) & inv['Is Live'] & ~inv['Custom Category'].isin(EXCLUDED_CATS)]
        if len(both):
            ratio = both['3M_Avg_Forecast_Cases'].sum() / both['3M_Avg_Depletion_Demand'].sum()
            add('Forecast', 'Forecast volume vs recent sales (same items)', 'OK' if 0.6 <= ratio <= 1.7 else 'Review', f"{ratio:,.2f}x ({len(both):,} items)",
                "A ratio far from 1 means the forecast and the sales history are in different units or cover very different periods.")
        nofc = inv[inv['Is Live'] & (inv['3M_Avg_Depletion_Demand'] > 0) & ~inv['Has Forecast'] & ~inv['Custom Category'].isin(NO_SS_CATS)]
        add('Forecast', 'Active selling items with no forecast', 'OK' if len(nofc) == 0 else 'Review', f"{len(nofc):,}",
            "They are left out of the safety stock chart unless you tick 'include items with no forecast'." if len(nofc) else "All selling items have a forecast.")
    group('Forecast', forecast)

    # ---------------- Bids ----------------
    def bids():
        if bids_df.empty:
            add('Bids', 'Bid file loaded', 'Review', 'No', DATA['errors'].get('bids', 'Bid file not loaded.'))
            return
        mb = DATA['meta'].get('bids', {})
        ncust = bids_df['Customer'].nunique()
        pairs_ = bids_df.drop_duplicates(['Customer', 'Item Code'])
        add('Bids', 'Bid customers and items on the Bids file', 'OK', f"{ncust:,} customers, {len(pairs_):,} customer / item pairs, {bids_df['Item Code'].nunique():,} different items",
            "The Bids file (BIDS.xlsx) is the one source of bid customers and items. Each customer's items are counted once, however many bid periods list them.")
        dropped = mb.get('dropped', pd.DataFrame())
        add('Bids', 'Bids file rows skipped (CPJ Code is not an item number)', level_for(len(dropped)), f"{len(dropped):,} of {mb.get('rows_in_file', len(bids_df)):,} rows",
            "These rows have no usable item code, so they cannot be counted. Correct the CPJ Code on the Bids file." if len(dropped) else "Every row has a readable CPJ Code and customer name.")
        if len(dropped):
            details['Bids file rows skipped (CPJ Code is not an item number)'] = dropped.head(300)
        dup = bids_df.groupby(['Customer', 'Bid Name', 'Item Code']).size().reset_index(name='Times listed')
        dup = dup[dup['Times listed'] > 1]
        add('Bids', 'Items listed more than once in the same bid', 'Note' if len(dup) else 'OK', f"{len(dup):,}",
            "Counted once per customer, so totals are not inflated. Remove the repeats on the Bids file to keep it tidy." if len(dup) else "No item is listed twice in a bid.")
        if len(dup):
            details['Items listed more than once in the same bid'] = dup.merge(bids_df.drop_duplicates('Item Code')[['Item Code', 'Item Description']], on='Item Code', how='left').head(300)
        # bid periods whose status label disagrees with their dates
        per = bids_df.groupby(['Customer', 'Bid Name', 'Bid Status'], dropna=False).agg(Start=('Bid Start', 'min'), End=('Bid End', 'max'), Items=('Item Code', 'nunique')).reset_index()
        is_cur = per['Bid Status'].astype(str).str.strip().str.lower().eq('current')
        stale = per[(is_cur & per['End'].notna() & (per['End'] < today)) | (~is_cur & per['End'].notna() & (per['End'] >= today) & (per['Start'].isna() | (per['Start'] <= today)))].copy()
        stale['What looks wrong'] = np.where(stale['Bid Status'].astype(str).str.strip().str.lower().eq('current'), 'Marked Current but the end date has passed', 'Marked ' + stale['Bid Status'].astype(str) + ' but the bid is still running')
        add('Bids', 'Bid periods whose Current / Expired label disagrees with the dates', level_for(len(stale)), f"{len(stale):,} of {len(per):,} bids",
            "Only matters when you choose 'Only bids marked Current' in the sidebar; the default uses every bid on the file. Update the Bid Status column on the Bids file." if len(stale) else "Every bid's label matches its dates.")
        if len(stale):
            details['Bid periods whose Current / Expired label disagrees with the dates'] = stale[['Customer', 'Bid Name', 'Bid Status', 'Start', 'End', 'Items', 'What looks wrong']]
        rev_ = per[per['Start'].notna() & per['End'].notna() & (per['End'] < per['Start'])].copy()
        rev_['What looks wrong'] = 'The bid ends before it starts - a year is probably mistyped'
        add('Bids', 'Bids whose end date is earlier than the start date', level_for(len(rev_)), f"{len(rev_):,} of {len(per):,} bids",
            "A mistyped year on the Bids file. Such a bid looks finished (and is hidden by 'Only bids marked Current'), so its customer can drop out of the figures. Correct the dates." if len(rev_) else "Every bid ends after it starts.")
        if len(rev_):
            details['Bids whose end date is earlier than the start date'] = rev_[['Customer', 'Bid Name', 'Bid Status', 'Start', 'End', 'Items', 'What looks wrong']]
        add('Bids', 'Bid statuses in the file', 'OK', "; ".join(f"{k or '(blank)'}: {v:,}" for k, v in bids_df['Bid Status'].value_counts().items()), "Shown for information.")
        if BID_ENGINE_ERROR:
            add('Bids', 'Bid availability worked out', 'Review', 'No', BID_ENGINE_ERROR)
            return
        if BID_CLS.empty:
            return
        # ---- every customer's total must equal the number of different CPJ Codes the Bids file lists for it
        src_pairs = bid_items_table(bid_src_mode)[0]
        file_tot = src_pairs.groupby('Customer')['Item Code'].nunique()
        eng_tot = BID_CI.groupby('Customer')['Item Code'].nunique()
        bkt_tot = BID_CI.groupby('Customer')['Bucket'].apply(lambda x: int(x.isin(BID_ALL_BUCKETS).sum()))
        tie = pd.DataFrame({'On the Bids file': file_tot, 'In the dashboard': eng_tot, 'Sorted into a bar': bkt_tot}).fillna(0).astype(int)
        tie['Difference'] = (tie['On the Bids file'] - tie['In the dashboard']).abs() + (tie['On the Bids file'] - tie['Sorted into a bar']).abs()
        bad_t = tie[tie['Difference'] > 0]
        add('Totals tie out', "Each bid customer's item total equals the Bids file", 'OK' if bad_t.empty else 'Problem',
            f"{len(tie):,} customers, {int(tie['On the Bids file'].sum()):,} items" if bad_t.empty else f"{len(bad_t):,} customer(s) differ",
            "Every item on the Bids file is in exactly one bar (stocked, on order, no order or left out)." if bad_t.empty else "An item went missing or was counted twice between the file and the bars.")
        details['Bid item totals per customer (file vs dashboard)'] = tie.reset_index().rename(columns={'index': 'Customer'})
        counted = BID_CLS[BID_CLS['Bucket'].isin(BID_BUCKETS)]
        # ---- the headline total: different CPJ Codes on the Bids file = rated + left out
        file_uni = int(src_pairs['Item Code'].nunique())
        left_d = BID_CLS[BID_CLS['Disc Left Out'].fillna(False).astype(bool)]
        left_o = BID_CLS[(BID_CLS['Bucket'] == B_OUT) & ~BID_CLS['Disc Left Out'].fillna(False).astype(bool)]
        ok_u = (file_uni == len(BID_CLS)) and (len(counted) + len(left_d) + len(left_o) == len(BID_CLS))
        add('Totals tie out', 'Unique bid items on the Bids file = items in the calculation + items left out', 'OK' if ok_u else 'Problem',
            f"{file_uni:,} = {len(counted):,} + {len(left_d):,} Discontinued no Replacement + {len(left_o):,} Code not usable",
            "The headline total on the Bid Performance and Bid Availability tabs is the number of different CPJ Codes on the Bids file." if ok_u else "The headline total does not match the Bids file - an item was dropped or counted twice.")
        disc_ = BID_CLS[BID_CLS['Discontinued'].fillna(False).astype(bool)]
        n_drated = int(disc_['Disc Rated'].fillna(False).astype(bool).sum())
        add('Bids', 'Discontinued bid items (status 50 / 80)', 'Note' if len(disc_) else 'OK', f"{len(disc_):,} items: {n_drated:,} rated on a strong substitute, {len(left_d):,} left out",
            "A discontinued item with a direct / strong substitute is rated on that substitute; one with none is left out of the calculation. The Bid Performance tab lists them all." if len(disc_) else "No bid item is discontinued.")
        if len(disc_):
            details['Discontinued bid items (status 50 / 80)'] = disc_[['Item Code', 'Item Description', 'Item Status', 'Responsible Buyer', 'Bid Stock (Cs)', 'Bucket', 'How', 'Sub Item', 'Sub Description', 'Sub Stock (Cs)', 'Sub Confidence']].rename(columns={'How': 'Recorded as', 'Bucket': 'Result'})
        left_stock = left_d[left_d['Bid Stock (Cs)'].fillna(0) > bid_stock_min]
        add('Bids', 'Discontinued bid items left out that still have stock', 'Note' if len(left_stock) else 'OK', f"{len(left_stock):,} of {len(left_d):,}",
            "They are left out of the calculation because they have no strong substitute, but stock is on hand at MDC / SEP / SEZ. If the customer can still be supplied they could be put back by setting up a substitute." if len(left_stock) else "None of the items left out has stock on hand.")
        if len(left_stock):
            details['Discontinued bid items left out that still have stock'] = left_stock[['Item Code', 'Item Description', 'Item Status', 'Responsible Buyer', 'Bid Stock (Cs)']]
        # the status typed on the bid sheet against the item master
        mst = inv.drop_duplicates('Item Number').set_index('Item Number')['Item Status']
        pr_ = pairs_.copy()
        pr_['Master Status'] = pd.to_numeric(pr_['Item Code'].map(mst), errors='coerce')
        diff = pr_[pr_['Item Code'].isin(mst.index) & (pr_['Item Status'].fillna(-1) != pr_['Master Status'].fillna(-1))]
        add('Bids', 'Status typed on the Bids file differs from the item master', 'Note' if len(diff) else 'OK', f"{len(diff):,} of {len(pr_):,} customer / item pairs",
            "The item master is the one that counts. The status column on the Bids file is out of date for these items." if len(diff) else "The Bids file statuses match the item master.")
        if len(diff):
            details['Status typed on the Bids file differs from the item master'] = diff[['Customer', 'Bid Name', 'Item Code', 'Item Description', 'Item Status', 'Master Status']].rename(columns={'Item Status': 'Status on Bids file'}).head(500)
        add('Bids', 'Bid items rated', 'OK', f"{len(counted):,} of {len(BID_CLS):,} different items ({BID_NOTE})",
            "Status 20 items are rated on stock, orders and substitutes; status 50 / 80 items are rated on a strong substitute or left out; codes that cannot be rated are set aside.")
        notpr = BID_CLS[~BID_CLS['In Purchase Report'].fillna(False).astype(bool)]
        add('Bids', 'Bid codes that are not in the Purchase Report', level_for(len(notpr)), f"{len(notpr):,} of {len(BID_CLS):,}",
            "These codes cannot be rated, so they are left out and show as grey items. A mistyped code is the usual cause." if len(notpr) else "Every bid code exists in the Purchase Report.")
        if len(notpr):
            details['Bid codes that are not in the Purchase Report'] = notpr[['Item Code', 'Item Description']].head(500)
            flag('Bid code not in the Purchase Report', notpr, item_col='Item Code', detail='', buyer=['Unassigned'] * len(notpr), desc=notpr['Item Description'].fillna('').tolist())
        unrated = BID_CLS[(BID_CLS['Bucket'] == B_OUT) & BID_CLS['In Purchase Report'].fillna(False).astype(bool) & ~BID_CLS['Disc Left Out'].fillna(False).astype(bool)]
        add('Bids', 'Bid items on codes that are not calculated (status 0-14 or 86-99)', level_for(len(unrated)), f"{len(unrated):,}",
            "They cannot be rated, so they are left out and show as grey items. Replace the code on the Bids file or have the item set up." if len(unrated) else "None.")
        if len(unrated):
            details['Bid items on codes that are not calculated (status 0-14 or 86-99)'] = unrated[['Item Code', 'Item Description', 'Item Status', 'Responsible Buyer', 'How']]
            flag('Bid code cannot be rated (status 0-14 / 86-99)', unrated, item_col='Item Code', detail=unrated['How'].tolist(), buyer=unrated['Responsible Buyer'].tolist(), desc=unrated['Item Description'].fillna('').tolist())
        # the M3 workbook's Bid Universe sheet is no longer the source; show how it compares
        bu = m3_data.get('bid_universe', pd.DataFrame()) if m3_data else pd.DataFrame()
        if len(bu):
            k_ = lambda d: set(zip(d['Customer'].astype(str).str.lower().str.strip(), d['Item Code'].astype(str)))
            only_u, only_b = k_(bu) - k_(pairs_), k_(pairs_) - k_(bu)
            add('Bids', "Bid Universe sheet (M3 workbook) compared with the Bids file", 'Note', f"{len(only_u):,} pairs only on the sheet, {len(only_b):,} only on the Bids file",
                "The Bids file is used for every bid figure. If the Bid Universe sheet is meant to list the same bids, one of the two files is out of date.")
            cmpt = pd.DataFrame({'On the Bids file': pairs_.assign(c=pairs_['Customer'].str.lower()).groupby('c').size(),
                                 'On the Bid Universe sheet': bu.assign(c=bu['Customer'].astype(str).str.lower().str.strip()).drop_duplicates(['c', 'Item Code']).groupby('c').size()}).fillna(0).astype(int)
            details['Bid Universe sheet compared with the Bids file (items per customer)'] = cmpt.reset_index().rename(columns={'index': 'Customer'})
        none_ = counted[counted['Bucket'] == B_NONE]
        add('Bids', 'Bid items with no stock, nothing on order and no suitable substitute', level_for(len(none_)), f"{len(none_):,} of {len(counted):,}",
            "These are the bid items that will fail to ship unless a buyer acts. The Bid Availability tab shows them by customer." if len(none_) else "Every rated bid item has stock, an order or a substitute.")
        if len(none_):
            nb_ = none_.merge(BID_CI.groupby('Item Code')['Customer'].nunique().rename('Customers'), left_on='Item Code', right_index=True, how='left').sort_values('Customers', ascending=False)
            details['Bid items with no stock, no order and no substitute'] = nb_[['Item Code', 'Item Description', 'Item Status', 'Responsible Buyer', 'Customers', 'OM_Cs', '3M_Avg_Depletion_Demand']].rename(columns={'OM_Cs': 'OM stock (cs, not counted)', '3M_Avg_Depletion_Demand': 'Avg monthly sales (cs)'})
            flag('Bid item: no stock, no order, no substitute', nb_, item_col='Item Code', detail='Bid for ' + nb_['Customers'].fillna(0).astype(int).astype(str) + ' customer(s)', buyer=nb_['Responsible Buyer'].tolist(), desc=nb_['Item Description'].fillna('').tolist())
        om_only = counted[(counted['OM_Cs'].fillna(0) > 0) & (counted['Bucket'] == B_NONE)]
        add('Bids', 'Bid items whose only stock is at OM (not counted)', 'Note', f"{len(om_only):,}",
            "OM stock is never counted as available. These items look out of stock here but OM holds some; a transfer could fix them." if len(om_only) else "None.")
        if len(om_only):
            details['Bid items whose only stock is at OM'] = om_only[['Item Code', 'Item Description', 'Responsible Buyer', 'OM_Cs']].rename(columns={'OM_Cs': 'OM stock (cs)'})
    group('Bids', bids)

    # ---------------- Reconciliations ----------------
    def reconcile():
        tot = float(po_filtered['USD $'].sum())
        by_cat = float(po_filtered.groupby('Custom Category')['USD $'].sum().sum())
        by_whs = float(po_filtered.groupby('Whs')['USD $'].sum().sum())
        by_buyer = float(po_filtered.groupby('Buyer')['USD $'].sum().sum())
        for nm, v in (('category', by_cat), ('warehouse', by_whs), ('buyer', by_buyer)):
            add('Totals tie out', f"Spend by {nm} adds up to total spend", 'OK' if abs(v - tot) < 1 else 'Problem', f"${v:,.0f} vs ${tot:,.0f}", "Every PO dollar lands in exactly one slice.")
        o_tot = float(po_active['USD $'].sum())
        o_buy = float(po_active.groupby('Buyer')['USD $'].sum().sum())
        add('Totals tie out', 'Open spend by buyer adds up to open spend', 'OK' if abs(o_tot - o_buy) < 1 else 'Problem', f"${o_buy:,.0f} vs ${o_tot:,.0f}", f"Open = Hst {OPEN_PO[0]}-{OPEN_PO[1]}.")
        site_cs = float(sum(inv[f'{x}_Cs'].sum() for x in ONHAND_SITES))
        add('Totals tie out', 'Cases by site add up to total cases', 'OK' if abs(site_cs - float(inv['Total_Cases'].sum())) / max(site_cs, 1) < 0.01 else 'Review',
            f"{site_cs:,.0f} vs {float(inv['Total_Cases'].sum()):,.0f}", "A small gap is expected when a site holds negative stock (treated as zero in the total).")
        wh_cs = float(inv['WH_Cases'].sum())
        cov_stock = float(cover_table(inv)['Stock (Cs)'].sum())
        add('Totals tie out', 'Cover calculation never uses more stock than the warehouses hold', 'OK' if cov_stock <= wh_cs + 1 else 'Problem',
            f"{cov_stock:,.0f} vs {wh_cs:,.0f} cases", "Months of cover uses only active, selling items in warehouses (no Stores).")
        wh_val = float(inv['WH Value'].sum())
        wh_val2 = float(sum(inv[f'Val_{x}'].sum() for x in WAREHOUSE_SITES))
        add('Totals tie out', 'Warehouse inventory value equals MDC + SEP + MJM + Bonded', 'OK' if abs(wh_val - wh_val2) < 1 else 'Problem', f"${wh_val:,.0f} vs ${wh_val2:,.0f}", "Stores, Plants and OM stock are not part of warehouse inventory.")
        open_cs = float(po_active_all['Order Qty (Cases)'].sum())
        mapped = float((inv['Cases At Port'] + inv['Cases On Order']).sum())
        lost = open_cs - mapped
        add('Totals tie out', 'Open PO cases reach the inventory pipeline', 'OK' if lost < max(1.0, open_cs * 0.01) else 'Review', f"{mapped:,.0f} of {open_cs:,.0f} cases",
            "Open cases on items missing from the Purchase Report cannot be attached to an item." if lost >= max(1.0, open_cs * 0.01) else "All open cases are attached to items.")
    group('Totals tie out', reconcile)

    # ---------------- Bid Purchasing workbook ----------------
    def m3_checks():
        meta_m = DATA['meta'].get('m3', {})
        if not m3_data:
            add('Bid Purchasing', 'M3 workbook loaded', 'Review', 'No', DATA['errors'].get('m3', 'The Bid Purchasing tab is using the Purchase Report CSV.'))
            return
        pr = m3_data['pr']
        add('Bid Purchasing', 'M3 workbook loaded', 'OK', f"{len(pr):,} items", f"'Purchase Report' sheet read. Sheets also used: " + (", ".join(meta_m.get('lists_found', [])) or 'none') + ".")
        a, b = set(pr['Item Number']), set(sales_df['Item Number'])
        only_m3, only_csv = len(a - b), len(b - a)
        add('Bid Purchasing', 'M3 Purchase Report sheet has the same items as the Purchase Report CSV', level_for(only_m3 + only_csv), f"{only_m3:,} only in M3, {only_csv:,} only in CSV",
            "The two files list the same items." if not (only_m3 + only_csv) else "The M3 workbook and the CSV were exported at different times. The Bid Purchasing tab uses the workbook; the other tabs use the CSV.")
        j = pr.set_index('Item Number')[['MDC', 'On Order', 'Inventory Value']].join(sales_df.set_index('Item Number')[['MDC', 'On Order', 'Inventory Value']], rsuffix='_csv', how='inner')
        diff = j[(j['MDC'] - j['MDC_csv']).abs() > 0.5]
        add('Bid Purchasing', 'M3 MDC stock matches the Purchase Report CSV', level_for(len(diff)), f"{len(diff):,} items differ",
            "Same snapshot." if not len(diff) else "The files hold different stock figures for these items (different export times).")
        diff_o = j[(j['On Order'] - j['On Order_csv']).abs() > 0.5]
        add('Bid Purchasing', 'M3 on-order quantity matches the Purchase Report CSV', level_for(len(diff_o)), f"{len(diff_o):,} items differ", "Same snapshot." if not len(diff_o) else "Different export times.")
        wms = m3_data['wms']
        if wms.empty:
            add('Bid Purchasing', 'WMS stock sheet', 'Review', 'Not found', "MDC stock comes from the Purchase Report instead of the WMS sheet.")
        else:
            w = wms.set_index('Item Number')
            cmp_ = pr.set_index('Item Number')[['MDC', 'Item Description', 'Stock Divisor']].join(w, how='outer')
            for c_ in ('MDC', 'WMS OK Units', 'WMS HOLD Units'):
                cmp_[c_] = cmp_[c_].fillna(0.0)
            cmp_['Diff (units)'] = cmp_['WMS OK Units'] - cmp_['MDC']
            far = cmp_[(cmp_['Diff (units)'].abs() > 1) & (cmp_['Diff (units)'].abs() > 0.05 * cmp_[['MDC', 'WMS OK Units']].max(axis=1))]
            add('Bid Purchasing', 'WMS OK stock agrees with the Purchase Report MDC stock', level_for(len(far)), f"{len(far):,} items differ by more than 5%",
                "The WMS sheet is a floor count by status; the Purchase Report is the M3 balance. Small differences are normal, large ones deserve a look." if len(far) else "They agree.")
            if len(far):
                details['MDC stock: WMS (OK) vs Purchase Report'] = far.reset_index().rename(columns={'index': 'Item Number'})[['Item Number', 'Item Description', 'MDC', 'WMS OK Units', 'WMS HOLD Units', 'Diff (units)']].sort_values('Diff (units)', key=lambda x: x.abs(), ascending=False)
            hold_items = int((w['WMS HOLD Units'] > 0).sum())
            add('Bid Purchasing', 'Stock on HOLD at MDC', 'OK' if hold_items == 0 else 'Review', f"{hold_items:,} items, {float(w['WMS HOLD Units'].sum()):,.0f} units",
                "HOLD stock is not counted as available (switch it on in the Bid Purchasing tab). It may be recoverable for items that are at risk." if hold_items else "None.")
            if meta_m.get('wms_other_status'):
                add('Bid Purchasing', 'WMS stock with a status other than OK / HOLD', 'Review', ", ".join(map(str, meta_m['wms_other_status'])), "These quantities are not counted anywhere.")
        for nm, ids in m3_data['lists'].items():
            missing = ids - a
            ids_in = pr[pr['Item Number'].isin(ids)]
            nosize = ids_in[ids_in['No Case Size'] & ids_in['Item Status'].isin(LIVE_STATUSES)]
            disc = ids_in[~ids_in['Item Status'].isin(LIVE_STATUSES)]
            add('Bid Purchasing', f"'{nm}': items missing from the Purchase Report", level_for(len(missing)), f"{len(missing):,} of {len(ids):,}", "Every listed item is in the Purchase Report." if not missing else "These cannot be rated: " + ", ".join(sorted(missing)[:8]) + ("..." if len(missing) > 8 else ""))
            add('Bid Purchasing', f"'{nm}': active items with no case size", level_for(len(nosize)), f"{len(nosize):,}", "Their stock cannot be turned into cases, so they are 'Not rated'." if len(nosize) else "None.")
            add('Bid Purchasing', f"'{nm}': discontinued or deleted items on the list", 'OK', f"{len(disc):,}", "Shown as 'Not rated' (the weekly sheet marks them 'Discontinued'); the list is under the table below." if len(disc) else "None.")
            if len(disc):
                details[f"'{nm}': discontinued or deleted items"] = disc[['Item Number', 'Item Description', 'Item Status']].copy()
        oo_m3 = (pr['On Order'] / pr['Stock Divisor']).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        oo_po = pr['Item Number'].map(po_active_all.groupby('Item number')['Order Qty (Cases)'].sum()).fillna(0.0)
        bad = pr.assign(M3=oo_m3, PO=oo_po)
        bad = bad[(bad['M3'] - bad['PO']).abs() > np.maximum(1.0, 0.1 * np.maximum(bad['M3'], bad['PO']))]
        add('Bid Purchasing', 'M3 on-order cases agree with the open lines in the PO Dates file', level_for(len(bad)), f"{len(bad):,} items differ by more than 10%",
            "Both sources describe the same open orders." if not len(bad) else "Typical causes: Otp = P01 orders (left out of the PO analysis), or POs not in the PO Dates export. The tab shows both quantities.")
        if len(bad):
            details['On order: M3 vs PO Dates file'] = bad[['Item Number', 'Item Description', 'M3', 'PO']].rename(columns={'M3': 'M3 on order (cs)', 'PO': 'PO file open (cs)'}).sort_values('M3 on order (cs)', ascending=False)
        man = m3_data['manual']
        if len(man):
            allv = pr[pr['Item Number'].isin(man['Item Number'])]
            add('Bid Purchasing', "'manual' sheet items missing from the Purchase Report", level_for(len(man) - len(allv)), f"{len(man) - len(allv):,} of {len(man):,}", "Vendor names and lead times for these items are ignored." if len(man) - len(allv) else "All found.")
    group('Bid Purchasing', m3_checks)

    # ---------------- Vendor terms ----------------
    def terms_checks():
        if vt_df.empty:
            add('Vendor Terms', 'Vendor Terms file loaded', 'Review', 'No', DATA['errors'].get('vt', 'The file has no usable rows. Cash flow is timed from the requested date instead.'))
            return
        meta_v = DATA['meta'].get('vt', {})
        add('Vendor Terms', 'Vendor Terms file loaded', 'OK', f"{len(vt_df):,} suppliers", f"{meta_v.get('rows', 0):,} rows read.")
        op = po_raw[po_raw['Status Code'].between(*OPEN_PO)]
        sup = op.groupby('Supplier')['USD $'].sum().rename('Open USD').reset_index()
        sup = add_terms(sup, 'Supplier')
        known = set(vt_df['Supplier Key'])
        nomatch = sup[~sup['Supplier'].astype(str).str.strip().str.upper().isin(known)]
        add('Vendor Terms', 'Suppliers with open orders that are not in the Vendor Terms file', level_for(len(nomatch)), f"{len(nomatch):,} (${nomatch['Open USD'].sum():,.0f})",
            f"They are timed with an assumed {DEFAULT_TERMS_DAYS} days." if len(nomatch) else "Every supplier with an open order has terms.")
        if len(nomatch):
            details['Open-order suppliers missing from Vendor Terms'] = nomatch[['Supplier', 'Open USD']].sort_values('Open USD', ascending=False)
        unc = sup[(~sup['Terms Confirmed']) & sup['Supplier'].astype(str).str.strip().str.upper().isin(known)]
        add('Vendor Terms', "Suppliers with open orders whose terms code is not a real payment period (e.g. N99)", level_for(len(unc)), f"{len(unc):,} (${unc['Open USD'].sum():,.0f})",
            f"Timed with an assumed {DEFAULT_TERMS_DAYS} days. Ask the vendor-master owner what N99 means, or set the real terms in VENDOR_TERMS_OVERRIDE." if len(unc) else "None.")
        if len(unc):
            details['Open-order suppliers with unconfirmed terms'] = unc[['Supplier', 'Terms Code', 'Open USD']].sort_values('Open USD', ascending=False)
        cf = sup[sup['Supplier'].astype(str).str.strip().str.upper().isin(set(meta_v.get('conflict_keys', [])))]
        add('Vendor Terms', 'Suppliers with open orders that share a search key with another vendor (conflicting terms)', level_for(len(cf)), f"{len(cf):,} (${cf['Open USD'].sum():,.0f})",
            "The Vendor Terms file has more than one company under the same 10-letter search key, with different terms. The most common code is used (the shorter term when tied)." if len(cf) else "None.")
        if len(cf):
            details['Open-order suppliers with conflicting terms'] = cf[['Supplier', 'Terms Code', 'Open USD']].sort_values('Open USD', ascending=False)
        add('Vendor Terms', 'Duplicate search keys in the Vendor Terms file', 'OK', f"{meta_v.get('duplicate_keys', 0):,} rows", "Duplicates are merged: active records first, then the most common code.")
        if meta_v.get('unknown_codes'):
            add('Vendor Terms', 'Terms codes in the file that are not payment periods', 'Review', "; ".join(f"{k}: {v:,}" for k, v in meta_v['unknown_codes'].items()),
                "Suppliers with these codes are timed with an assumed period.")
    group('Vendor Terms', terms_checks)

    # ---------------- Item set-up (typos and gaps in the item master) ----------------
    def item_setup():
        s = sales_df
        act = s[s['Item Status'].isin(LIVE_STATUSES + SELL_ONLY_STATUSES)]
        sale = act[act['Item Class'].isin(SALEABLE_GROUPS)]
        mv = sale[sale['Item Number'].isin(moving_)]
        is_ph = lambda c: mv[c].astype(str).str.strip().str.lower().str.startswith('usr-def') | mv[c].astype(str).str.strip().isin(['', 'nan'])
        cf = mv[is_ph('Category') | is_ph('Format')]
        add('Item set-up', 'Saleable items in use whose Category or Format was never filled in', level_for(len(cf)), f"{len(cf):,} of {len(mv):,}",
            "The item master still holds a placeholder (usr-def). Without a category and format the item cannot be matched to a substitute and its reports are less reliable. Items with no stock, sales or orders are not counted here." if len(cf) else "Every saleable item in use has a category and format.")
        if len(cf):
            details['Items whose Category or Format is still a placeholder'] = cf[['Item Number', 'Item Description', 'Item Class', 'Category', 'Format', 'Flavor', 'Brand']].assign(
                Sales=cf['3M_Avg_Depletion_Demand']).sort_values('Sales', ascending=False).rename(columns={'Sales': 'Avg monthly sales (cs)'}).head(1000)
            flag('Category or Format not filled in', cf.sort_values('3M_Avg_Depletion_Demand', ascending=False).head(500), detail=cf.sort_values('3M_Avg_Depletion_Demand', ascending=False).head(500)['Category'].astype(str) + ' / ' + cf.sort_values('3M_Avg_Depletion_Demand', ascending=False).head(500)['Format'].astype(str))
        nb = mv[mv['Brand'].astype(str).str.strip().isin(['', 'nan']) | mv['Brand'].astype(str).str.lower().str.startswith('usr-def')]
        add('Item set-up', 'Saleable items in use with no Brand', level_for(len(nb)), f"{len(nb):,} of {len(mv):,}",
            "The brand is part of the substitute match score and of the similar-item check." if len(nb) else "Every saleable item in use has a brand.")
        if len(nb):
            details['Saleable items in use with no Brand'] = nb[['Item Number', 'Item Description', 'Item Class', 'Category', 'Format']].head(1000)
            flag('Brand missing', nb.sort_values('3M_Avg_Depletion_Demand', ascending=False).head(300), detail='')
        def _is_cut(d_):
            m_ = _CUT_RE.search(d_)
            return bool(m_) and not re.search(r'\d\s*(KGS?|LBS?|OZ)\s*$', d_[:m_.start()].rstrip())      # '22.68KG AVG' is complete; 'CAB AVG' / 'AVG 4' are cut
        cut = act[act['Item Description'].astype(str).str.upper().map(_is_cut)]
        add('Item set-up', 'Descriptions that stop in the middle of a weight', level_for(len(cut)), f"{len(cut):,}",
            "Example: '...AVG 11.' - the item master holds 60 characters, so the weight was cut off. The case weight cannot be read from the description." if len(cut) else "None.")
        if len(cut):
            details['Descriptions cut off in the middle of a weight'] = cut[['Item Number', 'Item Description', 'Item Class', 'Item Status', 'Case Basis']]
            flag('Description cut off', cut, detail=cut['Item Description'].astype(str).str.slice(-24))
        sp = act[(act['Item Description'] != act['Item Description'].str.strip()) | act['Item Description'].str.contains(r'\s{2,}', regex=True)]
        add('Item set-up', 'Descriptions with double or stray spaces', 'Note', f"{len(sp):,}", "Cosmetic, but a stray space can hide a duplicate item from a simple search. The similar-item check already looks past spacing.")
        lt = act[(act['Lead time'] > 150) & act['Item Class'].isin(SALEABLE_GROUPS)]
        add('Item set-up', 'Saleable items with a lead time over 150 days', level_for(len(lt)), f"{len(lt):,}",
            "Probably a typing slip. A long lead time inflates safety stock." if len(lt) else "None.")
        if len(lt):
            details['Items with a lead time over 150 days'] = lt[['Item Number', 'Item Description', 'Item Class', 'Lead time', 'Supplier Name']].sort_values('Lead time', ascending=False)
            flag('Lead time looks too long', lt, detail=lt['Lead time'].map(lambda v: f"{v:,.0f} days").astype(str), moving_only=True)
        op = po_active_all.merge(s[['Item Number', 'Purchasing UM']], left_on='Item number', right_on='Item Number', how='left')
        um = op[op['Purchasing UM'].notna() & (op['POu'] != op['Purchasing UM'])]
        add('Item set-up', "Open order lines whose PO unit differs from the item's Purchasing U/M", 'Note', f"{len(um):,} of {len(op):,}",
            "Not always wrong (the PO unit can be changed per order), but it is where unit mistakes start: 100 'EA' typed where 100 'CS' was meant is a 12-fold error. The dashboard converts using the PO unit as typed.")
        if len(um):
            details["Open order lines whose PO unit differs from the item's Purchasing U/M"] = um[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Order qty', 'POu', 'Purchasing UM', 'Order Qty (Cases)']].head(500)
        un = inv[(inv['Responsible Buyer'] == 'Unassigned') & inv['Item Number'].isin(moving_) & inv['Item Status'].isin(LIVE_STATUSES + SELL_ONLY_STATUSES)]
        add('Item set-up', 'Items in use that no buyer is responsible for', 'Note', f"{len(un):,}",
            "Item groups outside the buyer list (operations, point-of-sale, packaging). They appear as 'Unassigned' on the buyer action list below." if len(un) else "None.")
    group('Item set-up', item_setup)

    # ---------------- Similar items in the item master ----------------
    def similar():
        s = sales_df[sales_df['Item Status'].isin(LIVE_STATUSES + SELL_ONLY_STATUSES)]
        cols = ['Item Number', 'Item Description', 'Item Class', 'Category', 'Format', 'Flavor', 'Brand', 'Unit Size', 'Case Size']
        grp, packs = similar_items_cached(s[cols].reset_index(drop=True))
        if grp.empty:
            add('Similar items', 'Items that are too similar', 'OK', '0', "No two active items have the same or nearly the same description.")
            extras['similar'] = grp
            extras['similar_packs'] = packs
            return
        # add the facts a buyer needs to decide which item to keep
        key = inv.set_index('Item Number')
        g = grp.copy()
        g['Status'] = g['Item Number'].map(key['Item Status'])
        g['Stock (cs)'] = g['Item Number'].map(key['Total_Cases'])
        g['Avg monthly sales (cs)'] = g['Item Number'].map(key['3M_Avg_Depletion_Demand'])
        g['On order (cs)'] = g['Item Number'].map(key['Cases On Order'])
        g['Last purchase'] = g['Item Number'].map(key['Last Purchase Date'])
        g['Buyer'] = g['Item Number'].map(key['Responsible Buyer'])
        g['_act'] = g['Avg monthly sales (cs)'].fillna(0) * 1000 + g['Stock (cs)'].fillna(0) + (g['Status'] == 20) * 0.5
        g = g.sort_values(['Group', '_act', 'Item Number'], ascending=[True, False, True], kind='stable')
        g['Suggestion'] = np.where(g.groupby('Group').cumcount() == 0, 'Keep (most active)', 'Check: merge or switch off?')
        g = g.drop(columns=['_act', '_pos'], errors='ignore')
        extras['similar'] = g
        extras['similar_packs'] = packs
        for tier, nm, hint in [('Identical wording', 'Items with identical wording', "The same description is set up under more than one item number. Open the 'Similar items' sub-tab to see each group side by side."),
                               ('Near-identical (spelling, plural or abbreviation)', 'Items that differ only by a spelling slip, plural or short form', "Probably the same product entered twice."),
                               ('Same product details, different wording', 'Items with the same product details but worded differently', "Same group, category, format, flavor, brand, unit size and case size. Confirm whether it is one product set up twice (or a pack variant that needs a clearer description).")]:
            sub = g[g['Match type'] == tier]
            ng, ni = sub['Group'].nunique(), len(sub)
            add('Similar items', nm, level_for(ng), f"{ng:,} groups ({ni:,} items)", hint if ng else "None.")
            issue = {'Identical wording': 'Duplicate item (identical wording)', 'Near-identical (spelling, plural or abbreviation)': 'Duplicate item (spelling differs)',
                     'Same product details, different wording': 'Duplicate item (same details, different wording)'}[tier]
            if ni:
                flag(issue, sub, detail='Group ' + sub['Group'].astype(str) + ' - ' + sub['Suggestion'], buyer=sub['Buyer'].tolist())
        npk = packs['Group'].nunique() if len(packs) else 0
        add('Similar items', 'Same wording but a different size or pack (usually genuine variants)', 'Note', f"{npk:,} groups",
            "For example 12/750ML and 6/750ML of the same wine. Normally fine; listed in the Similar items sub-tab so you can check the descriptions make the difference clear.")
    group('Similar items', similar)

    # ---------------- Item status rules ----------------
    def statuses():
        s = sales_df
        known = set(LIVE_STATUSES) | set(SELL_ONLY_STATUSES) | set(HARD_DISC_STATUSES) | set(DELETED)
        odd = s[~s['Item Status'].isin(known)]
        add('Item status', 'Items with a status code the dashboard does not know', level_for(len(odd)), f"{len(odd):,}" + (" (" + ", ".join(str(int(x)) for x in sorted(odd['Item Status'].dropna().unique())) + ")" if len(odd) else ""),
            f"Known: {LIVE_STATUSES[0]} active, {SELL_ONLY_STATUSES[0]} sell-only, {HARD_DISC_STATUSES[0]} no selling / buying, 0-14 and 86-99 not calculated. Anything else is ignored." if len(odd) else "Every item uses a known status.")
        if len(odd):
            details['Items with an unknown status code'] = odd[['Item Number', 'Item Description', 'Item Status', 'Item Class']]
            flag('Unknown item status code', odd, detail=odd['Item Status'].astype(str), moving_only=True)
        hard = s[s['Item Status'].isin(HARD_DISC_STATUSES)]
        held = hard[hard[ONHAND_SITES].clip(lower=0).sum(axis=1) > 0]
        add('Item status', 'Status 80 items that still hold stock', level_for(len(held)), f"{len(held):,} items, ${held['Inventory Value'].sum():,.0f}",
            "Status 80 items are left out of every calculation, so this stock is invisible in the cover, turnover and bid figures. Sell it through, transfer it or change the status." if len(held) else "None.")
        if len(held):
            details['Status 80 items that still hold stock'] = held[['Item Number', 'Item Description', 'Item Class'] + ONHAND_SITES + ['Inventory Value']].sort_values('Inventory Value', ascending=False)
            flag('Stock on an item switched off', held, detail='$' + held['Inventory Value'].map(lambda v: f"{v:,.0f}").astype(str) + ' of stock', moving_only=False)
        lines = po_active_all.merge(s[['Item Number', 'Item Status']].rename(columns={'Item Status': 'Master Status'}), left_on='Item number', right_on='Item Number', how='left')
        setup = lines[lines['Master Status'].between(0, 14)]
        add('Item status', 'Open orders on items still in set-up status (0-14)', level_for(len(setup)), f"{len(setup):,} lines, {setup['Item number'].nunique():,} items (${setup['USD $'].sum():,.0f})",
            "Status 0-14 items are left out of every calculation. When these orders arrive the goods will not appear in cover, turnover or bid figures until the item is activated (status 20)." if len(setup) else "None.")
        if len(setup):
            details['Open orders on items still in set-up status (0-14)'] = setup[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Master Status', 'Req dt', 'Order qty', 'USD $']].sort_values('USD $', ascending=False)
            flag('Open order on an item not yet active', setup, item_col='Item number', detail='PO ' + setup['PO no'].astype(str) + ' (' + setup['USD $'].map(lambda v: f"${v:,.0f}").astype(str) + '), status ' + setup['Master Status'].astype('Int64').astype(str),
                 desc=setup['ItemDescription'].tolist())
        off = lines[lines['Master Status'].isin(HARD_DISC_STATUSES) | lines['Master Status'].between(86, 99)]
        add('Item status', 'Open orders on items blocked from buying (status 80, or deleted 86-99)', level_for(len(off)), f"{len(off):,} lines (${off['USD $'].sum():,.0f})",
            "Status 80 means no selling and no buying. An order on such an item should be cancelled, or the status corrected." if len(off) else "None.")
        if len(off):
            details['Open orders on items blocked from buying'] = off[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Master Status', 'Order qty', 'USD $']].sort_values('USD $', ascending=False)
            flag('Open order on a switched-off item', off, item_col='Item number', detail='PO ' + off['PO no'].astype(str) + ' line ' + off['Ln'].astype(str) + ', status ' + off['Master Status'].astype('Int64').astype(str),
                 desc=off['ItemDescription'].tolist())
        trade = s[s['Item Status'].between(0, 14) & ((s[ONHAND_SITES].clip(lower=0).sum(axis=1) > 0) | (s['3M_Avg_Depletion'] > 0))]
        add('Item status', 'Items in set-up status (0-14) that already hold stock or sell', level_for(len(trade)), f"{len(trade):,} items, ${trade['Inventory Value'].sum():,.0f}",
            "These items are ignored by every report although they are trading. Activate them (status 20) or move the stock." if len(trade) else "None.")
        if len(trade):
            details['Items in set-up status (0-14) that hold stock or sell'] = trade[['Item Number', 'Item Description', 'Item Class', 'Item Status'] + ONHAND_SITES + ['Inventory Value']].sort_values('Inventory Value', ascending=False)
            flag('Item in set-up status but already trading', trade, detail='Status ' + trade['Item Status'].astype('Int64').astype(str) + ', $' + trade['Inventory Value'].map(lambda v: f"{v:,.0f}").astype(str) + ' of stock')
        so = lines[lines['Master Status'].isin(SELL_ONLY_STATUSES)]
        add('Item status', 'Open orders on sell-only items (status 50)', 'Review' if len(so) else 'OK', f"{len(so):,} lines (${so['USD $'].sum():,.0f})",
            "Status 50 is meant to be sold but no longer bought. If these orders are intended, the status may be wrong; if not, they should be cancelled. (Your notes say 50 means 'we can buy', the dashboard treats it as not purchased: please confirm.)" if len(so) else "None.")
        if len(so):
            details['Open orders on sell-only items (status 50)'] = so[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Order qty', 'USD $']].sort_values('USD $', ascending=False)
            flag('Sell-only item on an open order', so, item_col='Item number', detail='PO ' + so['PO no'].astype(str) + ' line ' + so['Ln'].astype(str), desc=so['ItemDescription'].tolist())
        recent = po_df[po_df['Ord dt'] >= today - pd.Timedelta(days=90)].merge(s[['Item Number', 'Item Status']].rename(columns={'Item Status': 'Master Status'}), left_on='Item number', right_on='Item Number', how='left')
        bought50 = recent[recent['Master Status'].isin(SELL_ONLY_STATUSES)]
        add('Item status', 'Sell-only items (status 50) ordered in the last 90 days', 'Note', f"{bought50['Item number'].nunique():,} items, {len(bought50):,} lines",
            "Tells you whether status 50 items really are still being bought. If many are, the 'we can buy' reading of your note may be the right one." if len(bought50) else "No status 50 item was ordered recently, which fits 'sold but not bought'.")
        om = s[s['OM'] > 0] if 'OM' in s.columns else s.iloc[0:0]
        add('Item status', 'Stock held at OM (never counted as available)', 'Note', f"{len(om):,} items, ${om['Val_OM'].sum():,.0f}",
            "OM stock is kept out of Total On-Hand, bid availability and every cover figure. This is its dollar value for reference." if len(om) else "None.")
        if len(om):
            details['Items with stock at OM'] = om[['Item Number', 'Item Description', 'Item Class', 'Item Status', 'OM', 'Val_OM']].sort_values('Val_OM', ascending=False).head(500)
    group('Item status', statuses)

    # ---------------- Samples ----------------
    def samples():
        n_codes = len(DATA.get('sample_codes', set()) or set())
        sl = DATA.get('sample_lines', pd.DataFrame())
        add('Samples', 'Sample items and lines left out of every analysis', 'Note', f"{n_codes:,} codes, {len(sl):,} PO lines (${meta_p.get('sample_usd', 0):,.0f})",
            "An item or PO line whose description contains the word SAMPLE is excluded from all reports, so samples never distort stock, spend or prices.")
        if len(sl):
            details['Sample PO lines that were excluded'] = sl.head(500)
    group('Samples', samples)

    # ---------------- Containers ----------------
    def containers():
        raw = po_raw[po_raw['Container Flag'] != '']
        bad = raw[raw['Container Flag'].isin(['Not a container number', 'Wrong length (typo?)', 'Several containers in one cell'])]
        sez = raw[raw['Container Flag'] == 'SEZ / transfer / internal']
        add('Containers', 'Container field holds text that is not a container number', level_for(len(bad)), f"{len(bad):,} lines, {bad['Container Raw'].nunique():,} different values",
            "Numbers such as 5407059195 or 'C9158 C11287' are not containers (a container is 4 letters + 7 digits). These lines are not counted in the container figures." if len(bad) else "None.")
        if len(bad):
            details['Container field values that are not container numbers'] = bad.groupby(['Container Raw', 'Container Flag']).agg(
                Lines=('PO no', 'size'), Example_PO=('PO no', 'first'), Supplier=('Supplier', 'first')).reset_index().sort_values('Lines', ascending=False).rename(columns={'Container Raw': 'Typed in the container field', 'Container Flag': 'Why not counted', 'Example_PO': 'Example PO'}).head(300)
        add('Containers', 'SEZ transfers, internal sales and pulls typed in the container field', 'Note', f"{len(sez):,} lines",
            "Expected: these are movements inside the group, not shipments, so they are never counted as containers." if len(sez) else "None.")
        ck = po_raw[(po_raw['Container ID'] != '') & ~po_raw['Container Check Digit OK']]
        n_ids = ck['Container ID'].nunique()
        add('Containers', 'Container numbers that fail the check-digit test (likely a mistyped digit)', level_for(n_ids), f"{n_ids:,} containers, {len(ck):,} lines",
            "Every real container number ends with a check digit worked out from the other ten characters. A number that fails it has almost certainly been mistyped. The last digit it should have is shown in the list." if n_ids else "Every container number passes.")
        if n_ids:
            t = ck.groupby('Container ID').agg(Lines=('PO no', 'size'), POs=('PO no', lambda x: ', '.join(map(str, sorted(set(x))[:4]))), Supplier=('Supplier', 'first'), Arrival=('Arrival', 'min')).reset_index()
            t['Last digit should be'] = t['Container ID'].map(iso_check_digit)
            t['Typed last digit'] = t['Container ID'].str[-1]
            details['Container numbers that fail the check-digit test'] = t.sort_values('Lines', ascending=False)
        # compare with the counts kept by hand
        ship = po_raw[po_raw['Status Code'].between(*PO_LIVE) & (po_raw['Shipment Key'] != '')]
        if len(ship) and MANUAL_CONTAINER_COUNTS:
            sh = ship.groupby('Shipment Key').agg(Container=('Container ID', 'first'), Yard=('Yard Date', 'min'), Stripped=('Stripped Date', 'min')).reset_index()
            rows = []
            for m_ in sorted(MANUAL_CONTAINER_COUNTS):
                if pd.Period(m_, freq='M') >= pd.Period(today, freq='M'):
                    continue
                man_str, man_clr = MANUAL_CONTAINER_COUNTS[m_]
                my_clr = sh.loc[sh['Yard'].notna() & (sh['Yard'].dt.strftime('%Y-%m') == m_), 'Container'].nunique()
                my_str = sh.loc[sh['Stripped'].notna() & (sh['Stripped'].dt.strftime('%Y-%m') == m_), 'Container'].nunique()
                rows.append({'Month': m_, 'Cleared (Yard) - dashboard': my_clr, 'Cleared - yours': man_clr, 'Cleared gap': my_clr - man_clr,
                             'Stripped - dashboard': my_str, 'Stripped - yours': man_str, 'Stripped gap': my_str - man_str})
            cmp_ = pd.DataFrame(rows)
            if len(cmp_):
                avg_c, avg_s = cmp_['Cleared gap'].abs().mean(), cmp_['Stripped gap'].abs().mean()
                rel_c = avg_c / max(cmp_['Cleared - yours'].mean(), 1) * 100
                rel_s = avg_s / max(cmp_['Stripped - yours'].mean(), 1) * 100
                worst = max((cmp_['Cleared gap'].abs() / cmp_['Cleared - yours'].clip(lower=1)).max(), (cmp_['Stripped gap'].abs() / cmp_['Stripped - yours'].clip(lower=1)).max()) * 100
                far = cmp_[((cmp_['Cleared gap'].abs() / cmp_['Cleared - yours'].clip(lower=1)) > 0.25) | ((cmp_['Stripped gap'].abs() / cmp_['Stripped - yours'].clip(lower=1)) > 0.25)]
                ok_ = (max(rel_c, rel_s) <= 20) and (worst <= 35)
                add('Containers', 'Container counts against your own monthly counts', 'OK' if ok_ else 'Review',
                    f"Average gap {avg_c:,.1f} cleared ({rel_c:,.0f}%) and {avg_s:,.1f} stripped ({rel_s:,.0f}%) containers a month; worst month {worst:,.0f}%",
                    "The dashboard counts each real container number once, by yard date (cleared) and by stripped date. A gap of a few containers a month is normal (a container counted in the month before or after, a mistyped number). "
                    "A large gap suggests container numbers are missing or mistyped, or your own columns are the other way round." if not ok_ else
                    "Close to your own counts: the remaining gap of a few containers a month is the usual month-boundary and typing noise. Monthly detail is under 'Lists behind the checks'.")
                extras['containers_manual'] = cmp_
                if len(far):
                    details['Months where the container counts differ from yours'] = far
        # dates without a container number
        nocont = po_df[(po_df['Container ID'] == '') & (po_df['Container Flag'] == '') & (po_df['Stripped Date'].notna() | po_df['Yard Date'].notna()) & (po_df['Otp'] != 'P01')]
        add('Containers', 'Lines with a yard or stripped date but no container number', 'Note', f"{len(nocont):,} lines",
            "Either the container number was never typed in, or the dates were entered on a local purchase. These lines cannot be counted as containers." if len(nocont) else "None.")
        if len(nocont):
            details['Lines with a yard or stripped date but no container number'] = nocont[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Arrival', 'Yard Date', 'Stripped Date']].head(300)
    group('Containers', containers)

    # ---------------- Prices ----------------
    def prices():
        pairs, _ = price_pairs(PRICE_STATUS_CODES)
        hb = pairs[pairs['Held Back Because'] != '']
        short_ = {'A price of': 'placeholder price', 'Currency': 'currency changed', 'The earlier': 'earlier price too old', 'Move above': 'move over the limit'}
        reasons = hb['Held Back Because'].map(lambda t_: next((v for k, v in short_.items() if str(t_).startswith(k)), 'other')).value_counts()
        add('Prices', 'Price moves held back from the Price Index', level_for(len(hb)), f"{len(hb):,} of {len(pairs):,}" + (": " + "; ".join(f"{k} {v}" for k, v in reasons.items()) if len(hb) else ""),
            "A price that jumps by more than the limit, comes after a $0 / $1 placeholder, switches currency or follows a very old price is not counted as a price increase. Check each one: a typing slip or unit-of-measure mistake is the usual cause." if len(hb) else "No price move was held back.")
        if len(hb):
            t = hb[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Ord dt', 'POu', 'Curr', 'Prev Price', 'Price USD', 'Chg %', 'Held Back Because']].sort_values('Ord dt', ascending=False)
            details['Price moves held back'] = t.head(800)
            recent_hb = hb[hb['Ord dt'] >= today - pd.Timedelta(days=120)]
            flag('Price move held back', recent_hb, item_col='Item number', detail='PO ' + recent_hb['PO no'].astype(str) + ': $' + recent_hb['Prev Price'].map(lambda v: f"{v:,.2f}").astype(str) + ' -> $' + recent_hb['Price USD'].map(lambda v: f"{v:,.2f}").astype(str),
                 desc=recent_hb['ItemDescription'].tolist())
        sale_open = po_active_all[po_active_all['Item grp'].isin(SALEABLE_GROUPS) & po_active_all['Status Code'].between(20, 40) & (po_active_all['Order qty'] > 0)]
        ph = sale_open[((sale_open['Price USD'] <= PRICE_PLACEHOLDER_USD) & (sale_open['POu'] == 'CS') & ~sale_open['Item grp'].isin(PLANT_GROUPS)) | (sale_open['Price USD'] < 0.01)]
        add('Prices', f"Open order lines priced like a placeholder (a whole case for ${PRICE_PLACEHOLDER_USD:,.0f} or less, or under 1 cent)", level_for(len(ph)), f"{len(ph):,} lines",
            "A $0 or $1 case price is normally a placeholder waiting for the real price. It makes the order look free in cash flow and spend. Packaging that is genuinely cheap per piece is not counted." if len(ph) else "None.")
        if len(ph):
            details['Open order lines priced at the placeholder level'] = ph[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Order qty', 'POu', 'Purch price', 'Curr', 'Price USD']].head(500)
            flag('Price placeholder on an open order', ph, item_col='Item number', detail='PO ' + ph['PO no'].astype(str) + ' priced at $' + ph['Price USD'].map(lambda v: f"{v:,.2f}").astype(str), desc=ph['ItemDescription'].tolist())
        ref = po_df[po_df['Status Code'].isin([75, 85]) & po_df['Item grp'].isin(SALEABLE_GROUPS) & (po_df['Price USD'] > PRICE_PLACEHOLDER_USD) & (po_df['Ord dt'] >= today - pd.Timedelta(days=365))]
        usual = ref.groupby(['Item number', 'POu']).agg(Usual=('Price USD', 'median'), N=('Price USD', 'size')).reset_index()
        usual = usual[usual['N'] >= 2]
        chk_ = sale_open[sale_open['Price USD'] > PRICE_PLACEHOLDER_USD].merge(usual, on=['Item number', 'POu'], how='inner')
        chk_['Times usual'] = chk_['Price USD'] / chk_['Usual']
        odd = chk_[(chk_['Times usual'] >= 3) | (chk_['Times usual'] <= 1 / 3)]
        add('Prices', "Open order lines priced 3x higher or lower than the item's usual price", level_for(len(odd)), f"{len(odd):,} of {len(chk_):,} lines checked",
            "Compared with the median price of the same item and PO unit on orders received in the last 12 months (at least 2 orders). A big gap on a NEW order is the best moment to catch a typo, before the goods arrive." if len(odd) else "No open order is priced out of line.")
        if len(odd):
            details["Open order lines priced out of line with the item's usual price"] = odd[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'POu', 'Curr', 'Price USD', 'Usual', 'Times usual', 'Order qty']].rename(
                columns={'Usual': 'Usual price (USD)', 'Price USD': 'This order (USD)'}).sort_values('Times usual', ascending=False)
            flag('Order price far from the usual price', odd, item_col='Item number', detail='PO ' + odd['PO no'].astype(str) + ': $' + odd['Price USD'].map(lambda v: f"{v:,.2f}").astype(str) + ' vs usual $' + odd['Usual'].map(lambda v: f"{v:,.2f}").astype(str),
                 desc=odd['ItemDescription'].tolist())
    group('Prices', prices)

    # ---------------- Early warnings (act before it happens) ----------------
    def early():
        ew = inv[inv['Is Live'] & ~inv['Custom Category'].isin(NO_SS_CATS) & (inv['Monthly Demand Used (Cases)'] >= 1.0)].copy()
        low_retail = (ew['Retail PO Share'] >= RETAIL_SHARE_MIN) & (ew['Last PO Cost per Case (USD)'] < RETAIL_LOW_VALUE_USD)
        ew = ew[~low_retail]
        nothing = ew['Cases Inbound'] <= 0
        out_now = ew[(ew['WH_Cases'] <= 0) & nothing]
        below = ew[(ew['WH_Cases'] <= ew['Safety Stock (Cases)']) & (ew['Safety Stock (Cases)'] > 0) & nothing & ~ew['Item Number'].isin(out_now['Item Number'])]
        cols = ['Item Number', 'Item Description', 'Custom Category', 'Responsible Buyer', 'WH_Cases', 'Monthly Demand Used (Cases)', 'Safety Stock (Cases)', 'Lead Time (Months)', 'SS Basis']
        ren = {'WH_Cases': 'Warehouse stock (cs)', 'Monthly Demand Used (Cases)': 'Monthly demand (cs)', 'Safety Stock (Cases)': 'Safety stock (cs)', 'Lead Time (Months)': 'Lead time (months)'}
        add('Early warnings', 'Selling items with no stock left and nothing on order', level_for(len(out_now)), f"{len(out_now):,} of {len(ew):,} selling items",
            "Warehouse stock is zero, the item sells at least 1 case a month and no order or container is on the way. Low-value items bought for the retail sites are left out, as on the Inventory Health tab." if len(out_now) else "None.")
        if len(out_now):
            details['Selling items with no stock and nothing on order'] = out_now[cols].rename(columns=ren).sort_values('Monthly demand (cs)', ascending=False)
            flag('Out of stock, selling, nothing inbound', out_now.sort_values('Monthly Demand Used (Cases)', ascending=False), detail='Sells ' + out_now.sort_values('Monthly Demand Used (Cases)', ascending=False)['Monthly Demand Used (Cases)'].map(lambda v: f"{v:,.0f}").astype(str) + ' cs / month')
        add('Early warnings', 'Items at or below safety stock with nothing on order', level_for(len(below)), f"{len(below):,} of {len(ew):,} selling items",
            "Stock is below what the lead time needs and no replenishment is coming. These will run out unless a buyer acts. (Items already at zero are in the row above.)" if len(below) else "None.")
        if len(below):
            details['Items at or below safety stock with nothing on order'] = below[cols].rename(columns=ren).sort_values('Warehouse stock (cs)')
            b_ = below.sort_values('Monthly Demand Used (Cases)', ascending=False)
            flag('Below safety stock, nothing inbound', b_, detail=b_['WH_Cases'].map(lambda v: f"{v:,.0f}").astype(str) + ' cs left, safety stock ' + b_['Safety Stock (Cases)'].map(lambda v: f"{v:,.0f}").astype(str))
        cover = ew.set_index('Item Number')
        late = po_active_all[(po_active_all['Delivery Status'] == 'Late (Past Req Date)') & po_active_all['Item number'].isin(cover.index)].copy()
        late['Warehouse stock (cs)'] = late['Item number'].map(cover['WH_Cases'])
        late['Monthly demand (cs)'] = late['Item number'].map(cover['Monthly Demand Used (Cases)'])
        late['Months of cover'] = late['Warehouse stock (cs)'].clip(lower=0) / late['Monthly demand (cs)']
        late['Days late'] = (today - late['Req dt']).dt.days
        late = late[late['Months of cover'] < 1]
        add('Early warnings', 'Late orders on items with under a month of stock', level_for(len(late)), f"{len(late):,} lines",
            "The order is past its requested date and the warehouse holds less than a month of sales. This is the order to chase first." if len(late) else "None.")
        if len(late):
            details['Late orders on items with under a month of stock'] = late[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Req dt', 'Days late', 'Status', 'Warehouse stock (cs)', 'Monthly demand (cs)', 'Months of cover']].sort_values('Days late', ascending=False)
            flag('Late order on an item about to run out', late, item_col='Item number', detail='PO ' + late['PO no'].astype(str) + ', ' + late['Days late'].astype(str) + ' days late, ' + late['Months of cover'].map(lambda v: f"{v:.1f}").astype(str) + ' months of stock',
                 desc=late['ItemDescription'].tolist())
        dm = inv.set_index('Item Number')['Monthly Demand Used (Cases)']
        oc = po_active_all[po_active_all['Convertible'] & (po_active_all['Order Qty (Cases)'] > 0) & ~po_active_all['Item grp'].isin(PLANT_GROUPS)].copy()
        oc['Monthly demand (cs)'] = oc['Item number'].map(dm)
        big = oc[(oc['Monthly demand (cs)'] >= 1.0) & (oc['Order Qty (Cases)'] > np.maximum(20, 12 * oc['Monthly demand (cs)']))].copy()
        big['Months of demand ordered'] = big['Order Qty (Cases)'] / big['Monthly demand (cs)']
        add('Early warnings', 'Open orders for more than a year of normal sales', level_for(len(big)), f"{len(big):,} of {len(oc):,} lines",
            "Often a genuine bulk or promotional buy, but this is also what an extra zero or cases-versus-each slip looks like. Worth a second look before the goods arrive." if len(big) else "None.")
        if len(big):
            details['Open orders for more than a year of normal sales'] = big[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Order qty', 'POu', 'Order Qty (Cases)', 'Monthly demand (cs)', 'Months of demand ordered']].sort_values('Months of demand ordered', ascending=False).head(500)
            flag('Order quantity looks too large', big.sort_values('Months of demand ordered', ascending=False).head(200), item_col='Item number',
                 detail='PO ' + big.sort_values('Months of demand ordered', ascending=False).head(200)['PO no'].astype(str) + ': ' + big.sort_values('Months of demand ordered', ascending=False).head(200)['Months of demand ordered'].map(lambda v: f"{v:,.0f}").astype(str) + ' months of demand',
                 desc=big.sort_values('Months of demand ordered', ascending=False).head(200)['ItemDescription'].tolist())
        op = po_active_all
        inv_dates = op[op['Req dt'].notna() & op['Ord dt'].notna() & (op['Req dt'] < op['Ord dt'] - pd.Timedelta(days=1))]
        add('Early warnings', 'Open order lines requested before they were ordered', level_for(len(inv_dates)), f"{len(inv_dates):,}",
            "The requested date is earlier than the order date, which is usually a mistyped year or month. The line then shows as late." if len(inv_dates) else "None.")
        if len(inv_dates):
            details['Open order lines requested before they were ordered'] = inv_dates[['PO no', 'Ln', 'Supplier', 'Item number', 'ItemDescription', 'Ord dt', 'Req dt']].head(500)
            flag('Order dates out of order', inv_dates, item_col='Item number', detail='PO ' + inv_dates['PO no'].astype(str) + ': ordered ' + inv_dates['Ord dt'].dt.strftime('%d %b %Y') + ', requested ' + inv_dates['Req dt'].dt.strftime('%d %b %Y'),
                 desc=inv_dates['ItemDescription'].tolist())
        fcm = inv[inv['Is Live'] & inv['Has Forecast'] & ~inv['Custom Category'].isin(NO_SS_CATS) & (inv['3M_Avg_Depletion_Demand'] >= 2) & (inv['3M_Avg_Forecast_Cases'] >= 2)].copy()
        fcm['Forecast / sales'] = fcm['3M_Avg_Forecast_Cases'] / fcm['3M_Avg_Depletion_Demand']
        off = fcm[(fcm['Forecast / sales'] >= 10) | (fcm['Forecast / sales'] <= 0.1)]
        add('Early warnings', 'Items whose forecast is 10x higher or lower than recent sales', level_for(len(off)), f"{len(off):,} of {len(fcm):,} items",
            "A forecast that far from what the item actually sells is often a typing slip or the wrong unit in the forecast file. It drives safety stock, so check it." if len(off) else "None.")
        if len(off):
            details['Items whose forecast is 10x off recent sales'] = off[['Item Number', 'Item Description', 'Custom Category', 'Responsible Buyer', '3M_Avg_Depletion_Demand', '3M_Avg_Forecast_Cases', 'Forecast / sales']].rename(
                columns={'3M_Avg_Depletion_Demand': 'Avg monthly sales (cs)', '3M_Avg_Forecast_Cases': 'Avg monthly forecast (cs)'}).sort_values('Forecast / sales', ascending=False)
            flag('Forecast far from recent sales', off, detail='Forecast ' + off['3M_Avg_Forecast_Cases'].map(lambda v: f"{v:,.0f}").astype(str) + ' vs sales ' + off['3M_Avg_Depletion_Demand'].map(lambda v: f"{v:,.0f}").astype(str) + ' cs / month')
    group('Early warnings', early)

    # ---------------- Known-answer self tests ----------------
    def selftests():
        tests = [("FISH, PROCESSED SALMON COMMERCIAL SMOKED 2-3 AVG 20KG", 20.0), ("SAUCE 8/2.5KG", 20.0), ("FISH, SNAPPER RED WG 1-2LB IVP AVG 11.34KG", 11.34),
                 ("CHEESE, PARMESAN WHEEL", None), ("SHRIMP 2-3LBS", None)]
        fails = []
        for d_, exp in tests:
            got = parse_case_weight_kg(d_)
            ok = (np.isnan(got) if exp is None else (not np.isnan(got) and abs(got - exp) < 0.01))
            if not ok:
                fails.append(f"'{d_}' gave {got}")
        add('Self tests', 'Description-weight reader gives the known answers', 'OK' if not fails else 'Problem', f"{len(tests) - len(fails)} of {len(tests)} passed", "; ".join(fails) or "Reads 'AVG 20KG', '8/2.5KG' and 'AVG 11.34KG' correctly and ignores piece sizes.")
        t = pd.DataFrame({'Item Description': ['FISH, PROCESSED SALMON COMMERCIAL SMOKED 2-3 AVG 20KG', 'SPREAD JAM MANGO 12/280ML', 'RICE LONG GRAIN 24/2LB'],
                          'Base U/M': ['CS', 'EA', 'CS'], 'Alt U/M': ['KG', 'CS', 'EA'], 'Conversion Factor': [24.0, 12.0, 24.0]})
        u = build_unit_table(t)
        got = [2000.0 / u.loc[0, 'Stock Divisor'], 120.0 / u.loc[1, 'Stock Divisor'], 50.0 / u.loc[2, 'Stock Divisor']]
        exp = [100.0, 10.0, 50.0]
        bad = [f"example {i + 1}: {g:,.1f} cases, expected {e:,.0f}" for i, (g, e) in enumerate(zip(got, exp)) if not abs(g - e) < 0.01]
        add('Self tests', 'Case conversion worked examples', 'OK' if not bad else 'Problem', f"{3 - len(bad)} of 3 passed",
            "; ".join(bad) or "2,000 KG of the salmon item (20 KG per case from the description, Conversion Factor 24 ignored) = 100 cases; 120 each of a 12-pack = 10 cases; a case-based item stays in cases.")
    group('Self tests', selftests)

    issues = pd.concat(issue_parts, ignore_index=True) if issue_parts else pd.DataFrame(columns=['Item Number', 'Description', 'Buyer', 'Detail', 'Issue'])
    if len(issues):
        issues['Priority'] = issues['Issue'].map(lambda k: DC_ISSUES.get(k, ('3 - Fix the data', ''))[0])
        issues['What to do'] = issues['Issue'].map(lambda k: DC_ISSUES.get(k, ('', ''))[1])
        issues = issues[['Priority', 'Issue', 'Buyer', 'Item Number', 'Description', 'Detail', 'What to do']].sort_values(['Priority', 'Issue', 'Buyer', 'Item Number'], kind='stable').reset_index(drop=True)
    else:
        issues = pd.DataFrame(columns=['Priority', 'Issue', 'Buyer', 'Item Number', 'Description', 'Detail', 'What to do'])
    return checks, details, issues, extras


def run_data_checks():
    """Backwards-compatible wrapper: (checks, details)."""
    c, d, _, _ = _data_checks_all()
    return c, d


with tabs[8], guard():
    st.header("✅ Data Checks - catch problems before they reach a customer")
    cap("This tab reads every source file and looks for three kinds of trouble: (1) numbers that do not add up, (2) typing and set-up mistakes in the data, and (3) early warnings - items that will run out, "
        "orders that look wrong, bids that cannot be filled - while there is still time to act. Start with the 'Buyer action list': it names the items, says what looks wrong and what to do. "
        "Green = fine, blue = for your information, yellow = worth a look, red = fix before relying on the related figures. These checks use all the data, not the sidebar filters.",
        label="ℹ️ How to use this tab (click to open)")
    _checks, _details, _issues, _extras = _data_checks_all()
    cdf = pd.DataFrame(_checks)
    n_ok = int((cdf['_rank'] == 3).sum()); n_note = int((cdf['_rank'] == 2).sum()); n_rev = int((cdf['_rank'] == 1).sum()); n_bad = int((cdf['_rank'] == 0).sum())
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("✅ Passed", f"{n_ok:,}")
    h2.metric("ℹ️ For your information", f"{n_note:,}")
    h3.metric("⚠️ Worth a look", f"{n_rev:,}")
    h4.metric("❌ Needs fixing", f"{n_bad:,}")
    h5.metric("📥 Items for buyers to act on", f"{len(_issues):,}")
    if n_bad:
        st.error("Some checks failed. Figures that depend on them may be wrong until the source data is fixed.")
    elif n_rev:
        st.warning("No failures, but several items deserve a look (see the yellow rows and the buyer action list).")
    else:
        st.success("All checks passed.")

    dc_buy, dc_all, dc_sim, dc_lists = st.tabs(["📥 Buyer action list", "📋 All checks", "🔎 Similar items", "🗂️ Lists behind the checks"])

    # ---------------- buyer action list ----------------
    with dc_buy:
        if _issues.empty:
            st.success("Nothing needs a buyer's attention right now.")
        else:
            cap("One row per item that needs a person to look at it. 'Act now' = a customer or a sale is at risk. 'Act soon' = will become a problem without action, or a typing slip is likely. "
                "'Fix the data' = the item master or a source file needs correcting so the figures can be trusted. Pick a buyer, then export their list to send it on.",
                label="ℹ️ What the three priorities mean (click to open)")
            pr_counts = _issues['Priority'].value_counts()
            p1, p2, p3 = st.columns(3)
            p1.metric("🔴 Act now", f"{int(pr_counts.get(DC_ACT_NOW, 0)):,}")
            p2.metric("🟠 Act soon", f"{int(pr_counts.get(DC_ACT_SOON, 0)):,}")
            p3.metric("🔵 Fix the data", f"{int(pr_counts.get(DC_FIX, 0)):,}")
            by_buyer = _issues.groupby(['Buyer', 'Priority']).size().reset_index(name='Items')
            fig = px.bar(by_buyer, x='Buyer', y='Items', color='Priority', text='Items', title="Items to act on, by buyer",
                         color_discrete_map={DC_ACT_NOW: '#d62728', DC_ACT_SOON: '#ff7f0e', DC_FIX: '#1f77b4'}, category_orders={'Priority': [DC_ACT_NOW, DC_ACT_SOON, DC_FIX]})
            fig.update_layout(xaxis_tickangle=-30)
            show_chart(fig, key="dc_buyer_bar")
            st.markdown("**What each buyer has waiting** (number of items)")
            mx = _issues.pivot_table(index=['Priority', 'Issue'], columns='Buyer', values='Item Number', aggfunc='count', fill_value=0)
            mx['All buyers'] = mx.sum(axis=1)
            show_table(style0(mx.reset_index()), height=min(560, 60 + 36 * len(mx)))
            st.markdown("**Pick a buyer to see and export their list**")
            f1, f2, f3 = st.columns([2, 2, 3])
            buyer_sel = f1.selectbox("Show items for", ['All buyers'] + sorted(_issues['Buyer'].astype(str).unique()), key="dc_buyer")
            prio_sel = f2.multiselect("Priority", [DC_ACT_NOW, DC_ACT_SOON, DC_FIX], default=[DC_ACT_NOW, DC_ACT_SOON, DC_FIX], key="dc_prio")
            issue_sel = f3.multiselect("Kind of finding", sorted(_issues['Issue'].unique()), default=[], key="dc_issue", help="Leave empty to show every kind.")
            vi = _issues[_issues['Priority'].isin(prio_sel)]
            if buyer_sel != 'All buyers':
                vi = vi[vi['Buyer'] == buyer_sel]
            if issue_sel:
                vi = vi[vi['Issue'].isin(issue_sel)]
            show_table(vi, height=520)
            cap(f"{len(vi):,} items shown.")
            fn = 'buyer_action_list_' + re.sub(r'[^a-z0-9]+', '_', buyer_sel.lower()).strip('_') + '.csv'
            csv_button(vi, "⬇️ Export this list", fn, "dl_dc_buyer")

    # ---------------- every check ----------------
    with dc_all:
        area_sel = st.multiselect("Show areas", list(dict.fromkeys(cdf['Area'])), default=list(dict.fromkeys(cdf['Area'])), key="chk_areas")
        only_open = st.checkbox("Only show items that need attention", value=False, key="chk_only")
        view_ = cdf[cdf['Area'].isin(area_sel)]
        if only_open:
            view_ = view_[view_['_rank'] < 2]
        view_ = view_.sort_values(['_rank'], kind='stable').drop(columns='_rank')
        show_table(view_[['Result', 'Area', 'Check', 'Found', 'What it means']], height=620)
        csv_button(view_, "⬇️ Export the check results", "data_checks.csv", "dl_checks")

    # ---------------- similar items ----------------
    with dc_sim:
        sim = _extras.get('similar', pd.DataFrame())
        cap("Active (20) and sell-only (50) items in the item master that look like the same product set up more than once. Every group lists all of its items side by side, with the facts needed to decide "
            "which to keep: status, stock, sales, orders and last purchase. 'Identical wording' = the same words and sizes. 'Near-identical' = one word is spelled differently. "
            "'Same product details' = same group, category, format, flavor, brand, unit size and case size, worded differently. Nothing here is changed automatically.",
            label="ℹ️ How similar items are found (click to open)")
        if sim is None or sim.empty:
            st.success("No two active items have the same or nearly the same description.")
        else:
            tiers = sim.groupby('Match type')['Group'].nunique()
            sc = st.columns(3)
            for _c, (_t, _lab) in zip(sc, [('Identical wording', 'Identical wording'), ('Near-identical (spelling, plural or abbreviation)', 'Near-identical'), ('Same product details, different wording', 'Same details, worded differently')]):
                _c.metric(_lab, f"{int(tiers.get(_t, 0)):,} groups")
            sf1, sf2, sf3 = st.columns([3, 3, 3])
            t_sel = sf1.multiselect("Match type", sorted(sim['Match type'].unique()), default=sorted(sim['Match type'].unique()), key="dc_sim_type")
            c_sel = sf2.multiselect("Item group", sorted(sim['Item Class'].astype(str).unique()), default=[], key="dc_sim_cls", help="Leave empty for every group.")
            q_sel = sf3.text_input("Search (item number or words in the description)", value="", key="dc_sim_q")
            v = sim[sim['Match type'].isin(t_sel)]
            if c_sel:
                v = v[v['Item Class'].astype(str).isin(c_sel)]
            if q_sel.strip():
                qq = q_sel.strip().upper()
                hit = v[v['Item Number'].astype(str).str.contains(qq, regex=False) | v['Item Description'].astype(str).str.upper().str.contains(qq, regex=False)]['Group'].unique()
                v = v[v['Group'].isin(hit)]
            cols_ = ['Group', 'Match type', 'Suggestion', 'Item Number', 'Item Description', 'Status', 'Item Class', 'Buyer', 'Stock (cs)', 'Avg monthly sales (cs)', 'On order (cs)', 'Last purchase', 'Why flagged']
            vv = v[[c_ for c_ in cols_ if c_ in v.columns]]
            show_table(style0(vv, one_dec=['Avg monthly sales (cs)']), height=520)
            cap(f"{vv['Group'].nunique():,} groups, {len(vv):,} items shown. 'Keep (most active)' is only a suggestion based on sales, then stock, then status.")
            csv_button(vv, "⬇️ Export the similar items", "similar_items.csv", "dl_dc_sim")
        pk = _extras.get('similar_packs', pd.DataFrame())
        if pk is not None and not pk.empty:
            if st.checkbox("Also show items with the same wording but a different size or pack", value=False, key="dc_sim_packs",
                           help="Usually genuine variants (for example 12/750ML and 6/750ML). Shown so you can check the descriptions make the difference clear."):
                pkc = pk[['Group', 'Item Number', 'Item Description', 'Item Class', 'Unit Size', 'Case Size']]
                show_table(style0(pkc, one_dec=['Unit Size', 'Case Size']), height=420)
                csv_button(pkc, "⬇️ Export same-wording variants", "same_wording_variants.csv", "dl_dc_packs")

    # ---------------- lists behind the checks ----------------
    with dc_lists:
        cm_ = _extras.get('containers_manual')
        if cm_ is not None and len(cm_):
            with st.expander("Container counts: dashboard vs your own monthly counts"):
                show_table(style0(cm_))
                cap("Cleared = containers with a yard date in the month; Stripped = containers with a stripped date in the month; each container number is counted once. 'Gap' = dashboard minus yours.")
        if _details:
            st.subheader("Lists behind the blue, yellow and red rows")
            for _i, (_title, _tbl) in enumerate(_details.items()):
                with st.expander(f"{_title} ({len(_tbl):,} shown)"):
                    show_table(style_auto(_tbl.reset_index(drop=True)), height=300)
                    csv_button(_tbl, "⬇️ Export this list", re.sub(r'[^a-z0-9]+', '_', _title.lower())[:50] + ".csv", f"dl_chk_{_i}")
        else:
            st.info("Nothing to list: every check passed.")

# ==========================================================================
# TAB 10 — BID PURCHASING  (shown as the 5th tab)
# The weekly "what is at risk, what did we miss, what must be reordered" report, built from the M3 workbook.
# ==========================================================================
BP_STATUS = [('Out of stock - nothing on order', '#8c1d18'), ('Out of stock - order not here yet', '#d62728'),
             ('Reorder now - short of lead time', '#ff7f0e'), ('Gap - runs out before order arrives', '#9467bd'),
             ('Watch - thin cover', '#f2c94c'), ('Covered', '#2ca02c'), ('Well stocked', '#1f77b4'), ('Not rated', '#9e9e9e')]
BP_ORDER = [n for n, _ in BP_STATUS]
BP_COLORS = dict(BP_STATUS)
BP_RISK = BP_ORDER[:4]
BP_ACT_NOW, BP_ACT_TOP = 'Order now - nothing on order', 'Top up - order too small'
BP_ACT_EXP, BP_ACT_PLAN = 'Expedite - order lands late', 'Plan an order - cover is thin'
BP_SITE_LABEL = {'MDC': 'MDC', 'MJM': 'MJM', 'SEP': 'SEP', 'Bonded': 'SEZ', 'OM': 'OM', 'Stores': 'Stores', 'Plants': 'Plants'}


def bp_cost_per_case():
    """Latest PO price per case for each item (PO dollars / cases ordered)."""
    h = po_raw[po_raw['Status Code'].between(*PO_LIVE) & (po_raw['Otp'] != 'P01') & po_raw['Convertible'] &
               (po_raw['Order Qty (Cases)'] > 0) & (po_raw['USD $'] > 0)].sort_values('Ord dt')
    if h.empty:
        return pd.Series(dtype='float64')
    return (h['USD $'] / h['Order Qty (Cases)']).groupby(h['Item number']).last()


def bp_last_supplier():
    h = po_raw[po_raw['Status Code'].between(*PO_LIVE) & (po_raw['Otp'] != 'P01')].sort_values('Ord dt')
    return h.groupby('Item number')['Supplier'].last()


def bp_table(base, wms, manual, lists, p):
    """One row per item in the Purchase Report: stock in the counted sites, orders, demand, cover, status, action, suggested order.
    p = settings dict: sites, use_wms, count_hold, demand_mode (0/1/2), order_src (0/1), manual_lt, watch, upto."""
    bi = build_inv(base).copy()
    div = bi['Stock Divisor']
    has_size = div.notna()

    def cases(units):
        return (units / div).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0)

    # ---------- stock by site, in cases
    site = {}
    for sname in ONHAND_SITES + NOT_COUNTED_SITES:
        site[sname] = bi[f'{sname}_Cs'].clip(lower=0).fillna(0.0)
    bi['MDC Hold (Cs)'] = 0.0
    if p['use_wms'] and not wms.empty:
        w = wms.drop_duplicates('Item Number').set_index('Item Number')
        site['MDC'] = cases(bi['Item Number'].map(w['WMS OK Units']).fillna(0.0))
        bi['MDC Hold (Cs)'] = cases(bi['Item Number'].map(w['WMS HOLD Units']).fillna(0.0))
    if p['count_hold']:
        site['MDC'] = site['MDC'] + bi['MDC Hold (Cs)']
    chosen = [s_ for s_ in ONHAND_SITES if s_ in p['sites']]
    for sname in ONHAND_SITES + NOT_COUNTED_SITES:
        bi[f'{BP_SITE_LABEL[sname]} (Cs)'] = site[sname]
    bi['On Hand (Cs)'] = sum(site[s_] for s_ in chosen) if chosen else 0.0
    bi['Other Sites (Cs)'] = sum(site[s_] for s_ in ONHAND_SITES if s_ not in chosen)          # OM is shown in its own column and never counted

    # ---------- orders
    bi['On Order M3 (Cs)'] = cases(bi['On Order'])
    bi['On Order PO file (Cs)'] = bi['Cases Inbound']
    bi['On Order (Cs)'] = bi['On Order PO file (Cs)'] if p['order_src'] == 1 else bi['On Order M3 (Cs)']
    op = po_active_all.copy()
    op['Expected'] = expected_receipt(op)
    nxt = op.groupby('Item number')['Expected'].min()
    bi['Next Arrival'] = bi['Item Number'].map(nxt)

    # ---------- demand: the CPJ forecast supersedes everything else
    cpj = bi['3M_Avg_Forecast_Cases'].fillna(0.0)
    prf = bi['PR_Fcst_Avg_Cases'].fillna(0.0) if 'PR_Fcst_Avg_Cases' in bi.columns else pd.Series(0.0, index=bi.index)
    sal = bi['3M_Avg_Depletion_NonZero'].fillna(0.0)
    fbest = np.where(cpj > 0, cpj, prf)
    flabel = np.where(cpj > 0, 'CPJ forecast', np.where(prf > 0, 'Purchase Report forecast', ''))
    if p['demand_mode'] == 0:
        D = np.where(fbest > 0, fbest, sal)
        basis = np.where(fbest > 0, flabel, np.where(sal > 0, 'Recent sales', 'None'))
    elif p['demand_mode'] == 1:
        D = np.maximum(fbest, sal)
        basis = np.where(D <= 0, 'None', np.where(fbest >= sal, flabel, 'Recent sales (higher)'))
    else:
        D = sal.to_numpy()
        basis = np.where(D > 0, 'Recent sales', 'None')
    bi['Demand (Cs/mo)'] = D
    bi['Demand Basis'] = basis
    bi['Sales Avg (Cs/mo)'] = sal
    bi['Forecast (Cs/mo)'] = fbest

    # ---------- lead time
    lead = bi['Lead time'].astype(float)
    if p['manual_lt'] and not manual.empty:
        mlt = bi['Item Number'].map(manual.drop_duplicates('Item Number').set_index('Item Number')['Manual Lead Time'])
        lead = mlt.where(mlt > 0, lead)
    bi['Lead Time (days)'] = lead
    Lm = (lead / 30.0).to_numpy()

    # ---------- cover
    oh = bi['On Hand (Cs)'].to_numpy()
    oo = bi['On Order (Cs)'].fillna(0.0).to_numpy()
    pos_d = D > 0
    safe_D = np.where(pos_d, D, 1.0)
    cov_oh = np.where(pos_d, oh / safe_D, np.nan)
    cov_all = np.where(pos_d, (oh + oo) / safe_D, np.nan)
    pos = cov_all - Lm
    bi['Months On Hand'] = cov_oh
    bi['Months Incl. On Order'] = cov_all
    bi['Stock Position (months)'] = pos
    run_days = np.where(pos_d, np.minimum(oh / safe_D * 30.0, 3650.0), np.nan)
    bi['Run-out Date'] = today + pd.to_timedelta(run_days, unit='D')
    nxt_eff = bi['Next Arrival'].clip(lower=today)
    bi['Days Until Order Arrives'] = (nxt_eff - today).dt.days
    bi['Gap (days)'] = (nxt_eff - bi['Run-out Date']).dt.days
    gap_flag = (oo > 0) & bi['Gap (days)'].gt(BID_GAP_DAYS).to_numpy() & (oh > 0)

    live = bi['Is Live'].to_numpy()
    rated = live & has_size.to_numpy() & pos_d
    conds = [~rated, (oh <= 0) & (oo <= 0), (oh <= 0) & (oo > 0), pos < 0, gap_flag, pos < p['watch'], cov_all >= COVER_CAP_MONTHS]
    bi['Status'] = np.select(conds, BP_ORDER[7:8] + BP_ORDER[0:1] + BP_ORDER[1:2] + BP_ORDER[2:3] + BP_ORDER[3:4] + BP_ORDER[4:5] + BP_ORDER[6:7],
                             default='Covered')
    bi['Why Not Rated'] = np.select([~live, ~has_size.to_numpy(), ~pos_d], ['Not an active item', 'No case size', 'No sales or forecast'], default='')

    # ---------- suggested order: lead time + a month of demand, less what is here and on the way
    need = D * (Lm + p['upto']) - (oh + oo)
    bi['Suggested Order (Cs)'] = np.where(rated, np.ceil(np.maximum(need, 0.0)), 0.0)
    sugg = bi['Suggested Order (Cs)'].to_numpy()
    st_ = bi['Status'].to_numpy()
    act = np.select([(np.isin(st_, [BP_ORDER[0], BP_ORDER[2]])) & (oo <= 0),
                     (np.isin(st_, [BP_ORDER[1], BP_ORDER[2]])) & (oo > 0) & (sugg > 0),
                     np.isin(st_, [BP_ORDER[1], BP_ORDER[3]]),
                     (st_ == BP_ORDER[4]) & (sugg > 0)],
                    [BP_ACT_NOW, BP_ACT_TOP, BP_ACT_EXP, BP_ACT_PLAN], default='')
    bi['Action'] = act
    bi['Reorder Flag'] = np.isin(act, [BP_ACT_NOW, BP_ACT_TOP])

    # ---------- money and timing
    cost = bi['Item Number'].map(bp_cost_per_case())
    avg_cost = (bi['Inventory Value'] / bi['Total_Cases'].where(bi['Total_Cases'] > 0)).replace([np.inf, -np.inf], np.nan)
    bi['Cost per Case (USD)'] = cost.fillna(avg_cost)
    bi['Cost Source'] = np.where(cost.notna(), 'Latest PO price', np.where(avg_cost.notna(), 'Stock value per case', 'Unknown'))
    bi['Est. Order Cost (USD)'] = bi['Suggested Order (Cs)'] * bi['Cost per Case (USD)']
    sup_key = bi['Item Number'].map(bp_last_supplier())
    if HAS_TERMS and 'Supplier Name' in bi.columns:
        nk = {_norm_name(n_): k_ for n_, k_ in zip(vt_df['Supplier Name'], vt_df['Supplier Key']) if n_}
        sup_key = sup_key.fillna(bi['Supplier Name'].map(lambda x: nk.get(_norm_name(x))))
    bi['Supplier Key'] = sup_key
    bi = add_terms(bi, 'Supplier Key')
    bi['Cash Date'] = today + pd.to_timedelta(bi['Lead Time (days)'].fillna(DEFAULT_LEAD_DAYS) + bi['Terms Days'], unit='D')
    bi['Cash Month'] = bi['Cash Date'].dt.strftime('%Y-%m')

    # ---------- who / what
    vendor = bi['Supplier Name'].fillna('') if 'Supplier Name' in bi.columns else pd.Series('', index=bi.index)
    if not manual.empty:
        mv = bi['Item Number'].map(manual.drop_duplicates('Item Number').set_index('Item Number')['Manual Vendor'])
        vendor = mv.where(mv.notna() & (mv.astype(str) != ''), vendor)
    bi['Vendor'] = vendor.fillna('')
    bi['M3 Buyer ID'] = bi['Responsible'].fillna('').astype(str) if 'Responsible' in bi.columns else ''
    tag = pd.Series('', index=bi.index)
    for nm, ids in lists.items():
        hit = bi['Item Number'].isin(ids)
        tag[hit] = tag[hit].map(lambda t, nm=nm: (t + ', ' if t else '') + nm)
    bi['Lists'] = tag
    bi['Bid Item'] = bi['Item Number'].isin(set().union(*[v for k, v in lists.items() if k != 'Weekly report list'])) if lists else False
    if 'Last Purchase Date' in bi.columns:
        _t = bi['Last Purchase Date'].astype(str).str.strip()
        lp = pd.to_datetime(_t.str.slice(0, 10), format='%Y-%m-%d', errors='coerce')
        _rest = lp.isna() & ~_t.str.lower().isin(['', 'nan', 'nat', 'none', '-', '(is missing)'])
        if _rest.any():
            lp = lp.where(~_rest, pd.to_datetime(_t.where(_rest), errors='coerce', dayfirst=False))
    else:
        lp = pd.Series(pd.NaT, index=bi.index)
    bi['Last Purchase'] = lp
    bi['Days Since Last Purchase'] = (today - lp).dt.days
    return bi


with tabs[9], guard():
    st.header("🎯 Bid Purchasing Report — covered, at risk, and what to reorder")
    _m3_ok = bool(m3_data) and not m3_data.get('pr', pd.DataFrame()).empty
    if _m3_ok:
        _base, _wms, _manual, _lists = m3_data['pr'], m3_data['wms'], m3_data['manual'], dict(m3_data['lists'])
    else:
        _base, _wms, _manual, _lists = sales_df, pd.DataFrame(), pd.DataFrame(), {}
        st.warning("The M3 Bid Purchasing workbook was not found or could not be read" +
                   (f" ({DATA['errors']['m3']})" if 'm3' in DATA['errors'] and not DATA['errors']['m3'].startswith('File not found') else "") +
                   ". This tab is using the Purchase Report CSV instead: there is no WMS stock split, no item lists and no 'manual' sheet. "
                   "Put the workbook in the dashboard folder to get the full report.")
    if not bids_df.empty:
        _cur = set(bids_df.loc[bids_df['Bid Status'] == 'Current', 'Item Code'])
        if _cur:
            _lists['Current bids (BIDS file)'] = _cur
        _lists['All bids (BIDS file)'] = set(bids_df['Item Code'].astype(str))        # every item on the Bids file: the same items the Bid Performance / Bid Availability tabs count
    cap("Stock coverage = what is in our sites + what is on order, divided by monthly demand. The CPJ forecast comes first; where an item has none, the "
               "Purchase Report's own forecast, then recent sales. An item is at risk when that cover is shorter than its supplier lead time "
               "(your weekly sheet's 'Stock Position' below zero). Only MDC, MJM, SEP and SEZ are counted unless you change it. OM stock is never counted.")

    # ---------------- settings
    s1, s2, s3 = st.columns(3)
    with s1:
        _list_opts = list(_lists.keys()) + ['Every live item']
        _def_list = ['Weekly report list'] if 'Weekly report list' in _lists else (['Current bids (BIDS file)'] if 'Current bids (BIDS file)' in _lists else ['Every live item'])
        sel_lists = st.multiselect("Items to review", _list_opts, default=_def_list, key="bp_lists",
                                   help="Pick one or more lists. 'Weekly report list' is the Master-All sheet of your M3 workbook; 'All bids (BIDS file)' is every item on the Bids file, the same items the Bid Performance and Bid Availability tabs count; the others are the Sandals / RIU sheets and the BIDS file.")
    with s2:
        _dm = ["Forecast first, then recent sales (recommended)", "The higher of forecast and recent sales (cautious)", "Recent sales only (like the weekly sheet)"]
        demand_pick = st.selectbox("Demand used", _dm, index=0, key="bp_demand",
                                   help="The existing CPJ forecast always comes before the Purchase Report's forecast. Recent sales = average of the last 3 complete months, skipping months with no sales.")
    with s3:
        _os = ["M3 on-order quantity (Purchase Report)", "PO Dates file (open lines)"]
        order_pick = st.selectbox("On order taken from", _os, index=0, key="bp_order_src",
                                  help="M3 on-order is what your weekly sheet uses. The PO Dates file has the arrival dates; both are shown in the table.")
    t1, t2, t3 = st.columns(3)
    with t1:
        _site_opts = [BP_SITE_LABEL[x] for x in ONHAND_SITES]
        sel_sites = st.multiselect("Sites counted as our stock", _site_opts, default=[BP_SITE_LABEL[x] for x in BID_SITES], key="bp_sites",
                                   help="MDC, MJM, SEP and SEZ are our sites. Stores and Plants are shown for information only unless you add them. OM stock is never counted.")
    with t2:
        watch_m = st.slider("Watch band: cover less than lead time plus (months)", 0.0, 3.0, float(BID_WATCH_MONTHS), 0.25, key="bp_watch")
        upto_m = st.slider("A suggested order buys lead time plus (months of demand)", 0.5, 6.0, float(BID_ORDER_UP_TO_MONTHS), 0.5, key="bp_upto")
    with t3:
        use_wms = st.checkbox("MDC stock from the WMS sheet (OK status only, like the weekly sheet)", value=not _wms.empty, disabled=_wms.empty, key="bp_wms")
        count_hold = st.checkbox("Also count MDC stock on HOLD", value=False, disabled=_wms.empty, key="bp_hold")
        manual_lt = st.checkbox("Use lead times typed on the 'manual' sheet", value=False, disabled=_manual.empty, key="bp_manual_lt",
                                help="Off = the Purchase Report's lead time, as in your weekly sheet. On = the buyers' own lead times where they exist (usually shorter, so fewer items are flagged).")

    _params = {'sites': [x for x in ONHAND_SITES if BP_SITE_LABEL[x] in sel_sites], 'use_wms': bool(use_wms), 'count_hold': bool(count_hold),
               'demand_mode': _dm.index(demand_pick) if demand_pick in _dm else 0, 'order_src': _os.index(order_pick) if order_pick in _os else 0,
               'manual_lt': bool(manual_lt), 'watch': float(watch_m), 'upto': float(upto_m)}
    full = bp_table(_base, _wms, _manual, _lists, _params)

    # ---------------- which items
    if 'Every live item' in sel_lists or not sel_lists:
        uni = full[full['Is Live']]
    else:
        ids_ = set().union(*[_lists[k] for k in sel_lists if k in _lists])
        uni = full[full['Item Number'].isin(ids_)]
    inc_disc = st.checkbox("Also show items that are not active (discontinued / deleted)", value=False, key="bp_disc")
    if not inc_disc:
        uni = uni[uni['Is Live']]
    if uni.empty:
        st.info("No items match the lists chosen above.")
    else:
        # ---------------- substitutes (same engine and ratings as the other tabs)
        bsub_lvl = st.radio("Show substitutes rated", list(SUB_LEVELS.keys()), index=1, horizontal=True, key="bp_sub_level",
                            help="High = very likely the same kind of product. Medium = probably. Any = every item with the same Category + Format.")
        _full_s = full.copy()
        _full_s['Sub Key'] = add_sub_key(_full_s, SUB_BASE_LEVEL)
        _full_s.loc[~_full_s['Item Class'].isin(SALEABLE_GROUPS) | _full_s['Item Class'].isin(PLANT_GROUPS), 'Sub Key'] = ''
        _full_s['Avail_Cases'] = _full_s['On Hand (Cs)']
        _full_s['3M_Avg_Depletion_Demand'] = _full_s['Demand (Cs/mo)']
        _tg = _full_s[_full_s['Item Number'].isin(uni.loc[uni['Status'].isin(BP_RISK), 'Item Number']) & (_full_s['Sub Key'] != '')]
        _cd = _full_s[(_full_s['Item Status'] == 20) & (_full_s['Sub Key'] != '') & (_full_s['Avail_Cases'] > 0) &
                      _full_s['Status'].isin(['Covered', 'Well stocked', 'Watch - thin cover'])]
        _pairs = find_substitutes(_tg, _cd, SUB_LEVELS[bsub_lvl], min_months=MIN_SUB_MONTHS) if (len(_tg) and len(_cd)) else pd.DataFrame()
        _best = best_substitute(_pairs) if len(_pairs) else pd.DataFrame()
        if len(_best):
            _bs = _best[['Item Number', 'Item Number (Sub)', 'Item Description (Sub)', 'Avail_Cases (Sub)', 'Sub Months', 'Match Confidence', 'Match Score', '# Subs']].rename(
                columns={'Item Number (Sub)': 'Sub Item', 'Item Description (Sub)': 'Sub Description', 'Avail_Cases (Sub)': 'Sub Stock (Cs)'})
            uni = uni.merge(_bs, on='Item Number', how='left')
        else:
            for c_ in ['Sub Item', 'Sub Description', 'Sub Stock (Cs)', 'Sub Months', 'Match Confidence', 'Match Score', '# Subs']:
                uni[c_] = np.nan
        uni['Has Sub'] = uni['Sub Item'].notna()

        # ---------------- filters
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            cat_sel = st.multiselect("Category", sorted(uni['Custom Category'].astype(str).unique()), key="bp_cat")
        with f2:
            buyer_sel = st.multiselect("Buyer", sorted(uni['Responsible Buyer'].astype(str).unique()), key="bp_buyer")
        with f3:
            vend_sel = st.multiselect("Vendor", sorted([v for v in uni['Vendor'].astype(str).unique() if v]), key="bp_vendor")
        with f4:
            q = st.text_input("Search item code or description", "", key="bp_search")
        g1, g2, g3 = st.columns(3)
        with g1:
            bid_only = st.checkbox("Bid items only (Sandals / RIU / BIDS file)", value=False, key="bp_bid_only", disabled=not uni['Bid Item'].any())
        with g2:
            hide_sub = st.checkbox("Hide at-risk items that have a substitute in stock", value=False, key="bp_hide_sub")
        with g3:
            only_flag = st.checkbox("Only items to reorder (order now / top up)", value=False, key="bp_only_flag")
        bpf = uni
        if cat_sel: bpf = bpf[bpf['Custom Category'].isin(cat_sel)]
        if buyer_sel: bpf = bpf[bpf['Responsible Buyer'].isin(buyer_sel)]
        if vend_sel: bpf = bpf[bpf['Vendor'].isin(vend_sel)]
        if bid_only: bpf = bpf[bpf['Bid Item']]
        if q.strip():
            bpf = bpf[bpf['Item Number'].str.contains(q.strip(), case=False, regex=False, na=False) |
                      bpf['Item Description'].astype(str).str.contains(q.strip(), case=False, regex=False, na=False)]
        if hide_sub:
            bpf = bpf[~(bpf['Status'].isin(BP_RISK) & bpf['Has Sub'])]

        # ---------------- tiles
        n_all = len(bpf)
        n_now = int((bpf['Action'] == BP_ACT_NOW).sum()); n_top = int((bpf['Action'] == BP_ACT_TOP).sum())
        n_exp = int((bpf['Action'] == BP_ACT_EXP).sum()); n_plan = int((bpf['Action'] == BP_ACT_PLAN).sum())
        n_ok = int(bpf['Status'].isin(['Covered', 'Well stocked']).sum())
        n_nr = int((bpf['Status'] == 'Not rated').sum())
        fl = bpf[bpf['Reorder Flag']]
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Items reviewed", f"{n_all:,}", f"{n_nr:,} not rated", delta_color="off")
        k2.metric("Order now - nothing on order", f"{n_now:,}", "likely missed by the buy", delta_color="off")
        k3.metric("Top up - order too small", f"{n_top:,}")
        k4.metric("Expedite - order lands late", f"{n_exp:,}")
        k5.metric("Covered / well stocked", f"{n_ok:,}", f"{n_plan:,} to plan", delta_color="off")
        j1, j2, j3, j4 = st.columns(4)
        j1.metric("Items to reorder", f"{len(fl):,}", f"{int(fl['Bid Item'].sum()):,} are bid items", delta_color="off")
        j2.metric("Estimated cost of those orders", f"${fl['Est. Order Cost (USD)'].sum():,.0f}")
        _risk = bpf[bpf['Status'].isin(BP_RISK)]
        j3.metric("At risk, a substitute is in stock", f"{int(_risk['Has Sub'].sum()):,}")
        j4.metric("At risk, no substitute", f"{int((~_risk['Has Sub']).sum()):,}")
        if n_all and (bpf['Lead Time Missing'].sum() > 0):
            cap(f"{int(bpf['Lead Time Missing'].sum()):,} items have no lead time in the Purchase Report, so {DEFAULT_LEAD_DAYS} days is assumed for them.")

        # ---------------- charts
        c_a, c_b = st.columns(2)
        with c_a:
            sc = bpf['Status'].value_counts().reindex(BP_ORDER).dropna().reset_index()
            sc.columns = ['Status', 'Items']
            fig = px.bar(sc, x='Items', y='Status', orientation='h', color='Status', color_discrete_map=BP_COLORS, text='Items',
                         category_orders={'Status': BP_ORDER}, title="Where every item stands")
            fig.update_layout(showlegend=False, height=380, yaxis={'autorange': 'reversed'})
            show_chart(fig, key="t10_status")
        with c_b:
            cs_ = bpf.groupby(['Custom Category', 'Status']).size().reset_index(name='Items')
            fig = px.bar(cs_, x='Items', y='Custom Category', color='Status', orientation='h', color_discrete_map=BP_COLORS,
                         category_orders={'Status': BP_ORDER}, title="Status by category")
            fig.update_layout(height=380, yaxis={'categoryorder': 'total ascending'})
            show_chart(fig, key="t10_cat")

        c_c, c_d = st.columns(2)
        with c_c:
            top = _risk.sort_values(['Bid Item', 'Stock Position (months)'], ascending=[False, True]).head(20).copy()
            if top.empty:
                st.info("No items at risk with the current filters.")
            else:
                top['Label'] = top['Item Number'] + ' ' + top['Item Description'].astype(str).str.slice(0, 28)
                top['In stock'] = top['Months On Hand'].fillna(0.0).clip(upper=12)
                top['On order'] = (top['Months Incl. On Order'].fillna(0.0).clip(upper=12) - top['In stock']).clip(lower=0)
                fig = go.Figure()
                fig.add_trace(go.Bar(y=top['Label'], x=top['In stock'], name='In stock (months)', orientation='h', marker_color='#2ca02c'))
                fig.add_trace(go.Bar(y=top['Label'], x=top['On order'], name='On order (months)', orientation='h', marker_color='#9ecae1'))
                fig.add_trace(go.Scatter(y=top['Label'], x=(top['Lead Time (days)'] / 30.0), mode='markers', name='Lead time (months)',
                                         marker=dict(symbol='line-ns', size=14, color='#d62728', line=dict(width=3, color='#d62728'))))
                fig.update_layout(barmode='stack', height=520, title="Top 20 at risk (bid items first): cover vs lead time",
                                  xaxis_title="Months of demand", yaxis={'autorange': 'reversed'}, legend=dict(orientation='h', y=-0.15))
                show_chart(fig, key="t10_top")
        with c_d:
            ro = _risk[_risk['Run-out Date'].notna()].copy()
            if ro.empty:
                st.info("No run-out dates to show.")
            else:
                ro['Week'] = (ro['Run-out Date'] - pd.to_timedelta(ro['Run-out Date'].dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
                ro = ro[ro['Run-out Date'] <= today + pd.Timedelta(days=120)]
                rg = ro.groupby(['Week', 'Status']).size().reset_index(name='Items')
                if rg.empty:
                    st.info("No at-risk items run out in the next 120 days.")
                else:
                    fig = px.bar(rg, x='Week', y='Items', color='Status', color_discrete_map=BP_COLORS, category_orders={'Status': BP_ORDER},
                                 title="When at-risk items run out (week starting, next 120 days)")
                    category_axis(fig, angle=-45)
                    fig.update_layout(height=520)
                    show_chart(fig, key="t10_runout")

        if len(fl) and fl['Est. Order Cost (USD)'].sum() > 0:
            cm = fl.groupby('Cash Month')['Est. Order Cost (USD)'].sum().reset_index().sort_values('Cash Month')
            cm2 = fl.groupby(['Cash Month', 'Terms Label'])['Est. Order Cost (USD)'].sum().reset_index()
            fig = px.bar(cm2, x='Cash Month', y='Est. Order Cost (USD)', color='Terms Label',
                         title="If every flagged item is ordered today: when the money goes out (arrival + the vendor's payment terms)")
            category_axis(fig)
            fig.update_layout(height=380, yaxis_title="USD")
            show_chart(fig, key="t10_cash")
            _unconf = fl.loc[~fl['Terms Confirmed'], 'Est. Order Cost (USD)'].sum()
            if _unconf > 0:
                cap(f"${_unconf:,.0f} of this uses an assumed {DEFAULT_TERMS_DAYS}-day term, because the supplier has no confirmed terms in the Vendor Terms file." if HAS_TERMS else
                           f"The Vendor Terms file is not loaded, so {DEFAULT_TERMS_DAYS}-day terms are assumed. Add VendorTerms.xlsx to the dashboard folder for real payment timing.")

        # ---------------- the working table
        st.subheader("Items to act on")
        stat_default = [x for x in BP_RISK + ['Watch - thin cover'] if x in set(bpf['Status'])]
        stat_sel = st.multiselect("Show these statuses", BP_ORDER, default=stat_default, key="bp_stat")
        tv = bpf[bpf['Status'].isin(stat_sel)] if stat_sel else bpf
        if only_flag:
            tv = tv[tv['Reorder Flag']]
        tv = tv.sort_values(['Bid Item', 'Stock Position (months)', 'Demand (Cs/mo)'], ascending=[False, True, False], na_position='last')
        cols_tbl = ['Item Number', 'Item Description', 'Custom Category', 'Vendor', 'Lists', 'Status', 'Action', 'Suggested Order (Cs)', 'Est. Order Cost (USD)',
                    'Lead Time (days)', 'Demand (Cs/mo)', 'Demand Basis', 'On Hand (Cs)', 'MDC (Cs)', 'SEP (Cs)', 'MJM (Cs)', 'SEZ (Cs)', 'OM (Cs)', 'MDC Hold (Cs)',
                    'On Order (Cs)', 'On Order M3 (Cs)', 'On Order PO file (Cs)', 'Next Arrival', 'Months On Hand', 'Months Incl. On Order',
                    'Stock Position (months)', 'Run-out Date', 'Gap (days)', 'Terms Label', 'Cash Date', 'Sub Item', 'Sub Description', 'Sub Stock (Cs)', 'Sub Months',
                    'Match Confidence', 'Last Purchase', 'Days Since Last Purchase', 'M3 Buyer ID', 'Responsible Buyer', 'Other Sites (Cs)']
        cols_tbl = [c_ for c_ in cols_tbl if c_ in tv.columns]
        shown = tv[cols_tbl].copy()
        for dc in ('Next Arrival', 'Run-out Date', 'Cash Date', 'Last Purchase'):
            if dc in shown.columns:
                shown[dc] = shown[dc].dt.strftime('%Y-%m-%d')
        cap(f"{len(tv):,} items. Sorted: bid items first, then the shortest cover relative to lead time. "
                   "Stock Position = months of cover (stock + on order) minus lead time; below zero means it will run out before a new order could arrive.")
        show_table(style0(shown.head(1500), one_dec=['Suggested Order (Cs)', 'Demand (Cs/mo)', 'On Hand (Cs)', 'MDC (Cs)', 'SEP (Cs)', 'MJM (Cs)', 'SEZ (Cs)', 'OM (Cs)', 'MDC Hold (Cs)',
                                                     'On Order (Cs)', 'On Order M3 (Cs)', 'On Order PO file (Cs)', 'Months On Hand', 'Months Incl. On Order', 'Stock Position (months)',
                                                     'Sub Stock (Cs)', 'Sub Months', 'Other Sites (Cs)'], money=['Est. Order Cost (USD)']), height=520)
        if len(tv) > 1500:
            cap("Only the first 1,500 rows are shown on screen. The downloads below hold every row.")
        d1, d2, d3 = st.columns(3)
        with d1:
            csv_button(tv[tv['Reorder Flag']][cols_tbl], "⬇️ Reorder list (CSV)", "bid_reorder_list.csv", "dl_bp_reorder")
        with d2:
            csv_button(tv[cols_tbl], "⬇️ Table above (CSV)", "bid_purchasing_table.csv", "dl_bp_all")
        with d3:
            try:
                _buf = io.BytesIO()
                with pd.ExcelWriter(_buf, engine='openpyxl') as xw:
                    tv[tv['Reorder Flag']][cols_tbl].to_excel(xw, sheet_name='Reorder list', index=False)
                    tv[tv['Action'] != ''][cols_tbl].to_excel(xw, sheet_name='All actions', index=False)
                    bpf[cols_tbl].to_excel(xw, sheet_name='All items reviewed', index=False)
                st.download_button("⬇️ Excel workbook (reorder list + all items)", _buf.getvalue(), file_name="bid_purchasing_report.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_bp_xlsx")
            except Exception as e_:
                cap(f"The Excel download could not be built ({type(e_).__name__}). The CSV downloads still work.")

        # ---------------- one item in detail
        st.subheader("Look at one item")
        opts = (tv['Item Number'] + ' - ' + tv['Item Description'].astype(str).str.slice(0, 60)).head(500).tolist()
        if opts:
            pick = st.selectbox("Item", opts, key="bp_pick")
            item = pick.split(' - ')[0].strip() if pick else opts[0].split(' - ')[0]
            r = full[full['Item Number'] == item].iloc[0]
            lines = po_active_all[po_active_all['Item number'] == item].copy()
            lines['Expected'] = expected_receipt(lines)
            D_ = float(r['Demand (Cs/mo)']); oh_ = float(r['On Hand (Cs)'])
            wk = pd.date_range(today, periods=27, freq='7D')
            arr = []
            for w_ in wk:
                got = lines.loc[lines['Expected'].fillna(today).clip(lower=today) <= w_, 'Order Qty (Cases)'].sum()
                arr.append(oh_ - D_ * ((w_ - today).days / 30.0) + got)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=wk, y=arr, mode='lines+markers', name='Projected stock (cases)', line=dict(color='#1f77b4', width=3),
                                     fill='tozeroy'))
            fig.add_shape(type='line', x0=wk[0], x1=wk[-1], y0=0, y1=0, line=dict(color='#d62728', dash='dash'))
            lead_d = today + pd.Timedelta(days=float(r['Lead Time (days)']) if pd.notna(r['Lead Time (days)']) else DEFAULT_LEAD_DAYS)
            fig.add_shape(type='line', x0=lead_d, x1=lead_d, y0=0, y1=1, yref='paper', line=dict(color='#ff7f0e', dash='dot'))
            fig.add_annotation(x=lead_d, y=1, yref='paper', text="A new order placed today would arrive", showarrow=False, yshift=10)
            fig.update_layout(height=360, title=f"{item}: projected stock over the next 6 months at {D_:,.1f} cases a month (orders in the PO file arrive on their expected date)",
                              yaxis_title="Cases")
            show_chart(fig, key="t10_item")
            m3_oo, po_oo = float(r['On Order M3 (Cs)']), float(r['On Order PO file (Cs)'])
            if abs(m3_oo - po_oo) > max(1.0, 0.1 * max(m3_oo, po_oo)):
                cap(f"Note: M3 shows {m3_oo:,.0f} cases on order, the PO Dates file shows {po_oo:,.0f}. The chart only includes what the PO Dates file has dates for.")
            if not lines.empty:
                lt_ = lines[['PO no', 'Supplier', 'Order Qty (Cases)', 'Status', 'Expected']].copy()
                lt_['Expected'] = lt_['Expected'].dt.strftime('%Y-%m-%d')
                show_table(style0(lt_, one_dec=['Order Qty (Cases)']), height=200)
            if len(_pairs):
                ps = _pairs[_pairs['Item Number'] == item].sort_values(['Match Score', 'Avail_Cases (Sub)'], ascending=False).head(8)
                if len(ps):
                    st.markdown("**Substitutes in stock**")
                    show_table(style0(ps[['Item Number (Sub)', 'Item Description (Sub)', 'Avail_Cases (Sub)', 'Sub Months', 'Match Confidence', 'Match Score', 'Why Matched']]
                                      .rename(columns={'Item Number (Sub)': 'Sub Item', 'Item Description (Sub)': 'Sub Description', 'Avail_Cases (Sub)': 'Sub Stock (Cs)'}),
                                      one_dec=['Sub Stock (Cs)', 'Sub Months']), height=220)

        with st.expander("How this report works"):
            st.markdown(
                "- **Stock** = cases in the sites ticked above (MDC from the WMS sheet, OK status, unless you switch it). Everything is in cases, using the same case sizes as the other tabs.\n"
                "- **On order** = the M3 on-order quantity (or the PO Dates file). **Next arrival** comes from the PO Dates file: stripped, yard, arrival, ETA, then requested date.\n"
                "- **Demand** = the CPJ forecast; if the item has none, the Purchase Report's forecast; if neither, the last 3 complete months of sales (months with no sales skipped, as in your weekly sheet).\n"
                "- **Stock Position** = (stock + on order) / monthly demand, minus lead time in months. Below zero = the item will run out before a new order could land.\n"
                "- **Gap** = stock runs out more than a week before the first open order is expected. **Watch** = cover is within the watch band above the lead time.\n"
                "- **Suggested order** = demand x (lead time + the months set above) minus stock and on-order, rounded up to whole cases. It is a starting point for the buyer, not an order.\n"
                "- **Cost** = the latest PO price per case (or stock value per case when the item has no PO). **Cash date** = today + lead time + the vendor's payment terms.\n"
                "- **Substitutes** use the same matching as the Inventory Health and Bid Performance tabs, but only offer items that are themselves covered.")
