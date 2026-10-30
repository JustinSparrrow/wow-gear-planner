--[[
    WoWGear · 静态数据

    这些数原来躺在 data/wowgear.db 里（meta.rating_curves / spec_mastery / stat_types），
    由 python 端读出来用。插件在游戏里没法读 sqlite，所以固化成 Lua 表。

    **哪些数是"抄来的"，哪些是"问游戏要的"**
    --------------------------------------------
    90 级绿字换算分母（46/44/54/46）和递减档位是抄 Maxroll + DBC 的，
    因为 API 只给「当前评级对应多少百分比」，不给曲线本身，算 Δ 必须知道曲线。
    但所有**角色相关**的东西（专精基线、天赋加成）一律不问表，运行时反推，
    见 Calc.baseline()。这样换个天赋、换个版本都不用改这份文件。

    为什么递减必须做：生存猎实测 1636 精通，按线性算是 35.6%，面板只有 35.1%。
    那份 0.5 的差正好是越过 1380 门槛后打九折的结果。不做递减，换装前后
    的差值会算大，你会以为换上一件提升比实际多。
]]

local ADDON, NS = ...

NS.VERSION = "1.0.0"

-- ── 角色装备槽 ───────────────────────────────────────────
--[[
    id 是 GetInventoryItemLink / GetInventoryItemID 的槽位号（暴雪的固定编号，
    从 1 开始，衬衣=4、战袍=19 这两个不参与配装所以跳过）。

    inv 用来判断背包里某件物品能不能进这个槽。戒指/饰品各两格，
    key 带序号是为了让它们在界面上分开摆，但 inv 是同一个。
]]
NS.SLOTS = {
  { key = "head",     id = 1,  inv = "INVTYPE_HEAD",     cn = "头部",  tex = "Head" },
  { key = "neck",     id = 2,  inv = "INVTYPE_NECK",     cn = "颈部",  tex = "Neck" },
  { key = "shoulder", id = 3,  inv = "INVTYPE_SHOULDER", cn = "肩部",  tex = "Shoulder" },
  { key = "back",     id = 15, inv = "INVTYPE_CLOAK",    cn = "背部",  tex = "Back" },
  { key = "chest",    id = 5,  inv = "INVTYPE_ROBE",     cn = "胸部",  tex = "Chest" },
  { key = "wrist",    id = 9,  inv = "INVTYPE_WRIST",    cn = "手腕",  tex = "Wrist" },
  { key = "hands",    id = 10, inv = "INVTYPE_HAND",     cn = "手部",  tex = "Hands" },
  { key = "waist",    id = 6,  inv = "INVTYPE_WAIST",    cn = "腰部",  tex = "Waist" },
  { key = "legs",     id = 7,  inv = "INVTYPE_LEGS",     cn = "腿部",  tex = "Legs" },
  { key = "feet",     id = 8,  inv = "INVTYPE_FEET",     cn = "脚部",  tex = "Feet" },
  { key = "finger1",  id = 11, inv = "INVTYPE_FINGER",   cn = "戒指1", tex = "Finger" },
  { key = "finger2",  id = 12, inv = "INVTYPE_FINGER",   cn = "戒指2", tex = "Finger" },
  { key = "trinket1", id = 13, inv = "INVTYPE_TRINKET",  cn = "饰品1", tex = "Trinket" },
  { key = "trinket2", id = 14, inv = "INVTYPE_TRINKET",  cn = "饰品2", tex = "Trinket" },
  { key = "main_hand", id = 16, inv = "INVTYPE_WEAPON",  cn = "主手",  tex = "MainHand" },
  { key = "off_hand",  id = 17, inv = "INVTYPE_OFFHAND", cn = "副手",  tex = "SecondaryHand" },
}

-- 槽位空着时用的占位图，取自游戏自己的角色面板，跟自己画的比一眼就是原生
NS.SLOT_TEX = "Interface\\PaperDollInfoFrame\\UI-PaperDoll-Slot-%s"

