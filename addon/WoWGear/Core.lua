--[[
    WoWGear 数据导出

    为什么要有这个插件：网页端算绿字百分比时，需要知道「这个专精的天赋给了多少」。
    那个数只能从游戏里取——插件把评级、换算百分比、面板总数一起吐出来，
    网页端一减就得到基线，不用手动抄面板，也不会抄错。

    为什么不把计算也搬进插件：插件拿不到全装备数据库（只有客户端缓存过的物品），
    也不能联网。算「换上那件会变成什么样」必须靠完整数据，那部分留在网页端。

    只读。不改任何游戏状态，不占 SavedVariables。
]]

local ADDON, NS = ...

-- 职业 ID -> 我们这边的 key
local CLASS_KEY = {
  [1] = "warrior", [2] = "paladin", [3] = "hunter", [4] = "rogue",
  [5] = "priest", [6] = "death_knight", [7] = "shaman", [8] = "mage",
  [9] = "warlock", [10] = "monk", [11] = "druid", [12] = "demon_hunter",
  [13] = "evoker",
}

-- 专精 ID -> key。由 data/raw/spec_mastery.json 生成，不要手改。
-- 注意 holy / frost / protection / restoration 各有两家同名，
-- 所以必须配着 classID 一起用，不能只看专精名。
local SPEC_KEY = {
  [62]="arcane", [63]="fire", [64]="frost", [65]="holy",
  [66]="protection", [70]="retribution", [71]="arms", [72]="fury",
  [73]="protection", [102]="balance", [103]="feral", [104]="guardian",
  [105]="restoration", [250]="blood", [251]="frost", [252]="unholy",
  [253]="beast_mastery", [254]="marksmanship", [255]="survival", [256]="discipline",
  [257]="holy", [258]="shadow", [259]="assassination", [260]="outlaw",
  [261]="subtlety", [262]="elemental", [263]="enhancement", [264]="restoration",
  [265]="affliction", [266]="demonology", [267]="destruction", [268]="brewmaster",
  [269]="windwalker", [270]="mistweaver", [577]="havoc", [581]="vengeance",
  [1467]="devastation", [1468]="preservation", [1473]="augmentation", [1480]="devourer",
}

-- 战斗评级索引。不用 CR_XXX 全局常量：那是老版本残留，新版可能挪进
-- Enum/Constants 命名空间，写死数字反而稳（来源 FrameXML/PaperDollFrame.lua）。
local CR = {
  crit = 9, haste = 18, mastery = 26, vers = 29,
  leech = 17, avoid = 21, speed = 14,
}

local ORDER = { "crit", "haste", "mastery", "vers", "leech", "avoid", "speed" }

local function n(v, digits)
  return string.format("%." .. (digits or 0) .. "f", tonumber(v) or 0)
end

--[[
    导出格式是一行 key=value;key=value

    为什么不用 JSON：Lua 拼 JSON 要处理转义和逗号，容易出错；
    而且这一段是给人眼看的，粘错一个字符也能发现。key=value 最不容易坏。

    R = rating（评级）
    B = bonus（GetCombatRatingBonus，这个评级换算出来的百分比，不含天赋）
    T = total（面板上显示的那个数，含天赋/被动）
    所以「天赋给了多少」= T - R 对应的换算。两个都给我们才能算。
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
  add("classKey", CLASS_KEY[classID] or "?")
  add("spec", specID or 0)
  add("specKey", SPEC_KEY[specID] or "?")
  add("specName", (specName or ""):gsub("|", ""))
  add("level", UnitLevel("player"))

  -- UnitStat 的顺序是 1 力量 / 2 敏捷 / 3 耐力 / 4 智力。
  -- 返回值已经含装备和 buff，和面板上看到的一致。
  add("str", n(UnitStat("player", 1)))
  add("agi", n(UnitStat("player", 2)))
  add("sta", n(UnitStat("player", 3)))
  add("int", n(UnitStat("player", 4)))

  for _, k in ipairs(ORDER) do
    add(k .. "R", n(GetCombatRating(CR[k])))
    add(k .. "B", n(GetCombatRatingBonus(CR[k]), 4))
  end

  -- 面板总数。暴击分物理/法术两条，法系和物理职业看的不是同一个，
  -- 所以两条都导出来，让网页端挑——猜错就会差十几个点。
  add("critT", n(GetCritChance(), 4))
  add("spellCritT", n(GetSpellCritChance(1), 4))
  add("hasteT", n(GetHaste(), 4))
  -- 精通这里是「点数」，不是百分比。面板上那个百分比 = 点数 × 专精系数，
  -- 系数我们已经有（spec_mastery），所以点数更有用。
  add("mastPts", n(GetMastery(), 4))
  add("mastT", n(GetMasteryEffect(), 4))

  add("ts", date("%Y-%m-%d %H:%M"))

  return table.concat(t, ";")
end

-- ── 界面 ────────────────────────────────────────────────
local frame = CreateFrame("Frame", "WoWGearFrame", UIParent, "BasicFrameTemplateWithInset")
frame:SetSize(580, 340)
frame:SetPoint("CENTER")
frame:SetFrameStrata("DIALOG")
frame:Hide()

frame.title = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
frame.title:SetPoint("TOPLEFT", 14, -6)
frame.title:SetText("WoWGear · 数据导出")

local hint = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
hint:SetPoint("TOPLEFT", 18, -30)
hint:SetPoint("TOPRIGHT", -18, -30)
hint:SetJustifyH("LEFT")
hint:SetText("全选后 Ctrl+C 复制，粘到配装规划器的「导入」框里。\n"
  .. "|cffffd100建议脱战、清掉食物合剂等临时 buff 再导|r，否则读到的是被 buff 加过的数。")

local eb = CreateFrame("EditBox", nil, frame, "InputBoxTemplate")
eb:SetMultiLine(true)
eb:SetSize(540, 210)
eb:SetPoint("TOPLEFT", 22, -70)
eb:SetAutoFocus(false)
eb:SetFontObject(ChatFontNormal)
eb:SetScript("OnEscapePressed", function() frame:Hide() end)

local refresh = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
refresh:SetSize(100, 22)
refresh:SetPoint("BOTTOMLEFT", 22, 18)
refresh:SetText("刷新")
refresh:SetScript("OnClick", function()
  eb:SetText(collect())
  eb:HighlightText()
  eb:SetFocus()
end)

local close = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
close:SetSize(100, 22)
close:SetPoint("LEFT", refresh, "RIGHT", 8, 0)
close:SetText("关闭")
close:SetScript("OnClick", function() frame:Hide() end)

local stamp = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
stamp:SetPoint("LEFT", close, "RIGHT", 12, 0)

local function fill()
  eb:SetText(collect())
  eb:HighlightText()
  stamp:SetText("已生成，点「刷新」可重新读一次")
end

SLASH_WOWGEAR1 = "/wg"
SLASH_WOWGEAR2 = "/wowgear"
SlashCmdList["WOWGEAR"] = function()
  if frame:IsShown() then
    frame:Hide()
  else
    frame:Show()
    fill()
    eb:SetFocus()
  end
end

-- 第一次登录提示一下怎么用，只提示一次
local boot = CreateFrame("Frame")
boot:RegisterEvent("PLAYER_LOGIN")
boot:SetScript("OnEvent", function()
  print("|cffd4b478WoWGear|r 已加载，输入 |cffffd100/wg|r 打开数据导出。")
end)
