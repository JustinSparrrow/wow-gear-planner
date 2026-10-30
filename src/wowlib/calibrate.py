"""用真实角色面板反推属性换算系数。

为什么需要这个：装备只存"属性分配万分比"（alloc），真实数值要乘装等预算。
预算来自游戏表 `RandPropPoints`，但另有三类倍率官方没写在小册子上：

    rating = alloc × 预算(装等, 部位) × 0.0001 × 倍率(属性类型)

倍率按属性类型分三档（SimC 里对应三个函数）：
    - 主属性（力量/敏捷/智力）  apply_primary_stat_multiplier
    - 耐力                    apply_stamina_multiplier
    - 战斗等级（暴击/急速/精通/全能） apply_combat_rating_multiplier

这个脚本的做法：拿一份真实 simc 导出（身上装备）+ 游戏内面板评级，
算出"如果倍率都是 1"时的值，再反除面板，得到每类属性的实际倍率。

    python src/wowlib/calibrate.py tests/my_profile.txt \
        --panel agi=2861,sta=42612,crit=1065,haste=121,mastery=1423,vers=394

注意：面板上要填**评级**（鼠标悬停看到的"XXX 等级"），不是百分比。
百分比里混着职业基础值和天赋加成，反推会污染结果。
"""

import argparse
import csv
import io
import json
import os
import sqlite3
import sys

# 这个文件在 src/<包>/ 下，把 src 本身放进 path 才能 `import wowgear`。
# 不用相对 import：那样 `python src/wowlib/calibrate.py` 直接跑会炸。
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from wowlib.simc import parse, resolve, class_primary  # noqa: E402
from wowgear.const import SIMC_SLOTS  # noqa: E402
from wowgear import stats as stat_calc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

from wowgear.paths import DB_PATH, RPP_PATH, ROOT  # noqa: E402

# 槽位 -> RandPropPoints 的部位档位（0=大件 1=中件 2=小件）
# 依据：五档系数比例约 1 : 0.75 : 0.5625 : 0.5 : 0.5，和 WoW 的部位预算分配一致。
# 这是本脚本唯一靠猜的地方，校准结果可以反过来验证它：如果某类槽位的倍率明显偏离，
# 多半是这个映射写错了。
SLOT_COLUMN = {
    "head": 0, "chest": 0, "legs": 0, "main_hand": 0, "ranged": 0,
    "shoulder": 1, "hands": 1, "waist": 1, "feet": 1,
    "wrist": 2, "back": 2, "neck": 2, "finger": 2, "trinket": 2, "off_hand": 2,
}

STAT_ID = stat_calc.STAT_ID   # 和 wowgear/stats.py 同一张表，别各存一份
HYBRID = {"str_agi_int", "str_agi", "agi_int", "str_int", "any"}

CATEGORY = {"agi": "主属性", "str": "主属性", "int": "主属性", "sta": "耐力",
            "crit": "战斗等级", "haste": "战斗等级", "mastery": "战斗等级",
            "vers": "战斗等级"}


def load_budget():
    """装等 -> 五档预算系数。

    这张表只有命令行 `lookup` 和 calibrate 脚本在用；运行时算属性走的是
    `stat_curve.csv` 那三条曲线，不碰它。内测包故意没带 `data/raw/`，
    文件不存在时返回空表而不是抛异常——以前这里 FileNotFoundError 会让
    **每次导入角色都 500**，而报错信息只指向一个"可以删"的构建期文件。
    """
    table = {}
    try:
        f = io.open(RPP_PATH, encoding="utf-8-sig")
    except OSError:
        return table
    with f:
        for row in csv.DictReader(f):
            try:
                ilevel = int(row["ID"])
            except (TypeError, ValueError):
                continue
            vals = []
            for i in range(5):
                raw = row.get(f"EpicF_{i}")
                vals.append(float(raw) if raw not in (None, "") else 0.0)
            table[ilevel] = vals
    return table


def _add(out, code, value):
    if code and value:
        out[code] = out.get(code, 0.0) + value


def gem_values(conn, item, primary):
    """一件装备上所有宝石给的属性。

    宝石的 `main`（三色宝石写的 "Primary Stat"）要落到角色真正吃的那个属性上——
    猎人的 +32 敏捷和法师的 +32 智力是两颗不同的宝石，库里只能记一条。

    返回 ({code: 值}, [认不出的 gem_id])。
    """
    if not item.gem_ids:
        return {}, []
    # 空孔用 0 占位（孔是位置不是数量），0 不是宝石，不能去查库、
    # 更不能报进"认不出的宝石"里
    ids = [g for g in item.gem_ids if g]
    if not ids:
        return {}, []
    rows = conn.execute(
        "SELECT id, stats FROM gems WHERE id IN (%s)"
        % ",".join("?" * len(ids)), tuple(ids)).fetchall()
    known = {r["id"]: r["stats"] for r in rows}
    out, missing = {}, []
    for gid in item.gem_ids:
        if not gid:
            continue
        raw = known.get(gid)
        if not raw:
            missing.append(gid)
            continue
        for code, val in json.loads(raw).items():
            _add(out, primary if code == "main" else code, val)
    return out, missing


