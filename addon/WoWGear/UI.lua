--[[
    WoWGear · 主窗口

    设计原则：**只用最稳定的原生控件**。

    没用 ItemButtonTemplate / BasicFrameTemplateWithInset 这些模板，原因写在
    上一版 Core.lua 的坑里：模板自带的子控件（标题行、悬停脚本、边框）
    各版本结构不一样，一旦对不上就是重影或者按钮没反应，而这种错在游戏里
    很难看出来（我上次就漏了「重新读取」按钮的 OnClick）。

    自己画 Frame + Texture + FontString 虽然多写几十行，但每个像素在谁手里
    是清楚的，换版本也不会变。

    ── 交互模型：虚拟换装 ───────────────────────────────────
    这个插件的核心不是"看数据"，是"试着换"。所以整个界面围绕一个状态：

        NS.UI.sim = { [槽位key] = 想换上去的装备 }

    点左栏的槽 → 右栏列出这个槽的候选 → 点候选 → 记进 sim，界面全部重算。
    可以连点好几个槽叠加，随时「还原全部」回到真实装备。

    **不真的换装备**。原生 EquipItemByName 能换，但那会改变游戏状态，
    玩家可能没注意自己身上就被换了，还要再换回去。模拟就只做模拟。
]]

local ADDON, NS = ...

local UI = {}
NS.UI = UI

UI.sim = {}          -- [slotKey] = itemInfo，虚拟换装状态
UI.selected = nil    -- 当前选中的槽位
UI.cand = nil        -- 当前选中的候选（用来在底栏显示详情）
UI.snap = nil        -- 当前快照

-- ── 配色 ─────────────────────────────────────────────────
local C = {
  bg      = { 0.045, 0.045, 0.055, 0.96 },
  panel   = { 0.10, 0.10, 0.115, 0.85 },
  panelHi = { 0.16, 0.16, 0.18, 0.95 },
  border  = { 1, 1, 1, 0.13 },
  gold    = { 1, 0.82, 0.25, 1 },
  dim     = { 0.60, 0.60, 0.62, 1 },
  white   = { 1, 1, 1, 1 },
}
local BORDER_TEX = "Interface\\Tooltips\\UI-Tooltip-Border"
local FLAT_TEX   = "Interface\\Buttons\\WHITE8X8"

local function flat(region, c)
  region:SetColorTexture(c[1], c[2], c[3], c[4] or 1)
end

local function skin(f, bgAlpha)
  f:SetBackdrop({
    bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 12,
    insets = { left = 4, right = 4, top = 4, bottom = 4 },
  })
  f:SetBackdropColor(C.bg[1], C.bg[2], C.bg[3], bgAlpha or C.bg[4])
  f:SetBackdropBorderColor(C.border[1], C.border[2], C.border[3], C.border[4])
end
UI.skin = skin

-- 一块深色底，用来分区。比边框轻，不会把界面切碎。
local function panelLite(parent, x, y, w, h)
  local p = CreateFrame("Frame", nil, parent, "BackdropTemplate")
  p:SetPoint("TOPLEFT", x, y)
  p:SetSize(w, h)
  p:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 10,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  p:SetBackdropColor(C.panel[1], C.panel[2], C.panel[3], C.panel[4])
  p:SetBackdropBorderColor(C.border[1], C.border[2], C.border[3], 0.08)
  return p
end
UI.panelLite = panelLite

local function text(parent, size, color, justify)
  local fs = parent:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  local font = GameFontHighlight:GetFont()
  fs:SetFont(font, size, "OUTLINE, THICKOUTLINE")
  fs:SetTextColor(color[1], color[2], color[3], color[4] or 1)
  fs:SetJustifyH(justify or "LEFT")
  return fs
end
UI.text = text

