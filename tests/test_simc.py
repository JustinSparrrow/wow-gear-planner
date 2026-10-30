"""simc 解析的验收测试。直接运行：

    python tests/test_simc.py

每一项都会打印「期望 / 实际」，你不需要看代码就能判断对不对。
全过退出码 0，有失败退出码 1。

分三组：
  解析组 —— 纯字符串进出，不碰数据库。验证 SimC 格式的各种写法。
  查库组 —— 拿装备 id 对照数据库，验证命中、告警、装等来源。
  端到端 —— 用 tests/sample_profile.txt 跑完整流程，核对属性合计。
  真实导出 —— 用 tests/my_profile.txt（插件真实导出）验证不写 ilevel 的那套写法。
"""

import io
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.stdout.reconfigure(encoding="utf-8")

from wowlib.simc import parse, resolve, class_primary, normalize_stats  # noqa: E402

DB_PATH = os.path.join(ROOT, "data", "wowgear.db")
SAMPLE = os.path.join(HERE, "sample_profile.txt")   # 人工构造的战士样例
_real = os.path.join(HERE, "my_profile.txt")        # 插件真实导出（不入库）
REAL = _real if os.path.exists(_real) else SAMPLE     # 没有就退回构造样例

results = []   # (组名, 用例名, [(字段, 期望, 实际), ...])


def check(group, name, pairs):
    """登记一个用例。pairs 是 [(字段名, 期望值, 实际值), ...]。"""
    results.append((group, name, pairs))


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------- 解析组


def group_parse():
    p = parse('warrior="Vex"\nlevel=90\nspec=arms\n'
              "head=Voidbent Crown,id=249305,bonus_id=6652/12667,ilevel=318,"
              "gem_id=240967,enchant_id=7961\n")
    it = p.items["head"]
    check("解析", "基本装备行", [
        ("职业", "warrior", p.class_key),
        ("角色名", "Vex", p.char_name),
        ("等级", 90, p.level),
        ("专精", "arms", p.spec),
        ("装备 id", 249305, it.item_id),
        ("装等", 318, it.ilevel),
        ("bonus_id 拆成两个", (6652, 12667), it.bonus_ids),
        ("gem_id", (240967,), it.gem_ids),
        ("enchant_id", 7961, it.enchant_id),
    ])

    p = parse("finger1=Band,id=1\nfinger2=Signet,id=2\ntrinket1=A,id=3\ntrinket2=B,id=4\n")
    check("解析", "戒指饰品分 1/2 槽", [
        ("槽位数量", 4, len(p.items)),
        ("戒指槽归到 finger", "finger", p.items["finger2"].db_slot),
        ("饰品槽归到 trinket", "trinket", p.items["trinket1"].db_slot),
    ])

    p = parse("chest=Shell of the Deep, Unbroken,id=7,ilevel=300\n")
    check("解析", "装备名自带逗号", [
        ("名字要完整保留", "Shell of the Deep, Unbroken", p.items["chest"].name),
        ("id 仍能解析出来", 7, p.items["chest"].item_id),
    ])

    p = parse("head=身上的,id=1\n### Gear from Bags ###\nhead=背包里的,id=2\n")
    check("解析", "背包段不算装备", [
        ("取身上的那件", 1, p.items["head"].item_id),
    ])

    p = parse("wrist=B,id=5,gem_id=0\n")
    check("解析", "空宝石孔 gem_id=0", [
        ("0 不当作宝石", (), p.items["wrist"].gem_ids),
    ])

    # gem_id 是按孔位排列的：0/240967 是"第 1 孔空、第 2 孔有"。
    # 早先这里把 0 丢掉，于是第 2 孔的宝石被读成第 1 孔，位置整个前移。
    p = parse("neck=N,id=6,gem_id=0/240967\nhead=H,id=7,gem_id=240967/0\n")
    check("解析", "空孔占位不丢", [
        ("开头的 0 保留", (0, 240967), p.items["neck"].gem_ids),
        ("末尾的 0 裁掉", (240967,), p.items["head"].gem_ids),
    ])

    p = parse("waist=C,id=11,crafted_stats=32/36,crafted_quality=5\n")
    check("解析", "制造业自选属性", [
        ("自选属性 stat_id", (32, 36), p.items["waist"].crafted_stats),
        ("制造品质", 5, p.items["waist"].crafted_quality),
    ])

    p = parse("\ufeffwarrior=X\r\nlevel=90\r\nhead=A,id=1\r\n")
    check("解析", "BOM 与 CRLF", [
        ("职业不被 BOM 干扰", "warrior", p.class_key),
        ("装备行正常", 1, p.items["head"].item_id),
    ])

    p = parse("head=A,id=1,upgrade=3,sockets=1\n")
    check("解析", "未识别字段保留", [
        ("sockets 识别", 1, p.items["head"].socket_count),
        ("upgrade 原样保留", "3", p.items["head"].extra.get("upgrade")),
    ])

    p = parse("legs=L,id=9\n")
    check("解析", "没写 ilevel 就是 None", [
        ("ilevel", None, p.items["legs"].ilevel),
    ])

    p = parse("这不是 simc\n# 只是注释\n")
    check("解析", "非 simc 输入", [
        ("解析不出装备", 0, len(p.items)),
        ("要给出告警", True, len(p.warnings) > 0),
    ])


# ---------------------------------------------------------------- 查库组


def group_db():
    c = conn()

    p = parse('warrior="T"\nspec=arms\n'
              "head=Aetherlume Sun Guard,id=244746,ilevel=318\n")
    rows, summary = resolve(c, p)
    strs = {(s["label_cn"], s["alloc"]) for s in rows[0]["stats"]}
    optional = [s for s in rows[0]["stats"] if s["is_crafted_optional"]]
    check("查库", "命中的装备", [
        ("库里找到", True, rows[0]["db"] is not None),
        ("属于本赛季", True, rows[0]["db"]["is_current"]),
        ("装等取自 simc", 318, rows[0]["ilevel"]),
        ("有耐力 7889", True, ("耐力", 7889) in strs),
        ("力量/智力 收成力量 5259", True, ("力量", 5259) in strs),
        ("保留 1 个自选槽", 1, len(optional)),
        ("无告警", [], rows[0]["flags"]),
    ])

    p = parse("head=名字写错了,id=244746,ilevel=318\n")
    rows, _ = resolve(c, p)
    check("查库", "名字对不上会告警", [
        ("出现「名字不符」", True, any("名字不符" in f for f in rows[0]["flags"])),
    ])

    p = parse('mage="M"\nspec=fire\nhead=Aetherlume Sun Guard,id=244746,ilevel=318\n')
    rows, _ = resolve(c, p)
    check("查库", "法师穿板甲要告警", [
        ("出现「护甲不符」", True, any("护甲不符" in f for f in rows[0]["flags"])),
    ])

    p = parse('warrior="W"\nspec=arms\nback=Sunfire Cloak,id=239658,ilevel=305\n')
    rows, _ = resolve(c, p)
    check("查库", "战士戴智力披风要告警", [
        ("出现「主属性不符」", True, any("主属性不符" in f for f in rows[0]["flags"])),
    ])

    p = parse("main_hand=无此装备,id=999999999,ilevel=334\n")
    rows, summary = resolve(c, p)
    check("查库", "库里没有的装备", [
        ("db 为空", None, rows[0]["db"]),
        ("出现「库中没有」", True, any("库中没有" in f for f in rows[0]["flags"])),
        ("统计计入缺失", 1, summary["missing"]),
    ])

    p = parse("legs=X,id=268224,bonus_id=12817\n")
    rows, _ = resolve(c, p)
    check("查库", "装等由 bonus_id 反查", [
        ("bonus 12817 -> 装等 266", 266, rows[0]["ilevel"]),
    ])

    p = parse("legs=X,id=268224\n")
    rows, _ = resolve(c, p)
    level = rows[0]["ilevel"]
    check("查库", "没有任何装等来源时不乱猜", [
        ("装等为 None", None, level),
        ("出现「缺装等」", True, any("缺装等" in f for f in rows[0]["flags"])),
    ])

    # 主属性多形态：同一件装备同时挂力量行和智力行，只能取其一
    stats = [dict(code="str", label_cn="力量", kind="primary", alloc=5259, is_crafted_optional=0),
             dict(code="int", label_cn="智力", kind="primary", alloc=16132, is_crafted_optional=0)]
    got = {s["code"]: s["alloc"] for s in normalize_stats(stats, "str")}
    check("查库", "多形态主属性只取一个", [
        ("战士只留力量", {"str": 5259}, got),
    ])

    check("查库", "职业专精决定主属性", [
        ("神圣骑士", "int", class_primary("paladin", "holy")),
        ("防护骑士", "str", class_primary("paladin", "protection")),
        ("增强萨满", "agi", class_primary("shaman", "enhancement")),
        ("元素萨满", "int", class_primary("shaman", "elemental")),
        ("法师", "int", class_primary("mage", "fire")),
        ("战士", "str", class_primary("warrior", "arms")),
    ])

    c.close()


