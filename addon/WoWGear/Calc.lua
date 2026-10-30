--[[
    WoWGear · 属性计算

    ── 整个模拟只算「差值」，不算绝对值和 ────────────────
    这是本文件最重要的一个决定，值得解释清楚。

    网页端要算绝对值，因为它只有装备表，得从零加起：职业基础 + 每件装备 +
    天赋基线。三层里任何一层估错，最终百分比就是错的，所以才需要
    spec_stats 那样一张一张去游戏里标定基线。

    插件里完全不需要这么做，因为**游戏自己知道当前百分比**：
    GetCritChance() 直接返回 38.57%，含装备、含天赋、含所有被动。
    我们只需要回答"换上这件之后变成多少"，那就只要算差：

        新百分比 = 面板百分比 + (曲线(新评级) - 曲线(旧评级))

    面板值本身多准，差值就有多准。基线（天赋给了多少）在这条式子里
    是个加在末尾的常数，一减就没了——所以插件根本不需要基线表，
    换天赋、换版本、换角色都不用重新标定。

    唯一还必须"抄"的是曲线斜率（每多少评级给 1%），因为 API 只告诉
    你当前评级对应多少百分比，不给你函数本身。

    ── 精通系数同样不问表 ────────────────────────────────
    GetMastery()      返回精通**点数**
    GetMasteryEffect()返回精通**效果百分比**
    两者一除就是系数，而且是游戏亲自算的。

    这样一来 spec_mastery 表里那些存疑值（织雾 31.185 明显差 100 倍、
    守护 0.7 还是 1.0 说不清）全部自动正确，不用再猜。
]]

local ADDON, NS = ...

NS.Calc = {}

local huge = math.huge

--[[
    按递减档位把评级折成「有效评级」。

    每档只对**越过该门槛的那部分**打折。Maxroll 的验算：
    1320 急速（30%）再加 264，应为 36%，实际 35.4% ——
    (1320 + 264×0.9) / 44 = 35.4 ✓

    区间算错（比如整段都打折）会让高评级的角色算出来的提升偏小，
    而且偏得很隐蔽：1379 急速时一切正常，1381 就开始慢慢偏。
]]
local function drConvert(rating, dr)
  if not rating or rating <= 0 or not dr then return 0 end
  local total, n = 0, #dr
  for i = 1, n do
    local lo, penalty = dr[i][1], dr[i][2]
    if rating <= lo then break end
    local hi = (i < n) and dr[i + 1][1] or huge
    local seg = math.min(rating, hi) - lo
    if seg > 0 then
      total = total + seg * (1 - penalty / 100)
    end
    if rating <= hi then break end
  end
  return total
end

NS.Calc.drConvert = drConvert

-- ── 换算分母 ─────────────────────────────────────────────
--[[
    优先用游戏实测的分母，表里的值只当兜底。

    怎么实测：GetCombatRating(CR) / GetCombatRatingBonus(CR)。
    在第一档递减门槛之前，两者严格线性，比值就是真分母。

    为什么值得这么麻烦：网页端那个"全能分母到底是 54 还是 64"的疑问
    悬了很久（兽王 394 评级→7.30% 支持 54，另一组 348 评级的数据支持 64）。
    在插件里这问题不存在——游戏当场就告诉你了。

    加了 0.5~1.5 倍的容差：万一某个版本 GetCombatRatingBonus 返回的是
    半值（全能伤害/治疗分两条的情况），比值会翻倍，那时候宁可用表，
    也不要拿一个错两倍的分母去算全盘。
]]
function NS.Calc.rate(stat)
  local crv = NS.CURVE[stat]
  if not crv then return 1 end
  local cr = NS.CR[stat]
  if not cr then return crv.rate end

  local R = GetCombatRating(cr)
  local B = GetCombatRatingBonus(cr)
  local gate = crv.dr[2] and crv.dr[2][1] or huge

  if R and B and R > 50 and B > 0.01 and R < gate then
    local measured = R / B
    if measured > crv.rate * 0.5 and measured < crv.rate * 1.5 then
      return measured
    end
  end
  return crv.rate
end

-- 精通系数。实测优先，测不出来才查表。
function NS.Calc.masteryCoeff()
  local pts  = GetMastery and GetMastery() or 0
  local effect = GetMasteryEffect and GetMasteryEffect() or 0
  -- 点数太小的时候比值会被浮点误差放大（0.02/0.01 之类），不采信
  if pts and effect and pts > 1 and effect > 0 then
    local c = effect / pts
    if c > 0.05 and c < 10 then return c end
  end
  local ctx = NS.Calc.context()
  return NS.MASTERY_COEFF[ctx.key] or 1
