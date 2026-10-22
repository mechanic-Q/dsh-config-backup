#!/usr/bin/env python3
"""migrate_project_dirs.py — 存量项目目录迁移（2026-09-07 阶段号配对重整）。

旧布局 → 新布局（目录号 = driver 阶段号 s01-s13 配对）：
  01_整体视觉方案/            ─┐
  02_逐段视觉设计/shot*、交接卡 ├→ 03_导演设计/
  03_分镜增强/                 → 04_分镜增强/
  04_美术画质/                 → 05_美术画质/
  05_H3prompt/                 → 06_H3prompt/
  06_TTS语音/                  → 02_TTS语音/
  02_逐段视觉设计/raw+口播台词 ─→ 01_分段/
  00_口播稿/_welcome、_welcome_audio → 02_TTS语音/_welcome
  00/07/08/09/10 不变。

用法：migrate_project_dirs.py <项目slug目录> [<slug2> ...]（--dry-run 预览）
幂等：已是新布局的项目自动跳过。stamps 假定已由 driver（≥017f014）迁移为 s01-s13。
"""
import os
import shutil
import sys


def merge_dir(src, dst, dry):
    """把 src 目录整体并入 dst（dst 存在时逐文件搬运，否则直接 rename）。"""
    if not os.path.isdir(src):
        return False
    if dry:
        print(f"  [DRY] {src} → {dst}")
        return True
    if not os.path.exists(dst):
        os.replace(src, dst)
        print(f"  {src} → {dst}")
        return True
    for root, _, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target_root, exist_ok=True)
        for f in files:
            fsrc = os.path.join(root, f)
            fdst = os.path.join(target_root, f)
            if os.path.exists(fdst):
                # 同名同内容 → 删源；不同 → 源保留为 .migrated 后缀（不丢数据）
                if os.path.getsize(fsrc) == os.path.getsize(fdst):
                    os.remove(fsrc)
                else:
                    base, ext = os.path.splitext(f)
                    os.replace(fsrc, os.path.join(target_root, f"{base}.migrated{ext}"))
                    print(f"  ⚠️ 同名不同内容: {f} → {base}.migrated{ext}")
                continue
            shutil.move(fsrc, fdst)
    # 自底向上删空子目录壳（merge 不产生文件时空目录会残留）
    for root, dirs, files in os.walk(src, topdown=False):
        if not dirs and not files:
            os.rmdir(root)
    if os.path.isdir(src):
        leftover = os.listdir(src)
        print(f"  ⚠️ {src} 非空未删（{len(leftover)} 项）: {leftover[:3]}")
        return False
    print(f"  {src} → {dst}（合并）")
    return True
    print(f"  {src} → {dst}（合并）")
    return True


def move_file(src, dst, dry):
    if not os.path.exists(src):
        return False
    if dry:
        print(f"  [DRY] {src} → {dst}")
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)
    print(f"  {src} → {dst}")
    return True


def migrate(slug, dry=False):
    print(f"== {slug} ==")
    if not os.path.isdir(slug):
        print("  ✗ 目录不存在，跳过")
        return False
    j = os.path.join
    ok = True
    # 顺序即安全序：06→02 先腾空 06；05→06 腾空 05；04→05；03→04；
    # 01/02 内容并入 03 后删除空壳
    steps = [
        ("dir", j(slug, "06_TTS语音"), j(slug, "02_TTS语音")),
        ("dir", j(slug, "05_H3prompt"), j(slug, "06_H3prompt")),
        ("dir", j(slug, "04_美术画质"), j(slug, "05_美术画质")),
        ("dir", j(slug, "03_分镜增强"), j(slug, "04_分镜增强")),
        ("dir", j(slug, "01_整体视觉方案"), j(slug, "03_导演设计")),
    ]
    for _, src, dst in steps:
        ok = merge_dir(src, dst, dry) and ok
    # 02_逐段视觉设计 拆分：raw/口播台词 → 01_分段；shot/交接卡 → 03_导演设计
    old02 = j(slug, "02_逐段视觉设计")
    if os.path.isdir(old02):
        seg = j(slug, "01_分段")
        move_file(j(old02, "分镜清单_raw.json"), j(seg, "分镜清单_raw.json"), dry)
        move_file(j(old02, "分镜清单_raw.json.done.json"), j(seg, "分镜清单_raw.json.done.json"), dry)
        merge_dir(j(old02, "口播台词"), j(seg, "口播台词"), dry)
        merge_dir(old02, j(slug, "03_导演设计"), dry)
    # 欢迎词归位（两种历史位置 → 02_TTS语音/_welcome）
    wdst = j(slug, "02_TTS语音", "_welcome")
    for wsrc in (j(slug, "00_口播稿", "_welcome_audio"),
                 j(slug, "00_口播稿", "_welcome")):
        if os.path.isdir(wsrc):
            merge_dir(wsrc, wdst, dry)
    # 根级产物完成标记（pipeline_contract.stamp_output 遗留）同步改名
    for old, new in (("01_整体视觉方案.done.json", "03_导演设计.done.json"),
                     ("02_逐段视觉设计.done.json", "03_导演设计.done.json"),
                     ("03_分镜增强.done.json", "04_分镜增强.done.json"),
                     ("04_美术画质.done.json", "05_美术画质.done.json"),
                     ("05_H3prompt.done.json", "06_H3prompt.done.json"),
                     ("06_TTS语音.done.json", "02_TTS语音.done.json")):
        move_file(j(slug, old), j(slug, new), dry)
    print("  完成" if ok else "  部分跳过")
    return ok


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    for slug in args:
        migrate(os.path.abspath(slug), dry)


if __name__ == "__main__":
    main()