# ---------------------------------------------------------------- 端到端


def group_e2e():
    with open(SAMPLE, encoding="utf-8") as f:
        p = parse(f.read())
    c = conn()
    rows, summary = resolve(c, p)
    c.close()

    totals = {}
    for r in rows:
        for s in r["stats"]:
            if s["is_crafted_optional"]:
                continue
            totals[s["label_cn"]] = totals.get(s["label_cn"], 0) + s["alloc"]

    check("端到端", "样例 profile", [
        ("解析出 16 件（背包段已排除）", 16, len(p.items)),
        ("职业", "warrior", p.class_key),
        ("专精", "arms", p.spec),
        ("库里命中", 15, summary["found"]),
        ("缺失", 1, summary["missing"]),
    ])

    check("端到端", "属性合计", [
        ("耐力", 102557, totals.get("耐力")),
        ("力量", 53997, totals.get("力量")),
        ("精通", 29819, totals.get("精通")),
        ("急速", 26517, totals.get("急速")),
        ("全能", 17724, totals.get("全能")),
        ("暴击", 9606, totals.get("暴击")),
        ("自选槽不计入合计", None, totals.get("自选属性1")),
    ])


# ---------------------------------------------------------------- 真实导出


def group_real():
    """用真实插件的导出验证：这种导出不写 ilevel，装等全靠注释和 bonus_id。"""
    with open(REAL, encoding="utf-8") as f:
        p = parse(f.read())
    c = conn()
    rows, summary = resolve(c, p)
    c.close()

    by_slot = {r["item"].slot: r for r in rows}

    check("真实导出", "整体", [
        ("职业", "hunter", p.class_key),
        ("专精", "beast_mastery", p.spec),
        ("角色名取自注释", "尛柒誒", p.char_name),
        ("战袍不计入装备数", 15, len(rows)),
        ("全部命中数据库", 15, summary["found"]),
        ("没有任何告警", [], [f for r in rows for f in r["flags"]]),
    ])

    check("真实导出", "装等来源", [
        # 头部 bonus_id=12846 在升级轨道里，能直接查到 321
        ("头部靠 bonus_id 反查", 321, by_slot["head"]["ilevel"]),
        # 颈部 bonus_id=12856 不在轨道表里，只能靠注释里的 (341) 兜底
        ("颈部靠注释兜底", 341, by_slot["neck"]["ilevel"]),
        ("主手靠注释兜底", 341, by_slot["main_hand"]["ilevel"]),
    ])

    check("真实导出", "字段与槽位", [
        # 插件写 crafting_quality，不是 crafted_quality
        ("crafting_quality 能读到", 5, p.items["back"].crafted_quality),
        ("自选属性 stat_id", (49, 36), p.items["back"].crafted_stats),
        # 弓在库里记作 ranged，simc 写在 main_hand，不该报槽位不符
        ("弓不算槽位不符", "ranged", by_slot["main_hand"]["db"]["slot"]),
        # 装备行里的名字是空的，中文名只能来自注释
        ("注释里的中文名", "潜伏蝰蛇的渗液之牙", by_slot["head"]["item"].comment_name),
        # 背包段里的装备都被注释掉了，不能算进来
        ("背包段不进装备数", 0, len([s for s in p.items if "bags" in s])),
    ])

    check("真实导出", "多形态主属性", [
        # 头部是 敏捷/智力，猎人应取敏捷
        ("头部取敏捷", ("敏捷", 5259),
         next((s["label_cn"], s["alloc"]) for s in by_slot["head"]["stats"]
              if s["kind"] == "primary")),
        # 披风是 力量/敏捷/智力，猎人仍应取敏捷
        ("披风取敏捷", ("敏捷", 5259),
         next((s["label_cn"], s["alloc"]) for s in by_slot["back"]["stats"]
              if s["kind"] == "primary")),
    ])

    check("真实导出", "属性重定向", [
        # 这件头部的属性表来自 239035（一件 BfA 装备），自身表里是"急速4993+精通2007"，
        # 用错会把玩家的急速凭空抬高 40 倍。修好后应是"暴击+精通"，没有急速。
        ("头部没有急速", False,
         any(s["label_cn"] == "急速" for s in by_slot["head"]["stats"])),
        ("头部有暴击 2750", ("暴击", 2750),
         next((s["label_cn"], s["alloc"]) for s in by_slot["head"]["stats"]
              if s["code"] == "crit")),
        ("头部标注了来源", True,
         any("239035" in n for n in by_slot["head"].get("notes", []))),
        # 全角色的急速只剩颈部那一点，和面板 121 处于同一量级
        ("急速总量很小", True,
         sum(s["alloc"] for r in rows for s in r["stats"]
             if s["code"] == "haste") < 6000),
    ])


# ---------------------------------------------------------------- 套装转化


def group_tiers():
    """套装转化的绿字追踪：套装件自身属性不可信，绿字随源装备。"""
    from build import tiers

    c = conn()
    # 身上那件套装头实际是 精通61%/暴击39%，源装备是 BfA 的（不在库里），
    # 反查应该能给出当前赛季里绿字最接近的几件
    top = tiers.rank_sources(c, "head", {"crit": 2750, "mastery": 4250}, top=3)
    best_dist, best_row, best_stats = top[0]
    n_head = len(tiers.candidates(c, "head"))

    with open(REAL, encoding="utf-8") as f:
        p = parse(f.read())
    rows, _ = resolve(c, p)
    c.close()

    head = next(r for r in rows if r["item"].slot == "head")

    check("套装转化", "套装件识别", [
        ("头部是催化转化件", "catalyst", head["db"]["category"]),
        ("标注了套装转化", True,
         any("套装转化" in n for n in head.get("notes", []))),
    ])

    check("套装转化", "绿字反查源装备", [
        ("最接近的是精通+暴击", {"mastery", "crit"}, set(best_stats)),
        ("占比差距小于 3%", True, best_dist < 0.03),
        ("反查结果是一对绿字", 2, len(best_stats)),
        (f"头部可转化源 {n_head} 件", True, n_head > 10),
    ])


