"""魔兽世界配装规划器 —— 主入口

用法
----
    python main.py                  交互式（默认，用 tests/my_profile.txt）
    python main.py 你的导出.txt      交互式，载入指定 simc 导出
    python main.py --web            启动网页界面
    python main.py --test           跑测试
    python main.py --build          从原始数据重建数据库

交互式里能做四件事：看当前配装、换单件装备看属性增减、查装绑/制造业的绿字、
追踪套装件的源装备。所有数值复用 calibrate.compute，不另算一套。

为什么这里不再实现一遍计算
------------------------
换装模拟最怕"预览用一套算法、结算用另一套"，两边一旦漂移，界面显示 +186 而实际
只有 +120，而且这种错误极难发现。所以预览走的是和最终结果完全相同的函数，
只是把装备临时换上、算完再放回去。
"""

import argparse
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from wowlib.simc import parse, EquippedItem, pad                  # noqa: E402
from wowlib.calibrate import load_budget, compute                 # noqa: E402
from wowgear.const import CLASSES, SLOT_LABELS, SIMC_SLOTS, SIMC_SLOT_ORDER  # noqa: E402
from wowlib.stat_picker import slots_of, SECONDARIES              # noqa: E402
from wowlib.session import Session, load_choices, save_choices, connect, CHOICE_PATH  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = os.path.join(HERE, "data", "wowgear.db")
DEFAULT_PROFILE = os.path.join(HERE, "tests", "my_profile.txt")

STAT_CN = {"agi": "敏捷", "str": "力量", "int": "智力", "sta": "耐力",
           "crit": "暴击", "haste": "急速", "mastery": "精通", "vers": "全能"}
STAT_ID_OF = {"crit": 32, "haste": 36, "vers": 40, "mastery": 49}
STAT_CN_OF_ID = {v: k for k, v in STAT_ID_OF.items()}
SHOW_ORDER = ["agi", "str", "int", "sta", "crit", "haste", "mastery", "vers"]
KIND_CN = {"crafted": "制造业", "boe": "装绑"}
QUALITY_CN = {0: "粗糙", 1: "普通", 2: "优秀", 3: "精良", 4: "史诗", 5: "传说", 6: "神器"}

# 配装用不到的分类：PVP、普通难度副本、非本赛季
EXCLUDE_CATEGORIES = ("pvp-conquest", "pvp-honor", "pvp-world", "dungeon_normal", "other")
LINE = "─" * 70


# ---------------------------------------------------------------- 展示


def stat_lines(totals, base=None, indent=2):
    """属性表。给了 base 就显示增减，用于换装预览。"""
    pad_s = " " * indent
    out = []
    for code in SHOW_ORDER:
        value = totals.get(code, 0)
        if not value and not (base or {}).get(code):
            continue
        label = STAT_CN.get(code, code)
        delta = ""
        if base is not None:
            diff = value - base.get(code, 0)
            if abs(diff) >= 0.5:
                delta = f"  {'+' if diff > 0 else ''}{diff:,.0f}"
        out.append(f"{pad_s}{pad(label, 8)}{value:>10,.0f}{delta}")
    return out


def header(session):
    prof = session.prof
    name = prof.char_name or "未命名"
    # 战袍和衬衣在 simc 里有，但不参与属性计算，不能算进"装备 N 件"
    equipped = [it for k, it in prof.items.items() if it.db_slot not in ("shirt", "tabard")]
    pending = [it for it in equipped
               if it.item_id and _has_slots(session.conn, it.item_id)
               and it.item_id not in session.choices]

    print()
    print(LINE)
    print(f"  {prof.class_cn}（{prof.class_key}）  {prof.level or '?'} 级  "
          f"{prof.spec or '未写专精'}  {name}")
    print(f"  装备 {len(equipped)} 件" + (f"   告警 {len(prof.warnings)} 条" if prof.warnings else ""))
    if pending:
        print(f"  ⚠ {len(pending)} 件制造业/装绑的属性还没确认，数值会偏（用 fix 改）")
    print(LINE)
    for line in stat_lines(session.totals):
        print(line)
    # 不写清楚的话，敏捷比游戏面板少一截会被当成算错。少的是宝石、附魔和套装，
    # 这些还没进计算；主属性与耐力另外还有一层职业倍率没收敛。
    print(f"  {'（装备本体；未含宝石/附魔/套装）':>52}")
    print(LINE)


def _has_slots(conn, item_id):
    return bool(slots_of(conn, item_id))


