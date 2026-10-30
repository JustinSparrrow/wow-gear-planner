--[[
    WoWGear · 改造面板

    给一件装备配：自选属性（制造业那种"两条绿字自己选"）、宝石、附魔。

    ── 为什么是独立窗口 ────────────────────────────────────
    主窗口右栏是"这个部位有哪些候选"，一屏能放二十行，很紧凑。
    改造是"针对某一件装备的四个格子"，塞进去会把候选列表挤没。
    所以做成子窗口：主窗口选装备 → 点「改造」→ 在这里配。

    ── 布局 ────────────────────────────────────────────────
    左边是这台装备的"格子"，右边是当前格子的可选项。
    点左边的格子切换，点右边的选项应用，改完立刻重算主界面。
]]

local ADDON, NS = ...

local UI = NS.UI
local Mod = NS.Mod

local C = {
  gold = { 1, 0.82, 0.25, 1 },
  dim = { 0.60, 0.60, 0.62, 1 },
  white = { 1, 1, 1, 1 },
  green = { 0.12, 1, 0.35, 1 },
}
local FLAT_TEX = "Interface\\Buttons\\WHITE8X8"
local BORDER_TEX = "Interface\\Tooltips\\UI-Tooltip-Border"

local W, H = 452, 392
local ROW_H = 22
local OPT_ROW_H = 20

local frame = CreateFrame("Frame", "WoWGearModFrame", UI.frame, "BackdropTemplate")
frame:SetSize(W, H)
frame:SetPoint("CENTER", UI.frame, "CENTER", 0, 16)
frame:SetFrameStrata("DIALOG")
if UI.skin then UI.skin(frame) end
frame:Hide()
UI.modFrame = frame

-- ── 标题 ─────────────────────────────────────────────────
local title = UI.text(frame, 12, C.gold)
title:SetPoint("TOPLEFT", 14, -12)
title:SetPoint("RIGHT", frame, "RIGHT", -34, 0)
title:SetText("改造")

local close = CreateFrame("Button", nil, frame)
close:SetSize(20, 20)
close:SetPoint("TOPRIGHT", -8, -9)
local closeX = close:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
closeX:SetFont(GameFontHighlight:GetFont(), 14, "OUTLINE")
closeX:SetPoint("CENTER")
closeX:SetText("×")
closeX:SetTextColor(0.7, 0.7, 0.72, 1)
close:SetScript("OnClick", function() frame:Hide() end)

local subTitle = UI.text(frame, 10, C.dim)
subTitle:SetPoint("TOPLEFT", 14, -30)
subTitle:SetPoint("RIGHT", frame, "RIGHT", -14, 0)
subTitle:SetText("")

local hint = UI.text(frame, 10, C.dim)
hint:SetPoint("BOTTOMLEFT", 14, 10)
hint:SetPoint("RIGHT", frame, "RIGHT", -14, 0)
hint:SetText("点左边选格子，点右边应用。改完主界面自动重算。")

-- ── 左栏：格子 ───────────────────────────────────────────
local cellPanel = CreateFrame("Frame", nil, frame, "BackdropTemplate")
cellPanel:SetPoint("TOPLEFT", 14, -50)
cellPanel:SetSize(186, H - 84)
cellPanel:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
  tile = true, tileSize = 16, edgeSize = 8,
  insets = { left = 3, right = 3, top = 3, bottom = 3 } })
cellPanel:SetBackdropColor(0.09, 0.09, 0.10, 0.9)
cellPanel:SetBackdropBorderColor(1, 1, 1, 0.07)

local cells = {}          -- {kind, index, label, value}
local cellRows = {}

