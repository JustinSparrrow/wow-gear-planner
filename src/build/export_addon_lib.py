"""把装备库导出成插件能读的数据。

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

── 为什么不用 Lua table 字面量 ───────────────────────────
第一版是每个物品一个表构造式：

    [158366] = {"充能沙石指环",59,3,"finger",11,{[7]=7889,[32]=10000},1},

3572 件写出来 462KB。其中真正有信息量的只有 id、名字、装等、两条属性，
剩下全是 `[32]=`、`},`、`{"` 这类语法噪音，以及被重复写了几千遍的
掉落来源字符串（实际只有 99 个唯一值）。

现在改成一行一件的紧凑文本 + 两张去重索引表：

    158366|充能沙石指环|59|3|finger|11|7:7889,32:10000,49:7500|1||12|

模块体积从 462KB 降到约 100KB，加载时 split 一遍即可，耗时在毫秒级。

── 只导本赛季 ────────────────────────────────────────────
默认只导 is_current=1 的（约 1357 件）。旧赛季的装备刷不到了，留在列表里
只会干扰判断；而且它们占全库 62%。要全量加参数 --all。

用法：
    python src/build/export_addon_lib.py          # 只导本赛季
    python src/build/export_addon_lib.py --all    # 全量（约 3570 件）
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

# 读进来要参与计算的 stat_id。
#
# 24/25 是"自选属性"占位槽（制造业、PVP 装备），本身不对应具体属性，
# 但它们的**总量**必须留着——只在后面的循环里摘出来单独存，
# 不能在读取阶段就丢掉，否则这批装备在库里会完全没有绿字。
KEEP_STATS = {3, 4, 5, 7, 24, 25, 32, 36, 40, 49, 71, 72, 73, 74}


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
# 插槽规则（跟 src/wowlib/session.py 里的 GUARANTEED_SOCKETS 一致）
#
# 12.x 的宝石系统：戒指和项链**保底一个棱彩孔**，不管是掉的、造的还是
# 升级出来的；头/腰/腕可以用打孔器开一个（开没开只有玩家知道，所以
# 插件里由玩家自己勾）；别的部位没有孔。
GUARANTEED_SOCKETS = {"finger": 1, "neck": 1}
DRILLABLE_SLOTS = {"head", "waist", "wrist"}

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


def clean(s):
    """字段里不能出现分隔符和换行，否则整行解析错位。"""
    s = (s or "").replace("\n", " ").replace("\r", " ").replace("|", "丨")
    return s.replace(",", "，")


# ── 拼音 ─────────────────────────────────────────────────
# 玩家用中文输入法打字时，WoW 的 EditBox 在"组合未提交"状态下
# `GetText()` 返回的是拼音串（'a\'man\'ni' 这种，音节用撇号分隔），
# 不是汉字——用户看到输入框里是"阿曼尼"，插件收到的却是拼音。
#
# 与其和客户端的行为较劲，不如把拼音也纳入索引：装备名的全拼和首字母
# 都存一份，这样"阿曼尼""amanni""a'man'ni""amn"全都能搜到。
#
# 代价是每件多约 25 字节（1355 件 ≈ 34KB）。这个体积换"怎么打都能搜到"，
# 值。
try:
    from pypinyin import lazy_pinyin
except ImportError:                                     # pragma: no cover
    lazy_pinyin = None


def pinyin_of(text):
    """返回 (全拼, 首字母)。没有 pypinyin 就返回空串，不影响导出。"""
    if not text or lazy_pinyin is None:
        return "", ""
    parts = lazy_pinyin(text)
    full = "".join(ch for p in parts for ch in p.lower()
                   if ch.isascii() and ch.isalnum())
    ini = "".join(p[0].lower() for p in parts
                  if p and p[0].isascii() and p[0].isalnum())
    return full, ini


def load_name_map():
    """英文名 -> 中文名。没有这个文件就退化成全英文，不影响导出。"""
    path = os.path.join(ROOT, "data", "raw", "instances_zhcn.json")
    if not os.path.exists(path):
        print("  提示：没有 instances_zhcn.json，掉落来源保持英文")
        print("        要中文就运行：python src/build/fetch_instances.py")
        return {}
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    m = dict(d.get("pairs") or {})
    m.update(d.get("manual") or {})
    return m


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


class Pool:
    """去重索引表：同一个字符串只存一次，条目里存下标。"""

    def __init__(self):
        self.items = []
        self.index = {}

    def add(self, value):
        if not value:
            return 0
        hit = self.index.get(value)
        if hit is None:
            self.items.append(value)
            hit = len(self.items)          # 1-based，0 表示"没有"
            self.index[value] = hit
        return hit


def main():
    if not os.path.exists(DB):
        print(f"找不到 {DB}")
        return 1

    include_all = "--all" in sys.argv

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
    #
    # 这里顺手把英文翻成中文。玩家对副本的中文名有印象（界面、攻略、集合石
    # 都是中文），对 "Temple of Sethraliss" 没有——而且搜索只匹配来源，
    # 不翻的话中文搜副本一个字都搜不到。
    name_map = load_name_map()
    sources = {}
    for r in conn.execute("""SELECT item_id, instance_name, encounter_name
                             FROM item_sources ORDER BY item_id"""):
        if r["item_id"] not in sources:
            inst = r["instance_name"] or ""
            enc = r["encounter_name"] or ""
            inst_cn = name_map.get(inst, inst)
            enc_cn = name_map.get(enc, enc)
            # "同名的当成没填"这条判断要用英文原文——
            # 翻译后可能碰巧一样，但那不是同一条数据
            if inst and enc and enc != inst:
                sources[r["item_id"]] = f"{inst_cn} · {enc_cn}"
            else:
                sources[r["item_id"]] = inst_cn or enc_cn

    sql = """SELECT id, name, name_cn, quality, slot, raw_ilevel, specs,
                    inventory_type, is_current, socket_info
             FROM items {} ORDER BY id"""
    if not include_all:
        sql = sql.format("WHERE is_current = 1")
    else:
        sql = sql.format("")

    rows, kept = [], set()
    src_pool, spec_pool = Pool(), Pool()
    src_py = {}          # 中文来源 -> 它的拼音，写文件时按 src_pool 顺序取
    opt_count = 0
    skipped = 0

    for r in conn.execute(sql):
        item_id = r["id"]
        slot = SLOT_MAP.get(r["slot"] or "")
        st = stats.get(item_id)
        if not slot or not st:
            skipped += 1
            continue

        name = clean(r["name_cn"] or r["name"] or str(item_id))

        # 自选属性槽（24/25）先摘出来。
        #
        # 这两个 stat_id 是"由玩家选"的占位，本身不对应任何具体属性。
        # 之前直接当未知 id 过滤掉了，后果是**一批当前赛季装备在库里完全没有绿字**
        # （比如 244582 远行者的加固腿铠：主属性+耐力+两个自选槽，
        # 过滤后只剩主属性，兜底算出来比实际弱一大截）。
        opt_total = sum(v for k, v in st.items() if k in (24, 25))
        st = {k: v for k, v in st.items() if k not in (24, 25)}
        if opt_total:
            opt_count += 1

        # 饰品不归一化：它的属性结构本来就不规则（有的给主属性、有的给绿字、
        # 有的什么都没有全靠触发），强行统一会把差异抹平
        if slot != "trinket":
            st = normalize_primary(st)

        # 属性写成 "statID:权重" 逗号分隔，比 [32]=10000 省一半字符
        stat_str = ",".join(f"{k}:{v}" for k, v in sorted(st.items()))

        spec_idx = 0
        if r["specs"]:
            try:
                spec_list = json.loads(r["specs"])
            except (TypeError, ValueError):
                spec_list = []
            if spec_list:
                spec_idx = spec_pool.add(tuple(spec_list))

        src_text = sources.get(item_id, "")
        src_idx = src_pool.add(src_text)
        if src_idx and src_idx not in src_py:
            src_py[src_idx] = pinyin_of(src_text)

        name_py, name_ini = pinyin_of(name)

        # 孔数：库里的真值跟保底规则取最大。少数团本项链自带两孔，
        # socket_info 里会写两条，所以不能只用保底值
        sockets = 0
        if r["socket_info"]:
            try:
                sockets = len((json.loads(r["socket_info"]) or {}).get("sockets") or [])
            except (TypeError, ValueError):
                sockets = 0
        sockets = max(sockets, GUARANTEED_SOCKETS.get(slot, 0))

        # 字段：id|名字|基础装等|品质|槽位|物品类型|属性|本赛季|自选总量|来源|专精限制|全拼|首字母
        rows.append("|".join([
            str(item_id), name, str(r["raw_ilevel"] or 0), str(r["quality"] or 1),
            slot, str(r["inventory_type"] or 0), stat_str,
            "1" if r["is_current"] else "0",
            str(opt_total or ""), str(src_idx), str(spec_idx),
            name_py, name_ini, str(sockets),
        ]))
        kept.add(item_id)

    # ── 写文件 ───────────────────────────────────────────
    out = []
    out.append("--[[")
    out.append("    WoWGear · 装备库（自动生成，不要手改）")
    out.append("")
    out.append("    由 src/build/export_addon_lib.py 从 data/wowgear.db 导出。")
    out.append("    换赛季要重新导：python src/build/export_addon_lib.py")
    out.append("    要导全部赛季（默认只导本赛季）：加参数 --all")
    out.append("")
    out.append("    ── 为什么不是一张 Lua 表 ───────────────────────────")
    out.append("    [158366] = {\"名字\",59,3,\"finger\",11,{[32]=10000},1},")
    out.append("    这种写法看着直观，但 3572 件就是 462KB，其中大半是")
    out.append("    [32]= / }, / { 这类语法噪音，以及被重复几千遍的掉落来源")
    out.append("    （实际只有 99 个唯一值）。改成一行一件的紧凑文本 +")
    out.append("    两张去重索引表后约 100KB，Curve.lua 加载时解析一遍。")
    out.append("")
    out.append("    ── 字段 ───────────────────────────────────────────")
    out.append("    id|名字|基础装等|品质|槽位|物品类型|属性|本赛季|自选总量|来源|专精限制|全拼|首字母|孔数")
    out.append("")
    out.append("    最后两列是名字的拼音（全拼 / 首字母）。存在的理由：中文输入法")
    out.append("    在组合未提交时，EditBox 的 GetText() 给的是拼音串（a'man'ni），")
    out.append("    不是汉字。与其跟客户端较劲，不如让拼音也能搜。")
    out.append("")
    out.append("    属性写成 statID:权重，逗号分隔。**存的是分配权重（万分比），")
    out.append("    不是属性数值**：真实数值 = 权重 × 装等系数 × 1e-4，系数由")
    out.append("    Curve.lua 按装等插值给出。存权重而不存数值，是为了让候选")
    out.append("    装备能按任意装等重算——库里各赛季的基础装等是混的")
    out.append("    （59/108/197/298），各按各的比只会得出「装等低的就是差」。")
    out.append("")
    out.append("    来源和专精限制存的是下面两张索引表的下标，0 表示没有。")
    out.append("    来源已经译成中文（由 src/build/fetch_instances.py 提供对照表），")
    out.append("    因为搜索只匹配装备名和来源，英文名中文玩家根本想不起来。")
    out.append("]]")
    out.append("")
    out.append("local ADDON, NS = ...")
    out.append("")
    out.append(f"-- 共 {len(rows)} 件")
    out.append("NS.LIB_RAW = [==[")
    out.extend(rows)
    out.append("]==]")
    out.append("")

    out.append("-- 掉落来源（去重）：副本名 · boss 名")
    out.append("NS.LIB_SRC_LIST = {")
    for v in src_pool.items:
        out.append(f'  "{v.replace(chr(34), chr(39))}",')
    out.append("}")
    out.append("")
    out.append("-- 来源的拼音，和上面一一对应（下标相同）")
    out.append("NS.LIB_SRC_PY = {")
    for i in range(1, len(src_pool.items) + 1):
        full, ini = src_py.get(i, ("", ""))
        out.append(f'  "{full}/{ini}",')
    out.append("}")
    out.append("")

    out.append("-- 专精限制（去重）：允许吃这件装备的专精 ID 列表")
    out.append("NS.LIB_SPEC_LIST = {")
    for v in spec_pool.items:
        out.append("  {" + ",".join(str(s) for s in v) + "},")
    out.append("}")
    out.append("")

    # ── 宝石与附魔 ──────────────────────────────────────
    # 这两样跟装备属性不同：它们是**固定数值**，不随装等缩放。
    # 一颗 295 的急速宝石永远是 +13 急速，所以直接存数值，不走曲线。
    out.append("--[[")
    out.append("    宝石，由 data/wowgear.db 的 gems 表导出。")
    out.append("    字段：id|名字|品质|物品等级|品种|属性")
    out.append("    属性是**绝对数值**，不随装等缩放——宝石插上就那么多。")
    out.append("]]")
    out.append("NS.GEM_RAW = [==[")
    for r in conn.execute("""SELECT id, name_cn, name, quality, ilvl, kind_cn, stats
                             FROM gems ORDER BY kind_cn, ilvl DESC, id"""):
        try:
            st = json.loads(r["stats"] or "{}")
        except ValueError:
            st = {}
        stat_str = ",".join(f"{k}:{v}" for k, v in sorted(st.items()))
        out.append("|".join([
            str(r["id"]), clean(r["name_cn"] or r["name"] or str(r["id"])),
            str(r["quality"] or 2), str(r["ilvl"] or 0),
            clean(r["kind_cn"] or ""), stat_str,
        ]))
    out.append("]==]")
    out.append("")

    out.append("--[[")
    out.append("    附魔，由 enchants 表导出。字段：id|名字|部位|属性")
    out.append("    名字去掉了「附魔戒指 - 」这类前缀，因为列表已经按部位分好了。")
    out.append("]]")
    out.append("NS.ENCH_RAW = [==[")
    for r in conn.execute("""SELECT id, name_cn, name, slot, stats
                             FROM enchants ORDER BY slot, id"""):
        try:
            st = json.loads(r["stats"] or "{}")
        except ValueError:
            st = {}
        # "main" 是"主属性"占位，落到本职业真吃的那条上（Calc.primary 处理）
        stat_str = ",".join(f"{k}:{v}" for k, v in sorted(st.items()))
        full = r["name_cn"] or r["name"] or str(r["id"])
        for pre in ("附魔戒指 - ", "附魔武器 - ", "附魔头盔 - ", "附魔护肩 - ",
                    "附魔胸甲 - ", "附魔腿部 - ", "附魔靴子 - ", "附魔护腕 - ",
                    "附魔腰带 - ", "附魔手套 - ", "附魔披风 - "):
            if full.startswith(pre):
                full = full[len(pre):]
                break
        out.append("|".join([str(r["id"]), clean(full), r["slot"] or "", stat_str]))
    out.append("]==]")
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
    print(f"  装备 {len(rows)} 件（跳过 {skipped} 件：槽位不认识或没有属性）"
          f"{'，含全部赛季' if include_all else '，仅本赛季'}")
    with_src = sum(1 for r in rows if r.split("|")[-2] != "0")
    print(f"  掉落来源 {with_src} 件，去重后 {len(src_pool.items)} 条")
    print(f"  专精限制去重后 {len(spec_pool.items)} 组")
    print(f"  带自选属性槽 {opt_count} 件")
    out_lines = text.count("\n")
    print(f"  曲线 {len(curve_rows)} 档装等，共 {out_lines} 行")
    print(f"  体积 {size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
