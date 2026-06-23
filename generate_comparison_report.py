import csv
from collections import Counter

# Read v1 and v2 results
v1_data = []
with open('output/supplier_scraping_assessment.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        v1_data.append(row)

v2_data = []
with open('output/supplier_scraping_assessment_v2.csv', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        v2_data.append(row)

# Build lookups
v1_lookup = {r['prefix']: r for r in v1_data}
v2_lookup = {r['prefix']: r for r in v2_data}

all_prefixes = sorted(set(v1_lookup.keys()) | set(v2_lookup.keys()))

# Categorize changes
improvements = []
regressions = []
same = []

for prefix in all_prefixes:
    v1 = v1_lookup.get(prefix, {})
    v2 = v2_lookup.get(prefix, {})

    v1_v = v1.get('verdict', 'N/A')
    v2_v = v2.get('verdict', 'N/A')

    if v1_v == 'WORKS' and v2_v in ('MARGINAL', 'WONT WORK'):
        regressions.append({'prefix': prefix, 'v1': v1_v, 'v2': v2_v, 'v1_r': v1.get('reason',''), 'v2_r': v2.get('reason','')})
    elif v1_v in ('MARGINAL', 'WONT WORK') and v2_v == 'WORKS':
        improvements.append({'prefix': prefix, 'v1': v1_v, 'v2': v2_v, 'v1_r': v1.get('reason',''), 'v2_r': v2.get('reason','')})
    else:
        same.append({'prefix': prefix, 'v1': v1_v, 'v2': v2_v})

# Stats
v1_verdicts = Counter(r['verdict'] for r in v1_data)
v2_verdicts = Counter(r['verdict'] for r in v2_data)

def get_products_by_verdict(data):
    return {v: sum(int(r.get('product_count',0)) for r in data if r.get('verdict')==v) for v in ['WORKS','MARGINAL','WONT WORK']}

v1_products = get_products_by_verdict(v1_data)
v2_products = get_products_by_verdict(v2_data)

# Generate comparison report
report = """# Supplier Scraping Assessment: v1 vs v2 Comparison Report
Generated: 2026-06-18

---

## Summary Comparison

| Metric | v1 | v2 | Change |
|--------|----|----|--------|
| **WORKS** | {v1_works} suppliers ({v1_works_p:,} products) | {v2_works} suppliers ({v2_works_p:,} products) | {works_chg_sup} suppliers, {works_chg_prod:,} products |
| **MARGINAL** | {v1_marg} suppliers ({v1_marg_p:,} products) | {v2_marg} suppliers ({v2_marg_p:,} products) | {marg_chg_sup} suppliers, {marg_chg_prod:,} products |
| **WONT WORK** | {v1_wont} suppliers ({v1_wont_p:,} products) | {v2_wont} suppliers ({v2_wont_p:,} products) | {wont_chg_sup} suppliers, {wont_chg_prod:,} products |

---

## Changes Summary

- **Total Changes**: {total_chg} suppliers changed status
- **Improvements**: {imp_cnt} suppliers improved (MARGINAL/WONT -> WORKS)
- **Regressions**: {reg_cnt} suppliers worsened (WORKS -> MARGINAL/WONT)
- **Same**: {same_cnt} suppliers unchanged

---

## Improvements (v1 -> v2 got better)

""".format(
    v1_works=v1_verdicts.get('WORKS',0), v2_works=v2_verdicts.get('WORKS',0),
    v1_works_p=v1_products.get('WORKS',0), v2_works_p=v2_products.get('WORKS',0),
    works_chg_sup=v2_verdicts.get('WORKS',0)-v1_verdicts.get('WORKS',0),
    works_chg_prod=v2_products.get('WORKS',0)-v1_products.get('WORKS',0),
    v1_marg=v1_verdicts.get('MARGINAL',0), v2_marg=v2_verdicts.get('MARGINAL',0),
    v1_marg_p=v1_products.get('MARGINAL',0), v2_marg_p=v2_products.get('MARGINAL',0),
    marg_chg_sup=v2_verdicts.get('MARGINAL',0)-v1_verdicts.get('MARGINAL',0),
    marg_chg_prod=v2_products.get('MARGINAL',0)-v1_products.get('MARGINAL',0),
    v1_wont=v1_verdicts.get('WONT WORK',0), v2_wont=v2_verdicts.get('WONT WORK',0),
    v1_wont_p=v1_products.get('WONT WORK',0), v2_wont_p=v2_products.get('WONT WORK',0),
    wont_chg_sup=v2_verdicts.get('WONT WORK',0)-v1_verdicts.get('WONT WORK',0),
    wont_chg_prod=v2_products.get('WONT WORK',0)-v1_products.get('WONT WORK',0),
    total_chg=len(improvements)+len(regressions),
    imp_cnt=len(improvements),
    reg_cnt=len(regressions),
    same_cnt=len(same)
)

if improvements:
    for i in improvements:
        report += """- **{prefix}**: {v1} -> {v2}
  - v1: {v1_r}
  - v2: {v2_r}

""".format(prefix=i['prefix'], v1=i['v1'], v2=i['v2'], v1_r=i['v1_r'], v2_r=i['v2_r'])
else:
    report += "_No improvements found_\n\n"

report += """---

## Regressions (v1 -> v2 got worse)

"""

if regressions:
    for r in regressions:
        report += """- **{prefix}**: {v1} -> {v2}
  - v1: {v1_r}
  - v2: {v2_r}

""".format(prefix=r['prefix'], v1=r['v1'], v2=r['v2'], v1_r=r['v1_r'], v2_r=r['v2_r'])
else:
    report += "_No regressions found_\n\n"

report += """---

## Supplier-by-Supplier Comparison

| Prefix | Supplier | v1 Verdict | v2 Verdict | Change |
|--------|----------|------------|------------|--------|
"""

for prefix in all_prefixes:
    v1 = v1_lookup.get(prefix, {})
    v2 = v2_lookup.get(prefix, {})

    v1_v = v1.get('verdict', 'N/A')
    v2_v = v2.get('verdict', 'N/A')

    if v1_v != v2_v:
        change = 'CHANGED'
    else:
        change = 'same'

    sup = v2.get('supplier', 'Unknown')
    report += f"| {prefix} | {sup} | {v1_v} | {v2_v} | {change} |\n"

# Save
with open('output/supplier_scraping_comparison_v1_v2.md', 'w', encoding='utf-8') as f:
    f.write(report)

print('Saved: output/supplier_scraping_comparison_v1_v2.md')
print(f'Improved: {len(improvements)}, Regressed: {len(regressions)}, Same: {len(same)}')