-- ── 主框架 ───────────────────────────────────────────────
local frame = CreateFrame("Frame", "WoWGearMainFrame", UIParent, "BackdropTemplate")
frame:SetSize(800, 560)
frame:SetFrameStrata("DIALOG")
frame:SetToplevel(true)
frame:EnableMouse(true)
frame:SetMovable(true)
frame:RegisterForDrag("LeftButton")
frame:SetClampedToScreen(true)
frame:Hide()
skin(frame)
UI.frame = frame

do
  local pos = WoWGearDB and WoWGearDB.pos
  if pos and pos.point then
    frame:SetPoint(pos.point, UIParent, pos.relPoint or pos.point, pos.x or 0, pos.y or 0)
  else
    frame:SetPoint("CENTER")
  end
end

frame:SetScript("OnDragStart", function(f) f:StartMoving() end)
frame:SetScript("OnDragStop", function(f)
  f:StopMovingOrSizing()
  local point, _, relPoint, x, y = f:GetPoint()
  WoWGearDB = WoWGearDB or {}
  WoWGearDB.pos = { point = point, relPoint = relPoint, x = x, y = y }
end)

-- 标题栏
local titleBar = CreateFrame("Frame", nil, frame)
titleBar:SetPoint("TOPLEFT", 6, -6)
titleBar:SetPoint("TOPRIGHT", -6, -6)
titleBar:SetHeight(26)
titleBar:EnableMouse(true)
titleBar:RegisterForDrag("LeftButton")
titleBar:SetScript("OnDragStart", function() frame:StartMoving() end)
titleBar:SetScript("OnDragStop", function()
  frame:StopMovingOrSizing()
  local point, _, relPoint, x, y = frame:GetPoint()
  WoWGearDB = WoWGearDB or {}
  WoWGearDB.pos = { point = point, relPoint = relPoint, x = x, y = y }
end)

local titleBg = titleBar:CreateTexture(nil, "BACKGROUND")
titleBg:SetAllPoints()
flat(titleBg, { 0.13, 0.11, 0.08, 1 })

local titleGlow = titleBar:CreateTexture(nil, "BORDER")
titleGlow:SetPoint("BOTTOMLEFT", 0, 0)
titleGlow:SetPoint("BOTTOMRIGHT", 0, 0)
titleGlow:SetHeight(1)
flat(titleGlow, { 0.55, 0.42, 0.16, 1 })

local title = text(titleBar, 14, C.gold)
title:SetPoint("LEFT", 8, 0)
title:SetText("WoWGear  配装模拟")

local hint = text(titleBar, 10, C.dim)
hint:SetPoint("RIGHT", titleBar, "RIGHT", -30, 0)
hint:SetText("左键选部位 · 右键候选可移除模拟")

local close = CreateFrame("Button", nil, titleBar, "UIPanelCloseButton")
close:SetPoint("RIGHT", titleBar, "RIGHT", -2, 0)
close:SetSize(24, 24)
close:SetScript("OnClick", function() frame:Hide() end)

-- ── 信息栏 ───────────────────────────────────────────────
local infoBar = CreateFrame("Frame", nil, frame)
infoBar:SetPoint("TOPLEFT", 12, -36)
infoBar:SetPoint("TOPRIGHT", -12, -36)
infoBar:SetHeight(20)

local infoName = text(infoBar, 13, C.white)
infoName:SetPoint("LEFT", 0, 0)

local infoSpec = text(infoBar, 12, C.dim)
infoSpec:SetPoint("LEFT", infoName, "RIGHT", 10, 0)

local infoIlvl = text(infoBar, 12, C.dim)
infoIlvl:SetPoint("RIGHT", 0, 0)

UI.infoName, UI.infoSpec, UI.infoIlvl = infoName, infoSpec, infoIlvl

-- ── 属性条 ───────────────────────────────────────────────
--[[
    四块，每块三行：
        (小字) 暴击
        (大字) 38.57%      ← 有模拟时这一格显示模拟后的值，并且染色
        (小字) 1,033 评级  ▲1.11
]]
local statBar = CreateFrame("Frame", nil, frame)
statBar:SetPoint("TOPLEFT", 12, -60)
statBar:SetSize(776, 72)

