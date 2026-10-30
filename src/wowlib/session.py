"""配装会话：持有角色当前装备，提供预览 / 替换 / 恢复。

命令行（main.py）和网页（serve.py）共用这一份。

为什么必须共用：换装预览最怕"预览用一套算法、结算用另一套"。两边一旦漂移，
界面显示 +186 而实际只有 +120，这种错不会报异常，只会让人做出错误决定。
所以预览走的就是最终计算函数，只是把装备临时换上、算完再放回去。

槽位名有两套：simc 用 finger1/finger2/trinket1/trinket2，库里统一成 finger/trinket。
对外一律用 simc 的键（有 finger1 才能区分两枚戒指），需要查库时再映射。
"""

import json
import os
import re
import sqlite3
import zlib

from dataclasses import replace

from wowlib.simc import parse, EquippedItem, class_primary, HYBRID_PRIMARY
from wowlib.calibrate import load_budget, compute, SLOT_COLUMN, STAT_ID
from wowgear import stats as stat_calc
from wowlib.catalog import Catalog
from wowgear.source import source_of
from wowgear.store import (load_choices, save_choices, load_panel, save_panel,
                           load_panels, panel_key,
                           RATING_PER_PCT, PANEL_STATS)
from wowgear.const import (CLASSES, ARMOR_CN, SLOT_LABELS, SIMC_SLOTS, SPEC_CN,
                           CLASS_IDS, PROF_CN, ARMOR_SLOTS,
                           STAT_CN, STAT_ORDER as SHOW_ORDER,
                           KIND_CN, QUALITY_CN, TRACK_CN, EXTRA_ILEVELS)
from wowlib.stat_picker import slots_of

from wowgear.paths import CHOICE_PATH, DB_PATH, PANEL_PATH, ROOT  # noqa: E402


# 来源类别 -> (中文标签, 前端配色档)。dungeon 在本赛季就是 M+ 池，
# dungeon_normal 被排除在候选之外，所以正常不会出现。


# 升级轨道之外的装等。341 = 神话满级再加 7（额外升级），
# 344 = 神话团本最后两个 boss 专属掉落。两个都不在 ilevel_tracks 里，
# 但属性预算表（RandPropPoints）有对应行，所以能直接算。
# 定义在 wowgear/const.py（前端快照、装等下拉都用同一份），这里不另抄一份——
# 抄两份的下场就是改了一处、另一处永远停在旧值。

# 平均装等按 16 个格子算（游戏的角色面板就是 16 格，衬衫和战袍不算）
ROSTER = ("head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist",
          "legs", "feet", "finger1", "finger2", "trinket1", "trinket2",
          "main_hand", "off_hand")

# inventory_type：15 弓/枪、17 双手武器、26 远程右手。这三个都占满两只手，
# 平均装等里要算两次；13 单手、21 主手、22 副手、14 盾只占一格。
TWO_HAND_TYPES = (15, 17, 26)


def connect():
    if not os.path.exists(DB_PATH):
        raise SystemExit("找不到数据库，先运行 python main.py --build")
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn



# 面板评级 -> 百分比。这些转换率是从真实面板反推的（暴击 1065=38.15%、
# 急速 121=4.81%、精通 1423=79.50%、全能 394=7.30%），Midnight 把属性压缩过，
# 网上流传的"暴击 46=1%"是旧版本的，套上去会算错将近一半。
# 这两张表现在在 wowgear/store.py 里（面板的读写也在那儿，按专精分开存），
# 这里 import 过来只是为了让老的调用点不用改。


# 宝石库里存的是 "main"（三色宝石写的 Primary Stat），它要按职业落地成
# 敏捷/力量/智力，所以在选项列表里只能写成"主属性"。
EXTRA_CN = {"main": "主属性", "leech": "吸血", "avoid": "闪避", "speed": "移速"}

# 客户端 `dbc::all_crit_base()` 写死的 5%，所有职业专精一样。
# 军团版本删掉 gt 表之后，只有这一项基础百分比还能确定地拿到。
CRIT_BASE_PCT = 5.0
TEXT_ORDER = ("main", "agi", "str", "int", "sta",
              "crit", "haste", "mastery", "vers", "leech", "avoid", "speed")

# 宝石品种 -> (它主给的属性, 分类名)。品种是游戏里的规则，不属于数据，
# 所以放在这里而不是数据库：Peridot 给急速、Amethyst 精通、Garnet 暴击、
# Lapis 全能，两种做主属性的（永恒钻石 / 日长石）并成一类。
GEM_GROUP = {
    "peridot": ("haste", "急速"),
    "amethyst": ("mastery", "精通"),
    "garnet": ("crit", "暴击"),
    "lapis": ("vers", "全能"),
    "diamond": ("main", "主属性"),
    "heliotrope": ("main", "主属性"),
}
GEM_GROUP_ORDER = ("haste", "mastery", "crit", "vers", "main", "other")

# 保底插槽：戒指和项链在 12.x 一定带一个棱彩孔（掉的、造的、升级的都算）。
# 数据里只有一部分装备有 has_sockets 记录，按记录判断会把另一半当成"没孔"，
# 结果玩家想给戒指换宝石却点不进去。
GUARANTEED_SOCKETS = {"finger": 1, "neck": 1}

# 能用打孔器（Radiant Jewelbinder）开一个孔的部位。游戏规则：只有头 / 腰带 / 护腕，
# 每件限一个。这个信息不在物品数据里（同一件装备开没开孔是玩家状态），
# 所以只能由玩家在界面上勾选。
DRILLABLE_SLOTS = {"head", "waist", "wrist"}


def _stat_text(stats, primary=None):
    """{"crit":16,"mastery":7} -> "暴击 +16  精通 +7"。顺序固定，主属性在前。

    primary 给了就把 "main"（三色宝石写的 Primary Stat）落到本职业真吃的那条上：
    猎人那颗 +18 是敏捷，法师那颗 +18 是智力，库里只能记成一条 "main"。
    """
    if primary and stats.get("main") and primary not in stats:
        stats = {**stats, primary: stats["main"]}
        stats.pop("main")
    parts = []
    for code in TEXT_ORDER:
        if code in stats and stats[code]:
            label = STAT_CN.get(code) or EXTRA_CN.get(code, code)
            parts.append(f"{label} +{round(stats[code])}")
    return "  ".join(parts)


def _note_cn(note):
    """把附魔的特效原文翻成中文。

    原文是从 wowhead 抓的英文（"critical strike effectiveness +1%"），
    直接显示会在中文界面里冒出一行英文，看着像没做完。
    这类描述格式固定，属性名替换一下就够了，不另存翻译表——
    存表的话每加一条新赛季附魔就要回来补一次。
    """
    if not note:
        return ""
    out = note
    # 长的先替换：先换 "critical strike effectiveness" 才不会
    # 被后面的 "critical strike" 拆成 "暴击 effectiveness"
    for en, cn in (("critical strike effectiveness", "暴击效果"),
                   ("critical strike", "暴击"),
                   ("primary stat", "主属性"),
                   ("versatility", "全能"),
                   ("mastery", "精通"),
                   ("haste", "急速")):
        out = re.sub(en, cn, out, flags=re.I)
    return out.replace("your ", "").strip()


def _ench_name(name):
    """去掉 "Enchant Ring - " / "附魔戒指 - " 前缀。候选列表里已经按部位分好了，
    每条都带前缀会把这行撑满，反而看不清真正叫什么。"""
    if not name:
        return ""
    head, sep, tail = name.partition(" - ")
    return tail if sep else name


