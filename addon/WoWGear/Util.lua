--[[
    WoWGear · 物品读取与格式化

    ── 为什么全部用 link 而不是 itemID ────────────────────
    `C_Item.GetItemInfo(271492)` 拿到的装等是**基础装等**。现代魔兽的装备
    装等由一堆 bonus ID 决定（战火/泰坦/赛季升级/制造业附加），同一个 itemID
    可以是 334 也可以是 340，区别全在 link 末尾那串 `[1]=...:2=...` 里。

    `C_Item.GetItemInfo(fullLink)` 才能返回这件具体物品的真实装等，
    `C_Item.GetItemStats(fullLink)` 才能返回真实属性值。用 itemID 查的话，
    整套模拟的基准就是错的，而且错得不明显——只会表现为"算出来的数
    总比游戏里少一点"。

    ── 为什么缓存要整表清空 ──────────────────────────────
    物品在背包里换位置、镶嵌/取下宝石、升级装等，link 就变了。
    按 itemID 做增量缓存很容易拿到过期数据，而这里的数据量（几十件）
    重建一次也就几毫秒。宁可全丢重来，不要维护一份会骗人的缓存。
]]

local ADDON, NS = ...

--[[
    容器 API 在 9.0 从全局挪进了 C_Container。这里补一层转发，
    老客户端（以及某些私有服务器）上也能跑，不至于一打开就报
    "attempt to index a nil value (global 'C_Container')"。
]]
if not C_Container then
  C_Container = {
    GetContainerNumSlots = GetContainerNumSlots,
    GetContainerItemLink = GetContainerItemLink,
  }
end

local floor = math.floor

-- ── 数字格式化 ───────────────────────────────────────────
function NS.round(v)
  return floor((v or 0) + 0.5)
end

function NS.fmt(v, digits)
  return string.format("%." .. (digits or 0) .. "f", tonumber(v) or 0)
end

--[[
    Δ 值带正负号。0 显示成 "·"，因为 "+0" 和 "-0" 都会让人以为有变化。
    小于显示精度时也归零，否则界面上一堆 +0.001 在闪。
]]
function NS.signed(v, digits)
  v = tonumber(v) or 0
  local eps = 0.5 * (10 ^ -(digits or 0))
  if v > -eps and v < eps then return "·" end
  return string.format("%+." .. (digits or 0) .. "f", v)
end

-- 提升绿色、下降红色。跟暴雪原生装备对比 tooltip 一致，不要反过来。
function NS.deltaColor(v)
  v = tonumber(v) or 0
  if v > 0 then return "|cff1eff00" end
  if v < 0 then return "|cffff5555" end
  return "|cff888888"
end

function NS.colorizeDelta(v, digits)
  return NS.deltaColor(v) .. NS.signed(v, digits) .. "|r"
end

-- 大数字加千分位，跟游戏里一致（1,636 而不是 1636）
function NS.comma(v)
  local s = tostring(NS.round(v))
  local out = s:reverse():gsub("(%d%d%d)", "%1,"):reverse()
  return (out:gsub("^,", ""))
end

-- ── 物品 ─────────────────────────────────────────────────
NS.Item = { cache = {} }

function NS.Item.clear()
  wipe(NS.Item.cache)
end

local function first(...)
  return ...
end