end

--[[
    评级 -> 百分比，**只含评级本身贡献的那部分**，不含天赋被动。

    精通的返回值是「效果百分比」而不是点数：点数 × 系数 = 效果百分比，
    跟 GetMasteryEffect() 同一个口径，这样才能直接和面板值相减。
]]
function NS.Calc.curvePct(stat, rating)
  local crv = NS.CURVE[stat]
  if not crv then return nil end
  local eff = drConvert(rating, crv.dr) / NS.Calc.rate(stat)
  -- 注意分母用的是 rate(stat)：精通也用 46，算出来是"点子"，
  -- 那 46 是不是也该用实测？精通同理，rate 表里是 46，实测能覆盖。
  if stat == "mastery" then
    return eff * NS.Calc.masteryCoeff()
  end
  return eff
end

-- ── 角色上下文 ───────────────────────────────────────────
--[[
    主属性靠**看面板哪个最高**来判断，不查职业表。

    理由：混合职业的主属性随专精变（奶骑吃智力、防骑吃力量），
    以后暴雪再改一次就要维护一张表。而 UnitStat 里那个非零又最大的
    必然是当前专精吃的那条——耐力单独排除，因为它俩数量级差不多时
    反而容易误判（低装等小号）。
]]
local function detectPrimary()
  local s = {
    str = UnitStat("player", 1) or 0,
    agi = UnitStat("player", 2) or 0,
    int = UnitStat("player", 4) or 0,
  }
  local best, bestVal = nil, -1
  for _, code in ipairs(NS.PRIMARY) do
    if s[code] > bestVal then best, bestVal = code, s[code] end
  end
  return best or "agi", s
end

function NS.Calc.context()
  local _, _, classID = UnitClass("player")
  local specIndex = GetSpecialization()
  local specID, specName = 0, ""
  if specIndex then
    specID, specName = GetSpecializationInfo(specIndex)
  end
  local classKey = NS.CLASS_KEY[classID] or "?"
  local specKey  = NS.SPEC_KEY[specID] or "?"
  local primary, primaries = detectPrimary()

  return {
    name = UnitName("player") or "?",
    classID = classID, classKey = classKey,
    specID = specID or 0, specKey = specKey, specName = specName or "",
    key = classKey .. ":" .. specKey,
    primary = primary,
    primaries = primaries,
    level = UnitLevel("player") or 0,
  }
end

-- 全能面板值 = 评级贡献 + 被动贡献，两段分开加
local function versatilityPct()
  local cr = NS.CR.vers
  local fromRating = GetCombatRatingBonus(cr) or 0
  local fromPassive = 0
  if GetVersatilityBonus then
    local ok, v = pcall(GetVersatilityBonus, cr)
    if ok and type(v) == "number" then fromPassive = v end
  end
  return fromRating + fromPassive
end

-- ── 快照 ─────────────────────────────────────────────────
--[[
    采一次当前状态。界面每次刷新都调这个，成本很低（几十次 API + 16 次
    GetItemStats），没有必要做增量。
]]
function NS.Calc.snapshot()
  local ctx = NS.Calc.context()
  local gear = NS.Gear.equipped()

  local rating, pct = {}, {}
  for _, s in ipairs(NS.SECONDARY) do
    rating[s] = GetCombatRating(NS.CR[s]) or 0
  end

  pct.crit    = GetCritChance() or 0
  pct.haste   = GetHaste() or 0
  pct.mastery = GetMasteryEffect() or 0
  pct.vers    = versatilityPct()

  return {
    ctx = ctx,
    gear = gear,
    rating = rating,
    pct = pct,
    stats = ctx.primaries,
    ilvl = NS.Gear.averageIlvl(gear),
    -- 曲线表是 90 级的。低等级角色算出来的 Δ 会偏，界面上要标出来。
    levelOK = ctx.level >= 90,
  }
end

-- 一件装备贡献的属性，多属性合一的槽摊到主属性上
local function itemStats(snap, item)
  local out = {}
  if not item then return out end
  local st = item.stats

  --[[
      客户端没给属性表时的兜底：用装备库的权重 + 装等曲线自己算。

      库里有 3570 件（含全部当前赛季装备），身上和背包的绝大多数都能查到。
      算出来的值不含战火/泰坦那类额外加成，但比"整套模拟全 0"强得多——
      而且这种情况下界面上会标出来（见 UI 的属性条星号逻辑）。

      触发条件是 stats 为空：说明 GetItemStats 这版客户端的签名不认识。
  ]]
  if (not st or next(st) == nil) and item.id and NS.LIB and NS.LIB[item.id] then
    st = NS.Lib.statsFor(item.id, item.ilvl, snap.ctx.primary) or {}
    item.libFallback = true
  end
  if not st then return out end
  for code, v in pairs(st) do
    if code ~= "_multi" then out[code] = v end
  end
  if st._multi then
    local p = snap.ctx.primary
    for _, m in ipairs(st._multi) do
      for _, c in ipairs(m.codes) do
        if c == p then
          out[p] = (out[p] or 0) + m.value
          break
        end
      end
    end
  end
  return out
