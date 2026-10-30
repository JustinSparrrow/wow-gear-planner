"""抽天赋节点表（SimC 的 trait_data.inc）并顺带抓"加绿字"的天赋效果。

为什么要这份数据
----------------
`talents=` 字符串只编码"哪些节点被选中"，不含名字和数值。要把它变成
"这条天赋 +85 暴击"，需要两样东西：

1. **节点表**：node_id -> 名字 / 法术 id / 最大级数 / 属于哪个树；
2. **法术效果**：每个天赋法术实际给了什么（加绿字、加百分比、还是纯技能改动）。

第 1 样在 SimC 的 `engine/dbc/generated/trait_data.inc` 里（字段顺序见
`trait_data.hpp` 的 `trait_data_t`）。第 2 样用 wago.tools 的 SpellEffect 按法术逐条查，
只查询选中过的那些（几十条），不整表下载。

    python src/build/fetch_talents.py --from-file <trait_data.inc>
    python src/build/fetch_talents.py --fetch-effects     # 联网补效果（走代理）

产物：
    data/raw/talents.json   {"classes": {"3": {"102423": [{...}]}}, "classes_mask": ...}
    data/raw/talent_effects.json  {"<spell>": [[aura, misc, value], ...]}
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(RAW, "talents.json")
EFFECTS = os.path.join(RAW, "talent_effects.json")
INC = "engine/dbc/generated/trait_data.inc"
SIMC_ZIP = "https://codeload.github.com/simulationcraft/simc/zip/refs/heads/midnight"

ROW = re.compile(
    r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),"
    r"\s*(\d+),\s*(\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*\"((?:[^\"\\]|\\.)*)\","
    r"\s*\{([^}]*)\},\s*\{([^}]*)\},\s*(\d+),\s*(\d+)\s*\}")

TREES = {0: "class", 1: "class", 2: "spec", 3: "hero", 4: "hero"}


def parse(text):
    """trait_data.inc -> {class_id: {node_id: [entry, ...]}}。

    trait_data_t 字段顺序：
      tree_index, id_class, id_trait_node_entry, id_node, max_ranks, req_points,
      id_trait_definition, id_spell, id_replace_spell, id_override_spell,
      row, col, selection_index, name, id_spec[4], id_spec_starter[4],
      id_sub_tree, node_type
    """
    classes = {}
    for m in ROW.finditer(text):
        (tree, cls, entry, node, max_ranks, req, definition, spell,
         _repl, _over) = m.group(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
        row, col, sel = (int(m.group(11)), int(m.group(12)), int(m.group(13)))
        name = m.group(14)
        specs = [int(x) for x in re.findall(r"-?\d+", m.group(15))]
        sub_tree, node_type = int(m.group(17)), int(m.group(18))
        e = {
            "name": name,
            "spell": int(spell),
            "node": int(node),
            "entry": int(entry),
            "definition": int(definition),
            "max_ranks": int(max_ranks),
            "tree": TREES.get(int(tree), "class"),
            "spec": [s for s in specs if s],
            "sub_tree": sub_tree,
            "node_type": node_type,
            "row": row, "col": col, "sel": sel,
        }
        classes.setdefault(int(cls), {}).setdefault(int(node), []).append(e)
    # 同一节点内的多个条目按 selection_index 排，和游戏里的选项顺序一致
    for nodes in classes.values():
        for entries in nodes.values():
            entries.sort(key=lambda x: (x["sel"], x["entry"]))
    return classes


def _from_zip(zip_path):
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        n = next(n for n in z.namelist() if n.endswith(INC))
        return z.read(n).decode("utf-8")


def download(proxy=""):
    print(f"下载 {SIMC_ZIP}")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "simc.zip")
    req = urllib.request.Request(SIMC_ZIP, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=900) as r, io.open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    try:
        return _from_zip(tmp)
    finally:
        os.remove(tmp)


def fetch_effects(spells, proxy=""):
    """按法术 id 查 SpellEffect，只留"改战斗评级"的（108 号光环）。"""
    build = ""
    meta = os.path.join(RAW, "meta.json")
    if os.path.exists(meta):
        build = json.load(io.open(meta, encoding="utf-8")).get("wowBuild") or ""
    if not build:
        print("读不到 meta.json 的 build 号，跳过效果抓取")
        return {}
    out = {}
    base = "https://wago.tools/db2/SpellEffect/csv"
    for i, sid in enumerate(sorted(set(spells))):
        if not sid:
            continue
        url = f"{base}?build={build}&filter%5BSpellID%5D={sid}&filter%5BDifficultyID%5D=0"
        cmd = ["curl.exe", "-s", "-L", "--compressed", "--max-time", "40", url]
        if proxy:
            cmd[1:1] = ["-x", proxy]
        r = subprocess.run(cmd, capture_output=True)
        text = r.stdout.decode("utf-8", "ignore")
        lines = text.strip().splitlines()
        if len(lines) < 2:
            continue
        hdr = lines[0].split(",")
        try:
            i_aura, i_misc, i_val = (hdr.index("EffectAura"), hdr.index("EffectMiscValue_0"),
                                     hdr.index("EffectBasePointsF"))
        except ValueError:
            continue
        rows = []
        for ln in lines[1:]:
            f = ln.split(",")
            if len(f) <= max(i_aura, i_misc, i_val):
                continue
            try:
                rows.append([int(f[i_aura]), int(f[i_misc]), float(f[i_val])])
            except ValueError:
                continue
        if rows:
            out[str(sid)] = rows
        if (i + 1) % 25 == 0:
            print(f"  …{i + 1}/{len(spells)}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="抽天赋节点表")
    ap.add_argument("--from-file", help="本地 SimC 的 trait_data.inc")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""))
    ap.add_argument("--fetch-effects", action="store_true",
                    help="联网按法术查效果（会慢，几十条）")
    args = ap.parse_args(argv)

    if args.from_file:
        text = io.open(args.from_file, encoding="utf-8").read()
        source = args.from_file
    else:
        text = download(args.proxy)
        source = SIMC_ZIP

    classes = parse(text)
    total = sum(len(nodes) for nodes in classes.values())
    entries = sum(len(e) for nodes in classes.values() for e in nodes.values())
    build = ""
    meta = os.path.join(RAW, "meta.json")
    if os.path.exists(meta):
        build = json.load(io.open(meta, encoding="utf-8")).get("wowBuild") or ""
    payload = {
        "_meta": {
            "source": source,
            "wow_build": build,
            "note": ("职业 -> node_id -> 条目。节点顺序必须按 node_id 升序，"
                     "这是天赋字符串的位序。" + str(classes.keys())),
        },
        "classes": {str(k): {str(n): v for n, v in nodes.items()}
                    for k, nodes in classes.items()},
    }
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
    print(f"职业 {len(classes)} 个、节点 {total} 个、条目 {entries} 条 -> {os.path.relpath(OUT, ROOT)}")

    if args.fetch_effects:
        spells = [e["spell"] and int(e["spell"])
                  for nodes in classes.values() for es in nodes.values() for e in es]
        print(f"查 {len(set(s for s in spells if s))} 个法术的效果…")
        eff = fetch_effects([s for s in spells if s], args.proxy)
        io.open(EFFECTS, "w", encoding="utf-8").write(json.dumps(eff, ensure_ascii=False))
        print(f"有 {len(eff)} 个法术带评级效果 -> {os.path.relpath(EFFECTS, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