def item_row_text(r):
    parts = [pad(str(r["id"]), 9), pad((r["name"] or "")[:34], 35)]
    parts.append(pad(QUALITY_CN.get(r["quality"], "?"), 5))
    kind = KIND_CN.get(r["craft_kind"], "")
    if kind:
        parts.append(f"[{kind}] ")
    return "".join(parts)


# ---------------------------------------------------------------- 功能


def fetch_candidates(session, db_slot):
    """当前职业能穿、本赛季能拿到的该部位装备。"""
    armor, prim = CLASSES[session.prof.class_key][1], CLASSES[session.prof.class_key][2]
    marks = ",".join("?" * len(EXCLUDE_CATEGORIES))
    sql = f"""
        SELECT id, name, name_cn, quality, category, craft_kind, expansion, raw_ilevel
          FROM items
         WHERE is_current = 1 AND slot = ?
           AND category NOT IN ({marks})
           AND (armor_type IS NULL OR armor_type IN ({",".join("?" * len(armor))}))
           AND (primary_stat IS NULL OR primary_stat IN ({",".join("?" * len(prim))}))
         ORDER BY quality DESC, category, name
    """
    return session.conn.execute(
        sql, (db_slot, *EXCLUDE_CATEGORIES, *armor, *prim)).fetchall()


def ask(prompt, default=""):
    try:
        got = input(prompt).strip()
    except EOFError:
        raise SystemExit
    return got or default


def choose_stats(conn, item_id):
    """让用户给装绑/制造业选两个属性，返回 (stat_id 元组, 可读说明)。"""
    slots = slots_of(conn, item_id)
    n = len(slots)
    total = sum(a for _, a in slots)

    print(f"\n  这件装备有 {n} 个自选槽，属性由你决定：")
    for i, (_, alloc) in enumerate(slots, 1):
        print(f"    槽 {i}  {alloc}（{alloc * 100 / total:.0f}%）")
    print(f"\n  可选属性：{'  '.join(f'{k}={STAT_CN[k]}' for k in STAT_ID_OF)}")

    if n == 1:
        while True:
            raw = ask("  选一个属性（如 crit）：").lower()
            if raw in STAT_ID_OF:
                return (STAT_ID_OF[raw],), STAT_CN[raw]
            print("  输入无效，重来")

    while True:
        raw = ask("  选两个属性，用逗号分隔（如 crit,mastery）：").lower()
        picked = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
        if len(picked) == 2 and all(p in STAT_ID_OF for p in picked):
            if picked[0] == picked[1]:
                print("  两个槽不能选同一个属性")
                continue
            return (tuple(STAT_ID_OF[p] for p in picked),
                    f"{STAT_CN[picked[0]]} + {STAT_CN[picked[1]]}")
        print("  要给两个不同的属性")


