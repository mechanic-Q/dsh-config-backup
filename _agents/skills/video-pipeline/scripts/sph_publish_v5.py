#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sph_publish_v5.py — 视频号全自动发布（v5：封面 + 原创声明 + 核验 + 飞书播报）。

用法: python sph_publish_v5.py <YYYY-MM-DD>          # 真发布
      python sph_publish_v5.py --check [YYYY-MM-DD]  # 发布前干跑：只解析+查输入，
                                                     # 不连 CDP、不写标记、不真发
前置: driver s13 完成后由其 WMI 拉起（Windows 侧独立进程）；也可手动触发。
脚本位置（#152）：同仓邻居一律从本文件自身位置解析（HERE），不再写死任何
  安装路径——发布器与 feishu_gate.py 同目录，跨侧调用也不依赖具体副本位置。
流程: CDP Chrome（无 9222 则自启 sph profile）→ 登录检查（过期 → 开登录页 +
  飞书叫人扫码 + 守望续跑）→ 创建页清残留草稿视频 → setFileInputFiles 传成片
  → 等就绪(10min) → 封面(预览编辑 → 上传封面 → chooser 注入 shipinhao 封面 →
  确认) → 填标题+话题 → 发表 → 拦截弹窗点【声明原创】→ 通用弹窗梯跟进 →
  post/list 核验(计数+1 且今日标题在场) → 飞书播报结果。
参数与产物全部从 待发视频号/发布信息.txt 的「视频号」段解析。
失败 → 飞书叫人 + 退出码 3；成功 → 退出码 0。
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

CHECK_ONLY = "--check" in sys.argv[1:]


def _cli_date(argv):
    """第一个 YYYY-MM-DD 实参即目标日期；--check 这类旗标不算日期。

    形似日期但不合规（2026-1-5、2026-13-01）直接报错退出：静默回落「今天」会把
    「参数打错」变成「发错单」，比报错难查得多（#152 评审）。
    """
    for a in argv:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", a):
            try:
                time.strptime(a, "%Y-%m-%d")
            except ValueError:
                print(f"[date] 非法日期: {a}（要 YYYY-MM-DD）", flush=True)
                sys.exit(2)
            return a
        if re.match(r"^\d", a):
            print(f"[date] 非法日期: {a}（要 YYYY-MM-DD）", flush=True)
            sys.exit(2)
    return time.strftime("%Y-%m-%d")


DATE = _cli_date(sys.argv[1:])
BASE = rf"E:\每日新中国\{DATE}\video\每日新中国b站\流水线\daily-china-{DATE.replace('-', '')}"
PUBDIR = BASE + r"\10_发布\待发视频号"
INFO = PUBDIR + r"\发布信息.txt"
VIDEO = PUBDIR + r"\最终成片.mp4"
COVER = PUBDIR + r"\封面_shipinhao.png"
CDP = "http://127.0.0.1:9222"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"E:\每日新中国\logs\sau_win\chrome_sph_profile"
# 自身位置即安装位置（#152）：同目录邻居（feishu_gate.py）从这里派生，
# 脚本被 junction / 软链 / 另一份 clone 调起时都指向它自己所在的仓。
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FG = os.path.join(HERE, "feishu_gate.py")

# 跨侧路径口径不在这里另立一份：dsh_judge.to_host_path 是仓库既有实现
# （宿主契约要求路径转换收口到 #143 的单一模块），本文件只调用。
import dsh_judge as _dj  # noqa: E402 — 需在 sys.path.insert 之后

TITLE = TAGS = None


def parse_info():
    """从 发布信息.txt 的「视频号」段解析标题/标签。"""
    global TITLE, TAGS
    try:
        txt = open(_dj.to_host_path(INFO), encoding="utf-8", errors="replace").read()
        seg = txt.split("== 视频号 ==")[1].split("==")[0]
        m = re.search(r"标题：(.+)", seg)
        TITLE = m.group(1).strip() if m else None
        m = re.search(r"标签：(.+)", seg)
        TAGS = [t.strip() for t in m.group(1).split(",")] if m else []
    except Exception as e:  # noqa: BLE001
        print(f"[info] 解析失败: {e}", flush=True)


