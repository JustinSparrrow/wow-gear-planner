"""自定义装备管理工具

官方装备来自 Raidbots 数据，但总有覆盖不到的：装绑、世界掉落、任务奖励、
下个版本的新内容、或者你只想给某件装备补个备注。这个工具把这类装备补进
数据库，和官方装备同表（is_custom = 1），前端无需特殊处理。

属性录入用"属性代码=数值"的形式，直接在游戏面板上读到多少就填多少：
    --stats str=1500,sta=3000,crit=800,haste=600
可用代码见 `stat_types` 表：str/agi/int/sta/crit/haste/mastery/vers
（自定义装备存的是绝对属性值，不参与官方装备的装等缩放）

用法：
    python src/manage_items.py add --name "熔火护腕" --slot wrist \\
        --stats str=1200,sta=2400,haste=900 --icon inv_bracer_plate_raidwarrior_d_01 \\
        --source "装绑 / 拍卖行"
    python src/manage_items.py list
    python src/manage_items.py icons --search plate --local
    python src/manage_items.py remove 9000001
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "data", "wowgear.db")
ICON_DIR = os.path.join(ROOT, "data", "icons")

CUSTOM_ID_BASE = 9000000
SLOTS = ["head", "neck", "shoulder", "back", "chest", "wrist", "hands",
         "waist", "legs", "feet", "finger", "trinket", "main_hand", "off_hand"]


def connect():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"数据库不存在：{DB_PATH}\n请先运行 python src/build_db.py")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def cmd_add(args):
    db = connect()
    codes = {r["code"]: r["id"] for r in db.execute("SELECT id, code FROM stat_types")}

    stats = {}
    for part in (args.stats or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SystemExit(f"属性格式错误：{part}，应为 代码=数值")
        code, val = part.split("=", 1)
        code = code.strip().lower()
        if code not in codes:
            raise SystemExit(f"未知属性代码：{code}\n可用：{', '.join(sorted(codes))}")
        stats[codes[code]] = int(val)

    if not stats:
        raise SystemExit("至少要给一个属性，例：--stats str=1500,crit=800")

    if args.icon:
        hit = db.execute("SELECT name FROM icons WHERE name = ?", (args.icon,)).fetchone()
        if not hit:
            print(f"提示：图标名 {args.icon} 不在游戏图标库中，仍然写入（界面会显示占位）")
        elif not os.path.exists(os.path.join(ICON_DIR, args.icon + ".png")):
            print(f"提示：本地还没有 {args.icon}.png，可运行 fetch_icons.py --all 补齐")

    new_id = (db.execute(
        "SELECT MAX(id) FROM items WHERE is_custom = 1").fetchone()[0] or CUSTOM_ID_BASE - 1) + 1

    db.execute("""INSERT INTO items(
        id, name, icon, quality, item_class, item_sub_class, inventory_type, slot,
        raw_ilevel, expansion, season, category, is_current, item_set_id, has_sockets,
        socket_info, specs, classes, is_custom, custom_note, created_at
      ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        new_id, args.name, args.icon, args.quality,
        4 if args.slot not in ("main_hand", "off_hand", "trinket") else 4,
        None, None, args.slot, args.ilevel, 11, "custom", "custom", 1,
        None, 0, None, None, None, 1, args.source,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))
    db.executemany("INSERT INTO item_stats VALUES(?,?,?,0)",
                   [(new_id, sid, val) for sid, val in stats.items()])
    if args.source:
        db.execute("INSERT INTO item_sources VALUES(?,?,?,?,?,?,?)",
                   (new_id, None, None, args.source, None, "custom", "custom"))
    db.commit()

    desc = " ".join(f"{r['label_cn']}:{r['alloc']}" for r in db.execute(
        """SELECT t.label_cn, s.alloc FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
           WHERE s.item_id = ?""", (new_id,)))
    print(f"已添加自定义装备 #{new_id}  {args.name}  [{args.slot}]  {desc}")
    db.close()


def cmd_list(args):
    db = connect()
    rows = db.execute("""SELECT i.id, i.name, i.slot, i.icon, i.custom_note,
               (SELECT GROUP_CONCAT(t.label_cn || ':' || s.alloc, ' ')
                  FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
                 WHERE s.item_id = i.id) AS stats_text
          FROM items i WHERE i.is_custom = 1 ORDER BY i.id""").fetchall()
    if not rows:
        print("还没有自定义装备。用 add 子命令添加。")
        return
    print(f"共 {len(rows)} 件自定义装备：\n")
    for r in rows:
        local = "有图" if os.path.exists(os.path.join(ICON_DIR, (r["icon"] or "") + ".png")) else "缺图"
        print(f"  #{r['id']}  {r['name'][:24]:26} {r['slot']:10} {local}  {r['stats_text'] or ''}")
        if r["custom_note"]:
            print(f"        来源：{r['custom_note']}")
    db.close()


def cmd_remove(args):
    db = connect()
    row = db.execute("SELECT name, is_custom FROM items WHERE id = ?", (args.id,)).fetchone()
    if not row:
        raise SystemExit(f"找不到装备 #{args.id}")
    if not row["is_custom"]:
        raise SystemExit(f"#{args.id} 是官方装备，不允许通过此工具删除")
    db.execute("DELETE FROM item_stats WHERE item_id = ?", (args.id,))
    db.execute("DELETE FROM item_sources WHERE item_id = ?", (args.id,))
    db.execute("DELETE FROM items WHERE id = ?", (args.id,))
    db.commit()
    print(f"已删除 #{args.id} {row['name']}")
    db.close()


def cmd_icons(args):
    """列出可选图标。--local 只列已下载的，否则按名称搜索全库。"""
    db = connect()
    if args.local:
        files = sorted(f[:-4] for f in os.listdir(ICON_DIR) if f.endswith(".png"))
        hits = [f for f in files if not args.search or args.search.lower() in f.lower()]
        print(f"本地已有 {len(hits)} 个图标：")
    else:
        q = "%" + (args.search or "") + "%"
        hits = [r[0] for r in db.execute(
            "SELECT name FROM icons WHERE name LIKE ? ORDER BY name LIMIT ?",
            (q, args.limit))]
        print(f"图标库匹配 {len(hits)} 个（限 {args.limit}）：")
    for h in hits:
        mark = "" if os.path.exists(os.path.join(ICON_DIR, h + ".png")) else "  (未下载)"
        print(f"  {h}{mark}")
    if not args.local and not args.search:
        print("提示：加 --search 关键词缩小范围，例如 --search plate")
    db.close()


def main():
    p = argparse.ArgumentParser(description="魔兽装备库 · 自定义装备管理")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="添加自定义装备")
    a.add_argument("--name", required=True, help="装备名称")
    a.add_argument("--slot", required=True, choices=SLOTS, help="装备槽位")
    a.add_argument("--stats", required=True, help="属性，如 str=1500,crit=800")
    a.add_argument("--icon", default=None, help="图标名，可用 icons 子命令查")
    a.add_argument("--quality", type=int, default=4, help="品质 0-6，默认 4 史诗")
    a.add_argument("--ilevel", type=int, default=None, help="装等")
    a.add_argument("--source", default=None, help="来源说明，如 装绑 / 拍卖行")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出自定义装备")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("remove", help="删除自定义装备")
    r.add_argument("id", type=int)
    r.set_defaults(func=cmd_remove)

    i = sub.add_parser("icons", help="浏览图标库")
    i.add_argument("--search", default=None)
    i.add_argument("--local", action="store_true", help="只看已下载的")
    i.add_argument("--limit", type=int, default=40)
    i.set_defaults(func=cmd_icons)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