def do_equip(session, slot_key):
    db_slot = SIMC_SLOTS.get(slot_key, slot_key)
    label = SLOT_LABELS.get(db_slot, (db_slot, 0))[0]
    cands = fetch_candidates(session, db_slot)
    if not cands:
        print(f"\n  {label}没有可换的装备")
        return

    cur = session.prof.items.get(slot_key)
    cur_name = cur.comment_name or cur.name if cur else "（空）"
    cur_ilevel = (cur.comment_ilevel or cur.ilevel) if cur else None

    print()
    print(LINE)
    print(f"  {label} —— 当前：{cur_name}（装等 {cur_ilevel or '?'}）")
    print(LINE)
    for idx, r in enumerate(cands[:24], 1):
        mark = " ←" if cur and r["id"] == cur.item_id else ""
        print(f"  {idx:>2}. {item_row_text(r)}{mark}")
    if len(cands) > 24:
        print(f"  … 还有 {len(cands) - 24} 件，输入 s 关键词 搜索")

    raw = ask("\n  编号 / s 关键词 / 回车返回：").strip()
    if not raw:
        return
    if raw.lower().startswith("s "):
        kw = raw[2:].strip().lower()
        hits = [r for r in cands if kw in (r["name"] or "").lower()]
        if not hits:
            print("  没搜到")
            return
        for idx, r in enumerate(hits[:24], 1):
            mark = " ←" if cur and r["id"] == cur.item_id else ""
            print(f"  {idx:>2}. {item_row_text(r)}{mark}")
        raw = ask("  编号：").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= min(len(hits), 24):
            return
        chosen = hits[int(raw) - 1]
    else:
        if not raw.isdigit() or not 1 <= int(raw) <= min(len(cands), 24):
            print("  编号无效")
            return
        chosen = cands[int(raw) - 1]

    item_id = chosen["id"]
    ilevel = cur_ilevel or 1
    crafted = ()

    if chosen["craft_kind"]:
        # 装绑和制造业：装等与属性都得问，库里没有
        print(f"\n  {chosen['name']} 是{KIND_CN[chosen['craft_kind']]}，装等和属性需要你指定")
        raw_ilvl = ask(f"  装等（回车用当前 {cur_ilevel or 318}）：", str(cur_ilevel or 318))
        if raw_ilvl.isdigit() and int(raw_ilvl) in session.budget:
            ilevel = int(raw_ilvl)
        else:
            print("  装等无效，用当前值")
        crafted, desc = choose_stats(session.conn, item_id)
        print(f"  属性：{desc}")
    else:
        # 普通装备：装等沿用当前部位的，这样比的是"同装等横向替换"
        if chosen["category"] != "profession":
            raw_ilvl = ask(f"  按哪个装等算（回车用当前 {cur_ilevel or 318}）：",
                           str(cur_ilevel or 318))
            if raw_ilvl.isdigit() and int(raw_ilvl) in session.budget:
                ilevel = int(raw_ilvl)

    try:
        new_totals = session.preview(slot_key, item_id, ilevel, crafted)
    except Exception as exc:                      # 数据缺失不该让整个程序退出
        print(f"  预览失败：{exc}")
        return

    print()
    print(LINE)
    print(f"  替换预览：{cur_name} → {chosen['name']}（装等 {ilevel}）")
    print(LINE)
    for line in stat_lines(new_totals, session.totals):
        print(line)
    print(LINE)

    if ask("  换上这件？(y/N)：").lower() in ("y", "yes"):
        session.equip(slot_key, item_id, ilevel, crafted)
        print("  已换上")
    else:
        print("  取消")


def do_lookup(session):
    """查装绑 / 制造业在指定装等下的绿字。"""
    if not session.budget:
        # 这张表在 data/raw 里，内测包不带；缺了就别让人对着 KeyError 发愣
        print("\n  这个版本没带 RandPropPoints 表（data/raw），lookup 用不了。")
        return
    print("\n  1. 装绑  2. 制造业")
    kind = {"1": "boe", "2": "crafted"}.get(ask("  选类型：").strip())
    if not kind:
        return
    rows = session.conn.execute("""
        SELECT id, name, slot FROM items
         WHERE is_current = 1 AND craft_kind = ? ORDER BY slot, name
    """, (kind,)).fetchall()
    if not rows:
        print("  没有这类装备")
        return
    for idx, r in enumerate(rows[:30], 1):
        print(f"  {idx:>2}. {pad(str(r['id']), 9)}{pad((r['name'] or '')[:34], 35)}"
              f"{SLOT_LABELS.get(r['slot'], (r['slot'], 0))[0]}")
    raw = ask("\n  编号：").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= min(len(rows), 30):
        return
    item = rows[int(raw) - 1]
    ilevel = ask("  装等（回车 318）：", "318")
    ilevel = int(ilevel) if ilevel.isdigit() and int(ilevel) in session.budget else 318
    crafted, desc = choose_stats(session.conn, item["id"])

    budget = session.budget
    from wowlib.calibrate import SLOT_COLUMN
    slots = slots_of(session.conn, item["id"])
    factor = budget[ilevel][SLOT_COLUMN.get(item["slot"], 2)] * 1e-4
    total_alloc = sum(a for _, a in slots)
    print()
    print(LINE)
    print(f"  {item['name']}  {KIND_CN[kind]} · 装等 {ilevel}")
    print(LINE)
    total = 0
    for idx, (_, alloc) in enumerate(slots):
        code = STAT_CN_OF_ID.get(crafted[idx])
        if not code:
            continue
        value = alloc * factor
        total += value
        print(f"  {pad(STAT_CN[code], 8)}{value:>9,.0f}   ({alloc * 100 / total_alloc:.0f}%)")
    print(f"  {pad('合计', 8)}{total:>9,.0f}")
    print(LINE)


def do_tiers(session):
    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "src", "build", "tiers.py"),
         "--profile", DEFAULT_PROFILE],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(out.stdout or out.stderr)