local function makeCellRow(i)
  local b = CreateFrame("Button", nil, cellPanel, "BackdropTemplate")
  b:SetSize(178, ROW_H - 2)
  b:SetPoint("TOPLEFT", 4, -4 - (i - 1) * ROW_H)
  b:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 7,
    insets = { left = 2, right = 2, top = 2, bottom = 2 } })
  b:SetBackdropColor(0.10, 0.10, 0.115, 0.7)
  b:SetBackdropBorderColor(1, 1, 1, 0.06)

  local name = UI.text(b, 10, C.dim)
  name:SetPoint("LEFT", 7, 0)
  name:SetWidth(56)

  local val = UI.text(b, 11, C.white)
  val:SetPoint("LEFT", 64, 0)
  val:SetPoint("RIGHT", b, "RIGHT", -6, 0)
  val:SetJustifyH("RIGHT")

  b.label, b.value = name, val
  b:SetScript("OnClick", function(self)
    UI.modPick = self.cell
    NS.ModUI.paint()
  end)
  return b
end

for i = 1, 10 do cellRows[i] = makeCellRow(i) end

-- ── 右栏：可选项 ─────────────────────────────────────────
local optPanel = CreateFrame("Frame", nil, frame, "BackdropTemplate")
optPanel:SetPoint("TOPLEFT", 208, -50)
optPanel:SetSize(W - 222, H - 84)
optPanel:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
  tile = true, tileSize = 16, edgeSize = 8,
  insets = { left = 3, right = 3, top = 3, bottom = 3 } })
optPanel:SetBackdropColor(0.09, 0.09, 0.10, 0.9)
optPanel:SetBackdropBorderColor(1, 1, 1, 0.07)

local optHead = UI.text(optPanel, 10, C.gold)
optHead:SetPoint("TOPLEFT", 8, -7)
optHead:SetPoint("RIGHT", optPanel, "RIGHT", -8, 0)
optHead:SetText("")

local scroll = CreateFrame("ScrollFrame", "WoWGearModScroll", optPanel, "UIPanelScrollFrameTemplate")
scroll:SetPoint("TOPLEFT", 6, -24)
scroll:SetPoint("BOTTOMRIGHT", -24, 6)

local content = CreateFrame("Frame", nil, scroll)
content:SetSize(190, 1)
scroll:SetScrollChild(content)

local optRows = {}

local function makeOptRow(i)
  local b = CreateFrame("Button", nil, content, "BackdropTemplate")
  b:SetSize(optPanel:GetWidth() - 40, OPT_ROW_H - 1)
  b:SetPoint("TOPLEFT", 0, -(i - 1) * OPT_ROW_H)
  b:SetBackdrop({ bgFile = FLAT_TEX, edgeFile = BORDER_TEX,
    tile = true, tileSize = 16, edgeSize = 6,
    insets = { left = 2, right = 2, top = 2, bottom = 2 } })
  b:SetBackdropColor(0.10, 0.10, 0.115, 0.55)
  b:SetBackdropBorderColor(1, 1, 1, 0.05)

  local name = UI.text(b, 10, C.white)
  name:SetPoint("LEFT", 6, 0)
  name:SetPoint("RIGHT", b, "RIGHT", -6, 0)
  name:SetJustifyH("LEFT")
  b.name = name

  b:SetScript("OnClick", function(self)
    if self.pick then
      self.pick()
      NS.ModUI.paint()
      NS.ModUI.recalc()
    end
  end)
  return b
end

-- 行池：滚动时复用，不重建按钮
local ROWS = 12
for i = 1, ROWS do optRows[i] = makeOptRow(i) end

