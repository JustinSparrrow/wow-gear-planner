--[[
    WoWGear · 入口

    只做三件事：注册事件、把事件转成"该重刷了"、开斜杠命令。

    ── 为什么要节流 ────────────────────────────────────────
    一次登录会连着触发十几个事件（BAG_UPDATE_DELAYED 一条背包变化就来一次，
    ITEM_DATA_LOAD_RESULT 每件物品来一次）。每刷一次要重扫 16 个槽 + 六个包
    + 算几十次曲线，几十次叠起来在低配机器上能明显卡一下。

    用 C_Timer.After 合并：不管来多少次，150 毫秒内只刷一次。
]]

local ADDON, NS = ...

local UI = NS.UI

local pending = false

function NS.ScheduleRefresh()
  if pending then return end
  pending = true
  C_Timer.After(0.15, function()
    pending = false
    if UI.frame and UI.frame:IsShown() then
      UI:Refresh()
    end
  end)
end

-- ── 事件 ─────────────────────────────────────────────────
local ev = CreateFrame("Frame")
ev:RegisterEvent("PLAYER_LOGIN")
ev:RegisterEvent("PLAYER_ENTERING_WORLD")
ev:RegisterEvent("PLAYER_EQUIPMENT_CHANGED")
ev:RegisterEvent("PLAYER_SPECIALIZATION_CHANGED")
ev:RegisterEvent("ACTIVE_TALENT_GROUP_CHANGED")
ev:RegisterEvent("PLAYER_LEVEL_UP")
ev:RegisterEvent("BAG_UPDATE_DELAYED")
ev:RegisterEvent("ITEM_DATA_LOAD_RESULT")

local function dropCache()
  -- 物品 link 变了（换位置、镶嵌、升级）缓存就可能过期，直接全丢重建。
  -- 几十件的重建成本是毫秒级，维护增量缓存反而容易拿到过期数据。
  NS.Item.clear()
end

ev:SetScript("OnEvent", function(_, event, ...)
  if event == "PLAYER_LOGIN" then
    print("|cffd4b478WoWGear|r 已加载，输入 |cffffd100/wg|r 打开配装模拟。")
    return
  end

  if event == "ITEM_DATA_LOAD_RESULT" then
    -- 这件物品之前读不到，现在读到了。清掉"读不到"的标记让它重试。
    dropCache()
    NS.ScheduleRefresh()
    return
  end

  if event == "BAG_UPDATE_DELAYED" or event == "PLAYER_EQUIPMENT_CHANGED" then
    dropCache()
  end

  -- 换专精/换天赋会改变精通系数和面板，必须重算
  NS.ScheduleRefresh()
end)