UI.statCells = {}
for i, key in ipairs(NS.SECONDARY) do
  local cell = panelLite(statBar, (i - 1) * 194, 0, 188, 72)

  local label = text(cell, 11, C.dim)
  label:SetPoint("TOPLEFT", 10, -7)
  label:SetText(NS.STAT_CN[key])

  local value = text(cell, 20, C.white)
  value:SetPoint("TOPLEFT", 10, -22)
  value:SetText("0.00%")

  local sub = text(cell, 10, C.dim)
  sub:SetPoint("BOTTOMLEFT", 10, 7)
  sub:SetText("")

  local delta = text(cell, 11, C.white)
  delta:SetPoint("BOTTOMRIGHT", -10, 7)
  delta:SetText("")

  UI.statCells[key] = { cell = cell, label = label, value = value, sub = sub, delta = delta }
end

-- ── 右栏标题 + 模式切换 ─────────────────────────────────
local leftHead = text(frame, 12, C.gold)
leftHead:SetPoint("TOPLEFT", 16, -140)
leftHead:SetText("当前装备")
UI.leftHead = leftHead

--[[
    右栏两个模式：我有的 / 装备库。

    为什么非要有第二个：只比背包里的东西，那玩家自己在游戏里换一下
    就知道结果了，插件等于没用。**规划的价值在于回答"我还没有的那件
    值不值得去刷"**，所以必须能翻全部可获得的装备。

    装备库里的属性是按角色当前装等**折算**的，不是实物数值——库里的
    基础装等跟玩家身上的不是一个体系（59/108/197/298 混在一张表里），
    不统一就没法横着比。代价是丢了"这件装等更高"的信息，
    所以列表里仍然显示物品自身的参考装等。
]]
UI.mode = "bag"        -- "bag" = 我有的，"lib" = 装备库
UI.keyword = ""

local tabs = {}
local function makeTab(x, label, mode)
  local b = CreateFrame("Button", nil, frame, "BackdropTemplate")
  b:SetSize(80, 20)
  b:SetPoint("TOPLEFT", x, -139)
  b:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 8,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
  b:SetBackdropBorderColor(1, 1, 1, 0.08)

  local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  fs:SetFont(GameFontHighlight:GetFont(), 11, "OUTLINE")
  fs:SetPoint("CENTER", 0, 0)
  fs:SetTextColor(0.6, 0.6, 0.62, 1)
  fs:SetText(label)
  b.label = fs
  b.mode = mode

  b:SetScript("OnClick", function()
    if UI.mode == mode then return end
    UI.mode = mode
    UI.cand = nil
    UI.libCache = nil
    UI:Refresh()
  end)

  tabs[mode] = b
  return b
end

makeTab(438, "我的装备", "bag")
makeTab(522, "装备库", "lib")

local rightSub = text(frame, 10, C.dim)
rightSub:SetPoint("LEFT", tabs.lib, "RIGHT", 10, 0)
-- 右对齐到窗口右边：这段文字会随"件数 + 折算装等"变长，
-- 只设左边锚点的话一旦变长就顶出窗口
rightSub:SetPoint("RIGHT", frame, "RIGHT", -14, 0)
rightSub:SetJustifyH("RIGHT")
rightSub:SetText("")
UI.rightSub = rightSub
UI.tabs = tabs

local function paintTabs()
  for mode, b in pairs(tabs) do
    if mode == UI.mode then
      b:SetBackdropColor(0.28, 0.23, 0.12, 1)
      b:SetBackdropBorderColor(0.95, 0.75, 0.28, 1)
      b.label:SetTextColor(1, 0.82, 0.25, 1)
    else
      b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
      b:SetBackdropBorderColor(1, 1, 1, 0.08)
      b.label:SetTextColor(0.6, 0.6, 0.62, 1)
    end
  end
end
UI.paintTabs = paintTabs

