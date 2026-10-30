--[[
    假客户端：给 tests/test_addon_lua.py 用。

    目的不是"能跑就行"，是**能抓错**：
    控件对象的字段和方法全部显式列出来，SetText 之类会真的存下来，
    这样"忘了给某个 FontString 赋值"或者"方法名拼错"会被断言抓到，
    而不是在游戏里表现成一块空白。

    控件没列到的方法走 no-op 兜底（WoW 的控件方法太多，全列不现实），
    但列出来的这些是界面逻辑真正依赖的，它们的行为必须是真的。
]]

-- ── 控件 ─────────────────────────────────────────────────
local proto
local function noop() end

proto = setmetatable({}, { __index = function() return noop end })

local function widget(kind)
  return setmetatable({ _kind = kind }, { __index = proto })
end

proto.CreateTexture     = function(self) return widget("Texture") end
proto.CreateFontString  = function(self) return widget("FontString") end
proto.CreateFrame       = function(self) return widget("Frame") end

-- 文本真的存下来，测试里断言得到
proto.SetText = function(self, t) rawset(self, "_text", t) end
proto.GetText = function(self) return rawget(self, "_text") end
proto.SetFormattedText = function(self, t) rawset(self, "_text", t) end

-- 字体：GetFont 要返回三个值，UI.text 里拿它去 SetFont
proto.GetFont = function(self) return "Fonts\\FRIZQT__.TTF", 12, "" end

-- 位置：OnDragStop 里要 GetPoint 的返回值存 SavedVariables
proto.GetPoint = function(self) return "CENTER", UIParent, "CENTER", 0, 0 end

proto.IsShown = function(self) return rawget(self, "_shown") or false end
proto.Show = function(self) rawset(self, "_shown", true) end
proto.Hide = function(self) rawset(self, "_shown", false) end

-- 给测试用的钩子：模拟点击
proto.Click = function(self)
  local h = rawget(self, "_onclick")
  if h then h(self) end
end
proto.SetScript = function(self, event, fn)
  if event == "OnClick" then rawset(self, "_onclick", fn) end
  rawset(self, "_script_" .. event, fn)
end
proto.GetScript = function(self, event) return rawget(self, "_script_" .. event) end

-- ── 全局对象 ─────────────────────────────────────────────
GameFontHighlight = widget("Font")
GameFontNormal = widget("Font")
GameFontHighlightSmall = widget("Font")
GameFontDisableSmall = widget("Font")
ChatFontNormal = widget("Font")
NumberFontNormal = widget("Font")
UIParent = widget("Frame")
GameTooltip = widget("Tooltip")
SlashCmdList = {}

RAID_CLASS_COLORS = {
  HUNTER = { r = 0.67, g = 0.83, b = 0.45 },
  WARRIOR = { r = 0.78, g = 0.61, b = 0.43 },
}

C_Timer = { After = function(sec, fn) end }

-- 记录所有创建出来的控件，测试里要按类型翻找（比如导出用的 EditBox 没名字）
ALL_WIDGETS = {}