# ---------------------------------------------------------------- 输出


def show(v):
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(str(x) for x in v) + "]" if v else "[]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}:{x}" for k, x in v.items()) + "}"
    return str(v)


def group_gems_enchants():
    """宝石和附魔。

    这两块最容易漏，因为 simc 的 gem_id / enchant_id 一直解析出来了，
    但算属性时没人用，看上去"数据都在"其实一分没加。
    样例角色的绿字差 21 点精通，正好是四颗宝石里的三颗各 7 点。
    """
    from wowlib.calibrate import compute, load_budget as load_bg
    from wowlib.session import Session, connect, load_budget

    c = conn()
    n_gem = c.execute("SELECT COUNT(*) FROM gems").fetchone()[0]
    n_ench = c.execute("SELECT COUNT(*) FROM enchants").fetchone()[0]
    check("宝石附魔", "库里有数据", [
        ("宝石条数 > 50", True, n_gem > 50),
        ("附魔条数 > 20", True, n_ench > 20),
    ])

    # 单颗宝石
    row = c.execute("SELECT stats FROM gems WHERE id = 240908").fetchone()
    check("宝石附魔", "单颗宝石属性", [
        ("Flawless Masterful Garnet", {"crit": 16, "mastery": 7},
         __import__("json").loads(row["stats"])),
    ])

    # 附魔：脚给 186 耐力（第二项用 and 连的那条）
    row = c.execute("SELECT stats, tertiary FROM enchants WHERE id = 7963").fetchone()
    check("宝石附魔", "脚附魔的两项", [
        ("耐力", {"sta": 186}, __import__("json").loads(row["stats"])),
        ("闪避", {"avoid": 15}, __import__("json").loads(row["tertiary"])),
    ])

    # 百分比特效不能当绿字
    row = c.execute("SELECT stats, note FROM enchants WHERE id = 7967").fetchone()
    check("宝石附魔", "暴击效果不算绿字", [
        ("属性为空", {}, __import__("json").loads(row["stats"])),
        ("说明写清了是效果", True, "暴击效果" in (row["note"] or "")
         and "%" in (row["note"] or "")),
    ])

    # 触发类武器附魔：+99 是 proc 值，不能当常驻属性加进面板
    row = c.execute("SELECT stats, note FROM enchants WHERE id = 8041").fetchone()
    check("宝石附魔", "触发类不算常驻", [
        ("属性为空", {}, __import__("json").loads(row["stats"])),
        ("写明了触发和持续时间", True,
         "触发" in (row["note"] or "") and "秒" in (row["note"] or "")),
    ])

    # "+36 主属性"要存成 main，再由计算层落到本职业那条属性上
    row = c.execute("SELECT stats FROM enchants WHERE id = 7987").fetchone()
    check("宝石附魔", "主属性附魔记成 main", [
        ("是 main 36", 36, __import__("json").loads(row["stats"]).get("main")),
    ])

    with io.open(REAL, encoding="utf-8") as f:
        prof = parse(f.read())
    totals, detail = compute(c, prof, load_bg())

    gem_sum, ench_sum = {}, {}
    missing, unknown = [], []
    for d in detail:
        for k, v in d["gems"].items():
            gem_sum[k] = gem_sum.get(k, 0) + v
        for k, v in d["enchant"].items():
            ench_sum[k] = ench_sum.get(k, 0) + v
        if d["gem_missing"]:
            missing.extend(d["gem_missing"])
        if not d["enchant_known"]:
            unknown.append(d["slot"])

    check("宝石附魔", "样例角色宝石合计", [
        ("敏捷（三色宝石）", 32, round(gem_sum.get("agi", 0))),
        ("暴击", 39, round(gem_sum.get("crit", 0))),
        ("精通", 21, round(gem_sum.get("mastery", 0))),
        ("全能", 32, round(gem_sum.get("vers", 0))),
        ("没有认不出的宝石", [], missing),
    ])
    check("宝石附魔", "样例角色附魔合计", [
        # 脚 186 + 腿（森林猎手的护甲片）33：腿部附魔这轮才补上，
        # 之前 8159 不在库里，界面上写的"未收录"
        ("耐力（脚+腿）", 219, round(ench_sum.get("sta", 0))),
        ("敏捷（腿甲片 92 + 胸 36）", 128, round(ench_sum.get("agi", 0))),
        ("都认得", [], sorted(unknown)),
    ])

    # 换装后新装备要沿用原槽位的宝石附魔——不然导出 simc 给 Raidbots 时会少一截
    snap_slot = next(d for d in detail if d["slot"] == "feet")
    check("宝石附魔", "附魔挂在槽位上", [
        ("脚有 186 耐力", 186, round(snap_slot["enchant"].get("sta", 0))),
    ])

    s2 = Session(connect(), parse(io.open(REAL, encoding="utf-8").read()),
                 load_budget(), raw_text=io.open(REAL, encoding="utf-8").read())
    old_gem = list(s2.prof.items["neck"].gem_ids)
    cand = s2.candidates("neck")
    other = next((x for x in cand if x["id"] != s2.prof.items["neck"].item_id), None)
    if other:
        s2.equip("neck", other["id"], other.get("recommend_ilevel") or other["ilevel"])
        check("宝石附魔", "换装继承宝石", [
            ("新项链仍带原宝石", old_gem, list(s2.prof.items["neck"].gem_ids)),
        ])
    c.close()


