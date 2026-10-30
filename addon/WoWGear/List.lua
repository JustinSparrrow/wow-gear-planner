--[[
    WoWGear · 两侧列表

    左边是身上 16 个部位，右边是选中部位的所有候选（身上那件 + 背包里的）。

    ── 为什么左边也要能点 ──────────────────────────────────
    装备规划最常问的问题是"哪个部位最该换"。所以左边每一格都直接显示
    这一件的属性和装等，扫一眼就能找到短板，点进去看候选。

    ── 为什么右边把"身上那件"也放进去 ──────────────────────
    玩家模拟了半天想退回去，如果列表里没有它，就只能去点「还原此槽」，
    多一步而且容易忘。放进去并标「当前」，点它 = 还原，语义自然。
]]

local ADDON, NS = ...

local UI = NS.UI

local ROW_H = 38
local C_WHITE = { 1, 1, 1, 1 }
local C_DIM   = { 0.6, 0.6, 0.62, 1 }

local function flat(region, c)
  region:SetColorTexture(c[1], c[2], c[3], c[4] or 1)
end

-- ── 左栏：部位网格 ───────────────────────────────────────
local slotButtons = {}

local SLOT_W = 200
local SLOT_H = 34
local COL_X = { 12, 216 }
local ROW_Y0 = -158

--[[
    一个部位格子：图标 + 名字 + 装等。

    装等画在图标右下角，跟游戏里背包的样子一致——玩家已经习惯用这个位置
    判断装等，放到名字右边反而要多扫一眼。

    没有真装备时才显示部位占位图（PaperDoll 的那套），
    不然空槽看起来跟"数据没读到"没区别。
]]
local function makeSlotButton(index, slot)
  local b = CreateFrame("Button", nil, UI.frame, "BackdropTemplate")
  b:SetSize(SLOT_W, SLOT_H)
  local col = math.floor((index - 1) % 2)
  local row = math.floor((index - 1) / 2)
  b:SetPoint("TOPLEFT", COL_X[col + 1], ROW_Y0 - row * 36)
  b:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    tile = true, tileSize = 16, edgeSize = 10,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
  b:SetBackdropBorderColor(1, 1, 1, 0.08)

  -- 品质色底，用一小块放在图标后面当边框
  local qb = b:CreateTexture(nil, "BACKGROUND")
  qb:SetPoint("TOPLEFT", 4, -3)
  qb:SetSize(28, 28)
  b.qb = qb

  local icon = b:CreateTexture(nil, "ARTWORK")
  icon:SetPoint("TOPLEFT", 5, -4)
  icon:SetSize(26, 26)
  icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
  b.icon = icon

  local ilvl = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  ilvl:SetFont(GameFontHighlight:GetFont(), 10, "OUTLINE, THICKOUTLINE")
  ilvl:SetPoint("BOTTOMRIGHT", icon, "BOTTOMRIGHT", 2, -1)
  ilvl:SetJustifyH("RIGHT")
  b.ilvl = ilvl

  local name = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  name:SetFont(GameFontHighlight:GetFont(), 11, "OUTLINE")
  name:SetPoint("LEFT", icon, "RIGHT", 6, 4)
  name:SetPoint("RIGHT", b, "RIGHT", -4, 0)
  name:SetJustifyH("LEFT")
  name:SetWordWrap(false)
  b.name = name

  -- 模拟标记：这一格现在是虚拟的
  local simMark = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  simMark:SetFont(GameFontHighlight:GetFont(), 9, "OUTLINE")
  simMark:SetPoint("LEFT", icon, "RIGHT", 6, -6)
  simMark:SetText("")
  b.simMark = simMark

  b:SetScript("OnEnter", function(self)
    self:SetBackdropColor(0.18, 0.18, 0.20, 0.95)
    self:SetBackdropBorderColor(0.85, 0.66, 0.25, 0.7)
    local link = self.link
    if link then
      GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
      GameTooltip:SetHyperlink(link)
      GameTooltip:Show()
    end
  end)
  b:SetScript("OnLeave", function(self)
    self:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
    self:SetBackdropBorderColor(1, 1, 1, 0.08)
    GameTooltip:Hide()
  end)
  b:SetScript("OnClick", function()
    UI:Select(slot.key)
  end)

  b.slot = slot
  return b
end

for i, slot in ipairs(NS.SLOTS) do
  slotButtons[slot.key] = makeSlotButton(i, slot)
end

-- ── 右栏：候选列表（可滚动）────────────────────────────
local candPanel = CreateFrame("Frame", nil, UI.frame)
candPanel:SetPoint("TOPLEFT", 438, -156)
candPanel:SetSize(346, 308)

local scroll = CreateFrame("ScrollFrame", "WoWGearCandScroll", candPanel, "UIPanelScrollFrameTemplate")
scroll:SetPoint("TOPLEFT", 0, 0)
scroll:SetSize(324, 304)

