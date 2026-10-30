"""套装转化（催化）的绿字追踪。

机制
----
本赛季节气装备可以通过「催化转化」变成套装件，**绿字原样保留**。所以：

- 套装件自己的属性表是**不可信的**：同 id 的套装头，从不同源装备转化过来，绿字完全不同。
- simc 导出里的 `redirected_base_stats` 就是**源装备 id**——这是正向链路，能直接读出"这件是从哪来的"。
- 反过来，只有绿字时（比如你想换一件"暴击精通的套装头"），得查**同部位哪些可转化源装备能给这对绿字**。

用法
----
    python src/build/tiers.py --profile tests/my_profile.txt
        列出身上每件套装件的源装备；源不在当前赛季时，用绿字反查最接近的候选。

    python src/build/tiers.py --slot head --stats crit,mastery
        反查：头部哪些当前赛季装备能给暴击+精通。

    python src/build/tiers.py --slot head --list
        列出头部全部可转化源及其绿字组合。
"""

import argparse
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "data", "wowgear.db")

# 这个文件在 src/build/ 下，把 src 本身放进 path 才能 `from wowlib import simc`。
# 以前这里直接 `import simc`，是扁平时代的写法，挪进 build/ 之后一跑就 ModuleNotFoundError。
_SRC = os.path.join(ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 能被催化转化的来源类别（制造业/装绑不参与转化，PVP 也不参与）
CONVERTIBLE = ("raid", "dungeon", "delve", "prey", "world")

SLOT_CN = {"head": "头部", "shoulder": "肩部", "chest": "胸部", "hands": "手部",
           "legs": "腿部", "feet": "脚部", "back": "背部", "wrist": "手腕",
           "waist": "腰部"}


def secondaries(conn, item_id):
    """返回 {code: alloc}，只含次要属性。"""
    rows = conn.execute("""
        SELECT t.code, s.alloc
          FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
         WHERE s.item_id = ? AND t.kind = 'secondary'
    """, (item_id,)).fetchall()
    return {r["code"]: r["alloc"] for r in rows}


def secondaries_any(conn, item_id):
    """先查库，查不到再回原始数据（源装备可能是老资料片的，库里没有）。"""
    st = secondaries(conn, item_id)
    if st:
        return st
    import simc
    raw = simc._raw_stats(item_id, simc._stat_meta(conn))
    return {r["code"]: r["alloc"] for r in raw if r.get("kind") == "secondary"}


def shares(stats):
    """归一成占比，用占比比绝对值可比（不同装备预算不同）。"""
    total = sum(stats.values())
    if not total:
        return {}
    return {k: v / total for k, v in stats.items()}


def distance(a, b):
    """两个绿字组合的差距，0 表示比例完全一致。取各属性占比差的一半。"""
    keys = set(a) | set(b)
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys) / 2


def candidates(conn, slot):
    """该部位当前赛季所有可转化源装备。"""
    marks = ",".join("?" * len(CONVERTIBLE))
    return conn.execute(f"""
        SELECT i.id, i.name, i.name_cn, i.category, i.quality
          FROM items i
         WHERE i.is_current = 1 AND i.slot = ? AND i.item_set_id IS NULL
           AND i.category IN ({marks})
         ORDER BY i.quality DESC, i.name
    """, (slot, *CONVERTIBLE)).fetchall()


def rank_sources(conn, slot, want_stats, top=5, exclude=()):
    """按绿字占比排序，找最接近 want 的源装备。"""
    want = shares(want_stats)
    scored = []
    for row in candidates(conn, slot):
        if row["id"] in exclude:
            continue
        got = secondaries(conn, row["id"])
        if not got:
            continue
        scored.append((distance(want, shares(got)), row, got))
    scored.sort(key=lambda x: x[0])
    return scored[:top]