def group_customize():
    """自选宝石与附魔。

    这部分最容易出的错不是算错，而是"改了没生效"或者"生效了但撤不掉"——
    前者会让人以为功能没做，后者会让人不敢用。
    """
    from wowlib.session import Session, connect, load_budget

    with io.open(REAL, encoding="utf-8") as f:
        text = f.read()
    s = Session(connect(), parse(text), load_budget(), raw_text=text)
    it = s.prof.items["finger1"]
    before_gem = list(it.gem_ids)
    before_mastery = round(s.gear_totals.get("mastery", 0))

    # 换成全能+精通的宝石：精通不变（7→7），全能 +16
    vers0 = round(s.gear_totals.get("vers", 0))
    s.set_gem("finger1", 0, 240918)
    check("自选", "换宝石", [
        ("宝石换成 240918", (240918,), s.prof.items["finger1"].gem_ids),
        ("全能 +16", vers0 + 16, round(s.gear_totals.get("vers", 0))),
    ])

    s.set_gem("finger1", 0, 0)
    check("自选", "拔宝石", [
        ("宝石清空", (), s.prof.items["finger1"].gem_ids),
        ("精通少 7", before_mastery - 7, round(s.gear_totals.get("mastery", 0))),
    ])

    # 孔数是硬限制。戒指这个赛季只有 1 个棱彩孔，第 2 颗必须插不进去——
    # 以前只要 index 不越界就能插，等于每个部位都能凭空塞三颗宝石。
    err = ""
    try:
        s.set_gem("finger1", 1, 240908)
    except ValueError as e:
        err = str(e)
    check("自选", "超出孔数要被拦", [
        ("抛错", True, "插槽" in err),
        ("宝石没变", (), s.prof.items["finger1"].gem_ids),
    ])

    # 头部这个赛季没孔（打孔器是在游戏里用的，导出 simc 会带 sockets=）
    err = ""
    try:
        s.set_gem("head", 0, 240908)
    except ValueError as e:
        err = str(e)
    check("自选", "无孔装备插不进去", [
        ("抛错", True, "没有插槽" in err),
        ("头部仍是空的", (), s.prof.items["head"].gem_ids),
    ])

    # 项链保底一孔；团本掉的 Aqirbane Reliquary 是两孔，
    # 但数据只记到一孔，第二孔靠"玩家已经插了两颗宝石"这条规则补上
    neck = s.prof.items["neck"]
    check("自选", "孔数判定", [
        ("项链两孔（已插两颗）", 2, s.socket_count(neck.item_id, None, neck.gem_ids)),
        ("戒指一孔（保底）", 1, s.socket_count(s.prof.items["finger1"].item_id, None, ())),
        ("头部零孔", 0, s.socket_count(s.prof.items["head"].item_id, None, ())),
        # 已经插着宝石的装备，哪怕库里没记录也必须算有孔
        ("插着宝石就算有孔", 1, s.socket_count(s.prof.items["head"].item_id, None, (240908,))),
    ])

    s.set_gem("finger1", 0, before_gem[0] if before_gem else 240908)
    s.undo()
    check("自选", "撤销宝石", [
        ("回到空", (), s.prof.items["finger1"].gem_ids),
    ])

    # 附魔：戒指能换急速附魔
    s.set_gem("finger1", 0, before_gem[0] if before_gem else 240908)
    haste0 = round(s.gear_totals.get("haste", 0))
    s.set_enchant("finger1", 8021)
    check("自选", "换附魔", [
        ("附魔 = 8021", 8021, s.prof.items["finger1"].enchant_id),
        ("急速 +22", haste0 + 22, round(s.gear_totals.get("haste", 0))),
    ])

    # 跨部位要拦住
    err = ""
    try:
        s.set_enchant("finger1", 7961)      # 7961 是头部附魔
    except ValueError as e:
        err = str(e)
    check("自选", "跨部位附魔要被拦", [
        ("抛错", True, "不能附在" in err),
    ])

    # 移除附魔
    s.set_enchant("finger1", 0)
    check("自选", "移除附魔", [
        ("附魔清空", None, s.prof.items["finger1"].enchant_id),
    ])

    # 导出的 simc 必须跟着宝石走。这里踩过一次：
    # 改宝石后 _changed() 只看装备 id，判定"没换过"，于是原样搬了旧那行，
    # 界面显示宝石换了、导出去的还是旧宝石，Raidbots 跑出来当然不对。
    s2 = Session(connect(), parse(text), load_budget(), raw_text=text)
    s2.set_gem("neck", 1, 240967)
    head_line = [l for l in s2.to_simc().splitlines() if l.startswith("neck=")][0]
    check("自选", "导出跟着宝石走", [
        ("带上了新宝石", True, "gem_id=" in head_line and "240967" in head_line),
    ])

    # 选项接口
    gems = s.gem_options()
    ench = s.enchant_options("finger")
    # 宝石现在按"主要给的属性"分组排序，急速组在最前
    groups = [g["group"] for g in gems]
    check("自选", "选项列表", [
        ("宝石 75 颗", 75, len(gems)),
        ("戒指附魔 9 条", 9, len(ench)),
        ("宝石都有文案", True, all(g["text"] for g in gems)),
        ("每颗都带品种", True, all(g.get("kind_cn") for g in gems)),
        ("每颗都带品质", True, all(g.get("quality") for g in gems)),
        ("每颗都带图标", True, all(g.get("icon") for g in gems)),
        ("急速组排最前", "haste", groups[0]),
    ])