local content = CreateFrame("Frame", nil, scroll)
content:SetSize(318, 1)
scroll:SetScrollChild(content)

local rows = {}
local function getRow(i)
  local r = rows[i]
  if r then return r end

  r = CreateFrame("Button", nil, content, "BackdropTemplate")
  r:SetSize(318, ROW_H - 2)
  r:SetPoint("TOPLEFT", 0, -(i - 1) * ROW_H)
  r:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8", edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    tile = true, tileSize = 16, edgeSize = 8,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  r:SetBackdropColor(0.10, 0.10, 0.115, 0.7)
  r:SetBackdropBorderColor(1, 1, 1, 0.06)

  local qb = r:CreateTexture(nil, "BACKGROUND")
  qb:SetPoint("TOPLEFT", 4, -4)
  qb:SetSize(26, 26)
  r.qb = qb

  local icon = r:CreateTexture(nil, "ARTWORK")
  icon:SetPoint("TOPLEFT", 5, -5)
  icon:SetSize(24, 24)
  icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
  r.icon = icon

  local name = r:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  name:SetFont(GameFontHighlight:GetFont(), 11, "OUTLINE")
  name:SetPoint("TOPLEFT", icon, "TOPRIGHT", 6, -1)
  name:SetPoint("RIGHT", r, "RIGHT", -70, 0)
  name:SetJustifyH("LEFT")
  name:SetWordWrap(false)
  r.name = name

  local sub = r:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  sub:SetFont(GameFontHighlight:GetFont(), 10, "OUTLINE")
  sub:SetPoint("BOTTOMLEFT", icon, "BOTTOMRIGHT", 6, 1)
  sub:SetPoint("RIGHT", r, "RIGHT", -70, 0)
  sub:SetJustifyH("LEFT")
  sub:SetTextColor(0.6, 0.6, 0.62, 1)
  r.sub = sub

  local delta = r:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  delta:SetFont(GameFontHighlight:GetFont(), 12, "OUTLINE")
  delta:SetPoint("RIGHT", -6, 0)
  delta:SetJustifyH("RIGHT")
  r.delta = delta

  r:SetScript("OnEnter", function(self)
    self:SetBackdropColor(0.19, 0.19, 0.21, 0.95)
    self:SetBackdropBorderColor(0.85, 0.66, 0.25, 0.6)
    if self.link then
      GameTooltip:SetOwner(self, "ANCHOR_LEFT")
      GameTooltip:SetHyperlink(self.link)
      GameTooltip:Show()
    end
  end)
  r:SetScript("OnLeave", function(self)
    self:SetBackdropColor(0.10, 0.10, 0.115, 0.7)
    self:SetBackdropBorderColor(1, 1, 1, 0.06)
    GameTooltip:Hide()
  end)

  -- 左键：模拟换上这件。右键：取消这个部位的模拟。
  r:SetScript("OnMouseUp", function(self, button)
    if not self.item then return end
    if button == "Right" then
      if UI.selected then
        UI.sim[UI.selected] = nil
        UI.cand = nil
        UI:Refresh()
      end
      return
    end
    if self.isCurrent then
      -- 点「当前」= 还原，不需要单独记状态
      UI.sim[UI.selected] = nil
      UI.cand = nil
    else
      UI.cand = self.item
      UI.sim[UI.selected] = self.item
    end
    UI:Refresh()
  end)

  rows[i] = r
  return r
end