def do_fix(session):
    """逐件确认身上制造业/装绑的自选属性，存下来下次自动套用。"""
    targets = [it for it in session.prof.items.values()
               if it.item_id and _has_slots(session.conn, it.item_id)]
    if not targets:
        print("\n  身上没有需要指定属性的装备")
        return

    print()
    print(LINE)
    print(f"  逐件确认自选属性（{len(targets)} 件）")
    print(LINE)
    for it in targets:
        row = session.conn.execute("SELECT name FROM items WHERE id = ?",
                                   (it.item_id,)).fetchone()
        name = it.comment_name or (row["name"] if row else it.name)
        n_slots = len(slots_of(session.conn, it.item_id))
        cur = session.choices.get(it.item_id) or it.crafted_stats
        cur_txt = " + ".join(STAT_CN.get(STAT_CN_OF_ID.get(s, ""), str(s)) for s in cur) or "未指定"

        hint = "选一个属性" if n_slots == 1 else "选两个属性，逗号分隔"
        print(f"\n  {name}  id={it.item_id}  ({n_slots} 个槽)")
        print(f"    当前：{cur_txt}")
        raw = ask(f"    {hint}（如 crit,mastery，回车跳过）：").strip().lower()
        if not raw:
            continue

        picked = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
        ok = all(p in STAT_ID_OF for p in picked) and len(picked) == n_slots
        if ok and len(set(picked)) == len(picked):
            session.choices[it.item_id] = tuple(STAT_ID_OF[p] for p in picked)
            it.crafted_stats = session.choices[it.item_id]
            print("    已更新")
        else:
            print(f"    输入无效（需要 {n_slots} 个不同属性），跳过")

    save_choices(session.choices)
    session.recompute()
    print(f"\n  已保存到 {os.path.relpath(CHOICE_PATH, HERE)}，属性已重算")


# ---------------------------------------------------------------- 主循环


COMMANDS = """
  命令
    s <部位>   换装备，如 s head / s finger1 / s main_hand
    stat       详细属性（含每件装备的贡献）
    fix        确认身上制造业/装绑的自选属性（改完自动记住）
    lookup     查装绑 / 制造业在指定装等下的绿字
    tiers      套装转化追踪（看套装件来自哪件源装备）
    export     导出当前配装为 simc 字符串
    web        启动网页界面
    q          退出
"""


def repl(session):
    while True:
        header(session)
        print(COMMANDS)
        raw = ask("  > ").strip().lower()
        if not raw:
            continue
        if raw in ("q", "quit", "exit"):
            return 0
        if raw == "stat":
            print()
            for d in session.detail:
                got = " ".join(f"{STAT_CN.get(k, k)}{v:.0f}"
                               for k, v in sorted(d["got"].items()))
                # simc 的槽位是 finger1/finger2/trinket1/trinket2，库里统一成 finger/trinket，
                # 直接用原槽位名查不到中文，会退化成 "trinket1" 并把 id 挤在一起
                db_slot = SIMC_SLOTS.get(d["slot"], d["slot"])
                slot_cn = SLOT_LABELS.get(db_slot, (db_slot, 0))[0]
                if d["slot"][-1].isdigit():
                    slot_cn += d["slot"][-1]
                print(f"  {pad(slot_cn, 9)}{pad(str(d['item_id'] or '?'), 9)}"
                      f"装等 {pad(str(d['ilevel'] or '?'), 5)}{got}")
            continue
        if raw == "lookup":
            do_lookup(session)
            continue
        if raw == "fix":
            do_fix(session)
            continue
        if raw == "tiers":
            do_tiers(session)
            continue
        if raw == "export":
            print()
            for k, v in session.prof.items.items():
                if v.db_slot in ("shirt", "tabard"):
                    continue
                # profile 里装备行的名字通常是空的，中文名在注释里，导出时补回去
                parts = [v.comment_name or v.name or "", f"id={v.item_id}"]
                if v.ilevel:
                    parts.append(f"ilevel={v.ilevel}")
                if v.crafted_stats:
                    parts.append("crafted_stats=" + "/".join(str(s) for s in v.crafted_stats))
                print(f"  {k}=" + ",".join(parts))
            continue
        if raw == "web":
            print("  启动网页界面，浏览器打开 http://127.0.0.1:8765 （Ctrl+C 停止）")
            subprocess.call([sys.executable, os.path.join(HERE, "src", "server", "serve.py")])
            continue
        if raw.startswith("s "):
            slot_key = raw[2:].strip()
            if slot_key not in SIMC_SLOTS:
                print(f"  不认识的部位 {slot_key}，可用：{', '.join(SIMC_SLOT_ORDER)}")
                continue
            do_equip(session, slot_key)
            continue
        print("  不认识这个命令")