def _gem_name(name):
    """"无瑕迅捷榄石" -> "迅捷榄石"。

    品质已经在卡片右边单独标了（普通/无瑕/史诗），名字里再带一遍，
    同一颗宝石的两个装等版本看起来就更分不清了。英文的 Flawless 同理。
    """
    return re.sub(r"^(Flawless|完美|无瑕)\s*", "", name or "").strip()


# 插件在每件装备上方写 `# 中文名 (装等)`（英文版是 `# Name (334)`）。
# 换过装备之后这行要跟着改，否则导回去界面上显示的还是旧名字。
_NAME_ILEVEL = re.compile(r"^#\s+\S.*\(\d+\)\s*$")


def _panel_label(key):
    """`hunter:beast_mastery` -> 「猎人 · 野兽控制」。给面板弹层列已校准的专精用。"""
    cls, _, spec = (key or "").partition(":")
    cn = CLASSES.get(cls, (cls or key,))[0]
    return {"key": key, "label": f"{cn} · {SPEC_CN.get(spec, spec)}" if spec else cn}


def dr_convert(rating, brackets):
    """按递减档位把评级折算成「有效评级」。

    规则来自客户端：每个档位**只对越过该门槛的那部分**评级打折。
    例如急速 -10% 档从 1320 开始，那 1320 之后的评级只按 90% 计入。
    Maxroll 的例子：1320 急速（30%）再加 264 → 应为 36%，实际 35.4%：
    (1320 + 264×0.9) / 44 = 35.4 ✓
    """
    if not rating or not brackets:
        return 0.0
    total = 0.0
    for i, (lo, penalty) in enumerate(brackets):
        if rating <= lo:
            break
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        seg = min(rating, hi) - lo
        total += seg * (1.0 - penalty / 100.0)
        if rating <= hi:
            break
    return total


