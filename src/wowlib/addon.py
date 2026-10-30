"""解析 WoWGear 插件导出的角色数据。

插件吐一行 `key=value;key=value`，这里把它变成三样东西：
  1. 评级 —— 可以直接喂给 `Session(panel=...)`
  2. 基线 —— 该专精「装备之外」的固定百分比，写进 spec_stats 用
  3. 交叉校验 —— 插件的换算百分比 vs 我们自己算的，对不上就说明公式有问题

为什么单独一个模块：命令行、网页接口、测试三处都要用同一套解析，
放在一处免得实现走偏。

**只信任两样东西**：评级和面板总数。中间的换算部分一律用我们自己的
`dr_convert() + 分母` 重算，插件给的 `B` 仅用来对比。
原因是 `B` 的口径在不同属性上不一致——精通的 `GetCombatRatingBonus`
给的是「精通点数增量」而不是百分比，直接拿去减会错得很隐蔽。
"""

import json

# 评级 -> 分母（客户端 __combat_ratings 90 级值）
DEN = {"crit": 46.0, "haste": 44.0, "vers": 54.0, "mastery": 46.0}

# 主属性是智力的职业，面板上的「暴击」看的是法术暴击那条
INT_CLASSES = {"mage", "priest", "warlock", "evoker"}


def parse(text):
    """把插件导出的一行解析成 dict。容错优先，不抛异常。

    用户会手抖粘进换行、空格、多一个分号，这些都当分隔符处理，
    因为报错让他去猜哪里多了一个字符，不如直接忽略。
    """
    out = {}
    if not text:
        return out
    # 换行、制表符都当分隔符：从聊天框复制时常带进来
    raw = text.replace("\r", ";").replace("\n", ";").replace("\t", ";")
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _f(data, key, default=0.0):
    try:
        return float(data.get(key, default))
    except (TypeError, ValueError):
        return default


def ratings(data):
    """四维评级 + 三个第三属性评级。"""
    out = {}
    for k in ("crit", "haste", "mastery", "vers", "leech", "avoid", "speed"):
        if (k + "R") in data:
            out[k] = _f(data, k + "R")
    return out


def panel_pct(data):
    """面板上的百分比。缺的项不塞进来，让调用方自己决定退回什么。

    暴击有两条线：物理职业看 `critT`，法系看 `spellCritT`。选错会差十几个点，
    所以按职业主属性挑，而不是有哪个用哪个。
    """
    cls = (data.get("classKey") or "").lower()
    out = {}
    crit = _f(data, "spellCritT" if cls in INT_CLASSES else "critT")
    if not crit:
        crit = _f(data, "critT") or _f(data, "spellCritT")
    if crit:
        out["crit"] = round(crit, 2)
    if _f(data, "hasteT"):
        out["haste"] = round(_f(data, "hasteT"), 2)
    if _f(data, "mastT"):
        out["mastery"] = round(_f(data, "mastT"), 2)
    # 全能没有单独的「总数」接口，面板显示的就是评级换算值，
    # 所以直接用 B。这里是个例外，要和基线计算分开处理。
    if _f(data, "versB"):
        out["vers"] = round(_f(data, "versB"), 2)
    return out


def spec_key(data):
    """拼出 `hunter:survival` 这种 key。缺任何一半就返回空串。"""
    cls = data.get("classKey") or ""
    spec = data.get("specKey") or ""
    if not cls or not spec or "?" in (cls + spec):
        return ""
    return f"{cls}:{spec}"


def baselines(data, curves=None, coeff=None, dr_convert=None):
    """反推该专精的基线：面板总数 − 评级换算。

    `curves` 是 rating_curves.json 的内容，`coeff` 是该专精的精通系数。
    没给就只返回能算的项，不硬凑。

    返回 (基线 dict, 备注 list)。备注里记着哪些项没算出来、哪些数看着可疑，
    调用方可以决定要不要采纳——静默丢数据比报错更糟。
    """
    notes = []
    pct = panel_pct(data)
    r = ratings(data)
    out = {}

    if curves is None or dr_convert is None:
        notes.append("没有换算曲线，无法反推基线")
        return out, notes

    dr = (curves or {}).get("dr") or {}

    for stat in ("crit", "haste", "vers"):
        if stat not in pct or stat not in r:
            continue
        gear = dr_convert(r[stat], dr.get(stat, [])) / DEN[stat]
        out[stat] = round(pct[stat] - gear, 2)

    if "mastery" in pct and "mastery" in r and coeff:
        gear = dr_convert(r["mastery"], dr.get("mastery", [])) / DEN["mastery"] * coeff
        out["mastery"] = round(pct["mastery"] - gear, 2)
    elif "mastery" in r and coeff is None:
        notes.append("缺该专精的精通系数，精通基线没算")

    return out, notes


def cross_check(data, curves=None, dr_convert=None, tol=0.05):
    """拿插件给的 B 和我们的换算对一遍，返回差异清单。

    这一步才是插件的真正价值：它里面的数来自游戏本体，
    如果和我们的公式对不上，就是我们的公式有问题，而不是面板抄错了。

    精通的 B 口径不同（点数 vs 百分比），单独说明，不参与对比。
    """
    if curves is None or dr_convert is None:
        return []
    dr = (curves or {}).get("dr") or {}
    r = ratings(data)
    diffs = []
    for stat in ("crit", "haste", "vers"):
        if stat not in r or (stat + "B") not in data:
            continue
        ours = dr_convert(r[stat], dr.get(stat, [])) / DEN[stat]
        theirs = _f(data, stat + "B")
        d = round(ours - theirs, 3)
        if abs(d) > tol:
            diffs.append({
                "stat": stat, "rating": r[stat],
                "ours": round(ours, 3), "game": round(theirs, 3), "diff": d,
            })
    return diffs


def summarize(data):
    """给人看的一行摘要，命令行和网页提示都能用。"""
    key = spec_key(data) or "未知专精"
    lv = data.get("level", "?")
    build = data.get("build", "?")
    return f"{key}  {lv} 级  build {build}"


def load_curves(path):
    """读 rating_curves.json。命令行用，网页端走数据库。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
