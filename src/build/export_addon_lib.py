"""把装备库导出成插件能读的 Lua 表。

为什么要这一步：插件能算「我有的装备之间谁更好」，但**规划**要的是
「我还没有的装备值不值得去刷」。后者需要一份完整装备库，而客户端里
没有这种东西——`C_Item.GetItemInfo` 只认识玩家接触过的物品。

所以数据从 data/wowgear.db 走：同一个库网页端在用，导成 Lua 塞进插件。

── 装等怎么处理 ──────────────────────────────────────────
库里存的是分配权重（alloc，万分比），不是属性数值。真实数值 = 权重 ×
装等系数 × 1e-4，这个换算在插件里也要做（`Curve.lua` 就是 curve.py 的
Lua 版，同样 25 行采样表）。

但**不按每件装备自己的装等算**，而是统一按角色当前装等算。理由：
库里的 raw_ilevel 是历史基础装等（59/108/197/298 混在一起），跟玩家身上
的 334 不是一个体系，横着比会得出「装等低的装备属性更差」这种废话。
统一装等后，比的就只剩属性配比——而配比才是规划真正要回答的问题。

── 体积 ────────────────────────────────────────────────
3572 件装备，导出后约 400KB 源码。看着不小，但 WoW 插件这个量级很常见
（背包管理类插件动辄几 MB），加载耗时在毫秒级。

用法：
    python src/build/export_addon_lib.py
"""

import io
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "wowgear.db")
CURVE = os.path.join(ROOT, "data", "stat_curve.csv")
OUT = os.path.join(ROOT, "addon", "WoWGear", "Lib.lua")

# db 的槽位名 -> 插件里的槽位族名
SLOT_MAP = {
    "head": "head", "neck": "neck", "shoulder": "shoulder", "back": "back",
    "chest": "chest", "wrist": "wrist", "hands": "hands", "waist": "waist",
    "legs": "legs", "feet": "feet", "finger": "finger", "trinket": "trinket",
    "main_hand": "main_hand", "off_hand": "off_hand",
    # 猎人/战士的远程武器槽，游戏里归主手
    "ranged": "main_hand",
}

# 只保留能算出属性的 stat_id（24/25 自选槽、61 加速 这些不要）
KEEP_STATS = {3, 4, 5, 7, 32, 36, 40, 49, 71, 72, 73, 74}


# 主属性权重的标准值。
#
# 查过全库：所有护甲、所有物理武器的主属性 alloc 都是 5259，恒定不变；
# 部位之间的差异全部由装等系数 K 体现（头胸腿一档、肩手腰脚一档…）。
# 也就是说暴雪的设计里，同部位装备的**总属性完全一样，只有配比不同** ——
# 这就是为什么「按绿字总量排序」永远得到 0，真正要排的是
# 「它给的是不是我要的那条属性」。
#
# 但法系武器不守这个规矩：主手 30629 / 副手 16132 / 单手 23380，
# 像是抓数据时把法术强度也算进了智力（30629 / 5259 = 5.82）。
# 不修的话，法系职业在装备库里会看到智力是物理武器 5.8 倍的锤子。
PRI_STD = 5259
PRI_ABNORMAL = 10000      # 明显超过护甲标准的，按法系武器的单位错误处理


def normalize_primary(alloc):
    """把主属性权重归一到 PRI_STD。饰品不在这里处理——它本来就不规则。"""
    total = sum(v for k, v in alloc.items() if k in (3, 4, 5, 71, 72, 73, 74))
    if total <= PRI_ABNORMAL or total == 0:
        return alloc
    scale = PRI_STD / total
    out = dict(alloc)
    for k in (3, 4, 5, 71, 72, 73, 74):
        if k in out:
            out[k] = max(1, int(round(out[k] * scale)))
    return out


