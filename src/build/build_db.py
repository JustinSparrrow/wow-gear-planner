"""魔兽世界装备数据库构建脚本

数据源：Raidbots 静态数据（与 SimulationCraft 同源，从游戏客户端 DB2 导出）
输出：  data/wowgear.db  (SQLite)

三个关键设计决策
----------------
1. 只保留"当前赛季拿得到"的装备。11 万件历史装备里真正能用的只有一千多件，
   靠 seasons.json 的 active 标记 + encounter 交集推导，不硬编码副本 ID，
   补丁更新后重跑即可自动跟上。

   ⚠ 判据不能只看 expansion。暴雪每赛季会把老副本"复刻"进 M+ 池
   （本赛季的 Kings' Rest / Temple of Sethraliss / Ruby Life Pools 就是），
   这些副本掉的还是当年的老装备（expansion 7 或 9），但按本赛季装等掉落。
   只看 expansion 会把这类装备整批漏掉——实测漏了 86 件。
   正确判据是"来源命中本赛季内容池"，expansion 只用于给界面标注复刻本。

2. 属性存 alloc（万分比权重），不存最终数值。
   SimC 的算法是： 属性 = alloc * 预算(装等, 部位, 品质) * 0.0001 * 评级倍率
   同一部位同品质的 alloc 总和恒定（护甲头 20148、武器 25389），所以 alloc
   本质是"属性分配比例"。装等一变，属性整体缩放，比例不变。
   这样一件装备只需存一行 alloc，就能算出它在任意装等下的属性。

3. 装等不挂在装备上，挂在"升级轨道"上。
   客户端原始 ilevel 字段（如 219）不是玩家看到的装等，那是未加成的裸值。
   实际装等由纹章升级轨道决定：冒险者 266-282 / 老兵 279-295 /
   勇士 292-308 / 英雄 305-321 / 神话 318-334。
   因此单独建 ilevel_tracks 表，前端让用户选"按哪个轨道算"即可。
"""

import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")
DB_PATH = os.path.join(ROOT, "data", "wowgear.db")

CURRENT_EXPANSION = 11  # Midnight
CUSTOM_ID_BASE = 9000000  # 自定义装备 ID 从这开始，避免与官方装备冲突

# item_mod_type -> (代码, 中文, 类别)。来自 SimC engine/dbc/data_enums.hh
STAT_MAP = {
    3: ("agi", "敏捷", "primary"),
    4: ("str", "力量", "primary"),
    5: ("int", "智力", "primary"),
    7: ("sta", "耐力", "primary"),
    24: ("bonus1", "自选属性1", "crafted"),
    25: ("bonus2", "自选属性2", "crafted"),
    32: ("crit", "暴击", "secondary"),
    36: ("haste", "急速", "secondary"),
    40: ("vers", "全能", "secondary"),
    49: ("mastery", "精通", "secondary"),
    61: ("speed", "加速", "tertiary"),
    62: ("leech", "吸血", "tertiary"),
    63: ("avoidance", "闪避", "tertiary"),
    71: ("str_agi_int", "力量/敏捷/智力", "primary"),
    72: ("str_agi", "力量/敏捷", "primary"),
    73: ("agi_int", "敏捷/智力", "primary"),
    74: ("str_int", "力量/智力", "primary"),
}

SLOT_MAP = {
    1: "head", 2: "neck", 3: "shoulder", 4: "shirt", 5: "chest",
    6: "waist", 7: "legs", 8: "feet", 9: "wrist", 10: "hands",
    11: "finger", 12: "trinket", 13: "main_hand", 14: "off_hand",
    15: "ranged", 16: "back", 17: "main_hand", 20: "chest",
    21: "main_hand", 22: "off_hand", 23: "off_hand", 25: "ranged", 26: "ranged",
}

# 一个类别越靠前，越优先作为装备的主来源展示
CATEGORY_PRIORITY = [
    "raid", "dungeon", "delve", "prey", "profession",
    "catalyst", "pvp-conquest", "pvp-honor", "pvp-world", "world", "unknown",
]

QUALITY_CN = {0: "粗糙", 1: "普通", 2: "优秀", 3: "精良", 4: "史诗", 5: "传说", 6: "神器"}

# 护甲子类 -> 可穿的护甲类型。戒指/项链/饰品/披风的子类为 0，不限护甲类型。
ARMOR_TYPE = {1: "cloth", 2: "leather", 3: "mail", 4: "plate"}

# 主属性 stat_id -> 该装备的主属性标签
PRIMARY_STAT = {
    3: "agi", 4: "str", 5: "int",
    71: "any", 72: "str_agi", 73: "agi_int", 74: "str_int",
}