def group_gem_slots():
    """孔位位置、预览、名称。

    这三条都是"看着没坏其实不对"的类型：
    - 拔中间那颗孔，后面的宝石顶上来，数值没错但位置错了，导出的 simc 也错
    - 预览如果落了地，用户鼠标划一圈就改掉了配装
    - 宝石同名不同装等（278/295），不写装等就是两张分不清的卡
    """
    from wowlib.session import Session, connect, load_budget

    with io.open(REAL, encoding="utf-8") as f:
        text = f.read()
    s = Session(connect(), parse(text), load_budget(), raw_text=text)

    neck = s.prof.items["neck"]
    before = list(neck.gem_ids)
    if len(before) >= 2:
        s.set_gem("neck", 0, 0)
        after = list(s.prof.items["neck"].gem_ids)
        check("自选", "拔中间孔不移位", [
            ("第一个孔变空", 0, after[0]),
            ("第二颗还在原位", before[1], after[1]),
            ("长度没缩短", len(before), len(after)),
        ])
        neck_line = next(l for l in s.to_simc().splitlines() if l.startswith("neck="))
        check("自选", "空孔导出成 0", [
            ("gem_id 首位是 0", True, "gem_id=0/" in neck_line),
        ])
        s.set_gem("neck", 0, before[0])
        check("自选", "插回原位", [
            ("复原", tuple(before), s.prof.items["neck"].gem_ids),
        ])

    # 预览不能落地：鼠标划过去一圈，配装不该被改掉
    gem_before = list(s.prof.items["finger1"].gem_ids)
    totals_before = {k: round(v, 1) for k, v in s.totals.items()}
    pv = s.preview_gem("finger1", 0, 240967)
    check("自选", "宝石预览不落地", [
        ("预览算出了合计", True, bool(pv)),
        ("宝石没变", gem_before, list(s.prof.items["finger1"].gem_ids)),
        ("合计没变", totals_before, {k: round(v, 1) for k, v in s.totals.items()}),
    ])

    ench_before = s.prof.items["finger1"].enchant_id
    pv2 = s.preview_enchant("finger1", 8021)
    check("自选", "附魔预览不落地", [
        ("预览算出了合计", True, bool(pv2)),
        ("附魔没变", ench_before, s.prof.items["finger1"].enchant_id),
        ("合计没变", totals_before, {k: round(v, 1) for k, v in s.totals.items()}),
    ])

    gems = s.gem_options()
    check("自选", "宝石带上装等与中文名", [
        ("都有中文名", True, all(g["name"] for g in gems)),
        ("都有装等", True, all(g["ilvl"] for g in gems)),
        ("名字保留英文原名", True, all(g["name_en"] for g in gems)),
        # 同一颗宝石的 278 / 295 两个版本要能区分开。
        # 质量也要算进键里：名字里的"无瑕"被品质标签接手了，
        # 所以"致命榴石 278"会同时有普通和无瑕两条，它们本来就该是两张卡。
        ("名+装等+品质唯一", True,
         len({(g["name"], g["ilvl"], g["quality"]) for g in gems}) == len(gems)),
    ])

    ench = s.enchant_options("finger")
    check("自选", "附魔中文名", [
        ("都不用英文原名", True, all(e["name"] != e["name_en"] for e in ench)),
    ])

    # 某个部位一条附魔都没有时，前端要能说清是"本来就没有"还是"没收录"
    slots = s.enchant_slots()
    check("自选", "附魔覆盖部位", [
        ("腿部有 6 条", 6, slots.get("legs")),
        ("戒指 9 条", 9, slots.get("finger")),
        ("胸 4 条", 4, slots.get("chest")),
    ])

    # 腿部附魔：不是 "Enchant XXX" 卷轴，是护甲片/魔线，之前一条都没入库
    leg_opts = s.enchant_options("legs")
    check("自选", "腿部附魔", [
        ("6 条", 6, len(leg_opts)),
        ("森林猎手的护甲片有属性", True,
         any("敏捷" in (o["text"] or "") for o in leg_opts)),
    ])

    # "+36 主属性"要落到本职业真吃的那条上，否则加进去了也不显示
    agi0 = s.gear_totals.get("agi", 0)
    s.set_enchant("chest", 0)
    check("自选", "主属性附魔落到职业属性", [
        ("胸附魔贡献 +36 敏捷", 36, round(agi0 - s.gear_totals.get("agi", 0))),
    ])

    # 孔位数量看"位置"而不是"空了几颗"：拔掉第一颗之后，第二颗所在的孔必须
    # 还在，否则界面上两个孔会变成一个，第二颗宝石再也点不到
    s10 = Session(connect(), parse(text), load_budget(), raw_text=text)
    neck0 = s10.prof.items["neck"]
    n_before = s10.socket_count(neck0.item_id, neck0.socket_count,
                                neck0.gem_ids, neck0.drilled)
    s10.set_gem("neck", 0, 0)
    neck1 = s10.prof.items["neck"]
    check("自选", "拔一颗孔还在", [
        ("孔数没变", n_before,
         s10.socket_count(neck1.item_id, neck1.socket_count,
                          neck1.gem_ids, neck1.drilled)),
        ("第一孔是空位", 0, neck1.gem_ids[0]),
        ("第二颗还在原位", 240983, neck1.gem_ids[1]),
    ])

    # 唯一宝石（永歌钻石）每人限一颗：不同装等版本算同一颗
    s11 = Session(connect(), parse(text), load_budget(), raw_text=text)
    err = ""
    try:
        s11.set_gem("neck", 0, 240982)      # 278 版，和身上 295 版同组
    except ValueError as e:
        err = str(e)
    check("自选", "唯一宝石每人一颗", [
        ("抛错", True, "只能装一颗" in err),
        ("宝石没被改掉", (240908, 240983), s11.prof.items["neck"].gem_ids),
    ])
    # 同一个孔换成另一个装等版本 = 还是只有一颗，允许
    s11.set_gem("neck", 1, 240982)
    check("自选", "同孔换版本允许", [
        ("换成了 278 版", (240908, 240982), s11.prof.items["neck"].gem_ids),
    ])
    unique = [g for g in s11.gem_options() if g.get("unique_group")]
    check("自选", "唯一宝石带分组", [
        ("8 颗永歌钻石", 8, len(unique)),
        ("费解两版同组", 1,
         len({g["unique_group"] for g in unique
              if "Indecipherable" in (g.get("name_en") or "")})),
    ])

    # 存档只存"改动"。只改宝石、没换装备时，宝石必须算进改动里，
    # 否则存完再载入，宝石悄悄回到导入时那几颗。
    s3 = Session(connect(), parse(text), load_budget(), raw_text=text)
    s3.set_gem("finger1", 0, 240967)
    s3.set_enchant("finger1", 8021)
    ch = s3.changes()
    s4 = Session(connect(), parse(text), load_budget(), raw_text=text)
    s4.apply_changes(ch)
    check("自选", "存档带回宝石附魔", [
        ("宝石", (240967,), s4.prof.items["finger1"].gem_ids),
        ("附魔", 8021, s4.prof.items["finger1"].enchant_id),
        ("改动里带宝石字段", True, any("gems" in c for c in ch)),
    ])

    # 打孔器：头 / 腰带 / 护腕各能用一次，开出来的孔插宝石才算属性
    s5 = Session(connect(), parse(text), load_budget(), raw_text=text)
    head = s5.prof.items["head"]
    base_n = s5.socket_count(head.item_id, head.socket_count, head.gem_ids, head.drilled)
    mastery0 = round(s5.gear_totals.get("mastery", 0))
    s5.set_drilled("head", True)
    head = s5.prof.items["head"]
    check("自选", "打孔器开孔", [
        ("孔数 +1", base_n + 1,
         s5.socket_count(head.item_id, head.socket_count, head.gem_ids, head.drilled)),
        ("标了 drilled", 1, head.drilled),
        ("可以开孔", True, s5.can_drill("head", 0)),
        ("戒指不能开", False, s5.can_drill("finger", 0)),
    ])

    s5.set_gem("head", 0, 240908)
    check("自选", "开出来的孔能插宝石", [
        ("宝石进去了", (240908,), s5.prof.items["head"].gem_ids),
        ("属性跟着涨", mastery0 + 7, round(s5.gear_totals.get("mastery", 0))),
        # 插上宝石之后孔数不能变成 2——宝石数量是"有孔"的证据，
        # 但开出来的那个孔已经算在 drilled 里了
        ("孔数还是 1", base_n + 1,
         s5.socket_count(s5.prof.items["head"].item_id, None,
                         s5.prof.items["head"].gem_ids, s5.prof.items["head"].drilled)),
    ])

    err = ""
    try:
        s5.set_drilled("head", False)     # 孔里还插着宝石
    except ValueError as e:
        err = str(e)
    check("自选", "有宝石不能撤孔", [
        ("抛错", True, "先" in err),
        ("孔还在", 1, s5.prof.items["head"].drilled),
    ])

    s5.set_gem("head", 0, 0)
    s5.set_drilled("head", False)
    check("自选", "撤孔", [
        ("drilled 归零", 0, s5.prof.items["head"].drilled),
        ("孔数回到原样", base_n,
         s5.socket_count(s5.prof.items["head"].item_id, None, (), 0)),
    ])

    err = ""
    try:
        s5.set_drilled("finger1", True)
    except ValueError as e:
        err = str(e)
    check("自选", "只有头腰腕能开孔", [
        ("抛错", True, "打孔器" in err),
    ])

    # simc 格式里表达不了额外插槽（SimC 的 item 选项里没有 sockets=），
    # 所以导出不能写 sockets=，否则 Raidbots 那边会因为不认识的字段报错
    s5.set_drilled("head", True)
    head_line = next(l for l in s5.to_simc().splitlines() if l.startswith("head="))
    check("自选", "额外孔不写进 simc", [
        ("没有 sockets=", False, "sockets=" in head_line),
    ])

    # Raidbots 的「Load from SimC Addon」靠插件头认人。早先 to_simc 把所有
    # `#` 开头的行都 continue 掉了，装备一行没错，整段却被判 Unverified Input。
    s8 = Session(connect(), parse(text), load_budget(), raw_text=text)
    exported = s8.to_simc().splitlines()
    check("自选", "导出保留插件头", [
        ("有 SimC Addon 标识", True,
         any(l.startswith("# SimC Addon") for l in exported)),
        ("有 Checksum", True,
         any(l.startswith("# Checksum") for l in exported)),
        ("空行也保留", True, "" in exported),
    ])

    # 换过的部位：注释里那行 `# 中文名 (装等)` 也要跟着改，
    # 否则把导出的文本再导回来，界面上显示的还是旧装备名字
    s9 = Session(connect(), parse(text), load_budget(), raw_text=text)
    s9.equip("head", 268230, 334)
    lines = s9.to_simc().splitlines()
    idx = next(i for i, l in enumerate(lines) if l.startswith("head="))
    check("自选", "换装后注释同步", [
        ("注释放到新装等", True, lines[idx - 1].strip() == "# 永恒毒牙之冠 (334)"),
        ("下一行是头部装备", True, lines[idx].startswith("head=")),
    ])

    # **装备行不能写名字。** simc 的 engine/item/item.cpp 里，同一行同时给了
    # 名字和 id 时会拿名字和自己 DBC 的比，不一致就 sim->error，整个模拟失败。
    # Raidbots 跑的就是 simc，失败表现成 "input is not valid"——装备一行没错却进不去。
    # 游戏插件正是因此才只吐 `head=,id=271492`（中文客户端的名字 simc 必然不认）。
    check("自选", "导出行不写名字", [
        ("head 行名字留空", True, lines[idx].startswith("head=,id=")),
        ("行内没有装备名", False, "Crown" in lines[idx] or "永恒" in lines[idx]),
        ("id 还在", True, f"id=268230" in lines[idx]),
    ])

    # 导出的文本要能原样导回来：这是用户"导出→再导入"的日常路径
    back = Session(connect(), parse("\n".join(exported)), load_budget())
    check("自选", "导出能导回来", [
        ("头部还是同一件", s8.prof.items["head"].item_id,
         back.prof.items["head"].item_id),
        ("宝石没丢", tuple(s8.prof.items["finger1"].gem_ids),
         tuple(back.prof.items["finger1"].gem_ids)),
        ("解析零告警", 0, len(back.prof.warnings)),
    ])

    # 插件在结尾写 `# Checksum: <adler32>`，Raidbots 拿它当"这是插件原始输出"的
    # 凭据。改过装备之后必须按新内容重算，否则整段会被判 input is not valid。
    import zlib as _zlib

    def _checksum_ok(t):
        i = t.rindex("# Checksum: ")
        claimed = int(t[i + len("# Checksum: "):].strip(), 16)
        return _zlib.adler32(t[:i].encode("utf-8")) & 0xFFFFFFFF == claimed

    orig_sum = text.rsplit("# Checksum: ", 1)[1].strip()
    changed_text = s9.to_simc()
    check("自选", "导出校验和", [
        # 一件没动时必须与原文逐字节一致（这是"不破坏插件格式"的硬指标）
        ("未改动与原文一字不差", True, s8.to_simc() == text),
        ("未改动时校验和自洽", True, _checksum_ok(s8.to_simc())),
        # 改过装备后原值必然对不上。重算一个等于伪造"这是插件原样输出"的凭证，
        # 而且无从自证；所以干脆不带，让它按普通 profile 走（Quick Sim 吃这个）。
        ("改过后不再带 checksum", False, "# Checksum" in changed_text),
        ("原值确实变了（因为我们没抄它）", True, orig_sum not in changed_text),
    ])

    # 换过的装备要补进 `### Gear from Bags` 的注释里——那一段是 Raidbots 的
    # "可选装备池"，Top Gear 就是从这里枚举的。不补的话：换上的那件游戏里没有、
    # 原来那件又被换掉了不在身上，两头都看不见，"原 vs 新"压根比不了。
    check("自选", "背包补进换装候选", [
        ("换上的那件进了背包注释", True, "# head=,id=268230" in changed_text),
        ("原来那件也进了", True, "# head=,id=271492" in changed_text),
        ("原件的 bonus_id 没丢", True, "id=271492,bonus_id=6652/13696" in changed_text),
        ("注释行带中文名", True, "# 永恒毒牙之冠 (334)" in changed_text),
        # 一件没动时必须一个字都不插，否则"导出与原文逐字节一致"就不成立了
        ("没改动时不插", False, "# head=,id=271492" in s8.to_simc()),
    ])


