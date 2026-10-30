--[[
    WoWGear · 改造：自选属性、宝石、附魔

    ── 为什么单开一个文件 ──────────────────────────────────
    这三样东西在网页端都已经做过：`src/wowlib/session.py` 里的
    `socket_count` / `set_gem` / `set_enchant` / `set_drilled`，
    还有库里 `gems`、`enchants` 两张表和 `items.socket_info`。
    插件这边要做的不是重新设计规则，而是**把同一套规则搬过来**：

      · 戒指项链保底一个棱彩孔（GUARANTEED_SOCKETS）
      · 头/腰/腕可以用打孔器开一个（DRILLABLE_SLOTS），开没开是玩家状态
      · 附魔只能附在对应部位上
      · 制造业/装绑装备的绿字是"由玩家选两条"（stat 24/25 占位槽）

    所以这个文件里的常量、判断、默认值都跟 python 端保持一致，
    改规则时两边一起改，别让它们各走各的。

    ── 数值从哪来 ──────────────────────────────────────────
    装备属性是"权重 × 装等系数"，要按目标装等折算。
    宝石和附魔不一样：它们是**固定数值**，一颗 295 的急速宝石永远是
    +13 急速，跟你多少装等无关。所以它们直接加在结果上，不走曲线。
]]

local ADDON, NS = ...

local Mod = {}
NS.Mod = Mod

