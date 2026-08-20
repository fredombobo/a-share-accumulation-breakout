# Money Heatmap Bidirectional Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display every non-zero inflow and outflow industry in the latest-day treemap and render tooltip text in a high-contrast light color.

**Architecture:** Keep the existing API response contract and ECharts component. Correct selection at the backend boundary by sorting all non-zero industries by absolute net flow, then make tooltip direction and colors explicit in the frontend rather than inheriting theme body text.

**Tech Stack:** FastAPI, pandas, Pytest, React, TypeScript, ECharts, Playwright.

---

### Task 1: Return Both Flow Directions

**Files:**
- Create: `tests/test_money_heatmap.py`
- Modify: `web/backend_app.py:1684-1713`

- [ ] **Step 1: Write the failing API test**

```python
from unittest.mock import patch

import pandas as pd

from web.backend_app import money_heatmap


def test_money_heatmap_returns_every_nonzero_inflow_and_outflow():
    pivot = pd.DataFrame(
        [{"流入大": 100.0, "流出大": -80.0, "流入小": 10.0, "流出小": -5.0, "零值": 0.0}]
    )
    with patch("web.backend_app._load_sector_flow", return_value=(["20260807"], pivot)):
        result = money_heatmap(top=0)
    assert [item["name"] for item in result["items"]] == ["流入大", "流出大", "流入小", "流出小"]
    assert [item["net_wan"] for item in result["items"]] == [100, -80, 10, -5]
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_money_heatmap.py -q`

Expected: FAIL because the current signed `head(top)` selection returns no items for `top=0`.

- [ ] **Step 3: Implement absolute-value ordering without dropping either direction**

```python
row = pd.Series(pivot.iloc[-1])
nonzero = row[row != 0]
ordered = nonzero.reindex(nonzero.abs().sort_values(ascending=False).index)
selected = ordered if top <= 0 else ordered.head(top)
```

Build `items` from `selected`, retaining signed `net_wan` and absolute `value`. Change the endpoint default to `top=0` and the TypeScript client default to `0`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m pytest tests/test_money_heatmap.py -q`

Expected: `1 passed`.

### Task 2: Make Tooltip Text High Contrast

**Files:**
- Modify: `web/frontend/src/components/MoneyHeatmap.tsx:34-45`

- [ ] **Step 1: Reproduce the existing tooltip contrast failure**

Run a headless Playwright check against `http://127.0.0.1:8000/`: hover a treemap cell in light theme, locate the ECharts tooltip DOM, and assert its computed text color is a light RGB value.

Expected: FAIL because `textStyle.color` currently uses the light theme body color on a dark tooltip background.

- [ ] **Step 2: Implement explicit tooltip direction and color**

```tsx
const direction = d.net_wan >= 0 ? '净流入' : '净流出'
return `<b>${d.name}</b><br/>${direction}：${fmt(Math.abs(d.net_wan))}<br/>日期：${data.trade_date}`
```

Set `tooltip.textStyle.color` to `#f8fafc` and keep the existing dark background.

- [ ] **Step 3: Verify the production frontend build**

Run: `npm --prefix web/frontend run build`

Expected: TypeScript and Vite build complete successfully.

### Task 3: Runtime and Visual Acceptance

**Files:**
- Verify: `web/frontend/dist/`
- Verify: `runtime/stock_data.db`

- [ ] **Step 1: Run relevant quality checks**

Run:

```text
python -m pytest tests/test_money_heatmap.py tests/test_upgrade_system.py -q
python -m ruff check web/backend_app.py tests/test_money_heatmap.py
python -m mypy web/backend_app.py
```

Expected: all commands pass.

- [ ] **Step 2: Restart only the validated project process on port 8000**

Resolve the listener PID, verify its command line is `python web/backend_app.py` in this repository, stop that exact PID, then start the current backend hidden.

- [ ] **Step 3: Verify live data and UI**

Check `/api/money-heatmap?top=0` contains all 27 positive and 83 negative industries for data date `20260807`. In desktop and narrow viewports, confirm red and green treemap regions render, hover one positive and one negative cell, and confirm tooltip text is light with the correct “净流入/净流出” label. Console errors and failed requests must remain zero.
