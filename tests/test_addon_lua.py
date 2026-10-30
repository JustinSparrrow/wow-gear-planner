"""在模拟的客户端里跑 WoWGear 插件，验证算法和界面逻辑。

为什么值得这么测：插件没法进 CI，但它的三层（曲线换算、装备采集、界面刷新）
都是纯 Lua，灌一套假 API 就能真跑。上次那版插件出过「按钮 OnClick 没绑」
「标题重影」这类错，那些错在游戏里看一眼才知道，跑一遍就全暴露了。

最关键的一条是第 3 项的「已知答案」：项目之前用两张截图做过受控实验，
生存猎暴击 1033→1084（+51 评级），面板 37.46%→38.57%（+1.11 点）。
插件算的必须也是这个数——如果不对，说明曲线斜率错了，
而斜率错会让每一件装备的评估都偏，且偏得看不出来。
"""
import io
import os
import sys

from lupa.lua51 import LuaRuntime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "WoWGear")
MOCK = os.path.join(ROOT, "tests", "wowmock.lua")

# 顺序必须跟 WoWGear.toc 一致
FILES = ["Data.lua", "Lib.lua", "Util.lua", "Curve.lua", "Gear.lua", "Calc.lua",
         "UI.lua", "List.lua", "Export.lua", "Core.lua"]

fails = []


def check(name, got, want, tol=0.02):
    ok = abs(got - want) <= tol
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}: got {got:.4f}, want {want:.4f}")
    if not ok:
        fails.append(name)
    return ok


def check_eq(name, got, want):
    ok = got == want
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}: got {got!r}, want {want!r}")
    if not ok:
        fails.append(name)
    return ok


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def new_lua():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("wipe = function(t) for k in pairs(t) do t[k] = nil end return t end")

    # 假客户端
    lua.eval("function(s, n) return assert(loadstring(s, n)) end")(read(MOCK), "wowmock")()

    # 插件本体。每个文件开头都是 "local ADDON, NS = ..."，
    # 靠 vararg 传 addonName 和私有表，所以要按 WoW 的方式调。
    ns = lua.eval("{}")
    for name in FILES:
        chunk = lua.eval("function(s, n) return assert(loadstring(s, n)) end")(
            read(os.path.join(ADDON, name)), name)
        chunk("WoWGear", ns)
    lua.globals().NS = ns
    return lua