-- ── 搜索框 ───────────────────────────────────────────────
--[[
    背包里东西多的时候一样要找，所以这个框两个模式都留着，只是
    「我的装备」模式下的搜索发生在几十件里，几乎瞬间出结果。

    输入节流 180ms：每敲一个字母都重排一次列表的话，中文字典里
    一次连续输入能触发十几次全量重算，手快的会明显感觉到顿。
]]
local search = CreateFrame("EditBox", nil, frame, "BackdropTemplate")
search:SetPoint("TOPLEFT", 438, -163)
search:SetSize(250, 18)
search:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
  tile = true, tileSize = 16, edgeSize = 8,
  insets = { left = 3, right = 3, top = 3, bottom = 3 } })
search:SetBackdropColor(0, 0, 0, 0.45)
search:SetBackdropBorderColor(1, 1, 1, 0.10)
search:SetAutoFocus(false)
search:SetMaxLetters(40)
search:SetFontObject(ChatFontNormal)
search:SetTextInsets(6, 6, 0, 0)
search:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)

local searchHint = search:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
searchHint:SetPoint("LEFT", 8, 0)
searchHint:SetTextColor(0.45, 0.45, 0.47, 1)
searchHint:SetText("搜索装备名字…")

local aPending = false
search:SetScript("OnTextChanged", function(self, userInput)
  local t = self:GetText() or ""
  searchHint:SetShown(t == "")
  if not userInput then return end
  UI.keyword = t
  if aPending then return end
  aPending = true
  C_Timer.After(0.18, function()
    aPending = false
    if UI.RefreshListOnly then UI:RefreshListOnly() end
  end)
end)

UI.search = search

-- ── 排序依据 ─────────────────────────────────────────────
--[[
    为什么让玩家手动选排序，而不是给一个"综合评分"：

    同部位的装备总属性**完全相同**（暴雪预算制：每件头盔都是 主属性 5259 /
    绿字 7000 / 耐力 7889 的预算），差别只在配比——这件给暴击精通，
    那件给急速全能。所以"哪件更好"完全取决于你在堆什么属性。

    插件算不出 DPS，就没有资格替你决定什么属性更重要。与其编一个权重
    表让玩家照着选，不如把选择权交回去：点哪条，就按哪条排。
]]
UI.sortBy = (WoWGearDB and WoWGearDB.sortBy) or "crit"

local sortBtns = {}
local function makeSortBtn(x, key)
  local b = CreateFrame("Button", nil, frame, "BackdropTemplate")
  b:SetSize(22, 18)
  b:SetPoint("TOPLEFT", x, -163)
  b:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 8,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
  b:SetBackdropBorderColor(1, 1, 1, 0.08)

  local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  fs:SetFont(GameFontHighlight:GetFont(), 11, "OUTLINE")
  fs:SetPoint("CENTER", 0, 0)
  fs:SetText(NS.STAT_SHORT[key] or key)
  b.label = fs
  b.key = key

  b:SetScript("OnClick", function()
    if UI.sortBy == key then return end
    UI.sortBy = key
    WoWGearDB = WoWGearDB or {}
    WoWGearDB.sortBy = key
    UI:RefreshListOnly()
  end)

  sortBtns[key] = b
  return b
end

for i, key in ipairs(NS.SECONDARY) do
  makeSortBtn(692 + (i - 1) * 24, key)
end

local function paintSorts()
  for key, b in pairs(sortBtns) do
    if key == UI.sortBy then
      b:SetBackdropColor(0.28, 0.23, 0.12, 1)
      b:SetBackdropBorderColor(0.95, 0.75, 0.28, 1)
      b.label:SetTextColor(1, 0.82, 0.25, 1)
    else
      b:SetBackdropColor(0.10, 0.10, 0.115, 0.85)
      b:SetBackdropBorderColor(1, 1, 1, 0.08)
      b.label:SetTextColor(0.6, 0.6, 0.62, 1)
    end
  end
end
UI.paintSorts = paintSorts

