#!/usr/bin/env python3
"""krea2 数字迷幻工作流 → ComfyUI API 图转换器（手动输入参考模式）

用法:
  krea2_to_api.py <workflow.json> <out_api.json> <参考文本> <分辨率预设> <宽> <高>
  例: krea2_to_api.py krea2数字迷幻流-CLI.json out.json "文本" SDXL_1344x768 1536 768

处理:
  - 过滤 mode=4 静音节点 + pass-through 透传
  - SetNode/GetNode 同名配对展开
  - KSampler/KSamplerAdvanced 硬编码 widget 映射
  - Anything Everywhere 的 VAE 广播 → 显式连给 VAEDecode/VAEEncode
  - 198 填参考文本, 140/18 设分辨率
"""
import json, sys, copy

CONTROL = {"randomize", "fixed", "increment", "decrement"}

def build_api(wf, obj, ref_text, preset, width, height):
    # 填手动输入 + 分辨率
    nodes = {n["id"]: n for n in wf["nodes"]}
    nodes[198]["widgets_values"] = [ref_text]
    nodes[140]["widgets_values"][0] = preset
    nodes[18]["widgets_values"] = [width, height, 1]
    # === 枚举自适应：从 object_info 读每个输入的合法枚举，工作流里的值不合法就用枚举默认项 ===
    # （Krea2 工作流/插件升级后 category 等枚举值会变，硬编码修正串会过期）
    def _enum_fix(api_prompt):
        for nid, node in api_prompt.items():
            schema = obj.get(node.get("class_type", ""), {})
            inp_schema = schema.get("input", {})
            for group in ("required", "optional"):
                for name, spec in (inp_schema.get(group) or {}).items():
                    # 兼容三种 schema 形态：
                    #   A) ["TYPE", [枚举], {"default": x}]      （ComfyUI 标准组合型）
                    #   B) [[枚举...], {"default": x}]           （枚举直接在首位，本机 LoraManager 实测形态）
                    #   C) [[枚举...]]                            （纯枚举列表，如 UNETLoader.unet_name，无 default）
                    allowed = default = None
                    if isinstance(spec, list) and spec:
                        if isinstance(spec[0], list):
                            allowed = spec[0]
                            default = spec[1].get("default", spec[0][0]) if len(spec) > 1 and isinstance(spec[1], dict) else spec[0][0]
                        elif len(spec) >= 2 and isinstance(spec[1], list):
                            allowed = spec[1]
                            default = spec[2] if len(spec) > 2 else spec[1][0]
                    if not allowed:
                        continue
                    val = node["inputs"].get(name)
                    if isinstance(val, str) and val.strip() in allowed:
                        if val != val.strip():
                            node["inputs"][name] = val.strip()
                        continue
                    if isinstance(val, str):
                        node["inputs"][name] = default
                        print(f"[enum-fix] node {nid} {name}: {val!r} -> {default!r}")
        return api_prompt

    # === LoRA 名清洗：去 " (1)" 这类重复下载后缀（本地库无此名会校验失败） ===
    def _lora_clean(api_prompt):
        for nid, node in api_prompt.items():
            lv = node["inputs"].get("loras")
            loras = lv.get("__value__", []) if isinstance(lv, dict) else (lv or [])
            if isinstance(loras, list):
                for lr in loras:
                    nm = lr.get("name", "") if isinstance(lr, dict) else ""
                    if nm.endswith(" (1)"):
                        lr["name"] = nm[:-4]
                        print(f"[lora-clean] node {nid}: {nm!r} -> {lr['name']!r}")
        return api_prompt

    links = {}
    for raw_link in wf.get("links", []):
        if not isinstance(raw_link, list) or len(raw_link) < 6:
            continue
        links[int(raw_link[0])] = [str(raw_link[1]), int(raw_link[2])]
    muted = {str(n["id"]) for n in wf["nodes"] if n.get("mode", 0) == 4}
    set_values, get_nodes, passthrough = {}, {}, {}
    for node in wf["nodes"]:
        if str(node.get("id")) in muted:
            continue
        nid, ctype = str(node.get("id")), node.get("type")
        widgets = node.get("widgets_values", [])
        if ctype == "SetNode" and isinstance(widgets, list) and widgets:
            il = None
            for ii in node.get("inputs", []) or []:
                if ii.get("link") is not None:
                    il = int(ii["link"]); break
            if il is not None and il in links:
                set_values[str(widgets[0])] = links[il]
        elif ctype == "GetNode" and isinstance(widgets, list) and widgets:
            get_nodes[nid] = str(widgets[0])
    for node in wf["nodes"]:
        if str(node.get("id")) not in muted:
            continue
        for ii in node.get("inputs", []) or []:
            if ii.get("link") is not None and int(ii["link"]) in links:
                passthrough[str(node["id"])] = links[int(ii["link"])]; break

    def resolve(link_id):
        src = links[link_id]; sn = str(src[0])
        if sn in get_nodes and get_nodes[sn] in set_values:
            src = set_values[get_nodes[sn]]; sn = str(src[0])
        while sn in passthrough:
            src = passthrough[sn]; sn = str(src[0])
            if sn in get_nodes and get_nodes[sn] in set_values:
                src = set_values[get_nodes[sn]]; sn = str(src[0])
        return src

    prompt = {}
    for node in wf["nodes"]:
        nid, ctype = str(node.get("id")), node.get("type")
        if not nid or not ctype:
            continue
        if nid in muted or ctype in ("SetNode", "GetNode", "MarkdownNote", "Fast Groups Bypasser (rgthree)"):
            continue
        schema_in = obj.get(ctype, {}).get("input", {})
        schema = {**schema_in.get("required", {}), **schema_in.get("optional", {})}
        raw_w = node.get("widgets_values", [])
        node_in = node.get("inputs", [])
        inputs = {}
        for inp in node_in:
            name, lid = inp.get("name"), inp.get("link")
            if lid is not None and name and int(lid) in links:
                inputs[name] = resolve(lid)
        if ctype == "KSampler":
            w = raw_w
            for n, v in [("seed", w[0]), ("steps", w[2]), ("cfg", w[3]), ("sampler_name", w[4]), ("scheduler", w[5]), ("denoise", w[6])]:
                inputs.setdefault(n, v)
            prompt[nid] = {"class_type": ctype, "inputs": inputs}; continue
        if ctype == "KSamplerAdvanced":
            w = raw_w
            for n, v in [("add_noise", w[0]), ("noise_seed", w[1]), ("steps", w[3]), ("cfg", w[4]), ("sampler_name", w[5]), ("scheduler", w[6]), ("start_at_step", w[7]), ("end_at_step", w[8]), ("return_with_leftover_noise", w[9])]:
                inputs.setdefault(n, v)
            prompt[nid] = {"class_type": ctype, "inputs": inputs}; continue
        non_widget = {"MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK", "CONTROL_NET", "STYLE_MODEL", "GLIGEN", "LORA_STACK"}
        wi = 0
        # 特例：Lora Loader (LoraManager) 的 loras 是 widgets_values[2] 数组（LoraManager 特有，schema 标 LORAS 但值是 widget）
        # API 模式校验器把裸数组当连接，必须用 {"__value__": [...]} 包装；且值须是字典数组（_collect_widget_entries 用 .get("active")）
        if ctype == "Lora Loader (LoraManager)" and "loras" not in inputs and len(raw_w) >= 3 and isinstance(raw_w[2], list):
            loras_dict = []
            syntax_parts = []
            for l in raw_w[2]:
                if not l.get("active", True):
                    continue
                name = l["name"]
                ms = float(l.get("strength", 1)); cs = float(l.get("clipStrength", ms))
                loras_dict.append({"name": name, "strength": ms, "clipStrength": cs, "active": True})
                syntax_parts.append(f"<lora:{name}:{ms:g}:{cs:g}>")
            inputs["loras"] = {"__value__": loras_dict}
            # text 用 lora 语法串（LoraManager 的 text widget）
            inputs["text"] = " ".join(syntax_parts)
        for name, spec in schema.items():
            if name in inputs:
                typ = spec[0]
                if not (isinstance(typ, str) and typ in non_widget):
                    wi += 1
                continue
            typ = spec[0]
            if isinstance(typ, str) and typ in non_widget:
                continue
            if wi >= len(raw_w):
                break
            val = raw_w[wi]; wi += 1
            if isinstance(val, str) and val in CONTROL:
                if wi >= len(raw_w):
                    break
                val = raw_w[wi]; wi += 1
            inputs[name] = val
        prompt[nid] = {"class_type": ctype, "inputs": inputs}
    # VAE 广播补连
    for nid in ("1", "59", "220"):
        if nid in prompt and "vae" not in prompt[nid]["inputs"]:
            prompt[nid]["inputs"]["vae"] = ["4", 0]
    # 剔除保存/显示/扩写类节点（QuickSavePrompt / PromptExpand / ShowText）：
    # 不影响生成，但其枚举字段或悬空引用会随插件升级漂移卡校验。
    # 注意：不做级联剔除——引用它们的下游主链节点（如 71 文本选择器）由调用方做直连修复。
    for nid in list(prompt.keys()):
        ct = prompt[nid].get("class_type", "")
        if "SavePrompt" in ct or ct in ("PromptExpand", "ShowText|pysssss"):
            del prompt[nid]
            print(f"[drop] node {nid} ({ct}) 已剔除")
    # 引用已剔除节点的输入置空（None），交由下游"启用X"开关旁路（ZML_SelectTextV2 等设计如此）
    live = set(prompt.keys())
    for nid, node in prompt.items():
        for name, v in node["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in live:
                node["inputs"][name] = None
                print(f"[null] node {nid}.{name} 引用已剔除节点 {v[0]}，置空（应配启用开关旁路）")
    prompt = _lora_clean(_enum_fix(prompt))
    return prompt

if __name__ == "__main__":
    wf_path, out_path, ref, preset, w, h = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
    wf = json.load(open(wf_path, encoding="utf-8"))
    obj_path = "/mnt/c/Users/LMR/AppData/Local/Temp/object_info.json"
    obj = json.load(open(obj_path, encoding="utf-8-sig")) if __import__("os").path.exists(obj_path) else {}
    api = build_api(wf, obj, ref, preset, w, h)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"prompt": api}, f, ensure_ascii=False)
    print(f"OK {len(api)} nodes -> {out_path}")
