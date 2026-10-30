"""跨脚本共享的魔兽常量（唯一来源）。

放在包里，是为了让命令行、Web 服务、构建脚本用同一份数据。
旧的 `src/wowconst.py` 现在只是转出口，新代码一律从这里拿。
"""

# 职业 -> (中文名, 可穿护甲, 可用主属性)
# 护甲为 None 表示不限（戒指/项链/饰品/披风）
CLASSES = {
    "warrior":     ("战士",     ["plate"],   ["str", "str_int", "any"]),
    "paladin":     ("圣骑士",   ["plate"],   ["str", "int", "str_int", "any"]),
    "deathknight": ("死亡骑士", ["plate"],   ["str", "str_int", "any"]),
    "hunter":      ("猎人",     ["mail"],    ["agi", "agi_int", "any"]),
    "shaman":      ("萨满",     ["mail"],    ["agi", "int", "agi_int", "any"]),
    "evoker":      ("唤魔师",   ["mail"],    ["int", "agi_int", "any"]),
    "rogue":       ("潜行者",   ["leather"], ["agi", "agi_int", "any"]),
    "druid":       ("德鲁伊",   ["leather"], ["agi", "int", "agi_int", "any"]),
    "monk":        ("武僧",     ["leather"], ["agi", "int", "agi_int", "any"]),
    "demonhunter": ("恶魔猎手", ["leather"], ["agi", "agi_int", "any"]),
    "mage":        ("法师",     ["cloth"],   ["int", "any"]),
    "priest":      ("牧师",     ["cloth"],   ["int", "any"]),
    "warlock":     ("术士",     ["cloth"],   ["int", "any"]),
}

ARMOR_CN = {"cloth": "布甲", "leather": "皮甲", "mail": "锁甲", "plate": "板甲"}

# 只有这些部位分护甲类型。披风在数据里被标成布/皮/锁/板，但实际人人能穿——
# 按护甲过滤会让锁甲角色一件披风候选都看不到（背部候选直接是 0 件）。
ARMOR_SLOTS = {"head", "shoulder", "chest", "wrist", "hands", "waist", "legs", "feet"}

# 职业 -> 游戏内 classID。数据里 items.classes 存的是这个 ID 的 JSON 数组，
# 套装转化件靠它区分职业：唤魔师/萨满/猎人都是锁甲，但套装是三套不同的。
CLASS_IDS = {
    "warrior": 1, "paladin": 2, "hunter": 3, "rogue": 4, "priest": 5,
    "deathknight": 6, "shaman": 7, "mage": 8, "warlock": 9, "monk": 10,
    "druid": 11, "demonhunter": 12, "evoker": 13,
}

# 专业英文名 -> 中文。制造业装备的来源就是专业，界面直接显示英文太生硬
PROF_CN = {
    "Blacksmithing": "锻造", "Leatherworking": "制皮", "Tailoring": "裁缝",
    "Engineering": "工程学", "Inscription": "铭文", "Jewelcrafting": "珠宝加工",
    "Enchanting": "附魔", "Alchemy": "炼金术", "Herbalism": "草药学",
    "Mining": "采矿", "Skinning": "剥皮", "Cooking": "烹饪",
}

# 专精 -> 中文名。simc 里写的是 beast_mastery 这种键，界面直接显示英文不友好
SPEC_CN = {
    "arms": "武器", "fury": "狂怒", "protection": "防护",
    "holy": "神圣", "retribution": "惩戒",
    "beast_mastery": "野兽控制", "marksmanship": "射击", "survival": "生存",
    "assassination": "刺杀", "outlaw": "狂徒", "subtlety": "敏锐",
    "discipline": "戒律", "shadow": "暗影",
    "elemental": "元素", "enhancement": "增强", "restoration": "恢复",
    "arcane": "奥术", "fire": "火焰", "frost": "冰霜",
    "affliction": "痛苦", "demonology": "恶魔学识", "destruction": "毁灭",
    "balance": "平衡", "feral": "野性", "guardian": "守护",
    "brewmaster": "酒仙", "mistweaver": "织雾", "windwalker": "踏风",
    "vengeance": "复仇", "havoc": "浩劫",
    "blood": "鲜血", "unholy": "邪恶",
    "devastation": "湮灭", "preservation": "保存", "augmentation": "增辉",
}

SLOT_LABELS = {
    "head": ("头部", 1), "neck": ("颈部", 2), "shoulder": ("肩部", 3),
    "back": ("背部", 4), "chest": ("胸部", 5), "wrist": ("手腕", 6),
    "hands": ("手部", 7), "waist": ("腰部", 8), "legs": ("腿部", 9),
    "feet": ("脚部", 10), "finger": ("戒指", 11), "trinket": ("饰品", 12),
    "main_hand": ("主手", 13), "off_hand": ("副手", 14), "ranged": ("远程", 15),
    "shirt": ("衬衣", 90), "tabard": ("战袍", 91),
}

# simc 的槽位名 -> 库里的槽位名
# 戒指和饰品在 simc 里分 1/2，库里统一成一个类别
SIMC_SLOTS = {
    "head": "head", "neck": "neck", "shoulder": "shoulder", "back": "back",
    "chest": "chest", "wrist": "wrist", "hands": "hands", "waist": "waist",
    "legs": "legs", "feet": "feet",
    "finger1": "finger", "finger2": "finger",
    "trinket1": "trinket", "trinket2": "trinket",
    "main_hand": "main_hand", "off_hand": "off_hand",
    "shirt": "shirt", "tabard": "tabard",
}

# 装备栏位的标准展示顺序（simc 键）
SIMC_SLOT_ORDER = [
    "head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist",
    "legs", "feet", "finger1", "finger2", "trinket1", "trinket2",
    "main_hand", "off_hand",
]


# ── 展示用：中文名与顺序 ────────────────────────────────────

STAT_CN = {"agi": "敏捷", "str": "力量", "int": "智力", "sta": "耐力",
           "crit": "暴击", "haste": "急速", "mastery": "精通", "vers": "全能"}

# 属性条和卡片的固定顺序：主属性 → 耐力 → 四维绿字
STAT_ORDER = ["agi", "str", "int", "sta", "crit", "haste", "mastery", "vers"]

KIND_CN = {"crafted": "制造业", "boe": "装绑"}

QUALITY_CN = {0: "粗糙", 1: "普通", 2: "优秀", 3: "精良", 4: "史诗", 5: "传说", 6: "神器"}

TRACK_CN = {"Adventurer": "冒险", "Veteran": "老兵", "Champion": "勇士",
            "Hero": "英雄", "Myth": "神话"}

# 升级轨道之外的装等。341 = 神话满级再加 7（额外升级），344 = 神话尾王专属。
# 两个都不在 ilevel_tracks 里，但属性曲线覆盖到这些档位，能直接算。
EXTRA_ILEVELS = [(341, "额外升级 +7"), (344, "神话尾王")]
