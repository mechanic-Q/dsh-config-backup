#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sph_publish.py — 视频号一键发表 + 列表核验（Chrome CDP，2026-09-27 全链路实证）。

前置：视频号专用 Chrome 已带登录态运行（`E:\\每日新中国\\logs\\restart_sph_chrome.ps1`
拉起，CDP 9222，profile chrome_sph_profile——微信登录是内存态，存在 profile 里，
杀 Chrome 即丢）。cookie 文件（tencent_diyi.json）失效不影响本脚本。

用法：
  python3 sph_publish.py --title "标题" --video <mp4> [--tags a,b,c]
        [--short-title "短标题"] [--expect-key 发电装机] [--cdp http://127.0.0.1:9222]
  python3 sph_publish.py --check-only [--expect-key 发电装机]

三大实证坑（为什么这个脚本长这样，勿"优化"回去）：
1. connect_over_cdp 下 Playwright set_files 有 50MB 内容传输上限（83MB 视频必败）
   → CDP DOM.setFileInputFiles 直填本机路径，零内容传输。
2. Playwright selector 引擎未注入编辑器 iframe（micro/content frame）：
   一切 locator/get_by_role 在该 frame 恒返回 0——不是元素不在（evaluate 能看到）
   → 全程 frame.evaluate() + page.keyboard + JS btn.click()。
3. 点「发表」后弹「声明原创的视频有机会获得广告分成」（按钮 [直接发表, 声明原创]）
   → 点【直接发表】才是正式发表确认；AI 内容不声明原创。
另：uploader 库 TencentVideo.wait_for_upload_complete 在此场景是陷阱（每轮实际
耗时 30s+ 但计时只 +2s，超时后存草稿并跳过发表）——本脚本自带就绪探测，勿调它。
"""
import argparse
import asyncio
import json
import re
import sys

CDP_DEFAULT = "http://127.0.0.1:9222"
POST_LIST_URL = "https://channels.weixin.qq.com/platform/post/list"

# 弹窗按钮决策表：先按 prefer 顺序点（「直接发表」是发表确认），
# avoid 里的按钮会把流程带偏（去声明=进原创流程；取消=放弃发表）。
DIALOG_PREFER = ["直接发表", "仍要发表", "继续发表", "知道了", "确定", "确认",
                 "以后再说", "暂不", "关闭", "忽略", "下次再说", "稍后"]
DIALOG_AVOID = ["去声明", "声明原创", "取消", "不保存"]


def pick_dialog_button(buttons):
    """从弹窗按钮名列表里挑该点的那个：prefer 顺序优先，avoid 永不选；
    全不中时返回 None（宁可报Unknown也不乱点）。空白名视同不存在。"""
    cleaned = [b.strip() for b in buttons if b and b.strip()]
    for name in DIALOG_PREFER:
        if name in cleaned:
            return name
    for name in cleaned:
        if name not in DIALOG_AVOID:
            return name
    return None


def extract_video_count(text):
    """从创作中心列表文本提取 `视频 (N)` 计数；无则 None。"""
    m = re.search(r"视频\s*\((\d+)\)", text or "")
    return int(m.group(1)) if m else None


def title_in_list(text, expect_key):
    """今日标题关键词是否已出现在列表文本（发布成功的权威判据之一）。"""
    return bool(expect_key) and expect_key in (text or "")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="视频号一键发表（Chrome CDP）")
    ap.add_argument("--title", help="视频描述（正文首行，同时是列表标题来源）")
    ap.add_argument("--video", help="成片 mp4 绝对路径（本机路径，Chrome 需可读）")
    ap.add_argument("--tags", default="中国科技,正能量",
                    help="话题标签，逗号分隔（发表时逐个 #tag+空格）")
    ap.add_argument("--short-title", default=None, help="短标题（可选）")
    ap.add_argument("--expect-key", default=None,
                    help="列表核验用的标题关键词（默认取 --title 前 4 字）")
    ap.add_argument("--cdp", default=CDP_DEFAULT, help="Chrome CDP 端点")
    ap.add_argument("--check-only", action="store_true",
                    help="只查列表计数与标题在场，不发表")
    args = ap.parse_args(argv)
    if not args.check_only:
        for miss in ("title", "video"):
            if not getattr(args, miss):
                ap.error(f"--{miss} 必填（或用 --check-only 只核验）")
    if not args.expect_key:
        args.expect_key = (args.title or "")[:4]
    return args


async def find_wx_page(ctx):
    return next((pg for pg in ctx.pages if "channels.weixin.qq.com" in pg.url), None)


async def live_editor_frame(page, marker, wait_s=60):
    """返回当前真持有 marker 元素的 micro/content frame。

    编辑器 iframe 会重载，重载后旧 Frame 对象仍挂在 page.frames 里且 URL 匹配，
    但 document 已换——必须以元素在场为准，不能按 URL 首匹配。"""
    for _ in range(max(1, wait_s // 2)):
        for f in page.frames:
            if "micro/content" not in f.url:
                continue
            try:
                if await f.evaluate(f"!!document.querySelector('{marker}')"):
                    return f
            except Exception:
                continue
        await asyncio.sleep(2)
    return None


JS_SET_FILE = """(path) => {
  const inp = document.querySelector('input[type=file]');
  if (!inp) return false;
  inp.focus();
  return true;
}"""

JS_CLICK_PUBLISH = """(() => {
  const btn = [...document.querySelectorAll('div.form-btns button')]
    .find(b => b.innerText.trim() === '发表');
  if (!btn) return false;
  btn.click(); return true;
})()"""

JS_DUMP_DIALOGS = """(() => {
  const dlgs = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
    .filter(d => d.style.display !== 'none');
  return dlgs.map(d => ({
    title: (d.querySelector('.weui-desktop-dialog__title') || {innerText: ''}).innerText,
    btns: [...d.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean)
  }));
})()"""

JS_CLICK_DIALOG_BTN = """(name) => {
  const dlgs = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
    .filter(d => d.style.display !== 'none');
  for (const d of dlgs) {
    const b = [...d.querySelectorAll('button')].find(b => b.innerText.trim() === name);
    if (b) { b.click(); return true; }
  }
  return false;
}"""

JS_LIST_CHECK = """(key) => {
  const txt = document.body.innerText;
  const m = txt.match(/视频\\s*\\((\\d+)\\)/);
  return { count: m ? parseInt(m[1], 10) : null,
           hasToday: txt.indexOf(key) >= 0 };
}"""


async def upload_video(page, client, fr, video_path, probe_rounds=30):
    """CDP DOM.setFileInputFiles 直填视频路径（坑1 的绕法）。返回是否成功。"""
    for attempt in range(probe_rounds):
        tree = await client.send("DOM.getDocument", {"depth": -1, "pierce": True})
        node = _find_file_input(tree.get("root") or {})
        if node is not None:
            await client.send("DOM.setFileInputFiles",
                              {"files": [video_path], "nodeId": node["nodeId"]})
            print(f"[step] 视频已 setFileInputFiles（第{attempt + 1}次探测）", flush=True)
            return True
        await asyncio.sleep(2)
    return False


def _find_file_input(node):
    if not isinstance(node, dict):
        return None
    if node.get("nodeName", "").upper() == "INPUT":
        attrs = node.get("attributes") or []
        pairs = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
        if pairs.get("type") == "file":
            return node
    for key in ("children", "shadowRoots", "contentDocument", "templateContent"):
        for ch in node.get(key) or []:
            r = _find_file_input(ch)
            if r is not None:
                return r
    return None


async def wait_upload_ready(fr, rounds=90):
    """上传就绪 = video 元素在场且「发表」按钮非 disabled（纯 evaluate）。"""
    for _ in range(rounds):
        try:
            st = await fr.evaluate("""(() => {
              const btn = [...document.querySelectorAll('div.form-btns button')]
                .find(b => b.innerText.trim() === '发表');
              return { hasVideo: !!document.querySelector('video'),
                       disabled: btn ? (btn.disabled || /disabled/.test(btn.className)) : true };
            })()""")
            if st.get("hasVideo") and not st.get("disabled"):
                return True
        except Exception:
            pass
        await asyncio.sleep(2)
    return False


async def fill_title_tags(page, fr, title, tags):
    """focus + 键盘输入（坑2 的绕法），输入后回读校验。"""
    await fr.evaluate("document.querySelector('div.input-editor').focus()")
    await asyncio.sleep(0.3)
    await page.keyboard.type(title, delay=25)
    await page.keyboard.press("Enter")
    for tag in tags:
        await page.keyboard.type("#" + tag, delay=25)
        await page.keyboard.press("Space")
        await asyncio.sleep(0.4)
    txt = await fr.evaluate(
        "(document.querySelector('div.input-editor')||{innerText:''}).innerText")
    if title[:10] not in (txt or ""):
        raise RuntimeError(f"标题未落入编辑器: {(txt or '')[:40]!r}")


async def click_publish_and_handle_dialog(fr, page):
    """JS 点发表 → 拦截弹窗按 DIALOG_PREFER 处理 → 等跳转列表。"""
    clicked = await fr.evaluate(JS_CLICK_PUBLISH)
    print(f"[step] 发表点击: {clicked}", flush=True)
    if not clicked:
        return "NO_PUBLISH_BTN"
    for _ in range(25):  # 50s 窗口
        await asyncio.sleep(2)
        if "post/list" in page.url:
            return "NAVIGATED"
        dlgs = await fr.evaluate(JS_DUMP_DIALOGS)
        if dlgs:
            print(f"[dlg] {json.dumps(dlgs, ensure_ascii=False)[:300]}", flush=True)
            buttons = [b for d in dlgs for b in d.get("btns", [])]
            hit = pick_dialog_button(buttons)
            if hit and await fr.evaluate(JS_CLICK_DIALOG_BTN, hit):
                print(f"[dlg] 点击: {hit}", flush=True)
                for _ in range(10):
                    await asyncio.sleep(2)
                    if "post/list" in page.url:
                        return "NAVIGATED"
                return "CLICKED_NO_NAV"
    return "TIMEOUT"


async def check_list(page, expect_key):
    """创作中心列表核验：视频 (N) 计数 + 标题关键词在场。"""
    if "post/list" not in page.url:
        await page.goto(POST_LIST_URL, timeout=60000, wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    fr = await live_editor_frame(page, ".form-btns", wait_s=10)
    if fr is None:
        return {"count": None, "hasToday": False}
    return await fr.evaluate(JS_LIST_CHECK, expect_key)


async def run_publish(args):
    from playwright.async_api import async_playwright
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        ctx = browser.contexts[0]
        page = await find_wx_page(ctx)
        if page is None:
            print("RESULT: NO_WX_PAGE（Chrome 未开视频号页/未登录）")
            return 2
        await page.bring_to_front()
        if "post/create" not in page.url:
            await page.goto("https://channels.weixin.qq.com/platform/post/create",
                            timeout=60000, wait_until="domcontentloaded")
        fr = await live_editor_frame(page, ".ant-upload-btn, .ant-upload-drag, input[type=file]")
        if fr is None:
            print("RESULT: NO_EDITOR_FRAME")
            return 2
        client = await page.context.new_cdp_session(page)

        if not await upload_video(page, client, fr, args.video):
            print("RESULT: NO_FILE_INPUT")
            return 3
        if not await wait_upload_ready(fr):
            print("RESULT: UPLOAD_TIMEOUT")
            return 4
        print("[step] 上传就绪", flush=True)

        try:
            await fill_title_tags(page, fr, args.title, tags)
            print("[step] 标题+话题 ✓", flush=True)
        except Exception as e:
            print(f"RESULT: TITLE_FAILED（{type(e).__name__}: {str(e)[:100]}）")
            return 5
        if args.short_title:
            try:
                ok = await fr.evaluate(
                    "() => { const i = document.querySelector('span input[type=\"text\"]');"
                    " if (!i) return false; i.focus(); return true; }")
                if ok:
                    await page.keyboard.type(args.short_title, delay=30)
                    print(f"[step] 短标题: {args.short_title!r}", flush=True)
            except Exception as e:
                print(f"[warn] 短标题失败（跳过）: {type(e).__name__}: {str(e)[:80]}", flush=True)

        outcome = await click_publish_and_handle_dialog(fr, page)
        if outcome in ("NO_PUBLISH_BTN", "TIMEOUT"):
            print(f"RESULT: SUBMITTED_UNCONFIRMED（{outcome}，请到后台核验，勿重复发）")
            return 6

        await page.wait_for_timeout(4000)
        v = await check_list(page, args.expect_key)
        print(f"[verify] {json.dumps(v, ensure_ascii=False)}", flush=True)
        if v.get("hasToday"):
            print(f"RESULT: PUBLISHED_VERIFIED（视频计数 {v.get('count')}）")
            return 0
        print("RESULT: PUBLISHED_UNVERIFIED（列表未见今日标题，人工复核）")
        return 7


async def run_check(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        ctx = browser.contexts[0]
        page = await find_wx_page(ctx)
        if page is None:
            print("RESULT: NO_WX_PAGE")
            return 2
        v = await check_list(page, args.expect_key)
        print(f"[verify] {json.dumps(v, ensure_ascii=False)}", flush=True)
        print("RESULT: CHECKED")
        return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.check_only:
            return asyncio.run(run_check(args))
        return asyncio.run(run_publish(args))
    except Exception as e:
        print(f"RESULT: ERROR（{type(e).__name__}: {str(e)[:150]}）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