def enchant_values(conn, item, primary=None):
    """一件装备的附魔给的属性。

    返回 ({code: 值}, {tertiary}, note, 是否已知)。

    primary 有值时，附魔里的 "main"（"+36 主属性"）落到本职业真吃的那条上：
    同一件胸甲附魔，猎人的 +36 是敏捷，法师的是智力。不落的话这个键
    谁也不认识，加进去了也不会显示——胸附魔的 +36 主属性就这么丢过。
    """
    if not item.enchant_id:
        return {}, {}, "", True
    row = conn.execute(
        "SELECT stats, tertiary, note FROM enchants WHERE id = ?",
        (item.enchant_id,)).fetchone()
    if not row:
        # 库里没有这条附魔（老版本 / 采集工具附魔）。静默跳过会让面板莫名少一截，
        # 所以要把它标出来，界面上写"未收录"。
        return {}, {}, "", False
    stats = json.loads(row["stats"] or "{}")
    if primary and stats.get("main"):
        stats = {**stats, primary: stats["main"]}
        stats.pop("main")
    tert = json.loads(row["tertiary"] or "{}")
    return stats, tert, row["note"] or "", True


def compute(conn, prof, budget=None):
    """全身装备的属性合计（含宝石和附魔）。

    数值换算交给 wowgear.stats（它按装等和部位查曲线，主属性/耐力/绿字三条线）。
    budget 参数只为兼容旧调用保留，传了也不用了——RandPropPoints 只能算主属性，
    拿它算绿字会高估两成。
    """
    rows, _ = resolve(conn, prof)
    primary = class_primary(prof.class_key, prof.spec)
    totals, detail = {}, []

    for r in rows:
        it = r["item"]
        ilevel = r["ilevel"] or 0
        # resolve 给的是 sqlite Row，转成 stats 模块认的结构
        stats_in = [{"code": st["code"], "alloc": st["alloc"],
                     "optional": bool(st["is_crafted_optional"])}
                    for st in r["stats"]]
        got = stat_calc.item_values(it.db_slot, ilevel, stats_in,
                                    list(it.crafted_stats), r["inventory_type"])

        # 宝石和附魔不随装等缩放，是固定的点数，直接加
        gems, gem_missing = gem_values(conn, it, primary)
        ench, ench_tert, ench_note, ench_known = enchant_values(conn, it, primary)

        for code, value in got.items():
            totals[code] = totals.get(code, 0.0) + value
        for bucket in (gems, ench):
            for code, value in bucket.items():
                _add(totals, code, value)
        detail.append({"slot": it.slot, "item_id": it.item_id,
                       "stat_id": it.extra.get("redirected_base_stats") or it.item_id,
                       "ilevel": ilevel,
                       "factor": stat_calc.curve.factors(
                           ilevel, stat_calc.curve.kind_of(it.db_slot))["sec"],
                       "got": got,
                       "gems": gems,
                       "gem_missing": gem_missing,
                       "enchant": ench,
                       "enchant_tertiary": ench_tert,
                       "enchant_note": ench_note,
                       "enchant_known": ench_known})
    return totals, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description="用面板数据反推属性倍率")
    ap.add_argument("profile", help="simc 导出的 profile 文件")
    ap.add_argument("--panel", required=True,
                    help="面板评级，例如 agi=2861,sta=42612,crit=1065,...")
    args = ap.parse_args(argv)

    panel = {}
    for part in args.panel.split(","):
        key, _, val = part.partition("=")
        panel[key.strip()] = float(val or 0)

    with open(args.profile, encoding="utf-8") as f:
        prof = parse(f.read())

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    totals, detail = compute(conn, prof, load_budget())
    conn.close()

    print("=" * 74)
    print(f"角色 {prof.class_key}（{prof.class_cn}）  装等按注释与 simc 取值")
    print("=" * 74)
    print(f"{'槽位':10}{'装备 id':10}{'取属性 id':11}{'装等':6}{'预算':8}算出值")
    for d in detail:
        mark = " ←重定向" if d["stat_id"] != d["item_id"] else ""
        got = " ".join(f"{k}{v:.0f}" for k, v in sorted(d["got"].items()))
        print(f"{d['slot']:10}{d['item_id']:<10}{d['stat_id']:<11}"
              f"{d['ilevel']:<6}{d['factor'] * 1e4:<8.1f}{got}{mark}")

    print("\n" + "-" * 74)
    print(f"{'属性':8}{'类别':10}{'算出(倍率=1)':>14}{'面板评级':>10}{'隐含倍率':>10}")
    print("-" * 74)
    by_cat = {}
    for code in ("agi", "sta", "crit", "haste", "mastery", "vers"):
        if code not in panel:
            continue
        value = totals.get(code, 0.0)
        ratio = panel[code] / value if value else 0
        by_cat.setdefault(CATEGORY[code], []).append(ratio)
        print(f"{code:8}{CATEGORY[code]:10}{value:14.1f}{panel[code]:10.0f}{ratio:10.3f}")

    print("-" * 74)
    print("按类别汇总：")
    for cat, ratios in by_cat.items():
        avg = sum(ratios) / len(ratios)
        spread = max(ratios) - min(ratios)
        flag = "✓ 一致" if spread < avg * 0.08 else "⚠ 分散，可能有装备取错属性"
        print(f"  {cat:8} 倍率 ≈ {avg:.3f}   （同类之间差 {spread:.3f}）{flag}")
    print("\n提示：倍率=1 说明这条公式对该类属性成立；差异大说明有多件装备的属性取错了，")
    print("      优先查 redirected_base_stats（属性来自另一件装备）和制造业自选槽。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