# ---------------------------------------------------------------- 入口


def start_interactive(profile_path):
    if not os.path.exists(DB_PATH):
        print("找不到数据库，先跑 python main.py build")
        return 1
    if not profile_path:
        print("没有指定 simc 导出，用样例：" + os.path.relpath(DEFAULT_PROFILE, HERE))
        profile_path = DEFAULT_PROFILE
    if not os.path.exists(profile_path):
        print(f"找不到 {profile_path}")
        return 1

    with open(profile_path, encoding="utf-8") as f:
        prof = parse(f.read())
    if not prof.class_key:
        print("这份导出里没有识别到职业，检查第一行是不是 `warrior=\"名字\"` 这种格式")
        return 1

    # 导出里带的 crafted_stats 和玩家实际选的不一致（实测 4 件全错），
    # 只用确认过的那份覆盖，没确认过的保留原值并在界面上标出来。
    choices = load_choices()
    for it in prof.items.values():
        if it.item_id in choices:
            it.crafted_stats = choices[it.item_id]

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return repl(Session(conn, prof, load_budget(), choices))
    finally:
        conn.close()


def report_size():
    """列出各处占了多少空间，并指出哪些是可以删的。

    data/raw 是建库的原料（57MB），建完库其实用不到了，但删了下次 --build
    要重新下；data/icons 是离线图标缓存，删了界面会变成空框。
    这两块的性质不一样，不能一句"清理"就都删掉。
    """
    import glob

    def du(path):
        if os.path.isfile(path):
            return os.path.getsize(path), 1
        total, n = 0, 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    n += 1
                except OSError:
                    pass
        return total, n

    def fmt(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.1f} {unit}"
            n /= 1024

    rows = [
        ("data/raw", "建库原料，建完可删（删了下次 --build 要重下）", "可删"),
        ("data/icons", "装备图标缓存，删了界面变空框", "保留"),
        ("data/wowgear.db", "游戏数据，可 --build 重建", "保留"),
        ("data/accounts.db", "账号 / 历史 / 配装存档，删了就没了", "别删"),
        ("src/web/img", "入口页底图", "保留"),
        ("src/web", "前端代码", "保留"),
        ("src", "Python 代码", "保留"),
        ("tests", "测试与样例", "保留"),
        ("ref", "SimC 源码片段，查公式用", "可删"),
    ]
    print(f"{'路径':<20}{'大小':>10}{'文件':>7}   说明")
    print("-" * 78)
    total = 0
    for path, note, tag in rows:
        full = os.path.join(HERE, path)
        if not os.path.exists(full):
            continue
        size, n = du(full)
        total += size if not path.startswith("src") or path == "src" else 0
        print(f"{path:<20}{fmt(size):>10}{n:>7}   {note}")
    print("-" * 78)
    print(f"整个项目 {fmt(du(HERE)[0])}（含 .git 与 __pycache__）")
    pyc = sum(os.path.getsize(p) for p in glob.glob(
        os.path.join(HERE, "**", "__pycache__", "*.pyc"), recursive=True))
    if pyc:
        print(f"其中 __pycache__ {fmt(pyc)}，可以随手删")
    print("\ndata/raw 占了绝大部分体积，用完 --build 之后删掉不影响日常使用。")
    return 0


