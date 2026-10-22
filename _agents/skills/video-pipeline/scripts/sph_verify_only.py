# -*- coding: utf-8 -*-
"""视频号列表独立复核（只读，不发布）：连 9222 CDP，读 post/list 计数+标题+状态。"""
import asyncio, io, json, re, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CDP = "http://127.0.0.1:9222"
MARK = sys.argv[1] if len(sys.argv) > 1 else "中国医院船"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "channels.weixin.qq.com" in pg.url:
                page = pg
                break
        if page is None:
            page = await ctx.new_page()
        if "post/list" not in page.url:
            await page.goto("https://channels.weixin.qq.com/platform/post/list",
                            timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        fr = next((f for f in page.frames if "micro/content" in f.url), None)
        if fr is None:
            print("NO_FRAME: 未找到 micro/content 帧（可能登录态失效）")
            for f in page.frames:
                print("  frame:", f.url[:120])
            return
        v = await fr.evaluate("""() => {
          const txt = document.body.innerText;
          const m = txt.match(/视频\\s*\\((\\d+)\\)/);
          const items = [...document.querySelectorAll('.item-wrap, .video-item, .wechat-item, [class*="item"]')]
            .slice(0, 12)
            .map(e => (e.innerText || '').replace(/\\s+/g, ' ').slice(0, 80))
            .filter(t => t.length > 4);
          return { count: m ? m[1] : '(?)', text: txt.slice(0, 600), items };
        }""")
        print("count:", v["count"])
        print("hasMark:", MARK in v["text"])
        for it in v["items"][:10]:
            print(" item:", it)


asyncio.run(main())