def load(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return json.load(f)


def _season_of_instance(inst):
    """从伪实例的 type / name 反推它属于哪个赛季。

    type 形如 delve-mid2 / prey-mid2；raid、catalyst、profession 要靠 name 判断。
    """
    t = inst.get("type") or ""
    if t.startswith("profession"):
        # 制造业伪实例不带赛季号，随资料片更新，归入当前赛季
        return "@current"
    m = re.search(r"-mid(\d+)$", t)
    if m:
        return f"mid{m.group(1)}"
    if t in ("raid", "catalyst") or t.startswith("pvp-"):
        m = re.search(r"Season (\d+)", inst.get("name") or "")
        if m:
            return f"mid{m.group(1)}"
    return None


def build_content_pool(instances, seasons, currency):
    """推导每个赛季的内容池：哪些副本 / BOSS / 伪实例属于它。"""
    imap = {i["id"]: i for i in instances}
    active = next((s for s in seasons if s.get("active")), None)
    if not active:
        raise SystemExit("找不到 active 赛季，数据可能过期")
    cur_short = active["shortName"]

    mid_seasons = [s for s in seasons if (s.get("shortName") or "").startswith("mid")]
    prev_season = next((s for s in mid_seasons if s["shortName"] != cur_short), None)

    def make_pool(short, is_current=False):
        pseudo, pseudo_cat, raid_enc = {}, {}, set()
        for iid, i in imap.items():
            if iid > 0:
                continue
            tag = _season_of_instance(i)
            if tag is None:
                continue
            if tag == "@current" and not is_current:
                continue
            if tag != "@current" and tag != short:
                continue
            t = i.get("type") or ""
            if t.startswith("delve"):
                cat = "delve"
            elif t.startswith("prey"):
                cat = "prey"
            elif t == "raid":
                cat = "raid"
            elif t == "catalyst":
                cat = "catalyst"
            elif t.startswith("profession"):
                cat = "profession"
            elif t.startswith("pvp-"):
                cat = t
            else:
                continue
            pseudo[cat] = iid
            pseudo_cat[iid] = cat
            if cat == "raid":
                raid_enc = {e["id"] for e in i.get("encounters", []) if e["id"] > 0}

        raid_instances = {
            iid for iid, i in imap.items()
            if iid > 0 and i.get("type") == "raid"
            and {e["id"] for e in i.get("encounters", [])} & raid_enc
        }
        return {
            "season": short, "pseudo": pseudo, "pseudo_category": pseudo_cat,
            "raid_encounters": raid_enc, "raid_instances": raid_instances,
            "mplus_instances": set(),
        }

    pools = {"current": make_pool(cur_short, True)}
    pools["prev"] = make_pool(prev_season["shortName"]) if prev_season else None

    if -1 in imap:
        pools["current"]["mplus_instances"] = {e["id"] for e in imap[-1]["encounters"]}
    pools["current"]["season_meta"] = active
    pools["current"]["crest_names"] = [
        (currency.get(str(cid)) or {}).get("name", f"纹章{cid}")
        for cid in active.get("crestCurrencies", [])
    ]
    return pools, imap, active


def source_kind(src, pool, is_current):
    """判断单条来源属于什么内容类别；不属于该赛季则返回 None。"""
    iid, eid = src.get("instanceId"), src.get("encounterId")
    if iid in pool["pseudo_category"]:
        return pool["pseudo_category"][iid]
    if iid == -1:
        # M+ 容器：encounterId 才是真正的副本 id
        return "dungeon" if eid in pool["mplus_instances"] else None
    if iid == -32:
        return "dungeon_normal" if is_current else None
    if iid and iid > 0:
        if iid in pool["raid_instances"]:
            return "raid"
        if iid in pool["mplus_instances"]:
            return "dungeon"
    return None


def classify(item, pools):
    """返回 (赛季, 主类别, 命中列表)。主类别按优先级挑选，命中列表保留全部来源。"""
    sources = item.get("sources") or []
    hits = []
    for tag, is_cur in (("current", True), ("prev", False)):
        pool = pools.get(tag)
        if not pool:
            continue
        for s in sources:
            cat = source_kind(s, pool, is_cur)
            if cat:
                hits.append((tag, cat, s))
    if not hits:
        return "other", "unknown", []

    def rank(h):
        cat = h[1]
        idx = CATEGORY_PRIORITY.index(cat) if cat in CATEGORY_PRIORITY else 99
        return (0 if h[0] == "current" else 1, idx)

    best = sorted(hits, key=rank)[0]
    return pools[best[0]]["season"], best[1], hits


def parse_icons():
    """解析图标索引：name -> FileDataID。

    优先用 icon-index.csv（从社区 listfile 提取，覆盖全部新素材）；
    退回到 Raidbots 的 icon-paths.txt（只含旧版本图标）。
    """
    icons = {}
    idx = os.path.join(RAW, "icon-index.csv")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or "," not in line:
                    continue
                fid, name = line.split(",", 1)
                icons[name.lower()] = int(fid)
        return icons

    path = os.path.join(RAW, "icon-paths.txt")
    if not os.path.exists(path):
        return icons
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or "," not in line:
                continue
            fid, p = line.split(",", 1)
            name = p.replace("\\", "/").split("/")[-1]
            if name.lower().endswith(".blp"):
                name = name[:-4]
            icons[name.lower()] = int(fid)
    return icons


def _load_json(name):
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        print(f"  ⚠ 缺少 data/raw/{name}，宝石/附魔不会入库。"
              f"用 src/build/fetch_gems_enchants.py 生成")
        return {}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def load_names():
    """中文名。装备 / 宝石 / 附魔三种，来自 ItemSparse 的 zhCN 导出。

    缺这个文件不该让建库失败：界面会退回英文名，功能一切正常。
    只是候选列表里全是英文，中文玩家对不上号。
    """
    path = os.path.join(RAW, "names_zhcn.json")
    if not os.path.exists(path):
        print("  ⚠ 缺少 data/raw/names_zhcn.json，界面上的装备/宝石/附魔"
              "会显示英文名。用 src/build/fetch_names.py 生成")
        return {"items": {}, "gems": {}, "enchants": {}}
    with io.open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {"items": raw.get("items") or {},
            "gems": raw.get("gems") or {},
            "enchants": raw.get("enchants") or {}}


# 宝石的"品种"（底座）决定它主要给哪条属性，前缀（Quick/Deadly/...）决定
# 另一半配合的属性。数据验证：所有 Peridot 都以急速为主，Amethyst 精通，
# Garnet 暴击，Lapis 全能——和名字对得上，所以直接按名字后缀分类。
#
# 中文名一律用**客户端里的写法**（ItemSparse zhCN 抽出来的）：榄石/紫晶/榴石/
# 青金石/永歌钻石/鸡血石。以前这里按字面译成"翠榴石/紫水晶/石榴石/永恒钻石/
# 日长石"，游戏里根本不这么叫，玩家按品种找宝石时对不上。
GEM_FAMILY = (
    ("peridot", "haste", "榄石"),
    ("amethyst", "mastery", "紫晶"),
    ("garnet", "crit", "榴石"),
    ("lapis", "vers", "青金石"),
    ("diamond", "main", "永歌钻石"),
    ("heliotrope", "main", "鸡血石"),
)

# 品质：0 劣质 / 1 普通 / 2 优秀 / 3 精良 / 4 史诗。
# 宝石图标名里带 uncommon/rare/epic，比名字可靠（"Flawless" 只出现在精良那档，
# 但日长石那批是 inv_misc_epicgem_01 这种老图标名，得靠 epic 关键字判断）。
QUALITY_KEY = (("epic", 4), ("rare", 3), ("uncommon", 2))


def gem_kind(name, icon, stats):
    """宝石品种：(代码, 中文, 主要属性)。"""
    low = (name or "").lower()
    for key, stat, cn in GEM_FAMILY:
        if key in low:
            return key, cn, stat
    return "other", "其它", ""


def gem_quality(name, icon):
    low = (icon or "").lower()
    for key, q in QUALITY_KEY:
        if key in low:
            return q
    # 图标名不带品质的（日长石那批是 inv_misc_epicgem_*），按名字兜底
    if "flawless" in (name or "").lower():
        return 3
    if "epicgem" in low:
        return 4
    return 2


def gem_unique_group(name, kind):
    """这颗宝石有没有"每人限一颗"的限制；有就返回分组名。

    永歌钻石（Eversong Diamond）这类是**唯一装备**：同一颗只能装一颗，
    不同装等版本（278 / 295）算同一颗。不拦的话，界面上可以给项链两个孔
    都插同一个钻石，导出的 simc 在游戏里根本穿不上。

    判据用**去掉品质前缀的英文名**做分组键：`Indecipherable Eversong Diamond`
    在 278 和 295 两版上名字相同，天然归成一组；不同前缀（费解/强能/坚韧…）
    是不同的宝石，各限一颗。
    """
    if kind != "diamond":
        return None
    base = re.sub(r"^(Flawless|完美|无瑕)\s*", "", (name or "")).strip()
    return base or None


def load_gem_rows(names=None):
    """宝石 -> (id, 名字, 中文名, 图标, 属性 JSON, 品种, 品种中文, 品质, 装等)。

    同一颗宝石有两个装等版本（实测 278 / 295，数值差 1~2 点），名字、品质、
    图标一模一样，界面上原本是两张分不清的卡。把 ilvl 带进库里，
    卡片上就能写清"这是 278 那版还是 295 那版"。
    """
    meta = (names or {}).get("gems") or {}
    out = []
    for gid, v in _load_json("gems.json").items():
        stats = v.get("stats") or {}
        kind, kind_cn, _ = gem_kind(v.get("name"), v.get("icon"), stats)
        cn = meta.get(str(gid)) or {}
        out.append((int(gid), v.get("name"), cn.get("name"), v.get("icon"),
                    json.dumps(stats, ensure_ascii=False, sort_keys=True),
                    kind, kind_cn, gem_quality(v.get("name"), v.get("icon")),
                    cn.get("ilvl"), gem_unique_group(v.get("name"), kind)))
    return out


def load_ench_rows(names=None):
    """附魔 -> (id, 名字, 中文名, 槽位, 卷轴 id, 属性, 第三属性, 特效说明, 描述, 图标)。

    中文名优先取 `enchants.json` 里自带的 name_cn（腿部护甲片/魔线这类不是
    "Enchant XXX" 卷轴，从 ItemSparse 按名字对不上），其次才按卷轴 id 查。
    """
    scroll_cn = (names or {}).get("enchants") or {}
    out = []
    for eid, v in _load_json("enchants.json").items():
        item_id = v.get("item_id")
        cn = v.get("name_cn") or (scroll_cn.get(str(item_id)) if item_id else None)
        out.append((int(eid), v.get("name"), cn,
                    v.get("slot"), item_id,
                    json.dumps(v.get("stats") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(v.get("tertiary") or {}, ensure_ascii=False, sort_keys=True),
                    v.get("note") or "", v.get("desc") or "",
                    v.get("icon") or ""))
    return out


def load_rating_curves():
    """90 级绿字换算 + 递减档位 + 每专精精通系数。

    数据源：Maxroll《Stat Diminishing Returns》12.0.1 与客户端法术表。
    这两份是**公式数据**（不是某个人物面板反推），换版本要整体更新。
    """
    out = {}
    path = os.path.join(RAW, "rating_curves.json")
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            out["curves"] = json.load(f)
    else:
        print("  ⚠ 缺少 data/raw/rating_curves.json，百分比只能按线性近似")
    path = os.path.join(RAW, "spec_mastery.json")
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            out["mastery"] = json.load(f).get("levels") or {}
    return out


def load_spec_stats():
    """每专精「装备之外」的固定绿字百分比。

    和 rating_curves 不同，这批是**角色观测值**（从真实游戏面板反推），
    不是客户端公式数据。所以换赛季一定要重采 —— 天赋改版会直接让这些数失效。
    文件里记了 build，重建库时比对一下，不一致就提醒。
    """
    path = os.path.join(RAW, "spec_stats.json")
    if not os.path.exists(path):
        print("  ⚠ 缺少 data/raw/spec_stats.json，没填面板的专精只能显示装备那部分")
        return {}
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    want = data.get("build")
    now = (load("meta.json") or {}).get("wowBuild")
    if want and now and want != now:
        print(f"  ⚠ spec_stats.json 是 {want} 的，当前 {now}，"
              f"天赋可能改过，需要重新用面板反推基线")
    return data.get("specs") or {}


def load_class_base():
    """裸装基础属性（职业 + 种族）。缺文件不挡建库，只是没填面板时只能看装备本体。

    返回 (classes, races, scale)。`scale` 是校准系数：客户端 gt 表是压缩前的
    标尺（SimC 的 hp_per_sta(90)=75，而游戏面板 生命值/耐力 = 22），
    按真实 90 级角色面板反推要 ×1.5，见 fetch_class_base.py 里的说明。
    只在职业那部分乘，种族那点 ±3 是原始增量，不参与缩放。
    """
    path = os.path.join(RAW, "class_base.json")
    if not os.path.exists(path):
        print("  ⚠ 缺少 data/raw/class_base.json：没填面板时只能算装备本体。"
              "用 src/build/fetch_class_base.py 生成")
        return {}, {}, {"main": 1.0, "sta": 1.0}
    with io.open(path, encoding="utf-8") as f:
        raw = json.load(f)
    scale = (raw.get("_meta") or {}).get("scale") or 1.0
    if isinstance(scale, dict):
        # 主属性和耐力的缩放不一样：压缩前那张表里耐力本来就接近新标尺，
        # 主属性才差 1.43 倍。用同一个系数会让耐力偏高 40%。
        scale = {"main": float(scale.get("main") or 1.0),
                 "sta": float(scale.get("sta") or 1.0)}
    else:
        scale = {"main": float(scale), "sta": float(scale)}
    return raw.get("classes") or {}, raw.get("races") or {}, scale


def load_sockets():
    """装备孔位。{item_id: 孔数}。

    Blizzard 的 hasSockets/socketInfo 只覆盖一部分物品，12.x 新戒指项链
    大多没这两个字段，照它判断会把"其实有孔"当成"没孔"。所以用
    fetch_sockets.py 从物品说明抓的真值优先，API 只在没抓到的时候兜底。
    """
    out = {}
    path = os.path.join(RAW, "sockets.json")
    if not os.path.exists(path):
        print("  ⚠ 缺少 data/raw/sockets.json，插槽按 API 数据判断（可能不准）。"
              "用 src/build/fetch_sockets.py 生成")
        return out
    with io.open(path, encoding="utf-8") as f:
        for k, v in json.load(f).items():
            out[int(k)] = int(v.get("count") or 0)
    return out


def build_tracks(active, upgrade_sets, crest_names):
    """从赛季的 bonusListGroups + 纹章名，生成装等阶梯表。"""
    groups = active.get("bonusListGroups") or []
    rows = []
    for gi, gid in enumerate(groups):
        steps = upgrade_sets.get(str(gid)) or []
        if not steps:
            continue
        label = crest_names[gi].replace(" Mistcrest", "") if gi < len(crest_names) else f"第{gi+1}阶"
        for st in steps:
            rows.append((gid, gi, label, st.get("level"), st.get("max"),
                         st.get("bonusId"), st.get("itemLevel")))
    return rows


def main():
    print("载入数据 …")
    items = load("equippable-items.json")
    instances = load("instances.json")
    seasons = load("seasons.json")
    item_sets = load("item-sets.json")
    meta = load("meta.json")
    upgrade_sets = load("bonus-upgrade-sets.json")
    currency = load("currency-types.json")
    sockets = load_sockets()
    names = load_names()
    class_base, race_base, base_scale = load_class_base()

    pools, imap, active = build_content_pool(instances, seasons, currency)
    cur = pools["current"]
    print(f"当前赛季：{active['name']} ({active['shortName']})")
    print(f"  团本实例：{sorted(cur['raid_instances'])}")
    print(f"  M+ 副本数：{len(cur['mplus_instances'])}")
    print(f"  团本 BOSS 数：{len(cur['raid_encounters'])}")
    print(f"  纹章轨道：{cur['crest_names']}")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE stat_types(id INTEGER PRIMARY KEY, code TEXT, label_cn TEXT, kind TEXT);
    -- 裸装基础属性（职业 + 种族），由 fetch_class_base.py 从客户端 gt 表抽出来。
    -- 面板校准没填时用它当基准；填了就以面板为准（那可是含天赋的真实值）。
    CREATE TABLE class_base(
      class TEXT, level INTEGER,
      str INTEGER, agi INTEGER, sta INTEGER, int INTEGER, spi INTEGER,
      PRIMARY KEY(class, level)
    );
    CREATE TABLE race_base(
      race TEXT PRIMARY KEY,
      str INTEGER, agi INTEGER, sta INTEGER, int INTEGER, spi INTEGER
    );
    -- 每专精的精通系数：每 1 点精通给多少 %。
    -- 客户端 sc_spell_data.inc 精通法术 effectN(1) 的 sp_coeff，猎人的两条已用真实面板验证。
    CREATE TABLE spec_mastery(
      spec TEXT PRIMARY KEY, spell INTEGER, coeff REAL
    );
    -- 每专精「装备之外」的固定绿字百分比（天赋 + 职业/专精被动）。
    --
    -- 这一层原来靠在 session 里从玩家面板反推，等于每个角色都得手工标定一次，
    -- 换个专精就没了。存成表之后没填面板也能算准。
    --
    -- 精通那列已经乘过 spec_mastery.coeff，四列同单位（都是面板上直接看到的 %），
    -- 所以公式对四种属性完全同构：pct = 递减后评级 / 分母 + 基线。
    --
    -- confidence 是给后来人看的：high 有两套装备交叉验证，low 是单人单次采样。
    CREATE TABLE spec_stats(
      spec TEXT PRIMARY KEY,      -- hunter:survival
      crit REAL, haste REAL, mastery REAL, vers REAL,
      source TEXT,                -- panel（面板反推）/ simc / manual
      confidence TEXT,            -- high / medium / low
      note TEXT
    );
    CREATE TABLE items(
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      name_cn TEXT,
      icon TEXT,
      quality INTEGER,
      item_class INTEGER,
      item_sub_class INTEGER,
      inventory_type INTEGER,
      slot TEXT,
      armor_type TEXT,
      primary_stat TEXT,
      raw_ilevel INTEGER,
      expansion INTEGER,
      season TEXT,
      category TEXT,
      is_current INTEGER,
      item_set_id INTEGER,
      has_sockets INTEGER,
      socket_info TEXT,
      specs TEXT,
      classes TEXT,
      -- 属性待指定的两种模式，NULL 表示属性固定：
      --   'crafted' 制造业，两个自选槽平分（如 3500/3500）
      --   'boe'     团本小怪掉落的装绑，两个自选槽一大一小（如 4868/2132）
      craft_kind TEXT,
      is_custom INTEGER DEFAULT 0,
      custom_note TEXT,
      created_at TEXT
    );
    CREATE TABLE item_stats(
      item_id INTEGER NOT NULL,
      stat_id INTEGER NOT NULL,
      alloc INTEGER NOT NULL,
      is_crafted_optional INTEGER DEFAULT 0,
      PRIMARY KEY(item_id, stat_id)
    );
    CREATE TABLE item_sources(
      item_id INTEGER NOT NULL,
      instance_id INTEGER,
      encounter_id INTEGER,
      instance_name TEXT,
      encounter_name TEXT,
      category TEXT,
      season TEXT
    );
    CREATE TABLE item_set_bonus(
      set_id INTEGER, set_name TEXT, spell_id INTEGER, req_items INTEGER, spec_id INTEGER
    );
    CREATE TABLE content_pool(
      instance_id INTEGER, category TEXT, season TEXT, instance_name TEXT
    );
    CREATE TABLE ilevel_tracks(
      group_id INTEGER, track_index INTEGER, track_name TEXT,
      level INTEGER, max_level INTEGER, bonus_id INTEGER, ilevel INTEGER,
      PRIMARY KEY(group_id, level)
    );
    CREATE TABLE icons(name TEXT PRIMARY KEY, file_data_id INTEGER);
    -- 宝石和附魔。装备本体之外还有这两块能加属性，不算进来面板永远差一截：
    -- 样例角色光四颗宝石就少 21 点精通，脚附魔还带 186 耐力。
    -- 数值存 JSON（{"crit":16,"mastery":7}），因为一件宝石/附魔可能同时给几项。
    CREATE TABLE gems(
      id INTEGER PRIMARY KEY,
      name TEXT,
      name_cn TEXT,             -- 中文名（ItemSparse zhCN），没有就是 NULL，界面退回英文
      icon TEXT,
      stats TEXT NOT NULL,
      -- 品种（peridot/amethyst/garnet/lapis/diamond/heliotrope）决定主属性，
      -- 前缀（Quick/Deadly/Masterful/Versatile）决定配合的副属性。
      -- 界面上按品种分组，玩家找"急速宝石"时一眼能筛。
      kind TEXT,
      kind_cn TEXT,
      quality INTEGER,          -- 2 优秀 / 3 精良（Flawless）/ 4 史诗
      -- 宝石也有装等，而且同一颗宝石有两个装等版本（278 / 295），
      -- 名字和图标完全一样、数值差 1~2 点。不带这一列，界面上那两张卡分不清。
      ilvl INTEGER,
      -- 唯一宝石的分组：同一组里只允许装一颗（永歌钻石这类）。
      -- NULL 表示没有限制。
      unique_group TEXT
    );
    CREATE TABLE enchants(
      id INTEGER PRIMARY KEY,
      name TEXT,
      name_cn TEXT,             -- 中文名，取自附魔卷轴（"附魔戒指 - 阿曼尼精通"）
      slot TEXT,
      item_id INTEGER,          -- 对应的附魔卷轴，便于回查
      stats TEXT,               -- 主属性 / 耐力 / 绿字
      tertiary TEXT,            -- 第三属性（吸血 / 闪避 / 移动速度），不进六属性面板
      note TEXT,                -- 特效说明，如"暴击效果 +1%"或"触发：急速 +99，持续 15 秒"
      desc TEXT,                -- 卡片副行的一句话（触发类、未收录类的解释）
      icon TEXT                 -- 图标名（与装备图标同一套命名），没抓到就是空
    );
    CREATE INDEX idx_items_current ON items(is_current, slot);
    CREATE INDEX idx_items_cat ON items(category);
    CREATE INDEX idx_items_name ON items(name);
    CREATE INDEX idx_stats_item ON item_stats(item_id);
    CREATE INDEX idx_src_item ON item_sources(item_id);

    CREATE VIEW v_items AS
    SELECT i.*,
           (SELECT GROUP_CONCAT(t.label_cn || ':' || s.alloc, ' ')
              FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
             WHERE s.item_id = i.id AND s.is_crafted_optional = 0) AS stats_text
      FROM items i;
    """)

    db.executemany("INSERT INTO stat_types VALUES(?,?,?,?)",
                   [(k, v[0], v[1], v[2]) for k, v in STAT_MAP.items()])

    # 内容池
    pool_rows = []
    for tag in ("current", "prev"):
        p = pools.get(tag)
        if not p:
            continue
        for iid in sorted(p["raid_instances"]):
            pool_rows.append((iid, "raid", p["season"], imap[iid]["name"]))
        for iid in sorted(p["mplus_instances"]):
            pool_rows.append((iid, "dungeon", p["season"], imap[iid]["name"]))
        for iid, cat in sorted(p["pseudo_category"].items()):
            pool_rows.append((iid, cat, p["season"], imap[iid]["name"]))
    db.executemany("INSERT INTO content_pool VALUES(?,?,?,?)", pool_rows)

    # 装等阶梯
    track_rows = build_tracks(active, upgrade_sets, cur["crest_names"])
    db.executemany("INSERT OR REPLACE INTO ilevel_tracks VALUES(?,?,?,?,?,?,?)", track_rows)

    # 装备
    item_rows, stat_rows, src_rows = [], [], []
    season_counter, cat_counter = Counter(), Counter()

    for it in items:
        stats = it.get("stats") or []
        if not stats:
            continue  # 无属性装备（纯外观/背包），配装用不到

        season, cat, hits = classify(it, pools)

        # 复刻副本的装备是旧资料片的（expansion 7/9），但副本被排进本赛季
        # M+ 池，按本赛季装等掉落，必须收。判据是 classify 的赛季归属，
        # 不是 expansion。两个条件都不满足才是真·历史垃圾。
        if it.get("expansion") != CURRENT_EXPANSION and season != cur["season"]:
            continue
        alloc = {}
        for s in stats:
            alloc[s["id"]] = alloc.get(s["id"], 0) + s["alloc"]

        # 24/25 是"属性由玩家选"的自选槽，具体是哪两个属性不入库——
        # 掉落的装绑和造的制造业每次都可能不同，必须由用户指定。
        # 两者的区别只在分配比例：制造业平分，装绑一大一小。
        craft_kind = None
        if 24 in alloc or 25 in alloc:
            craft_kind = "crafted" if it.get("profession") else "boe"

        # 孔位：抓到的真值优先。API 的 hasSockets 只在少数物品上有，
        # 没有这个字段的戒指项链不一定是"没孔"，而是"暴雪没写"。
        socket_count = sockets.get(it["id"])
        if socket_count is None:
            socket_count = 1 if it.get("hasSockets") else 0
            sock_info = (json.dumps(it.get("socketInfo"), ensure_ascii=False)
                         if it.get("socketInfo") else None)
        else:
            # 现在只有棱彩孔一种；将来出现多色孔时这里要按 sockets.json 的类型出
            sock_info = (json.dumps({"sockets": [{"type": "PRISMATIC"}] * socket_count})
                         if socket_count else None)

        item_rows.append((
            it["id"], it["name"], (names["items"].get(str(it["id"])) or None),
            it.get("icon"), it.get("quality"),
            it.get("itemClass"), it.get("itemSubClass"), it.get("inventoryType"),
            SLOT_MAP.get(it.get("inventoryType")),
            ARMOR_TYPE.get(it.get("itemSubClass")) if it.get("itemClass") == 4 else None,
            next((PRIMARY_STAT[sid] for sid in alloc if sid in PRIMARY_STAT), None),
            it.get("itemLevel"),
            it.get("expansion"), season, cat, 1 if season == cur["season"] else 0,
            it.get("itemSetId"), socket_count,
            sock_info,
            json.dumps(it.get("specs"), ensure_ascii=False) if it.get("specs") else None,
            json.dumps(it.get("allowableClasses"), ensure_ascii=False) if it.get("allowableClasses") else None,
            craft_kind,
            0, None, None,
        ))
        for sid, amt in alloc.items():
            stat_rows.append((it["id"], sid, amt, 1 if sid in (24, 25) else 0))
        for tag, c, s in hits:
            iid, eid = s.get("instanceId"), s.get("encounterId")
            inst_name = imap[iid]["name"] if iid in imap else None
            enc_name = None
            if iid in imap:
                for e in imap[iid].get("encounters", []):
                    if e["id"] == eid:
                        enc_name = e["name"]
                        break
            if enc_name is None and iid == -1 and eid in imap:
                inst_name = imap[eid]["name"]
                enc_name = "史诗钥石"
            src_rows.append((it["id"], iid, eid, inst_name, enc_name, c, tag and pools[tag]["season"]))
        season_counter[season] += 1
        cat_counter[(season, cat)] += 1

    # 占位符按实际元组长度生成，加字段时不用再回来数
    db.executemany("INSERT INTO items VALUES(" + ",".join("?" * len(item_rows[0])) + ")", item_rows)
    db.executemany("INSERT INTO item_stats VALUES(?,?,?,?)", stat_rows)
    db.executemany("INSERT INTO item_sources VALUES(?,?,?,?,?,?,?)", src_rows)

    for s in item_sets:
        for sp in s.get("spells") or []:
            db.execute("INSERT INTO item_set_bonus VALUES(?,?,?,?,?)",
                       (s["id"], s["name"], sp.get("spellId"), sp.get("reqItems"), sp.get("specId")))

    icons = parse_icons()
    db.executemany("INSERT OR IGNORE INTO icons VALUES(?,?)", list(icons.items()))

    gem_rows, ench_rows = load_gem_rows(names), load_ench_rows(names)
    db.executemany("INSERT OR REPLACE INTO gems VALUES("
                   + ",".join("?" * len(gem_rows[0])) + ")", gem_rows)
    db.executemany("INSERT OR REPLACE INTO enchants VALUES("
                   + ",".join("?" * len(ench_rows[0])) + ")", ench_rows)

    cb_rows = [(cls, int(lv),
                round(v["str"] * base_scale["main"]),
                round(v["agi"] * base_scale["main"]),
                round(v["sta"] * base_scale["sta"]),
                round(v["int"] * base_scale["main"]),
                round(v["spi"] * base_scale["main"]))
               for cls, levels in class_base.items()
               for lv, v in levels.items()]
    db.executemany("INSERT OR REPLACE INTO class_base VALUES(?,?,?,?,?,?,?)", cb_rows)
    rb_rows = [(race, v["str"], v["agi"], v["sta"], v["int"], v["spi"])
               for race, v in race_base.items()]
    db.executemany("INSERT OR REPLACE INTO race_base VALUES(?,?,?,?,?,?)", rb_rows)

    curves = load_rating_curves()
    mastery_rows = [(k, v.get("spell"), v.get("coeff"))
                    for k, v in (curves.get("mastery") or {}).items()]
    db.executemany("INSERT OR REPLACE INTO spec_mastery VALUES(?,?,?)", mastery_rows)
    if curves.get("curves"):
        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)",
                   ("rating_curves", json.dumps(curves["curves"], ensure_ascii=False)))
    print(f"精通系数 {len(mastery_rows)} 个专精"
          + ("，绿字曲线已入库" if curves.get("curves") else ""))

    stats = load_spec_stats()
    stats_rows = [(k, v.get("crit"), v.get("haste"), v.get("mastery"), v.get("vers"),
                   v.get("source"), v.get("confidence"), v.get("note"))
                  for k, v in stats.items()]
    db.executemany("INSERT OR REPLACE INTO spec_stats VALUES(?,?,?,?,?,?,?,?)", stats_rows)
    if stats_rows:
        have = sum(1 for r in stats_rows if r[5] == "panel")
        print(f"专精基线 {len(stats_rows)} 个（其中 {have} 个来自真实面板反推），"
              f"其余专精没填面板时只能显示装备部分")

    db.executemany("INSERT INTO meta VALUES(?,?)", [
        ("wow_build", meta.get("wowBuild")),
        ("generated_at", meta.get("generatedAt")),
        ("season", active["name"]),
        ("season_short", active["shortName"]),
        ("built_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("source", "raidbots static data"),
    ])
    db.commit()

    # ---------- 统计 ----------
    print(f"\n入库装备 {len(item_rows)} 件（Midnight 全部有属性装备）")
    print("赛季分布：", dict(season_counter))
    print("\n当前赛季按类别：")
    for (s, c), n in sorted(cat_counter.items(), key=lambda x: -x[1]):
        if s == cur["season"]:
            print(f"  {c:16} {n:5} 件")
    print(f"\n当前赛季合计：{db.execute('SELECT COUNT(*) FROM items WHERE is_current=1').fetchone()[0]} 件")
    print(f"来源记录：{len(src_rows)} 条（一件装备可有多条）")
    print(f"图标库：{len(icons)} 个")
    print(f"宝石：{len(gem_rows)} 颗   附魔：{len(ench_rows)} 条")
    sock_cur = db.execute(
        "SELECT COUNT(*) FROM items WHERE is_current=1 AND has_sockets=1").fetchone()[0]
    print(f"当前赛季能插宝石：{sock_cur} 件"
          f"（孔位来自 sockets.json {len(sockets)} 条）")
    print("\n装等阶梯：")
    for r in track_rows:
        print(f"  {r[2]:8} {r[3]}/{r[4]} 档  ilvl {r[6]}")

    db.close()
    print(f"\n数据库已生成：{DB_PATH}")


if __name__ == "__main__":
    main()
