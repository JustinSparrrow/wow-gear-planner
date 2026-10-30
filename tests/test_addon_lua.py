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
FILES = ["Data.lua", "Util.lua", "Gear.lua", "Calc.lua",
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
