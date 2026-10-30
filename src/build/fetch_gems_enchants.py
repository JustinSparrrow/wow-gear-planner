"""抓宝石和附魔的属性数值。

为什么单独做这一步
------------------
装备本体在 build_db 里已经算得很准，但身上还有两块没进计算：

- 宝石（simc 的 `gem_id=`）：戒指项链插的宝石，一件能顶几十点绿字
- 附魔（simc 的 `enchant_id=`）：绝大多数给的是第三属性（吸血/闪避/速度）
  或百分比特效，只有少数给耐力和主属性

不算这两块，算出来的面板永远差一截——实测样例角色精通差 21 点，
正好是四颗宝石的 7+7+7。

数据从哪来
----------
附魔的数值在客户端 DBC 里是空的（SpellItemEnchantment 的 EffectPointsMin 全是 0，
真值靠 ScalingClass 缩放），所以不能自己算。真正可靠的是**附魔卷轴物品的说明文字**：

    "Permanently enchants boots with Lynx's Dexterity,
     increasing Avoidance by 15 and Stamina by 186."

这段文字是玩家在游戏里看到的，也就是权威值。

三步映射：
    1. permanent-enchant.inc   enchant_id → enchant_boots__lynxs_dexterity
    2. ItemSparse              名字 → 卷轴 item id（取 580 品质那档）
    3. wowhead tooltip         卷轴 id → 说明文字 → 数值

为什么取 580 品质
-----------------
DBC 里同一个附魔有 rank1 / rank2 两个 id，名字里带 `Tier2` 图标。
实测 7961(rank2) 的系数 × 26.9 = 44，正好是 580 卷轴的数值；
595 卷轴是 55（Tier3）。而 Tier3 在 DBC 里**没有**独立 id——
所以 SimulationCraft / Raidbots 用的也只能是 Tier2。
我们跟着取 580，保证"导出 simc 去 Raidbots 跑"和"本地算"两边数字一致。

用法
----
    python src/build/fetch_gems_enchants.py            # 全量抓
    python src/build/fetch_gems_enchants.py --limit 8  # 只抓几条看解析对不对

需要能访问 wowhead（国内要挂代理，脚本走 HTTPS_PROXY 环境变量）：

    set HTTPS_PROXY=http://127.0.0.1:7897
    python src/build/fetch_gems_enchants.py
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")

ENCHANT_INC = os.path.join(RAW, "permanent-enchant.inc")
ITEM_SPARSE = os.path.join(RAW, ".item-sparse.csv")
GEMS_OUT = os.path.join(RAW, "gems.json")
ENCH_OUT = os.path.join(RAW, "enchants.json")

API = "https://nether.wowhead.com/tooltip/item/{id}?dataEnv=1&locale=0"

# wowhead 英文属性名 -> 内部代码。第三属性单独一组，不进六大属性面板。
STAT_NAME = {
    "agility": "agi", "strength": "str", "intellect": "int", "stamina": "sta",
    "critical strike": "crit", "haste": "haste",
    "mastery": "mastery", "versatility": "vers",
}
TERTIARY = {
    "leech": "leech", "avoidance": "avoid", "speed": "speed",
}
CN = {"agi": "敏捷", "str": "力量", "int": "智力", "sta": "耐力", "crit": "暴击",
      "haste": "急速", "mastery": "精通", "vers": "全能",
      "leech": "吸血", "avoid": "闪避", "speed": "移动速度", "main": "主属性"}

# 卷轴槽位：inc 里的 enchant_<slot>__<name>，槽位名转成库里的 slot
INC_SLOT = {
    "helm": "head", "shoulders": "shoulder", "chest": "chest",
    "legs": "legs", "boots": "feet", "bracers": "wrist",
    "gloves": "hands", "cloak": "back", "belt": "waist",
    "ring": "finger", "weapon": "main_hand", "offhand": "off_hand",
    "shield": "off_hand", "main_hand": "main_hand",
}


def _opener(proxy=""):
    """显式挂代理。

    urllib 在 Windows 上并不会自动认 HTTPS_PROXY 环境变量（实测仍然 403），
    必须自己塞一个 ProxyHandler。wowhead 对国内 IP 直连返回 403，
    所以这一步不设代理基本等于抓不到。

    代理地址优先取 --proxy 参数，其次 WG_PROXY，最后才看标准环境变量。
    多这一层是因为有些环境（比如编辑器沙箱）会往 HTTP_PROXY 里塞自己的内部代理，
    那时候标准变量指向的不是真正的翻墙端口，照它走反而连不上。
    """
    proxy = proxy or os.environ.get("WG_PROXY") or ""
    if not proxy:
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                 or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "")
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener()


OPENER = None


def fetch(item_id, tries=3):
    """取一个物品的 tooltip JSON。"""
    global OPENER
    if OPENER is None:
        OPENER = _opener()
    req = urllib.request.Request(API.format(id=item_id),
                                 headers={"User-Agent": "Mozilla/5.0"})
    for i in range(tries):
        try:
            with OPENER.open(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.8 * (i + 1))
    return None


def norm(s):
    """名字归一化：只留小写字母和数字。

    inc 里写 `lynxs_dexterity`（撇号被吃掉），卷轴名叫 "Lynx's Dexterity"，
    `farstriders_hunt` 对应 "Farstrider's Hunt"（少个 s）。直接字符串比会漏掉
    二十多条。全部压成 `lynxsdexterity` 这种形式再比，这类差异就都消失了。
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def plain(html):
    """tooltip 是一坨 HTML，去掉标签只留文字。"""
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = t.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#39;", "'")
    return re.sub(r"\s+", " ", t).strip()