-- 只重刷右栏，不重算快照（打字时不需要重读角色状态）
function UI:RefreshListOnly()
  paintSorts()
  UI.libCache = nil
  if UI.RefreshLists then UI:RefreshLists(NS.Gear.bags()) end
  refreshFooter()
end

-- ── 底栏：模拟详情 ───────────────────────────────────────
local footer = panelLite(frame, 12, -466, 776, 84)
UI.footer = footer

local footTitle = text(footer, 12, C.white)
footTitle:SetPoint("TOPLEFT", 10, -8)
footTitle:SetText("选中一个部位开始模拟")

local footDetail = text(footer, 11, C.dim)
footDetail:SetPoint("TOPLEFT", 10, -26)
footDetail:SetPoint("TOPRIGHT", -10, -26)
footDetail:SetJustifyV("TOP")
footDetail:SetHeight(34)
footDetail:SetText("")

UI.footTitle, UI.footDetail = footTitle, footDetail

-- 按钮
local function button(parent, w, label)
  local b = CreateFrame("Button", nil, parent, "BackdropTemplate")
  b:SetSize(w, 22)
  b:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 10,
    insets = { left = 3, right = 3, top = 3, bottom = 3 } })
  b:SetBackdropColor(0.16, 0.14, 0.10, 1)
  b:SetBackdropBorderColor(0.55, 0.42, 0.16, 1)

  local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
  fs:SetFont(GameFontHighlight:GetFont(), 11, "OUTLINE")
  fs:SetPoint("CENTER", 0, 0)
  fs:SetText(label)
  b.label = fs

  b:SetScript("OnEnter", function(self)
    self:SetBackdropColor(0.24, 0.21, 0.14, 1)
    self:SetBackdropBorderColor(0.85, 0.66, 0.25, 1)
  end)
  b:SetScript("OnLeave", function(self)
    self:SetBackdropColor(0.16, 0.14, 0.10, 1)
    self:SetBackdropBorderColor(0.55, 0.42, 0.16, 1)
  end)
  return b
end
UI.button = button

local resetAll = button(footer, 74, "还原全部")
resetAll:SetPoint("BOTTOMRIGHT", -10, 8)
resetAll:SetScript("OnClick", function()
  wipe(UI.sim)
  UI.cand = nil
  UI:Refresh()
end)

local resetOne = button(footer, 74, "还原此槽")
resetOne:SetPoint("RIGHT", resetAll, "LEFT", -6, 0)
resetOne:SetScript("OnClick", function()
  if UI.selected then
    UI.sim[UI.selected] = nil
    UI.cand = nil
    UI:Refresh()
  end
end)

local exportBtn = button(footer, 74, "导出数据")
exportBtn:SetPoint("RIGHT", resetOne, "LEFT", -6, 0)
exportBtn:SetScript("OnClick", function()
  if NS.Export then NS.Export.Open() end
end)

UI.resetAll, UI.resetOne = resetAll, resetOne

