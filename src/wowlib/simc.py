"""simc 字符串解析：从 SimC 插件导出的 profile 里读出角色和全身装备。

用法
----
    python src/wowlib/simc.py tests/my_profile.txt      # 读文件
    python src/wowlib/simc.py -                             # 从标准输入读（粘贴后 Ctrl+Z 回车）
    python src/wowlib/simc.py tests/my_profile.txt --json
    python src/wowlib/simc.py --selftest                    # 跑解析器单元测试

格式要点（决定了实现细节，改动前先读一遍）
------------------------------------------
SimC 的 profile 每行一个 `键=值`：

    warrior="Kelthuzad"
    level=90
    spec=arms
    head=Corroded Hulk's Skullcap,id=249305,bonus_id=6652/12667,ilevel=318,gem_id=240967,enchant_id=7961

1. **`id` + `bonus_id` 才唯一确定一件装备实例**。同一个 id 配不同 bonus_id 就是不同装等 /
   不同属性版本，所以 bonus_id 必须原样保留，不能用 id 反查完就丢。
2. **`ilevel=` 可以直接覆盖最终装等**。这是"规划未来装备"的钥匙：想算"如果我拿到 334 的
   版本"，改这一个数字就行，不用去反推 bonus_id 组合。
3. `bonus_id` / `gem_id` / `crafted_stats` 用 `/` 分隔多个；空宝石孔写作 `gem_id=0`，
   **按位置保留**（`0/240983` 是"第 1 孔空、第 2 孔有"），只在末尾裁掉多余的 0。
4. `### Gear from Bags ###` 之后是背包里的东西，**不是**当前装备，不能算进属性。
5. 装备名字本身可能含逗号（少见但存在），所以不能简单地按逗号切完就取第一段。
"""

import json
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Optional

# 这个文件在 src/<包>/ 下，把 src 本身放进 path 才能 `import wowgear`。
# 不用相对 import：那样 `python src/wowlib/calibrate.py` 直接跑会炸。
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from wowgear.const import CLASSES, ARMOR_CN, SLOT_LABELS, SIMC_SLOTS, SIMC_SLOT_ORDER  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

from wowgear.paths import DB_PATH, RAW, ROOT, TESTS  # noqa: E402

CLASS_KEYS = set(CLASSES)

# 主属性有歧义的职业：同一个职业不同专精用的主属性不一样，光看职业名判断会错。
# 例如神圣骑士要智力，防护/惩戒骑士要力量；元素萨满要智力，增强萨满要敏捷。
SPEC_PRIMARY = {
    "paladin": {"holy": "int", "protection": "str", "retribution": "str"},
    "shaman": {"elemental": "int", "enhancement": "agi", "restoration": "int"},
    "druid": {"balance": "int", "feral": "agi", "guardian": "agi", "restoration": "int"},
    "monk": {"brewmaster": "agi", "mistweaver": "int", "windwalker": "agi"},
    "evoker": {"devastation": "int", "preservation": "int", "augmentation": "int"},
    "priest": {"discipline": "int", "holy": "int", "shadow": "int"},
}

# 装备上"力量/智力"这类多形态主属性，实际只出其中一个，按职业/专精收成一个
HYBRID_PRIMARY = {"str_agi_int", "str_agi", "agi_int", "str_int", "any"}
PRIMARY_CODES = {"str", "agi", "int"}
STAT_CN = {"str": "力量", "agi": "敏捷", "int": "智力"}
PRIMARY_CN = {"str": "力量", "agi": "敏捷", "int": "智力",
              "str_int": "力量/智力", "str_agi": "力量/敏捷", "agi_int": "敏捷/智力",
              "str_agi_int": "力量/敏捷/智力", "any": "不限"}

# 这些槽位不校验护甲类型：披风在数据里一律归为布甲，但全职业都能穿
SKIP_ARMOR_CHECK = {"back", "neck", "finger", "trinket"}

# 弓/枪/弩在库里记作 ranged，在 simc 里写在 main_hand，是同一件事，不算槽位错误
SLOT_EQUIV = {"ranged": "main_hand"}

# 衬衣和战袍不带属性，不参与计算
IGNORED_SLOTS = {"shirt", "tabard"}


def _same_slot(db_slot, simc_slot):
    return SLOT_EQUIV.get(db_slot, db_slot) == SLOT_EQUIV.get(simc_slot, simc_slot)


