--[[
    WoWGear · 装备与背包采集

    ── 为什么装备也用 link ──────────────────────────────────
    已装备的物品用 `GetInventoryItemLink("player", slotID)` 拿 link，
    不用 `GetInventoryItemID`。原因见 Util.lua 开头：ID 拿不到真实装等。

    ── 为什么要全量重扫 ────────────────────────────────────
    换一件装备会同时改变：这件物品的 link 消失了（变成别的）、背包里多出
    换下来的那件、两件物品的"是否已装备"状态。做增量更新要追踪的边界情况
    太多（背包整理、宝石镶嵌、邮件收取都会打乱 slot 序号），
    全量重扫几十件物品的代价是毫秒级，不值得为省这点去做增量。
]]

local ADDON, NS = ...

NS.Gear = { dirty = true }

--[[
    槽位族名：finger1 / finger2 都属于 finger。

    用来查 SLOT_ACCEPT。武器槽 key 本身没有数字，gsub 之后还是自己，
    所以不需要特判。
]]
local function familyOf(slotKey)
  return (slotKey:gsub("%d+$", ""))
end

function NS.Gear.accepts(slotKey, info)
  if not info or not info.equipLoc then return false end
  local set = NS.SLOT_ACCEPT[familyOf(slotKey)]
  if not set or not set[info.equipLoc] then return false end
  return true
end

-- 采集身上 16 个槽
function NS.Gear.equipped()
  local out = {}
  for _, slot in ipairs(NS.SLOTS) do
    local link = GetInventoryItemLink("player", slot.id)
    if link then
      local info = NS.Item.get(link)
      if not info then
        -- 客户端还没有这件的数据（罕见：刚登录还没拉全）
        NS.Item.request(link)
      end
      out[slot.key] = info
    end
  end
  return out
end

--[[
    扫背包，返回可装备的物品列表。

    背包号 0 是主背包，1~4 是四格包，5 是材料包。
    材料包里理论上不会有装备（现代版本有"材料包只能放材料"的限制），
    但把它一起扫一遍的代价可以忽略，而且某些版本/插件的容器编号会错位，
    漏扫比多扫危险得多。

    每一项带 bag/slot 定位，因为同一件装备可能在包里出现两次
    （两个相同戒指、不同升级等级），只按 itemID 去重会把它们合并成一个。
]]
function NS.Gear.bags()
  local out = {}
  local seen = {}
  for bag = 0, 5 do
    local slots = C_Container.GetContainerNumSlots(bag)
    if slots and slots > 0 then
      for slot = 1, slots do
        local link = C_Container.GetContainerItemLink(bag, slot)
        if link then
          local info = NS.Item.get(link)
          if info and info.equipLoc and info.equipLoc ~= "" then
            -- 同一次扫描里 bag/slot 唯一，直接当 key
            local key = bag .. ":" .. slot
            if not seen[key] then
              seen[key] = true
              local row = {}
              for k, v in pairs(info) do row[k] = v end
              row.bag, row.slot = bag, slot
              out[#out + 1] = row
            end
          end
        end
      end
    end
  end
  return out
end

--[[
    这个槽的候选列表。

    已装备的那件也放进候选并标 equipped=true —— 否则界面上一打开就是空的，
    玩家会以为插件坏了；而且"我想看换回原来那件会怎样"是个真实需求
    （模拟了半天的装备，想退回去）。

    唯一装备的冲突（同一个戒指已经戴在另一只手上）在这里标出来，
    不在 UI 里判断：UI 只负责画，判断逻辑集中在数据层好测。
]]
function NS.Gear.candidates(slotKey, equipped, bagItems)
  local family = familyOf(slotKey)
  local out = {}

  local cur = equipped[slotKey]
  if cur then
    local row = {}
    for k, v in pairs(cur) do row[k] = v end
    row.current = true
    out[#out + 1] = row
  end

  for _, it in ipairs(bagItems) do
    if NS.Gear.accepts(slotKey, it) then
      local row = {}
      for k, v in pairs(it) do row[k] = v end
      row.fromBag = true
      -- 已经戴在另一个同族槽上（另一只戒指/另一个饰品）
      if family ~= slotKey then
        for _, s in ipairs(NS.SLOTS) do
          if s.key ~= slotKey and familyOf(s.key) == family then
            local other = equipped[s.key]
            if other and other.id == it.id then
              row.otherEquipped = s.cn
            end
          end
        end
      end
      out[#out + 1] = row
    end
  end
  return out
end

-- 平均装等（游戏面板上那个数）
function NS.Gear.averageIlvl(equipped)
  local sum, n = 0, 0
  for _, slot in ipairs(NS.SLOTS) do
    local it = equipped[slot.key]
    -- 副手空着时（双手武器）不该拉低平均装等，游戏自己也不把它算进去
    if it and it.ilvl and it.ilvl > 0 then
      sum = sum + it.ilvl
      n = n + 1
    end
  end
  if n == 0 then return 0 end
  return sum / n
end
