"""解析 simc 导出的 `talents=` 字符串（暴雪的天赋导出格式）。

格式（来自 Interface/AddOns/Blizzard_PlayerSpells/ClassTalents/Blizzard_ClassTalentImportExport.lua，
SimC 的 `parse_traits_hash` 是同一套实现）：

    6 bit / 字符，字符表是标准 base64
    ├─ 8 bit   序列化版本（现在是 2）
    ├─ 16 bit  专精 id
    ├─ 128 bit 树哈希（用来校验，解析时跳过）
    └─ 每个天赋节点按 **node_id 升序**：
         1 bit  是否选中
         选中后 1 bit  是否已购买（0 = 系统赠送，固定 1 级）
         已购买后 1 bit 是否部分点满
           部分点满 → 6 bit 级数
         1 bit  是否选择节点（二选一）
           是 → 2 bit 选项下标

所以**节点顺序必须和游戏一致**（按 id 升序），否则后面全错位。
节点表从 SimC 的 `trait_data.inc` 抽，字段顺序见 `trait_data.hpp` 的 `trait_data_t`。
"""

import io
import json
import os
import re

BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
VERSION = 2
VERSION_BITS, SPEC_BITS, TREE_BITS = 8, 16, 128
RANK_BITS, CHOICE_BITS = 6, 2

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TALENTS_JSON = os.path.join(ROOT, "data", "raw", "talents.json")


class Bits:
    """按 6 bit 一个字符读取的位流。"""

    def __init__(self, text):
        self.text = text
        self.head = 0

    def get(self, n):
        val = 0
        for i in range(n):
            idx, bit = divmod(self.head, 6)
            self.head += 1
            ch = BASE64.index(self.text[idx]) if idx < len(self.text) else 0
            val |= ((ch >> bit) & 1) << i
        return val


def load_table():
    """节点表：{class_id: {node_id: [entry, ...]}}。没有就返回空。"""
    if not os.path.exists(TALENTS_JSON):
        return {}
    with io.open(TALENTS_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): {int(nid): v for nid, v in nodes.items()}
            for k, nodes in (raw.get("classes") or {}).items()}


def decode(text, class_id, table=None, spec_id=None):
    """解一条天赋字符串，返回 (spec_id, [选中项])。

    选中项：{node, name, spell, rank, max_ranks, tree, spec}
    """
    table = load_table() if table is None else table
    nodes = (table or {}).get(int(class_id)) or {}
    if not nodes:
        raise ValueError(f"没有职业 {class_id} 的天赋节点表")

    b = Bits(text)
    version = b.get(VERSION_BITS)
    if version != VERSION:
        raise ValueError(f"天赋字符串版本 {version}，只认识 {VERSION}")
    spec = b.get(SPEC_BITS)
    if spec_id is not None and spec != spec_id:
        raise ValueError(f"字符串里的专精 {spec} 和角色专精 {spec_id} 不一致")
    b.get(TREE_BITS)                      # 树哈希，用不上

    picked = []
    for node_id in sorted(nodes):
        if not b.get(1):
            continue
        entries = nodes[node_id]
        trait = entries[0]
        rank = trait["max_ranks"]
        if b.get(1):                      # 已购买
            if b.get(1):                  # 部分点满
                rank = b.get(RANK_BITS)
            if b.get(1):                  # 选择节点
                idx = b.get(CHOICE_BITS)
                if idx < len(entries):
                    trait = entries[idx]
        picked.append({
            "node": node_id,
            "name": trait["name"],
            "spell": trait["spell"],
            "rank": rank,
            "max_ranks": trait["max_ranks"],
            "tree": trait["tree"],
            "spec": trait["spec"],
            "node_type": trait["node_type"],
        })
    return spec, picked


def stat_bonuses(picked, effects):
    """从选中项里挑出"直接加绿字"的天赋。

    effects 是 {spell_id: [(aura, misc, value), ...]}，由抓取脚本填。
    这里只认 108 号光环（改战斗评级）和 29/189 这类纯属性加成，
    具体见 fetch_talents.py 里的说明。
    """
    out = []
    for t in picked:
        for aura, misc, value in effects.get(t["spell"], []):
            if aura == 108 and misc in STAT_BY_MISC and value:
                out.append({"name": t["name"], "stat": STAT_BY_MISC[misc],
                            "value": value, "rank": t["rank"]})
    return out


# 108 号光环的 EffectMiscValue 对应哪条绿字（客户端 ItemModType）
STAT_BY_MISC = {32: "crit", 36: "haste", 49: "mastery", 40: "vers", 31: "hit", 30: "hit"}
