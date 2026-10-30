import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import io
import os
import re
import json
import hmac
import hashlib
import time
import traceback
from contextlib import contextmanager

# ==========================================================================
# CPJ PURCHASING DASHBOARD  --  REVISION 4
#
# What changed in this revision (plain English):
#   * Sign-in screen for the purchasing team (passwords are stored scrambled, never as text)
#   * The new PO Dates layout is supported (new date columns, new USD / Month / Year columns)
#   * Open POs are now defined as Hst 20-49
#   * KG / catch-weight items are converted to cases using the weight written in the item
#     description (the Conversion Factor is only used when the description has no weight)
#   * Safety stock now uses the confirmed forecast; months of cover, fill rate, container
#     clearance and substitute matching were rebuilt
#   * A "Data Checks" tab shows whether the files loaded and reconciled correctly
#   * Every tab is protected, so one problem never blanks the whole dashboard
#
# Key logic decisions (all editable in the CONFIG block below):
#   * PO Otp = P01 is excluded everywhere by default (sidebar toggle to bring it back).
#   * Open PO = Hst 20-49.  Closed/received = Hst 75-85.  Hst 99 = deleted / dropped line.
#   * Item Status (Purchase Report): 10 new, 20 active, 50 soft-disc, 80 full-disc, 90-99 deleted.
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
LIVE_STATUSES = [10, 20]                 # new + active
DISCONTINUED = [50, 80]
DELETED = list(range(90, 100))
MIN_SUB_MONTHS = 0.5                     # a substitute must hold more than this many months of stock
DEAD_STOCK_INCLUDES_DISCONTINUED = True
DEFAULT_LEAD_DAYS = 30                   # used only when the item has no lead time in the Purchase Report
COVER_CAP_MONTHS = 6                     # one overstocked item cannot count for more than this many months
INCLUDE_CURRENT_MONTH_SALES = False      # False = sales averages use the last 3 COMPLETE months only

SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM', 'Allocated quantity']
SITE_CS = ['MDC_Cs', 'SEP_Cs', 'MJM_Cs', 'Stores_Cs', 'Bonded_Cs', 'Plants_Cs', 'OM_Cs', 'Allocated_Cs']
ONHAND_SITES = ['MDC', 'SEP', 'MJM', 'Stores', 'Bonded', 'Plants', 'OM']      # Allocated is shown separately
WAREHOUSE_SITES = ['MDC', 'SEP', 'MJM', 'Bonded']                              # replenishment-relevant stock (no Stores)
AVAIL_SITES = ['MDC']                                                          # bid availability = MDC stock
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

# ---- Bid Purchasing tab ---------------------------------------------------------------------------------------
BID_SITES = ['MDC', 'MJM', 'SEP', 'Bonded', 'OM']        # the sites the Bid Purchasing tab counts as "our stock" (Bonded is shown as SEZ)
BID_OTHER_SITES = ['Stores', 'Plants']                    # shown for information only, never counted
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
    s = s[~s['Item Description'].astype(str).str.contains('SAMPLE', case=False, na=False)]
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
    for site in ONHAND_SITES:
        s[f'{site}_Cs'] = (s[site] / div).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    s['Allocated_Cs'] = (s['Allocated quantity'] / div).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    s['Total_Cases'] = s[[f'{x}_Cs' for x in ONHAND_SITES]].sum(axis=1).clip(lower=0)
    s['WH_Cases'] = s[[f'{x}_Cs' for x in WAREHOUSE_SITES]].sum(axis=1).clip(lower=0)
    s['Avail_Cases'] = s[[f'{x}_Cs' for x in AVAIL_SITES]].sum(axis=1).clip(lower=0)
    s['No Case Size'] = s['Stock Divisor'].isna()

    # ---- inventory value (a TOTAL $ figure) shared out by each site's share of units
    s['Inventory Value'] = num(s['Inventory Value'])
    pos = s[ONHAND_SITES].clip(lower=0)
    tot_q = pos.sum(axis=1)
    wh_q = pos[WAREHOUSE_SITES].sum(axis=1)
    safe_tot = tot_q.where(tot_q > 0, 1.0)
    s['WH Value'] = np.where(tot_q > 0, s['Inventory Value'] * wh_q / safe_tot, 0.0)
    s['Unplaced Value'] = np.where(tot_q > 0, 0.0, s['Inventory Value'])             # dollars on items that show no stock quantity anywhere
    for site in ONHAND_SITES:
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

    # ---- one key per physical shipment (a container number can be re-used on a later voyage)
    cont = po['Container Numb'].fillna('').astype(str).str.strip()
    cont = cont.where(~cont.str.lower().isin(['nan', 'none', 'nat']), '')
    po['Container ID'] = cont.str.split('/').str[0].str.upper()
    bl = po['BL Number'].fillna('').astype(str).str.strip()
    bl = bl.where(~bl.str.lower().isin(['nan', 'none', 'nat']), '')
    alt_key = po['Arrival'].dt.strftime('%Y%m%d').fillna('')
    po['Shipment Key'] = (po['Container ID'] + '|' + bl.where(bl != '', alt_key)).where(po['Container ID'] != '', '')
    return po, meta


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
    order = [n for n in names if 'fcst' in n.lower() or 'forecast' in n.lower()] + names
    fc, used = None, None
    for n in order:
        try:
            t = xls.parse(n)
        except Exception:
            continue
        t.columns = [str(c).strip() for c in t.columns]
        if 'Item Code' in t.columns and any(c in MONTH_NAMES for c in t.columns):
            fc, used = t, n
            break
    if fc is None:
        raise DataError(f"'{xls_name}' has no sheet with 'Item Code' and month columns (January ... December).")
    meta = {'warnings': [], 'sheet': used, 'rows_raw': len(fc)}
    fc['Item Code'] = clean_id(fc['Item Code'])
    fc = fc[fc['Item Code'].str.fullmatch(r'\d+', na=False)].copy()
    mcols = [c for c in fc.columns if c in MONTH_NAMES]
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

    upcoming = []                                           # months after this one, until the file runs out
    for i in range(12):
        mname = MONTH_NAMES[(today.month + i) % 12]
        if mname in mcols:
            upcoming.append(mname)
        else:
            break
    if len(upcoming) >= 3:
        cols3, cols6 = upcoming[:3], upcoming[:6]
    else:
        cols3, cols6 = mcols[-3:], mcols[-6:]
        meta['warnings'].append("The forecast file does not cover the next 3 months - the latest months in the file are used instead. Please load a newer forecast.")
    fc['3M_Avg_Forecast_Cases'] = fc[cols3].mean(axis=1)
    meta.update({'cols3': cols3, 'cols6': cols6, 'all': mcols})
    return fc, meta