--[[
    给一行填数据。

    Δ 那一列显示的是**绿字总量变化**（四项点数直接相加），不是 DPS。
    猜一个"DPS 提升"数字给玩家看是最危险的做法——他们会照着它选，
    而插件没有模拟器，那个数字只会是错的。所以宁可只给可验证的量。
]]
local function fillRow(r, item, snap, slotKey, sim)
  r.item = item
  r.link = item.link

  local q = NS.QUALITY_COLOR[item.quality or 1] or C_WHITE
  flat(r.qb, q)

  local icon = item.icon or 134400
  r.icon:SetTexture(icon)

  r.name:SetText(item.name or "|cffff5555读取中…|r")
  r.name:SetTextColor(q[1], q[2], q[3], 1)

  local subParts = {}
  subParts[#subParts + 1] = string.format("%d 装等", item.ilvl or 0)

  if item.otherEquipped then
    subParts[#subParts + 1] = string.format("|cffff5555已戴在%s|r", item.otherEquipped)
  end
  if item.socketHint then
    subParts[#subParts + 1] = item.socketHint
  end
  r.sub:SetText(table.concat(subParts, "  "))

  if item.current and not UI.sim[slotKey] then
    -- 就是身上那件，没有装等变化可言
    r.delta:SetText("|cff888888当前|r")
    r.delta:SetTextColor(1, 1, 1, 1)
    r.isCurrent = true
    return
  end

  r.isCurrent = false
  sim = sim or NS.Calc.simulate(snap, slotKey, item)
  local score = NS.Calc.score(sim)
  local dIlvl = sim.dIlvl

  local txt = string.format("%s%d", NS.deltaColor(score), score)
  if dIlvl ~= 0 then
    txt = txt .. string.format("   |cff888888%s%d|r", dIlvl > 0 and "+" or "", dIlvl)
  end
  r.delta:SetText(txt)
  r.delta:SetTextColor(NS.deltaColor(score) == "|cff1eff00" and 0.12 or 1,
    NS.deltaColor(score) == "|cff1eff00" and 1 or 0.33, 0.12, 1)
end

-- ── 刷新两侧 ─────────────────────────────────────────────
function UI:RefreshLists(bagItems)
  local snap = UI.snap
  if not snap then return end

  -- 左栏
  for _, slot in ipairs(NS.SLOTS) do
    local b = slotButtons[slot.key]
    local real = snap.gear[slot.key]
    local virt = UI.sim[slot.key]
    local shown = virt or real

    if shown then
      local q = NS.QUALITY_COLOR[shown.quality or 1] or C_WHITE
      b.icon:SetTexture(shown.icon or 134400)
      flat(b.qb, q)
      b.name:SetText(shown.name or "?")
      b.name:SetTextColor(q[1], q[2], q[3], 1)
      b.ilvl:SetText(tostring(shown.ilvl or 0))
      b.link = shown.link
      if virt then
        b.simMark:SetText("|cffd4b478模拟|r")
      else
        b.simMark:SetText("")
      end
    else
      -- 空槽：用游戏自己的部位占位图，一眼能看懂是"这里没东西"
      b.icon:SetTexture(string.format(NS.SLOT_TEX, slot.tex))
      flat(b.qb, { 0.25, 0.25, 0.27, 1 })
      b.name:SetText("|cff666666" .. slot.cn .. "|r")
      b.ilvl:SetText("")
      b.link = nil
      b.simMark:SetText("")
    end

    -- 选中态
    if UI.selected == slot.key then
      b:SetBackdropColor(0.22, 0.19, 0.12, 1)
      b:SetBackdropBorderColor(0.95, 0.75, 0.28, 1)
    else
      b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
      b:SetBackdropBorderColor(1, 1, 1, 0.08)
    end
  end

  -- 右栏
  local slotDef = UI.selected and NS.SLOT_BY_KEY[UI.selected]
  local n = 0
  if slotDef then
    local list = NS.Gear.candidates(UI.selected, snap.gear, bagItems or {})

    -- 排序：当前装备钉在最上面，其余按绿字总量降序
    -- 每个候选的模拟结果算一次存下来：排序要用，画那一行也要用，
    -- 算两遍纯属浪费，而且两边算出来的东西万一不一致会更难查。
    local scored = {}
    for _, item in ipairs(list) do
      local sim = NS.Calc.simulate(snap, UI.selected, item)
      scored[#scored + 1] = { item = item, sim = sim, score = NS.Calc.score(sim) }
    end
    table.sort(scored, function(a, b)
      if a.item.current ~= b.item.current then return a.item.current end
      if a.score ~= b.score then return a.score > b.score end
      return (a.item.ilvl or 0) > (b.item.ilvl or 0)
    end)

    UI.rightHead:SetText(string.format("候选装备 · %s", slotDef.cn))

    n = #scored
    for i = 1, n do
      local r = getRow(i)
      r:Show()
      fillRow(r, scored[i].item, snap, UI.selected, scored[i].sim)
    end
  else
    UI.rightHead:SetText("候选装备")
  end

  for i = n + 1, #rows do rows[i]:Hide() end
  content:SetHeight(math.max(1, n * ROW_H))
end

--[[
    第一次刷新时先请求一遍背包物品的数据。

    刚登录时客户端可能还没缓存背包里的物品（尤其是一键导入的配装），
    这时 GetItemInfo 返回 nil，列表会是空的。点一下"刷新"才出来
    体验很差，所以打开界面时主动去拉一次，拉到之后靠
    ITEM_DATA_LOAD_RESULT 事件自动重刷。
]]
function UI:Prewarm()
  local bags = NS.Gear.bags()
  if #bags == 0 then return end
  -- bags() 本身已经调过 Item.get，读不到的会返回 nil 被过滤掉了，
  -- 所以这里再扫一遍原始 link 主动请求
  for bag = 0, 5 do
    local slots = C_Container.GetContainerNumSlots(bag)
    if slots and slots > 0 then
      for slot = 1, slots do
        local link = C_Container.GetContainerItemLink(bag, slot)
        if link and not NS.Item.get(link) then
          NS.Item.request(link)
        end
      end
    end
  end
end