# ── 宝石 ──────────────────────────────────────────────────────────────
# 三种写法：
#   "+16 Critical Strike & +7 Mastery"         双属性宝石
#   "+32 Primary Stat"                          三色宝石（Eversong Diamond）
#   "+23 Primary Stat and +0.15% critical..."   带特效的
#
# 属性名必须白名单精确匹配。第一版写的是 `([A-Za-z ]+)`，
# 它会把后面那句 "Use: Max Stack: 200 Sell Price" 一起吞掉，
# 于是每个宝石都解析失败、全部被当成"认不出"丢掉。
STAT_ALT = (r"(Critical Strike|Primary Stat|Versatility|Mastery|Haste"
            r"|Stamina|Agility|Strength|Intellect|Leech|Avoidance|Speed)")
# `\+\s*` 而不是 `\+`：wowhead 有的 tooltip 渲染成 "+ 1 Critical Strike"（加号后带空格），
# 只认 "+1" 的话这类宝石整批解析失败。
GEM_STAT = re.compile(r"\+\s*(\d+)\s+" + STAT_ALT, re.I)


def parse_gem(text):
    """返回 {code: 值}，认不出的返回 None。"""
    out = {}
    for m in GEM_STAT.finditer(text):
        name = m.group(2).strip().lower()
        num = int(m.group(1))
        if name == "primary stat":
            out["main"] = out.get("main", 0) + num
        elif name in STAT_NAME:
            code = STAT_NAME[name]
            out[code] = out.get(code, 0) + num
        elif name in TERTIARY:
            code = TERTIARY[name]
            out[code] = out.get(code, 0) + num
    return out or None


# ── 附魔 ──────────────────────────────────────────────────────────────
# 三种写法：
#   "increasing Leech by 44"                               第三属性
#   "increasing Avoidance by 15 and Stamina by 186"         两项
#   "increasing your Primary stat by 36"                    主属性（"your"）
#   "increasing critical strike effectiveness by 1%"        百分比特效，不是绿字
#   "... increasing your Haste by 99 for 15 sec"            触发特效，不是常驻
# 一条附魔可能给两项，第二项用 and 连：
#   "increasing Avoidance by 15 and Stamina by 186"
# 只认 "increasing X by N" 的话第二项整条丢掉，脚附魔的 186 耐力就没了。
#
# 但 `.+?` 太宽松：武器附魔的说明是
#   "...and abilities to sometimes grant you Precision of the Dragonhawk,
#    increasing Critical Strike by 99%"
# 从那个 and 开始一路吞到句尾，特效就变成一句没头没尾的英文。
# 把捕获限制成"字母和空格、不含逗号"，跨不过 ", increasing" 这一刀。
ENCH_ITEM = re.compile(
    r"(?:increasing|and)\s+([A-Za-z][A-Za-z /]*?)\s+by\s+(\d+)(%?)", re.I)
