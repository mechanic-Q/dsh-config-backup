#!/usr/bin/env python3
"""krea2 数字迷幻封面生成：横版(16:9) + 竖版(9:16) 提交 ComfyUI。
用法：python3 gen_krea2_cover.py <项目根> [--ref-file <参考文本文件>]
  --ref-file：文件文本同时作 H/V 两版参考（driver 单 ref 语义，构图差异靠
  preset/尺寸参数）；缺省回落硬编码 REF_H/REF_V 样本（手工一键生成路径）。
  票9 #72：替代 driver 运行期 re.sub 源码补丁 + 临时副本（源码移植术拆除）。
"""
import json, os, sys, subprocess, time, importlib.util, re, glob, shutil, tempfile

# #151 双宿主口径：WSL 下 /mnt/e，Windows 下盘符直写
if os.name == "nt":
    WF = r"E:\AI\Krea2资源\数字迷幻工作流、规则、插件、例图\工作流\krea2数字迷幻流.json"  # noqa: mechanical
    WIN_OUT = r"E:\AI\Comfyui-WF-2026.8.8\Comfyui-WF-2026.8.8\ComfyUI\output"  # noqa: mechanical
    _TEMP = os.environ.get("TEMP", r"C:\Users\LMR\AppData\Local\Temp")  # noqa: mechanical
else:
    WF = "/mnt/e/AI/Krea2资源/数字迷幻工作流、规则、插件、例图/工作流/krea2数字迷幻流.json"
    WIN_OUT = "/mnt/e/AI/Comfyui-WF-2026.8.8/Comfyui-WF-2026.8.8/ComfyUI/output"
    _TEMP = "/mnt/c/Users/LMR/AppData/Local/Temp"  # noqa: mechanical — WSL 宿主口径，与 OBJ_INFO 同址
HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location("krea2_to_api", f"{HERE}/krea2_to_api.py")
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)

def ps(cmd, timeout=120):
    PS = ("powershell.exe" if os.name == "nt"
          else "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    r = subprocess.run([PS,
                        "-NoProfile", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd],
                       capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", errors="replace")


OBJ_INFO = os.path.join(_TEMP, "object_info.json")

