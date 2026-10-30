"""玩家自己填的两份配置：自选属性、面板基准。

都是"人的决定"，不是算出来的，所以单独存、单独读，跟数据库无关。
两份都用同样的手写 JSON 格式，为的是保住 `_note` 说明——`json.dump` 一覆盖
就只剩一堆看不懂的数字。
"""

import json
import os

from .paths import CHOICE_PATH, PANEL_PATH

# 面板评级 -> 百分比。这些转换率是从真实面板反推的（暴击 1065=38.15%、
# 急速 121=4.81%、精通 1423=79.50%、全能 394=7.30%），Midnight 压缩过属性，
# 网上流传的"暴击 46=1%"是旧版本，套上去会差将近一半。
RATING_PER_PCT = {"crit": 27.92, "haste": 25.16, "mastery": 17.90, "vers": 53.97}
PANEL_STATS = ("agi", "str", "int", "sta", "crit", "haste", "mastery", "vers")


def _write(path, note, items):
    """写 `{ "_note": ..., "k": v }` 这种手排格式。"""
    lines = ["{", f' "_note": {json.dumps(note, ensure_ascii=False)},']
    for i, (key, val) in enumerate(items):
        lines.append(f' "{key}": {val}{"," if i < len(items) - 1 else ""}')
    lines.append("}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── 自选属性 ────────────────────────────────────────────────

def load_choices():
    """玩家确认过的自选属性。simc 导出的 crafted_stats 不可信，这份才准。"""
    if not os.path.exists(CHOICE_PATH):
        return {}
    try:
        with open(CHOICE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {int(k): tuple(v) for k, v in raw.items() if k.isdigit()}


def save_choices(choices):
    note = ("玩家确认过的自选属性。key 是装备 id，value 是两个槽的 stat_id"
            "（32=暴击 36=急速 40=全能 49=精通），顺序 = 大槽/小槽。"
            "制造业两槽平分，顺序不影响结果。")
    items = [(k, "[" + ", ".join(str(x) for x in v) + "]")
             for k, v in choices.items()]
    _write(CHOICE_PATH, note, items)


# ── 面板基准 ────────────────────────────────────────────────
#
# **按「职业:专精」分开存**。同一个职业的不同专精，基础属性不一样：
# - 混合职业的主属性本身就会换（圣骑神圣吃智力、防护/惩戒吃力量；
#   萨满元素吃智力、增强吃敏捷）；
# - 就算主属性相同，天赋也随专精变，面板上的基础值跟着变。
#
# 以前是一份全局面板，换专精之后 base 还是按上一套算，于是"面板 2861"
# 被套到另一个专精上，所有绝对值都是错的。文件长这样：
#
#   { "_note": "...", "hunter:beast_mastery": {"agi": 2861, ...}, ... }

def panel_key(class_key, spec):
    """面板基准的键：`hunter:beast_mastery`。没有专精时就只有职业。"""
    if not class_key:
        return ""
    return f"{class_key}:{spec}" if spec else class_key


def _read_panel_file():
    if not os.path.exists(PANEL_PATH):
        return {}
    try:
        with open(PANEL_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _clean_panel(raw):
    """把 `{stat: 值}` 洗干净：只留认识的属性，值转 float。

    面板里还可以带一个 `pct` 子字典（游戏面板上的百分比），用它反推
    这个专精真实的"评级→百分比"换算率——精通尤其必须这么做：
    每个专精的 mastery 系数不同，统一用 17.9 会让射击猎的精通显示成 64%，
    而游戏里是 20.5%。
    """
    out = {}
    for k, v in (raw or {}).items():
        if k in PANEL_STATS:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
        elif k == "pct" and isinstance(v, dict):
            pct = {}
            for kk, vv in v.items():
                if kk in PANEL_STATS:
                    try:
                        pct[kk] = float(vv)
                    except (TypeError, ValueError):
                        pass
            if pct:
                out["pct"] = pct
    return out


def load_panels():
    """所有专精的面板值：{key: {stat: 值}}。

    兼容老格式（顶层直接是 agi/sta/... 的扁平结构）——那种只有一份，
    归到 `__legacy__`，由 `load_panel()` 在第一次按专精读取时认领。
    """
    raw = _read_panel_file()
    legacy = _clean_panel(raw)
    out = {}
    for key, val in raw.items():
        if key.startswith("_") or key == "__legacy__":
            continue
        cleaned = _clean_panel(val if isinstance(val, dict) else {})
        if cleaned:
            out[key] = cleaned
    if legacy and "__legacy__" not in out:
        out["__legacy__"] = legacy
    return out


def load_panel(key=""):
    """某个专精的面板值。老格式会在这次读取时迁到当前专精名下。"""
    raw = _read_panel_file()
    legacy = _clean_panel(raw)
    if key and isinstance(raw.get(key), dict):
        return _clean_panel(raw[key])
    if legacy and key:
        # 老文件只有一份，没法分辨属于哪个专精：归给第一个来读它的专精，
        # 并立刻写回新格式，这样别的专精不会跟着套用
        save_panel(key, legacy)
        return legacy
    return _clean_panel(raw.get(key)) if key else {}


def save_panel(key, panel):
    """写回某个专精的面板值，别的专精和 `_note` 都不动。"""
    data = load_panels()
    data.pop("__legacy__", None)
    cleaned = {k: v for k, v in panel.items() if k in PANEL_STATS and v}
    # pct（游戏面板上的百分比）要留着：换算率靠它反推，尤其是精通
    pct = panel.get("pct")
    if isinstance(pct, dict) and pct:
        cleaned["pct"] = {k: float(v) for k, v in pct.items()
                          if k in PANEL_STATS and v}
    data[key] = cleaned
    note = ("按「职业:专精」分开存的游戏面板值。评级不是百分比；"
            "pct 是面板上括号里那个百分比，用来反推该专精的换算率"
            "（精通每个专精差好几倍）。换专精要重新填。")
    with open(PANEL_PATH, "w", encoding="utf-8") as f:
        json.dump({"_note": note, **{k: data[k] for k in sorted(data)}},
                  f, ensure_ascii=False, indent=1)
        f.write("\n")