def load_bids(path):
    raw, sheet = read_sheet(path, 'Sheet2', {'CPJ Code', 'CPJ Item Description', 'Customer Name', 'CPJ Class', 'Bid Status'})
    raw.columns = [str(c).strip() for c in raw.columns]
    b = raw.rename(columns={'CPJ Code': 'Item Code', 'CPJ Item Description': 'Item Description',
                            'Customer Name': 'Customer', 'CPJ Class': 'Item Class'})
    b['Item Code'] = clean_id(b['Item Code'])
    b = b[b['Item Code'].str.fullmatch(r'\d+', na=False)].copy()
    b['Custom Category'] = b['Item Class'].map(CAT_MAP).fillna('UNCLASSIFIED')
    b['Item Status'] = pd.to_numeric(b['Item Status'], errors='coerce')
    b['Bid Status'] = b['Bid Status'].fillna('').astype(str).str.strip()
    return b, {'warnings': [], 'sheet': sheet, 'rows': len(b)}


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
           'm3': {}, 'vt': pd.DataFrame(), 'fc_meta': {}, 'meta': {}, 'errors': {}}

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


_all_tabs = st.tabs(["📊 Executive Spend", "🚢 GIT & Expediting", "⚠️ Inventory Health", "🏆 Bid Performance", "🎯 Bid Purchasing",
                     "📈 Forecast", "🤝 Supplier Scorecard", "💵 Cash Flow", "📉 Price Index (PPV)", "✅ Data Checks"])
tabs = list(_all_tabs[:4]) + list(_all_tabs[5:]) + [_all_tabs[4]]          # tabs[0..8] are the original nine; tabs[9] is Bid Purchasing

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
    if len(po_filtered):
        _cl = pct_of(po_filtered['Convertible'].sum(), len(po_filtered))
        _cu = pct_of(po_filtered.loc[po_filtered['Convertible'], 'USD $'].sum(), po_filtered['USD $'].sum())
        st.caption(f"Case volumes cover {_cl:,.0f}% of PO lines ({_cu:,.0f}% of the dollars). The rest are mostly plant raw materials bought by the pound "
                   f"or gallon, which have no case size, and are left out of case totals. Warehouse inventory = MDC + SEP + MJM + Bonded (Stores, Plants and OM not included).")
    st.divider()

    # ---------------- Warehouse breakdown ----------------
    st.subheader("Breakdown by Warehouse")
    st.caption("Left: the receiving warehouse code ('Whs') on each PO line. Right: where today's inventory value sits, by site, from the Purchase Report. "
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
            st.caption(f"{yr} year to date (Jan-{MONTH_NAMES[last_m - 1][:3]}): {ytd:,.0f} cases" +
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
                st.caption(f"Unclassified items (${hidden:,.0f}) are not shown in this chart.")
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

        st.markdown("**Case-conversion accuracy checks**")
        st.caption("Two independent checks that the dollars-to-cases conversion is sound. Check 1 should stay flat from month to month - a sudden jump or drop means "
                   "some items were converted with the wrong case size. Check 2 applies to catch-weight items (priced per KG, ordered by the case): it compares the case weight "
                   "taken from the item description with the weight implied by the dollars actually on the PO line.")
        cw = pd.DataFrame()
        ck1, ck2 = st.columns(2)
        with ck1:
            sale = trend[trend['Item grp'].isin(SALEABLE_GROUPS) & ~trend['Item grp'].isin(PLANT_GROUPS)]
            cv_ = sale[sale['Convertible'] & (sale['Order Qty (Cases)'] > 0)]
            if not cv_.empty:
                m1 = cv_.groupby('Order Month').agg(Spend=('USD $', 'sum'), Cases=('Order Qty (Cases)', 'sum')).reset_index()
                m1['Cost per Case'] = np.where(m1['Cases'] > 0, m1['Spend'] / m1['Cases'].replace(0, 1), np.nan)
                m1['Share'] = [pct_of(cv_.loc[cv_['Order Month'] == m, 'USD $'].sum(), sale.loc[sale['Order Month'] == m, 'USD $'].sum()) for m in m1['Order Month']]
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=m1['Order Month'], y=m1['Cost per Case'], mode='lines+markers', name='$ per case', line=dict(color='#1f77b4', width=3)))
                fig.add_trace(go.Bar(x=m1['Order Month'], y=m1['Share'], name='% of spend converted to cases', yaxis='y2', opacity=0.25, marker_color='#2ca02c'))
                fig.update_layout(title="Check 1: average cost per case, and share of spend converted", yaxis=dict(title='$ per case'),
                                  yaxis2=dict(title='% of spend converted', overlaying='y', side='right', range=[0, 105]), height=380)
                category_axis(fig, -45)
                show_chart(fig, key="t1_chk1")
                st.caption("Saleable items only (plant raw materials are bought in bags, drums and gallons and would distort an average).")
            else:
                st.info("No convertible saleable lines to check.")
        with ck2:
            cw = trend[trend['Implied KG per Case'].notna() & trend['Weight Dev %'].notna()].copy()
            if not cw.empty:
                cw['Within 10%'] = cw['Weight Dev %'].abs() <= 10
                m2 = cw.groupby('Order Month').agg(Lines=('PO no', 'size'), Ok=('Within 10%', 'mean')).reset_index()
                m2['Ok'] = m2['Ok'] * 100
                fig = go.Figure()
                fig.add_trace(go.Bar(x=m2['Order Month'], y=m2['Ok'], name='% of lines within 10%', marker_color='#2ca02c',
                                     text=m2['Ok'].map(lambda v: f"{v:,.0f}%"), textposition='outside'))
                fig.add_trace(go.Scatter(x=m2['Order Month'], y=m2['Lines'], name='Catch-weight lines checked', yaxis='y2', mode='lines+markers', line=dict(color='#444444')))
                fig.update_layout(title="Check 2: catch-weight lines where description weight matches the PO dollars", yaxis=dict(title='% within 10%', range=[0, 115]),
                                  yaxis2=dict(title='Lines checked', overlaying='y', side='right'), height=380)
                category_axis(fig, -45)
                show_chart(fig, key="t1_chk2")
                st.caption(f"{len(cw):,} catch-weight lines checked; {cw['Within 10%'].mean() * 100:,.0f}% agree within 10%. "
                           f"Typical gap: {cw['Weight Dev %'].abs().median():,.1f}% (natural weight variation is expected on 'AVG' items).")
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
    st.caption(f"Current open POs (Hst {OPEN_PO[0]}-{OPEN_PO[1]}). Buyers under 5% of open spend are grouped as 'Other Buyers'.")
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
            st.caption("Other Buyers = " + ", ".join(others))
    else:
        st.info("No open PO spend for the selected filters.")

