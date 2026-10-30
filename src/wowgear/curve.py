"""属性曲线：装等 → 每万点权重折算出多少属性。

**为什么不用 RandPropPoints 算到底**

游戏表 `RandPropPoints` 只够算主属性：拿肩部实测，331 装等主属性 K=262.4，
表里档位1 是 261.51，对得上；腰腿头等大件对应档位 0（348.68），也吻合。

但绿字不是它——肩部 331 装等的绿字 K 是 212.9，表里任何一档都不是这个数；
而且绿字和主属性的**比值随装等变化**（250 装等时绿字比主属性还高，344 装等时
只有 0.76 倍）。耐力同理，是第三条曲线。

所以这张表的做法是：拿真实装等的游戏内数值反算 K，直接采样成表，插值使用。
数据来自 wowhead tooltip（`nether.wowhead.com/tooltip/item/<id>?ilvl=<装等>`），
同一件装备只改装等参数，属性就是该装等的真实值。

采样脚本见 `tools/sample_curve.py`，产物 `data/stat_curve.csv`：

    ilvl, main_big, sta_big, sec_big, main_small, sta_small, sec_small, main_weapon, ...

五类部位（**不是三档中的"大件/中件/小件"那么简单**，踩过坑）：

    big     头 胸 腿            331 装等：敏捷 183、绿字 198
    mid     肩 手 腰 脚          331 装等：敏捷 138、绿字 149
    small   腕 背                331 装等：敏捷 103、绿字 112
    jewel   颈 戒指 饰品         331 装等：没有主属性，绿字 398
    weapon  主手 / 副手

**jewel 那类是个独立档**：项链/戒指/饰品没有主属性，绿字预算被补偿到
同样是档位2 的腕/背的 1.42 倍。不单独分出来，戒指的绿字会少算三成
（8750 权重算出 139，游戏里是 199）。

第一版采样拿 "Soulslither Spaulders"（肩）当大件，得出"头和脚同档"的错误结论，
结果头胸腿全部用成中件系数，腿的绿字少算 25%（74 vs 99）。
教训：采样样本的部位必须核对 slot，不能看英文名猜。
"""

import bisect
import csv
import io

from .paths import STAT_CURVE

KINDS = ("big", "mid", "small", "jewel", "weapon")
PARTS = ("main", "sta", "sec")


class _Curve:
    """一张按装等排序、可线性插值的系数表。"""

    def __init__(self, rows):
        rows.sort(key=lambda r: r["ilvl"])
        self._levels = [r["ilvl"] for r in rows]
        self._rows = rows

    def at(self, ilvl, part, kind):
        if not self._rows:
            return 0.0
        key = f"{part}_{kind}"
        i = bisect.bisect_left(self._levels, ilvl)
        if i <= 0:
            return float(self._rows[0][key])
        if i >= len(self._levels):
            # 超出采样范围就外推：拿最后两点连线的斜率，总比钳死在一个值上强
            lo, hi = self._rows[-2], self._rows[-1]
            span = hi["ilvl"] - lo["ilvl"] or 1
            slope = (hi[key] - lo[key]) / span
            return float(hi[key]) + slope * (ilvl - hi["ilvl"])
        lo, hi = self._rows[i - 1], self._rows[i]
        span = hi["ilvl"] - lo["ilvl"] or 1
        ratio = (ilvl - lo["ilvl"]) / span
        return float(lo[key]) + (float(hi[key]) - float(lo[key])) * ratio


_cache = {}


def load():
    """读曲线表，返回 _Curve。文件缺失时返回空表（调用方会退回旧公式）。"""
    if "curve" in _cache:
        return _cache["curve"]
    rows = []
    try:
        with io.open(STAT_CURVE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({k: (int(v) if k == "ilvl" else float(v))
                                 for k, v in r.items() if v not in ("", None)})
                except (TypeError, ValueError):
                    continue
    except OSError:
        pass
    _cache["curve"] = _Curve(rows)
    return _cache["curve"]


def factors(ilvl, kind="big"):
    """这个装等、这一类部位的三个系数：主属性 / 耐力 / 绿字。

    用法：`attribute = alloc * factors(ilvl, kind)[part] * 1e-4`
    """
    curve = load()
    out = {part: curve.at(ilvl, part, kind) for part in PARTS}
    # jewel 类里戒指和项链没有主属性，采样出来的主属性系数是 0；但**饰品有**
    # （比如加敏捷的主动饰品）。全按 0 算的话饰品的主属性会凭空消失，
    # 所以回退到同档位（腕/背）的系数——它们同属 RandPropPoints 档位 2。
    if kind == "jewel" and not out["main"]:
        out["main"] = curve.at(ilvl, "main", "small")
    return out


def available():
    """曲线表是否可用（为空说明采样文件没生成）。"""
    return bool(load()._rows)


def kind_of(db_slot, inventory_type=None):
    """数据库槽位名 -> 曲线档位。

    完全对应游戏 RandPropPoints 的部位分档，外加一个"无主属性部位"特例：
        头胸腿     RandPropPoints 档位 0（最大）
        肩手腰脚   档位 1
        腕背       档位 2
        颈戒指饰品 档位 2，但没有主属性，绿字另有 1.42 倍补偿

    **武器要按"几只手"分档**：双手武器（长柄/法杖/弓/枪，inventory_type 17/15/26）
    的预算和头胸腿是同一档；单手/副手才是 `weapon` 那档。
    这条以前没分：采样用的是 268201（单手战刃），把双手武器按单手算，
    结果 321 的长柄斧敏捷算成 69，游戏里是 167——少了 2.4 倍。
    """
    if db_slot in ("head", "chest", "legs"):
        return "big"
    if db_slot in ("shoulder", "hands", "waist", "feet"):
        return "mid"
    if db_slot in ("wrist", "back"):
        return "small"
    if db_slot in ("neck", "finger", "trinket"):
        return "jewel"
    # 双手武器 / 远程武器走"大件"那档预算
    if inventory_type in (15, 17, 26):
        return "big"
    return "weapon"