def class_primary(class_key, spec):
    """该角色真正吃的主属性（str/agi/int）。"""
    if not class_key:
        return None
    spec_map = SPEC_PRIMARY.get(class_key, {})
    if spec and spec.lower() in spec_map:
        return spec_map[spec.lower()]
    first = CLASSES[class_key][2][0]           # 例如 str_int
    return first.split("_")[0]

# 这些键是角色信息，不是装备
SCALAR_KEYS = ("level", "spec", "talents", "race", "region", "server", "role",
               "professions", "position", "chat", "world_lag", "attack_power",
               "spell_power", "main_hand_power", "off_hand_power", "healing_power")


# ---------------------------------------------------------------- 数据结构


@dataclass
class EquippedItem:
    slot: str                      # simc 里的键，例如 finger1
    db_slot: Optional[str]         # 库里的槽位，例如 finger
    name: str
    item_id: Optional[int] = None
    ilevel: Optional[int] = None   # simc 明确写出的最终装等
    bonus_ids: tuple = ()
    gem_ids: tuple = ()
    enchant_id: Optional[int] = None
    crafted_stats: tuple = ()      # 制造业自选属性的 stat_id
    crafted_quality: Optional[int] = None
    socket_count: Optional[int] = None
    # 玩家自己用打孔器（Radiant Jewelbinder）开的那一个孔。头/护腕/腰带各限一个，
    # 游戏里不写在物品数据上，所以 simc 导出里没有——只能由玩家在选择器里说一声。
    drilled: int = 0
    # 插件在每件装备上方写 `# 中文名 (装等)`，这是客户端真值。
    # 装等可以用它兜底，名字也能直接拿来显示（数据库里没有中文名）。
    comment_name: str = ""
    comment_ilevel: Optional[int] = None
    extra: dict = field(default_factory=dict)   # 未识别的字段原样保留


@dataclass
class Profile:
    class_key: Optional[str] = None
    char_name: Optional[str] = None
    level: Optional[int] = None
    spec: Optional[str] = None
    talents: Optional[str] = None
    items: dict = field(default_factory=dict)   # slot -> EquippedItem
    scalars: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def class_cn(self):
        return CLASSES.get(self.class_key, ("?",))[0] if self.class_key else "?"


# ---------------------------------------------------------------- 解析