NS.SLOT_BY_ID, NS.SLOT_BY_KEY = {}, {}
for _, s in ipairs(NS.SLOTS) do
  NS.SLOT_BY_ID[s.id] = s
  NS.SLOT_BY_KEY[s.key] = s
end

--[[
    哪些 equipLoc 能进哪个槽。

    武器最麻烦：INVTYPE_WEAPON 是"两手都能拿的单手武器"，2HWEAPON 只能主手
    （双持职业也是这样，双手武器永远在主手），SHIELD/HOLDABLE 只能副手。
    胸甲有两个值：CHEST 和 ROBE，布甲胸是 ROBE，只认 CHEST 的话布甲职业
    的胸会一件都列不出来。
]]
NS.SLOT_ACCEPT = {
  head     = { INVTYPE_HEAD = true },
  neck     = { INVTYPE_NECK = true },
  shoulder = { INVTYPE_SHOULDER = true },
  back     = { INVTYPE_CLOAK = true },
  chest    = { INVTYPE_CHEST = true, INVTYPE_ROBE = true },
  wrist    = { INVTYPE_WRIST = true },
  hands    = { INVTYPE_HAND = true },
  waist    = { INVTYPE_WAIST = true },
  legs     = { INVTYPE_LEGS = true },
  feet     = { INVTYPE_FEET = true },
  finger   = { INVTYPE_FINGER = true },
  trinket  = { INVTYPE_TRINKET = true },
  main_hand = {
    INVTYPE_WEAPON = true, INVTYPE_WEAPONMAINHAND = true, INVTYPE_2HWEAPON = true,
    INVTYPE_RANGED = true, INVTYPE_RANGEDRIGHT = true, INVTYPE_THROWN = true,
  },
  off_hand = {
    INVTYPE_WEAPON = true, INVTYPE_WEAPONOFFHAND = true,
    INVTYPE_SHIELD = true, INVTYPE_HOLDABLE = true,
  },
}

-- ── 属性代号 ─────────────────────────────────────────────
--[[
    物品属性表的 key 是 ITEM_MOD_* 常量。**优先读游戏常量**，读不到才用数字。
    理由跟评级索引一样：暴雪挪过这些编号，写死迟早过期。

    71~74 是"多属性合一"的占位（力量敏捷智力三合一之类），
    真实分配由专精决定，这里按职业主属性拆开。
]]
local function mod(name, fallback)
  return _G["ITEM_MOD_" .. name] or fallback
end

NS.MOD = {
  [mod("AGILITY", 3)]          = "agi",
  [mod("STRENGTH", 4)]         = "str",
  [mod("INTELLECT", 5)]        = "int",
  [mod("STAMINA", 7)]          = "sta",
  [mod("CRIT_RATING", 32)]     = "crit",
  [mod("HASTE_RATING", 36)]    = "haste",
  [mod("VERSATILITY", 40)]     = "vers",
  [mod("MASTERY_RATING", 49)]  = "mastery",
  [mod("CR_SPEED", 61)]        = "speed",
  [mod("CR_LIFESTEAL", 62)]    = "leech",
  [mod("CR_AVOIDANCE", 63)]    = "avoid",
}

-- 多属性合一槽，独立处理（值要摊到主属性上）
NS.MOD_MULTI = {
  [mod("AGI_STR_INT", 71)] = { "agi", "str", "int" },
  [mod("AGI_STR", 72)]     = { "agi", "str" },
  [mod("AGI_INT", 73)]     = { "agi", "int" },
  [mod("STR_INT", 74)]     = { "str", "int" },
}

NS.SECONDARY = { "crit", "haste", "mastery", "vers" }
NS.PRIMARY = { "str", "agi", "int" }

NS.STAT_CN = {
  str = "力量", agi = "敏捷", int = "智力", sta = "耐力",
  crit = "暴击", haste = "急速", mastery = "精通", vers = "全能",
  speed = "加速", leech = "吸血", avoid = "闪避",
}