function CreateFrame(kind, name, parent, template)
  local f = widget(kind)
  rawset(f, "_name", name)
  ALL_WIDGETS[#ALL_WIDGETS + 1] = f
  if name then _G[name] = f end
  return f
end

function print(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring((select(i, ...))) end
  _G.PRINTED = (_G.PRINTED or "") .. table.concat(t, " ") .. "\n"
end

-- ── 客户端注入的常量 ─────────────────────────────────────
-- 真身由客户端在加载插件前塞进 _G，值是官方编号。
ITEM_MOD_AGILITY = 3
ITEM_MOD_STRENGTH = 4
ITEM_MOD_INTELLECT = 5
ITEM_MOD_STAMINA = 7
ITEM_MOD_CRIT_RATING = 32
ITEM_MOD_HASTE_RATING = 36
ITEM_MOD_VERSATILITY = 40
ITEM_MOD_MASTERY_RATING = 49
ITEM_MOD_CR_SPEED = 61
ITEM_MOD_CR_LIFESTEAL = 62
ITEM_MOD_CR_AVOIDANCE = 63
ITEM_MOD_AGI_STR_INT = 71
ITEM_MOD_AGI_STR = 72
ITEM_MOD_AGI_INT = 73
ITEM_MOD_STR_INT = 74

CR_CRIT_MELEE = 9
CR_HASTE_MELEE = 18
CR_MASTERY = 26
CR_VERSATILITY_DAMAGE_DONE = 29
CR_LIFESTEAL = 17
CR_AVOIDANCE = 21
CR_SPEED = 14

INVTYPE_HEAD = "INVTYPE_HEAD"
INVTYPE_NECK = "INVTYPE_NECK"
INVTYPE_SHOULDER = "INVTYPE_SHOULDER"
INVTYPE_CLOAK = "INVTYPE_CLOAK"
INVTYPE_CHEST = "INVTYPE_CHEST"
INVTYPE_ROBE = "INVTYPE_ROBE"
INVTYPE_WRIST = "INVTYPE_WRIST"
INVTYPE_HAND = "INVTYPE_HAND"
INVTYPE_WAIST = "INVTYPE_WAIST"
INVTYPE_LEGS = "INVTYPE_LEGS"
INVTYPE_FEET = "INVTYPE_FEET"
INVTYPE_FINGER = "INVTYPE_FINGER"
INVTYPE_TRINKET = "INVTYPE_TRINKET"
INVTYPE_WEAPON = "INVTYPE_WEAPON"
INVTYPE_WEAPONMAINHAND = "INVTYPE_WEAPONMAINHAND"
INVTYPE_2HWEAPON = "INVTYPE_2HWEAPON"
INVTYPE_WEAPONOFFHAND = "INVTYPE_WEAPONOFFHAND"
INVTYPE_SHIELD = "INVTYPE_SHIELD"
INVTYPE_HOLDABLE = "INVTYPE_HOLDABLE"
INVTYPE_RANGED = "INVTYPE_RANGED"
INVTYPE_RANGEDRIGHT = "INVTYPE_RANGEDRIGHT"
INVTYPE_THROWN = "INVTYPE_THROWN"

-- ── 游戏数据（测试里改这些）───────────────────────────────
CURRENCY = {}

PLAYER = {
  name = "Demo",
  classID = 3,
  specID = 255,
  specName = "生存",
  level = 90,
  stats = { str = 0, agi = 2400, sta = 8000, int = 0 },
  rating = { crit = 1033, haste = 543, mastery = 1636, vers = 173,
             leech = 0, avoid = 0, speed = 0 },
  pct = { crit = 38.57, haste = 12.34, mastery = 29.84, vers = 3.20 },
}

-- CR 编号 -> 我们的代号。真身由 Data.lua 的 NS.CR 给，这里对齐同一套。
local CRMAP = {
  [9] = "crit", [18] = "haste", [26] = "mastery", [29] = "vers",
  [17] = "leech", [21] = "avoid", [14] = "speed",
}

function GetCombatRating(cr) return PLAYER.rating[CRMAP[cr]] or 0 end
-- 故意返回 0：让 Calc.rate 走"实测不可用 → 用表"这条路径。
-- 实测路径单独在 test_measured_rate 里测。
function GetCombatRatingBonus(cr) return PLAYER.rating_bonus and PLAYER.rating_bonus[CRMAP[cr]] or 0 end

function GetCritChance() return PLAYER.pct.crit end
function GetHaste() return PLAYER.pct.haste end
function GetMastery() return PLAYER.mastery_points or 35.1 end
function GetMasteryEffect() return PLAYER.pct.mastery end
function GetVersatilityBonus(cr) return PLAYER.vers_passive or 0 end
function GetSpellCritChance(school) return PLAYER.pct.crit end

function UnitName(u) return PLAYER.name end
function UnitClass(u) return PLAYER.name, "HUNTER", PLAYER.classID end
function UnitLevel(u) return PLAYER.level end
function UnitStat(u, i)
  local k = ({ "str", "agi", "sta", "int" })[i]
  return PLAYER.stats[k] or 0
end
function GetSpecialization() return 1 end
function GetSpecializationInfo(i) return PLAYER.specID, PLAYER.specName end
function GetBuildInfo() return "12.1.5", "70575", "Oct 1 2026", 120105 end
function date(fmt) return "2026-10-31 05:00" end

-- ── 物品 ─────────────────────────────────────────────────
ITEM_DB = {}       -- [link] = { name, quality, ilvl, loc, icon, stats }
EQUIPPED = {}      -- [slotID] = link
BAGS = {}          -- [[bag, slot]] = link

function GetItemInfoInstant(id)
  local d = ITEM_DB[id]
  return id, d and d.type or "Armor", d and d.sub or "Plate",
         d and d.loc or "INVTYPE_HEAD", d and d.icon or 134400, 4, 1
end

C_Item = {
  GetItemInfo = function(x)
    local d = ITEM_DB[x]
    if not d then return nil end
    -- 顺序必须跟真 API 一致：name, link, quality, ilvl, reqLevel,
    -- type, subType, stack, equipLoc, icon, sellPrice, classID, subclassID...
    return d.name, x, d.quality, d.ilvl, 1, "Armor", "Plate", 1, d.loc, d.icon,
           0, 4, 1, 1, 0, 0, false
  end,
  GetItemStats = function(x)
    local d = ITEM_DB[x]
    return d and d.stats or nil
  end,
  RequestLoadItemDataByID = function(id) end,
}

C_Container = {
  GetContainerNumSlots = function(bag) return BAGS[bag] and #BAGS[bag] or 0 end,
  GetContainerItemLink = function(bag, slot)
    local row = BAGS[bag]
    return row and row[slot]
  end,
}

function GetInventoryItemLink(unit, slotID) return EQUIPPED[slotID] end

-- ── 造数据的小工具 ───────────────────────────────────────
function mkItem(id, ilvl, stats, loc, name)
  local link = string.format("|cffa335ee|Hitem:%d::::::::90:::::|h[%s]|h|r", id, name or ("Item" .. id))
  ITEM_DB[link] = {
    name = name or ("Item " .. id),
    quality = 4, ilvl = ilvl, loc = loc or "INVTYPE_HEAD",
    icon = 134400, stats = stats,
  }
  return link
end