def group_update():
    """版本清单：判断"数据是不是这一代的"，而不是每次重做整个项目。

    这里只测离线逻辑（版本号比较、清单结构）；联网查最新 build 的部分
    由 `main.py --check-update` 负责，测试里不打网络。
    """
    from build import versions as V

    check("更新", "版本号比较", [
        ("同大版本不算过期", True, V.major("12.1.5.70575") == V.major("12.1.7.70668")),
        ("跨大版本算过期", False, V.major("12.0.1.63000") == V.major("12.1.7.70668")),
        ("12.1.7 比 12.1.5 新", True, V._key("12.1.7.70668") > V._key("12.1.5.70575")),
    ])
    data = V.read()
    sets = data.get("datasets") or {}
    check("更新", "清单覆盖所有数据集", [
        ("7 个数据集", 7, len(sets)),
        ("装备有脚本", "fetch_static.py", V.DATASETS["items"][1]),
        ("换算率人工核对", None, V.DATASETS["rating_curves"][1]),
        ("每条都有版本号", True,
         all((sets.get(k) or {}).get("build") for k in V.DATASETS)),
    ])


def group_curves():
    """绿字换算：递减档位 + 每专精精通系数。

    验证锚点是用户提供的**无 buff** 守护德面板（2026-10-31）：
    暴击 1010=31.96%、急速 1294=30.70%、全能 419=19.76%、精通 252=13.63%。
    这些是"数据 → 模型 → 对上面板"，不是拿面板去调常数：
    换算率 46/44/54 和档位表来自 Maxroll 12.0.1，精通点数公式来自游戏（8 点基础）。
    """
    from wowlib.session import dr_convert, Session, connect, load_budget

    s = Session(connect(), parse(io.open(SAMPLE, encoding="utf-8").read()),
                load_budget())
    curve = s.rating_curves()
    check("换算", "曲线数据在库里", [
        ("有曲线", True, bool(curve)),
        ("暴击 46/1%", 46.0, curve["base_rate"]["crit"]),
        ("急速 44/1%", 44.0, curve["base_rate"]["haste"]),
        ("全能 54/1%", 54.0, curve["base_rate"]["vers"]),
        ("精通基础 8 点", 8.0, curve["mastery_base_points"]),
    ])

    # 递减：1320 急速之后那 264 点只按 90% 计 → (1320 + 237.6) / 44 = 35.4%
    check("换算", "递减档位", [
        ("1320+264 = 35.4%", 35.4,
         round(dr_convert(1320 + 264, curve["dr"]["haste"]) / 44.0, 2)),
        ("门槛以下不打折", 30.0,
         round(dr_convert(1320, curve["dr"]["haste"]) / 44.0, 2)),
    ])

    # 曲线部分（不含角色自带的固定加成）：守护德 1010 暴击 → 21.96%，
    # 面板上的 31.96% 多出的 10 点是这个专精自带的，由面板反推（见下一条）
    check("换算", "曲线部分", [
        ("暴击 1010 → 21.96", 21.96, round(s._curve_pct("crit", 1010), 2)),
        ("全能 419 → 7.76", 7.76, round(s._curve_pct("vers", 419), 2)),
        ("急速 1294 → 29.41", 29.41, round(s._curve_pct("haste", 1294), 2)),
    ])

    # 精通 = (8 点基础 + 评级/46) × 专精系数；守护德面板显示的是攻强那条（1.0）
    check("换算", "精通点数公式", [
        ("守护 252 → 13.48", 13.48, round((8 + 252 / 46.0) * 1.0, 2)),
        ("射击 1142 → 20.52", 20.52, round((8 + 1142 / 46.0) * 0.625, 2)),
    ])

    # 端到端：带面板的守护德，四项必须和游戏逐字对上
    import tempfile
    import wowgear.store as store
    tmp = os.path.join(tempfile.mkdtemp(), "panel.json")
    old = store.PANEL_PATH
    store.PANEL_PATH = tmp
    try:
        store.save_panel("druid:guardian", {
            "agi": 2382, "sta": 63243,
            "crit": 1010, "haste": 1294, "mastery": 252, "vers": 419,
            "pct": {"crit": 31.96, "haste": 30.70, "mastery": 13.63, "vers": 19.76},
        })
        gtext = 'druid="测试"\nlevel=90\nspec=guardian\nrace=tauren\n'
        g = Session(connect(), parse(gtext), load_budget(), raw_text=gtext)
        check("换算", "守护德四项对上面板", [
            # 精通这 8.15 里有 8 点是客户端的基础精通点数。旧版把它塞在曲线里，
            # 于是这里只剩 0.15 的残差——看着像"守护德没有精通加成"，其实是藏起来了。
            ("自带的固定加成", {"crit": 10.0, "haste": 1.29, "mastery": 8.15, "vers": 12.0},
             g._flat),
            ("暴击", 31.96, round(g.stat_pct("crit", 1010), 2)),
            ("急速", 30.70, round(g.stat_pct("haste", 1294), 2)),
            ("全能", 19.76, round(g.stat_pct("vers", 419), 2)),
            ("精通", 13.63, round(g.stat_pct("mastery", 252), 2)),
        ])
    finally:
        store.PANEL_PATH = old

    # 专精系数表：40 个专精都在，猎人和守护的值和面板/客户端对得上
    rows = {r["spec"]: r["coeff"] for r in
            connect().execute("SELECT spec, coeff FROM spec_mastery")}
    check("换算", "专精精通系数", [
        ("40 个专精", 40, len(rows)),
        ("兽王 1.9", 1.9, rows.get("hunter:beast_mastery")),
        ("射击 0.625", 0.625, rows.get("hunter:marksmanship")),
        ("守护 1.0", 1.0, rows.get("druid:guardian")),
    ])