def pct_text(stats):
    s = shares(stats)
    if not s:
        return "—"
    cn = {"crit": "暴击", "haste": "急速", "mastery": "精通", "vers": "全能"}
    return " ".join(f"{cn.get(k, k)}{v * 100:.0f}%" for k, v in
                    sorted(s.items(), key=lambda kv: -kv[1]))


def show_profile(path):
    from wowlib import simc

    with open(path, encoding="utf-8") as f:
        prof = simc.parse(f.read())
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("=" * 76)
    print("套装转化追踪 —— 转化的绿字来自源装备，套装件自身属性表不可信")
    print("=" * 76)
    print(f"{'部位':6}{'身上这件':26}{'源装备':26}绿字")

    for slot, it in prof.items.items():
        if not it.item_id:
            continue
        row = conn.execute("SELECT * FROM items WHERE id = ?", (it.item_id,)).fetchone()
        if not row or row["category"] != "catalyst":
            continue

        src = it.extra.get("redirected_base_stats")
        src_id = int(src) if src else None
        src_row = conn.execute("SELECT * FROM items WHERE id = ?", (src_id,)).fetchone() if src_id else None
        stats = secondaries_any(conn, src_id) if src_id else secondaries(conn, it.item_id)

        name = (row["name_cn"] or row["name"])[:24]
        if src_row:
            src_name = (src_row["name_cn"] or src_row["name"])[:24]
        elif src_id:
            src_name = f"id={src_id}（非本赛季）"
        else:
            src_name = "导出里没有"
        print(f"{SLOT_CN.get(row['slot'], row['slot']):6}{name:26}{src_name:26}{pct_text(stats)}")

        # 源不在当前赛季时，给几个"能刷到的同类绿字"
        if not src_row and stats:
            near = rank_sources(conn, row["slot"], stats, top=2)
            for dist, cand, cand_stats in near:
                label = (cand["name_cn"] or cand["name"])[:22]
                print(f"{'':6}↳ 当前赛季最接近：{label:24}{pct_text(cand_stats)}"
                      f"  差 {dist * 100:.1f}%")
    conn.close()


def show_slot(slot, want_codes=None, list_all=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if list_all or not want_codes:
        rows = candidates(conn, slot)
        print(f"{SLOT_CN.get(slot, slot)} 当前赛季可转化源 {len(rows)} 件：")
        seen = {}
        for r in rows:
            st = secondaries(conn, r["id"])
            key = tuple(sorted(shares(st).items())) if st else ()
            seen.setdefault(key, []).append((r, st))
        for key, group in sorted(seen.items(), key=lambda kv: -len(kv[1])):
            r, st = group[0]
            name = (r["name_cn"] or r["name"])[:30]
            print(f"  {pct_text(st):26} {len(group)} 件，例如 {name}（{r['category']}）")
    else:
        # 只有属性名没比例时，按"有没有这两个属性"筛
        want = {c: 1 for c in want_codes}
        print(f"{SLOT_CN.get(slot, slot)} 同时带 {'+'.join(want_codes)} 的可转化源：")
        for r in candidates(conn, slot):
            st = secondaries(conn, r["id"])
            if st and set(st) == set(want_codes):
                name = (r["name_cn"] or r["name"])[:30]
                print(f"  {name:32}{pct_text(st)}  {r['category']}")
    conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="套装转化的绿字追踪")
    ap.add_argument("--profile", help="simc 导出文件，列出身上套装件的源装备")
    ap.add_argument("--slot", help="部位，例如 head")
    ap.add_argument("--stats", help="反查绿字，例如 crit,mastery")
    ap.add_argument("--list", action="store_true", help="列出该部位全部可转化源")
    args = ap.parse_args(argv)

    if not os.path.exists(DB_PATH):
        print("找不到数据库，先跑 python main.py --build")
        return 2
    if args.profile:
        show_profile(args.profile)
    elif args.slot:
        codes = args.stats.split(",") if args.stats else None
        show_slot(args.slot, codes, args.list)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