-- ── 斜杠命令 ─────────────────────────────────────────────
SLASH_WOWGEAR1 = "/wg"
SLASH_WOWGEAR2 = "/wowgear"
SLASH_WOWGEAR3 = "/配装"
SlashCmdList["WOWGEAR"] = function(msg)
  msg = (msg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
  if msg == "export" or msg == "导出" then
    NS.Export.Open()
    return
  end
  UI:Toggle()
  if UI.frame:IsShown() and UI.Prewarm then
    UI:Prewarm()
  end
end

-- ── 自检 ─────────────────────────────────────────────────
--[[
    读装备属性表这件事失败时**不报错，只是返回空表**。表现是"换装备后
    面板一动不动、所有候选差值都是 0"——看起来像整个插件坏了。

    所以进入世界后主动查一次：如果身上有装备读不到属性，当场打红字。
    有这条，下次同样的故障用户一眼就能说出"是读属性那步挂了"，
    而不是只能描述"没反应"。
]]
local function selfCheck()
  NS.Item.clear()
  local gear = NS.Gear.equipped()
  local total, empty, sample = 0, 0, nil
  for _, it in pairs(gear) do
    total = total + 1
    if not it.stats or next(it.stats) == nil then
      empty = empty + 1
      sample = it
    end
  end
  if empty > 0 then
    print(string.format("|cffff5555WoWGear|r 有 %d/%d 件装备读不到属性表（%s）",
      empty, total, tostring(NS.Item.statsPath)))
    if sample then
      print(string.format("  例如「%s」itemID=%s —— 已退回用装备库的权重换算",
        tostring(sample.name), tostring(sample.id)))
      -- 把原始属性表原样打出来。key 长什么样只有看到才知道怎么兼容，
      -- 与其猜"可能是什么枚举"，不如让用户截一行图。
      local raw = sample.rawStats
      if type(raw) == "table" then
        local parts, cnt = {}, 0
        for k, v in pairs(raw) do
          cnt = cnt + 1
          if cnt <= 8 then
            parts[#parts + 1] = tostring(k) .. "=" .. tostring(v)
          end
        end
        print(string.format("  原始属性表 %d 项：%s", cnt, table.concat(parts, ", ")))
      else
        print("  原始属性表: " .. tostring(raw))
      end
    end
    print("  如果换算出来的数字明显不对，把上面两行发给我。")
  end
end

-- 登录时如果是一键导入的角色，物品数据可能还没拉全，先把界面预热的逻辑
-- 挂到进入世界之后（那时候 GetInventoryItemLink 才一定有值）
local boot = CreateFrame("Frame")
boot:RegisterEvent("PLAYER_ENTERING_WORLD")
boot:SetScript("OnEvent", function(self)
  self:UnregisterEvent("PLAYER_ENTERING_WORLD")
  NS.Item.clear()
  C_Timer.After(3, selfCheck)
end)

SLASH_WOWGEARDEBUG1 = "/wgcheck"
SlashCmdList["WOWGEARDEBUG"] = function()
  NS.Item.clear()
  selfCheck()
end

--[[
    /wgdump —— 把「插件现在到底在用什么」一次性打出来。

    为什么需要这条命令：

    排查这几轮的时候，最有价值的线索全来自"实际值"，而我每次只能靠
    用户在聊天框里读到的那一两行去猜。猜错过好几次：

      - 属性表 key 是什么形态（猜到第三轮才对）
      - 搜索拿到的到底是汉字还是拼音（a'man'ni 那次纯属撞上）
      - 界面上出现了一件库里根本没有的装备（170 装等的旧赛季头）

    最后这条尤其说明问题：代码、数据、装进客户端的文件，三者的版本
    可能不一致，而界面上完全看不出来。所以让插件自己把身份报清楚：

      代码版本（TOC）+ 数据版本（Lib.lua 里的构建指纹）+ 列表实际内容

    三样对不上，就不用猜了。
]]
SLASH_WOWGEARDUMP1 = "/wgdump"
SlashCmdList["WOWGEARDUMP"] = function()
  local ver = (GetAddOnMetadata and GetAddOnMetadata("WoWGear", "Version")) or "?"
  print("|cffd4b478WoWGear|r 代码版本 " .. tostring(ver))

  print("|cffd4b478库|r " .. tostring(NS.LIB_BUILD or
    "|cffff5555未知（Lib.lua 不是新版，或没被加载）|r"))

  local n = 0
  for _ in pairs(NS.LIB or {}) do n = n + 1 end
  print(string.format("  实际解析 %d 件；头部 %d 件；来源表 %d 条",
    n, #NS.Lib.search("head", "", 0), #(NS.LIB_SRC_LIST or {})))

  print(string.format("|cffd4b478状态|r 模式=%s 部位=%s 关键字=[%s] 折算装等=%s",
    tostring(NS.UI.mode), tostring(NS.UI.selected),
    tostring(NS.UI.keyword or ""), tostring(NS.UI.targetIlvl)))

  -- 列表实际前几条：名字 + id + 来源。出现库里没有的东西，这里立刻看得见
  local list = NS.UI:BuildCandidates(NS.SLOT_BY_KEY[NS.UI.selected] or {}, NS.Gear.bags())
  print(string.format("|cffd4b478列表|r 共 %d 条（库内命中 %s）",
    #list, tostring(NS.UI.libHits)))
  for i = 1, math.min(#list, 5) do
    local it = list[i]
    print(string.format("  %d. [%s] %s  %s 装等  %s",
      i, tostring(it.id), tostring(it.name), tostring(it.ilvl or it.baseIlvl),
      tostring(it.source or (it.fromLib and "库" or "客户端"))))
  end

  print("|cff888888把这几行截图发出来，比描述现象准得多。|r")
end