class Session:
    def __init__(self, conn, prof, budget=None, choices=None, panel=None, raw_text=""):
        self.conn = conn
        self.prof = prof
        self.raw_text = raw_text          # 导入的 simc 原文，配装存档要用
        self.budget = budget if budget is not None else load_budget()
        self.choices = choices if choices is not None else load_choices()
        self.catalog = Catalog(conn, prof)
        # 面板基准按「职业:专精」分开取：换专精（甚至换角色）之后 base 不能沿用上一套
        self.panel_key = panel_key(prof.class_key, prof.spec)
        raw_panel = dict(panel) if panel is not None else load_panel(self.panel_key)
        # 面板里可能带一个 pct 子表（游戏里看到的百分比），用它反推换算率
        self.panel_pct = {k: float(v) for k, v in (raw_panel.pop("pct", {}) or {}).items()}
        self.panel = {k: v for k, v in raw_panel.items() if k in PANEL_STATS}
        self.rating_per_pct = self._rates()
        self.base = {}
        self.base_source = ""      # "panel" / "class" / 空
        self._curves = None        # meta 里的 rating_curves，懒加载
        self._spec_base = None     # spec_stats 里的专精基线，懒加载
        self._flat = self.flat_pct()   # 角色自带的固定百分比（专精基线 + 面板覆盖）
        self.gear_totals = {}
        self.totals = {}
        self.detail = []
        # 原始装备快照，用于"恢复这个部位"
        self.original = {k: v for k, v in prof.items.items()}
        # 换装历史，撤销用。栈里存 (部位, 换之前的件)，换回去即可
        self.history = []
        self._ilevels = None
        self._apply_choices()
        self.recompute()

    def rating_curves(self):
        """库里存的换算表（meta.rating_curves）。没有就返回 None，退回线性。"""
        if self._curves is None:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key = 'rating_curves'").fetchone()
            self._curves = json.loads(row["value"]) if row and row["value"] else {}
        return self._curves or None

    def mastery_coeff(self):
        """本专精的精通系数（每 1 点给多少 %）。查不到返回 None。"""
        key = f"{self.prof.class_key}:{self.prof.spec}"
        row = self.conn.execute(
            "SELECT coeff FROM spec_mastery WHERE spec = ?", (key,)).fetchone()
        return row["coeff"] if row else None

    def _curve_pct(self, stat, rating):
        """只按曲线算评级那部分，不含角色自带的固定百分比。"""
        c = self.rating_curves()
        if not c or not rating:
            return None
        base = c.get("base_rate") or {}
        dr = c.get("dr") or {}
        if stat == "mastery":
            coeff = self.mastery_coeff()
            if not coeff or not base.get("mastery"):
                return None
            # 这里**只剩评级换算**。基础精通点数不再用全局的 8——实测每个专精
            # 差得离谱（射击 8 点、生存 14 点），存在 spec_stats 里由 flat_pct 加回。
            return dr_convert(rating, dr.get("mastery", [])) / base["mastery"] * coeff
        if stat not in base:
            return None
        return dr_convert(rating, dr.get(stat, [])) / base[stat]

    def spec_base(self):
        """本专精的默认基线（天赋/被动给的固定百分比），没有就返回 {}。

        这批数换了赛季会失效——天赋改版直接改变它。所以 build_db 会比对
        spec_stats.json 里记的 build 和当前 build，不一致时提醒重采。
        """
        if self._spec_base is None:
            key = f"{self.prof.class_key}:{self.prof.spec}"
            try:
                row = self.conn.execute(
                    "SELECT crit, haste, mastery, vers FROM spec_stats WHERE spec = ?",
                    (key,)).fetchone()
            except sqlite3.OperationalError:
                row = None      # 老库还没这张表，退回面板反推
            self._spec_base = ({k: row[k] for k in ("crit", "haste", "mastery", "vers")
                                if row[k] is not None} if row else {})
        return self._spec_base

    def flat_source(self):
        """每项固定加成是从哪来的，前端要据此告诉用户"这个数是准的还是估的"。

        panel — 玩家填了面板，精确到这个角色
        spec  — 查 spec_stats 表，是该专精的默认基线（不含角色自己的天赋选择）
        base  — 只有客户端写死的 5% 基础暴击，职业天赋一概没算
        none  — 都没有，这一项只有装备部分，会比游戏里低
        """
        out = {}
        for k in ("crit", "haste", "mastery", "vers"):
            if self.panel.get(k) and k in (self.panel_pct or {}):
                out[k] = "panel"
            elif k in self.spec_base():
                out[k] = "spec"
            elif k == "crit":
                out[k] = "base"
            else:
                out[k] = "none"
        return out

    def flat_pct(self):
        """角色自带的固定百分比（天赋/被动/种族），不是换来的。

        两级来源，面板优先：
        1. `spec_stats`：专精默认基线，没填面板也有数
        2. 面板反推：面板那一格减去按曲线算出来的装备部分

        为什么面板优先：天赋是**角色级**选择，同一个专精两个人能差好几个点。
        但反过来，只有专精基线的话，换个没标定过的角色也不会差到离谱——
        同一专精的差异通常只是可选项，不像专精之间差十几点。
        """
        out = dict(self.spec_base())
        # 专精表里没有的，至少把客户端那 5% 基础暴击给上。
        # SimC 里 `dbc::all_crit_base()` 直接 `return 0.05`，和职业专精都无关
        # （注释写着 "Gametables removed in Legion"）——这是唯一一个不查表、
        # 不扫被动就确定存在的基础百分比，缺了它战士的暴击会凭空少 5 个点。
        if "crit" not in out:
            out["crit"] = CRIT_BASE_PCT
        for k, pct in (self.panel_pct or {}).items():
            val = self.panel.get(k)
            if not val:
                continue
            base = self._curve_pct(k, val)
            if base is not None:
                out[k] = round(pct - base, 2)
        return out

    def stat_pct(self, stat, rating):
        """评级 -> 百分比。曲线（含递减）+ 角色自带的固定加成。"""
        pct = self._curve_pct(stat, rating)
        if pct is None:
            return None
        return pct + self._flat.get(stat, 0.0)

    def _pct_all(self):
        """四个绿字的百分比，缺数据的项不塞进来（前端自己会退回线性）。"""
        out = {}
        for k in ("crit", "haste", "mastery", "vers"):
            pct = self.stat_pct(k, self.totals.get(k, 0))
            if pct is not None:
                out[k] = round(pct, 2)
        return out

    def _apply_choices(self):
        for it in self.prof.items.values():
            if it.item_id in self.choices:
                it.crafted_stats = self.choices[it.item_id]

    def class_base(self):
        """裸装基础属性（职业 gt 表 + 种族修正），只给主属性和耐力。

        数据是 `fetch_class_base.py` 从客户端 `gtClassStatsByLevel` / `gtRaceStats`
        抽的，**不含天赋和专精被动**——真实裸装会比这个数高。所以它只是
        "没填面板时的默认基准"，填了面板（含天赋的真实值）就盖过它。

        只返回本职业真正吃的那条主属性 + 耐力：把力量/敏捷/智力全塞进去，
        猎人的属性条上会平白多出两条用不上的。
        """
        prof = self.prof
        if not prof.class_key or not prof.level:
            return {}
        row = self.conn.execute(
            "SELECT str, agi, sta, int FROM class_base WHERE class = ? AND level = ?",
            (prof.class_key, int(prof.level))).fetchone()
        if not row:
            return {}
        primary = class_primary(prof.class_key, prof.spec)
        if not primary:
            return {}
        out = {primary: float(row[primary]), "sta": float(row["sta"])}
        race = (prof.scalars or {}).get("race")
        if race:
            rr = self.conn.execute(
                "SELECT str, agi, sta, int FROM race_base WHERE race = ?",
                (race,)).fetchone()
            if rr:
                out[primary] += rr[primary]
                out["sta"] += rr["sta"]
        return {k: v for k, v in out.items() if v}

    # ---------------------------------------------------------------- 计算

    def recompute(self):
        """算装备合计，再加上角色自身的基础值。

        只把装备加起来是错的：面板上的敏捷 2861 里有很大一部分来自天赋和等级，
        装备只贡献 1765。拿装备合计当"总量"会让用户以为自己少穿了半套。

        base 有三种来源，优先级从高到低：
          1. 玩家填的面板（`base = 面板 − 首次装备合计`）——含天赋，最准
          2. 职业基础（客户端 gt 表，不含天赋）——没填面板时的默认值
          3. 无——只显示装备本体
        """
        self.gear_totals, self.detail = compute(self.conn, self.prof, self.budget)
        if self.panel:
            if not self.base:
                # 第一次算完才能定出基础值；之后装备怎么换 base 都不动
                self.base = {k: v - self.gear_totals.get(k, 0.0)
                             for k, v in self.panel.items()}
                self.base = {k: v for k, v in self.base.items() if abs(v) > 0.5}
                self.base_source = "panel"
        elif not self.base:
            cb = self.class_base()
            if cb:
                self.base = cb
                self.base_source = "class"
        self.totals = self._with_base(self.gear_totals)
        return self.totals

    def _with_base(self, gear):
        out = dict(gear)
        for k, v in self.base.items():
            out[k] = out.get(k, 0.0) + v
        return out

    def _rates(self):
        """评级 -> 百分比。默认用固定转换率；面板里带了百分比就用它反推。

        精通**必须**按专精算：兽王 1423 → 79.5%（17.9/1%），射击 1142 → 20.5%
        （55.7/1%），同一个公式差近 4 倍。不知道这个专精的系数时，显示出来的
        百分比会离谱到没法看。
        """
        rates = dict(RATING_PER_PCT)
        for k, pct in (self.panel_pct or {}).items():
            val = self.panel.get(k)
            if val and pct and pct > 0 and k in rates:
                rates[k] = round(val / pct, 2)
        return rates

    def set_panel(self, panel):
        """玩家填面板值。base 按当前这套装备重算，只写回当前专精那一份。

        `panel` 里可以带 `pct` 子字典（面板上的百分比），用来把换算率也校准掉。
        """
        data = dict(panel or {})
        self.panel_pct = {k: float(v) for k, v in (data.pop("pct", {}) or {}).items()}
        self.panel = {k: float(v) for k, v in data.items()
                      if k in PANEL_STATS and v}
        self.rating_per_pct = self._rates()
        # 面板一改，固定百分比就得重算：它俩是「面板% − 曲线%」推出来的。
        # 以前这里漏了，填完面板百分比纹丝不动，看着像没生效。
        self._flat = self.flat_pct()
        self.base = {k: v - self.gear_totals.get(k, 0.0)
                     for k, v in self.panel.items()}
        self.base = {k: v for k, v in self.base.items() if abs(v) > 0.5}
        # 清空面板时把来源也清掉，recompute() 会退回职业基础
        self.base_source = "panel" if self.panel else ""
        saved = dict(self.panel)
        if self.panel_pct:
            saved["pct"] = self.panel_pct
        save_panel(self.panel_key, saved)
        self.recompute()
        return self.totals

    def socket_count(self, item_id, simc_sockets=None, gems=(), drilled=0):
        """这件装备能插几颗宝石。

        规则来自游戏本身（12.x 改了宝石系统）：
        - 戒指和项链**保底一个棱彩孔**，不管是掉的、造的还是升级出来的
        - 头 / 腰带 / 护腕可以用打孔器（Radiant Jewelbinder，6 个低保币）
          开一个孔，但开没开只有玩家身上那件知道
        - 别的部位一个都没有；少数团本项链自带两孔

        三个数字取最大：库里的 has_sockets（爬来的真值）、simc 里的 sockets=、
        以及已经插着的宝石数量（import 时游戏里明明插着，说明就是有孔，
        不能因为库里没记录就把人家宝石判成不存在）。

        drilled 是玩家自己开的那一个孔，最后再加。这里要把它从"宝石数量"
        那一路证据里减掉，否则开完孔插上宝石，len(gems) 会比真实孔数多 1，
        下一轮又把它算成"本来就有这个孔"，再开一次就变成两个孔了。
        """
        row = self.conn.execute(
            "SELECT slot, has_sockets FROM items WHERE id = ?", (item_id,)).fetchone()
        base = (row["has_sockets"] if row else 0) or 0
        if row:
            base = max(base, GUARANTEED_SOCKETS.get(row["slot"], 0))
        drilled = int(drilled or 0)
        # 用"位置数"而不是"空了几颗"：gem_ids 按孔位对齐，`(0, 240983)` 说明
        # 这件装备**至少两个孔**（第一个空着）。早先只数非空宝石，拔掉第一颗后
        # 孔数从 2 掉到 1，第二颗宝石连同那个孔直接消失——界面上就变成
        # "两个孔只能选一个"。
        evidence = len(tuple(gems or ())) - drilled
        return max(base, simc_sockets or 0, evidence, 0) + drilled

    def can_drill(self, db_slot, drilled=0):
        """这个部位还能不能再开一个孔。

        游戏规则：头 / 腰带 / 护腕各能用打孔器开**一个**，开了就没了；
        别的部位（戒指项链保底孔、胸腿脚手肩）没有打孔器可用。
        """
        return db_slot in DRILLABLE_SLOTS and not drilled

    def set_drilled(self, slot_key, on=True):
        """用打孔器开一个孔 / 取消这个孔。

        开孔是**玩家的一次选择**，不在物品数据里：同一个头盔，开没开过孔
        游戏里是两个状态，导出的 simc 又不带这个信息。所以只能由玩家在界面上说，
        记在会话里，跟着撤销栈走。
        """
        it = self.prof.items.get(slot_key)
        if it is None:
            raise ValueError("这个部位还没有装备，先选装备")
        if on:
            if it.db_slot not in DRILLABLE_SLOTS:
                raise ValueError(f"「{self._item_label(it)}」这个部位不能用打孔器"
                                 f"（只有头、腰带、护腕能开孔）")
            if it.drilled:
                return self.totals
            self._mutate(slot_key, it, drilled=1)
        else:
            if not it.drilled:
                return self.totals
            n = self.socket_count(it.item_id, it.socket_count, it.gem_ids, it.drilled)
            if len(it.gem_ids) >= n and it.gem_ids[n - 1]:
                # 孔里还插着宝石就撤孔，宝石会凭空消失，先让玩家自己拔
                raise ValueError("这个孔里还插着宝石，先把宝石拔下来再取消开孔")
            self._mutate(slot_key, it, drilled=0)
        return self.recompute()

    def _make_item(self, slot_key, item_id, ilevel, crafted_stats=()):
        row = self.conn.execute("SELECT slot, name, has_sockets FROM items WHERE id = ?",
                                (item_id,)).fetchone()
        if not row:
            raise ValueError(f"库中没有装备 {item_id}")
        # 宝石和附魔跟着槽位走：换装备时人会把宝石抠下来重新插、附魔重新附，
        # 属性计算用不到这两个字段，但导出 simc 给 Raidbots 时必须带上，
        # 否则模拟出来的属性平白少一截（尤其戒指项链的宝石）。
        #
        # 但孔数必须按新装备截断：从戒指换到没孔的头盔，宝石不会跟过去。
        # 不截断的话，导出会写成"没孔的头盔插着两颗宝石"，Raidbots 直接忽略，
        # 界面上却还显示这两颗的属性，两边就对不上了。
        old = self.prof.items.get(slot_key)
        gems = tuple(old.gem_ids) if old else ()
        n = self.socket_count(item_id, old.socket_count if old else None, gems)
        return EquippedItem(
            slot=slot_key, db_slot=row["slot"], name=row["name"],
            item_id=item_id, ilevel=ilevel, crafted_stats=tuple(crafted_stats),
            gem_ids=gems[:n],
            enchant_id=old.enchant_id if old else None,
            socket_count=n)

    def preview(self, slot_key, item_id, ilevel, crafted_stats=()):
        """临时换上算一遍，然后原样放回去。返回新的属性合计。"""
        return self.preview_full(slot_key, item_id, ilevel, crafted_stats)[0]

    def preview_full(self, slot_key, item_id, ilevel, crafted_stats=()):
        """同上，但同时返回换完之后的平均装等——顶部那行要一起变。"""
        old = self.prof.items.get(slot_key)
        self.prof.items[slot_key] = self._make_item(slot_key, item_id, ilevel, crafted_stats)
        try:
            items = dict(self.prof.items)
            return (self._with_base(compute(self.conn, self.prof, self.budget)[0]),
                    self.avg_ilevel(items)[0])
        finally:
            if old is None:
                self.prof.items.pop(slot_key, None)
            else:
                self.prof.items[slot_key] = old

    def equip(self, slot_key, item_id, ilevel, crafted_stats=()):
        old = self.prof.items.get(slot_key)
        new = self._make_item(slot_key, item_id, ilevel, crafted_stats)
        self.prof.items[slot_key] = new
        # 换成同一件就不记历史，否则连点两下要按两次撤销
        if old is None or old.item_id != item_id:
            self.history.append((slot_key, old))
            if len(self.history) > 50:
                self.history.pop(0)
        return self.recompute()

    def undo(self):
        """撤销上一步换装。返回被撤销的部位，空栈时返回 None。"""
        if not self.history:
            return None
        slot_key, old = self.history.pop()
        if old is None:
            self.prof.items.pop(slot_key, None)
        else:
            self.prof.items[slot_key] = old
        self.recompute()
        return slot_key

    def reset(self, slot_key):
        """恢复这个部位的原始装备。"""
        orig = self.original.get(slot_key)
        old = self.prof.items.get(slot_key)
        if orig is None:
            self.prof.items.pop(slot_key, None)
        else:
            self.prof.items[slot_key] = orig
        if old is not None and (orig is None or old.item_id != orig.item_id):
            self.history.append((slot_key, old))
        return self.recompute()

    def reset_all(self):
        self.prof.items = {k: v for k, v in self.original.items()}
        self.history.clear()
        return self.recompute()

    # ------------------------------------------------------------ 宝石 / 附魔

    def _mutate(self, slot_key, old, **changes):
        """改一件装备的某几个字段，并记进撤销栈。

        history 里存的是整个 EquippedItem，所以改宝石、改附魔天然可以撤销，
        不用为它们再造一套栈——那个栈迟早会和换装的栈打架。
        """
        new = replace(old, **changes)
        if new == old:
            return False          # 没变就别记，否则连点两下要撤两次
        self.prof.items[slot_key] = new
        self.history.append((slot_key, old))
        if len(self.history) > 50:
            self.history.pop(0)
        return True

    def _item_label(self, it):
        """报错时怎么称呼这件装备。

        不能直接用 it.name：simc 导出的行常常是 `neck=,id=268265`，
        前缀是空的，解析出来的名字就是空字符串，报错会变成「」没有插槽。
        """
        row = self.conn.execute(
            "SELECT name_cn, name FROM items WHERE id = ?", (it.item_id,)).fetchone()
        if row:
            return row["name_cn"] or row["name"]
        return it.name or str(it.item_id)

    def _gems_after(self, it, index, gem_id):
        """算出改完之后这个孔位的宝石列表，不落地。

        孔是**位置**不是**数量**：`gem_ids` 按槽位对齐，空孔用 0 占位。
        以前拔掉第 1 颗会用 pop 把数组压紧，第 2 颗就顶到第 1 个孔上去了——
        数值没错，但界面上宝石"跳"了一个孔，导出的 simc 也把位置写错了。
        SimC 自己就是用 0 表示空孔的（engine/item/item.cpp 里 `range::fill(gem_id, 0)`
        之后照样把 0 写进 gem_id=），所以 0 占位是格式允许的写法。
        """
        if it is None:
            raise ValueError("这个部位还没有装备，先选装备")
        n = self.socket_count(it.item_id, it.socket_count, it.gem_ids, it.drilled)
        index = int(index)
        gems = list(it.gem_ids) + [0] * max(0, n - len(it.gem_ids))
        if gem_id:
            # 孔数是装备的物理属性，插不进去就是插不进去。
            # 以前这里只挡住越界不挡总数，头盔武器也能凭空多出三颗宝石。
            if n == 0:
                raise ValueError(f"「{self._item_label(it)}」没有插槽，插不了宝石")
            if index >= n:
                raise ValueError(f"「{self._item_label(it)}」只有 {n} 个插槽")
            gems[index] = int(gem_id)
        else:
            # 拔一颗本来就不存在的宝石，什么都不用做
            if index >= n or index >= len(gems) or not gems[index]:
                return None
            gems[index] = 0
        # 末尾的空孔不留：gem_ids 只记到最后一颗宝石为止，
        # 否则每件装备都会拖一串 0，导出时 gem_id=240908/0/0/0
        while gems and not gems[-1]:
            gems.pop()
        return tuple(gems)

    def _check_gem_unique(self, slot_key, index, gem_id):
        """唯一宝石（永歌钻石这类）每人限一颗。

        `gems.unique_group` 为 NULL 的不检查。同一颗宝石的不同装等版本
        （278 / 295）算同一组——游戏里它们就是同一颗的两种版本。

        目标孔里本来放着的就是它，算"重插"，放行；其余任何位置（包括同一件
        装备的另一个孔）已有同组宝石就拒绝。
        """
        if not gem_id:
            return
        gem_id, index = int(gem_id), int(index)
        row = self.conn.execute(
            "SELECT name, name_cn, unique_group FROM gems WHERE id = ?",
            (gem_id,)).fetchone()
        if not row or not row["unique_group"]:
            return
        for key, other in self.prof.items.items():
            for i, gid in enumerate(other.gem_ids):
                if not gid:
                    continue
                if key == slot_key and i == index:
                    continue          # 这个孔里就是它，等于原地重插
                r = self.conn.execute(
                    "SELECT unique_group FROM gems WHERE id = ?", (gid,)).fetchone()
                if r and r["unique_group"] == row["unique_group"]:
                    where = SLOT_LABELS.get(other.db_slot, (other.db_slot, 0))[0]
                    raise ValueError(
                        f"「{row['name_cn'] or row['name']}」每人只能装一颗"
                        f"（{where}上已经有一颗了）")

    def set_gem(self, slot_key, index, gem_id):
        """改第 index 个孔的宝石。gem_id 传 0 表示拔掉这一颗。"""
        it = self.prof.items.get(slot_key)
        if it is None:
            raise ValueError(f"{slot_key} 上还没有装备，先选装备")
        self._check_gem_unique(slot_key, index, gem_id)
        gems = self._gems_after(it, index, gem_id)
        if gems is None:
            return self.totals        # 拔一个空孔，什么都不用做
        self._mutate(slot_key, it, gem_ids=gems)
        return self.recompute()

    def preview_gem(self, slot_key, index, gem_id):
        """临时插上这颗宝石算一遍，再放回去。返回换完的属性合计。

        和换装预览同一个理由：宝石模式以前只能点了才知道属性怎么变，
        而"要不要为了 +16 精通拔掉 +7 暴击"正是来这儿要判断的事。
        """
        it = self.prof.items.get(slot_key)
        if it is None:
            raise ValueError(f"{slot_key} 上还没有装备，先选装备")
        self._check_gem_unique(slot_key, index, gem_id)
        gems = self._gems_after(it, index, gem_id)
        if gems is None:
            return None
        return self._swap_preview(slot_key, replace(it, gem_ids=gems))

    def _enchant_after(self, it, enchant_id):
        """校验附魔能不能附在这件装备上，返回 enchant_id（0/None 表示去掉）。"""
        if it is None:
            raise ValueError("这个部位还没有装备，先选装备")
        eid = int(enchant_id) if enchant_id else None
        if eid:
            row = self.conn.execute(
                "SELECT slot FROM enchants WHERE id = ?", (eid,)).fetchone()
            if not row:
                raise ValueError(f"库里没有附魔 {eid}")
            # 附魔只能附在对应部位上。允许乱附的话，头部的附魔会算进戒指，
            # 数字看着变了但游戏里根本插不上。
            if row["slot"] != it.db_slot:
                raise ValueError(
                    f"这条附魔是给 {row['slot']} 的，不能附在 {it.db_slot} 上")
        return eid

    def set_enchant(self, slot_key, enchant_id):
        """换附魔。传 0 或空表示去掉附魔。"""
        it = self.prof.items.get(slot_key)
        eid = self._enchant_after(it, enchant_id)
        self._mutate(slot_key, it, enchant_id=eid)
        return self.recompute()

    def preview_enchant(self, slot_key, enchant_id):
        """临时附上算一遍，再放回去。附魔的差额（尤其 186 耐力）不预览看不出值不值。"""
        it = self.prof.items.get(slot_key)
        eid = self._enchant_after(it, enchant_id)
        return self._swap_preview(slot_key, replace(it, enchant_id=eid))

    def _swap_preview(self, slot_key, new_item):
        """把这件临时放上去算一遍，然后原样放回。"""
        old = self.prof.items.get(slot_key)
        self.prof.items[slot_key] = new_item
        try:
            totals, _ = compute(self.conn, self.prof, self.budget)
            return self._with_base(totals)
        finally:
            if old is None:
                self.prof.items.pop(slot_key, None)
            else:
                self.prof.items[slot_key] = old

    def gem_options(self):
        """所有宝石，带中文名 / 品种 / 品质 / 装等 / 图标。

        宝石有两层属性：品种（Peridot/Amethyst/Garnet/Lapis）决定主要给哪条，
        前缀（Quick/Deadly/Masterful/Versatile）决定另一条配合的。
        玩家找宝石的心智是"我要急速的"，所以对外按 group（属性）分类，
        品种名放在卡片副行，品质决定卡片配色。

        **装等必须给出来**：同一颗宝石有 278 和 295 两个版本，名字、品质、
        图标完全一样，数值差 1~2 点。不写装等就是两张分不清的卡。
        """
        primary = class_primary(self.prof.class_key, self.prof.spec)
        rows = self.conn.execute(
            "SELECT id, name, name_cn, icon, stats, kind, kind_cn, quality, ilvl, "
            "       unique_group FROM gems").fetchall()
        out = []
        for r in rows:
            stats = json.loads(r["stats"])
            group, group_cn = GEM_GROUP.get(r["kind"], ("other", "其它"))
            # 三色宝石的 "main" 要落到本职业真吃的那条上，否则卡片上写
            # "主属性 +18"，猎人没法判断这 18 点是敏捷还是智力。
            shown = dict(stats)
            if primary and shown.get("main"):
                shown[primary] = shown.pop("main")
            out.append({
                "id": r["id"],
                "name": _gem_name(r["name_cn"] or r["name"]),
                "name_en": r["name"],
                "full_name": r["name_cn"] or r["name"],
                "icon": r["icon"],
                "stats": shown,
                "text": _stat_text(stats, primary),
                "kind": r["kind"], "kind_cn": r["kind_cn"] or "其它",
                "group": group, "group_cn": group_cn,
                "quality": r["quality"] or 2,
                "ilvl": r["ilvl"],
                # 唯一宝石：同一组每人只能装一颗，前端据此标"唯一"并禁用已装备的卡
                "unique_group": r["unique_group"],
                "weight": (1000 if "main" in stats else 0) + sum(stats.values()),
            })
        # 品质优先（史诗宝石数值本来就高），但同一品种的排在一起：
        # "全部"模式下如果纯按品质排，75 颗会变成史诗/精良/普通三团混着各品种，
        # 看着还是没分类。按品种分组、组内品质降序，一屏扫下去才成块。
        # 同品质再按装等降序：278 和 295 两个版本要并排、高的在前。
        out.sort(key=lambda x: (GEM_GROUP_ORDER.index(x["group"]),
                                -x["quality"], -(x["ilvl"] or 0),
                                -x["weight"], x["name"]))
        return out

    def enchant_slots(self):
        """哪些部位有附魔、各几条。{slot: 数量}

        界面上某个部位一条都没有时，光说"没有可附的魔"会和"这条没收录"
        混在一起——腿部的附魔是存在的，只是 id（8159）不在 SimC 那张清单里。
        """
        return {r["slot"]: r["n"] for r in self.conn.execute(
            "SELECT slot, COUNT(*) AS n FROM enchants GROUP BY slot")}

    def enchant_options(self, db_slot):
        """某个部位能用的附魔。

        第三属性和百分比特效也要带上——它们不进口算，但用户得知道
        "这条附魔不是白给"，不然会以为选了没用。
        """
        primary = class_primary(self.prof.class_key, self.prof.spec)
        rows = self.conn.execute(
            "SELECT id, name, name_cn, slot, item_id, stats, tertiary, note, desc, icon "
            "  FROM enchants WHERE slot = ? ORDER BY id", (db_slot,)).fetchall()
        out = []
        for r in rows:
            stats = json.loads(r["stats"] or "{}")
            tert = json.loads(r["tertiary"] or "{}")
            # "+36 主属性"落到本职业真吃的那条上，卡片上写"敏捷 +36"而不是"主属性 +36"
            if primary and stats.get("main"):
                stats = {**stats, primary: stats["main"]}
                stats.pop("main")
            full = r["name_cn"] or r["name"]
            note = _note_cn(r["note"])
            out.append({
                "id": r["id"],
                # 列表里已经按部位分好了，名字不用再带"附魔戒指 - "
                "name": _ench_name(full),
                "full_name": full,
                "name_en": r["name"],
                "slot": r["slot"],
                "item_id": r["item_id"], "stats": stats, "tertiary": tert,
                "note": note, "desc": r["desc"] or "", "icon": r["icon"] or "",
                # 附魔不随装备装等缩放，点数就是点数（Tier2 / 580 卷轴那档）
                "text": (_stat_text(stats) or _stat_text(tert)
                         or note or r["desc"] or ""),
            })
        return out

    # ---------------------------------------------------------------- 配装存档

    def changes(self):
        """相对导入时的改动清单。存档只存这个，不存整份装备表。

        存"改了哪几格"的好处：以后属性公式或装备库更新，重新载入就按新数据
        算一遍；存死最终属性表的话，存档会永远停在保存那天的旧公式上。

        **宝石和附魔也要记**。早先这里只比 item_id / 装等 / 自选属性，
        插的宝石、附的魔改了却不算"改动"——存完配装再载入，宝石悄悄回到
        导入时那几颗。这种错不报异常，只会让人以为"存档坏了"。
        """
        out = []
        for key, cur in self.prof.items.items():
            orig = self.original.get(key)
            same_item = (orig is not None and orig.item_id == cur.item_id
                         and (cur.comment_ilevel or cur.ilevel)
                         == (orig.comment_ilevel or orig.ilevel)
                         and tuple(cur.crafted_stats) == tuple(orig.crafted_stats))
            same_custom = (orig is not None
                           and tuple(cur.gem_ids) == tuple(orig.gem_ids)
                           and cur.enchant_id == orig.enchant_id
                           and (cur.drilled or 0) == (orig.drilled or 0))
            if same_item and same_custom:
                continue
            out.append({"slot": key, "id": cur.item_id,
                        "ilevel": cur.comment_ilevel or cur.ilevel,
                        "crafted": list(cur.crafted_stats),
                        "gems": list(cur.gem_ids),
                        "enchant": cur.enchant_id,
                        "drilled": int(cur.drilled or 0)})
        return out

    def apply_changes(self, changes):
        """载入存档：把改动逐条装上。认不出的装备跳过，不整份失败——
        赛季一换库里的老 id 就没了，能载回大半套也比报错强。"""
        skipped = []
        for ch in changes or []:
            key, item_id = ch.get("slot"), ch.get("id")
            if not key or not item_id:
                continue
            try:
                self.equip(key, int(item_id), ch.get("ilevel"),
                           tuple(int(x) for x in ch.get("crafted") or ()))
            except ValueError:
                skipped.append(key)
                continue
            # 打孔器的孔在装备本体之后恢复：它决定这件装备有几个孔
            it = self.prof.items.get(key)
            if it is not None and ch.get("drilled"):
                self.prof.items[key] = replace(it, drilled=1)
                it = self.prof.items[key]
            # 宝石按孔位恢复：存档里有这一项才动，老存档没有这个键就保持原样
            if "gems" in ch and it is not None:
                gems = tuple(int(g) for g in (ch.get("gems") or ()))
                n = self.socket_count(it.item_id, it.socket_count, gems, it.drilled)
                self.prof.items[key] = replace(it, gem_ids=gems[:n] if n else ())
            if "enchant" in ch:
                try:
                    self.set_enchant(key, ch.get("enchant") or 0)
                except ValueError:
                    pass          # 附魔清单变了（赛季更新）就跳过，不影响别的部位
        self.history.clear()
        self.recompute()
        return skipped

    # ---------------------------------------------------------------- 平均装等

    def avg_ilevel(self, items=None):
        """角色面板上那个"平均装等"。

        游戏的口径是 16 个格子求和除以 16，空着的算 0，**双手武器算两次**
        （它同时占主手和副手两个格子）。所以猎人拿一把 341 的弓，
        武器贡献的是 682 而不是 341。

        没有副手但主手是双手武器时才补上第二次；单手武器 + 空副手，
        副手那一格就是 0，不能凭空补齐——不然一手剑的角色会虚高。
        """
        items = items if items is not None else self.prof.items
        lv = {k: (it.comment_ilevel or it.ilevel or 0) for k, it in items.items()}
        filled = sum(1 for k in ROSTER if lv.get(k))
        main = items.get("main_hand")
        if main and not items.get("off_hand"):
            if self._inventory_type(main.item_id) in TWO_HAND_TYPES:
                lv["off_hand"] = lv.get("main_hand", 0)
        total = sum(lv.get(k, 0) for k in ROSTER)
        return round(total / len(ROSTER), 2), filled

    def _inventory_type(self, item_id):
        if not hasattr(self, "_inv_cache"):
            self._inv_cache = {}
        if item_id not in self._inv_cache:
            row = self.conn.execute("SELECT inventory_type FROM items WHERE id = ?",
                                    (item_id,)).fetchone()
            self._inv_cache[item_id] = row["inventory_type"] if row else None
        return self._inv_cache[item_id]

    # ---------------------------------------------------------------- 序列化

    @property
    def armor_types(self):
        entry = CLASSES.get(self.prof.class_key)
        return entry[1] if entry else []

    @property
    def armor_cn(self):
        return "/".join(ARMOR_CN.get(a, a) for a in self.armor_types) or "不限"

    def stat_values(self, db_slot, ilevel, stats, crafted=()):
        """单件装备在某装等下的属性数值。

        算法本身在 wowgear.stats（主属性 / 耐力 / 绿字走三条不同曲线），
        这里只把库里的槽位名和 stats 结构转过去。方法留着是为了让调用方
        不用改，也方便测试替换。
        """
        return stat_calc.item_values(db_slot, ilevel, stats, crafted)

    def ilevel_options(self):
        """本赛季能选的全部装等，按升级轨道分组。

        只有制造业和装绑能自选装等是错的——任何一件装备都有"这件是几级升上来的"
        的问题。神话团本尾王的 344、额外升级的 341 都不在 ilevel_tracks 里，
        单独列一组。属性预算表（RandPropPoints）覆盖到 875，这些档位都能直接算。
        """
        if self._ilevels is None:
            groups = {}
            for r in self.conn.execute(
                    "SELECT track_name, ilevel FROM ilevel_tracks ORDER BY ilevel"):
                groups.setdefault(r["track_name"], []).append(r["ilevel"])
            out = []
            for name in ("Adventurer", "Veteran", "Champion", "Hero", "Myth"):
                if name in groups:
                    out.append({"track": name, "label": TRACK_CN.get(name, name),
                                "levels": sorted(set(groups[name])), "notes": {}})
            out.append({"track": "Extra", "label": "特殊",
                        "levels": [lv for lv, _ in EXTRA_ILEVELS],
                        "notes": {str(lv): note for lv, note in EXTRA_ILEVELS}})
            self._ilevels = out
        return self._ilevels

    def item_payload(self, item_id, ilevel=None, crafted_stats=(), stat_src=None):
        """装备详情。实现在 wowgear.catalog，这里转发保持旧调用点不变。"""
        return self.catalog.payload(item_id, ilevel, crafted_stats, stat_src)

    def candidates(self, slot_key, ilevel=None):
        """候选列表。实现在 wowgear.catalog。"""
        return self.catalog.candidates(slot_key, ilevel)

    # simc 里算"装备"的那几个键。别的键（level/spec/region/server/talents）是头部
    SIMC_ITEM_SLOTS = frozenset((
        "head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist",
        "legs", "feet", "finger1", "finger2", "trinket1", "trinket2",
        "main_hand", "off_hand"))

    def _customized(self, slot_key):
        """这件装备的宝石或附魔被改过吗（装备本体没换）。

        单拎出来是因为它不能走 _simc_line 整行重写：原行里可能有
        redirected_base_stats（套装转化件的源装备）、content_tuning、
        crafting_quality，解析器不认但 Raidbots 认，重写一遍就全丢了。
        """
        cur = self.prof.items.get(slot_key)
        orig = self.original.get(slot_key)
        if cur is None or orig is None:
            return False
        if cur.item_id != orig.item_id:
            return False        # 本体换了，走整行重写，不用管这里
        return (tuple(cur.gem_ids) != tuple(orig.gem_ids)
                or cur.enchant_id != orig.enchant_id)

    def _patch_line(self, line, item):
        """只改原行里的 gem_id / enchant_id，其它字段一字不动。"""
        parts = [p for p in line.split(",")
                 if p.split("=", 1)[0].strip() not in ("gem_id", "enchant_id")]
        if item.gem_ids:
            parts.append("gem_id=" + "/".join(map(str, item.gem_ids)))
        if item.enchant_id:
            parts.append(f"enchant_id={item.enchant_id}")
        return ",".join(parts)

    def _simc_line(self, key, item):
        """一件装备写成 simc 的一行。字段顺序和 /simc 导出的顺序一致。

        **名字必须留空，只写 id。** 这不是省事，是 simc 的硬约束：
        `engine/item/item.cpp` 里，装备行同时给了名字和 id 时，会拿名字和
        自己 DBC 里的名字比对，一旦不一致就
            sim->error("... has inconsistency between name '{}' and '{}' for id {}")
        `sim->error` 会让整个模拟以失败退出——Raidbots 那边看到的就是
        "input is not valid"，而装备本身一行都没错。

        游戏插件正是因此才不写名字的：中文客户端拿到的名字是中文，
        simc 必然不认，所以它只吐 `head=,id=271492`。我们曾经"好心"补上
        英文名，正好踩在这个雷上，一整份 profile 被拒。
        """
        parts = [""]
        if item.item_id:
            parts.append(f"id={item.item_id}")
        if item.bonus_ids:
            parts.append("bonus_id=" + "/".join(map(str, item.bonus_ids)))
        lvl = item.comment_ilevel or item.ilevel
        if lvl:
            parts.append(f"ilevel={lvl}")
        if item.gem_ids:
            parts.append("gem_id=" + "/".join(map(str, item.gem_ids)))
        if item.enchant_id:
            parts.append(f"enchant_id={item.enchant_id}")
        if item.crafted_stats:
            parts.append("crafted_stats=" + "/".join(map(str, item.crafted_stats)))
        return f"{key}=" + ",".join(parts)

    def _bag_notes(self, key, item, seen):
        """把一件装备写成背包段的三行注释。

        `### Gear from Bags` 整段都是 `#` 注释，插件**不往这里写真正的装备行**。
        Raidbots 是自己解析这些注释来建"可选装备池"的——Top Gear 的装备勾选框
        就是从这里来的。所以格式必须和插件一致，多一个字段少一个都可能被忽略。
        """
        if str(item.item_id) in seen:
            return []
        seen.add(str(item.item_id))
        lvl = item.comment_ilevel or item.ilevel
        label = self._item_label(item)
        return ["#",
                f"# {label}" + (f" ({lvl})" if lvl else ""),
                "# " + self._simc_line(key, item)]

    def to_simc(self):
        """把当前这套装备导出成 simc 字符串，能直接粘进 Raidbots / SimulationCraft。

        做法是逐行重写原文而不是从零拼：头部那些 region/server/talents/custom
        字段解析器本来就不认识，原样搬运最保险；只有换过的部位才用新数据生成。
        从零拼的话，粘进 Raidbots 会发现"名字和服务器不认识"，模拟直接报错。

        **注释行必须原样保留**。插件导出开头是
            # SimC Addon 12.1.5-03
            # WoW 12.1.5.70575, TOC 120105
        结尾还有 `# Checksum: ...`。Raidbots 的「Load from SimC Addon」靠这几行
        判断"这是插件导出的、版本够新"，把它们删掉就会直接甩
        "Unverified Input / input is not valid"——装备本身一行都没错，但整段被拒。
        早先这里 `continue` 掉了所有 `#` 开头的行，正好踩中。
        """
        out, done, in_bags = [], set(), False
        last_comment = None      # 最近一条注释在 out 里的下标，换装备时要跟着改
        has_checksum = False
        orig_checksum = None     # 插件原来那行的值，一件没动时原样带回去
        edited = False           # 有没有真的改过东西（决定 checksum 怎么处理）
        bag_idx = info_idx = None   # 背包段起止，换过的装备要补进这段注释里
        for raw in (self.raw_text or "").splitlines():
            line = raw.strip()
            # 注释、空行、背包段：一律原样搬。漏一条注释就可能过不了 Raidbots 的校验
            if not line:
                out.append(raw)
                continue
            if line.startswith("#"):
                # 插件在结尾写 `# Checksum: <adler32>`，校验的是**这一行之前**的整段文本。
                # 改过装备之后原文的校验值必然对不上，所以先摘掉，最后按新内容重算。
                if line.startswith("# Checksum:"):
                    has_checksum = True
                    orig_checksum = line.split(":", 1)[1].strip()
                    continue
                if "Gear from Bags" in line:
                    in_bags = True
                    bag_idx = len(out)
                elif line.startswith("### Additional Character Info"):
                    info_idx = len(out)
                out.append(raw)
                last_comment = len(out) - 1
                continue
            if in_bags:
                # 背包里的东西不是身上穿的，不参与计算，也没必要改写
                out.append(raw)
                continue
            key, sep, _ = line.partition("=")
            if sep and key.strip() in self.SIMC_ITEM_SLOTS:
                k = key.strip()
                if k in done:
                    continue
                done.add(k)
                item = self.prof.items.get(k)
                if not item:
                    continue
                # 没换过的部位原样搬运。重新生成会丢字段（redirected_base_stats /
                # content_tuning / crafting_quality），套装转化件丢了源装备 id
                # 属性就全错了；而且玩家在界面上选过的自选属性会覆盖掉原文的顺序。
                if self._changed(k):
                    edited = True
                    out.append(self._simc_line(k, item))
                    # 上面那行 `# 中文名 (装等)` 写的是换之前的装备，跟着改掉——
                    # 不改的话导回去界面上显示的还是旧名字（注释优先于库里的名字）
                    if last_comment is not None and _NAME_ILEVEL.match(out[last_comment]):
                        lvl = item.comment_ilevel or item.ilevel
                        label = self._item_label(item)
                        out[last_comment] = f"# {label}" + (f" ({lvl})" if lvl else "")
                elif self._customized(k):
                    # 只动了宝石/附魔：保留原行，只替换这两段
                    edited = True
                    out.append(self._patch_line(line, item))
                else:
                    out.append(line)
            else:
                out.append(line)
        # 原文里没有、但现在身上有的部位（理论上不该出现，兜底）
        for k, item in self.prof.items.items():
            if k not in done and item.db_slot not in ("shirt", "tabard"):
                edited = True
                out.append(self._simc_line(k, item))

        # 换过的部位要补进背包注释。
        #
        # 为什么非补不可：`### Gear from Bags` 是 Raidbots 的"可选装备池"，
        # Top Gear 靠它枚举组合。我们换上的那件游戏里并不存在（是这里假设的），
        # 而原来那件被换掉了、也不在身上——两头都不在这段里，Top Gear 就一件
        # 都看不见，只能跑当前这一套，"原 vs 新"根本比不了。
        #
        # 补进去之后这两件都能在 Top Gear 里勾选，对比直接出结果。
        # 整段是注释，simc 解析时会跳过，最坏情况也只是被 Raidbots 忽略。
        if info_idx is not None and edited:
            bag_text = "\n".join(out[bag_idx:info_idx]) if bag_idx is not None else ""
            seen = set(re.findall(r"\bid=(\d+)", bag_text))
            extra = []
            for k, cur in self.prof.items.items():
                if not self._changed(k):
                    continue
                for it in (cur, self.original.get(k)):
                    if it is not None:
                        extra.extend(self._bag_notes(k, it, seen))
            if extra:
                out[info_idx:info_idx] = extra + ["#"]

        body = "\n".join(out)
        if not has_checksum:
            return body + "\n"
        # 插件 core.lua 的写法：
        #     local checksum = adler32(simulationcraftProfile)
        #     profile = profile .. '# Checksum: ' .. string.format('%x', checksum)
        # 校验范围就是 `# Checksum:` 之前的**全部字节**（含结尾那串空行）。
        #
        # 一件没改：把原来那个值原样还回去，导出的文本和插件输出一字不差，
        #           Raidbots 才会认这是"插件原始输出"。
        # 改过装备：**不再带 checksum**。这个值的作用是给"未经改动"背书，
        #           改完装备它还带着就是一张假凭证；重算一个同样是在冒充，
        #           而且无从验证。索性去掉，让 Raidbots 按普通 profile 处理
        #           （Quick Sim 就是这条路，接受任意合法 profile）。
        payload = body + "\n"
        if not edited and orig_checksum:
            return f"{payload}# Checksum: {orig_checksum}"
        return payload

    def snapshot(self):
        """整份状态。前端靠它渲染，不用自己维护装备数据。"""
        det = {d["slot"]: d for d in (self.detail or [])}
        slots = []
        for key, item in self.prof.items.items():
            if item.db_slot in ("shirt", "tabard"):
                continue
            # 先定装等再算属性：simc 注释里的装等才是真值，item.ilevel 可能是裸装等
            lvl = item.comment_ilevel or item.ilevel
            entry = self.item_payload(item.item_id, lvl, item.crafted_stats,
                                      item.extra.get("redirected_base_stats")) or {}
            entry["slot_key"] = key
            entry["slot_cn"] = SLOT_LABELS.get(item.db_slot, (item.db_slot, 0))[0]
            if key[-1].isdigit():
                entry["slot_cn"] += key[-1]
            entry["display_name"] = item.comment_name or entry.get("name") or item.name
            entry["ilevel"] = lvl
            entry["changed"] = self._changed(key)

            # 宝石和附魔单独给字段，前端要分开展示：
            # 宝石是"插了什么"，附魔是"附了什么"，而且附魔里有一类是百分比特效
            # （暴击效果 +1%），不能混进绿字里当点数显示。
            d = det.get(key) or {}
            entry["gems"] = d.get("gems") or {}
            entry["gem_ids"] = list(item.gem_ids)
            entry["sockets"] = self.socket_count(item.item_id, item.socket_count,
                                                 item.gem_ids, item.drilled)
            # 打孔器：这件装备上还能不能再开一个孔，前端据此决定要不要显示开孔按钮
            entry["drilled"] = bool(item.drilled)
            entry["can_drill"] = self.can_drill(item.db_slot, item.drilled)
            entry["gem_missing"] = d.get("gem_missing") or []
            entry["enchant_id"] = item.enchant_id
            entry["enchant"] = d.get("enchant") or {}
            entry["enchant_tertiary"] = d.get("enchant_tertiary") or {}
            entry["enchant_note"] = _note_cn(d.get("enchant_note") or "")
            entry["enchant_known"] = d.get("enchant_known", True)
            slots.append(entry)

        order = {k: i for i, k in enumerate(
            ["head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist",
             "legs", "feet", "finger1", "finger2", "trinket1", "trinket2",
             "main_hand", "off_hand"])}
        slots.sort(key=lambda s: order.get(s["slot_key"], 99))

        pending = [s["slot_key"] for s in slots
                   if s.get("craft_kind") and s["id"] not in self.choices]
        avg, filled = self.avg_ilevel()
        base_avg, _ = self.avg_ilevel(self.original)
        return {
            "class_key": self.prof.class_key,
            "class_cn": self.prof.class_cn,
            "spec": self.prof.spec,
            "spec_cn": SPEC_CN.get(self.prof.spec, self.prof.spec or ""),
            "level": self.prof.level,
            "name": self.prof.char_name,
            "armor": self.armor_types,
            "armor_cn": self.armor_cn,
            "slots": slots,
            "avg_ilevel": avg,
            "avg_ilevel_base": base_avg,
            "avg_filled": filled,
            "totals": {k: round(v, 1) for k, v in self.totals.items()},
            "pending": pending,
            "changed_slots": [s["slot_key"] for s in slots if s["changed"]],
            "can_undo": bool(self.history),
            "ilevel_options": self.ilevel_options(),
            "panel": self.panel,
            # 面板里填的百分比（可选），前端回填输入框用
            "panel_pct": self.panel_pct,
            # base 来源：panel（玩家填的，含天赋）/ class（职业基础，未含天赋）/ 空
            "base_source": self.base_source,
            # 面板基准按专精分开存，前端要能说清"这套是哪个专精的面板"
            "panel_key": self.panel_key,
            "panel_specs": [_panel_label(k) for k in sorted(load_panels())
                            if k != "__legacy__"],
            "base": {k: round(v, 1) for k, v in self.base.items()},
            "gear_totals": {k: round(v, 1) for k, v in self.gear_totals.items()},
            "rating_per_pct": self.rating_per_pct,
            # 按递减档位算好的真实百分比；前端优先用这个，不再自己除以常数
            "pct": self._pct_all(),
            # 百分比里有多少是「装备之外」的固定加成（天赋/被动），
            # 以及它从哪来。查表说明是这个专精的默认值，面板反推才是精确到人。
            "flat": {k: round(v, 2) for k, v in self._flat.items()},
            "flat_source": self.flat_source(),
            "warnings": self.prof.warnings,
            "stat_labels": STAT_CN,
            "show_order": [c for c in SHOW_ORDER if c in self.totals
                           or c in ("crit", "haste", "mastery", "vers")],
        }

    def _changed(self, slot_key):
        cur = self.prof.items.get(slot_key)
        orig = self.original.get(slot_key)
        if cur is None or orig is None:
            return cur is not orig
        return cur.item_id != orig.item_id