--[[
    读一件物品。传 link（推荐）或 itemID。

    返回 nil 表示客户端还没有这件物品的数据——这时候不要拿 0 去凑，
    要显式告诉调用方"还没读到"，否则界面上这件装备会显示成"什么都没有"，
    跟真的空槽混在一起。

    返回 { id, link, name, ilvl, quality, icon, equipLoc, stats }
]]
--[[
    读物品的属性表。

    **这个 API 的签名各版本不一致，必须容错**：
      - 新：C_Item.GetItemStats(itemInfo)     返回 table
      - 老：GetItemStats(itemLink, statTable) 把结果填进你给的表
    参数可能是 link 字符串、itemID 数字、ItemInfo 对象，全试一遍。

    为什么值得这么啰嗦：它失败的时候**不报错，只是返回空表**。
    表现是"换装备后面板纹丝不动、所有候选差值都是 0"——
    看起来像插件整体坏了，其实只是这一行没拿到数据。
    （这次就是栽在这上面。）
]]
function NS.Item.readStats(link, id)
  local attempts = {
    function() return C_Item.GetItemStats(link) end,
    function() return C_Item.GetItemStats(id) end,
    function()
      local t = {}
      if GetItemStats then GetItemStats(link, t) end
      return t
    end,
    function()
      local t = {}
      if GetItemStats then GetItemStats(id, t) end
      return t
    end,
  }
  local tried = {}
  for _, fn in ipairs(attempts) do
    local ok, raw = pcall(fn)
    if ok and type(raw) == "table" and next(raw) ~= nil then
      NS.Item.statsPath = (#tried == 0) and "ok" or table.concat(tried, "+")
      return raw
    end
    tried[#tried + 1] = ok and "empty" or "err"
  end
  NS.Item.statsPath = "FAIL:" .. table.concat(tried, ",")
  return nil
end

--[[
    属性表的 key 可能是三种东西，全都要认：

      数字       32                     —— 正常的 ITEM_MOD_CRIT_RATING
      常量名字符串 "ITEM_MOD_CRIT_RATING" —— 某些版本直接给名字
      属性名      "Crit" / "CriticalStrike"

    **这次就是栽在这里**：GetItemStats 返回了表，但 key 一个都没命中，
    stats 保持空 → 所有差值恒为 0 → 面板纹丝不动。而且它不报错，
    自检打出来的括号里也是空的，因为 table.concat(空表) 是空字符串，
    不是 nil。（"拿到了表但一个 key 都不认识"这种情况下要单独标出来。）
]]
local MOD_BY_NAME = {
  agility = 3, strength = 4, intellect = 5, stamina = 7,
  crit = 32, criticalstrike = 32, critrating = 32,
  haste = 36, hasterating = 36,
  mastery = 49, masteryrating = 49,
  versatility = 40, versatilityrating = 40,
  speed = 61, leech = 62, lifesteal = 62, avoidance = 63,
}

--[[
    实测拿到的 key 长这样（一条自检日志，来自真实客户端）：

      ITEM_MOD_MASTERY_RATING_SHORT = 99
      ITEM_MOD_STAMINA_SHORT        = 3777
      ITEM_MOD_AGILITY_SHORT        = 183
      ITEM_MOD_VERSATILITY          = 99
      RESISTANCE0_NAME              = 248      -- 护甲

    也就是说 key 是**全局常量的名字**，不是它的值。中文客户端里
    `_G["ITEM_MOD_AGILITY_SHORT"]` 的值是"敏捷"（字符串），所以
    `_G[k]` 那条路永远拿到 string、拿不到数字，之前整个映射自然全落空。
    （之前我只想到"数字 id"和"属性名"两种，恰好都不是。）

    结论：别指望整串精确匹配，**找特征词**。名字里含 MASTERY 就是精通，
    含 AGILITY 就是敏捷——前缀统一是 ITEM_MOD_，后缀是 _SHORT 或 _RATING，
    中间还会夹 RATING，用子串匹配全都吃得下。
]]
local MOD_TOKENS = {
  { "VERSATILITY", 40 },   -- 放最前：别的词都不含它，先排除干扰
  { "MASTERY",     49 },
  { "STAMINA",      7 },
  { "AGILITY",      3 },
  { "STRENGTH",     4 },
  { "INTELLECT",    5 },
  { "HASTE",       36 },
  { "CRIT",        32 },
  { "AVOIDANCE",   63 },
  { "LEECH",       62 },
  { "LIFESTEAL",   62 },
  { "SPEED",       61 },
}

-- 中文客户端如果哪天直接给本地化名字，也认
local MOD_CN = {
  ["耐力"] = 7,  ["敏捷"] = 3,  ["力量"] = 4,  ["智力"] = 5,
  ["暴击"] = 32, ["急速"] = 36, ["精通"] = 49, ["全能"] = 40,
  ["加速"] = 61, ["吸血"] = 62, ["闪避"] = 63,
}

function NS.Item.modID(k)
  if type(k) == "number" then return k end
  if type(k) ~= "string" then return k end

  if MOD_CN[k] then return MOD_CN[k] end

  -- 万一某个客户端版本给的就是数字常量名，_G 能直接查到
  local v = _G[k]
  if type(v) == "number" then return v end

  local norm = k:upper():gsub("[^%u]", "")

  -- 护甲单独标出来：它不参与绿字换算，但要知道是"认出来了、只是不算"
  if norm:find("^RESISTANCE") or norm:find("ARMOR", 1, true) then
    return "armor"
  end

  for _, t in ipairs(MOD_TOKENS) do
    if norm:find(t[1], 1, true) then return t[2] end
  end

  -- "Crit" / "CriticalStrike" / "CritRating" 这类写法
  return MOD_BY_NAME[norm:lower()] or k
end

function NS.Item.get(linkOrID)
  if not linkOrID then return nil end
  local key = linkOrID

  local hit = NS.Item.cache[key]
  if hit then return hit ~= false and hit or nil end

  local name, itemLink, quality, ilvl, _, _, _, _, equipLoc, icon = C_Item.GetItemInfo(linkOrID)
  if not name then
    NS.Item.cache[key] = false          -- 记下"读不到"，别反复去查
    return nil
  end

  -- icon 有时返回 0，用同步的 GetItemInfoInstant 兜一下（它不依赖缓存）
  local id
  if type(linkOrID) == "number" then
    id = linkOrID
  else
    id = tonumber(tostring(linkOrID):match("item:(%d+)")) or 0
  end
  if not icon or icon == 0 then
    local _, _, _, _, instantIcon = GetItemInfoInstant(id)
    icon = instantIcon
  end

  local stats = {}
  local raw = NS.Item.readStats(itemLink or linkOrID, id)
  if raw then
    for modID, value in pairs(raw) do
      modID = NS.Item.modID(modID)
      -- 护甲认得出但不参与计算：它不是绿字，跟百分比换算无关
      local code = (modID ~= "armor") and NS.MOD[modID] or nil
      if code then
        stats[code] = (stats[code] or 0) + value
      else
        local multi = NS.MOD_MULTI[modID]
        if multi then
          -- 多属性合一的槽（力敏智三合一）按职业主属性摊，见 Calc.primaryOf
          stats._multi = stats._multi or {}
          table.insert(stats._multi, { codes = multi, value = value })
        end
      end
    end
  end

  if raw and next(stats) == nil then
    NS.Item.statsPath = "unmapped(" .. tostring(raw[1] and "array" or "?") .. ")"
  end

  local info = {
    id = id, link = itemLink or linkOrID, name = name,
    ilvl = ilvl or 0, quality = quality or 1,
    icon = icon, equipLoc = equipLoc, stats = stats,
  }
  info.rawStats = raw
  NS.Item.cache[key] = info
  return info
end

-- 请求客户端去服务器拉这件物品的数据（背包里从没悬停过的物品会走到这）
function NS.Item.request(linkOrID)
  if not linkOrID then return end
  local id = type(linkOrID) == "number" and linkOrID
    or tonumber(tostring(linkOrID):match("item:(%d+)"))
  if id and C_Item.RequestLoadItemDataByID then
    C_Item.RequestLoadItemDataByID(id)
  end
end

-- ── Tooltip ──────────────────────────────────────────────
--[[
    自己挂 tooltip 而不用 ItemButtonTemplate 自带的那套：那套要额外调
    ItemButton_UpdateTooltip 之类的全局函数才生效，各版本名字还不一样。
    直接 GameTooltip:SetHyperlink(link) 最稳，而且能拿到完整物品信息
    （含已镶嵌宝石、套装效果），正是玩家右键游戏里装备能看到的那一份。
]]
function NS.attachTooltip(frame, getLink)
  frame:SetScript("OnEnter", function(self)
    local link = getLink()
    if not link then return end
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    if type(link) == "number" then
      GameTooltip:SetItemByID(link)
    else
      GameTooltip:SetHyperlink(link)
    end
    GameTooltip:Show()
  end)
  frame:SetScript("OnLeave", function()
    GameTooltip:Hide()
  end)
end
