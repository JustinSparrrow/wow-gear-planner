--[[
    WoWGear · 数据导出

    这块是上一版唯一的界面，现在降级成主窗口底栏的一个按钮。

    留着它的原因：插件能算"我有这些装备，换哪件更好"，但算不出
    "全服最好的配装是什么"——那需要所有掉落表的完整数据，插件拿不到。
    想找新装备还是得去网页端，而网页端要知道你当前的天赋基线，
    所以导出这条链路还得在。

    格式没变，仍然是 `key=value;key=value` 一行。原因见下一段。
]]

local ADDON, NS = ...

local Export = {}
NS.Export = Export

local function n(v, digits)
  return string.format("%." .. (digits or 0) .. "f", tonumber(v) or 0)
end

local ORDER = { "crit", "haste", "mastery", "vers", "leech", "avoid", "speed" }

--[[
    为什么还是 key=value 而不是 JSON：
    这一段是人眼能读的，粘错一个字符当场能看出来；Lua 拼 JSON 要处理
    转义和逗号，出错的时候是"格式错误"，看不出错在哪。

    R = rating（评级）
    B = bonus（GetCombatRatingBonus，这个评级换算出的百分比，不含天赋）
    T = total（面板显示值，含天赋/被动）
    三样都给，网页端才能反推基线。
]]
local function collect()
  local _, _, classID = UnitClass("player")
  local specID, specName = 0, ""
  local specIndex = GetSpecialization()
  if specIndex then
    specID, specName = GetSpecializationInfo(specIndex)
  end

  local t = {}
  local function add(k, v) t[#t + 1] = k .. "=" .. tostring(v) end

  add("v", 1)
  add("build", select(4, GetBuildInfo()) or "?")
  add("class", classID or 0)
  add("classKey", NS.CLASS_KEY[classID] or "?")
  add("spec", specID or 0)
  add("specKey", NS.SPEC_KEY[specID] or "?")
  add("specName", (specName or ""):gsub("|", ""))
  add("level", UnitLevel("player"))

  add("str", n(UnitStat("player", 1)))
  add("agi", n(UnitStat("player", 2)))
  add("sta", n(UnitStat("player", 3)))
  add("int", n(UnitStat("player", 4)))

  for _, k in ipairs(ORDER) do
    add(k .. "R", n(GetCombatRating(NS.CR[k])))
    add(k .. "B", n(GetCombatRatingBonus(NS.CR[k]), 4))
  end

  add("critT", n(GetCritChance(), 4))
  local sc = 0
  if GetSpellCritChance then
    sc = select(1, GetSpellCritChance(1)) or select(1, GetSpellCritChance()) or 0
  end
  add("spellCritT", n(sc, 4))
  add("hasteT", n(GetHaste(), 4))
  add("mastPts", n(GetMastery(), 4))
  add("mastT", n(GetMasteryEffect(), 4))
  add("ts", date("%Y-%m-%d %H:%M"))

  return table.concat(t, ";")
end

-- 评级一个都没读到说明索引对不上，这份数据是废的，宁可当场标红
local function checkRatings(text)
  local hits = 0
  for _, k in ipairs(ORDER) do
    local v = tonumber(text:match(k .. "R=(%d+)"))
    if v and v > 0 then hits = hits + 1 end
  end
  return hits
end

local frame, eb, stamp

local function build()
  if frame then return end

  frame = CreateFrame("Frame", "WoWGearExportFrame", UIParent, "BackdropTemplate")
  frame:SetSize(620, 300)
  frame:SetPoint("CENTER")
  frame:SetFrameStrata("FULLSCREEN_DIALOG")
  frame:EnableMouse(true)
  frame:SetMovable(true)
  frame:RegisterForDrag("LeftButton")
  frame:SetScript("OnDragStart", function(f) f:StartMoving() end)
  frame:SetScript("OnDragStop", function(f) f:StopMovingOrSizing() end)
  frame:Hide()
  NS.UI.skin(frame)

  local t = NS.UI.text(frame, 13, { 1, 0.82, 0.25, 1 })
  t:SetPoint("TOPLEFT", 14, -12)
  t:SetText("导出到配装规划器")

  local close = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
  close:SetPoint("TOPRIGHT", -4, -4)
  close:SetScript("OnClick", function() frame:Hide() end)

  local hint = NS.UI.text(frame, 10, { 0.6, 0.6, 0.62, 1 })
  hint:SetPoint("TOPLEFT", 14, -34)
  hint:SetPoint("TOPRIGHT", -14, -34)
  hint:SetJustifyH("LEFT")
  hint:SetText("框里已全选，Ctrl+C 复制后粘到网页端的「导入」框。\n"
    .. "|cffffd100建议脱战、清掉食物合剂等临时 buff 再导|r，否则读到的是被 buff 加过的数。")

  -- 不用 InputBoxTemplate：那是单行输入框的画法，多行之后文字会溢到背景外
  eb = CreateFrame("EditBox", nil, frame, "BackdropTemplate")
  eb:SetPoint("TOPLEFT", 14, -64)
  eb:SetPoint("BOTTOMRIGHT", -14, 46)
  eb:SetBackdrop({ bgFile = "Interface\\ChatFrame\\ChatFrameBackground",
    edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    tile = true, tileSize = 16, edgeSize = 12,
    insets = { left = 4, right = 4, top = 4, bottom = 4 } })
  eb:SetBackdropColor(0, 0, 0, 0.55)
  eb:SetBackdropBorderColor(1, 1, 1, 0.18)
  eb:SetMultiLine(true)
  eb:SetAutoFocus(false)
  eb:SetMaxLetters(0)
  eb:SetFontObject(ChatFontNormal)
  eb:SetTextInsets(6, 6, 4, 4)
  eb:SetScript("OnEscapePressed", function() frame:Hide() end)

  stamp = NS.UI.text(frame, 10, { 0.6, 0.6, 0.62, 1 })
  stamp:SetPoint("BOTTOMLEFT", 14, 18)
  stamp:SetPoint("BOTTOMRIGHT", -14, 18)

  local copy = NS.UI.button(frame, 90, "重新读取")
  copy:SetPoint("BOTTOMRIGHT", -14, 12)
  copy:SetScript("OnClick", function() Export.Fill() end)
end

function Export.Fill()
  if not eb then return end
  local text = collect()
  eb:SetText(text)
  -- 先给焦点再全选：反过来的话没有焦点时 HighlightText 经常不生效，
  -- 用户以为选中了，Ctrl+C 出来是空的
  eb:SetFocus()
  eb:HighlightText()

  if checkRatings(text) == 0 then
    stamp:SetText("|cffff5555评级一个都没读到——这版游戏的战斗评级编号可能变了，这份数据不能用|r")
  else
    stamp:SetText("|cff888888已是当前状态，点「重新读取」可以再取一次|r")
  end
end

function Export.Open()
  build()
  frame:Show()
  Export.Fill()
end