def _get_object_info():
    """拉取 ComfyUI 节点 schema。缓存超 24h 自动刷新（工作流/插件升级后枚举会变）。"""
    need = True
    if os.path.exists(OBJ_INFO):
        need = (time.time() - os.path.getmtime(OBJ_INFO)) > 86400
    if need:
        print("[obj_info] 拉取 ComfyUI /object_info ...")
        r = subprocess.run(["curl", "-s", "-m", "30",
                            "http://192.168.50.231:18188/object_info", "-o", OBJ_INFO],
                           capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(OBJ_INFO):
            raise FileNotFoundError("object_info 拉取失败，检查 ComfyUI 是否在线 (192.168.50.231:18188)")
    return json.load(open(OBJ_INFO, encoding="utf-8-sig"))

# 参考文本（数字迷幻风格，2026-09-02 每日新中国·天宫塔林主题）
REF_H = (
    "2026年9月2日中国新闻大事件，数字迷幻科技海报。"
    "主体：一片中式天宫塔林——无数晶莹剔透的光谱棱面塔、玉米肌理塔、风电螺旋塔、火箭发射架塔"
    "如森林般拔地而起，塔身流淌霓虹数据流与全息电路，每一座塔都在跃动金色电流光纹。"
    "背景：一轮巨大的金色朝阳从塔林地平线升起，光芒被数字粒子打散成亿万光点。"
    "前景：极小的古装人物剪影仰望塔林，衬托建筑的宏大。天空漂浮巨大的悬浮数字\"9月2日\"，"
    "由发光像素构成，边缘渗出彩虹光晕。"
    "色彩：冷蓝灰低饱和底、朱红鎏金高饱和点缀、霓虹青蓝、电光金——高饱和数字迷幻。"
    "构图：横向宽银幕，塔林为主视觉，太阳为光源，中式天宫与赛博科技交织，宏大震撼。"
    "数字迷幻风格，赛博光影，全息质感，流光溢彩，丁达尔圣光。"
)
REF_V = (
    "2026年9月2日中国新闻大事件，数字迷幻科技海报。"
    "主体：一束巨大的金色光柱自下而上贯穿画面，光柱由无数发光的中式天宫塔粒子螺旋上升组成，"
    "每一座塔都在跃动霓虹数据流。光柱顶端托起一轮炽热的数字太阳，太阳内部浮现动态的"
    "新闻数据仪表盘与攀升曲线。画面下部是浩瀚的天宫塔林阵列，晶莹棱面塔表面流淌全息电路。"
    "前景极小的古装人物剪影仰望塔林，衬托建筑的宏大。"
    "天空漂浮巨大的悬浮数字\"9月2日\"，由发光像素构成，边缘渗出彩虹光晕。"
    "色彩：冷蓝灰低饱和底、朱红鎏金高饱和点缀、霓虹青蓝、电光金——高饱和数字迷幻。"
    "构图：竖向海报，金色光柱居中贯穿，数字太阳在上，塔林阵列在下，垂直纵深，"
    "中式天宫与赛博科技交织，宏大震撼。数字迷幻风格，赛博光影，全息质感，流光溢彩。"
)

def submit(ref, preset, w, h, tag):
    wf = json.load(open(WF, encoding="utf-8"))
    # 强制切换为"手动输入参考"模式：激活 198，静音图片链/LLM扩写链，保留原生成链(mode 不变)
    LLM_SILENT = {196, 197, 187, 199, 194, 208, 209, 216, 215, 30, 191, 192, 193, 210, 212, 218}
    for n in wf["nodes"]:
        nid = n["id"]
        if nid in (198, 217, 71):   # 手动链激活
            n["mode"] = 0
        elif nid in LLM_SILENT:      # 图片/随机/LLM扩写链静音（绕开 9router 429）
            n["mode"] = 4
        # 其余节点保持原始 mode
        if nid == 218:  # GetNode 指向"手动输入参考生成"（工作流原指向图片链）
            n["widgets_values"] = ["手动输入参考生成"]
            n["mode"] = 4
    api = k.build_api(wf, _get_object_info(), ref, preset, w, h)
    # 修复 unet_name 反斜杠子目录（Krea2\xxx -> xxx，模型在根目录）
    for nid, node in api.items():
        if node.get("class_type") == "UNETLoader" and "unet_name" in node.get("inputs", {}):
            v = node["inputs"]["unet_name"]
            if isinstance(v, str) and "\\" in v:
                node["inputs"]["unet_name"] = v.split("\\")[-1]
                print(f"[{tag}] unet_name 修复: {v} -> {node['inputs']['unet_name']}")
    # 71 文本2 直连 198（原文），绕过 GetNode/LLM 扩写
    for nid, node in api.items():
        if node.get("class_type") == "ZML_SelectTextV2" and nid in ("71",):
            node["inputs"]["文本2"] = ["198", 0]
            node["inputs"]["启用2"] = True
            node["inputs"]["启用1"] = False
            print(f"[{tag}] node 71 文本2 直连 198(原文)，绕过 LLM 扩写")
    # 摘掉 194 PromptCategoryReader（题材历史去重，手动模式不需要）：断开 196 的文本3
    # 摘掉 ShowText 显示节点（207→209, 138→218 死引用，不影响生成）
    for nid in list(api.keys()):
        if api[nid].get("class_type") == "ShowText|pysssss":
            del api[nid]
            print(f"[{tag}] node {nid} (ShowText) 已移除")
    # QuickSavePrompt 等保存类节点：category 枚举可能与工作流版本不一致导致校验失败，且不影响生成，直接移除
    for nid in list(api.keys()):
        if api[nid].get("class_type") == "QuickSavePrompt":
            del api[nid]
            print(f"[{tag}] node {nid} (QuickSavePrompt) 已移除")
    for nid, node in api.items():
        if node.get("class_type") == "ZML_SelectTextV2" and "194" in str(node.get("inputs", {}).get("文本3", "")):
            node["inputs"]["文本3"] = ""
            if "启用3" in node["inputs"]:
                node["inputs"]["启用3"] = False
            print(f"[{tag}] node {nid} 文本3 已断开(摘除题材历史)")
    if "194" in api:
        del api["194"]
        print(f"[{tag}] node 194 (PromptCategoryReader) 已移除")
    # 修复 Lora Loader (LoraManager) 缺 loras 字段：从工作流 widgets 提取
    wf_nodes = {n["id"]: n for n in json.load(open(WF, encoding="utf-8"))["nodes"]}
    for nid, node in api.items():
        if node.get("class_type") == "Lora Loader (LoraManager)":
            # LoraManager category 参数：object_info 默认值可能是旧枚举（krea数字梦幻），强制修正为合法值
            node["inputs"]["category"] = "krea2数字魔幻"
            print(f"[{tag}] node {nid} category 强制 = krea2数字魔幻")
            if "loras" not in node.get("inputs", {}):
                src = wf_nodes.get(int(nid))
                if src:
                    wv = src.get("widgets_values", [])
                    loras = None
                    if len(wv) >= 3 and isinstance(wv[2], list):
                        loras = wv[2]
                    elif len(wv) >= 2 and isinstance(wv[1], dict) and "loras" in wv[1]:
                        loras = wv[1]["loras"]
                    if loras:
                        # 修复 LoRA 名带 " (1)" 后缀（本地库无此名，去后缀）
                        for lr in loras:
                            nm = lr.get("name", "")
                            if nm.endswith(" (1)"):
                                lr["name"] = nm[:-4]
                                print(f"[{tag}] LoRA 名修复: {nm} -> {lr['name']}")
                        node["inputs"]["loras"] = {"__value__": loras}
                        print(f"[{tag}] node {nid} loras 补齐 ({len(loras)} 个)")
            else:
                # loras 已存在（build_api 生成）——同样去 " (1)" 后缀（本地库无此名）
                lv = node["inputs"].get("loras", {})
                loras = lv.get("__value__", []) if isinstance(lv, dict) else lv
                if isinstance(loras, list):
                    for lr in loras:
                        nm = lr.get("name", "")
                        if nm.endswith(" (1)"):
                            lr["name"] = nm[:-4]
                            print(f"[{tag}] LoRA 名修复(existing): {nm} -> {lr['name']}")
    body = json.dumps({"prompt": api}, ensure_ascii=False)
    # #151 双宿主：JSON 直落 Windows 可见的临时目录（PS cmd 读 C:\Users\...\Temp
    # 同址文件；WSL 下 _TEMP 即 /mnt/c/...，无需再 cp）
    tmp_json = os.path.join(_TEMP, f"krea2_{tag}.json")
    with open(tmp_json, "w", encoding="utf-8") as f:
        f.write(body)
    out = ps(f"$body = [System.IO.File]::ReadAllBytes('C:\\Users\\LMR\\AppData\\Local\\Temp\\krea2_{tag}.json'); "
             f"try {{ $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/prompt' -Method Post -Body $body "
             f"-ContentType 'application/json' -TimeoutSec 15 -UseBasicParsing; Write-Host $r.Content }} "
             f"catch {{ Write-Host ('ERROR ' + $_.Exception.Message); if ($_.ErrorDetails) {{ Write-Host $_.ErrorDetails.Message }} }}")
    print(f"[{tag}] submit: {out[:200]}")
    m = re.search(r'"prompt_id"\s*:\s*"([^"]+)"', out)
    if not m:
        print(f"[{tag}] 无 prompt_id，node_errors: {out[:400]}")
        return None
    # 检查 node_errors 是否为空（有错误则没真跑）
    if '"node_errors": {}' not in out:
        print(f"[{tag}] ⚠️ node_errors 非空，可能未实际生成: {out[out.find('node_errors'):out.find('node_errors')+300]}")
    pid = m.group(1)
    # 等完成（检查 status_str=success）
    ok = False
    for _ in range(120):
        h = ps(f"try {{ $h = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/history/{pid}' -UseBasicParsing -TimeoutSec 5; Write-Host $h.Content }} catch {{ Write-Host '' }}")
        if pid in h:
            try:
                hj = json.loads(h[h.find('{'):h.rfind('}')+1])
                st = hj.get(pid, {}).get("status", {})
                if st.get("status_str") == "success" and st.get("completed"):
                    ok = True
                    break
            except Exception:
                pass
        time.sleep(10)
    print(f"[{tag}] 完成: {ok}")
    return pid

def main(argv):
    # root 位置参数守卫（#74 review）：漏 root 直打 --ref-file 曾把 flag 当
    # root 静默 makedirs（scripts/--ref-file/ 游离目录实证），响亮拒绝
    if len(argv) < 2 or argv[1].startswith("-"):
        raise SystemExit(f"用法: python3 {os.path.basename(argv[0]) if argv else 'gen_krea2_cover.py'}"
                         " <项目根> [--ref-file <参考文本文件>]")
    root = argv[1]
    os.makedirs(os.path.join(root, "09_封面"), exist_ok=True)
    ref_h, ref_v = REF_H, REF_V
    if "--ref-file" in argv:
        fp = argv[argv.index("--ref-file") + 1]
        text = open(fp, encoding="utf-8").read().strip()
        if not text:
            raise ValueError(f"--ref-file 为空: {fp}")
        ref_h = ref_v = text  # driver 单 ref：H/V 同文本，构图差异靠 preset/尺寸
    # 横版 16:9 (SDXL_1344x768, 1536x768) / 竖版 9:16 (SDXL_768x1344, 768x1344)
    submit(ref_h, "SDXL_1344x768", 1536, 768, "H")
    submit(ref_v, "SDXL_768x1344", 768, 1344, "V")
    print("done")


if __name__ == "__main__":
    main(sys.argv)
