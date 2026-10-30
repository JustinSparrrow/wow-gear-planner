--[[
    WoWGear · 装等曲线 + 装备库取数

    ── 为什么这里又出现曲线了 ──────────────────────────────
    前面说过"插件里不需要 curve.py"，那说的是**身上和背包里的装备**——
    那些物品 `C_Item.GetItemStats` 直接给数值。

    但"我还没有的装备"不一样：客户端里根本没有它的数据（没有 link，
    缓存里也没有），拿不到真实属性。能拿到的只有库里存的分配权重
    （alloc，万分比），必须靠装等系数把权重折成数值。

    所以这个文件就是 curve.py 的 Lua 版：同一张 25 行采样表、同一套
    五类部位分档、同样的线性插值和越界外推。

    ── 为什么不按每件装备自己的装等算 ──────────────────────
    库里各赛季的基础装等是混着的（59 / 108 / 197 / 298 都在一张表里），
    跟玩家身上的 334 不是一个体系。各按各的装等比，结论必然是
    "装等低的装备属性更差"——这是废话，不是规划。

    统一按角色当前装等算之后，比的就只剩属性配比：
    "同样是 334 的这个部位，这件给 212 暴击 276 精通，我身上那件给
    198 暴击 290 精通"。这才是要回答的问题。

    代价是丢了"这件装等更高"的信息，所以列表里仍然显示物品自身的
    参考装等，头上也标一句"候选按 xxx 装等计算"。
]]

local ADDON, NS = ...

local Lib = {}
NS.Lib = Lib

-- 列名 -> 列号，避免每次插值都去查字符串
local COL = {}
for i, name in ipairs(NS.CURVE_COLS or {}) do
  COL[name] = i
end

local ROWS = NS.CURVE_ROWS or {}

--[[
    槽位 -> 曲线档位。跟 curve.py 的 kind_of 一一对应。

    分档依据是游戏表 RandPropPoints：头胸腿一档、肩手腰脚一档、腕背一档，
    颈/戒指/饰品是"没有主属性"的特例档（绿字有 1.42 倍预算补偿）。

    武器按**几只手**分：双手武器/远程武器的预算跟头胸腿同档，单手/副手
    低一档。这条以前在 python 端踩过坑——把双手武器按单手算，321 的长柄斧
    敏捷算成 69，游戏里是 167，少了 2.4 倍。
]]
local function kindOf(slot, invType)
  if slot == "head" or slot == "chest" or slot == "legs" then return "big" end
  if slot == "shoulder" or slot == "hands" or slot == "waist" or slot == "feet" then
    return "mid"
  end
  if slot == "wrist" or slot == "back" then return "small" end
  if slot == "neck" or slot == "finger" or slot == "trinket" then return "jewel" end
  -- 15 弓 / 17 双手 / 26 弩
  if invType == 15 or invType == 17 or invType == 26 then return "big" end
  return "weapon"
end

Lib.kindOf = kindOf

-- 二分找第一个 >= ilvl 的行号
local function lowerBound(ilvl)
  local lo, hi = 1, #ROWS
  while lo <= hi do
    local mid = math.floor((lo + hi) / 2)
    if ROWS[mid][1] < ilvl then lo = mid + 1 else hi = mid - 1 end
  end
  return lo
end

--[[
    这个装等、这类部位的三条系数（主属性 / 耐力 / 绿字）。

    用法：属性数值 = 分配权重 × 系数 × 1e-4

    超出采样范围就外推（拿最后两点的斜率），不要钳死在最后一个值上——
    新赛季装等一涨，钳死会让所有新装备的属性停在旧装等的水平，
    而且看起来"很正常"，比报错难查得多。
]]
function Lib.factors(ilvl, kind)
  local n = #ROWS
  if n == 0 then return { main = 0, sta = 0, sec = 0 } end

  local out = {}
  local function at(part)
    local ci = COL[part .. "_" .. kind]
    if not ci then return 0 end

    local i = lowerBound(ilvl)
    if i <= 1 then return ROWS[1][ci] end
    if i > n then
      local lo, hi = ROWS[n - 1], ROWS[n]
      local span = hi[1] - lo[1]
      if span == 0 then return hi[ci] end
      return hi[ci] + (hi[ci] - lo[ci]) / span * (ilvl - hi[1])
    end
    local lo, hi = ROWS[i - 1], ROWS[i]
    local span = hi[1] - lo[1]
    if span == 0 then return hi[ci] end
    local ratio = (ilvl - lo[1]) / span
    return lo[ci] + (hi[ci] - lo[ci]) * ratio
  end

  out.main = at("main")
  out.sta  = at("sta")
  out.sec  = at("sec")

  -- 颈/戒指/饰品没有主属性，采样出来是 0；但饰品有（比如加敏捷的主动饰品）。
  -- 全按 0 算的话饰品主属性会凭空消失，所以回退到同档位的腕/背系数。
  if kind == "jewel" and out.main == 0 then
    local ci = COL["main_small"]
    if ci and n > 0 then out.main = ROWS[1][ci] end
  end

  return out
