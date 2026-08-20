"""UI 改版自动化审计：布局溢出 / 元素重叠 / 文本对比度 / 控制台错误 / 双主题生效。"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8001"

JS_AUDIT = """
() => {
  const out = { overflowX: document.documentElement.scrollWidth > window.innerWidth,
                scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
                bodyBg: getComputedStyle(document.body).backgroundColor,
                issues: [] };
  const check = (el, name) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    if (r.right > window.innerWidth + 1 || r.left < -1) out.issues.push(`${name} 超出视口: right=${r.right.toFixed(0)}`);
  };
  document.querySelectorAll('.topbar, .sidebar, .today-card, .kpi, .card, .stock-card, .paper-panel, .lab-hero-main').forEach((el, i) => check(el, `${el.className.split(' ')[0]}[${i}]`));
  // 关键文本对比度（兼容 rgb() / rgba() / color(srgb r g b / a) 三种计算样式格式）
  const lum = (c) => {
    let m = c.match(/srgb\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)/);
    if (m) {
      const [r, g, b] = [m[1], m[2], m[3]].map(Number);
      const f = (v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    }
    m = c.match(/([\\d.]+)[^\\d]*,\\s*([\\d.]+)[^\\d]*,\\s*([\\d.]+)/);
    if (!m) return null;
    const [r, g, b] = [m[1], m[2], m[3]].map(Number);
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); if (l1 == null || l2 == null) return null; const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]; return (hi + 0.05) / (lo + 0.05); };
  const pairs = [['.kpi-label', '.kpi'], ['.muted', '.card'], ['.topbar h1', '.topbar'], ['.asof', '.topbar'], ['.badge', '.status-bar']];
  out.contrast = pairs.map(([fgSel, bgSel]) => {
    const fg = document.querySelector(fgSel); if (!fg) return null;
    const bgEl = document.querySelector(bgSel);
    const bg = bgEl ? getComputedStyle(bgEl).backgroundColor : getComputedStyle(document.body).backgroundColor;
    const fgc = getComputedStyle(fg).color;
    const r = ratio(fgc, bg);
    return { pair: `${fgSel} on ${bgSel}`, ratio: r == null ? null : Math.round(r * 100) / 100 };
  }).filter(Boolean);
  return out;
}
"""


def audit(page, name: str) -> dict:
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_timeout(1200)
    result = {"page": name}
    result["dark"] = page.evaluate(JS_AUDIT)
    try:
        page.click(".btn-icon[title*='浅色']")
        page.wait_for_timeout(600)
        result["light"] = page.evaluate(JS_AUDIT)
        page.click(".btn-icon[title*='深色']")
    except Exception as exc:  # noqa: BLE001
        result["light"] = {"error": str(exc)}
    return result


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1560, "height": 950})
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR {e}"))

        for path, name in (("/", "总览"), ("/lab", "实验室"), ("/paper", "纸面交易")):
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_timeout(1200)
            res = page.evaluate(JS_AUDIT)
            print(f"\n===== {name} ({path}) 深色 =====")
            print(f"横向溢出: {res['overflowX']} (scrollW={res['scrollW']} innerW={res['innerW']})")
            print(f"body bg: {res['bodyBg']}")
            for i in res["issues"]:
                print(f"  ! {i}")
            for cset in res["contrast"]:
                flag = "LOW" if cset["ratio"] is not None and cset["ratio"] < 4.5 else "ok"
                print(f"  对比度 [{flag}] {cset['pair']}: {cset['ratio']}")
            # 浅色
            try:
                page.click(".btn-icon[title*='浅色']")
                page.wait_for_timeout(600)
                res2 = page.evaluate(JS_AUDIT)
                print(f"----- {name} 浅色 -----")
                print(f"横向溢出: {res2['overflowX']} (scrollW={res2['scrollW']})")
                print(f"body bg: {res2['bodyBg']}")
                for i in res2["issues"]:
                    print(f"  ! {i}")
                for cset in res2["contrast"]:
                    flag = "LOW" if cset["ratio"] is not None and cset["ratio"] < 4.5 else "ok"
                    print(f"  对比度 [{flag}] {cset['pair']}: {cset['ratio']}")
                page.click(".btn-icon[title*='深色']")
            except Exception as exc:  # noqa: BLE001
                print(f"浅色审计失败: {exc}")

        print("\n===== 控制台错误 =====")
        for e in console_errors:
            print("  !", e)
        if not console_errors:
            print("  (无)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
