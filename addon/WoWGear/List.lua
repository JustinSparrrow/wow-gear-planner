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
candPanel:SetPoint("TOPLEFT", 438, -186)
candPanel:SetSize(346, 276)

local scroll = CreateFrame("ScrollFrame", "WoWGearCandScroll", candPanel, "UIPanelScrollFrameTemplate")
scroll:SetPoint("TOPLEFT", 0, 0)
scroll:SetSize(324, 272)

--[[
    空列表提示。

    搜索没结果、或者背包里这个部位一件可换的都没有时，右栏就是一片纯黑，
    玩家分不清是"真的没有"还是"插件坏了"。给一句话说清楚。
]]
-- 提示做成按钮：搜到的东西在别的部位时，点一下就跳过去，
-- 省得玩家自己去左栏一个个试（搜索是按部位来的，他未必知道那件属于哪）
local emptyBtn = CreateFrame("Button", nil, candPanel)
emptyBtn:SetPoint("CENTER", candPanel, "CENTER", 0, 0)
emptyBtn:SetSize(310, 76)
emptyBtn:Hide()

local emptyHint = emptyBtn:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
emptyHint:SetFont(GameFontHighlight:GetFont(), 11, "")
emptyHint:SetPoint("CENTER", emptyBtn, "CENTER", 0, 0)
-- 限宽 + 居中：提示最长三行，不限宽会横着顶到面板外面去
emptyHint:SetWidth(300)
emptyHint:SetJustifyH("CENTER")
emptyHint:SetTextColor(0.52, 0.52, 0.55, 1)
emptyHint:SetText("")