def run_update(proxy=""):
    """只刷新过期的数据，代码一行不动。

    流程：查最新 build → 对比清单 → 逐个跑对应的抓取脚本 → 重建库 → 记账。
    抓取脚本失败（断网、wowhead 403）不中断，其余数据集照常更新，
    最后把失败的列出来让你补。
    """
    import json as _json
    vscript = os.path.join(HERE, "src", "build", "versions.py")
    bdir = os.path.join(HERE, "src", "build")
    common = ["--proxy", proxy] if proxy else []

    print("检查版本…")
    out = subprocess.run([sys.executable, vscript, "--json", *common],
                         capture_output=True, text=True, encoding="utf-8")
    try:
        info = _json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("版本检查失败：")
        print(out.stdout or out.stderr)
        return 1
    latest = info.get("latest") or ""
    stale = info.get("stale") or []
    if not latest:
        print("查不到最新 build（没网或代理没开），先跑 --check-update 看看。")
        return 1
    if not stale:
        print(f"已经是最新版本（{latest}），不用更新。")
        return 0

    print(f"最新 build {latest}，需要刷新：{', '.join(stale)}\n")
    done, failed, manual = [], [], []
    for key in stale:
        script = (info.get("scripts") or {}).get(key)
        if not script:
            manual.append(key)
            continue
        path = os.path.join(bdir, script)
        if not os.path.exists(path):
            failed.append((key, f"缺少 {script}"))
            continue
        print(f"→ {key}（{script}）")
        code = subprocess.call([sys.executable, path, *common])
        if code == 0:
            done.append(key)
        else:
            failed.append((key, f"{script} 退出码 {code}"))

    print("\n重建数据库…")
    # 服务在跑的时候 sqlite 文件被占用，Windows 上会直接 PermissionError。
    # 与其甩 traceback，不如提前说清楚。
    import socket as _socket
    with _socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8765)) == 0:
            print("网页服务还开着（8765 被占用），数据库锁住了。")
            print("先关掉 --web 的窗口，再跑一次 --update。")
            return 1
    if subprocess.call([sys.executable, os.path.join(bdir, "build_db.py")]) != 0:
        print("建库失败，先别用旧库更新过的数据。")
        return 1
    for key in done:
        subprocess.call([sys.executable, vscript, "--mark", key, latest])
    print(f"\n完成：{len(done)} 个数据集已更新到 {latest}")
    for key, why in failed:
        print(f"  ⚠ {key}：{why}")
    if failed:
        print("这些可以手动跑对应脚本，或者忽略——它们不影响其它数据。")
    if manual:
        print(f"  需人工核对：{', '.join(manual)}"
              "（没有自动抓取脚本，去对应来源看一眼数值有没有变）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="魔兽世界配装规划器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("profile", nargs="?", help="simc 导出文件，不填就用 tests/my_profile.txt")
    ap.add_argument("--web", action="store_true", help="启动网页界面")
    ap.add_argument("--test", action="store_true", help="跑测试")
    ap.add_argument("--build", action="store_true", help="重建数据库")
    ap.add_argument("--size", action="store_true", help="看各处占了多少空间")
    ap.add_argument("--check-update", action="store_true",
                    help="对比游戏版本，看哪些数据过期了")
    ap.add_argument("--update", action="store_true",
                    help="只刷新过期的数据，代码不动")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""),
                    help="下载用代理，例如 http://127.0.0.1:7897")
    args = ap.parse_args(argv)

    if args.check_update or args.update:
        script = os.path.join(HERE, "src", "build", "versions.py")
        if not os.path.exists(script):
            print("这个版本不带更新脚本（内测包），数据已经打进去了。")
            return 1
        if args.check_update:
            return subprocess.call([sys.executable, script, "--check",
                                    *(["--proxy", args.proxy] if args.proxy else [])])
        return run_update(args.proxy)

    if args.size:
        return report_size()
    if args.build:
        # 内测包故意没带 data/raw（57MB）和构建脚本，这里给一句人话，
        # 别甩一个 "No such file" 的 traceback 让人以为程序坏了
        script = os.path.join(HERE, "src", "build", "build_db.py")
        if not os.path.exists(script):
            print("这个版本不带建库脚本——内测包里是已经建好的数据，直接 --web 用就行。")
            return 1
        return subprocess.call([sys.executable, script])
    if args.test:
        # 两套都跑：解析/计算一套，账号存档一套，任一套挂了整体算失败
        t1 = os.path.join(HERE, "tests", "test_simc.py")
        t2 = os.path.join(HERE, "tests", "test_accounts.py")
        if not (os.path.exists(t1) and os.path.exists(t2)):
            print("这个版本不带测试脚本（内测包），直接 --web 用就行。")
            return 1
        code = subprocess.call([sys.executable, t1])
        code2 = subprocess.call([sys.executable, t2])
        return code or code2
    if args.web:
        # 先看一眼 8765 有没有被占。占了就直接说清楚，别让 socketserver
        # 甩一个 "Address already in use" 的 traceback 让人猜。
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8765)) == 0:
                print("8765 已经有一个服务在跑了，直接用浏览器打开 http://127.0.0.1:8765")
                print("要重启的话先关掉那个窗口")
                return 1
        return subprocess.call(
            [sys.executable, os.path.join(HERE, "src", "server", "serve.py")])
    return start_interactive(args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