end

-- 装等外推得太远时结果不可信，界面要标出来
function Lib.ilvlInRange(ilvl)
  local n = #ROWS
  if n == 0 then return false end
  return ilvl >= ROWS[1][1] and ilvl <= ROWS[n][1]
end

-- ── 装备库取数 ───────────────────────────────────────────
local function bucketOf(code)
  if code == "str" or code == "agi" or code == "int" then return "main" end
  if code == "sta" then return "sta" end
  return "sec"
end

--[[
    一件库装备在指定装等下的属性数值。

    primary 用来决定"力敏智三合一"那个槽该摊到哪条属性上——
    跟 Calc.itemStats 对已装备物品的处理保持一致，否则同一个槽
    在两边的算法不同，模拟结果会莫名差几点。
]]
function Lib.statsFor(itemID, ilvl, primary)
  local raw = NS.LIB[itemID]
  if not raw then return nil end
  local alloc = raw[6]
  local f = Lib.factors(ilvl, kindOf(raw[4], raw[5]))

  local out = {}
  for statID, w in pairs(alloc) do
    local code = NS.MOD[statID]
    if code then
      local b = bucketOf(code)
      out[code] = (out[code] or 0) + w * f[b] * 1e-4
    else
      local multi = NS.MOD_MULTI[statID]
      if multi then
        for _, c in ipairs(multi) do
          if c == primary then
            out[c] = (out[c] or 0) + w * f.main * 1e-4
            break
          end
        end
      end
    end
  end

  for k, v in pairs(out) do out[k] = NS.round(v) end
  return out
end

-- 槽位 -> equipLoc，用来复用 Gear.accepts 那套判断
local EQUIP_LOC = {
  head = "INVTYPE_HEAD", neck = "INVTYPE_NECK", shoulder = "INVTYPE_SHOULDER",
  back = "INVTYPE_CLOAK", chest = "INVTYPE_CHEST", wrist = "INVTYPE_WRIST",
  hands = "INVTYPE_HAND", waist = "INVTYPE_WAIST", legs = "INVTYPE_LEGS",
  feet = "INVTYPE_FEET", finger = "INVTYPE_FINGER", trinket = "INVTYPE_TRINKET",
}

local function equipLocOf(slot, invType)
  if slot == "main_hand" then
    if invType == 17 then return "INVTYPE_2HWEAPON" end
    if invType == 15 or invType == 26 then return "INVTYPE_RANGEDRIGHT" end
    return "INVTYPE_WEAPON"
  end
  if slot == "off_hand" then
    if invType == 14 then return "INVTYPE_SHIELD" end
    if invType == 23 then return "INVTYPE_HOLDABLE" end
    return "INVTYPE_WEAPONOFFHAND"
  end
  return EQUIP_LOC[slot] or ""
end

--[[
    把库条目包成跟 NS.Item.get 一样的形状，这样 UI 和 Calc 不用区分来源。

    图标走 GetItemInfoInstant——它是同步的，不需要物品被缓存，
    任意 itemID 都能拿到图标和类型。名字用库里的中文名（客户端可能
    没有这件物品的本地化数据，所以不指望 GetItemInfo）。
]]
function Lib.build(itemID, ilvl, primary)
  local raw = NS.LIB[itemID]
  if not raw then return nil end
  local _, _, _, _, icon = GetItemInfoInstant(itemID)

  return {
    id = itemID,
    name = raw[1],
    ilvl = ilvl,
    baseIlvl = raw[2],
    quality = raw[3],
    slot = raw[4],
    invType = raw[5],
    equipLoc = equipLocOf(raw[4], raw[5]),
    icon = icon,
    stats = Lib.statsFor(itemID, ilvl, primary),
    fromLib = true,
    -- 名字不能叫 current：Gear.candidates 里 item.current 是"身上那件"，
    -- 撞名字会让库里所有本赛季装备在列表里显示成"当前"
    curSeason = raw[7] == 1,
    source = NS.LIB_SRC[itemID],
  }
