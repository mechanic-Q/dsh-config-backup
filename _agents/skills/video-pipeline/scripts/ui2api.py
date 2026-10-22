#!/usr/bin/env python3
"""ui2api.py — ComfyUI UI 工作流 → API prompt 转换器（纯 Python，无需连服务器）。

原理（与 comfy run 相同）：遍历 nodes，把每个节点的 widget 值 + link 连接
映射成 {node_id: {class_type, inputs}}。

用法：
  ui2api.py <workflow.json> <output_api.json>
  [--node <node_id> --set <widget_index> <value>]  # 覆盖指定节点的 widget

说明：转换不需要 /object_info（widget 值按顺序映射到 input 的 widget 名）。
对 TE 节点同样适用（它们是标准 ComfyUI 节点）。
"""
import json, sys, argparse


def convert(workflow, overrides=None):
    """UI 格式 → API 格式。overrides: {node_id: [(widget_index, value), ...]}"""
    nodes = workflow["nodes"]
    links = workflow.get("links", [])

    # link_id -> (from_node, from_slot, to_node, to_slot)
    link_map = {}
    for l in links:
        if len(l) >= 5:
            link_id, from_node, from_slot, to_node, to_slot = l[:5]
            link_map[link_id] = (from_node, from_slot, to_node, to_slot)

    api = {}
    for n in nodes:
        nid = n["id"]
        ctype = n.get("type")
        # 跳过注释/标记类节点（Note / Label (rgthree) / 无类型）
        if not ctype or ctype == "Note" or "Label" in ctype or "note" in ctype.lower():
            continue
        # rgthree Fast Groups Bypasser：纯 UI 控制，无连接，跳过
        if "Fast Groups Bypasser" in ctype:
            continue
        inputs = {}
        # rgthree Seed：widgets[0] → seed input（特殊处理）
        if ctype == "Seed (rgthree)":
            widgets = n.get("widgets_values") or []
            inputs["seed"] = widgets[0] if widgets else 0
            api[str(nid)] = {"class_type": ctype, "inputs": inputs}
            continue
        # widget 值
        widgets = n.get("widgets_values") or []
        # VHS 等新版节点 widgets_values 是 dict（{name: value}）：直接映射，
        # 剔除 videopreview 等非输入键；有 link 的 input 仍以 link 优先
        if isinstance(widgets, dict):
            for k, v in widgets.items():
                if k in ("videopreview", "control_after_generate"):
                    continue
                inputs.setdefault(k, v)
        # node 的 input 定义（widget 顺序）
        node_inputs = n.get("inputs", [])
        widget_idx = 0
        for inp in node_inputs:
            name = inp.get("name")
            if inp.get("widget"):
                # widget input：无论是否有 link，widget 值按顺序消耗一个
                wval = (widgets[widget_idx] if isinstance(widgets, list) and widget_idx < len(widgets)
                        else widgets.get(name) if isinstance(widgets, dict) else None)
                if not isinstance(widgets, list):
                    pass  # dict 形态已在前面 setdefault，这里仅在缺失时兜底
                widget_idx += 1
                if inp.get("link") is not None:
                    # 有连接：用 link（优先级高于 widget 值）
                    link_id = inp["link"]
                    if link_id in link_map:
                        fn, fs, _, _ = link_map[link_id]
                        inputs[name] = [str(fn), fs]
                elif wval is not None:
                    inputs[name] = wval
            elif inp.get("link") is not None:
                # 非 widget 但有连接的 input：用 link
                link_id = inp["link"]
                if link_id in link_map:
                    fn, fs, _, _ = link_map[link_id]
                    inputs[name] = [str(fn), fs]
        # 额外 widget（inputs 里没有的，如某些节点的额外 widget）
        api[str(nid)] = {"class_type": ctype, "inputs": inputs}

    # 应用 overrides
    if overrides:
        for nid, ovs in overrides.items():
            nid = str(nid)
            if nid not in api:
                continue
            # 按 widget 名覆盖：需要从节点 inputs 找 widget 名
            for node in nodes:
                if str(node["id"]) == nid:
                    node_inputs = node.get("inputs", [])
                    wnames = [i["name"] for i in node_inputs if i.get("widget")]
                    break
            for wi, val in ovs:
                if wi < len(wnames):
                    api[nid]["inputs"][wnames[wi]] = val
    return api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_json")
    ap.add_argument("output_api")
    ap.add_argument("--node", action="append", nargs="+",
                    help="--node <id> <widget_idx> <value>")
    args = ap.parse_args()

    wf = json.load(open(args.workflow_json, encoding="utf-8"))
    overrides = {}
    if args.node:
        for g in args.node:
            nid, wi, val = g[0], int(g[1]), g[2]
            overrides.setdefault(nid, []).append((wi, val))

    api = convert(wf, overrides)
    with open(args.output_api, "w", encoding="utf-8") as f:
        json.dump(api, f, ensure_ascii=False, indent=2)
    print(f"转换完成: {len(api)} 节点 → {args.output_api}")
    # 打印几个关键节点确认
    for k in list(api)[:5]:
        print(f"  {k}: {api[k]['class_type']}")


if __name__ == "__main__":
    main()