end

NS.Calc.itemStats = itemStats

-- ── 模拟 ─────────────────────────────────────────────────
--[[
    把 slotKey 上的装备换成 cand，返回变化。

    cand 传 nil 表示"空出来"（把装备拿掉看损失多少）——用来回答
    "这件饰品不戴会掉多少"。

    返回：
      dStat   属性点数变化 { crit=+54, agi=+12, ... }
      dPct    百分比变化 { crit=+1.11, ... }
      pct     换完之后的面板百分比
      rating  换完之后的评级
      dPrimary 主属性变化
      dIlvl   装等变化
]]
function NS.Calc.simulate(snap, slotKey, cand)
  local old = snap.gear[slotKey]
  local a = itemStats(snap, old)
  local b = itemStats(snap, cand)

  --[[
    双手武器会顶掉副手。

    不处理这件事的话，主手从单手剑换成双手斧时，模拟结果里副手的属性
    还稳稳算着——于是"换上双手武器"看起来像是白赚，而实际上副手的东西
    全没了，甚至会因为副手被换下而掉一截。这是模拟器最容易骗人的地方：
    错的方向是"偏乐观"，玩家照做就亏。
  ]]
  local offDrop = nil
  if slotKey == "main_hand" and cand and cand.equipLoc == "INVTYPE_2HWEAPON" then
    offDrop = snap.gear.off_hand
    if offDrop then
      local offStats = itemStats(snap, offDrop)
      for code, v in pairs(offStats) do
        a[code] = (a[code] or 0) + v
      end
    end
  end

  -- 把所有出现过的属性代号求并集，漏掉某个代号会让那一条悄悄不显示
  local codes, dStat = {}, {}
  for code in pairs(a) do codes[code] = true end
  for code in pairs(b) do codes[code] = true end
  for code in pairs(codes) do
    dStat[code] = (b[code] or 0) - (a[code] or 0)
  end

  local out = {
    dStat = dStat,
    dRating = {},
    dPct = {},
    rating = {},
    pct = {},
    dIlvl = (cand and cand.ilvl or 0) - (old and old.ilvl or 0),
    oldIlvl = old and old.ilvl or 0,
    newIlvl = cand and cand.ilvl or 0,
    offDropped = offDrop,
  }

  for _, s in ipairs(NS.SECONDARY) do
    local d  = dStat[s] or 0
    local r0 = snap.rating[s] or 0
    local r1 = math.max(0, r0 + d)
    local p0 = NS.Calc.curvePct(s, r0) or 0
    local p1 = NS.Calc.curvePct(s, r1) or 0
    out.dRating[s] = r1 - r0
    out.rating[s]  = r1
    out.dPct[s]    = p1 - p0
    out.pct[s]     = (snap.pct[s] or 0) + (p1 - p0)
  end

  local primary = snap.ctx.primary
  out.dPrimary = dStat[primary] or 0
  out.primary  = primary
  out.newPrimary = (snap.stats[primary] or 0) + out.dPrimary
  out.dSta = dStat.sta or 0

  return out
end

--[[
    排序用的分数。

    ── 为什么不能按"绿字总量"排 ────────────────────────────
    查过全库的分配权重：同部位的每件装备，总属性**完全一样**
    （护甲恒定 主属性 5259 / 绿字 7000 / 耐力 7889），差别只在配比。
    这是暴雪的预算制设计——一件头和另一件头的总预算相同，
    只是一个给暴击精通、一个给急速全能。

    所以按总量排序永远得到 0，等于没排。真正要回答的是
    "这件给的是不是我需要的那条属性"，那就必须按单条属性排。

    key 传具体属性代号（crit/haste/mastery/vers）时，返回那条属性的**差值**。
    差值而不是绝对值，是因为玩家关心的是"换上之后多多少"。
]]
function NS.Calc.score(sim, key)
  if key and key ~= "total" then
    return sim.dStat[key] or 0
  end
  local s = 0
  for _, k in ipairs(NS.SECONDARY) do
    s = s + (sim.dStat[k] or 0)
  end
  return s
end