emptyBtn:SetScript("OnClick", function(self)
  local slot = self.jumpSlot
  if not slot then return end
  self.jumpSlot = nil
  UI:Select(slot)
end)

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
    if self.item and self.item.fromLib then
      -- 库物品没有 link，客户端也未必认识这件物品，自己拼 tooltip
      NS.Lib.tooltip(self.item, self)
    elseif self.link then
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
  if item.fromLib then
    --[[
        装备库这一行显示**属性配比**，不是装等。

        同部位的装备总属性完全相同（护甲恒定 主属性 5259 / 绿字 7000 /
        耐力 7889），所以装等和总量在这里都是零信息量。真正决定
        "值不值得换"的是它给的是哪两条属性、各多少——这正是这一行要说的。

        掉落来源放在 tooltip 和底栏：一行里塞"暴4755 精2245"再加个
        "Temple of Sethraliss · Galvazzt"必然要折行，反而看不清。
    ]]
    local mix = {}
    for _, k in ipairs(NS.SECONDARY) do
      local v = item.stats and item.stats[k]
      if v and v > 0 then
        mix[#mix + 1] = string.format("%s%d", NS.STAT_SHORT[k] or k, v)
      end
    end
    if #mix > 0 then subParts[#subParts + 1] = table.concat(mix, " ") end
    if item.hasOpt then
      -- 配比是插件猜的（默认暴击+精通），标出来别让人当成实物数据
      subParts[#subParts + 1] = "|cffd4b478自选|r"
    end
    -- 配过宝石/附魔的标一下，不然改完关掉面板就看不出差别在哪
    local modTxt = NS.Mod and NS.Mod.summary(item)
    if modTxt and modTxt ~= "" then
      subParts[#subParts + 1] = "|cff1eff00" .. modTxt .. "|r"
    end
    if not item.curSeason then
      subParts[#subParts + 1] = "|cff666666旧|r"
    end
  else
    subParts[#subParts + 1] = string.format("%d 装等", item.ilvl or 0)
    if item.otherEquipped then
      subParts[#subParts + 1] = string.format("|cffff5555已戴在%s|r", item.otherEquipped)
    end
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
  local score = NS.Calc.score(sim, UI.sortBy)
  -- 库里装备的装等变化没有意义（属性是按统一装等折算的），只给绿字总量
  local dIlvl = item.fromLib and 0 or sim.dIlvl

  local txt = string.format("%s%d", NS.deltaColor(score), score)
  if dIlvl ~= 0 then
    txt = txt .. string.format("   |cff888888%s%d|r", dIlvl > 0 and "+" or "", dIlvl)
  end
  r.delta:SetText(txt)
  r.delta:SetTextColor(NS.deltaColor(score) == "|cff1eff00" and 0.12 or 1,
    NS.deltaColor(score) == "|cff1eff00" and 1 or 0.33, 0.12, 1)
end

-- ── 候选来源 ─────────────────────────────────────────────
--[[
    "我的装备"模式：背包 + 身上那件，属性是客户端给的真值。

    "装备库"模式：从 Lib.lua 按部位取出全部可获得的装备，属性用曲线按统一
    装等折算。这个列表要缓存——里面每件都要跑一遍曲线插值，而玩家在列表上
    点来点去时这些数值并不会变，重算是纯浪费。
]]
function UI:BuildCandidates(slotDef, bagItems)
  if UI.mode ~= "lib" then
    local list = NS.Gear.candidates(UI.selected, UI.snap.gear, bagItems or {})
    -- 背包模式也吃搜索框：包里有几十件时一样要翻。
    -- 匹配规则跟装备库一致（名字 / 来源 / 物品 ID，多词全部命中），
    -- 免得同一个框在两个模式下行为不一样
    local words = NS.Lib.words(UI.keyword)
    if #words > 0 then
      local kept = {}
      for _, it in ipairs(list) do
        -- 库里有这件装备就借它的拼音索引，背包模式也能用拼音搜
        local lraw = it.id and NS.LIB[it.id]
        local name = (it.name or ""):lower()
        local src = (it.id and NS.LIB_SRC[it.id] or ""):lower()
        local py = lraw and lraw[9] or ""
        local ini = lraw and lraw[10] or ""
        local spy = (it.id and NS.LIB_SRC_PY_BY_ID[it.id]) or ""
        local idStr = tostring(it.id or "")
        local ok = true
        for _, w in ipairs(words) do
          if not (name:find(w, 1, true) or src:find(w, 1, true)
                  or py:find(w, 1, true) or ini:find(w, 1, true)
                  or spy:find(w, 1, true) or idStr:find(w, 1, true)) then
            ok = false
            break
          end
        end
        if ok then kept[#kept + 1] = it end
      end
      list = kept
      UI.bagHits = #kept
    else
      UI.bagHits = #list
    end
    UI.libHits = nil
    return list
  end

  local snap = UI.snap
  local key = table.concat({ UI.selected or "", UI.keyword or "",
                             UI.targetIlvl or 0, snap.ctx.primary or "" }, "|")
  if UI.libCache and UI.libCache.key == key then
    UI.libHits = UI.libCache.hits
    return UI.libCache.list
  end

  local list = {}

  -- 身上那件也放进来打头。装备库模式下没有它的话，玩家看到一堆候选
  -- 却不知道自己在跟什么比——Δ 是相对身上那件算的，基准得看得见。
  local cur = snap.gear[UI.selected]
  if cur then
    local row = {}
    for k, v in pairs(cur) do row[k] = v end
    row.current = true

    --[[
        基准也要过一遍同样的折算。

        候选是"库里权重 × 目标装等"算出来的，身上那件却是客户端给的实物数值。
        两边不在同一把尺子上：身上 331、候选按 334 折算，Δ 就会系统性偏向候选
        ——每件都显得比实际好，这正是模拟器最容易骗人的方向。

        所以基准也用同一套折算，比的就只剩属性配比。
        读不到库数据（旧赛季、库里没有的）才退回实物值。
    ]]
    local norm = cur.id and NS.Lib.statsFor(cur.id, UI.targetIlvl, snap.ctx.primary)
    if norm then
      row.realStats = row.stats
      row.stats = norm
      row.normed = true
    end

    list[1] = row
  end

  local ids = NS.Lib.search(UI.selected, UI.keyword, snap.ctx.specID)
  for _, id in ipairs(ids) do
    -- 带上玩家配的自选属性，否则列表里显示的和模拟算的会是两套数
    local it = NS.Lib.build(id, UI.targetIlvl, snap.ctx.primary,
                            NS.Mod.optSplit(id))
    if it then list[#list + 1] = it end
  end

  -- 记下"库里命中几件"（不含身上那件）。身上那件永远在列表第一条，
  -- 所以光看 #list 永远看不出"搜索把库筛空了"。
  UI.libHits = #ids
  UI.libCache = { key = key, list = list, hits = #ids }
  return list
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
    local list = UI:BuildCandidates(slotDef, bagItems)

    -- 排序：当前装备钉在最上面，其余按选中那条属性的差值降序
    -- 每个候选的模拟结果算一次存下来：排序要用，画那一行也要用，
    -- 算两遍纯属浪费，而且两边算出来的东西万一不一致会更难查。
    local scored = {}
    for _, item in ipairs(list) do
      local sim = NS.Calc.simulate(snap, UI.selected, item)
      scored[#scored + 1] = { item = item, sim = sim, score = NS.Calc.score(sim, UI.sortBy) }
    end
    table.sort(scored, function(a, b)
      -- 身上那件永远第一
      if a.item.current ~= b.item.current then return a.item.current end
      -- 本赛季的排在旧赛季前面：旧赛季装备刷不到了，属性再好也没用
      if (a.item.fromLib and a.item.curSeason) ~= (b.item.fromLib and b.item.curSeason) then
        return (a.item.fromLib and a.item.curSeason) and true or false
      end
      if a.score ~= b.score then return a.score > b.score end
      return (a.item.ilvl or 0) > (b.item.ilvl or 0)
    end)

    -- 把"折算装等"讲清楚：不然玩家会以为库里 298 装等那件和身上 334 是一回事
    if UI.mode == "lib" then
      -- 用 libHits 而不是 #scored：列表第一条永远是身上那件，
      -- 拿列表长度当件数的话，搜不到任何东西也会显示"1 件"，
      -- 看着像"有一条结果"，实际全是空的
      UI.rightSub:SetText(string.format("|cff888888%s %d 件 · 按 %d 装等折算|r",
        slotDef.cn, UI.libHits or 0, UI.targetIlvl or 0))
    else
      UI.rightSub:SetText(string.format("|cff888888%s %d 件|r", slotDef.cn, #scored))
    end

    n = #scored
    for i = 1, n do
      local r = getRow(i)
      r:Show()
      fillRow(r, scored[i].item, snap, UI.selected, scored[i].sim)
    end
  else
    UI.rightSub:SetText("")
  end

  for i = n + 1, #rows do rows[i]:Hide() end
  content:SetHeight(math.max(1, n * ROW_H))

  --[[
      空列表时说清楚是哪一种"空"。

      装备库模式下身上那件永远占着第一条，所以 `n` 恒大于 0，
      光判断 `n == 0` 会把"搜索把库筛空了"显示成"有一条结果"——
      玩家看到的是一行孤零零的当前装备，跟坏掉没区别。
      所以按 `UI.libHits`（库里真正命中几件）来判断。
  ]]
  local hits = n
  if UI.mode == "lib" then hits = UI.libHits or 0 end

  if hits == 0 then
    emptyBtn.jumpSlot = nil
    -- 显示清洗过之后的词：用户打进来的可能是「阿曼尼×」，
    -- 原样回显只会让人更困惑"我明明打对了"
    local shown = table.concat(NS.Lib.words(UI.keyword), " ")
    if shown == "" then shown = UI.keyword or "" end

    if not slotDef then
      emptyHint:SetText("← 先在左边点一个部位")
    elseif UI.mode == "lib" and not NS.LIB then
      emptyHint:SetText("装备库没加载（Lib.lua 不在插件目录里）")
    elseif UI.mode == "lib" and shown ~= "" then
      -- 搜索是按部位来的，别的部位有货就把话说清楚，并且给个能点的东西
      local other = NS.Lib.searchOther(UI.selected, UI.keyword, UI.snap.ctx.specID)
      if other then
        local parts = {}
        for i = 1, math.min(#other, 3) do
          local def = NS.SLOT_BY_KEY[other[i][1]]
          parts[#parts + 1] = string.format("%s %d", def and def.cn or other[i][1], other[i][2])
        end
        local first = NS.SLOT_BY_KEY[other[1][1]]
        emptyBtn.jumpSlot = other[1][1]
        emptyHint:SetText(string.format(
          "「%s」不在%s\n其他部位：%s\n点击跳到%s",
          shown, slotDef.cn, table.concat(parts, "、"),
          first and first.cn or other[1][1]))
      else
        emptyHint:SetText(string.format(
          "没有「%s」\n可以搜装备名、副本名或物品 ID\nESC 清空", shown))
      end
    elseif UI.mode == "lib" then
      emptyHint:SetText(string.format("库里没有本专精能用的%s", slotDef.cn))
    elseif shown ~= "" then
      emptyHint:SetText(string.format("背包里没有「%s」\nESC 清空搜索", shown))
    else
      emptyHint:SetText(string.format("背包里没有可换的%s", slotDef.cn))
    end
    emptyBtn:Show()
  else
    emptyBtn:Hide()
  end
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
