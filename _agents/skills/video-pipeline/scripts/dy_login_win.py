# -*- coding: utf-8 -*-
"""dy_login_win.py — 用 Windows 真实 Chrome 扫码登录抖音创作者中心，
cookie 导出为 sau storage_state 格式（写入 WSL cookies 文件）。

流程：独立 profile 启动 Chrome（远程调试 9222）→ 打开 creator.douyin.com
→ 用户扫码登录（窗口可见）→ 服务端判据（user/info status_code==0）
→ storage_state 写入 \\wsl.localhost\...（旧件自动备份）。"""
import asyncio
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"E:\每日新中国\logs\sau_win\chrome_sph_profile"  # 复用 v5 sph profile（9222 实证可起）
COOKIE_WSL = r"\\wsl.localhost\Ubuntu\home\lmr\social-auto-upload\cookies\douyin_diyi.json"
FETCH_USER_JS = """
async () => {
    const r = await fetch('/web/api/media/user/info/', {credentials: 'same-origin'});
    return await r.json();
}
"""


async def main():
    os.makedirs(PROFILE, exist_ok=True)
    if os.path.exists(COOKIE_WSL):
        shutil.copy2(COOKIE_WSL, COOKIE_WSL + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
    chrome = subprocess.Popen([
        CHROME, f"--remote-debugging-port=9222", f"--user-data-dir={PROFILE}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "https://creator.douyin.com/",
    ])
    print("CHROME_LAUNCHED: 窗口已弹出，请用抖音 App 扫码登录（最长等 10 分钟）…", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        deadline = time.time() + 600
        ok = False
        while time.time() < deadline:
            # 服务端判据：任一 creator.douyin.com 页面内 fetch user/info
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "creator.douyin.com" not in pg.url:
                        continue
                    try:
                        info = await pg.evaluate(FETCH_USER_JS)
                        if info.get("status_code") in (0, None):
                            ok = True
                            break
                    except Exception:
                        continue
                if ok:
                    break
            if ok:
                break
            await asyncio.sleep(5)
        if not ok:
            print("LOGIN_TIMEOUT: 10 分钟内未检测到登录", flush=True)
            chrome.terminate()
            sys.exit(2)
        # 等 session cookie 写稳
        await asyncio.sleep(5)
        storage = await context.storage_state()
        # 只保留 douyin 相关域的 cookie/origin（Chrome profile 里可能混入其他站）
        keep = tuple(("douyin", "toutiao", "zijie", "bytedance"))
        storage["cookies"] = [c for c in storage["cookies"]
                              if any(k in (c.get("domain") or "") for k in keep)]
        storage["origins"] = [o for o in storage.get("origins", [])
                              if any(k in (o.get("origin") or "") for k in keep)]
        import json
        with open(COOKIE_WSL, "w", encoding="utf-8") as f:
            json.dump(storage, f, ensure_ascii=False, indent=1)
        print(f"LOGIN_OK COOKIE_SAVED: cookies={len(storage['cookies'])} "
              f"origins={len(storage['origins'])}", flush=True)
    # 留 Chrome 窗口几秒让用户看到结果，然后收掉调试实例
    await asyncio.sleep(5)
    chrome.terminate()
    print("DONE", flush=True)


asyncio.run(main())