end

-- 这件装备能不能被本专精用（库里 123 件有专精限制，比如法系/物理饰品）
function Lib.allowed(itemID, specID)
  local list = NS.LIB_SPEC[itemID]
  if not list then return true end          -- 没限制 = 通用
  for _, s in ipairs(list) do
    if s == specID then return true end
  end
  return false
end

-- ── 按部位建索引 ─────────────────────────────────────────
--[[
    3570 件全表遍历一次要几毫秒，而且每次敲键盘都会触发一次搜索。
    预先按槽位分桶之后，搜"头部"只需遍历那一百多件，快一个数量级。

    pairs 遍历只做一次（首次搜索时懒建），代价可以忽略。
]]
local bySlot

local function ensureIndex()
  if bySlot then return end
  bySlot = {}
  for id, raw in pairs(NS.LIB) do
    local s = raw[4]
    local bucket = bySlot[s]
    if not bucket then bucket = {}; bySlot[s] = bucket end
    bucket[#bucket + 1] = id
  end
end

-- 这个槽位在库里有多少件（切换 tab 时显示"库 116"用）
function Lib.countFor(slotKey, specID)
  ensureIndex()
  local family = slotKey:gsub("%d+$", "")
  local list = bySlot[family]
  if not list then return 0 end
  local n = 0
  for _, id in ipairs(list) do
    if Lib.allowed(id, specID) then n = n + 1 end
  end
  return n
end

--[[
    搜索。keyword 为空就是全部。

    用 plain find 不用模式匹配：玩家搜「之牙」这种带特殊字符的名字时，
    gsub/find 的模式语法会把 `-` `.` `(` 当成元字符，搜不出东西还不知道为什么。
]]
function Lib.search(slotKey, keyword, specID)
  ensureIndex()
  local family = slotKey:gsub("%d+$", "")
  local list = bySlot[family] or {}
  local kw = (keyword or ""):lower()

  local out = {}
  for _, id in ipairs(list) do
    local raw = NS.LIB[id]
    if Lib.allowed(id, specID) then
      if kw == "" or raw[1]:lower():find(kw, 1, true) then
        out[#out + 1] = id
      end
    end
  end
  return out
end

--[[
    库物品没有 link，GameTooltip:SetHyperlink 用不了（客户端可能不认这件物品）。
    自己拼一个，至少名字、属性、掉落来源都能看到——而"从哪刷"恰恰是规划时
    最想知道的一件事。
]]
function Lib.tooltip(item, owner)
  GameTooltip:SetOwner(owner or UIParent, "ANCHOR_LEFT")
  GameTooltip:ClearLines()

  local q = NS.QUALITY_COLOR[item.quality or 1] or { 1, 1, 1 }
  GameTooltip:AddLine(item.name, q[1], q[2], q[3])
  GameTooltip:AddLine(string.format("%d 装等 · %s", item.ilvl,
    NS.SLOT_BY_KEY[item.slot] and NS.SLOT_BY_KEY[item.slot].cn or item.slot),
    0.7, 0.7, 0.7)

  local st = item.stats or {}
  local order = { "agi", "str", "int", "sta", "crit", "haste", "mastery", "vers" }
  for _, code in ipairs(order) do
    local v = st[code]
    if v and v ~= 0 then
      GameTooltip:AddDoubleLine(NS.STAT_CN[code] or code,
        string.format("+%d", v), 1, 1, 1, 0.2, 1, 0.2)
    end
  end

  if item.source then
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine(item.source, 1, 0.82, 0.25)
  end
  GameTooltip:AddLine(" ")
  GameTooltip:AddLine("|cff888888属性按 " .. item.ilvl .. " 装等折算，不是实物数值|r")
  GameTooltip:Show()
end
