"""属性待指定装备的绿字计算

本工具处理两类装备，它们的次要属性不由装备决定，而由玩家选择：

  制造业（crafted）  两个自选槽平分，例如 3500 : 3500
  团本装绑（boe）    两个自选槽一大一小，例如 4868 : 2132

两者的槽位比例都写在 item_stats 的 stat_id 24/25 里，属性本身入不了库——
同一件装备可以做成暴击+精通，也可以做成急速+全能，拍卖行上同时有好几种。

用法
----
    python src/stat_picker.py list                      # 列出全部待指定装备
    python src/stat_picker.py list --kind boe           # 只看装绑
    python src/stat_picker.py list --kind crafted --slot back
    python src/stat_picker.py pick --item 271438 --ilvl 321 --stats crit,mastery
    python src/stat_picker.py pick --item 271438 --ilvl 321 --stats crit,mastery --json

数值口径与 calibrate.py 一致：属性 = alloc × 预算(装等, 部位档位) × 1e-4
"""

import argparse
import json
import os
import sqlite3
import sys

# 这个文件在 src/<包>/ 下，把 src 本身放进 path 才能 `import wowgear`。
# 不用相对 import：那样 `python src/wowlib/calibrate.py` 直接跑会炸。
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from wowlib.calibrate import load_budget, SLOT_COLUMN      # noqa: E402

from wowgear.paths import DB_PATH, ROOT  # noqa: E402

SECONDARIES = ["crit", "haste", "mastery", "vers"]
SEC_CN = {"crit": "暴击", "haste": "急速", "mastery": "精通", "vers": "全能"}
KIND_CN = {"crafted": "制造业", "boe": "装绑"}


def connect():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"找不到 {DB_PATH}，先跑 python src/build_db.py")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def slots_of(conn, item_id):
    """取 24/25 两个自选槽的 alloc，按大→小返回。

    制造业会是 3500/3500（相等），装绑则一大一小。
    """
    rows = conn.execute("""
        SELECT stat_id, alloc FROM item_stats
         WHERE item_id = ? AND stat_id IN (24, 25)
         ORDER BY alloc DESC
    """, (item_id,)).fetchall()
    return [(r["stat_id"], r["alloc"]) for r in rows]


def stat_values(conn, item_id, ilevel, stats, budget):
    """把用户选的两个属性换算成绿字数值。

    返回 (明细列表, 部位档位, 预算系数)。
    """
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise SystemExit(f"库中没有装备 {item_id}")

    slots = slots_of(conn, item_id)
    if not slots:
        raise SystemExit(f"{item['name']} 没有自选槽，属性是固定的，不需要指定")

    column = SLOT_COLUMN.get(item["slot"], 2)
    row = budget.get(ilevel)
    if not row:
        raise SystemExit(f"预算表里没有装等 {ilevel}（范围 {min(budget)}-{max(budget)}）")
    factor = row[column] * 1e-4

    total = sum(a for _, a in slots)
    if len(stats) > len(slots):
        hint = "只有一个自选槽，只能选一个属性" if len(slots) == 1 else f"只有 {len(slots)} 个自选槽"
        raise SystemExit(f"这件装备{hint}，你选了 {len(stats)} 个：{', '.join(stats)}")
    detail = []
    for idx, (_, alloc) in enumerate(slots):
        code = stats[idx] if idx < len(stats) else stats[-1]
        detail.append({
            "code": code,
            "label": SEC_CN.get(code, code),
            "alloc": alloc,
            "share": alloc / total if total else 0,
            "value": round(alloc * factor, 1),
        })
    return item, detail, column, row[column]


def cmd_list(args):
    conn = connect()
    sql = """SELECT id, name, name_cn, slot, craft_kind, quality
               FROM items
              WHERE is_current = 1 AND craft_kind IS NOT NULL"""
    params = []
    if args.kind:
        sql += " AND craft_kind = ?"
        params.append(args.kind)
    if args.slot:
        sql += " AND slot = ?"
        params.append(args.slot)
    sql += " ORDER BY craft_kind, slot, name"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("没有符合条件的装备")
        return 0

    print(f"{'类型':<8}{'部位':<10}{'id':<9}{'装备':<40}{'自选槽'}")
    print("-" * 86)
    for r in rows:
        slots = slots_of(conn, r["id"])
        total = sum(a for _, a in slots)
        if len(slots) >= 2:
            a, b = slots[0][1], slots[1][1]
            ratio = f"{a} : {b}  ({a*100//total}:{b*100//total})"
        elif len(slots) == 1:
            ratio = f"{slots[0][1]}（单属性占满）"
        else:
            ratio = "—"
        name = r["name_cn"] or r["name"]
        print(f"{KIND_CN.get(r['craft_kind'], r['craft_kind']):<8}"
              f"{(r['slot'] or ''):<10}{r['id']:<9}{name[:38]:<40}{ratio}")
    print(f"\n共 {len(rows)} 件")
    return 0


def cmd_pick(args):
    budget = load_budget()
    conn = connect()

    stats = [s.strip() for s in args.stats.split(",") if s.strip()]
    bad = [s for s in stats if s not in SECONDARIES]
    if bad:
        raise SystemExit(f"不认识的属性 {bad}，可用：{', '.join(SECONDARIES)}")

    item, detail, column, coef = stat_values(conn, args.item, args.ilvl, stats, budget)

    if args.json:
        print(json.dumps({
            "item_id": item["id"], "name": item["name"],
            "kind": item["craft_kind"], "slot": item["slot"],
            "ilevel": args.ilvl, "column": column, "coef": coef,
            "stats": detail,
        }, ensure_ascii=False, indent=1))
        return 0

    name = item["name_cn"] or item["name"]
    print(f"{name}")
    print(f"  {KIND_CN.get(item['craft_kind'], '?')} · 部位 {item['slot']} · 装等 {args.ilvl}")
    if len(detail) >= 2:
        print(f"  自选槽  {detail[0]['alloc']} : {detail[-1]['alloc']}"
              f"  →  {detail[0]['share']*100:.1f}% / {detail[-1]['share']*100:.1f}%")
    else:
        print(f"  自选槽  {detail[0]['alloc']}（单属性占满）")
    print(f"  预算    档位 {column}，系数 {coef}")
    print()
    for d in detail:
        print(f"  {d['label']}  {d['value']:>9,.0f}")
    print()
    print(f"  合计    {sum(d['value'] for d in detail):>9,.0f}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="属性待指定装备（制造业 / 装绑）的绿字计算")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出属性待指定的装备")
    p_list.add_argument("--kind", choices=["crafted", "boe"], help="crafted=制造业, boe=装绑")
    p_list.add_argument("--slot", help="部位，例如 head / back / waist")
    p_list.set_defaults(func=cmd_list)

    p_pick = sub.add_parser("pick", help="指定属性，算出绿字")
    p_pick.add_argument("--item", type=int, required=True, help="装备 id")
    p_pick.add_argument("--ilvl", type=int, required=True, help="装等（拍卖行/成品上看到的）")
    p_pick.add_argument("--stats", required=True, help="两个属性，逗号分隔，例如 crit,mastery")
    p_pick.add_argument("--json", action="store_true")
    p_pick.set_defaults(func=cmd_pick)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