# "for 15 sec" / "for 10 - 15 sec"：跟在数值后面就是触发特效，不是常驻属性
ENCH_PROC = re.compile(r"for\s+(\d+)(?:\s*-\s*(\d+))?\s*sec", re.I)
# 另一种写法："providing you with 53 Primary Stat for 15 sec"（武器附魔的触发主属性）。
# 没有 increasing/by，靠前一条正则一条都收不到，界面上就变成"无属性"。
ENCH_PROVIDE = re.compile(
    r"providing you with\s+(\d+)\s+([A-Za-z][A-Za-z /]*?)\s+for\s+(\d+)(?:\s*-\s*(\d+))?\s*sec",
    re.I)

# 说明文字里出现的属性名 -> 内部代码。带 " effectiveness" 的是百分比，单独处理。
ENCH_PHRASE = {
    "agility": "agi", "strength": "str", "intellect": "int", "stamina": "sta",
    "primary stat": "main",
    "critical strike": "crit", "haste": "haste",
    "mastery": "mastery", "versatility": "vers",
    "leech": "leech", "avoidance": "avoid", "speed": "speed",
}
ENCH_CN = {"agi": "敏捷", "str": "力量", "int": "智力", "sta": "耐力", "main": "主属性",
           "crit": "暴击", "haste": "急速", "mastery": "精通", "vers": "全能",
           "leech": "吸血", "avoid": "闪避", "speed": "移速"}

# 第三属性（吸血/闪避/移速）不进六属性面板，得和主副属性分开存。
# 注意这里存的是**内部代码**（avoid 不是 avoidance）——早先拿英文名去比，
# 闪避全被判成常驻属性，界面上的闪避会凭空算进面板。
TERTIARY_VALUES = {"leech", "avoid", "speed"}

# 纯触发类武器附魔（没有常驻属性，也没有 "increasing ... by N" 的写法）。
# 这几条的说明是一整句机制描述，机翻不如手写一句；数值靠游戏内 tooltip 补。
ENCH_DESC = {
    7979: "触发：流血伤害",
    8007: "触发：治疗护盾",
    8009: "受击时触发：吸收护盾",
    8037: "触发：火焰伤害",
    8689: "触发：随机副属性",
}


def _enchant_stat(phrase):
    """说明里的说法 -> (code, 是否百分比)。

    "your Primary stat" 这种带 your 的要先剥掉；"critical strike effectiveness"
    是百分比加成，不是绿字点数。剥完认不出的返回 (None, False)。
    """
    p = re.sub(r"^your\s+", "", (phrase or "").strip().lower())
    p = re.sub(r"\s+", " ", p)
    if p.endswith(" effectiveness"):
        base = p[: -len(" effectiveness")]
        return ENCH_PHRASE.get(base), True
    if p in ("total mana", "mana"):
        return None, True          # 法力百分比，不进六属性
    return ENCH_PHRASE.get(p), False


def parse_enchant(text):
    """返回 ({code: 值}, {第三属性: 值}, [说明...])。

    说明分三类，混在一起就会算错：
      - 常驻属性 → 进 stats/tertiary
      - 百分比  → 只写说明（"暴击效果 +1%"）
      - 触发特效 → 只写说明（"触发：急速 +99，持续 15 秒"），**不能**当常驻加进面板
    """
    stats, tertiary, notes = {}, {}, []
    for m in ENCH_ITEM.finditer(text):
        phrase, num, pct_src = m.group(1), int(m.group(2)), bool(m.group(3))
        code, is_pct = _enchant_stat(phrase)
        if pct_src or is_pct:
            # 百分比特效绝不能当点数算：暴击凭空多 1 点，看着像没问题其实全错
            label = ENCH_CN.get(code, phrase.strip())
            if "effectiveness" in phrase.lower() and code in ENCH_CN:
                label = ENCH_CN[code] + "效果"
            if "mana" in phrase.lower():
                label = "法力"
            notes.append(f"{label} +{num}%")
            continue
        if code is None:
            # 认不出的写法原样记下来，但**不加 %**——以前这里无条件补了个 %，
            # 结果 "increasing your Primary stat by 36" 变成了 "主属性 +36%"。
            notes.append(f"{phrase.strip()} +{num}")
            continue
        # 数值后面紧跟 "for N sec" 就是触发，不是常驻
        tail = text[m.end():m.end() + 40]
        proc = ENCH_PROC.search(tail)
        if proc:
            dur = proc.group(1) if not proc.group(2) else f"{proc.group(1)}~{proc.group(2)}"
            notes.append(f"触发：{ENCH_CN[code]} +{num}，持续 {dur} 秒")
            continue
        bucket = tertiary if code in TERTIARY_VALUES else stats
        bucket[code] = bucket.get(code, 0) + num

    # "providing you with 53 Primary Stat for 15 sec" 这类
    for m in ENCH_PROVIDE.finditer(text):
        num, phrase, dur = int(m.group(1)), m.group(2), m.group(3)
        code, _ = _enchant_stat(phrase)
        label = ENCH_CN.get(code, phrase.strip())
        notes.append(f"触发：{label} +{num}，持续 {dur} 秒")
    return stats, tertiary, notes


