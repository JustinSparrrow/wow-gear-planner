"""装备从哪来：副本 / 团本 / 专业 / 套装转化。

独立成一模块是因为它是纯查询，不持有任何状态，也不需要角色信息——
训练家、网页、命令行想标来源都直接调 `source_of()`。
"""

from .const import PROF_CN

# 类别 -> (中文标签, 前端配色档)
CATEGORY_KIND = {
    "raid": ("团本", "raid"),
    "dungeon": ("M+", "mplus"),
    "dungeon_normal": ("普通副本", "mplus"),
    "delve": ("地下堡", "delve"),
    "catalyst": ("套装转化", "catalyst"),
    "profession": ("制造业", "craft"),
    "prey": ("猎杀", "prey"),
    "pvp-conquest": ("PVP", "pvp"),
    "pvp-honor": ("PVP", "pvp"),
    "pvp-world": ("PVP", "pvp"),
}

# 数据里的伪实例：不是真副本，只是把一批装备归到一起的桶。
# 真正的"哪个副本哪个 boss"在 instance/encounter 都具体的那条记录上。
AGGREGATE_INSTANCES = {
    "Mythic+ Dungeons", "Normal Dungeons", "Heroic Dungeons",
    "Mythic Dungeons", "Timewalking", "Prey Season 1", "Prey Season 2",
    "Delves Season 1", "Delves Season 2",
    "Catalyst Season 1", "Catalyst Season 2",
    "Epic Profession Items", "Rare Profession Items", "PVP Profession Items",
}


def source_of(conn, item_id, category):
    """这件装备的出处：副本/团本名 + boss 名，或者专业/套装名。

    一件装备在 item_sources 里往往有好几条，比如 Blazebound Lieutenant's Helm：
        Ruby Life Pools / Kokia Blazehoof      ← 真正的出处
        Mythic+ Dungeons / Ruby Life Pools     ← 聚合桶，boss 被抹成了副本名
    所以先挑"instance 和 encounter 都是具体名字"的那条，挑不到才用聚合行兜底。
    不做这个筛选，界面上就是一片"Mythic+ Dungeons"，等于什么都没说。
    """
    rows = conn.execute(
        "SELECT instance_name, encounter_name FROM item_sources WHERE item_id = ?",
        (item_id,)).fetchall()

    def score(row):
        inst = row["instance_name"] or ""
        enc = row["encounter_name"] or ""
        s = 0
        if inst and inst not in AGGREGATE_INSTANCES:
            s += 2
        if enc and enc != inst and enc not in AGGREGATE_INSTANCES:
            s += 1
        if enc.startswith("Catalyst -") or enc.startswith("Delves Season"):
            s -= 3
        return s

    best = max(rows, key=score) if rows else None
    label, kind = CATEGORY_KIND.get(category, ("其他", "other"))
    inst = (best["instance_name"] or "") if best else ""
    enc = (best["encounter_name"] or "") if best else ""

    # 制造业：来源是专业，显示中文专业名
    if kind == "craft":
        detail = PROF_CN.get(enc, enc)
        return {"kind": kind, "label": label, "instance": "", "encounter": detail,
                "text": f"制造业 · {detail}" if detail else "制造业"}

    # 套装转化：数据里没有"由哪个副本件转化而来"的对应关系，只能标到套装名
    if kind == "catalyst":
        row = conn.execute(
            "SELECT b.set_name FROM items i JOIN item_set_bonus b "
            "  ON b.set_id = i.item_set_id WHERE i.id = ? LIMIT 1",
            (item_id,)).fetchone()
        set_name = row["set_name"] if row else ""
        return {"kind": kind, "label": label, "instance": "", "encounter": set_name,
                "text": f"套装转化 · {set_name}" if set_name else "套装转化"}

    # 地下堡 / 猎杀：数据只到赛季粒度，没有具体场景名
    if kind in ("delve", "prey"):
        return {"kind": kind, "label": label, "instance": "", "encounter": "",
                "text": label}

    if inst in AGGREGATE_INSTANCES:
        inst = ""
    parts = [p for p in (inst, enc) if p]
    return {"kind": kind, "label": label, "instance": inst, "encounter": enc,
            "text": " · ".join(parts) or label}