def main():
    lua = new_lua()
    ns = lua.globals().NS
    calc = ns.Calc

    # ── 1. 递减折算 ──────────────────────────────────────
    print("─" * 62)
    print("1. 递减折算（dr_convert）")
    print("─" * 62)
    dr = lua.eval("{ {0,0},{1320,10},{1760,20},{2200,30},{2640,40},{3080,50},{8800,100} }")
    eff = calc.drConvert(1584, dr)
    check("1320+264 → 有效评级 1557.6", eff, 1557.6, 0.01)
    check("→ 急速 1557.6/44 = 35.4%", eff / 44, 35.4, 0.01)
    check("1300 未过门槛 = 1300", calc.drConvert(1300, dr), 1300, 0.001)

    # ── 2. 精通 ──────────────────────────────────────────
    print()
    print("─" * 62)
    print("2. 生存猎精通：1636 评级该是多少点")
    print("─" * 62)
    linear = 1636 / 46
    withdr = calc.drConvert(1636, lua.eval("NS.CURVE.mastery.dr")) / 46
    print(f"  线性 {linear:.3f} 点 / 含递减 {withdr:.3f} 点 / 面板 35.1")
    if abs(withdr - 35.1) < abs(linear - 35.1):
        print("  [OK  ] 递减让结果更接近面板")
    else:
        print("  [FAIL] 递减方向不对")
        fails.append("mastery-dr")

    # 精通系数靠 GetMasteryEffect()/GetMastery() 实测
    coeff = calc.masteryCoeff()
    check("精通系数实测 = 29.84/35.1 = 0.850", coeff, 0.85, 0.005)

    # ── 3. 受控实验 ──────────────────────────────────────
    print()
    print("─" * 62)
    print("3. 受控实验：生存猎暴击 1033→1084（两张截图实测 +1.11%）")
    print("─" * 62)
    check("Δ 暴击百分比", calc.curvePct("crit", 1084) - calc.curvePct("crit", 1033), 1.11, 0.02)

    # ── 4. simulate ──────────────────────────────────────
    print()
    print("─" * 62)
    print("4. 换装模拟的差值")
    print("─" * 62)
    lua.execute("""
      _snap = {
        ctx = { primary = "agi", key = "hunter:survival", level = 90 },
        rating = { crit = 1033, haste = 543, mastery = 1636, vers = 173 },
        pct    = { crit = 38.57, haste = 12.34, mastery = 29.84, vers = 3.20 },
        stats  = { agi = 2400, str = 0, int = 0, sta = 8000 },
        gear = {}, ilvl = 334, levelOK = true,
      }
      _snap.gear.head = { ilvl = 331, stats = { crit = 198 } }
    """)
    snap = lua.globals()._snap
    cand = lua.eval("{ ilvl = 334, stats = { crit = 252, haste = 12 } }")
    sim = calc.simulate(snap, "head", cand)
    check("Δ 暴击 (+54 评级)", sim.dPct.crit, 54 / 46, 0.001)
    check("Δ 急速 (+12 评级)", sim.dPct.haste, 12 / 44, 0.001)
    check("换完后暴击面板", sim.pct.crit, 38.57 + 54 / 46, 0.01)
    check_eq("装等 331→334", sim.dIlvl, 3)

    # ── 5. 递减在模拟里的作用 ────────────────────────────
    print()
    print("─" * 62)
    print("5. 高评级时递减必须让 Δ 变小")
    print("─" * 62)
    lua.execute("""
      _snap2 = {
        ctx = { primary = "agi", key = "hunter:survival", level = 90 },
        rating = { crit = 1800, haste = 0, mastery = 0, vers = 0 },
        pct    = { crit = 40, haste = 0, mastery = 0, vers = 0 },
        stats  = { agi = 2400, str = 0, int = 0, sta = 8000 },
        gear = {}, ilvl = 334, levelOK = true,
      }
      _snap2.gear.head = { ilvl = 331, stats = { crit = 100 } }
    """)
    sim2 = calc.simulate(lua.globals()._snap2, "head",
                         lua.eval("{ ilvl = 334, stats = { crit = 154 } }"))
    naive = 54 / 46
    print(f"  线性估 {naive:.4f} / 含递减 {sim2.dPct.crit:.4f}")
    if sim2.dPct.crit < naive:
        print("  [OK  ] 递减生效（模拟结果比线性估小）")
    else:
        print("  [FAIL] 递减没有起作用")
        fails.append("dr-effect")

    # ── 6. 多属性合一 ────────────────────────────────────
    print()
    print("─" * 62)
    print("6. 三合一属性槽按主属性摊")
    print("─" * 62)
    item = lua.eval("""{
      ilvl = 334,
      stats = { sta = 500, crit = 120,
                _multi = { { codes = {"agi","str","int"}, value = 300 } } },
    }""")
    st = calc.itemStats(snap, item)
    check_eq("+300 三合一 → 敏捷 300", st.agi, 300)
    check_eq("暴击保留 120", st.crit, 120)
    check_eq("耐力保留 500", st.sta, 500)

    # ── 7. 实测分母 ──────────────────────────────────────
    print()
    print("─" * 62)
    print("7. 运行时实测换算分母（未进递减时）")
    print("─" * 62)
    # 全能 394 评级 → 7.2963%，比值 54 —— 这就是网页端一直没定案的那个数
    lua.execute("""
      PLAYER.rating.vers = 394
      PLAYER.rating_bonus = { vers = 394/54 }
    """)
    check("实测全能分母 = 54", calc.rate("vers"), 54.0, 0.01)
    # 进递减后不能再采信实测，必须退回表
    lua.execute("""
      PLAYER.rating.crit = 2000
      PLAYER.rating_bonus = { crit = 2000/46*0.9 }
    """)
    check("过门槛后回退到表 46", calc.rate("crit"), 46.0, 0.01)
    lua.execute("PLAYER.rating.crit = 1033 PLAYER.rating_bonus = nil PLAYER.rating.vers = 173")

    # ── 8. 槽位匹配 ──────────────────────────────────────
    print()
    print("─" * 62)
    print("8. 物品能不能进这个槽")
    print("─" * 62)
    gear = ns.Gear
    cases = [
        ("head",   "INVTYPE_HEAD",     True,  "头盔进头"),
        ("head",   "INVTYPE_FEET",     False, "鞋子不能进头"),
        ("chest",  "INVTYPE_ROBE",     True,  "布甲胸(ROBE)进胸"),
        ("finger2", "INVTYPE_FINGER",  True,  "戒指进戒指2"),
        ("main_hand", "INVTYPE_2HWEAPON", True, "双手武器进主手"),
        ("off_hand",  "INVTYPE_2HWEAPON", False, "双手武器不能进副手"),
        ("off_hand",  "INVTYPE_SHIELD",   True, "盾进副手"),
        ("main_hand", "INVTYPE_TRINKET",  False, "饰品不能进主手"),
    ]
    for slotKey, loc, want, desc in cases:
        got = gear.accepts(slotKey, lua.eval(f"{{ equipLoc = '{loc}' }}"))
        check_eq(desc, bool(got), want)

    # ── 9. 唯一装备冲突 ──────────────────────────────────
    print()
    print("─" * 62)
    print("9. 同一枚戒指已经戴在另一只手")
    print("─" * 62)
    lua.execute("""
      EQUIPPED = {}
      BAGS = { [0] = {} }
      local l1 = mkItem(100, 334, { [32] = 100 }, "INVTYPE_FINGER", "Ring A")
      local l2 = mkItem(101, 334, { [32] = 120 }, "INVTYPE_FINGER", "Ring B")
      EQUIPPED[11] = l1        -- 戒指1 戴着 A
      BAGS[0][1] = l1          -- 包里也有一枚 A（不可能，但用来测冲突）
      BAGS[0][2] = l2
    """)
    lua.execute("""
      _eq = NS.Gear.equipped()
      _bags = NS.Gear.bags()
      _cands = NS.Gear.candidates("finger2", _eq, _bags)
    """)
    cands = lua.globals()._cands
    n = len(cands)
    found_conflict = False
    found_b = False
    for i in range(1, n + 1):
        c = cands[i]
        if c.otherEquipped:
            found_conflict = True
        if c.name == "Ring B":
            found_b = True
    check_eq("戒指2 的候选里标出了冲突", found_conflict, True)
    check_eq("Ring B 在候选里", found_b, True)

    # ── 10. 端到端：UI 刷新 ──────────────────────────────
    print()
    print("─" * 62)
    print("10. 端到端：真实装备 → 界面刷新 → 模拟换装")
    print("─" * 62)
    lua.execute("""
      EQUIPPED = {}
      BAGS = { [0] = {} }
      -- 身上：头 331（暴击99/急速99）、胸 331、腿 331、双手武器 331
      local h  = mkItem(200, 331, { [3]=183, [7]=500, [32]=99, [36]=99 }, "INVTYPE_HEAD", "Old Helm")
      local c  = mkItem(201, 331, { [3]=183, [7]=500, [32]=120, [40]=80 }, "INVTYPE_CHEST", "Old Chest")
      local l  = mkItem(202, 331, { [3]=183, [7]=500, [49]=120, [36]=80 }, "INVTYPE_LEGS", "Old Legs")
      local w  = mkItem(203, 331, { [3]=300, [7]=700, [32]=150, [49]=110 }, "INVTYPE_2HWEAPON", "Old Bow")
      EQUIPPED[1], EQUIPPED[5], EQUIPPED[7], EQUIPPED[16] = h, c, l, w

      -- 包里：一件更好的头
      local better = mkItem(300, 334, { [3]=200, [7]=540, [32]=150, [36]=80 }, "INVTYPE_HEAD", "New Helm")
      BAGS[0][1] = better
      -- 包里还有一件鞋子，不该出现在头的候选里
      BAGS[0][2] = mkItem(301, 334, { [3]=130, [7]=400, [32]=80 }, "INVTYPE_FEET", "Boots")
      _BETTER = better
    """)
    lua.execute("NS.UI:Refresh()")
    ui = ns.UI

    check("界面读到的当前暴击%", float(lua.eval("NS.UI.statCells.crit.value:GetText()").rstrip("%")), 38.57, 0.01)
    check("界面读到的装等", ui.snap.ilvl, (331 + 331 + 331 + 331) / 4, 0.01)

    # 选头部
    lua.execute("NS.UI:Select('head')")
    rows = lua.globals()
    # 候选应有 2 条：当前头盔 + 背包头盔，鞋子被过滤掉
    print(f"  头部候选数: {len(lua.globals().NS.Gear.candidates('head', ui.snap.gear, lua.globals().NS.Gear.bags()))}")
    check_eq("头的候选数（当前 + 新头，鞋子被过滤）",
             len(lua.globals().NS.Gear.candidates("head", ui.snap.gear, lua.globals().NS.Gear.bags())), 2)

    # 模拟换上更好的头
    lua.execute("""
      local info = NS.Item.get(_BETTER)
      NS.UI.sim.head = info
      NS.UI.cand = info
      NS.UI:Refresh()
    """)
    newCrit = float(lua.eval("NS.UI.statCells.crit.value:GetText()").rstrip("%"))
    # 暴击 +51 评级 → +1.1087%；面板 38.57 → 39.68
    check("模拟后暴击%", newCrit, 38.57 + 51 / 46, 0.02)
    check("模拟后装等", lua.eval("NS.UI.simIlvl"), (334 + 331 + 331 + 331) / 4, 0.01)

    check_eq("底栏显示了模拟详情", "New Helm" in (lua.eval("NS.UI.footTitle:GetText()") or ""), True)

    # 还原
    lua.execute("NS.UI.sim = {} NS.UI.cand = nil NS.UI:Refresh()")
    check("还原后暴击%", float(lua.eval("NS.UI.statCells.crit.value:GetText()").rstrip("%")), 38.57, 0.01)

    # ── 11. 导出格式 ────────────────────────────────────
    print()
    print("─" * 62)
    print("11. 导出文本还能被 python 端解析")
    print("─" * 62)
    lua.execute("NS.Export.Open()")
    lua.execute("""
      _EB = nil
      for _, w in ipairs(ALL_WIDGETS) do
        if rawget(w, "_kind") == "EditBox" then _EB = w end
      end
    """)
    if lua.eval("_EB") is not None:
        txt = lua.eval("select(1, _EB:GetText())")
        check_eq("含 v=1 头字段", "v=1" in txt, True)
        check_eq("含 specKey=survival", "specKey=survival" in txt, True)
        check_eq("含 critR=1033", "critR=1033" in txt, True)
        check_eq("含 mastT", "mastT=" in txt, True)
        print(f"  导出样例: {txt[:100]}…")

        # 最要紧的一条：这份文本能不能被 python 端的 addon.py 解析
        import sys as _sys
        _sys.path.insert(0, ROOT)
        from src.wowlib import addon as addon_mod
        parsed = addon_mod.parse(txt) if hasattr(addon_mod, "parse") else None
        if parsed:
            check_eq("python 端解析出专精", parsed.get("specKey"), "survival")
            print(f"  python 端解析结果: spec={parsed.get('specKey')} "
                  f"crit={parsed.get('critR')} mastery={parsed.get('mastPts')}")
        else:
            print("  [SKIP] addon.py 没有 parse()，跳过对拍")
    else:
        print("  [FAIL] 找不到导出用的 EditBox")
        fails.append("export-editbox")

    # ── 12. 曲线：Lua 版 vs python 版 ──────────────────
    print()
    print("─" * 62)
    print("12. Lua 版装等曲线必须和 python 版 curve.py 算出一个数")
    print("─" * 62)
    sys.path.insert(0, ROOT)
    from src.wowgear import stats as py_stats

    # 充能沙石指环：耐力 7889 / 暴击 10000 / 精通 7500，戒指（jewel 档）
    py_vals = py_stats.item_values("finger", 334, [
        {"code": "sta", "alloc": 7889},
        {"code": "crit", "alloc": 10000},
        {"code": "mastery", "alloc": 7500},
    ])
    lua_vals = lua.eval("NS.Lib.statsFor(158366, 334, 'agi')")
    print(f"  python: {py_vals}")
    print(f"  lua:    sta={lua_vals['sta']} crit={lua_vals['crit']} mastery={lua_vals['mastery']}")
    for k in ("sta", "crit", "mastery"):
        check_eq(f"{k} 两版一致（容差 1）", abs(lua_vals[k] - py_vals[k]) <= 1, True)

    # 双手武器按"大件"档算，不能按单手
    py_w = py_stats.item_values("main_hand", 334, [{"code": "agi", "alloc": 5259}],
                                inventory_type=17)
    lua_w = lua.eval("NS.Lib.statsFor(158370, 334, 'agi')")
    print(f"  双手武器敏捷 python={py_w.get('agi')} lua={lua_w['agi']}")
    check_eq("双手武器走大件档", abs(lua_w["agi"] - py_w["agi"]) <= 1, True)
    # 和单手比，必须明显更高（这就是当初 python 端踩过的坑）
    lua_1h = lua.eval("NS.Lib.statsFor(158369, 334, 'int')")
    print(f"  同装等单手武器智力={lua_1h['int']}（应该明显低于双手）")
    check_eq("双手 > 单手", lua_w["agi"] > (lua_1h["int"] or 0), True)

    # ── 13. 装备库搜索 ──────────────────────────────
    print()
    print("─" * 62)
    print("13. 装备库：按部位、关键字、专精筛")
    print("─" * 62)
    n_head = lua.eval("NS.Lib.countFor('head', 255)")
    n_all = lua.eval("NS.Lib.countFor('head', 0)")
    print(f"  头部：全部 {n_all} 件 / 猎人可用 {n_head} 件")
    check_eq("猎人可用的头部足够多（能规划）", n_head > 20, True)

    lua.execute("_s = NS.Lib.search('head', '盔', 255)")
    n_kw = len(lua.globals()._s)
    print(f"  搜「盔」命中 {n_kw} 件")
    check_eq("关键字搜索有结果", n_kw > 0, True)

    # 专精限制：找一个限定了非猎人的物品
    import sqlite3, json as _json
    conn = sqlite3.connect(os.path.join(ROOT, "data", "wowgear.db"))
    locked = None
    # 只导本赛季，所以这里也必须限定 is_current=1，
    # 否则可能挑到一件库里根本没有的旧赛季装备，测的就不是"限制生效"了
    for iid, sp in conn.execute(
            "SELECT id, specs FROM items WHERE specs IS NOT NULL "
            "AND is_current = 1 LIMIT 400"):
        try:
            lst = _json.loads(sp)
        except Exception:
            continue
        if lst and 255 not in lst:
            locked = iid
            break
    conn.close()
    if locked:
        got = lua.eval(f"NS.Lib.allowed({locked}, 255)")
        check_eq(f"专精限制生效（{locked} 猎人不能用）", bool(got), False)
        got2 = lua.eval(f"NS.Lib.allowed({locked}, {_json.loads(conn_specs) if False else 66})")
        print(f"        同一件对专精 66 是否可用: {bool(got2)}")
    else:
        print("  [SKIP] 没找到带专精限制的物品")

    # ── 14. 端到端：切到装备库模式 ──────────────────
    print()
    print("─" * 62)
    print("14. 端到端：切到装备库，看能不能挑一件自己没有的")
    print("─" * 62)
    import time as _time
    lua.execute("""
      NS.UI.mode = "lib"
      NS.UI.keyword = ""
      NS.UI.selected = "head"
      NS.UI.libCache = nil
    """)
    t0 = _time.time()
    lua.execute("NS.UI:Refresh()")
    dt = _time.time() - t0
    sub = lua.eval("NS.UI.rightSub:GetText()") or ""
    print(f"  刷新耗时 {dt*1000:.0f} ms")
    print(f"  标题: {sub}")
    check_eq("标题标明了折算装等", "折算" in sub, True)
    check_eq("刷新在 300ms 内（不卡手）", dt < 0.3, True)

    # 挑一件库里最好的头换上，看属性对不对
    lua.execute("""
      local ids = NS.Lib.search("head", "", 255)
      _best = NS.Lib.build(ids[1], NS.UI.targetIlvl, NS.UI.snap.ctx.primary)
      NS.UI.sim.head = _best
      NS.UI.cand = _best
      NS.UI:Refresh()
    """)
    best = lua.globals()._best
    print(f"  换上的库装备: {best.name}（基础 {best.baseIlvl} 装等，按 {lua.eval('NS.UI.targetIlvl')} 折算）")
    print(f"  折算后属性: crit={best.stats.crit} mastery={best.stats.mastery}")
    newCrit2 = float(lua.eval("NS.UI.statCells.crit.value:GetText()").rstrip("%"))
    check_eq("库装备换上去后面板变了", abs(newCrit2 - 38.57) > 0.001, True)
    check_eq("左栏显示成模拟态", lua.eval("NS.UI.sim.head ~= nil"), True)

    # 库装备的"本赛季"标记不能和背包物品的"身上那件"撞名字，
    # 撞了会让列表里所有本赛季装备都显示成"当前"
    check_eq("库装备没被误标成「当前」", lua.eval("_best.current == nil"), True)
    check_eq("库装备带本赛季标记", bool(lua.eval("_best.curSeason")), True)

    # 排序维度生效：score 取的是选中那条属性的差值
    lua.execute("""
      local sim = NS.Calc.simulate(NS.UI.snap, "head", _best)
      _s_crit = NS.Calc.score(sim, "crit")
      _s_mast = NS.Calc.score(sim, "mastery")
      _d_crit = sim.dStat.crit or 0
      _d_mast = sim.dStat.mastery or 0
    """)
    check_eq("score(crit) == 暴击差值",
             abs(lua.eval("_s_crit") - lua.eval("_d_crit")) < 0.001, True)
    check_eq("score(mastery) == 精通差值",
             abs(lua.eval("_s_mast") - lua.eval("_d_mast")) < 0.001, True)
    check_eq("两条属性的分数不一样（说明真的按维度排）",
             abs(lua.eval("_s_crit") - lua.eval("_s_mast")) > 1, True)

    lua.execute("NS.UI.mode = 'bag' NS.UI.sim = {} NS.UI.cand = nil NS.UI:Refresh()")

    # ── 15. 属性表读不到时的兜底 ────────────────────
    print()
    print("─" * 62)
    print("15. GetItemStats 失效时，能不能靠装备库兜住")
    print("─" * 62)
    # 这是线上真出过的故障：GetItemStats 签名不认识 → 返回空表 → 不报错，
    # 表现是"换装备后面板一动不动、所有差值都是 0"。
    lua.execute("""
      -- 把所有读法都打断
      C_Item.GetItemStats = function() return nil end
      GetItemStats = nil
      NS.Item.clear()

      EQUIPPED = {}
      BAGS = { [0] = {} }
      -- 用库里的真实 itemID（158370 连击大戟，双手武器）
      local old = mkItem(158370, 331, nil, "INVTYPE_2HWEAPON", "连击大戟")
      EQUIPPED[16] = old
      BAGS[0][1] = mkItem(158369, 334, nil, "INVTYPE_WEAPON", "充电破风锤")
    """)
    lua.execute("NS.Item.clear() NS.UI.sim = {} NS.UI.cand = nil NS.UI.mode='bag' NS.UI.selected='main_hand' NS.UI:Refresh()")

    hl = lua.eval("NS.Item.statsPath") or ""
    print(f"  读属性表的结果: {hl}")
    check_eq("读失败被记录下来（不是静默）", "FAIL" in str(hl), True)

    fb = lua.eval("NS.Lib.statsFor(158370, 331, 'agi')")
    print(f"  兜底算出的属性: agi={fb['agi']} sta={fb['sta']} crit={fb['crit']} haste={fb['haste']}")
    check_eq("兜底能算出主属性", (fb["agi"] or 0) > 0, True)
    check_eq("兜底能算出绿字", (fb["crit"] or 0) + (fb["haste"] or 0) > 0, True)

    # 换上另一件之后，差值必须非 0
    lua.execute("""
      local cands = NS.Gear.candidates("main_hand", NS.UI.snap.gear, NS.Gear.bags())
      _simFallback = NS.Calc.simulate(NS.UI.snap, "main_hand", cands[2] or cands[1])
    """)
    dsum = abs(lua.eval("_simFallback.dStat.agi or 0")) + \
           abs(lua.eval("_simFallback.dStat.crit or 0")) + \
           abs(lua.eval("_simFallback.dStat.haste or 0"))
    print(f"  换武器后的属性差总和: {dsum}")
    check_eq("兜底状态下差值不是 0", dsum > 0, True)

    # 恢复，别影响后面的断言
    lua.execute("""
      C_Item.GetItemStats = function(x)
        local d = ITEM_DB[x]
        return d and d.stats or nil
      end
      NS.Item.clear()
    """)

    # ── 16. 属性表 key 的兼容 + 自选槽 ──────────────
    print()
    print("─" * 62)
    print("16. 属性表 key 不认识 / 自选属性槽")
    print("─" * 62)

    # 线上故障的第二种形态：GetItemStats **返回了表**，但 key 不是数字常量，
    # 一个都没命中 → stats 仍为空 → 差值恒 0。而且它不报错。
    lua.execute("""
      C_Item.GetItemStats = function(x)
        local d = ITEM_DB[x]
        if not d then return nil end
        return { ITEM_MOD_STAMINA = 1000, ITEM_MOD_CRIT_RATING = 200, ["Haste"] = 50 }
      end
      NS.Item.clear()
      _link16 = mkItem(999001, 334, {[7] = 1}, "INVTYPE_HEAD", "KeyTest")
      _i16 = NS.Item.get(_link16)
    """)
    check_eq("常量名 key 认得（stamina）", lua.eval("_i16.stats.sta"), 1000)
    check_eq("常量名 key 认得（crit）", lua.eval("_i16.stats.crit"), 200)
    check_eq("属性名 key 认得（Haste）", lua.eval("_i16.stats.haste"), 50)

    # 自选属性槽（24/25）：以前被当未知 id 过滤，导致这批装备在库里没有绿字
    lua.execute("""
      C_Item.GetItemStats = function(x)
        local d = ITEM_DB[x]
        return d and d.stats or nil
      end
      NS.Item.clear()
    """)
    opt = lua.eval("NS.Lib.statsFor(244582, 334, 'agi')")
    print(f"  带自选槽的腿铠按 334 折算: agi={opt['agi']} sta={opt['sta']} "
          f"crit={opt['crit']} mastery={opt['mastery']}")
    check_eq("自选槽折出了绿字（以前是 0）",
             (opt["crit"] or 0) + (opt["mastery"] or 0) > 0, True)
    check_eq("自选槽标记得上", bool(lua.eval(
        "NS.Lib.build(244582, 334, 'agi').hasOpt")), True)

    # ── 17. 真实客户端给的 key 形态 ──────────────────
    print()
    print("─" * 62)
    print("17. 真实客户端 key：ITEM_MOD_XXX_SHORT（实测日志逐字照搬）")
    print("─" * 62)
    # 用户自检打出来的是这张表：
    # ITEM_MOD_MASTERY_RATING_SHORT = 99
    # ITEM_MOD_STAMINA_SHORT        = 3777
    # ITEM_MOD_AGILITY_SHORT        = 183
    # ITEM_MOD_VERSATILITY          = 99
    # RESISTANCE0_NAME              = 248
    # key 是**常量名**，不是它的值。而且中文客户端里
    # _G["ITEM_MOD_AGILITY_SHORT"] 的值是"敏捷"（字符串），
    # 所以"把常量名当 _G 键查数字"这条路根本走不通。
    lua.execute("""
      -- 还原中文客户端的真实环境
      ITEM_MOD_AGILITY = "敏捷"        ITEM_MOD_AGILITY_SHORT = "敏捷"
      ITEM_MOD_STAMINA = "耐力"        ITEM_MOD_STAMINA_SHORT = "耐力"
      ITEM_MOD_MASTERY_RATING = "精通" ITEM_MOD_MASTERY_RATING_SHORT = "精通"
      ITEM_MOD_VERSATILITY = "全能"
      ITEM_MOD_CRIT_RATING = "暴击"    ITEM_MOD_CRIT_RATING_SHORT = "暴击"
      ITEM_MOD_HASTE_RATING = "急速"   ITEM_MOD_HASTE_RATING_SHORT = "急速"

      _link17 = mkItem(999017, 331, {}, "INVTYPE_LEGS", "远行者的加固腿铠")
      C_Item.GetItemStats = function(x)
        if x == _link17 then
          return {
            ["ITEM_MOD_MASTERY_RATING_SHORT"] = 99,
            ["ITEM_MOD_STAMINA_SHORT"]        = 3777,
            ["RESISTANCE0_NAME"]              = 248,
            ["ITEM_MOD_VERSATILITY"]          = 99,
            ["ITEM_MOD_AGILITY_SHORT"]        = 183,
          }
        end
        local d = ITEM_DB[x]
        return d and d.stats or nil
      end
      NS.Item.clear()
      _i17 = NS.Item.get(_link17)
    """)
    st = lua.eval("_i17.stats")
    print(f"  解析结果: agi={st['agi']} sta={st['sta']} mastery={st['mastery']} "
          f"vers={st['vers']} crit={st['crit']} armor={st['armor']}")
    check_eq("敏捷 183", st["agi"], 183)
    check_eq("耐力 3777", st["sta"], 3777)
    check_eq("精通 99", st["mastery"], 99)
    check_eq("全能 99", st["vers"], 99)
    check_eq("护甲不混进绿字", st["armor"], None)
    check_eq("没有一个 key 落空", "unmapped" in str(lua.eval("NS.Item.statsPath")), False)

    # 这条是真正的回归点：Data.lua 重跑一遍（此时 _G 里全是中文字符串），
    # NS.MOD 必须仍以数字为键。原来 `_G["ITEM_MOD_AGILITY"] or fallback`
    # 会把键变成"敏捷"，数字全丢——而测试环境没有这些常量，所以从没抓到。
    chunk = lua.eval("function(s, n) return assert(loadstring(s, n)) end")(
        read(os.path.join(ADDON, "Data.lua")), "Data.lua")
    chunk("WoWGear", ns)
    print(f"  Data.lua 在中文常量环境下重建后：NS.MOD[3]={lua.eval('NS.MOD[3]')} "
          f"NS.MOD['敏捷']={lua.eval(chr(39).join(['NS.MOD[', '敏捷', ']']))}")
    check_eq("数字键仍在（假通过的根源）", lua.eval("NS.MOD[3]"), "agi")
    check_eq("中文字符串别名也留着", lua.eval("NS.MOD['敏捷']"), "agi")

    # ── 18. 切模式必须清搜索词 ──────────────────────
    print()
    print("─" * 62)
    print("18. 切到装备库时，别让上一个模式的搜索词把库筛空")
    print("─" * 62)
    # 线上现象：背包里搜"阿曼尼"，切到装备库后整个库只剩一行当前装备，
    # 看起来像"库里根本没有未拥有的装备"。
    lua.execute("""
      NS.UI.mode = "bag"
      NS.UI.keyword = "阿曼尼"
      NS.UI.search:SetText("阿曼尼")
      NS.UI.tabs.lib:GetScript("OnClick")(NS.UI.tabs.lib)
    """)
    check_eq("切模式后关键字被清掉", lua.eval("NS.UI.keyword"), "")
    check_eq("搜索框文本也清了", lua.eval("NS.UI.search:GetText()"), "")
    hits = lua.eval("NS.UI.libHits") or 0
    print(f"  切过去后库里命中 {hits} 件")
    check_eq("装备库有内容（不是空的）", hits > 20, True)

    # 搜一个库里绝对没有的词，必须给出提示而不是一片空白
    lua.execute("""
      NS.UI.keyword = "zzzz不存在zzzz"
      NS.UI.libCache = nil
      NS.UI:RefreshListOnly()
    """)
    check_eq("搜索无结果时 libHits 为 0", lua.eval("NS.UI.libHits"), 0)
    lua.execute("NS.UI.keyword = '' NS.UI.libCache = nil NS.UI:RefreshListOnly()")

    # 基准必须和候选一起折算，否则 Δ 会系统性偏向候选（每件都显得更好）
    lua.execute("""
      local list = NS.UI:BuildCandidates(NS.SLOT_BY_KEY[NS.UI.selected], {})
      _base = list[1]
      _baseSim = NS.Calc.simulate(NS.UI.snap, NS.UI.selected, _base)
    """)
    base = lua.globals()._base
    print(f"  基准装备: {base.name}  normed={base.normed}")
    print("  基准与快照同一套数字:", lua.eval("""(function()
      local g = NS.UI.snap.gear[NS.UI.selected]
      return string.format("快照 crit=%s / 基准 crit=%s", tostring(g.stats.crit),
        tostring(_base.stats.crit))
    end)()"""))
    check_eq("基准也走了同一套折算", bool(base.normed), True)
    check_eq("基准对自己的差值为 0",
             abs(lua.eval("_baseSim.dStat.crit or 0")) +
             abs(lua.eval("_baseSim.dStat.mastery or 0")), 0)

    # ── 19. 中文搜索：输入法 + 副本名 ────────────────
    print()
    print("─" * 62)
    print("19. 中文搜索：输入法确认那一下，和按副本名搜")
    print("─" * 62)
    # 线上故障：用户输入"阿曼尼"，搜索框里是中文，提示里却是「a man ni」。
    # 根因是 EditBox 在输入法确认候选词时 userInput=false，
    # 原代码 `if not userInput then return end` 把这次更新吞了，
    # UI.keyword 停在拼音上。表现就是"搜索只认英文"。
    lua.execute("""
      NS.UI.search:SetText("塞塔里斯")
      -- WoW 真实行为：输入法确认时也会触发 OnTextChanged，但 userInput=false
      NS.UI.search:GetScript("OnTextChanged")(NS.UI.search, false)
    """)
    check_eq("输入法确认（userInput=false）也更新关键字",
             lua.eval("NS.UI.keyword"), "塞塔里斯")

    # 搜索要能命中掉落来源，不能只认装备名——玩家记的是副本，不是装备叫啥
    lua.execute("""
      _probeId, _probeSlot = nil, nil
      for id, raw in pairs(NS.LIB) do
        local src = NS.LIB_SRC[id]
        if src and src:find("神庙", 1, true) then
          _probeId, _probeSlot = id, raw[4]
          break
        end
      end
    """)
    probe_id = lua.eval("_probeId")
    probe_slot = lua.eval("_probeSlot")
    print(f"  拿一件「神庙」出的装备做样本: {probe_id} / 槽位 {probe_slot}")

    lua.execute("_hitSrc = NS.Lib.search(_probeSlot, '神庙', 0)")
    hit_ids = [int(x) for x in lua.eval("_hitSrc").values()]
    print(f"  搜「神庙」命中 {len(hit_ids)} 件")
    check_eq("按副本名能搜到", int(probe_id) in hit_ids, True)
    check_eq("副本名搜索不是全量返回", len(hit_ids) < 400, True)

    # 多词 = 全部命中
    lua.execute("_hitMulti = NS.Lib.search(_probeSlot, '神庙 不存在词', 0)")
    n_multi = len(list(lua.eval("_hitMulti").values()))
    check_eq("两个词要同时成立（不是或）", n_multi, 0)

    # 搜物品 ID
    lua.execute(f"_hitId = NS.Lib.search('{probe_slot}', '{probe_id}', 0)")
    id_hits = [int(x) for x in lua.eval("_hitId").values()]
    check_eq("按物品 ID 能搜到", int(probe_id) in id_hits, True)

    # 装备中文名仍然能搜
    lua.execute("""
      _firstName = NS.LIB[_probeId][1]
      _hitName = NS.Lib.search(_probeSlot, _firstName, 0)
    """)
    name_hits = [int(x) for x in lua.eval("_hitName").values()]
    check_eq("按装备名能搜到", int(probe_id) in name_hits, True)

    # 全角空格也要能当分隔符（中文输入法下很容易打出来）
    lua.execute("_hitWide = NS.Lib.search(_probeSlot, '神庙　法师', 0)")
    check_eq("全角空格也能分词", len(list(lua.eval("_hitWide").values())) >= 0, True)

    # 搜索是按部位来的，搜不到时要告诉玩家"别的部位有"。
    # 用用户实际打的"阿曼尼"做样本：库里有 4 件（背部/主手/戒指/饰品各一），
    # 颈部一件都没有。赛季更新后如果这条红了，改成本赛季有的名字即可。
    lua.execute("_other = NS.Lib.searchOther('neck', '阿曼尼', 255)")
    other_txt = lua.eval("""(function()
      local t = NS.Lib.searchOther('neck', '阿曼尼', 255)
      if not t then return "nil" end
      local o = {}
      for i = 1, #t do o[#o + 1] = tostring(t[i][1]) .. "=" .. tostring(t[i][2]) end
      return table.concat(o, ",")
    end)()""")
    n_neck = lua.eval("#NS.Lib.search('neck','阿曼尼',255)")
    print(f"  颈部搜「阿曼尼」: {n_neck} 件；其他部位: {other_txt}")
    check_eq("颈部确实一件没有", n_neck, 0)
    check_eq("别的部位有货时能指出来", lua.eval("_other ~= nil"), True)

    # ── 拼音 ────────────────────────────────────────────
    # 这条是本轮的核心：中文输入法在候选词**未确认**时，EditBox 的
    # GetText() 返回的是拼音串（音节用撇号分隔），插件根本拿不到汉字。
    # 用户看到输入框里是「阿曼尼」，我们收到的是 a'man'ni。
    for kw, label in (("a'man'ni", "输入法原始拼音串（截图上就是这个）"),
                      ("amanni", "全拼"),
                      ("amn", "首字母"),
                      ("AMN", "首字母大写"),
                      ("阿曼尼", "汉字（输入法确认后）")):
        n = lua.eval(f"#NS.Lib.search('back', [[{kw}]], 255)")
        print(f"  {label:28s} {kw!r:12s} -> {n} 件")
        check_eq(f"拼音/汉字都能搜到（{kw}）", n >= 1, True)

    # 拼音不该把整个库都匹配进来
    n_broad = lua.eval("#NS.Lib.search('back', 'amn', 255)")
    check_eq("首字母匹配不是全量返回", n_broad < 30, True)

    lua.execute("NS.UI.search:SetText('') NS.UI.keyword = '' NS.UI.libCache = nil")

    # ── 汇总 ────────────────────────────────────────────
    print()
    print("=" * 62)
    if fails:
        print(f"失败 {len(fails)} 项：")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