def group_spec_base():
    """专精基线表：没填面板也能算出绿字百分比。

    动机：原来「装备之外的固定百分比」只能从玩家面板反推，等于每个角色
    手工标定一次，换个专精就归零。存成表之后没填面板也有数。

    判定这批数可不可信，门槛只有一条——**同一专精换装备后反推的基线不变**。
    生存猎 2026-10-31 换一件装备的两套面板就是验收样本：
    暴击 1033→1084、精通 1636→1585，反推的基线四项漂移 ≤0.002。
    如果公式里混进了和评级相关的东西，这里立刻就会炸。
    """
    from wowlib.session import Session, connect, load_budget, dr_convert
    conn = connect()
    text = io.open(REAL, encoding="utf-8").read()

    rows = {r["spec"]: r for r in conn.execute("SELECT * FROM spec_stats")}
    check("基线", "基线表入库", [
        ("5 个专精", 5, len(rows)),
        ("猎人三系暴击同值", 1,
         len({rows[k]["crit"] for k in rows if k.startswith("hunter")})),
        ("猎人暴击 15", 15.0, rows["hunter:survival"]["crit"]),
        ("生存精通 11.9", 11.9, rows["hunter:survival"]["mastery"]),
        ("每条都记了来源", True, all(r["source"] for r in rows.values())),
    ])

    # 没填面板：固定加成立刻来自表，不用等用户标定
    s0 = Session(conn, parse(text), load_budget(), panel={})
    check("基线", "没面板也能给固定加成", [
        ("四项都填上", 4, len(s0._flat)),
        ("暴击来自表", 15.0, s0._flat.get("crit")),
        ("精通来自表", 20.9, s0._flat.get("mastery")),
    ])

    # 面板只填了暴击：那一项用面板，剩下三项退回表里的默认值
    s1 = Session(conn, parse(text), load_budget(),
                 panel={"crit": 1065, "pct": {"crit": 38.15}})
    check("基线", "面板逐项覆盖", [
        ("暴击按面板推", 15.0, s1._flat.get("crit")),
        ("急速退回表", 2.06, s1._flat.get("haste")),
        ("精通退回表", 20.9, s1._flat.get("mastery")),
    ])

    # 换装备不动基线（生存猎真实两套，系数 0.85）
    drm = s0.rating_curves()["dr"]

    def flat_of(crit, haste, mast, vers, pct, coeff=0.85):
        return {
            "crit": pct["crit"] - dr_convert(crit, drm["crit"]) / 46.0,
            "haste": pct["haste"] - dr_convert(haste, drm["haste"]) / 44.0,
            "vers": pct["vers"] - dr_convert(vers, drm["vers"]) / 54.0,
            "mastery": pct["mastery"] - dr_convert(mast, drm["mastery"]) / 46.0 * coeff,
        }

    a = flat_of(1033, 105, 1636, 348,
                {"crit": 37.46, "haste": 4.43, "mastery": 41.66, "vers": 5.44})
    b = flat_of(1084, 105, 1585, 348,
                {"crit": 38.57, "haste": 4.43, "mastery": 40.81, "vers": 5.44})
    check("基线", "换装备后基线不变", [
        (f"{k} 漂移 ≤0.02", True, abs(a[k] - b[k]) <= 0.02)
        for k in ("crit", "haste", "mastery", "vers")
    ])

    # 精通必须走递减：1636 已经越过 1380 那档，不打折会漂 0.109
    nd_a = 41.66 - 1636 / 46.0 * 0.85
    nd_b = 40.81 - 1585 / 46.0 * 0.85
    check("基线", "精通必须走递减", [
        ("不带递减会漂 0.1 以上", True, abs(nd_a - nd_b) > 0.05),
        ("带递减收敛到 0.02 内", True, abs(a["mastery"] - b["mastery"]) <= 0.02),
    ])

    # 一个数据都没有的专精（战士武器）不能静默少 5% 基础暴击
    sw = Session(conn, parse(io.open(SAMPLE, encoding="utf-8").read()), load_budget(),
                 panel={})
    check("基线", "没数据的专精至少给基础暴击", [
        ("战士暴击 5%", 5.0, sw._flat.get("crit")),
        ("只补暴击，不瞎补别的", None, sw._flat.get("haste")),
        ("标明只有客户端基础", "base", sw.flat_source().get("crit")),
        ("其余三项如实标缺", "none", sw.flat_source().get("haste")),
        # 有专精数据的那三项要标成 spec，别和"没有"混为一谈
        ("有表数据标 spec", "spec", s0.flat_source().get("haste")),
        ("面板优先于表", "panel", s1.flat_source().get("crit")),
    ])


