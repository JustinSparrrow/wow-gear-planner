--[[
    WoWGear 数据导出

    为什么要有这个插件：网页端算绿字百分比时，需要知道「这个专精的天赋给了多少」。
    那个数只能从游戏里取——插件把评级、换算百分比、面板总数一起吐出来，
    网页端一减就得到基线，不用手动抄面板，也不会抄错。

    为什么不把计算也搬进插件：插件拿不到全装备数据库（只有客户端缓存过的物品），
    也不能联网。算「换上那件会变成什么样」必须靠完整数据，那部分留在网页端。

    只读。不改任何游戏状态，不写 SavedVariables。
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

--[[
    战斗评级索引。

    **优先读游戏给的全局常量**（`CR_MASTERY` 这种），读不到才用写死的数字。
    理由：这套索引在 6.0 大改过一次（武器技能、韧性那一批被删，后面整体前移），
    以后还可能再挪。写死数字迟早过期，但全局常量也可能被暴雪清掉，所以两条腿走。

    写死的值来自 FrameXML/PaperDollFrame.lua，是 **1-based** 的 Lua 层编号；
    DBC / wowdev 上那张表是 **0-based**（那边的 CR_MASTERY 是 25），两者差 1，
    查资料时别拿错表。
]]
local function cr(name, fallback)
  return _G["CR_" .. name] or fallback
end

local CR = {
  crit    = cr("CR_CRIT_MELEE", 9),
  haste   = cr("CR_HASTE_MELEE", 18),
  mastery = cr("CR_MASTERY", 26),
  vers    = cr("CR_VERSATILITY_DAMAGE_DONE", 29),
  leech   = cr("CR_LIFESTEAL", 17),
  avoid   = cr("CR_AVOIDANCE", 21),
  speed   = cr("CR_SPEED", 14),
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
    所以「天赋给了多少」= T 减去按曲线算出来的装备部分。三样都给才够用。
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
  -- GetSpellCritChance 在某些版本要传学派、某些版本无参，两种都试一下，
  -- 取不到就写 0（网页端看到 0 会退回 critT，不会算出离谱的值）
  local sc = 0
  if GetSpellCritChance then
    sc = select(1, GetSpellCritChance(1)) or select(1, GetSpellCritChance()) or 0
  end
  add("spellCritT", n(sc, 4))
  add("hasteT", n(GetHaste(), 4))
  -- 精通这里是「点数」，不是百分比。面板上那个百分比 = 点数 × 专精系数，
  -- 系数我们已经有（spec_mastery），所以点数更有用。mastT 给的是最终百分比，
  -- 用来交叉验证。
  add("mastPts", n(GetMastery(), 4))
  add("mastT", n(GetMasteryEffect(), 4))

  add("ts", date("%Y-%m-%d %H:%M"))

  return table.concat(t, ";")
end

--[[
    自检：评级全读成 0 说明索引对不上（游戏改过编号），这时候导出的数据是废的，
    但它长得跟正常数据一样，粘进去只会让网页端算出"这个角色没有任何绿字"。
    所以宁可当场标红，也别让人拿着错数据去核对。
]]
local function check_ratings(text)
  local hits = 0
  for _, k in ipairs(ORDER) do
    local v = tonumber(text:match(k .. "R=(%d+)"))
    if v and v > 0 then hits = hits + 1 end
  end
  return hits
end

-- ── 界面 ────────────────────────────────────────────────
local frame = CreateFrame("Frame", "WoWGearFrame", UIParent, "BasicFrameTemplateWithInset")
frame:SetSize(600, 340)
frame:SetPoint("CENTER")
frame:SetFrameStrata("DIALOG")
frame:Hide()

-- BasicFrameTemplateWithInset 自带标题行（.TitleText），别自己再建一个，
-- 两个会叠在同一个位置。
if frame.TitleText then
  frame.TitleText:SetText("WoWGear · 数据导出")
end

local hint = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
hint:SetPoint("TOPLEFT", 16, -30)
hint:SetPoint("TOPRIGHT", -16, -30)
hint:SetJustifyH("LEFT")
hint:SetText("框里已经全选，直接 Ctrl+C 复制，粘到配装规划器的「导入」框。\n"
  .. "|cffffd100建议脱战、清掉食物合剂等临时 buff 再导|r，否则读到的是被 buff 加过的数。")

--[[
    不能用 InputBoxTemplate：那个模板的背景是给单行输入框画的，
    设成多行之后文字会溢出到背景外面，长文本也没有任何边框可依。
    这里用 BackdropTemplate 自己画一个 9-slice 框，跟游戏原生输入框一个风格。

    多行只是**视觉换行**：文本本身还是那一行，GetText() 和复制出来的内容里没有换行。
]]
local eb = CreateFrame("EditBox", nil, frame, "BackdropTemplate")
eb:SetBackdrop({
  bgFile = "Interface\\ChatFrame\\ChatFrameBackground",
  edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
  tile = true, tileSize = 16, edgeSize = 12,
  insets = { left = 4, right = 4, top = 4, bottom = 4 },
})
eb:SetBackdropColor(0, 0, 0, 0.55)
eb:SetBackdropBorderColor(1, 1, 1, 0.18)
eb:SetMultiLine(true)
eb:SetAutoFocus(false)
eb:SetMaxLetters(0)          -- 0 = 不限长度
eb:SetFontObject(ChatFontNormal)
eb:SetSize(556, 200)
eb:SetPoint("TOPLEFT", 22, -72)
eb:SetTextInsets(6, 6, 4, 4)
eb:SetScript("OnEscapePressed", function() frame:Hide() end)

local stamp = frame:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")
stamp:SetPoint("TOPLEFT", eb, "BOTTOMLEFT", 6, -6)
stamp:SetPoint("TOPRIGHT", eb, "BOTTOMRIGHT", -6, -6)
stamp:SetJustifyH("LEFT")

local fill              -- 前向声明：按钮先建好，读数据的逻辑在后面才定义

local refresh = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
refresh:SetSize(100, 22)
refresh:SetPoint("BOTTOMLEFT", 22, 16)
refresh:SetText("重新读取")
refresh:SetScript("OnClick", function() fill() end)

local close = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
close:SetSize(100, 22)
close:SetPoint("LEFT", refresh, "RIGHT", 8, 0)
close:SetText("关闭")
close:SetScript("OnClick", function() frame:Hide() end)

fill = function()
  local text = collect()
  eb:SetText(text)
  -- 先给焦点再全选。反过来的话 HighlightText 在没有焦点时经常不生效，
  -- 用户以为已经全选，Ctrl+C 复制到的是空的或者只有光标处。
  eb:SetFocus()
  eb:HighlightText()

  local hits = check_ratings(text)
  if hits == 0 then
    stamp:SetText("|cffff5555评级一个都没读到——这版游戏的战斗评级编号可能变了，"
      .. "这份数据不能用|r")
  else
    stamp:SetText("|cff888888已是当前状态，点「重新读取」可以再取一次|r")
  end
end

SLASH_WOWGEAR1 = "/wg"
SLASH_WOWGEAR2 = "/wowgear"
SlashCmdList["WOWGEAR"] = function()
  if frame:IsShown() then
    frame:Hide()
  else
    frame:Show()
    fill()
  end
end

-- 第一次登录提示一下怎么用
local boot = CreateFrame("Frame")
boot:RegisterEvent("PLAYER_LOGIN")
boot:SetScript("OnEvent", function()
  print("|cffd4b478WoWGear|r 已加载，输入 |cffffd100/wg|r 打开数据导出。")
end)