-- 单字缩写。候选行里"暴 4755 精 2245"比"暴击 4755 精通 2245"省一半宽度，
-- 而这一列恰恰是装备库里唯一有信息量的东西（同部位总属性相同，只有配比不同）
NS.STAT_SHORT = { crit = "暴", haste = "急", mastery = "精", vers = "全" }

-- 主属性按职业定，用来看哪一条要放大显示
NS.CLASS_PRIMARY = {
  warrior = "str", paladin = nil, hunter = "agi", rogue = "agi",
  priest = "int", deathknight = nil, shaman = nil, mage = "int",
  warlock = "int", monk = nil, druid = nil, demonhunter = "agi",
  evoker = "int",
}
-- 圣骑士/死骑/萨满/武僧/德鲁伊的主属性随专精变，见下面的 SPEC_PRIMARY
NS.SPEC_PRIMARY = {
  ["paladin:holy"] = "int", ["paladin:protection"] = "str", ["paladin:retribution"] = "str",
  ["deathknight:blood"] = "str", ["deathknight:frost"] = "str", ["deathknight:unholy"] = "str",
  ["shaman:elemental"] = "int", ["shaman:enhancement"] = "agi", ["shaman:restoration"] = "int",
  ["monk:brewmaster"] = "agi", ["monk:mistweaver"] = "int", ["monk:windwalker"] = "agi",
  ["druid:balance"] = "int", ["druid:feral"] = "agi", ["druid:guardian"] = "agi",
  ["druid:restoration"] = "int",
}

-- ── 90 级绿字换算 ────────────────────────────────────────
--[[
    rate = 每 1% 需要多少评级（DBC __combat_ratings 的 90 级那一列）
    dr   = 递减档位，{门槛, 折扣百分比}。每档只对**越过该门槛的那部分**打折。

    Maxroll 的验算例子：1320 急速（30%）再加 264 → 应为 36%，实际 35.4%，
    (1320 + 264×0.9) / 44 = 35.4 ✓
]]
NS.CURVE = {
  crit = {
    rate = 46,
    dr = { {0, 0}, {1380, 10}, {1840, 20}, {2300, 30}, {2760, 40}, {3220, 50}, {9200, 100} },
  },
  haste = {
    rate = 44,
    dr = { {0, 0}, {1320, 10}, {1760, 20}, {2200, 30}, {2640, 40}, {3080, 50}, {8800, 100} },
  },
  mastery = {
    rate = 46,
    dr = { {0, 0}, {1380, 10}, {1840, 20}, {2300, 30}, {2760, 40}, {3220, 50}, {9200, 100} },
  },
  vers = {
    rate = 54,
    dr = { {0, 0}, {1620, 10}, {2160, 20}, {2700, 30}, {3240, 40}, {3780, 50}, {10800, 100} },
  },
}

