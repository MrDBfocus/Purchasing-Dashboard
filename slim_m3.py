"""Make a small, dashboard-only copy of the weekly M3 Bid Purchasing workbook (for GitHub's 25 MB upload limit).

Keeps only the sheets the dashboard reads, as values instead of formulas and in the same cell positions, and drops everything
else (the other sheets, pivot tables, calculation chain, external links). Your original file is never changed.

Usage:   python3 slim_m3.py "M3 Purchasing Report - Bid.xlsx"
         python3 slim_m3.py "M3 Purchasing Report - Bid.xlsx" some_folder/M3_Purchasing_Report-_Bid.xlsx
Needs:   pip install openpyxl   (already in the dashboard's requirements.txt)
"""
import sys, time, zipfile, os, shutil, datetime
import openpyxl
from openpyxl.cell import WriteOnlyCell

# openpyxl writes numbers with 16 significant digits; Excel stores 17. Write the exact value so every number round-trips unchanged.
import openpyxl.cell._writer as _w
from openpyxl.compat import strings as _strings
def _exact_string(value):
    if isinstance(value, float) and value == value and value not in (float('inf'), float('-inf')):
        return repr(value)
    return _strings.safe_string(value)
_w.safe_string = _exact_string

KEEP = ['Master-All', 'Sandals Report', 'RIU Report', 'Bid Universe', 'Purchase Report', 'WMS', 'Sandals Bid', 'manual']   # names the dashboard looks up

if len(sys.argv) < 2:
    sys.exit(__doc__)
src = sys.argv[1]
dst = sys.argv[2] if len(sys.argv) > 2 else 'M3_Purchasing_Report-_Bid.xlsx'    # the name the dashboard looks for
if os.path.abspath(src) == os.path.abspath(dst):
    sys.exit('The output must be a different file from the original (the original is never overwritten).')
t0 = time.time()
wb_in = openpyxl.load_workbook(src, read_only=True, data_only=True)
names_in = wb_in.sheetnames
if 'Purchase Report' not in names_in:
    sys.exit(f"This workbook has no 'Purchase Report' sheet (sheets found: {', '.join(names_in)}), so the dashboard could not use it.")
wb_out = openpyxl.Workbook(write_only=True)

for name in names_in:
    if name not in KEEP:
        continue
    ws_in = wb_in[name]
    rows = []
    last_col = 0
    for r in ws_in.iter_rows(values_only=True):
        r = list(r)
        while r and r[-1] is None:                  # trim blank cells at the end of each row
            r.pop()
        last_col = max(last_col, len(r))
        rows.append(r)
    while rows and not rows[-1]:                    # trim blank rows at the bottom
        rows.pop()
    ws_out = wb_out.create_sheet(title=name)
    ws_out.sheet_state = ws_in.sheet_state
    for r in rows:
        out = []
        for v in r:
            if isinstance(v, str) and v[:1] == '=':   # keep text that merely starts with '=' as text, not a formula
                c = WriteOnlyCell(ws_out, value=v)
                c.data_type = 's'
                out.append(c)
            else:
                out.append(v)
        ws_out.append(out)
    print(f'{name:16s} rows={len(rows):6d} cols={last_col:3d}  ({time.time()-t0:.0f}s)', flush=True)

tmp = dst + '.tmp'
wb_out.save(tmp)
wb_in.close()

# repack at maximum compression
with zipfile.ZipFile(tmp) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
    for item in zin.infolist():
        zout.writestr(item.filename, zin.read(item.filename))
os.remove(tmp)
print(f'done: {os.path.getsize(dst)/1e6:.2f} MB in {time.time()-t0:.0f}s', flush=True)
