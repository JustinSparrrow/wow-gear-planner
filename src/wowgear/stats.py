"""把装备存的"权重"折成实际属性数值。

装备表里存的不是属性值，是分配权重（alloc，万分比）。真实数值 = 权重 × 系数 × 1e-4，
系数由 `curve` 按装等和部位给。这里只做这一件事，不知道数据库、不知道 simc。

为什么要分三个桶：主属性、耐力、绿字各走一条曲线（见 curve.py 的说明），
用同一个系数算的话，绿字会高估两成、耐力会少算一个数量级。
"""

from . import curve

MAIN = ("agi", "str", "int")
SECONDARY = ("crit", "haste", "mastery", "vers")

# stat_id -> 代码。24/25 是制造业和装绑的两个"自选"占位槽，本身没有固定属性。
STAT_ID = {3: "agi", 4: "str", 5: "int", 7: "sta",
           32: "crit", 36: "haste", 40: "vers", 49: "mastery"}


def _bucket(code):
    if code in MAIN:
        return "main"
    if code == "sta":
        return "sta"
    if code in SECONDARY:
        return "sec"
    return None


def item_values(db_slot, ilevel, stats, crafted=(), inventory_type=None):
    """单件装备的属性数值。

    stats      : [{code, alloc, optional}]，optional 为真表示这个槽的属性由玩家指定
    crafted    : 玩家指定的 stat_id 列表，顺序对应 optional 槽出现的顺序
    inventory_type : 背包里的物品类型（15 弓 / 17 双手 / 26 弩），用来区分
                 双手武器和单手武器——两者预算差一倍多，不能同一条曲线
    """
    if not ilevel or not stats:
        return {}
    f = curve.factors(ilevel, curve.kind_of(db_slot, inventory_type))
    out, slot_no = {}, 0
    for st in stats:
        code = st.get("code")
        if st.get("optional"):
            code = STAT_ID.get(crafted[slot_no]) if slot_no < len(crafted) else None
            slot_no += 1
        bucket = _bucket(code)
        if not bucket:
            continue
        out[code] = out.get(code, 0.0) + st["alloc"] * f[bucket] * 1e-4
    return {k: round(v) for k, v in out.items()}


def optional_each(db_slot, ilevel, stats):
    """还没选属性的自选槽，每个槽能提供多少点。

    候选列表里就靠它显示「自选槽 125 × 2」——只写"待指定"的话，
    用户没法判断这件换上去到底能加多少。
    """
    if not ilevel or not stats:
        return []
    f = curve.factors(ilevel, curve.kind_of(db_slot))
    return [round(st["alloc"] * f["sec"] * 1e-4) for st in stats if st.get("optional")]


def total(values_list):
    """多件装备相加。"""
    out = {}
    for v in values_list:
        for k, val in v.items():
            out[k] = out.get(k, 0.0) + val
    return out