--[[
    底栏内容。

    有选中候选时列出一行一行的属性对比，没有时给操作提示。
    这里刻意把「主属性 / 耐力」也列出来——换装时玩家最先看的是主属性，
    只给绿字百分比反而会漏掉"这件敏捷高但暴击低"这类取舍。
]]
local function refreshFooter()
  if not UI.cand or not UI.selected then
    UI.footTitle:SetText(UI.selected and "选一件候选装备" or "选中一个部位开始模拟")
    UI.footDetail:SetText("")
    return
  end

  local sim = UI.sim[UI.selected]
  local cand = UI.cand
  local r = NS.Calc.simulate(UI.snap, UI.selected, cand)

  local name = cand.name or "?"
  local q = NS.QUALITY_COLOR[cand.quality or 1] or C.white
  local slotCN = NS.SLOT_BY_KEY[UI.selected] and NS.SLOT_BY_KEY[UI.selected].cn or UI.selected
  local colorHex = string.format("|cff%02x%02x%02x", q[1] * 255, q[2] * 255, q[3] * 255)
  if cand.fromLib then
    -- 库装备没有"装等变化"可言（属性按统一装等折算），
    -- 换成显示掉落来源——这才是规划时最想知道的信息
    UI.footTitle:SetText(string.format("%s%s|r  →  %s  |cff888888%s|r",
      colorHex, name, slotCN, cand.source or "装备库"))
  else
    UI.footTitle:SetText(string.format("%s%s|r  →  %s  |cff888888(%d → %d 装等)|r",
      colorHex, name, slotCN, r.oldIlvl, r.newIlvl))
  end

  local parts = {}
  -- 主属性
  local pcn = NS.STAT_CN[r.primary] or r.primary
  parts[#parts + 1] = string.format("%s %s", pcn, NS.colorizeDelta(r.dPrimary))
  if r.dSta ~= 0 then
    parts[#parts + 1] = string.format("耐力 %s", NS.colorizeDelta(r.dSta))
  end
  for _, k in ipairs(NS.SECONDARY) do
    local d = r.dStat[k] or 0
    if d ~= 0 then
      parts[#parts + 1] = string.format("%s %s|cff888888(%s%%)|r",
        NS.STAT_CN[k], NS.colorizeDelta(d), NS.signed(r.dPct[k], 2))
    end
  end
  if #parts == 0 then parts[#parts + 1] = "|cff888888属性完全相同|r" end

  UI.footDetail:SetText(table.concat(parts, "   "))
end
UI.refreshFooter = refreshFooter

-- ── 刷新 ─────────────────────────────────────────────────
function UI:Refresh()
  paintTabs()
  paintSorts()
  UI.snap = NS.Calc.snapshot()
  local snap = UI.snap
  local ctx = snap.ctx

  --[[
      库里候选按角色当前平均装等折算。

      刻意用**真实**装等而不是模拟后的装等：拿模拟后的值去折算，
      候选的属性会跟着模拟一起变，换一件涨一次，越换越虚高，
      最后榜单上全是同一件被反复放大的装备。
  ]]
  -- 装等为 0 说明装备还没读出来，拿 334（当前赛季中心档）兜底，
  -- 否则曲线会在 1 装等处外推出一个荒唐的属性值
  UI.targetIlvl = (snap.ilvl and snap.ilvl >= 100) and NS.round(snap.ilvl) or 334

  -- 有任何一个槽在模拟，就按模拟状态重算"换完之后的面板"
  local hasSim = next(UI.sim) ~= nil

  -- 先算出整体模拟后的评级与百分比：把每个槽的 Δ 累加
  local dRating = { crit = 0, haste = 0, mastery = 0, vers = 0 }
  local dPrimary, dSta = 0, 0
  for slotKey, item in pairs(UI.sim) do
    local r = NS.Calc.simulate(snap, slotKey, item)
    for _, k in ipairs(NS.SECONDARY) do
      dRating[k] = dRating[k] + (r.dStat[k] or 0)
    end
    dPrimary = dPrimary + (r.dPrimary or 0)
    dSta = dSta + (r.dSta or 0)
  end

  -- 属性条
  for _, k in ipairs(NS.SECONDARY) do
    local cell = UI.statCells[k]
    local cur  = snap.pct[k] or 0
    local curR = snap.rating[k] or 0
    local newR = math.max(0, curR + dRating[k])
    local newPct = cur + ((NS.Calc.curvePct(k, newR) or 0) - (NS.Calc.curvePct(k, curR) or 0))
    local d = newPct - cur

    cell.value:SetText(NS.fmt(newPct, 2) .. "%")
    if hasSim and math.abs(d) > 0.005 then
      cell.value:SetTextColor(NS.deltaColor(d) == "|cff1eff00" and 0.12 or 1,
        NS.deltaColor(d) == "|cff1eff00" and 1 or 0.33, 0.12, 1)
      cell.delta:SetText(NS.colorizeDelta(d, 2) .. "%")
    else
      cell.value:SetTextColor(1, 1, 1, 1)
      cell.delta:SetText("")
    end

    local ratingText = NS.comma(newR)
    if hasSim and dRating[k] ~= 0 then
      ratingText = ratingText .. " " .. NS.colorizeDelta(dRating[k], 0)
    end
    cell.sub:SetText("|cff888888" .. ratingText .. " 评级|r")
  end

  -- 信息栏
  local q = RAID_CLASS_COLORS and RAID_CLASS_COLORS[ctx.classKey:upper()] or nil
  local classC = q and string.format("|cff%02x%02x%02x", q.r * 255, q.g * 255, q.b * 255) or "|cffffffff"
  infoName:SetText(classC .. ctx.name .. "|r")
  infoSpec:SetText(string.format("|cff888888%s · %s|r", classC, ctx.specName))

  local ilvl = snap.ilvl
  -- 平均装等也要跟着模拟变。算出来的结果存到 UI.simIlvl，
  -- 不然只有那一行文本知道"现在是多少"，别的代码（比如导出）就拿不到。
  local newIlvl = ilvl
  if hasSim then
    local sum, n = 0, 0
    for _, slot in ipairs(NS.SLOTS) do
      local it = UI.sim[slot.key] or snap.gear[slot.key]
      if it and it.ilvl and it.ilvl > 0 then sum = sum + it.ilvl; n = n + 1 end
    end
    if n > 0 then newIlvl = sum / n end
    infoIlvl:SetText(string.format("|cff888888装等|r %.1f  %s", newIlvl,
      NS.colorizeDelta(newIlvl - ilvl, 1)))
  else
    infoIlvl:SetText(string.format("|cff888888装等|r %.1f", ilvl))
  end
  UI.simIlvl = newIlvl

  if not snap.levelOK then
    infoIlvl:SetText("|cffff5555换算表只有 90 级|r")
  end

  -- 主属性变化提示放在右栏标题
  if hasSim then
    rightSub:SetText(string.format("|cff888888%s %s|r",
      NS.STAT_CN[ctx.primary] or "", NS.colorizeDelta(dPrimary)))
  else
    rightSub:SetText("")
  end

  -- 列表
  -- 背包只扫一次，传下去给列表用。RefreshLists 内部自己扫的话会扫两遍，
  -- 包里东西多的时候（100+ 件）每次刷新都白搭一遍容器遍历。
  if UI.RefreshLists then UI:RefreshLists(NS.Gear.bags()) end
  refreshFooter()
end

-- ── 打开 / 关闭 ──────────────────────────────────────────
function UI:Show()
  -- 没有选中部位时右栏就是空的。之前打开界面后直接点「装备库」会看到
  -- 一片空白（右栏没有内容、也没有任何提示），看起来像插件坏了。
  -- 默认选中头部，至少打开就有东西。
  if not UI.selected then UI.selected = NS.SLOTS[1].key end
  frame:Show()
  UI:Refresh()
end

function UI:Toggle()
  if frame:IsShown() then
    frame:Hide()
  else
    UI:Show()
  end
end

-- 选中某个槽位
function UI:Select(slotKey)
  UI.selected = slotKey
  UI.cand = nil
  UI:Refresh()
end

--[[
    ESC 关闭。UISpecialFrames 是客户端维护的一张表，塞进去的窗口名
    会自动响应 ESC——比自己在 OnKeyDown 里处理稳（不会跟别的窗口抢键）。
]]
if UISpecialFrames then
  table.insert(UISpecialFrames, "WoWGearMainFrame")
end

--[[
    小屏幕缩放。

    窗口是 800×560 的固定尺寸。在 1366×768 的笔记本上，UI 缩放通常是 0.7，
    UIParent 的宽度只有不到 900，800 宽的面板会顶到屏幕两边。
    这里按 UIParent 的实际宽度退一档，比写死在 0.85 更适应各种分辨率。
]]
do
  local w = UIParent:GetWidth() or 1200
  if w < 900 then
    frame:SetScale(math.max(0.7, w / 1000))
  end
end