# ==========================================================================
# TAB 2 — GIT & EXPEDITING
# ==========================================================================
with tabs[1], guard():
    st.header("Goods In Transit: Clearance Tracking & Exceptions")

    # ---------------- Clearance metrics (rebuilt) ----------------
    st.subheader("Clearance Metrics: Containers Arrived, Cleared (Yard) and Stripped by Month")
    st.caption("Each container is counted once (container number + bill of lading), in the month of its own date - not the month the PO was written. "
               "Arrived = reached the port. Cleared = moved to the yard. Stripped = unloaded. Supplier / Category filters apply; the Order Year / Month filters do not.")
    ship_src = by_supplier_cat(po_df)
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

        def _per_month(col):
            return ship[col].dt.strftime('%Y-%m').value_counts().reindex(mlist, fill_value=0)

        cm = pd.DataFrame({'Month': mlist, 'Arrived at port': _per_month('Arrival').values, 'Cleared (Yard)': _per_month('Yard').values,
                           'Stripped': _per_month('Stripped').values})
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
            st.caption(f"⚠️ {len(stale):,} containers show an arrival date more than 90 days ago but no yard or stripped date. "
                       "Either they are still waiting (follow up) or the dates were never entered in the PO file.")
        show_table(style0(cm[['Month', 'Arrived at port', 'Cleared (Yard)', 'Stripped', 'Avg days port to yard', 'Waiting at port (not yet cleared)',
                              'In yard (not yet stripped)']].iloc[::-1], one_dec=['Avg days port to yard']), height=260)

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
        st.caption("Red rows = Req dt has passed. Port / Yard containers are listed first, then On the Water; sorted by earliest Req dt. "
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
    st.caption("Every site quantity is converted to cases (packs by the Conversion Factor, KG items by the weight written in the item description). "
               "Total On-Hand = MDC + SEP + MJM + Stores + Bonded + Plants + OM; Allocated is shown separately (not added). "
               "Turnover = (3-month avg monthly sales × 12) ÷ Total On-Hand cases. Deleted items (90-99) excluded.")
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
        fig = px.bar(tv, x='Custom Category', y='Annualized Turnover (x)', text_auto=',.1f', title="Annualized turnover (x)")
        fig.update_layout(xaxis_tickangle=-45)
        show_chart(fig, key="t3_turn")
    with t2:
        site_long = tv.melt(id_vars='Custom Category', value_vars=ONHAND_SITES, var_name='Site', value_name='Cases')
        fig = px.bar(site_long, x='Custom Category', y='Cases', color='Site', title="Where the cases sit (by site)")
        fig.update_layout(xaxis_tickangle=-45)
        show_chart(fig, key="t3_sites")
    show_table(style0(tv, pct=['Sell-Through %']))

    # ---------------- Months of cover ----------------
    st.divider()
    st.subheader("Months of Cover by Category")
    st.caption("How long today's warehouse stock lasts at the current selling pace. Counted: active items (status 20) that are selling. "
               "Stock = MDC + SEP + MJM + Bonded cases - Stores, Plants and OM stock are NOT counted. "
               f"One heavily stocked item can count for at most {COVER_CAP_MONTHS} months, so a few big stocks cannot hide shortages elsewhere in the category. "
               "The right-hand chart shows the same thing item by item: the red share is items with under a month of stock.")
    ct = cover_table(inv_f)
    cover_charts(ct, "t3")
    if not ct.empty:
        show_table(style0(ct, one_dec=['Months of Cover', 'Months (no cap)'], pct=['Under 1 Month %', '1-3 Months %', 'Over 3 Months %']))

    # ---------------- Dead & slow ----------------
    st.divider()
    ds_src = inv_f[~inv_f['Item Status'].isin(DELETED + ([] if DEAD_STOCK_INCLUDES_DISCONTINUED else DISCONTINUED))]
    ds_src = ds_src[~ds_src['Custom Category'].isin(EXCLUDED_CATS)]
    r3a, r3b = st.columns(2)
    with r3a:
        st.subheader("Dead Stock by Category")
        st.caption("Inventory value on hand with no movement over the trailing 3 months (includes discontinued items - that is where dead stock usually sits).")
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
        st.caption("Actual sales below 70% of forecast (active items only).")
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
    st.caption("Safety stock = the item's lead time (in months) × its monthly forecast (average of the next 3 months in the CPJ Forecast file). "
               "Stock = warehouse cases (MDC + SEP + MJM + Bonded). Active / new items only. Unclassified, meat plant, juice plant, operations and beverage-system items are left out. "
               "By default only items that have a confirmed forecast are shown.")
    use_calc = st.checkbox("Also include items with no forecast (their safety stock is calculated from the last 3 months of sales)", value=False, key="ss_calc")
    min_fc = st.slider("Ignore items selling or forecast below (cases per month)", 0.0, 20.0, 1.0, 0.5, key="ss_minfc",
                       help="Items that move less than this are not worth a buyer's time. Set to 0 to see everything.")
    ss = inv_live[~inv_live['Custom Category'].isin(NO_SS_CATS)].copy()
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
    if n_tiny:
        st.caption(f"{n_tiny:,} very slow items (under {min_fc:g} case per month) are left out. Lower the slider to include them.")
    if not use_calc:
        n_nofc = int(((~inv_live['Has Forecast']) & ~inv_live['Custom Category'].isin(NO_SS_CATS) & (inv_live['Safety Stock (Cases)'] > 0)).sum())
        st.caption(f"{n_nofc:,} more active items sell but have no forecast, so they are not shown. Tick the box above to include them.")
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

        st.markdown("**25 items furthest below safety stock** - bar = % of safety stock covered (stock, then what has arrived, then on order). Dashed line = 100% of safety stock.")
        worst = below.assign(Cov=below['WH_Cases'] / below['Safety Stock (Cases)']).sort_values('Cov').head(25).iloc[::-1]
        ssv = worst['Safety Stock (Cases)']
        lab = worst['Item Number'] + ' ' + worst['Item Description'].astype(str).str.slice(0, 32)
        fig = go.Figure()
        for nm, col, colr in [('On hand', 'WH_Cases', '#1f77b4'), ('Arrived (port / yard)', 'Cases At Port', '#2ca02c'), ('On order', 'Cases On Order', '#f2c94c')]:
            fig.add_trace(go.Bar(y=lab, x=(worst[col] / ssv * 100).clip(upper=300), name=nm, orientation='h', marker_color=colr,
                                 customdata=worst[col], hovertemplate=nm + ": %{customdata:,.0f} cases<br>%{x:,.0f}% of safety stock<extra></extra>"))
        fig.add_vline(x=100, line_dash='dash', line_color='red')
        fig.update_layout(barmode='stack', height=680, xaxis_title="% of safety stock (capped at 300%)")
        show_chart(fig, key="t3_ss_worst")
        det = below.sort_values('WH_Cases')[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases At Port', 'Cases On Order',
                                             'Monthly Demand Used (Cases)', 'SS Basis', 'Safety Stock (Cases)', 'Lead Time (Months)', 'Inbound Status', 'Responsible Buyer']]
        det = det.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)', 'Cases At Port': 'Arrived (Cs)', 'Monthly Demand Used (Cases)': 'Monthly Demand Used (Cs)',
                                  'SS Basis': 'Demand Based On'})
        show_table(style0(det, one_dec=['Lead Time (Months)', 'Monthly Demand Used (Cs)', 'Safety Stock (Cases)']), height=350)
        csv_button(det, "⬇️ Export items at/below safety stock", "below_safety_stock.csv", "dl_ss")
    else:
        st.info("No items currently at or below their safety stock.")

    st.subheader("Safety Stock Alerts — Significant Forecast Changes")
    st.caption("Near-term forecast average vs trailing actual sales differs by more than 30% - buyers should reassess safety stock.")
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
    st.caption("On-hand warehouse stock + everything on order won't outlast the item's lead time against the near-term forecast.")
    rb = inv_live[inv_live['3M_Avg_Forecast_Cases'] > 0].copy()
    rb['Months Cover (On Hand + Pipeline)'] = (rb['WH_Cases'] + rb['Cases Inbound']) / rb['3M_Avg_Forecast_Cases']
    rf = rb[rb['Months Cover (On Hand + Pipeline)'] < rb['Lead Time (Months)']].sort_values('Months Cover (On Hand + Pipeline)')
    if not rf.empty:
        rf = rf[['Item Number', 'Item Description', 'Custom Category', 'WH_Cases', 'Cases Inbound', '3M_Avg_Forecast_Cases', 'Lead Time (Months)',
                 'Months Cover (On Hand + Pipeline)', 'Responsible Buyer']]
        rf = rf.rename(columns={'WH_Cases': 'Warehouse Stock (Cs)', '3M_Avg_Forecast_Cases': 'Near-Term Forecast (Cs)'})
        show_table(style0(rf, one_dec=['Lead Time (Months)', 'Months Cover (On Hand + Pipeline)', 'Near-Term Forecast (Cs)']), height=400)
        st.caption(f"{len(rf):,} items flagged.")
    else:
        st.info("No items currently flagged for reorder.")

    # ---------------- Item replacement / substitutes ----------------
    st.divider()
    st.subheader("Item Replacement & Substitute Item Performance")
    st.caption("Items are paired when they share the same item Category + Format in the Purchase Report. Each pair then gets a 0-100 match score from extra checks, "
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
    if bids_df.empty:
        st.info("Bid data not loaded.")
    else:
        st.caption("Availability uses MDC stock (cases). Items at status 50 / 80 (discontinued) and 90-99 (deleted) are not counted in the base availability view. "
                   "Discontinued bid codes appear only in the substitute-adjusted view and replacement table below.")
        bsub_lvl = st.radio("Show substitutes rated", list(SUB_LEVELS.keys()), index=1, horizontal=True, key="bid_sub_level",
                            help="High = very likely the same kind of product. Medium = probably. Any = every item with the same Category + Format.")
        b_min_score = SUB_LEVELS[bsub_lvl]
        bc = bids_df[bids_df['Bid Status'] == 'Current']
        bi = bc.groupby('Item Code').agg(Description=('Item Description', 'first'), Bid_Class=('Item Class', 'first'),
                                         Bid_Category=('Custom Category', 'first'), Bid_Item_Status=('Item Status', 'first'),
                                         Customer_Count=('Customer', 'nunique')).reset_index()
        inv_b = inv.copy()
        inv_b['Sub Key'] = add_sub_key(inv_b, SUB_BASE_LEVEL)
        inv_b.loc[~inv_b['Item Class'].isin(SALEABLE_GROUPS) | inv_b['Item Class'].isin(PLANT_GROUPS), 'Sub Key'] = ''
        cols_b = ['Item Number', 'Item Status', 'Avail_Cases', 'Cases At Port', 'Cases On Order', '3M_Avg_Depletion_Demand', 'Sub Key', 'Responsible Buyer']
        bi = bi.merge(inv_b[cols_b], left_on='Item Code', right_on='Item Number', how='left')
        bi['In Purchase Report'] = bi['Item Number'].notna()
        bi['Status'] = bi['Item Status'].fillna(bi['Bid_Item_Status']).fillna(0).astype(int)
        for c in ['Avail_Cases', 'Cases At Port', 'Cases On Order', '3M_Avg_Depletion_Demand']:
            bi[c] = bi[c].fillna(0.0)
        bi['Sub Key'] = bi['Sub Key'].fillna('')
        bi['Category'] = bi['Bid_Category']
        bi['Responsible Buyer'] = bi['Bid_Class'].map(BUYER_MAP).fillna(bi['Item Code'].map(last_buyer)).fillna('Unassigned')
        bi = bi[~bi['Status'].isin(DELETED)]
        bi['Discontinued'] = bi['Status'].isin(DISCONTINUED)

        # substitute candidates: active, in stock, enough stock to matter (more than MIN_SUB_MONTHS months)
        cand = inv_b[(inv_b['Item Status'] == 20) & (inv_b['Sub Key'] != '') & (inv_b['Avail_Cases'] > 0)]
        tgt = inv_b[inv_b['Item Number'].isin(bi['Item Code']) & (inv_b['Sub Key'] != '')]
        sub_pairs = find_substitutes(tgt, cand, b_min_score, min_months=MIN_SUB_MONTHS)
        sub_best = best_substitute(sub_pairs)
        if not sub_best.empty:
            best_sub = sub_best[['Item Number', 'Item Number (Sub)', 'Item Description (Sub)', 'Avail_Cases (Sub)', 'Sub Months', 'Match Confidence',
                                 'Match Score', 'Why Matched']].rename(columns={'Item Number': 'Item Code', 'Item Number (Sub)': 'Sub Item',
                                                                                'Item Description (Sub)': 'Sub Description', 'Avail_Cases (Sub)': 'Sub MDC Stock (Cs)'})
            bi = bi.merge(best_sub, on='Item Code', how='left')
        else:
            for c in ['Sub Item', 'Sub Description', 'Sub MDC Stock (Cs)', 'Sub Months', 'Match Confidence', 'Match Score', 'Why Matched']:
                bi[c] = np.nan
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
        show_table(style0(cov, one_dec=['Avg Months of Stock']))

        pa, pb = st.columns(2)
        with pa:
            st.subheader("Overall Bid Item Availability")
            st.caption("Current conditions - the bid code itself must have stock, or stock incoming.")
            fig = px.pie(base, names='Availability', color='Availability', color_discrete_map=colors, title=f"{len(base):,} unique bid items")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            show_chart(fig, key="t4_pie0")
        with pb:
            st.subheader("Availability incl. Substitutes / Replacements")
            st.caption(f"Adds active (status 20) substitutes rated '{bsub_lvl}' with more than {MIN_SUB_MONTHS} months of MDC stock; "
                       "discontinued bid codes count only when a substitute exists.")
            fig = px.pie(adj, names='Availability', color='Availability', color_discrete_map=colors, title=f"{len(adj):,} unique bid items")
            fig.update_traces(texttemplate="%{label}<br>%{value} (%{percent})")
            show_chart(fig, key="t4_pie1")
        m_a, m_b, m_c = st.columns(3)
        av0 = (base['Availability'] == 'Available').mean() * 100 if len(base) else 0
        av1 = adj['Availability'].isin(['Available', 'Available via Substitute']).mean() * 100 if len(adj) else 0
        m_a.metric("Item availability - bid codes only", f"{av0:,.0f}%")
        m_b.metric("Item availability - incl. substitutes", f"{av1:,.0f}%", f"{av1 - av0:+,.0f} pts", delta_color="normal")
        m_c.metric("Bid items rescued by a substitute", f"{(adj['Availability'] == 'Available via Substitute').sum():,}")
        if (adj['Availability'] == 'Available via Substitute').any():
            resc = adj[adj['Availability'] == 'Available via Substitute']['Match Confidence'].value_counts()
            st.caption("Confidence in those substitutes: " + ", ".join(f"{k} {v:,}" for k, v in resc.items()) + ". Change the match level above to be stricter or broader.")

        st.subheader("Stock vs. Demand")
        cat_f = st.selectbox("Category focus", ['All'] + sorted(base['Category'].unique()))
        sc_src = base if cat_f == 'All' else base[base['Category'] == cat_f]
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
            show_chart(fig, key="t4_scatter")
            st.caption(f"{len(sc_src) - len(pts):,} items with zero stock or zero demand are not plotted (log scale) - see the at-risk table below.")
        else:
            st.info("No items with both stock and demand.")

        st.markdown("**Months of cover for the bid items, by category**")
        st.caption("Same definition as the Inventory Health tab: active bid items that are selling, warehouse stock (MDC + SEP + MJM + Bonded) without Stores, all in cases.")
        ctb = cover_table(inv[inv['Item Number'].isin(base['Item Code'])])
        cover_charts(ctb, "t4")

        st.subheader("⚠️ At-Risk Bid Items - No Stock, Not On Order, Not At Port")
        risk = base[(base['Avail_Cases'] <= 0) & (base['Cases On Order'] <= 0) & (base['Cases At Port'] <= 0)]
        rk = risk[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Has Sub', 'Sub Item', 'Sub Description', 'Match Confidence']].rename(
            columns={'Customer_Count': '# Bid Customers', 'Has Sub': 'Substitute Available'})
        show_table(style0(rk))
        st.caption(f"{len(risk):,} of {len(base):,} unique bid items are at risk; {int(risk['Has Sub'].sum()):,} of them have a usable substitute.")
        csv_button(rk, "⬇️ Export at-risk bid items", "at_risk_bid_items.csv", "dl_risk")

        st.subheader("Bid Items on Discontinued Codes - Replacement Needed")
        dtab = bi[bi['Discontinued']][['Item Code', 'Description', 'Status', 'Category', 'Customer_Count', 'Responsible Buyer', 'Sub Item', 'Sub Description',
                                       'Sub MDC Stock (Cs)', 'Match Confidence', 'Match Score', 'Why Matched']]
        dtab = dtab.rename(columns={'Customer_Count': '# Bid Customers', 'Sub Item': 'Suggested Replacement', 'Sub Description': 'Replacement Description'})
        dtab['Suggested Replacement'] = dtab['Suggested Replacement'].fillna('No replacement found')
        show_table(style0(dtab))
        csv_button(dtab, "⬇️ Export discontinued bid items", "discontinued_bid_items.csv", "dl_disc")

        with st.expander("All Bid Item Coverage (detail)"):
            det = base[['Item Code', 'Description', 'Category', 'Customer_Count', 'Responsible Buyer', 'Avail_Cases', 'Cases At Port', 'Cases On Order',
                        'Months of Stock', 'Availability']].rename(columns={'Customer_Count': '# Bid Customers', 'Avail_Cases': 'MDC Stock (Cs)'})
            show_table(style0(det, one_dec=['Months of Stock']))

# ==========================================================================
# TAB 5 — FORECAST
# ==========================================================================
with tabs[4], guard():
    st.header("6-Month Forecast")
    if fc_df.empty:
        st.info("Forecast data not loaded.")
    else:
        c6 = fc_meta['cols6']
        st.caption(f"Forecast window: {', '.join(c6)}. Near-term average = {', '.join(fc_meta['cols3'])}. Discontinued (50/80) and deleted items excluded. "
                   "Forecast quantities are in cases and are shown unrounded, so slow items keep their fractions of a case.")
        q = st.text_input("🔍 Search Item Code or Description:", "")
        bad_codes = set(sales_df[sales_df['Item Status'].isin(DISCONTINUED + DELETED)]['Item Number'])
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
        st.caption("Warehouse stock (MDC+SEP+MJM+Bonded) and total inbound (on order + arrived, full open pipeline) ÷ near-term forecast average, by category.")
        cv = fd.merge(inv[['Item Number', 'Custom Category', 'WH_Cases', 'Cases On Order', 'Cases At Port']], left_on='Item Code', right_on='Item Number', how='left')
        cv['Custom Category'] = cv['Custom Category'].fillna('UNCLASSIFIED')
        cv = cv[~cv['Custom Category'].isin(['OPERATIONS', 'BEVERAGE SYSTEM', 'UNCLASSIFIED'])]
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
    st.caption("Fill outcome per line: received/closed lines (Hst 75-85) use received ÷ ordered, never more than 100% per line (extra quantity is counted as an over-shipment, not credit). "
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
                    st.caption(f"{hidden_n:,} dropped / short lines have no item category in the Purchase Report and are not shown in this chart.")

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
        st.caption("High-SKU vendors (e.g. Dot Foods, KeHE) naturally show more dropped lines - compare the % columns as well as counts. "
                   "Ordered / Received (Cs) cover lines that have a case size.")

        # ---------------- Fill deviation (rebuilt) ----------------
        st.subheader("Fill Deviation Score — Month over Month")
        st.caption("By month the PO was written (2025 onward). Fill rate is capped at 100% per line, so an over-shipment can no longer push a month off the chart or hide a shortfall elsewhere. "
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
                st.caption("Still being delivered (more than 20% of the month's lines are open - grey dots): " + ", ".join(inc) + ". Their fill rate will change as orders close.")
            tbl = mo[['Order Month', 'Lines', 'Dropped', 'Short', 'Over', 'Fill Rate %', 'Deviation (pts)', 'Open Share %']].rename(columns={'Over': 'Over-shipped', 'Open Share %': 'Still open %'})
            show_table(style0(tbl.iloc[::-1], one_dec=['Fill Rate %', 'Deviation (pts)'], pct=['Still open %']), height=300)
        else:
            st.info("Not enough completed lines from 2025 onward.")

        # ---------------- Composite ----------------
        st.subheader("Vendor Composite Scorecard")
        st.caption("Score = 50% fill rate + 50% on-time % (received lines, on or before Req dt). Higher is better.")
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
        st.caption(f"Open PO lines (Hst {OPEN_PO[0]}-{OPEN_PO[1]}) on active items, Otp = P01 excluded. Each line is paid when the goods are expected to arrive "
                   "(stripped, yard, arrival, ETA, then Req dt - the first one known) plus the supplier's payment terms from the Vendor Terms file. "
                   "Past months are excluded from the forward view. Ignores the Order Year/Month sidebar filters (all open orders are included); Supplier / Category filters apply.")
        pay_mode = st.radio("Time the payments by", _vendor_modes, index=0, horizontal=True, key="cf_mode")
    else:
        pay_mode = _vendor_modes[1]
        st.caption(f"Open PO lines (Hst {OPEN_PO[0]}-{OPEN_PO[1]}) on active items, Otp = P01 excluded, timed by Req dt. Past months are excluded from the forward view. "
                   "Ignores the Order Year/Month sidebar filters (all open orders are included); Supplier / Category filters apply.")
        st.info("No Vendor Terms were loaded (VendorTerms.xlsx is missing, unreadable or empty), so payments are timed from the requested date. Put a working copy of the file in the dashboard folder to time them by each supplier's terms.")
    use_terms = pay_mode == _vendor_modes[0]
    lag = 0
    if not use_terms:
        lag = st.number_input("Payment lag after Req dt (days)", 0, 180, 0, 15)
    cfl = by_supplier_cat(po_df[po_df['Status Code'].between(*OPEN_PO)]).copy()
    cfl = cfl[~cfl['Item Status'].isin(DISCONTINUED + DELETED)]
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
        st.caption(f"{len(no_date):,} open lines have no date to time the payment from (${no_date['USD $'].sum():,.0f}) and are not in the timeline.")
    if use_terms:
        if n_late_open:
            st.caption(f"{n_late_open:,} lines (${usd_late_open:,.0f}) are past their expected date but have not arrived yet. They are assumed to arrive today at the earliest, so their payment is timed from today.")
        _all_open = cfl['USD $'].sum()
        _assumed = cfl.loc[~cfl['Terms Confirmed'], 'USD $'].sum()
        if _assumed > 0:
            _who = cfl.loc[~cfl['Terms Confirmed'], 'Supplier'].nunique()
            st.caption(f"${_assumed:,.0f} ({pct_of(_assumed, _all_open):.0f}% of open dollars, {_who:,} suppliers) is timed with an assumed {DEFAULT_TERMS_DAYS}-day term, because the supplier's "
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

        st.subheader("Top 20 Item Categories with Price Increases — Last 6 Months")
        st.caption("Item category = Purchase Report 'Category' (column AB), e.g. CHOCOLATE, FISH, CEREAL. Items with no category are not shown.")
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
# TAB 9 — DATA CHECKS
# ==========================================================================
def run_data_checks():
    """Runs every logic check over the compiled data. Returns (checks, details):
    checks = list of dicts (Area, Check, Result, Found, What it means); details = {title: DataFrame} for the drill-down lists."""
    checks, details = [], {}
    LEVEL = {'OK': '✅ OK', 'Review': '⚠️ Review', 'Problem': '❌ Problem'}

    def add(area, name, level, found, note):
        checks.append({'Area': area, 'Check': name, 'Result': LEVEL[level], 'Found': found, 'What it means': note, '_rank': {'Problem': 0, 'Review': 1, 'OK': 2}[level]})

    def level_for(n, review_at=1, problem_at=None):
        if problem_at is not None and n >= problem_at:
            return 'Problem'
        return 'Review' if n >= review_at else 'OK'

    def group(area, fn):
        try:
            fn()
        except Exception as e:
            add(area, 'Checks in this group could not run', 'Review', type(e).__name__, f"{e}")

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
        nolead = live[live['Lead Time Missing']]
        add('Purchase Report', 'Active items with no lead time', level_for(len(nolead)), f"{len(nolead):,}",
            f"A {DEFAULT_LEAD_DAYS}-day lead time is assumed for them in safety stock." if len(nolead) else "All active items have a lead time.")
        tot_val = float(s['Inventory Value'].sum())
        site_val = float(sum(s[f'Val_{x}'].sum() for x in ONHAND_SITES))
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
        cur = bids_df[bids_df['Bid Status'] == 'Current']
        known = set(sales_df['Item Number'])
        miss = cur[~cur['Item Code'].isin(known)]['Item Code'].nunique()
        add('Bids', 'Current bid items not found in the Purchase Report', level_for(miss), f"{miss:,} of {cur['Item Code'].nunique():,}", "They show as not in stock." if miss else "All bid items exist.")
        deleted_ = sales_df[sales_df['Item Number'].isin(cur['Item Code']) & sales_df['Item Status'].isin(DELETED)]
        add('Bids', 'Current bid items on deleted codes (status 90-99)', level_for(len(deleted_)), f"{len(deleted_):,}", "They are excluded from the Bid tab." if len(deleted_) else "None.")
        add('Bids', 'Bid statuses in the file', 'OK', "; ".join(f"{k or '(blank)'}: {v:,}" for k, v in bids_df['Bid Status'].value_counts().items()), "Only 'Current' bids are analysed.")
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

    return checks, details


with tabs[8], guard():
    st.header("✅ Data Checks")
    st.caption("Every number on the other tabs is built from the source files. These checks look for gaps, mismatches and anything that could make a figure unreliable. "
               "Green = fine. Yellow = worth a look. Red = fix before relying on the related figures. These checks use all the data, not the sidebar filters.")
    _checks, _details = run_data_checks()
    cdf = pd.DataFrame(_checks)
    n_ok = int((cdf['_rank'] == 2).sum()); n_rev = int((cdf['_rank'] == 1).sum()); n_bad = int((cdf['_rank'] == 0).sum())
    h1, h2, h3 = st.columns(3)
    h1.metric("✅ Passed", f"{n_ok:,}")
    h2.metric("⚠️ Worth a look", f"{n_rev:,}")
    h3.metric("❌ Needs fixing", f"{n_bad:,}")
    if n_bad:
        st.error("Some checks failed. Figures that depend on them may be wrong until the source data is fixed.")
    elif n_rev:
        st.warning("No failures, but a few items deserve a look (see the yellow rows).")
    else:
        st.success("All checks passed.")
    area_sel = st.multiselect("Show areas", list(dict.fromkeys(cdf['Area'])), default=list(dict.fromkeys(cdf['Area'])), key="chk_areas")
    only_open = st.checkbox("Only show items that need attention", value=False, key="chk_only")
    view_ = cdf[cdf['Area'].isin(area_sel)]
    if only_open:
        view_ = view_[view_['_rank'] < 2]
    view_ = view_.sort_values(['_rank'], kind='stable').drop(columns='_rank')
    show_table(view_[['Result', 'Area', 'Check', 'Found', 'What it means']], height=560)
    csv_button(view_, "⬇️ Export the check results", "data_checks.csv", "dl_checks")
    if _details:
        st.subheader("Lists behind the yellow and red rows")
        for _i, (_title, _tbl) in enumerate(_details.items()):
            with st.expander(f"{_title} ({len(_tbl):,} shown)"):
                show_table(style0(_tbl.reset_index(drop=True)), height=300)
                csv_button(_tbl, "⬇️ Export this list", re.sub(r'[^a-z0-9]+', '_', _title.lower())[:50] + ".csv", f"dl_chk_{_i}")

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
    for sname in ONHAND_SITES:
        site[sname] = bi[f'{sname}_Cs'].clip(lower=0).fillna(0.0)
    bi['MDC Hold (Cs)'] = 0.0
    if p['use_wms'] and not wms.empty:
        w = wms.drop_duplicates('Item Number').set_index('Item Number')
        site['MDC'] = cases(bi['Item Number'].map(w['WMS OK Units']).fillna(0.0))
        bi['MDC Hold (Cs)'] = cases(bi['Item Number'].map(w['WMS HOLD Units']).fillna(0.0))
    if p['count_hold']:
        site['MDC'] = site['MDC'] + bi['MDC Hold (Cs)']
    chosen = [s_ for s_ in ONHAND_SITES if s_ in p['sites']]
    for sname in ONHAND_SITES:
        bi[f'{BP_SITE_LABEL[sname]} (Cs)'] = site[sname]
    bi['On Hand (Cs)'] = sum(site[s_] for s_ in chosen) if chosen else 0.0
    bi['Other Sites (Cs)'] = sum(site[s_] for s_ in ONHAND_SITES if s_ not in chosen)

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
    st.caption("Stock coverage = what is in our sites + what is on order, divided by monthly demand. The CPJ forecast comes first; where an item has none, the "
               "Purchase Report's own forecast, then recent sales. An item is at risk when that cover is shorter than its supplier lead time "
               "(your weekly sheet's 'Stock Position' below zero). Only MDC, MJM, SEP, SEZ and OM are counted unless you change it.")

    # ---------------- settings
    s1, s2, s3 = st.columns(3)
    with s1:
        _list_opts = list(_lists.keys()) + ['Every live item']
        _def_list = ['Weekly report list'] if 'Weekly report list' in _lists else (['Current bids (BIDS file)'] if 'Current bids (BIDS file)' in _lists else ['Every live item'])
        sel_lists = st.multiselect("Items to review", _list_opts, default=_def_list, key="bp_lists",
                                   help="Pick one or more lists. 'Weekly report list' is the Master-All sheet of your M3 workbook; the others are the Sandals / RIU sheets and the BIDS file.")
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
                                   help="MDC, MJM, SEP, SEZ and OM are our sites. Stores and Plants are shown for information only unless you add them.")
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
            st.caption(f"{int(bpf['Lead Time Missing'].sum()):,} items have no lead time in the Purchase Report, so {DEFAULT_LEAD_DAYS} days is assumed for them.")

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
                st.caption(f"${_unconf:,.0f} of this uses an assumed {DEFAULT_TERMS_DAYS}-day term, because the supplier has no confirmed terms in the Vendor Terms file." if HAS_TERMS else
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
        st.caption(f"{len(tv):,} items. Sorted: bid items first, then the shortest cover relative to lead time. "
                   "Stock Position = months of cover (stock + on order) minus lead time; below zero means it will run out before a new order could arrive.")
        show_table(style0(shown.head(1500), one_dec=['Suggested Order (Cs)', 'Demand (Cs/mo)', 'On Hand (Cs)', 'MDC (Cs)', 'SEP (Cs)', 'MJM (Cs)', 'SEZ (Cs)', 'OM (Cs)', 'MDC Hold (Cs)',
                                                     'On Order (Cs)', 'On Order M3 (Cs)', 'On Order PO file (Cs)', 'Months On Hand', 'Months Incl. On Order', 'Stock Position (months)',
                                                     'Sub Stock (Cs)', 'Sub Months', 'Other Sites (Cs)'], money=['Est. Order Cost (USD)']), height=520)
        if len(tv) > 1500:
            st.caption("Only the first 1,500 rows are shown on screen. The downloads below hold every row.")
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
                st.caption(f"The Excel download could not be built ({type(e_).__name__}). The CSV downloads still work.")

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
                st.caption(f"Note: M3 shows {m3_oo:,.0f} cases on order, the PO Dates file shows {po_oo:,.0f}. The chart only includes what the PO Dates file has dates for.")
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