def notify(text):
    """飞书播报（尽力而为）。v5 跑在 Windows 侧，凭证定位由 feishu_gate 自己负责
    （env → ~/.dsh → 通配），本脚本只管「同目录邻居 + 当前解释器」直跑：不再经
    wsl.exe 转手，也不再假设脚本装在哪份副本里（#152）。"""
    log(text)
    try:
        r = subprocess.run([sys.executable, FG, "send", json.dumps(text)],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        if '"code": 0' not in (r.stdout or ''):
            print(f"[notify] 发送返回异常: {(r.stdout or r.stderr or '')[:120]}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[notify] 发送失败（忽略）: {e}", flush=True)


def log(msg):
    print(f"[sph-v5 {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_chrome():
    """9222 不在线则拉起 CDP Chrome（sph profile）。"""
    r = subprocess.run(["curl", "-s", "-m", "3", "-o", "NUL", "-w", "%{http_code}",
                        "http://127.0.0.1:9222/json/version"], capture_output=True, text=True)
    if r.stdout.strip() == "200":
        return True
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Start-Process '{CHROME}' -ArgumentList '--remote-debugging-port=9222',"
                    f"'--user-data-dir={PROFILE}','--no-first-run','--no-default-browser-check',"
                    f"'--window-size=1280,900','about:blank'"], timeout=30)
    for _ in range(10):
        time.sleep(2)
        r = subprocess.run(["curl", "-s", "-m", "3", "-o", "NUL", "-w", "%{http_code}",
                            "http://127.0.0.1:9222/json/version"], capture_output=True, text=True)
        if r.stdout.strip() == "200":
            return True
    return False


async def dlg_buttons(fr):
    return await fr.evaluate("""(() => {
      const dlgs = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
        .filter(d => d.style.display !== 'none');
      return dlgs.map(d => ({
        title: (d.querySelector('.weui-desktop-dialog__title') || {innerText: ''}).innerText,
        btns: [...d.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean)
      }));
    })()""")


async def click_dialog_btn(fr, names):
    js = """(names) => {
      const dlgs = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
        .filter(d => d.style.display !== 'none');
      for (const d of dlgs) {
        for (const name of names) {
          const b = [...d.querySelectorAll('button')].find(b => b.innerText.trim() === name);
          if (b) { b.click(); return name; }
        }
      }
      return null;
    }"""
    return await fr.evaluate(js, names)