-- ── 组装格子列表 ─────────────────────────────────────────
--[[
    哪些装备能改造？

    只有"从库里算出来"的候选。身上和背包里的装备属性是客户端给的**真值**，
    那个值本身已经含了它现在插着的宝石、附着的魔——在这儿再配一遍就重复了。

    这条限制是有代价的：你不能给身上那件补配宝石。但比起把属性算重，
    宁可少一个功能——算重的数字看着更好，玩家照着换会亏。
]]
local function buildCells(item)
  local list = {}
  if not item or not item.id then return list end
  local raw = NS.LIB[item.id]

  if raw and (raw[8] or 0) > 0 then
    list[#list + 1] = { kind = "opt1", label = "自选①" }
    list[#list + 1] = { kind = "opt2", label = "自选②" }
  end

  local n = Mod.socketCount(item)
  for i = 1, n do
    list[#list + 1] = { kind = "gem", index = i, label = "宝石孔" .. i }
  end

  if Mod.canDrill(item) then
    list[#list + 1] = { kind = "drill", label = "打孔器" }
  end

  if #Mod.enchOptions(item.slot) > 0 then
    list[#list + 1] = { kind = "ench", label = "附魔" }
  end

  return list
end

-- 某个格子当前显示成什么
local function cellValue(item, cell)
  local m = Mod.get(item.id) or {}
  if cell.kind == "opt1" then
    return NS.STAT_CN[m.opt1 or "crit"] or "—"
  elseif cell.kind == "opt2" then
    return NS.STAT_CN[m.opt2 or "mastery"] or "—"
  elseif cell.kind == "gem" then
    local g = m.gems and m.gems[cell.index]
    return g and (NS.Gems[g] and NS.Gems[g].name) or "|cff666666空|r"
  elseif cell.kind == "drill" then
    return m.drill and "|cff1eff00已开孔|r" or "|cff666666未开|r"
  elseif cell.kind == "ench" then
    local e = m.ench and NS.Enchs[m.ench]
    return e and e.name or "|cff666666无|r"
  end
  return ""
end

-- ── 右栏：当前格子的选项 ─────────────────────────────────
local function buildOptions(item, cell)
  local out = {}
  if not cell then return out end
  local m = Mod.get(item.id) or {}

  if cell.kind == "opt1" or cell.kind == "opt2" then
    for _, code in ipairs(Mod.OPT_CODES) do
      local cur = (cell.kind == "opt1" and (m.opt1 or "crit"))
                  or (cell.kind == "opt2" and (m.opt2 or "mastery"))
      out[#out + 1] = {
        text = NS.STAT_CN[code] .. (code == cur and "  |cff1eff00（当前）|r" or ""),
        pick = function()
          local mm = Mod.ensure(item.id)
          if cell.kind == "opt1" then mm.opt1 = code else mm.opt2 = code end
        end,
      }
    end
    return out
  end

  if cell.kind == "gem" then
    out[#out + 1] = {
      text = "|cffff8888拔掉这颗|r",
      pick = function()
        local mm = Mod.ensure(item.id)
        mm.gems[cell.index] = nil
      end,
    }
    for _, g in ipairs(NS.GemList) do
      local desc = Mod.statText(g.stats)
      out[#out + 1] = {
        text = string.format("%s  |cff888888%d|r  %s", g.name, g.ilvl, desc),
        pick = function()
          local mm = Mod.ensure(item.id)
          mm.gems[cell.index] = g.id
        end,
      }
    end
    return out
  end

  if cell.kind == "ench" then
    out[#out + 1] = {
      text = "|cffff8888不要附魔|r",
      pick = function()
        local mm = Mod.ensure(item.id)
        mm.ench = nil
      end,
    }
    for _, e in ipairs(Mod.enchOptions(item.slot)) do
      local desc = Mod.statText(e.stats)
      out[#out + 1] = {
        text = string.format("%s  %s", e.name, "|cff888888" .. desc .. "|r"),
        pick = function()
          local mm = Mod.ensure(item.id)
          mm.ench = e.id
        end,
      }
    end
    return out
  end

  if cell.kind == "drill" then
    out[#out + 1] = {
      text = "用打孔器开一个孔（头/腰/腕，每件限一个）",
      pick = function()
        local mm = Mod.ensure(item.id)
        mm.drill = not mm.drill
      end,
    }
    return out
  end

  return out
end

-- ── 绘制 ─────────────────────────────────────────────────
local ModUI = {}
NS.ModUI = ModUI

function ModUI.paint()
  local item = UI.modItem
  if not item then return end

  local raw = NS.LIB[item.id]
  title:SetText(string.format("改造 · |cffffffff%s|r", item.name or "?"))
  subTitle:SetText(string.format("|cff888888%s · 按 %d 装等折算，宝石附魔是固定数值|r",
    NS.SLOT_BY_KEY[item.slot] and NS.SLOT_BY_KEY[item.slot].cn or item.slot,
    UI.targetIlvl or 0))

  -- 左栏
  UI.modCells = buildCells(item)
  if not UI.modPick then UI.modPick = UI.modCells[1] end
  -- 选中的格子可能已经不存在了（比如取消打孔后）
  local found = false
  for _, c in ipairs(UI.modCells) do
    if UI.modPick and c.kind == UI.modPick.kind and c.index == UI.modPick.index then
      found = true
      UI.modPick = c
    end
  end
  if not found then UI.modPick = UI.modCells[1] end

  for i, row in ipairs(cellRows) do
    local cell = UI.modCells[i]
    if cell then
      row.cell = cell
      row.label:SetText(cell.label)
      row.value:SetText(cellValue(item, cell))
      local sel = UI.modPick and cell.kind == UI.modPick.kind
                  and cell.index == UI.modPick.index
      if sel then
        row:SetBackdropColor(0.28, 0.23, 0.12, 1)
        row:SetBackdropBorderColor(0.95, 0.75, 0.28, 1)
      else
        row:SetBackdropColor(0.10, 0.10, 0.115, 0.7)
        row:SetBackdropBorderColor(1, 1, 1, 0.06)
      end
      row:Show()
    else
      row:Hide()
    end
  end

  if #UI.modCells == 0 then
    optHead:SetText("|cff888888这件装备没有可改造的格子|r")
    for i = 1, ROWS do optRows[i]:Hide() end
    content:SetHeight(1)
    return
  end

  -- 右栏
  local opts = buildOptions(item, UI.modPick)
  optHead:SetText(UI.modPick and ("可选项 · " .. UI.modPick.label) or "可选项")
  for i, row in ipairs(optRows) do
    local o = opts[i]
    if o then
      row.name:SetText(o.text)
      row.pick = o.pick
      row:Show()
    else
      row.pick = nil
      row:Hide()
    end
  end
  content:SetHeight(math.max(1, #opts * OPT_ROW_H))
  scroll:SetVerticalScroll(0)
end

-- 改完要重算：清掉候选缓存，否则列表里还是旧属性
function ModUI.recalc()
  UI.libCache = nil
  if UI.RefreshListOnly then UI:RefreshListOnly() end
end

function ModUI.Open(item)
  if not item or not item.id then return end
  if not item.fromLib then
    -- 客户端给的是真值，已经含了它自己的宝石附魔，配了会算重
    if UI.notify then
      UI.notify("这件装备的属性来自客户端（已含宝石附魔），不需要在这里配")
    else
      print("|cffff5555WoWGear|r 这件装备的属性来自客户端（已含宝石附魔），不需要在这里配")
    end
    return
  end
  UI.modItem = item
  UI.modPick = nil
  frame:Show()
  ModUI.paint()
end

frame:SetScript("OnShow", function() ModUI.paint() end)
frame:SetScript("OnHide", function() UI.modItem = nil end)

-- ESC 关闭
if UISpecialFrames then
  table.insert(UISpecialFrames, "WoWGearModFrame")
end

-- ── 主界面入口 ───────────────────────────────────────────
--[[
    入口：底栏「改造」按钮，排在「导出数据」左边。

    作用对象是**当前选中的候选**（右栏点一下就能选中它）。
    没有选中、或者选中的是背包装备时给一句话，而不是静默无反应——
    静默最容易让人以为按钮坏了。
]]
local modBtn = UI.button(UI.footer, 74, "改造")
if UI.exportBtn then
  modBtn:SetPoint("RIGHT", UI.exportBtn, "LEFT", -6, 0)
else
  modBtn:SetPoint("BOTTOMRIGHT", -260, 8)
end
modBtn:SetScript("OnClick", function()
  local item = UI.cand
  if not item then
    print("|cffff5555WoWGear|r 先在右边点一件候选装备，再点改造")
    return
  end
  ModUI.Open(item)
end)
UI.modBtn = modBtn