def group_addon():
    """插件导出的数据能不能正确解析和反推。

    数据取自生存猎人真实面板（2026-10-31）：暴击 1033/37.46%、急速 105/4.43%、
    精通 1636/41.66%、全能 348/5.44%。系数 0.85。
    """
    from wowlib import addon
    from wowlib.session import Session, connect, load_budget, dr_convert

    conn = connect()
    s = Session(conn, parse(io.open(SAMPLE, encoding="utf-8").read()), load_budget())
    curves = s.rating_curves()

    text = ("v=1;build=12.1.5.70575;class=3;classKey=hunter;spec=255;specKey=survival;"
            "level=90;agi=2614;sta=41014;"
            "critR=1033;critB=22.4565;hasteR=105;hasteB=2.3864;"
            "masteryR=1636;masteryB=35.0087;versR=348;versB=5.44;"
            "critT=37.46;hasteT=4.43;mastT=41.66")
    data = addon.parse(text)

    check("插件", "解析导出串", [
        ("专精 key", "hunter:survival", addon.spec_key(data)),
        ("评级取到", 1033.0, addon.ratings(data).get("crit")),
        ("面板取到", 37.46, addon.panel_pct(data).get("crit")),
        ("精通面板取 mastT", 41.66, addon.panel_pct(data).get("mastery")),
    ])

    # 用户从聊天框复制会带换行和多余分号，不能因此报错
    messy = addon.parse("v=1\nclass=3\n;spec=255;;\tlevel=90")
    check("插件", "解析容错", [
        ("换行当分隔符", "3", messy.get("class")),
        ("空段忽略", "255", messy.get("spec")),
        ("制表符也当分隔符", "90", messy.get("level")),
    ])

    # holy/frost/protection/restoration 各有两家，必须靠 classKey 区分
    check("插件", "重名专精靠职业区分", [
        ("圣骑神圣", "paladin:holy",
         addon.spec_key({"classKey": "paladin", "specKey": "holy"})),
        ("牧师神圣", "priest:holy",
         addon.spec_key({"classKey": "priest", "specKey": "holy"})),
        ("缺一半就返回空", "",
         addon.spec_key({"classKey": "hunter", "specKey": "?"})),
    ])

    base, _ = addon.baselines(data, curves, 0.85, dr_convert)
    check("插件", "反推基线", [
        ("暴击 15.00", 15.0, base.get("crit")),
        ("急速 2.04", 2.04, base.get("haste")),
        ("精通 11.90", 11.9, base.get("mastery")),
        ("全能 -1.00（分母存疑）", -1.0, base.get("vers")),
    ])

    # 校验的意义：分母不对要能抓出来，且不能拖累对得上的项
    diffs = addon.cross_check(data, curves, dr_convert)
    bad = {d["stat"] for d in diffs}
    check("插件", "换算校验", [
        ("全能对不上被抓到", True, "vers" in bad),
        ("暴击没被误报", False, "crit" in bad),
        ("急速没被误报", False, "haste" in bad),
        ("差值约 1.00", True,
         any(abs(d["diff"] - 1.0) < 0.05 for d in diffs if d["stat"] == "vers")),
    ])


def group_panel():
    """面板基准按「职业:专精」分开存。

    同一个职业换专精，主属性和天赋都会变（圣骑神圣吃智力、防护吃力量），
    一份全局面板会让另一个专精的 base 全错。这里用临时文件测，不碰玩家真面板。
    """
    import json as _json
    import tempfile

    import wowgear.store as store
    from wowlib.session import Session, connect, load_budget

    tmpdir = tempfile.mkdtemp()
    tmp = os.path.join(tmpdir, "panel.json")
    old_path = store.PANEL_PATH
    store.PANEL_PATH = tmp
    try:
        with io.open(REAL, encoding="utf-8") as f:
            text = f.read()
        s = Session(connect(), parse(text), load_budget(), raw_text=text)
        check("面板", "按专精取面板", [
            ("键是 职业:专精", "hunter:beast_mastery", s.panel_key),
            # 没填面板时用职业基础兜底（客户端 gt 表 + 血精灵种族修正）
            ("来源是职业基础", "class", s.base_source),
            ("90 级猎人敏捷 620×1.43+1", 888.0, s.base.get("agi")),
            ("耐力 4600×1.03", 4738.0, s.base.get("sta")),
        ])

        s.set_panel({"agi": 3000, "sta": 40000})
        with io.open(tmp, encoding="utf-8") as f:
            saved = _json.load(f)
        check("面板", "保存写进当前专精", [
            ("文件里只有这一个专精", 1, len([k for k in saved if not k.startswith("_")])),
            ("值写对了", 3000, saved["hunter:beast_mastery"]["agi"]),
            ("填完改成面板来源", "panel", s.base_source),
        ])

        # 同一个角色换成生存猎：不能沿用兽王的面板（但职业基础是通用的）
        other = text.replace("spec=beast_mastery", "spec=survival")
        s2 = Session(connect(), parse(other), load_budget(), raw_text=other)
        check("面板", "换专精不继承", [
            ("新专精面板为空", {}, s2.panel),
            ("键跟着换", "hunter:survival", s2.panel_key),
            # base 退回到职业基础，而不是兽王那份 3000
            ("base 是职业基础", "class", s2.base_source),
            ("base 值不含兽王的 3000", 888.0, s2.base.get("agi")),
        ])

        s2.set_panel({"agi": 2999})
        s3 = Session(connect(), parse(text), load_budget(), raw_text=text)
        check("面板", "各专精互不影响", [
            ("兽王还是 3000", 3000.0, s3.panel.get("agi")),
            ("snapshot 带专精列表", 2, len(s3.snapshot()["panel_specs"])),
        ])

        # 面板里可以带百分比：用它反推这个专精的换算率。
        # 精通必须这么算——兽王 1423=79.5%（17.9/1%），射击 1142=20.5%（55.7/1%），
        # 统一用 17.9 会让射击的精通显示成 64%，而游戏里是 20.5%。
        s3.set_panel({"mastery": 1423, "pct": {"mastery": 79.5}})
        check("面板", "百分比反推换算率", [
            ("精通 17.9", 17.90, s3.rating_per_pct["mastery"]),
            ("没给百分比的项保持默认", 27.92, s3.rating_per_pct["crit"]),
            ("百分比存进文件", 79.5,
             _json.load(io.open(tmp, encoding="utf-8"))["hunter:beast_mastery"]["pct"]["mastery"]),
        ])
        s3.set_panel({"mastery": 1142, "pct": {"mastery": 20.52}})
        check("面板", "换专精系数不同", [
            ("射击精通 55.65", 55.65, s3.rating_per_pct["mastery"]),
        ])

        # 老格式（顶层直接是 agi/sta）：第一次读取时认领给当前专精
        with io.open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"agi": 1234, "sta": 5678}, f)
        s4 = Session(connect(), parse(text), load_budget(), raw_text=text)
        with io.open(tmp, encoding="utf-8") as f:
            migrated = _json.load(f)
        check("面板", "老格式自动迁移", [
            ("读到了旧值", 1234.0, s4.panel.get("agi")),
            ("文件改成按专精", True, isinstance(migrated.get("hunter:beast_mastery"), dict)),
        ])
    finally:
        store.PANEL_PATH = old_path
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    if not os.path.exists(DB_PATH):
        print("找不到数据库，先跑 python main.py --build")
        return 2
    if not os.path.exists(SAMPLE):
        print(f"找不到样例文件 {SAMPLE}")
        return 2

    group_parse()
    group_db()
    group_e2e()
    if os.path.exists(REAL):
        group_real()
        group_tiers()
    group_gems_enchants()
    group_customize()
    group_gem_slots()
    group_curves()
    group_spec_base()
    group_addon()
    group_update()
    group_panel()

    total = failed = 0
    last_group = None
    for group, name, pairs in results:
        if group != last_group:
            print(f"\n=== {group} ===")
            last_group = group
        print(f"\n[{name}]")
        for field, expect, actual in pairs:
            total += 1
            ok = expect == actual
            if not ok:
                failed += 1
                print(f"  ✗ {field:26} 期望 {show(expect):32} 实际 {show(actual)}")
            else:
                print(f"  ✓ {field:26} {show(actual)}")

    print("\n" + "-" * 70)
    if failed:
        print(f"{total - failed}/{total} 通过，{failed} 项不符")
        return 1
    print(f"{total}/{total} 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