async def live_editor_frame(page, marker, wait_s=60):
    for _ in range(wait_s // 2):
        for f in page.frames:
            if "micro/content" not in f.url:
                continue
            try:
                if await f.evaluate(f"!!document.querySelector('{marker}')"):
                    return f
            except Exception:
                pass
        await asyncio.sleep(2)
    return None


async def run_publish():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "channels.weixin.qq.com" in pg.url), None)
        if page is None:
            page = await ctx.new_page()

        # ── 网络取证（10-04：发表卡在服务端不可见，抓 mmfinderassistant-bin
        #     的写接口响应与 errCode，定位静默失败点）────────────────────
        loop = asyncio.get_event_loop()
        net = {"upload_done": False, "create_ok": False}
        def _on_response(resp):
            u = resp.url
            if "mmfinderassistant-bin" not in u:
                return
            seg = u.split("cgi-bin/")[-1].split("?")[0]
            async def _read():
                try:
                    body = await resp.text()
                    err = ""
                    try:
                        err = str(json.loads(body).get("errCode"))
                    except Exception:
                        pass
                    print(f"[net] {resp.status} err={err} {seg} {body[:180]}",
                          flush=True)
                    # CDN 上传完成信号：post_clip_video_result 返回完整媒体元数据
                    # （含 md5）。flag:2 = 传输中。10-04 实锤：UI 的 <video> 用
                    # 本地 blob 预览，上传没完也能"就绪"，发表被服务端静默吞掉
                    if "post_clip_video_result" in seg and '"md5"' in body:
                        net["upload_done"] = True
                        print("[net] ✅ CDN 上传完成（媒体元数据就绪）", flush=True)
                    if "post_create" in seg and err == "0":
                        net["create_ok"] = True
                        print("[net] ✅ 发表 POST 落库（post_create err=0）", flush=True)
                except Exception:
                    pass
            if any(k in seg.lower() for k in ("post", "publish", "create", "declare",
                                              "original", "upload", "auth", "error")):
                loop.create_task(_read())
        def _on_reqfail(req):
            u = req.url
            if "channels.weixin.qq.com" not in u or req.method != "POST":
                return
            seg = u.split("cgi-bin/")[-1].split("?")[0][:70]
            print(f"[reqfail] POST {seg} failure={req.failure}", flush=True)

        def _on_console(msg):
            # 只留错误/警告，发表失败时客户端必留 JS 错误线索
            if msg.type in ("error", "warning"):
                print(f"[console.{msg.type}] {msg.text[:200]}", flush=True)

        def _on_pageerror(err):
            print(f"[pageerror] {str(err)[:300]}", flush=True)

        def _on_req(req):
            u = req.url
            if "channels.weixin.qq.com" in u and req.method == "POST" \
                    and "mmfinderassistant-bin" in u and "clip_video" not in u \
                    and "/auth/" not in u and "report" not in u:
                seg = u.split("cgi-bin/")[-1].split("?")[0][:70]
                print(f"[req->] POST {seg}", flush=True)

        page.on("response", _on_response)
        page.on("requestfailed", _on_reqfail)
        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)
        page.on("request", _on_req)
        await page.bring_to_front()
        if "post/create" not in page.url:
            await page.goto("https://channels.weixin.qq.com/platform/post/create",
                            timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
        if "login" in page.url:
            notify("⚠️ 视频号登录态过期：桌面 Chrome 已开登录页，扫码后自动续发（2 小时守望）")
            scan_ok = False
            for cycle in range(40):  # ~2h，二维码 3 分钟一刷
                deadline = time.time() + 180
                while time.time() < deadline:
                    if "login" not in page.url:
                        break
                    await asyncio.sleep(5)
                if "login" not in page.url:
                    scan_ok = True
                    break
                try:
                    await page.reload(wait_until="domcontentloaded")
                    log(f"[watch] cycle{cycle}: 刷新二维码")
                except Exception:
                    pass
            if not scan_ok:
                print("RESULT: SCAN_TIMEOUT", flush=True)
                return False
            try:
                st = await ctx.storage_state()
                json.dump(st, open(r"E:\每日新中国\logs\sau_win\tencent_diyi.json", "w",
                                   encoding="utf-8"), ensure_ascii=False)
                log("[watch] 会话已存 cookie 文件")
            except Exception:
                pass
            await page.goto("https://channels.weixin.qq.com/platform/post/create",
                            timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            if "login" in page.url:
                print("RESULT: STILL_LOGIN_AFTER_SCAN", flush=True)
                return False

        fr = next((f for f in page.frames if "micro/content" in f.url), None)
        # ── 0. 清残留草稿视频（上次中途退出的自动保存）────────────────
        # 用真实鼠标点击——JS .click() 对该组件无效（09-29 实测：点过但视频仍
        # 在场）；清不掉就中止（带残留草稿继续 = 重复发布风险）。
        try:
            # 09-30 实测×2：刷新页面即清草稿（草稿是会话内存态）；点击删除各法均无效。
            # 策略 = reload 优先；仍残留则中止（防把旧视频/重复视频发出去）。
            for attempt in range(2):
                if not await fr.evaluate("!!document.querySelector('video')"):
                    break
                print(f"[step] 检测到残留草稿，刷新清理（第{attempt+1}次）", flush=True)
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5)
                fr = next((f for f in page.frames if "micro/content" in f.url), None) or fr
            if await fr.evaluate("!!document.querySelector('video')"):
                print("RESULT: RESIDUAL_DRAFT（残留草稿清不掉，中止防重复发）", flush=True)
                return False
            print("[step] 编辑器干净", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[step] 草稿清理异常（忽略继续）: {type(e).__name__} {str(e)[:80]}", flush=True)

        # ── 1. 传视频（CDP setFileInputFiles）─────────────────────────
        client = await page.context.new_cdp_session(page)
        uploaded = False
        for attempt in range(20):
            tree = await client.send("DOM.getDocument", {"depth": -1, "pierce": True})

            def find_video_input(node):
                if not isinstance(node, dict):
                    return None
                if node.get("nodeName", "").upper() == "INPUT":
                    attrs = node.get("attributes") or []
                    pairs = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
                    if pairs.get("type") == "file" and "video" in (pairs.get("accept") or ""):
                        return node
                for key in ("children", "shadowRoots", "contentDocument", "templateContent"):
                    for ch in node.get(key) or []:
                        r = find_video_input(ch)
                        if r is not None:
                            return r
                return None

            node = find_video_input(tree.get("root") or {})
            if node is not None:
                await client.send("DOM.setFileInputFiles",
                                  {"files": [VIDEO], "nodeId": node["nodeId"]})
                uploaded = True
                print(f"[step] 视频已注入（第{attempt+1}次探测）", flush=True)
                break
            await asyncio.sleep(2)
        if not uploaded:
            print("RESULT: NO_VIDEO_INPUT", flush=True)
            return False

        # ── 2. 等 UI 就绪即点发表（大文件必然仍在上传中）───────────────
        # 10-10 三跑实锤反转：等 CDN 传完（md5 元数据）再点发表，客户端反而
        # **永不发出 post_create**（乐观跳卡片视图，服务端无条目，无 reqfail）；
        # 10-04 成功链路 = 点发表时上传仍在传（flag:2），提交由上传完成回调
        # 触发（md5 到达后才发 post_create）。所以正确姿势 = UI 就绪马上去走
        # 封面/标题/发表，让"发表 → 弹窗 → 声明"与上传并行，post_create 由
        # 回调自然落地；点发表后原页面永久冻结（守望+新标签页核验）。
        # 注：仅适用大文件（本流程成片恒 >60MB，blob 预览秒就绪而 CDN 需
        # 2-3 分钟，窗口必然存在）；小文件上传秒完则退化到未知路径。
        ready = False
        for i in range(300):
            try:
                st = await fr.evaluate("""(() => {
                  const btn = [...document.querySelectorAll('div.form-btns button')]
                    .find(b => b.innerText.trim() === '发表');
                  return { hasVideo: !!document.querySelector('video'),
                           disabled: btn ? (btn.disabled || /disabled/.test(btn.className)) : true };
                })()""")
                if st.get("hasVideo") and not st.get("disabled"):
                    ready = True
                    inflight = "（上传在传中，发表与上传并行——成功路径）" \
                        if not net["upload_done"] else "⚠️ 上传已完（非成功路径，观察）"
                    print(f"[step] 上传 UI 就绪（{i*2}s）{inflight}", flush=True)
                    break
            except Exception:
                fr = next((f for f in page.frames if "micro/content" in f.url), None) or fr
            await asyncio.sleep(2)
        if not ready:
            print("RESULT: UPLOAD_TIMEOUT", flush=True)
            return False

        # ── 3. 封面：CDP 真实鼠标事件（09-29 实测：JS .click() 对「编辑」与
        #      modal 全部失效——React 合成事件只认 Input 域真事件；getBoxModel
        #      取绝对坐标 + Input.dispatchMouseEvent 是唯一可行路径）────────
        async def cdp_click_at(cx, cy):
            await client.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
            await asyncio.sleep(0.4)
            await client.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
            await asyncio.sleep(0.15)
            await client.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})

        cover_ok = False
        for attempt in range(4):
            try:
                pos = await fr.evaluate("""(() => {
                  const b = document.querySelector('.edit-btn');
                  if (!b) return null;
                  const r = b.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                })()""")
                if not pos:
                    print(f"[cover] 第{attempt+1}轮：编辑按钮未渲染，等待", flush=True)
                    await asyncio.sleep(3)
                    continue
                await cdp_click_at(pos["x"], pos["y"])
                # 等 modal（.single-cover-uploader-wrap 在场）
                up_pos = None
                for _ in range(8):
                    await asyncio.sleep(1)
                    up_pos = await fr.evaluate("""(() => {
                      const u = document.querySelector('.single-cover-uploader-wrap');
                      if (!u) return null;
                      const r = u.getBoundingClientRect();
                      return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                    })()""")
                    if up_pos:
                        break
                if not up_pos:
                    print(f"[cover] 第{attempt+1}轮：modal 未开", flush=True)
                    await asyncio.sleep(2)
                    continue
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await cdp_click_at(up_pos["x"], up_pos["y"])
                fc = await fc_info.value
                await fc.set_files(COVER)
                print("[cover] 封面已注入", flush=True)
                await asyncio.sleep(5)
                # 确认按钮：CDP 点击
                cpos = await fr.evaluate("""(() => {
                  const d = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
                    .find(x => x.getBoundingClientRect().height > 0 && x.innerText.includes('封面'));
                  if (!d) return null;
                  const b = [...d.querySelectorAll('button')].find(b => b.innerText.trim() === '确认');
                  if (!b) return null;
                  const r = b.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                })()""")
                if cpos:
                    await cdp_click_at(cpos["x"], cpos["y"])
                    print("[cover] 封面确认 ✓", flush=True)
                    cover_ok = True
                    await asyncio.sleep(3)
                    break
                print(f"[cover] 第{attempt+1}轮：确认按钮未找到", flush=True)
            except Exception as e:
                print(f"[cover] 第{attempt+1}轮失败: {type(e).__name__} {str(e)[:80]}", flush=True)
                await asyncio.sleep(2)
        if not cover_ok:
            print("[cover] 封面环节失败（继续发表，封面用自动帧）", flush=True)

        # ── 4. 填标题 + 话题 ──────────────────────────────────────────
        async def fill_title():
            f = await live_editor_frame(page, "div.input-editor", wait_s=30)
            await f.evaluate("document.querySelector('div.input-editor').focus()")
            await asyncio.sleep(0.3)
            await page.keyboard.type(TITLE, delay=25)
            await page.keyboard.press("Enter")
            for tag in TAGS:
                await page.keyboard.type("#" + tag, delay=25)
                await page.keyboard.press("Space")
                await asyncio.sleep(0.4)
            txt = await f.evaluate("(document.querySelector('div.input-editor')||{innerText:''}).innerText")
            if TITLE[:10] not in (txt or ""):
                raise RuntimeError(f"标题未落入: {(txt or '')[:40]!r}")
        for i in range(3):
            try:
                await fill_title()
                print("[step] 标题+话题 ✓", flush=True)
                break
            except Exception as e:
                print(f"[step] 标题第{i+1}次失败: {type(e).__name__} {str(e)[:80]}", flush=True)
                await asyncio.sleep(3)
        else:
            print("RESULT: TITLE_FAILED", flush=True)
            return False

        # ── 5. 点发表（CDP 真鼠标）→ 声明原创弹窗 → 网络层等 post_create ──
        # 10-10 六跑定案：发表按钮必须 scrollIntoView + CDP 真鼠标——JS click
        # 只半触发（能弹声明窗、永不发 post_create，页面原地不动）；真鼠标点击
        # 2s 内 post_create 落地。铁律升级：**一切提交类按钮只认真鼠标**。
        async def cdp_click_selector(sel_js, label):
            """scrollIntoView 后取坐标 → CDP 真鼠标三段式。返回是否点到。"""
            pos = await fr.evaluate(
                "(() => { const el = " + sel_js + "; if (!el) return null;"
                "el.scrollIntoView({block:'center', behavior:'instant'});"
                "const r = el.getBoundingClientRect();"
                "if (r.width === 0 || r.bottom < 0) return null;"
                "return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()")
            if not pos:
                print(f"[cdp] {label}: 未找到/不可见", flush=True)
                return False
            await cdp_click_at(pos["x"], pos["y"])
            print(f"[cdp] {label} ✓", flush=True)
            return True

        PUBLISH_BTN = ("[...document.querySelectorAll('div.form-btns button')]"
                       ".find(b => b.innerText.trim() === '发表')")
        # ── 声明原创（2026-10-13 平台改版）：原创声明从弹窗流程移到发表前页面内
        # checkbox（「声明后，作品将展示原创标记…」）。拦截弹窗的「声明原创」按钮
        # 现在只是广告分成提示，点它不带声明语义。勾选=按文本关联定位行的
        # checkbox，CDP 真鼠标（同发表铁律）。旧弹窗状态机保留（兼容双流程）。
        ORIGINAL_CK = ("[...document.querySelectorAll('input[type=checkbox]')]"
                       ".find(c => { const row = c.closest('label, div'); "
                       "return row && /声明原创|原创标记/.test(row.innerText || ''); })")
        await cdp_click_selector(ORIGINAL_CK, "勾选声明原创")
        await asyncio.sleep(0.8)
        # ── 发表前列表快照（2026-10-13 核验假阳性根治）：同标题连发时 hasToday
        # 恒 true（首发后旧标也匹配）——verify 必须以 count 增量为准。独立 tab
        # 快进快出，原页面不动。
        count_before = None
        try:
            vp0 = await ctx.new_page()
            await vp0.goto("https://channels.weixin.qq.com/platform/post/list",
                           timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(6)
            fr0 = next((f for f in vp0.frames if "micro/content" in f.url), None)
            if fr0:
                v0 = await fr0.evaluate("""(() => {
                  const m = document.body.innerText.match(/视频\\s*\\((\\d+)\\)/);
                  return m ? m[1] : null;
                })()""")
                count_before = int(v0) if v0 and v0.isdigit() else None
            await vp0.close()
            print(f"[verify-预] 发表前列表 count_before={count_before}", flush=True)
        except Exception as e:
            print(f"[verify-预] 快照失败（verify 退回 hasToday 口径）: {e}", flush=True)
        await cdp_click_selector(PUBLISH_BTN, "发表点击")
        # 弹窗状态机（09-29 两层结构；10-10 起按钮一律 CDP 真鼠标）：
        #  ①「声明原创的视频有机会获得广告分成」→ 点【声明原创】
        #  ②「原创权益」弹窗 → CDP 勾选条款 checkbox → 点【声明原创】
        #  ③ post_create（网络层）→ post/list
        # 声明状态已挂时（如重发场景）无弹窗，post_create 直发。
        DIALOG_BTN = ("[...document.querySelectorAll('.weui-desktop-dialog__wrp')]"
                      ".filter(d => d.style.display !== 'none')"
                      ".flatMap(d => [...d.querySelectorAll('button')])"
                      ".find(b => ['声明原创','直接发表','仍要发表','继续发表',"
                      "'确定','确认','知道了'].includes(b.innerText.trim()))")
        # 条款勾选（2026-10-13 四修）：checkbox 是 Ant Design ant-checkbox（原生
        # input 隐藏在 span 里，视觉由 ant-checkbox-inner 画，React onClick 挂在
        # wrapper）——点 input 本体坐标落空（v1 根因）、原生 setter 不触发 antd
        # 内部 state（v3 根因）。正解 = CDP 真鼠标点 label.ant-checkbox-wrapper
        # 中心（等价用户点击 label 整块，触发 antd onClick → setState）。
        DECLARED_CK = ("[...document.querySelectorAll('.weui-desktop-dialog__wrp')]"
                       ".filter(d => d.style.display !== 'none')"
                       ".map(d => d.querySelector('label.ant-checkbox-wrapper'))"
                       ".find(c => c && c.querySelector('input[type=checkbox]')"
                       " && !c.querySelector('input[type=checkbox]').checked)")
        handled = False
        declared = False
        for i in range(60):
            await asyncio.sleep(1.5)
            if "post/list" in page.url:
                print("[step] 跳转列表 ✓", flush=True)
                handled = True
                break
            dlgs = await dlg_buttons(fr)
            if not dlgs:
                continue
            print("[dlg]", json.dumps(dlgs, ensure_ascii=False)[:360], flush=True)
            is_rights = any("原创权益" in d.get("title", "") or
                            any("我已阅读" in b for b in d.get("btns", []))
                            for d in dlgs)
            if is_rights and not declared:
                # 人工诊断模式（SPH_DECL_DIAG=1，2026-10-13）：弹窗出现不自动点——
                # dump 弹窗 DOM 落盘，等用户在 Chrome 窗口人工勾选条款+点声明原创；
                # 网络监听（[req->]/[net]）自动记录人工点击触发的真实声明 API。
                if os.environ.get("SPH_DECL_DIAG"):
                    html = await fr.evaluate("""(() => {
                      const dlg = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
                        .find(d => d.style.display !== 'none' && /原创权益/.test(d.innerText || ''));
                      return dlg ? dlg.outerHTML : 'no-dialog';
                    })()""")
                    with open(r"E:\每日新中国\logs\sph_decl_dom.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    if not globals().get("_decl_manual_hint"):
                        globals()["_decl_manual_hint"] = True
                        print("[diag-人工] 弹窗 DOM 已落盘 sph_decl_dom.html；"
                              "请在 Chrome 窗口人工勾选条款并点【声明原创】，"
                              "我持续守望网络请求…", flush=True)
                    await asyncio.sleep(3)
                    continue
                # 条款勾选（2026-10-13 三修）：CDP 真鼠标点 checkbox 不触发 React
                # onChange（受控组件视觉勾、状态没勾）→「声明原创」按钮灰色点击无效
                # → 声明未落服务端。改用原生 setter + input/change 事件冒泡
                # （快手 set_schedule_time 同款 React 兼容技巧），文本关联定位。
                checked = await fr.evaluate("""(() => {
                  const dlg = [...document.querySelectorAll('.weui-desktop-dialog__wrp')]
                    .find(d => d.style.display !== 'none' && /原创权益/.test(d.innerText || ''));
                  if (!dlg) return 'no-dialog';
                  const boxes = [...dlg.querySelectorAll('input[type=checkbox]')]
                    .filter(c => !c.checked && !c.disabled);
                  if (!boxes.length) return dlg.querySelector('input[type=checkbox]') ? 'already' : 'no-checkbox';
                  const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'checked').set;
                  boxes.forEach(c => {
                    setter.call(c, true);
                    c.dispatchEvent(new Event('input', {bubbles: true}));
                    c.dispatchEvent(new Event('change', {bubbles: true}));
                  });
                  return boxes.every(c => c.checked) ? 'checked' : 'failed';
                })()""")
                print(f"[decl] 条款勾选（native setter）: {checked}", flush=True)
                await asyncio.sleep(0.6)
                if checked == "no-checkbox":
                    # 自绘勾选框兜底：真鼠标点「我已阅读」文本行
                    await cdp_click_selector(
                        "[...document.querySelectorAll('.weui-desktop-dialog__wrp')]"
                        ".filter(d => d.style.display !== 'none')"
                        ".flatMap(d => [...d.querySelectorAll('*')])"
                        ".find(e => /我已阅读/.test(e.innerText || '') && e.children.length === 0)",
                        "条款文本点击")
                    await asyncio.sleep(0.6)
                if await cdp_click_selector(DIALOG_BTN, "声明原创"):
                    declared = True
                    print("[decl] 声明原创已提交（CDP）", flush=True)
                continue
            if not declared and any("声明原创" in b for d in dlgs for b in d.get("btns", [])):
                if await cdp_click_selector(DIALOG_BTN, "拦截窗声明原创"):
                    print("[dlg] 拦截窗已点声明原创", flush=True)
                    # 诊断（2026-10-13：拦截窗点击后「原创权益」弹窗未出现，声明未生效）：
                    # 截全页落盘定位声明面板真实 DOM（页面内开关 or 二段弹窗 or 直接发表）
                    try:
                        await asyncio.sleep(2.5)
                        await page.screenshot(path=r"E:\每日新中国\logs\sph_decl_diag.png", full_page=True)
                        print("[diag] 声明环节全页截图 → sph_decl_diag.png", flush=True)
                    except Exception as _diag_err:
                        print(f"[diag] 截图失败（忽略）: {_diag_err}", flush=True)
                continue
            if await cdp_click_selector(DIALOG_BTN, "通用弹窗"):
                handled = True
                break
        if not handled and not declared:
            dlgs = await dlg_buttons(fr)
            print(f"[dlg] 窗口结束弹窗态: {json.dumps(dlgs, ensure_ascii=False)[:300]}", flush=True)
            print("RESULT: SUBMITTED_UNCONFIRMED（请到后台核验，勿重复发）", flush=True)
            return False

        # ── 5.5 发表后守望：网络层等 post_create（原页面冻结不动）────────
        # 「个人主页和分享卡片(3:4)」是编辑器封面预览模块的标签（预览卡固定
        # 存在），不是发表成功信号——10-10 五跑被它全数误判。唯一真信号 =
        # post_create（网络层）或跳转列表。上传在传中时提交由 md5 回调触发，
        # 可能晚数分钟，守望窗给足 10 分钟。
        if not net.get("create_ok") and "post/list" not in page.url:
            for i in range(120):
                await asyncio.sleep(5)
                if net.get("create_ok"):
                    print("[step] 发表 POST 已确认（网络层）✓", flush=True)
                    break
                if "post/list" in page.url:
                    print("[step] 跳转列表（守望期）✓", flush=True)
                    break
                if i in (12, 36, 72):
                    try:
                        toasts = await fr.evaluate(
                            """(() => [...document.querySelectorAll(
                              '[class*="toast"],[class*="Toast"],.weui-desktop-toast')]
                              .map(e => (e.innerText||'').trim()).filter(Boolean))()""")
                        if toasts:
                            print(f"[toast] {toasts}", flush=True)
                    except Exception:
                        pass  # 页面跳转中 frame 已 detach——正常

        # ── 6. 列表核验（新标签页，原页面永不再导航）────────────────────
        for i in range(20):
            await asyncio.sleep(2)
            if "post/list" in page.url:
                break
        vp = await ctx.new_page()  # 核验在独立标签页做，原页面保持冻结
        await vp.goto("https://channels.weixin.qq.com/platform/post/list",
                      timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(6)
        fr2 = next((f for f in vp.frames if "micro/content" in f.url), None)
        mark = TITLE[:6]
        try:
            v = await fr2.evaluate("""((mark) => {
              const txt = document.body.innerText;
              const m = txt.match(/视频\\s*\\((\\d+)\\)/);
              return { count: m ? m[1] : '(?)', hasToday: txt.indexOf(mark) >= 0 };
            })('%s')""" % mark)
            print("[verify]", json.dumps(v, ensure_ascii=False), flush=True)
            if not v.get("hasToday"):
                # 刚发表的视频可能几秒后才进列表（10-04 实证）：60s 后复查一次
                await asyncio.sleep(60)
                v = await fr2.evaluate("""((mark) => {
                  const txt = document.body.innerText;
                  const m = txt.match(/视频\\s*\\((\\d+)\\)/);
                  return { count: m ? m[1] : '(?)', hasToday: txt.indexOf(mark) >= 0 };
                })('%s')""" % mark)
                print("[verify-2]", json.dumps(v, ensure_ascii=False), flush=True)
            if v.get("hasToday"):
                # 2026-10-13 核验收紧：hasToday 同标题连发时恒 true（假阳性 6 连根因），
                # 必须 count 增量（=before+1）才算真发布；before 未知时退回旧口径
                inc = (count_before is not None and v.get("count", "(?)").isdigit()
                       and int(v["count"]) == count_before + 1)
                if count_before is not None and not inc:
                    print(f"RESULT: PUBLISHED_NOT_INCREMENTED"
                          f"（count {count_before}→{v.get('count')} 未+1——同标题旧条匹配或发表被拦，"
                          f"人工复核，勿以本条为准）", flush=True)
                    return False
                print("RESULT: PUBLISHED_VERIFIED"
                      + ("（count 增量确认）" if inc else "（before 未知的旧口径）"), flush=True)
                return True
            if net.get("create_ok"):
                # 服务端已受理（post_create err=0）而列表未刷出：审核中，勿重发
                print("RESULT: CREATE_OK_LIST_PENDING（服务端已受理，列表待刷出，勿重复发）",
                      flush=True)
                return "CARD_SEEN"
            print("RESULT: PUBLISHED_UNVERIFIED（未见今日标题，人工复核）", flush=True)
            return False
        except Exception as e:
            print(f"[verify] 失败: {type(e).__name__}: {str(e)[:100]}", flush=True)
            print("RESULT: PUBLISHED_UNVERIFIED（人工复核）", flush=True)
            return False


async def main():
    parse_info()
    if not TITLE:
        notify("视频号发布失败：发布信息.txt 缺视频号标题")
        sys.exit(3)
    if not os.path.isfile(VIDEO):
        notify(f"视频号发布失败：成片缺失 {VIDEO}")
        sys.exit(3)
    if not ensure_chrome():
        notify("视频号发布失败：CDP Chrome 拉起失败")
        sys.exit(3)
    ok = await run_publish()
    if ok:
        # 幂等标记（driver 完成钩子重启时跳过重复发布）
        try:
            marker = PUBDIR.rsplit("\\", 1)[0] + r"\视频号发布完成.json"
            json.dump({"date": DATE, "title": TITLE, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      open(marker, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            log(f"[marker] 写成功标记失败（不影响发布）: {e}")
        if ok == "CARD_SEEN":
            notify(f"✅ 视频号已受理发表（已发表卡片在列；列表未现=大概率原创审核中）：{TITLE}")
        else:
            notify(f"✅ 视频号发布成功并核验：{TITLE}")
        sys.exit(0)
    notify(f"⚠️ 视频号发布未完成（{TITLE}），请到后台/视频管理核验，勿重复发")
    sys.exit(3)


def check():
    """发布前干跑（AC⑤）：解析发布信息 + 查输入文件，只打印计划，不碰 Chrome、
    不写完成标记、不发飞书。退出码 0=可发，3=输入不全。"""
    parse_info()
    print(f"[check] date={DATE}", flush=True)
    print(f"[check] 发布信息: {INFO} → {_dj.to_host_path(INFO)}", flush=True)
    ok = True
    for label, path in (("发布信息", INFO), ("成片", VIDEO), ("封面", COVER)):
        hit = os.path.isfile(_dj.to_host_path(path))
        print(f"[check] {label}: {'OK' if hit else '缺失'} {path}", flush=True)
        ok = ok and hit
    print(f"[check] 标题: {TITLE or '（缺）'}", flush=True)
    print(f"[check] 标签: {','.join(TAGS or []) or '（缺）'}", flush=True)
    print(f"[check] 飞书邻居: {FG}（{'在' if os.path.isfile(FG) else '缺失'}）", flush=True)
    if not TITLE:
        print("[check] 结论: 缺视频号标题，不可发", flush=True)
        return 3
    print(f"[check] 结论: {'可发（未真发）' if ok else '输入不全，不可发'}", flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    if CHECK_ONLY:
        sys.exit(check())
    asyncio.run(main())