-- ── 解析 ─────────────────────────────────────────────────
local function split(line)
  local f = {}
  for cell in (line .. "|"):gmatch("([^|]*)|") do f[#f + 1] = cell end
  return f
end

-- "crit:5,haste:12" -> {crit=5, haste=12}
local function parseStats(str)
  local out = {}
  for pair in (str or ""):gmatch("[^,]+") do
    local k, v = pair:match("(%a+):(%-?%d+)")
    if k then out[k] = tonumber(v) end
  end
  return out
end

NS.Gems = {}
NS.GemList = {}

for line in (NS.GEM_RAW or ""):gmatch("[^\n]+") do
  local f = split(line)
  local id = tonumber(f[1])
  if id then
    local g = {
      id = id,
      name = f[2],
      quality = tonumber(f[3]) or 2,
      ilvl = tonumber(f[4]) or 0,
      kind = f[5] or "",
      stats = parseStats(f[6]),
    }
    NS.Gems[id] = g
    NS.GemList[#NS.GemList + 1] = g
  end
end

-- 列表顺序：装等高的在前（295 比 278 好），同装等按品质
table.sort(NS.GemList, function(a, b)
  if a.ilvl ~= b.ilvl then return a.ilvl > b.ilvl end
  if a.quality ~= b.quality then return a.quality > b.quality end
  return a.id < b.id
end)

NS.Enchs = {}
NS.EnchBySlot = {}

for line in (NS.ENCH_RAW or ""):gmatch("[^\n]+") do
  local f = split(line)
  local id = tonumber(f[1])
  if id then
    local e = { id = id, name = f[2], slot = f[3], stats = parseStats(f[4]) }
    NS.Enchs[id] = e
    NS.EnchBySlot[e.slot] = NS.EnchBySlot[e.slot] or {}
    table.insert(NS.EnchBySlot[e.slot], e)
  end
end

-- ── 规则常量（跟 python 端一致）──────────────────────────
-- 戒指和项链保底一个棱彩孔
Mod.GUARANTEED_SOCKETS = { finger = 1, neck = 1 }
-- 能用打孔器开一个孔的部位（开没开只有玩家知道，由玩家勾）
Mod.DRILLABLE_SLOTS = { head = true, waist = true, wrist = true }

-- 自选属性可以选的四条
Mod.OPT_CODES = { "crit", "haste", "mastery", "vers" }

--[[
    改造状态：按**物品 id** 存。

    为什么不按部位：宝石和附魔是长在物品上的，换掉装备它们跟着走。
    按部位存的话，把戒指 A 换成 B，B 会莫名其妙继承 A 的宝石。

    状态结构：
        NS.Mods[itemID] = {
          opt1 = "crit", opt2 = "mastery",   -- 自选槽分给哪两条（各一半）
          gems = { [1] = 240856, ... },      -- 第 i 个孔插的宝石
          ench = 7935,                       -- 附魔 id
          drill = true,                      -- 有没有用打孔器开过孔
        }
]]
NS.Mods = {}

function Mod.get(itemID)
  return itemID and NS.Mods[itemID] or nil
end

function Mod.ensure(itemID)
  if not itemID then return nil end
  local m = NS.Mods[itemID]
  if not m then
    m = {
      opt1 = "crit",
      opt2 = "mastery",
      gems = {},
    }
    NS.Mods[itemID] = m
  end
  return m
end

-- 自选槽分给哪两条，返回 {code = 比例}。没配过就用默认暴击+精通各半。
function Mod.optSplit(itemID)
  local m = itemID and NS.Mods[itemID]
  if m and m.opt1 then
    if m.opt1 == m.opt2 then
      -- 两条选了同一个属性，那就是 100% 全给它（游戏里也允许这么选）
      return { [m.opt1] = 1 }
    end
    return { [m.opt1] = 0.5, [m.opt2] = 0.5 }
  end
  return NS.OPT_SPLIT or { crit = 0.5, mastery = 0.5 }
end

-- 这件装备有几个孔（含打孔器开的那一个）
function Mod.socketCount(item)
  if not item or not item.id then return 0 end
  local raw = NS.LIB and NS.LIB[item.id]
  local base = raw and tonumber(raw[11]) or 0
  base = math.max(base, Mod.GUARANTEED_SOCKETS[item.slot] or 0)
  local m = NS.Mods[item.id]
  if m and m.drill and Mod.DRILLABLE_SLOTS[item.slot] then
    base = base + 1
  end
  return base
end

function Mod.canDrill(item)
  return item and Mod.DRILLABLE_SLOTS[item.slot] ~= nil
end

--[[
    这件装备在当前改造下额外给多少属性。

    返回 { crit = 26, haste = 13, ... }，主属性用主属性代号返回。

    三个来源：
      1. 宝石：每颗的 stats 直接加
      2. 附魔：同上
      3. "main" 占位：宝石和附魔里写的是"主属性 +23"这种，要落到本职业
         真吃的那条上——猎人看到"敏捷 +23"，法师看到"智力 +36"
]]
function Mod.bonus(item, primary)
  local out = {}
  if not item or not item.id then return out end
  local m = NS.Mods[item.id]

  local function add(stats)
    for code, v in pairs(stats or {}) do
      if code == "main" then
        code = primary or "agi"
      end
      out[code] = (out[code] or 0) + v
    end
  end

  if m then
    local n = Mod.socketCount(item)
    for i = 1, n do
      local gid = m.gems[i]
      local g = gid and NS.Gems[gid]
      if g then add(g.stats) end
    end
    -- 附魔必须跟装备同部位。python 端的 _enchant_after 也是这么卡的：
    -- 允许乱附的话，胸甲的 93 耐力能算到戒指上，数字变了但游戏里根本附不上。
    -- 界面里已经按部位筛过，这里是最后一道防线（配置可能从存档或旧版本带过来）。
    local e = m.ench and NS.Enchs[m.ench]
    if e and e.slot == item.slot then add(e.stats) end
  end

  return out
end

-- 某个部位能用的附魔列表（附魔只能附在对应部位，乱附数字会算歪）
function Mod.enchOptions(slot)
  return NS.EnchBySlot[slot] or {}
end

-- 一句话描述改造，画在列表和底栏上
function Mod.summary(item)
  if not item or not item.id then return "" end
  local m = NS.Mods[item.id]
  if not m then return "" end

  local parts = {}
  local n = Mod.socketCount(item)
  local filled = 0
  for i = 1, n do
    if m.gems[i] and NS.Gems[m.gems[i]] then filled = filled + 1 end
  end
  if filled > 0 then parts[#parts + 1] = string.format("宝石 %d/%d", filled, n) end
  if m.ench and NS.Enchs[m.ench] then parts[#parts + 1] = "附魔" end
  if m.drill then parts[#parts + 1] = "已打孔" end

  return table.concat(parts, " ")
end

-- 有没有值得显示的改造（列表上打标用）
function Mod.hasAny(item)
  if not item or not item.id then return false end
  local m = NS.Mods[item.id]
  if not m then return false end
  return m.ench ~= nil or m.drill or next(m.gems) ~= nil
end

-- ── 属性文字 ────────────────────────────────────────────
function Mod.statText(stats, primary)
  local order = { "str", "agi", "int", "sta", "crit", "haste", "mastery", "vers" }
  local parts = {}
  for _, code in ipairs(order) do
    local v = stats[code]
    if v and v > 0 then
      parts[#parts + 1] = string.format("%s +%d", NS.STAT_CN[code] or code, v)
    end
  end
  if stats.main then
    local code = primary or "agi"
    parts[#parts + 1] = string.format("%s +%d", NS.STAT_CN[code] or code, stats.main)
  end
  return table.concat(parts, "  ")
end