def enchant_desc(eid, stats, tertiary, notes):
    """卡片副行的一句话：优先用说明，其次手写的触发描述，都没有就空着。"""
    if notes:
        return notes[0]
    if not stats and not tertiary:
        return ENCH_DESC.get(eid, "")
    return ""


# ── 原料解析 ──────────────────────────────────────────────────────────
def load_enchants_inc(path):
    """enchant_id -> (tokenized_name, slot)。同名多 rank 只留 rank 最大的那个 id。"""
    if not os.path.exists(path):
        print(f"缺少 {path}\n"
              "从 GitHub 下载（走代理）：\n"
              "  https://raw.githubusercontent.com/simulationcraft/simc/midnight"
              "/engine/dbc/generated/permanent_enchant.inc")
        return {}
    src = io.open(path, encoding="utf-8").read()
    rows = re.findall(
        r'\{\s*(\d+),\s*(\d+),\s*(\d+),\s*0x([0-9a-fA-F]+),\s*0x([0-9a-fA-F]+),'
        r'\s*"([^"]+)"', src)
    best = {}
    for eid, rank, _inv, _mask, _mask2, name in rows:
        eid, rank = int(eid), int(rank)
        if not name.startswith("enchant_"):
            continue
        key = name
        if key not in best or rank > best[key][0]:
            best[key] = (rank, eid)
    out = {}
    for key, (rank, eid) in best.items():
        body = key[len("enchant_"):]
        slot_raw, _, rest = body.partition("__")
        out[eid] = {
            "token": key,
            "name_key": norm(rest),
            "slot": INC_SLOT.get(slot_raw, slot_raw),
            "rank": rank,
        }
    return out


def load_item_names(path, want_prefix="enchant "):
    """ItemSparse 里名字以 "Enchant " 开头、且物品等级 580 的卷轴。

    580 是 Tier2 那档（见模块开头的说明）。同名的其它等级不要。
    """
    if not os.path.exists(path):
        print(f"缺少 {path}\n"
              "从 wago.tools 下载：\n"
              "  https://wago.tools/db2/ItemSparse/csv?build=12.1.5.70575")
        return {}
    csv.field_size_limit(10 ** 9)
    out = {}
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            nm = (r.get("Display_lang") or "").strip()
            if not nm.lower().startswith(want_prefix):
                continue
            if r.get("ItemLevel") != "580":
                continue
            # "Enchant Ring - Eyes of the Eagle" -> "eyesoftheeagle"
            key = norm(nm.split(" - ", 1)[-1])
            out.setdefault(key, int(r["ID"]))
    return out


def load_gem_ids(path):
    """当前版本的宝石。判据：Gem_properties 非 0 且属于当前资料片。

    不能用装等筛：老宝石的 ItemLevel 反而更高（BfA 那批是 385），
    新宝石是 295。用装等会把新的滤掉、把旧的留下。
    ExpansionID == 11 才是 Midnight。
    """
    csv.field_size_limit(10 ** 9)
    out = []
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("Gem_properties") or "0") in ("0", ""):
                continue
            if (r.get("ExpansionID") or "0") != "11":
                continue
            nm = (r.get("Display_lang") or "").strip()
            if not nm:
                continue
            out.append((int(r["ID"]), nm))
    return out


# ── 主流程 ────────────────────────────────────────────────────────────
def do_gems(limit=0):
    ids = load_gem_ids(ITEM_SPARSE)
    if limit:
        ids = ids[:limit]
    print(f"宝石：{len(ids)} 个待抓")
    out = {}

    def one(pair):
        gid, nm = pair
        d = fetch(gid)
        if not d:
            return gid, None
        text = plain(d.get("tooltip", ""))
        stats = parse_gem(text)
        if not stats:
            return gid, None
        return gid, {"name": d.get("name") or nm, "icon": d.get("icon") or "",
                     "stats": stats}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (gid, val) in enumerate(ex.map(one, ids), 1):
            if val:
                out[str(gid)] = val
            if i % 25 == 0:
                print(f"  {i}/{len(ids)}")

    with io.open(GEMS_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"宝石：写入 {len(out)} 条 -> {GEMS_OUT}")
    return out


