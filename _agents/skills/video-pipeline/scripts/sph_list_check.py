# -*- coding: utf-8 -*-
"""sph_list_check.py — 视频号管理页列表抓取（标题/时间/原创标记）。
用于核对 2026-10-13 多次发布的声明状态（g 条人工声明是否生效）。"""
import asyncio
import sys
import time

from playwright.async_api import async_playwright

LIST_JS = """(() => {
  const items = [...document.querySelectorAll('.video-item, .find-post-item, [class*=video-item]')];
  return items.slice(0, 8).map(it => {
    const title = (it.querySelector('[class*=title], .name') || {}).innerText || '';
    const img = it.querySelector('img');
    const mark = /已声明原创|原创/.test(it.innerText || '');
    return {title: title.trim()[:60], hasOriginalWord: mark, text: (it.innerText||'').replace(/\\n/g, ' | ').slice(0, 120)};
  });
})()"""


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"E:\每日新中国\logs\sau_win\chrome_sph_profile"


def ensure_chrome():
    import subprocess as sp
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as r:
            if r.status == 200:
                return
    except Exception:
        pass
    sp.Popen([CHROME, "--remote-debugging-port=9222", f"--user-data-dir={PROFILE}",
              "--no-first-run", "--window-size=1280,900", "about:blank"])
    for _ in range(10):
        time.sleep(2)
        try:
            with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:
            continue


async def main():
    ensure_chrome()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "channels.weixin.qq.com" in pg.url:
                page = pg  # 复用用户已开着的 tab（SPA 已渲染）
                break
        if page is None:
            page = await ctx.new_page()
            await page.goto("https://channels.weixin.qq.com/platform/post/manage",
                            wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(12)
        print("URL:", page.url[:100])
        items = await page.evaluate(
            """(() => {
              const out = [];
              const nodes = [...document.querySelectorAll('div')].filter(
                d => /\\d{4}年\\d{1,2}月\\d{1,2}日/.test(d.innerText || '') && true);
              const seen = new Set();
              for (const d of nodes) {
                const txt = (d.innerText || '').replace(/\\n/g, ' | ');
                const key = txt.slice(0, 50);
                if (seen.has(key) || txt.length > 500) continue;
                seen.add(key);
                out.push(txt.slice(0, 150));
                if (out.length >= 8) break;
              }
              return out;
            })()""")
        for it in items:
            print("-", it)
        if not items:
            await page.screenshot(path=r"E:\每日新中国\logs\sph_list_check.png", full_page=True)
            print("列表抓取为空，截图 → sph_list_check.png")
        await browser.close()


asyncio.run(main())