def lua_escape(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def load_curve():
    rows = []
    with io.open(CURVE, encoding="utf-8-sig") as f:
        header = f.readline().strip().split(",")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            try:
                rows.append([int(parts[0])] + [round(float(v), 2) for v in parts[1:]])
            except ValueError:
                continue
    rows.sort(key=lambda r: r[0])
    return header, rows


def main():
    if not os.path.exists(DB):
        print(f"找不到 {DB}")
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    header, curve_rows = load_curve()

    # 属性：{item_id: {stat_id: alloc}}
    stats = {}
    for r in conn.execute("SELECT item_id, stat_id, alloc FROM item_stats"):
        if r["stat_id"] not in KEEP_STATS:
            continue
        stats.setdefault(r["item_id"], {})[r["stat_id"]] = r["alloc"]

    # 来源：优先取副本名 + boss 名，同一个 item 可能有多条（普通/大秘境），取第一条
    sources = {}
    for r in conn.execute("""SELECT item_id, instance_name, encounter_name
                             FROM item_sources ORDER BY item_id"""):
        if r["item_id"] not in sources:
            inst = r["instance_name"] or ""
            enc = r["encounter_name"] or ""
            if inst and enc and enc != inst:
                sources[r["item_id"]] = f"{inst} · {enc}"
            else:
                sources[r["item_id"]] = inst or enc

    items, spec_restrict, kept = [], {}, set()
    skipped = 0
    for r in conn.execute("""SELECT id, name, name_cn, quality, slot, raw_ilevel, specs,
                                    inventory_type, is_current
                             FROM items ORDER BY id"""):
        item_id = r["id"]
        slot = SLOT_MAP.get(r["slot"] or "")
        st = stats.get(item_id)
        if not slot or not st:
            skipped += 1
            continue

        name = r["name_cn"] or r["name"] or str(item_id)
        # 槽位用短码省体积："main_hand" 出现 700 次，省下来是几十 KB
        # inventory_type 单独留着：判断"这把武器是不是双手"要靠它，
        # 槽位名只能区分主手/副手，区分不了一只手还是两只手
        # 饰品不归一化：它的属性结构本来就不规则（有的给主属性、有的给绿字、
        # 有的什么都没有全靠触发），强行统一会把差异抹平
        if slot != "trinket":
            st = normalize_primary(st)

        parts = [f'"{lua_escape(name)}"', str(r["raw_ilevel"] or 0),
                 str(r["quality"] or 1), f'"{slot}"', str(r["inventory_type"] or 0)]
        stat_str = ",".join(f"[{k}]={v}" for k, v in sorted(st.items()))
        parts.append("{" + stat_str + "}")
        # 本赛季能拿到的标 1。库里混着好几个赛季的东西，旧赛季的已经刷不到了，
        # 按统一装等折算后属性看着一样，不标出来玩家会去刷一件根本不存在的东西
        parts.append("1" if r["is_current"] else "0")
        items.append(f"[{item_id}] = {{{','.join(parts)}}},")
        kept.add(item_id)

        if r["specs"]:
            try:
                spec_list = json.loads(r["specs"])
            except (TypeError, ValueError):
                spec_list = []
            if spec_list:
                spec_restrict[item_id] = spec_list

    # ── 写文件 ───────────────────────────────────────────
    out = []
    out.append("--[[")
    out.append("    WoWGear · 装备库（自动生成，不要手改）")
    out.append("")
    out.append("    由 src/build/export_addon_lib.py 从 data/wowgear.db 导出。")
    out.append("    换赛季要重新导：python src/build/export_addon_lib.py")
    out.append("")
    out.append("    格式：[itemID] = { 名字, 基础装等, 品质, 槽位, 物品类型, {[statID]=分配权重}, 是否本赛季 }")
    out.append("")
    out.append("    **存的是分配权重（万分比），不是属性数值**。真实数值 =")
    out.append("    权重 × 装等系数 × 1e-4，系数由 Curve.lua 按装等插值给出。")
    out.append("    存权重而不是存数值，是为了让候选装备能按任意装等重算——")
    out.append("    库里各赛季的基础装等是混的（59/108/197/298），")
    out.append("    直接拿各自身装等比会得出「装等低的就是差」这种废话。")
    out.append("]]")
    out.append("")
    out.append("local ADDON, NS = ...")
    out.append("")
    out.append(f"-- 共 {len(items)} 件")
    out.append("NS.LIB = {")
    out.extend("  " + line for line in items)
    out.append("}")
    out.append("")

    # 掉落来源
    out.append("-- 掉落来源：副本名 · boss 名")
    out.append("NS.LIB_SRC = {")
    src_lines = [f'  [{k}] = "{lua_escape(v)}",'
                 for k, v in sorted(sources.items()) if k in kept]
    out.extend(src_lines)
    out.append("}")
    out.append("")

    # 专精限制：只有少数装备有，单开一张表比每条都带个空字段省
    out.append("-- 专精限制：只有这些装备限定了能吃它的专精，其余全部通用")
    out.append("NS.LIB_SPEC = {")
    for k, v in sorted(spec_restrict.items()):
        out.append(f"  [{k}] = {{{','.join(str(s) for s in v)}}},")
    out.append("}")
    out.append("")

    # 装等系数表
    out.append("--[[")
    out.append("    装等系数表，由 data/stat_curve.csv 导出。")
    out.append("    列顺序：" + ",".join(header))
    out.append("")
    out.append("    这是 curve.py 那张表的 Lua 版：拿真实装等的游戏内数值反算 K，")
    out.append("    采样成表，插值使用。五类部位（big/mid/small/jewel/weapon）")
    out.append("    对应 RandPropPoints 的分档，jewel（颈/戒指/饰品）绿字另有 1.42 倍补偿。")
    out.append("]]")
    out.append("NS.CURVE_COLS = {" + ",".join(f'"{h}"' for h in header) + "}")
    out.append("NS.CURVE_ROWS = {")
    for row in curve_rows:
        cells = [str(row[0])] + [f"{v:g}" for v in row[1:]]
        out.append("  {" + ",".join(cells) + "},")
    out.append("}")

    text = "\n".join(out) + "\n"
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    size = len(text.encode("utf-8"))
    print(f"写出 {OUT}")
    print(f"  装备 {len(items)} 件（跳过 {skipped} 件：槽位不认识或没有属性）")
    print(f"  有掉落来源 {len(src_lines)} 件")
    print(f"  有专精限制 {len(spec_restrict)} 件")
    print(f"  曲线 {len(curve_rows)} 档装等")
    print(f"  体积 {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