def do_enchants(limit=0):
    inc = load_enchants_inc(ENCHANT_INC)
    names = load_item_names(ITEM_SPARSE)
    print(f"附魔：inc {len(inc)} 条，卷轴名录 {len(names)} 条")

    todo = []
    for eid, meta in inc.items():
        item_id = names.get(meta["name_key"])
        if item_id:
            todo.append((eid, item_id, meta))
    todo.sort()
    print(f"附魔：能对上卷轴 {len(todo)} 条 / inc 共 {len(inc)} 条")
    if limit:
        todo = todo[:limit]

    out = {}

    def one(t):
        eid, item_id, meta = t
        d = fetch(item_id)
        if not d:
            return eid, None
        text = plain(d.get("tooltip", ""))
        stats, tertiary, notes = parse_enchant(text)
        return eid, {
            "name": d.get("name") or "",
            "slot": meta["slot"],
            "item_id": item_id,
            "stats": stats,
            "tertiary": tertiary,
            "note": notes[0] if notes else "",
            "desc": enchant_desc(int(eid), stats, tertiary, notes),
            "text": text[:400],
        }

    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (eid, val) in enumerate(ex.map(one, todo), 1):
            if val:
                out[str(eid)] = val
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}")

    with io.open(ENCH_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"附魔：写入 {len(out)} 条 -> {ENCH_OUT}")

    # 对不上卷轴的列出来，方便判断是数据缺失还是解析漏了
    miss = [e for e in inc if str(e) not in out]
    if miss:
        print(f"\n没抓到数值的 {len(miss)} 条：")
        for e in miss[:12]:
            print(f"  {e} {inc[e]['token']}")
    return out


# ── 腿部附魔：护甲片 / 魔线 ────────────────────────────────────────────
#
# 这类东西**不是** "Enchant XXX - YYY" 卷轴，物品名就叫「森林猎手的护甲片」，
# 所以 "只认名字以 Enchant 开头的卷轴" 那条老逻辑一条都收不到——腿部位在
# 界面上一直是空的。SimC 的 permanent-enchant.inc 里它们叫
# `forest_hunters_armor_kit` / `arcanoweave_spellthread`，不带 enchant_ 前缀，
# 又被 `if not name.startswith("enchant_")` 这行过滤掉了 92% 的表。
#
# 数值来源：客户端 SpellItemEnchantment 的 `ench_coeff`（SimC 生成表
# `engine/dbc/generated/spell_item_enchantment.inc`）乘 580 档基数 26.99，
# 和库里其余附魔是同一个基准。例：8159 的 coeff 是 3.4194/1.2191 →
# 敏捷 92 / 耐力 33。系数改动时这里要跟着改。
LEG_BASE = 26.99

# id -> (显示名, 中文名, 附魔卷轴/物品 id, {属性: coeff}, 备注)
LEG_ENCHANTS = {
    7935: ("Sunfire Silk Spellthread", "阳炎丝绸魔线", 240133,
           {"int": 1.2191, "sta": 3.4194}, "另有法力加成不计入"),
    7937: ("Arcanoweave Spellthread", "奥纹魔线", 240155,
           {"int": 1.2191}, "另有法力加成不计入"),
    7939: ("Bright Linen Spellthread", "光明亚麻魔线", 240157,
           {"int": 0.7136}, ""),
    8159: ("Forest Hunter's Armor Kit", "森林猎手的护甲片", 244641,
           {"main": 3.4194, "sta": 1.2191}, ""),
    8161: ("Thalassian Scout Armor Kit", "萨拉斯斥候护甲片", 244645,
           {"main": 0.7136}, ""),
    8163: ("Blood Knight's Armor Kit", "血骑士的护甲片", 244643,
           {"main": 1.2191}, "另有护甲值不计入"),
}

# 腿部附魔的图标：和其余附魔一样用羊皮纸，Tier2 是蓝色那张
_VELLUM = "inv_12_profession_enchanting_enchantedvellum_blue"