def _to_ints(raw, where, warnings):
    """把 `1/2/3` 或 `1` 拆成整数元组。"""
    out = []
    for chunk in str(raw).split("/"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            warnings.append(f"{where}: 字段值不是数字 {chunk!r}")
    return tuple(out)


def _first_int(fields, key):
    """取某个字段的第一个整数。字段值是列表（同一键可能出现多次）。"""
    raw = fields.get(key)
    if not raw:
        return None
    try:
        return int(str(raw[0]).split("/")[0])
    except ValueError:
        return None


def _trim_gems(ids):
    """裁掉末尾的空孔，保留中间和开头的 0。

    `gem_id=0/240983` 是"第 1 孔空、第 2 孔有宝石"，位置必须留住；
    `gem_id=240967/0` 只是导出时多写了个尾巴，裁掉才和"一颗宝石"等价。
    """
    out = list(ids)
    while out and not out[-1]:
        out.pop()
    return tuple(out)


# 一个字段的位置：逗号后面紧跟 `键=`。用它切分，才能保住装备名里自带的逗号
_FIELD_SPLIT = re.compile(r",(?=[A-Za-z_0-9]+=)")


def parse_item(slot, raw, warnings, comment_name="", comment_ilevel=None):
    """解析一行装备：`名字,id=1,bonus_id=2/3,gem_id=4`。"""
    parts = _FIELD_SPLIT.split(raw)
    name = parts[0].strip()
    fields = {}

    for tok in parts[1:]:
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        key, val = tok.split("=", 1)
        # bonus_id 可能重复出现，用列表累加，最后再合并
        fields.setdefault(key.strip(), []).append(val.strip())

    def merged_ints(key):
        out = []
        for chunk in fields.get(key, []):
            out.extend(_to_ints(chunk, slot, warnings))
        return tuple(out)

    extras = {k: "/".join(v) for k, v in fields.items()
              if k not in ("id", "bonus_id", "gem_id", "enchant_id", "ilevel",
                           "ilvl", "crafted_stats", "crafted_quality",
                           "crafting_quality", "sockets")}

    ilvl = None
    for key in ("ilevel", "ilvl"):
        if key in fields:
            ilvl = _first_int(fields, key)
            break

    return EquippedItem(
        slot=slot,
        db_slot=SIMC_SLOTS.get(slot),
        name=name,
        item_id=_first_int(fields, "id"),
        ilevel=ilvl,
        bonus_ids=merged_ints("bonus_id"),
        # gem_id 是**按孔位排列**的，0 表示这个孔是空的（SimC 自己也是这么导的：
        # engine/item/item.cpp 里 `range::fill(gem_id, 0)` 之后照样把 0 写进 gem_id=）。
        # 早先这里把 0 直接丢掉，于是 `gem_id=0/240983` 会被读成"第 1 孔有宝石"，
        # 宝石位置整个前移一格。末尾的空孔仍然裁掉，不然每件都拖一串 0。
        gem_ids=_trim_gems(merged_ints("gem_id")),
        enchant_id=_first_int(fields, "enchant_id") or None,
        crafted_stats=merged_ints("crafted_stats"),
        # 插件写的是 crafting_quality，早期版本写 crafted_quality，两个都收
        crafted_quality=_first_int(fields, "crafted_quality")
                        or _first_int(fields, "crafting_quality"),
        socket_count=_first_int(fields, "sockets"),
        comment_name=comment_name,
        comment_ilevel=comment_ilevel,
        extra=extras,
    )


def parse(text):
    """把 simc 文本解析成 Profile。"""
    if isinstance(text, bytes):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = text.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = text.decode("utf-8", "replace")

    prof = Profile()
    section = "header"
    pending = (None, None)     # 上一行注释里的 (名字, 装等)，供紧跟其后的装备行使用

    for lineno, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue

        if line.startswith("###"):
            # `### Gear from Bags ###` 之后是背包内容，不是当前装备
            if "bag" in line.lower():
                section = "bags"
            continue

        if line.startswith("#"):
            m = re.search(r"Player:\s*(.+)$", line)
            if m and not prof.char_name:
                prof.char_name = m.group(1).strip()
            # 插件的每条装备上方都有一行 `# 中文名 (321)`，是本机客户端的真值。
            # 名字可直接用来显示，装等可以在 bonus_id 查不到时兜底。
            m = re.match(r"#\s*(.+?)\s*\((\d+)\)\s*$", line)
            if m:
                pending = (m.group(1).strip(), int(m.group(2)))
            continue

        if "=" not in line:
            prof.warnings.append(f"第 {lineno} 行无法解析：{line[:40]}")
            continue

        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()

        if key in SIMC_SLOTS:
            if section == "bags":
                continue
            if key in prof.items:
                prof.warnings.append(f"槽位 {key} 出现两次，用后者覆盖")
            prof.items[key] = parse_item(key, val, prof.warnings, *pending)
            pending = (None, None)
            continue

        if key in CLASS_KEYS and not val.isdigit():
            prof.class_key = key
            prof.char_name = prof.char_name or val.strip('"')
            continue

        if key == "level":
            prof.level = int(val) if val.isdigit() else None
        elif key == "spec":
            prof.spec = val
        elif key == "talents":
            prof.talents = val
        else:
            prof.scalars[key] = val

    return prof


# ---------------------------------------------------------------- 查库


def _row_dict(row):
    return {k: row[k] for k in row.keys()} if row else None


# `redirected_base_stats` 指向的可能是老资料片的装备（实测有一件是 BfA 的），
# 数据库只收了当前资料片，所以查不到。这时回原始数据兜底，只在需要时加载一次。
_RAW_CACHE = None


def _raw_stats(item_id, meta):
    global _RAW_CACHE
    if _RAW_CACHE is None:
        path = os.path.join(RAW, "equippable-items.json")
        try:
            with open(path, encoding="utf-8") as f:
                _RAW_CACHE = {i["id"]: i for i in json.load(f)}
        except OSError:
            _RAW_CACHE = {}
    item = _RAW_CACHE.get(item_id)
    if not item:
        return []
    out = []
    for s in item.get("stats") or []:
        info = meta.get(s.get("id"))
        if not info:
            continue
        out.append({"code": info[0], "label_cn": info[1], "kind": info[2],
                    "alloc": s.get("alloc", 0), "is_crafted_optional": 0})
    return out


def _stat_meta(conn):
    """stat_id -> (code, 中文名, 类别)，兜底路径也要带全，否则同一属性会被拆成两行。"""
    return {r["id"]: (r["code"], r["label_cn"], r["kind"])
            for r in conn.execute("SELECT id, code, label_cn, kind FROM stat_types")}


def normalize_stats(stats, want):
    """把装备的属性行整理成"这个角色实际吃到的属性"。

    要做两件事：
    1. `力量/智力` 这种多形态主属性，实际只出其中一个，按角色主属性收成一个。
    2. 同一件装备可能同时挂了力量行和智力行（职业自适应装备），只能取其一，
       否则会把两种形态都加进去，属性直接虚高一倍。
    """
    out = []
    for s in stats:
        # 多形态主属性只在这个角色真能吃到的属性上收敛。
        # `力量/敏捷/智力` 的披风给战士不该变成"力量"，这类装备直接保持原样，
        # 后面靠 primary_stat 校验去报警。
        if s["code"] in HYBRID_PRIMARY and want and want in s["code"].split("_"):
            s = dict(s, code=want, label_cn=STAT_CN[want])
        out.append(s)

    variants = {s["code"] for s in out
                if s["code"] in PRIMARY_CODES and not s["is_crafted_optional"]}
    if want and len(variants) > 1:
        out = [s for s in out
               if s["code"] not in PRIMARY_CODES or s["code"] == want]

    merged = {}
    for s in out:
        key = (s["code"], s["is_crafted_optional"])
        if key in merged:
            merged[key]["alloc"] += s["alloc"]
        else:
            merged[key] = dict(s)

    order = {"primary": 0, "secondary": 1, "tertiary": 2, "crafted": 3}
    return sorted(merged.values(), key=lambda s: (order.get(s["kind"], 9), s["code"]))


def resolve(conn, prof):
    """把解析出的装备逐件对照数据库，返回 (列表, 统计)。"""
    want = class_primary(prof.class_key, prof.spec)
    out = []
    for slot in SIMC_SLOT_ORDER + [s for s in prof.items if s not in SIMC_SLOT_ORDER]:
        it = prof.items.get(slot)
        if not it:
            continue
        if it.db_slot in IGNORED_SLOTS:
            continue

        row = None
        if it.item_id:
            row = conn.execute("""
                SELECT id, name, name_cn, quality, slot, armor_type, primary_stat,
                       raw_ilevel, category, is_current, item_set_id, has_sockets, inventory_type
                  FROM items WHERE id = ?
            """, (it.item_id,)).fetchone()

        stats = []
        redirect = it.extra.get("redirected_base_stats")
        stat_source = int(redirect) if redirect else it.item_id
        if row and stat_source:
            stats = [dict(r) for r in conn.execute("""
                SELECT t.code, t.label_cn, t.kind, s.alloc, s.is_crafted_optional
                  FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
                 WHERE s.item_id = ? ORDER BY t.kind, t.id
            """, (stat_source,))]
            if not stats and redirect:
                # 目标装备不在库里（老资料片），回原始数据取
                stats = _raw_stats(stat_source, _stat_meta(conn))
            stats = normalize_stats(stats, want)

        # bonus_id 反查升级轨道，用来核对 simc 写的 ilevel 是否自洽
        track = None
        if it.bonus_ids:
            marks = ",".join("?" * len(it.bonus_ids))
            track = conn.execute(f"""
                SELECT track_name, level, max_level, ilevel FROM ilevel_tracks
                 WHERE bonus_id IN ({marks}) LIMIT 1
            """, it.bonus_ids).fetchone()

        flags = []
        # 装等只认三个来源，优先级从高到低：
        #   1. simc 明写的 ilevel（规划未来装备时就靠它）
        #   2. bonus_id 反查升级轨道
        #   3. 插件注释里的 `# 名字 (321)`，客户端真值
        # 绝不回退到 items.raw_ilevel——那是"裸机装等"（S2 团本装备记作 219），
        # 和玩家在游戏里看到的数值差几十级，显示出来只会误导。
        track_ilevel = track["ilevel"] if track else None
        ilevel = it.ilevel or track_ilevel or it.comment_ilevel
        if not ilevel:
            flags.append("缺装等：simc 没写 ilevel，bonus_id 与注释都对不上")
        if not it.item_id:
            flags.append("没有 id，无法查库")
        elif not row:
            flags.append("库中没有这件装备")
        else:
            if it.name and row["name"].lower() != it.name.lower():
                flags.append(f"名字不符（库中为 {row['name']}）")
            if not row["is_current"]:
                flags.append(f"非本赛季（{row['category']}）")
            armor = row["armor_type"]
            if (armor and prof.class_key
                    and (it.db_slot or row["slot"]) not in SKIP_ARMOR_CHECK):
                allowed = CLASSES[prof.class_key][1]
                if armor not in allowed:
                    flags.append(f"护甲不符（{ARMOR_CN.get(armor, armor)}，该职业只能穿"
                                 f"{'/'.join(ARMOR_CN.get(a, a) for a in allowed)}）")
            if it.db_slot and row["slot"] and not _same_slot(row["slot"], it.db_slot):
                flags.append(f"槽位不符（库中为 {row['slot']}）")
            prim = row["primary_stat"]
            if prim and prof.class_key:
                allowed_p = CLASSES[prof.class_key][2]
                if prim not in allowed_p:
                    flags.append(f"主属性不符（这件是{PRIMARY_CN.get(prim, prim)}，"
                                 f"该职业吃{PRIMARY_CN.get(allowed_p[0], allowed_p[0])}）")
        # 两个装等来源都在时互相核对，对不上说明数据有一边是旧的
        if it.ilevel and track_ilevel and it.ilevel != track_ilevel:
            flags.append(f"装等与升级轨道不一致（bonus 指向 "
                         f"{track['track_name']}{track['level']}={track_ilevel}，"
                         f"simc 写 {it.ilevel}）")
        elif track_ilevel and it.comment_ilevel and track_ilevel != it.comment_ilevel:
            flags.append(f"装等与注释不一致（bonus 指向 {track_ilevel}，"
                         f"注释写 {it.comment_ilevel}）")

        notes = []
        if row and row["category"] == "catalyst":
            notes.append("套装转化件：绿字取自源装备，本身属性表不可信（用 tiers.py 查来源）")
        if redirect:
            # 12.x 新字段：这件装备的属性表来自另一件装备。用自身 id 取属性会取错
            # 次要属性（实测有 4 件装备凭空多出急速），必须按目标装备取。
            notes.append(f"属性取自装备 {stat_source}（redirected_base_stats）")

        out.append({
            "item": it,
            "db": _row_dict(row),
            "stats": stats,
            "track": _row_dict(track),
            "ilevel": ilevel,
            # 双手武器和单手武器预算差一倍多，算属性时要靠它分档
            "inventory_type": row["inventory_type"] if row else None,
            "flags": flags,
            "notes": notes,
        })

    found = sum(1 for r in out if r["db"])
    return out, {"total": len(out), "found": found, "missing": len(out) - found}


# ---------------------------------------------------------------- 输出


def _w(s):
    """按中日韩字符占两列来算显示宽度，否则表格会歪。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, width, align="left"):
    s = str(s)
    gap = width - _w(s)
    if gap <= 0:
        return s
    return s + " " * gap if align == "left" else " " * gap + s


def _stats_text(stats):
    if not stats:
        return "—"
    shown = [s for s in stats if not s["is_crafted_optional"]]
    optional = [s for s in stats if s["is_crafted_optional"]]
    # 制造业的自选槽没有固定数值，显示数字会误导（alloc 是"整块预算"，不是属性）
    txt = " ".join(f"{s['label_cn']}{s['alloc']:,}" for s in shown) or "—"
    if optional:
        txt += f"   +自选×{len(optional)}"
    return txt


def _slot_label(slot):
    base = slot.rstrip("12") if slot[-1:].isdigit() else slot
    label = SLOT_LABELS.get(base, (slot, 99))[0]
    if slot[-1:].isdigit():
        label += slot[-1]
    return label


def print_report(prof, rows, summary):
    print("=" * 78)
    print(f"角色  {prof.class_key or '?'}（{prof.class_cn}）"
          f"    {prof.level or '?'} 级    spec={prof.spec or '?'}    {prof.char_name or ''}")
    print(f"装备  {summary['total']} 件，库中命中 {summary['found']} 件，"
          f"缺失 {summary['missing']} 件")
    print("=" * 78)

    head = f"{pad('槽位', 8)}{pad('装备', 40)}{pad('装等', 6)}{pad('状态', 6)}属性分配"
    print(head)
    print("-" * 78)
    for r in rows:
        it, db = r["item"], r["db"]
        # 中文名优先用库里的 name_cn，其次用插件注释里的名字（那是客户端本地化名），
        # 最后才回退英文名
        if db:
            name = db["name_cn"] or it.comment_name or db["name"]
        else:
            name = it.comment_name or it.name or "?"
        mark = "✗" if not db else ("!" if r["flags"] else "✓")
        print(f"{pad(_slot_label(it.slot), 8)}{pad(name[:38], 40)}"
              f"{pad(r['ilevel'] or '?', 6)}"
              f"{pad(mark, 6)}{_stats_text(r['stats'])}")
        if it.gem_ids or it.enchant_id or it.crafted_stats:
            extra = []
            if it.gem_ids:
                # 空孔（0）留在数据里是对的，但打出来"宝石 0/240983"看着像 bug
                extra.append("宝石 " + "/".join(
                    str(g) if g else "空" for g in it.gem_ids))
            if it.enchant_id:
                extra.append(f"附魔 {it.enchant_id}")
            if it.crafted_stats:
                extra.append("自选属性 " + "/".join(map(str, it.crafted_stats)))
            print(f"{'':8}{'└ ' + '  '.join(extra)}")
        for f in r["flags"]:
            print(f"{'':8}⚠ {f}")
        for n in r.get("notes", []):
            print(f"{'':8}· {n}")

    print("-" * 78)
    print("图例  ✓ 正常     ! 有告警（说明在行下）     ✗ 库中没有这件装备")
    print("属性合计（alloc 是装备内的属性权重，万分比）：")
    totals = {}
    for r in rows:
        for s in r["stats"]:
            if s["is_crafted_optional"]:
                continue
            totals[s["label_cn"]] = totals.get(s["label_cn"], 0) + s["alloc"]
    if totals:
        print("   " + "   ".join(f"{k} {v:,}" for k, v in
                                 sorted(totals.items(), key=lambda kv: -kv[1])))
    else:
        print("   （没有可汇总的属性）")
    levels = [r["ilevel"] for r in rows if r["ilevel"]]
    span = f"，装等跨度 {min(levels)}-{max(levels)}" if levels else ""
    print(f"   注：alloc 是装备内万分比，跨装等直接相加会低估高装等装备{span}。")
    print("       换算成面板数值要乘「装等预算」，见 README 待办。")

    if prof.warnings:
        print("-" * 78)
        print("解析告警：")
        for w in prof.warnings:
            print(f"  ! {w}")


# ---------------------------------------------------------------- 自测


def selftest():
    """跑 tests/test_simc.py。

    用例集中在那一个文件里，避免同一个断言在源码和测试里各写一份——
    两份期望值一旦漂移，测试会"通过"但代码是错的。
    """
    import subprocess
    path = os.path.join(TESTS, "test_simc.py")
    if not os.path.exists(path):
        print(f"找不到测试文件 {path}")
        return 2
    return subprocess.call([sys.executable, path])

# ---------------------------------------------------------------- 入口


def main(argv):
    if "--selftest" in argv:
        return selftest()

    as_json = "--json" in argv
    args = [a for a in argv if not a.startswith("--")]

    if "--text" in argv:
        text = argv[argv.index("--text") + 1]
    elif args:
        path = args[0]
        if path == "-":
            text = sys.stdin.buffer.read()
        else:
            with open(path, "rb") as f:
                text = f.read()
    else:
        print(__doc__)
        return 2

    prof = parse(text)
    if not prof.items:
        print("没有解析到任何装备行。确认贴进来的是 SimC 插件导出的 profile。")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows, summary = resolve(conn, prof)
    conn.close()

    if as_json:
        payload = {
            "class": prof.class_key, "level": prof.level, "spec": prof.spec,
            "name": prof.char_name,
            "items": [{
                "slot": r["item"].slot, "db_slot": r["item"].db_slot,
                "name": r["db"]["name"] if r["db"] else r["item"].name,
                "name_cn": (r["db"]["name_cn"] if r["db"] else None)
                           or r["item"].comment_name or None,
                "id": r["item"].item_id, "ilevel": r["ilevel"],
                "bonus_ids": list(r["item"].bonus_ids),
                "gem_ids": list(r["item"].gem_ids),
                "enchant_id": r["item"].enchant_id,
                "track": r["track"],
                "in_db": bool(r["db"]), "is_current": bool(r["db"] and r["db"]["is_current"]),
                "stats": r["stats"], "flags": r["flags"],
            } for r in rows],
            "summary": summary, "warnings": prof.warnings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print_report(prof, rows, summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