--[[
    专精精通系数：每 1 点精通给多少 %。来源 spec_mastery 表（客户端精通法术 effectN(1)）。

    织雾武僧 31.185 明显是量级错的（同一条法术里还有个 0.31185，差 100 倍），
    这里按 0.31185 处理，并在界面上标注该专精的精通%是估的。
]]
NS.MASTERY_COEFF = {
  ["deathknight:blood"] = 2.0,        ["deathknight:frost"] = 2.0,
  ["deathknight:unholy"] = 1.8,       ["demonhunter:devourer"] = 1.0,
  ["demonhunter:havoc"] = 2.25,       ["demonhunter:vengeance"] = 2.8,
  ["druid:balance"] = 0.75,           ["druid:feral"] = 2.0,
  ["druid:guardian"] = 1.0,           ["druid:restoration"] = 1.311,
  ["evoker:augmentation"] = 0.272,    ["evoker:devastation"] = 1.5,
  ["evoker:preservation"] = 1.8,      ["hunter:beast_mastery"] = 1.9,
  ["hunter:marksmanship"] = 0.625,    ["hunter:survival"] = 0.85,
  ["mage:arcane"] = 1.32,             ["mage:fire"] = 0.483,
  ["mage:frost"] = 1.6,               ["monk:brewmaster"] = 0.924,
  ["monk:mistweaver"] = 0.31185,      ["monk:windwalker"] = 2.3287,
  ["paladin:holy"] = 1.5,             ["paladin:protection"] = 1.0,
  ["paladin:retribution"] = 1.35,     ["priest:discipline"] = 1.35,
  ["priest:holy"] = 0.9084,           ["priest:shadow"] = 0.5,
  ["rogue:assassination"] = 1.7,      ["rogue:outlaw"] = 1.0,
  ["rogue:subtlety"] = 2.45,          ["shaman:elemental"] = 1.875,
  ["shaman:enhancement"] = 2.0,       ["shaman:restoration"] = 3.0,
  ["warlock:affliction"] = 2.5,       ["warlock:demonology"] = 1.45,
  ["warlock:destruction"] = 2.0,      ["warrior:arms"] = 1.1,
  ["warrior:fury"] = 1.4,             ["warrior:protection"] = 1.5,
}
NS.MASTERY_SUSPECT = { ["monk:mistweaver"] = true }

-- ── 职业 / 专精 ──────────────────────────────────────────
NS.CLASS_KEY = {
  [1] = "warrior", [2] = "paladin", [3] = "hunter", [4] = "rogue",
  [5] = "priest", [6] = "deathknight", [7] = "shaman", [8] = "mage",
  [9] = "warlock", [10] = "monk", [11] = "druid", [12] = "demonhunter",
  [13] = "evoker",
}

NS.SPEC_KEY = {
  [62]="arcane", [63]="fire", [64]="frost", [65]="holy",
  [66]="protection", [70]="retribution", [71]="arms", [72]="fury",
  [73]="protection", [102]="balance", [103]="feral", [104]="guardian",
  [105]="restoration", [250]="blood", [251]="frost", [252]="unholy",
  [253]="beast_mastery", [254]="marksmanship", [255]="survival", [256]="discipline",
  [257]="holy", [258]="shadow", [259]="assassination", [260]="outlaw",
  [261]="subtlety", [262]="elemental", [263]="enhancement", [264]="restoration",
  [265]="affliction", [266]="demonology", [267]="destruction", [268]="brewmaster",
  [269]="windwalker", [270]="mistweaver", [577]="havoc", [581]="vengeance",
  [1467]="devastation", [1468]="preservation", [1473]="augmentation", [1480]="devourer",
}

--[[
    战斗评级索引，**优先读游戏全局常量**，读不到才用写死的。

    写死的值来自 FrameXML/PaperDollFrame.lua，是 Lua 层 **1-based** 编号；
    DBC / wowdev 上那张表是 0-based（那边 CR_MASTERY 是 25），差 1，查资料别拿错。
]]
local function cr(name, fallback)
  return _G["CR_" .. name] or fallback
end

NS.CR = {
  crit    = cr("CR_CRIT_MELEE", 9),
  haste   = cr("CR_HASTE_MELEE", 18),
  mastery = cr("CR_MASTERY", 26),
  vers    = cr("CR_VERSATILITY_DAMAGE_DONE", 29),
  leech   = cr("CR_LIFESTEAL", 17),
  avoid   = cr("CR_AVOIDANCE", 21),
  speed   = cr("CR_SPEED", 14),
}

-- 品质色，跟游戏原生一致
NS.QUALITY_COLOR = {
  [0] = {0.62, 0.62, 0.62}, [1] = {1, 1, 1},       [2] = {0.12, 1, 0},
  [3] = {0, 0.44, 0.87},    [4] = {0.64, 0.21, 0.93}, [5] = {1, 0.5, 0},
  [6] = {0.90, 0.80, 0.50}, [7] = {0, 0.8, 1},     [8] = {0.90, 0.80, 0.50},
}