def leg_enchant_rows():
    out = {}
    for eid, (en, cn, item_id, coeffs, note) in LEG_ENCHANTS.items():
        stats = {code: round(coeff * LEG_BASE) for code, coeff in coeffs.items()}
        out[str(eid)] = {
            "name": en, "name_cn": cn, "slot": "legs", "item_id": item_id,
            "stats": stats, "tertiary": {}, "note": note,
            "desc": "数值按客户端附魔系数推算（580 档）",
            "icon": _VELLUM, "derived": True, "text": "",
        }
    return out


def reparse_enchants():
    """不联网，用存下来的 tooltip 原文重算 stats / note / desc，再补上腿部。

    为什么要单独一条通道：`text` 里已经存了玩家看到的原文，改解析规则
    （主属性、触发特效、百分号）不该再去 wowhead 抓一遍——那边限流，
    而且重抓会把人工核对过的数值覆盖掉。
    """
    if not os.path.exists(ENCH_OUT):
        print(f"缺少 {ENCH_OUT}，先跑一次全量抓取")
        return 1
    with io.open(ENCH_OUT, encoding="utf-8") as f:
        data = json.load(f)

    for eid, v in data.items():
        stats, tertiary, notes = parse_enchant(v.get("text") or "")
        v["stats"], v["tertiary"] = stats, tertiary
        v["note"] = notes[0] if notes else ""
        v["desc"] = enchant_desc(int(eid), stats, tertiary, notes)

    legs = leg_enchant_rows()
    data.update(legs)
    with io.open(ENCH_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f"重新解析 {len(data) - len(legs)} 条，补腿部 {len(legs)} 条 -> {ENCH_OUT}")
    changed = [(k, v["note"]) for k, v in data.items() if v.get("note")]
    print(f"带说明的 {len(changed)} 条：")
    for k, note in sorted(changed, key=lambda kv: int(kv[0]))[:40]:
        print(f"  {k}: {note}")
    return 0


def do_icons():
    """只补图标，不重算数值。

    附魔第一版没存图标（只存了数值），重跑一遍全量抓会把已经人工核对过的
    数值和特效说明再覆盖一次——网络抽风时少一条就得重新查。所以单独一条
    通道：读现有 JSON，只对缺 icon 的条目补一次 tooltip，其它字段一字不动。

    图标的来源是卷轴物品（enchant 的 item_id / 宝石自身 id），拿到的名字
    直接喂给 fetch_icons.py 下载。
    """
    changed = 0
    for path, id_key in ((GEMS_OUT, "gem"), (ENCH_OUT, "enchant")):
        if not os.path.exists(path):
            continue
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        todo = [(k, v) for k, v in data.items() if not v.get("icon")]
        if not todo:
            print(f"{id_key}：图标都齐了（{len(data)} 条）")
            continue
        print(f"{id_key}：{len(todo)} 条缺图标")

        def one(kv):
            k, v = kv
            target = v.get("item_id") or int(k)
            d = fetch(target)
            return k, (d or {}).get("icon") or ""

        with ThreadPoolExecutor(max_workers=6) as ex:
            for k, icon in ex.map(one, todo):
                if icon:
                    data[k]["icon"] = icon
                    changed += 1
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        got = sum(1 for v in data.values() if v.get("icon"))
        print(f"  {id_key}：{got}/{len(data)} 条有图标")
    print(f"\n共补 {changed} 个图标")


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓宝石与附魔的属性数值")
    ap.add_argument("--limit", type=int, default=0, help="每种只抓前 N 条，用于试跑")
    ap.add_argument("--only", choices=("gem", "enchant"), help="只跑一种")
    ap.add_argument("--icons", action="store_true",
                    help="只补缺的图标，不重算数值（数值抓好了想补图标时用）")
    ap.add_argument("--reparse", action="store_true",
                    help="不联网，用已存的原文重算属性/说明，并补上腿部附魔")
    ap.add_argument("--proxy", default="",
                    help="翻墙代理，例如 http://127.0.0.1:7897（也可用 WG_PROXY 环境变量）")
    args = ap.parse_args(argv)

    global OPENER
    OPENER = _opener(args.proxy)
    if args.proxy or os.environ.get("WG_PROXY"):
        print(f"代理：{args.proxy or os.environ['WG_PROXY']}")
    else:
        print("提示：没指定代理。国内直连 wowhead 会返回 403，"
              "需要加 --proxy http://127.0.0.1:7897\n")

    if args.reparse:
        return reparse_enchants()
    if args.icons:
        do_icons()
        return 0
    if args.only != "enchant":
        do_gems(args.limit)
    if args.only != "gem":
        do_enchants(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
