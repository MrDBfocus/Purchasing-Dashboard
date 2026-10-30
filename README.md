# CPJ Purchasing Dashboard - Revision 4 (administrator notes)

This page is for whoever looks after the dashboard. Buyers and managers do not need it.

## What is in this folder
| File | Purpose |
|---|---|
| `app.py` | The dashboard |
| `manage_users.py` | Adds, removes and resets sign-ins |
| `requirements.txt` | The Python packages the dashboard needs |
| `users.json` | Created by `manage_users.py` (keep it private, do not share or upload it) |
| The 4 source files | `Purchase_Report_Sales_Analysis_with_Raw_Depletions.csv`, `PO_Dates.xlsx`, `CPJ_FORECAST.xlsx`, `BIDS.xlsx` |

## First-time setup
1. Install the packages: `pip install -r requirements.txt`
2. Put the four source files in this folder (names with spaces, e.g. `PO Dates.xlsx`, also work).
3. Add the people who may sign in (about 8):
   `python manage_users.py add jsmith --name "Jane Smith"` (you are asked for a password; minimum 8 characters)
4. Start the dashboard: `streamlit run app.py`

## Day to day
* Replace a source file with a fresh export and the dashboard reloads by itself. The **Reload data files** button in the sidebar forces it.
* Close the Excel file before replacing it. If it is still open the dashboard says so in plain words.
* Forgot a password: `python manage_users.py password jsmith`. Someone leaves: `python manage_users.py remove jsmith`. Who has access: `python manage_users.py list`.
* A user name is locked for 10 minutes after 5 wrong passwords. Sessions end after 12 hours.

## Hosting on Streamlit Community Cloud (or any shared server)
Do not upload `users.json`. Run `python manage_users.py export-secrets` and paste the output into the app's **Secrets** box; the dashboard reads users from there.

## Settings you may want to change (top of `app.py`, section CONFIG)
* `WHS_LABELS` - friendly names for the warehouse codes on the PO file, for example `{'MDC': 'Main Distribution Center', 'OPS': 'Operations'}`. Codes seen today: MDC, MJM, MR1, OPS, KR1, DH1, CON, MJP, SEP, SEZ, MPP, MBS, MBW, OM1.
* `OPEN_PO = (20, 49)` - which Hst codes count as an open order.
* `COVER_CAP_MONTHS = 6` - one heavily stocked item can count for at most this many months in "months of cover".
* `INCLUDE_CURRENT_MONTH_SALES = False` - sales averages use the last three complete months. Set to `True` to include the month in progress.
* `BUYER_MAP`, `CAT_MAP` - which item group belongs to which buyer / category.

## If a number looks wrong
Open the **Data Checks** tab first. It lists every check run on the source files (missing case sizes, duplicate PO lines, dates out of order, totals that do not tie out, ...) and the exact lines behind each warning, with a download button.
