"""装备目录：一件装备长什么样、某个部位能换哪些。

这一层只回答"有什么、数值多少"，不关心角色穿了什么、能不能撤销——
那些是 session 的事。所以它拿 conn + prof 就能独立跑（命令行脚本、
以后的批量导出、离线分析都能直接用）。
"""

from wowlib.simc import class_primary, HYBRID_PRIMARY
from wowlib.stat_picker import slots_of
from wowgear.const import CLASSES, ARMOR_CN, ARMOR_SLOTS, CLASS_IDS, SIMC_SLOTS

from wowgear import stats as stat_calc
from wowgear.const import KIND_CN, STAT_CN
from wowgear.source import source_of


class Catalog:
    def __init__(self, conn, prof):
        self.conn = conn
        self.prof = prof

    # ---------------------------------------------------------------- 角色

    @property
    def armor_types(self):
        entry = CLASSES.get(self.prof.class_key)
        return entry[1] if entry else []

    @property
    def armor_cn(self):
        return "/".join(ARMOR_CN.get(a, a) for a in self.armor_types) or "不限"

    # ---------------------------------------------------------------- 单件

    def payload(self, item_id, ilevel=None, crafted_stats=(), stat_src=None):
        """给前端渲染用的装备信息（图标、来源、属性分配、按装等算好的数值）。

        stat_src：属性从哪件装备取。套装转化件（catalyst）自己没有固定绿字，
        转化出来是什么属性完全取决于你拿哪件去转——simc 用 redirected_base_stats
        记着源装备。不按源取的话，界面会显示套装模板的绿字（急速 4993/精通 2007），
        而身上实际是源装备的（暴击 2750/精通 4250），差了整整一个属性的量。
        """
        row = self.conn.execute("""
            SELECT id, name, name_cn, icon, quality, category, slot,
                   armor_type, primary_stat, craft_kind, expansion, has_sockets
              FROM items WHERE id = ?
        """, (item_id,)).fetchone()
        if not row:
            return None

        src_id = stat_src or item_id
        stats = [dict(code=r["code"], label=r["label_cn"], alloc=r["alloc"],
                      optional=bool(r["is_crafted_optional"]))
                 for r in self.conn.execute("""
                    SELECT t.code, t.label_cn, s.alloc, s.is_crafted_optional
                      FROM item_stats s JOIN stat_types t ON t.id = s.stat_id
                     WHERE s.item_id = ? ORDER BY t.kind, t.id
                 """, (src_id,))]

        # 多形态主属性（"敏捷/智力"）要收敛到本职业实际吃的那条，
        # 否则猎人看到胸甲写着这两条，也不知道哪条算数
        want = class_primary(self.prof.class_key, self.prof.spec)
        if want:
            for s in stats:
                if s["code"] in HYBRID_PRIMARY and want in s["code"].split("_"):
                    s["code"] = want
                    s["label"] = STAT_CN[want]

        # 自选槽（制造业/装绑）在库里只有占位 code：bonus1 / bonus2。
        # 玩家指定过属性就换成真实属性——数值算的是 vers/mastery，stats 里却还写着
        # bonus1，前端按 code 一查就查不到，整件装备看起来"只有耐力敏捷、没有绿字"。
        slot_no = 0
        for s in stats:
            if not s["optional"]:
                continue
            if slot_no < len(crafted_stats):
                code = stat_calc.STAT_ID.get(crafted_stats[slot_no])
                if code:
                    s["code"] = code
                    s["label"] = STAT_CN.get(code, code)
            slot_no += 1

        slots = slots_of(self.conn, item_id)
        # 还没选属性的自选槽，把每槽能给多少点算出来，卡片上写「自选 125 × 2」，
        # 只写"待指定"的话用户没法判断值不值得换
        opt_each = []
        if not crafted_stats and ilevel:
            opt_each = stat_calc.optional_each(row["slot"], ilevel, stats)

        return {
            "id": row["id"],
            "name": row["name_cn"] or row["name"],
            "name_en": row["name"],
            "icon": row["icon"],
            "quality": row["quality"],
            "category": row["category"],
            "slot": row["slot"],
            "armor": row["armor_type"],
            "armor_cn": ARMOR_CN.get(row["armor_type"], ""),
            "primary": row["primary_stat"],
            "craft_kind": row["craft_kind"],
            "kind_cn": KIND_CN.get(row["craft_kind"], ""),
            "expansion": row["expansion"],
            # 能插几颗宝石。0 表示这件装备没有孔，界面上就别给空孔位了——
            # 以前不管什么部位都画一个"空孔"，头盔武器也能点进去插宝石。
            "sockets": row["has_sockets"] or 0,
            "is_reprint": row["expansion"] not in (None, 11),
            "source": source_of(self.conn, item_id, row["category"]),
            "variants": [],
            "variant_count": 1,
            "ilevel": ilevel,
            "crafted_stats": list(crafted_stats),
            "optional_each": opt_each,
            "slot_shares": [s[1] for s in slots],
            "stats": stats,
            "stat_from": src_id if src_id != item_id else None,
            "stat_from_name": self._name_of(src_id) if src_id != item_id else "",
            "stat_values": stat_calc.item_values(row["slot"], ilevel, stats,
                                                 crafted_stats),
        }

    def _name_of(self, item_id):
        row = self.conn.execute("SELECT name FROM items WHERE id = ?",
                                (item_id,)).fetchone()
        return row["name"] if row else ""

    # ---------------------------------------------------------------- 候选

    def candidates(self, slot_key, ilevel=None):
        """这个部位、这个职业能穿的全部装备，按获取难度排序。

        ilevel 是用户选定的装等，候选卡的数值按它算——同一件选 334 和 344
        差别很大，不给定装等就只能显示权重，用户没法比较。没指定时用当前
        部位身上的装等。

        两个去重动作，都是因为界面上一眼看过去"同一件出现了好几遍"：
        1. 套装转化件按职业过滤。唤魔师/萨满/猎人都是锁甲，套装却是三套不同的，
           不过滤的话锁甲角色会看到三个"头部 套装转化"，属性还不一样。
        2. 制造业同属性合并。工程/制皮/裁缝各做一款，属性分配一模一样，
           对"算属性"完全等价。合并成一张卡，角标写 ×N。
        团本/M+ 不合并——那是不同 boss 掉的不同东西，合并了就丢掉"去哪刷"。
        """
        db_slot = SIMC_SLOTS.get(slot_key, slot_key)
        armor = self.armor_types
        prim = CLASSES[self.prof.class_key][2]
        exclude = ("pvp-conquest", "pvp-honor", "pvp-world", "dungeon_normal", "other")

        where = ["i.is_current = 1", "i.slot = ?",
                 f"i.category NOT IN ({','.join('?' * len(exclude))})"]
        args = [db_slot, *exclude]
        if armor and db_slot in ARMOR_SLOTS:
            where.append(f"(i.armor_type IS NULL OR i.armor_type IN "
                         f"({','.join('?' * len(armor))}))")
            args.extend(armor)
        if prim:
            where.append(f"(i.primary_stat IS NULL OR i.primary_stat IN "
                         f"({','.join('?' * len(prim))}))")
            args.extend(prim)
        cid = CLASS_IDS.get(self.prof.class_key)
        if cid:
            # classes 存的是 JSON 数组文本（"[13]"）。套个逗号再 LIKE '%,13,%'，
            # 否则职业 1 会匹配上 11/12/13。
            where.append("(i.classes IS NULL OR (',' || REPLACE(REPLACE("
                         "REPLACE(i.classes,'[',''),']',''),' ','') || ',') LIKE ?)")
            args.append(f"%,{cid},%")

        sql = f"""
            SELECT i.id FROM items i WHERE {' AND '.join(where)}
             ORDER BY i.quality DESC,
                      CASE i.category
                        WHEN 'raid' THEN 0 WHEN 'dungeon' THEN 1 WHEN 'delve' THEN 2
                        WHEN 'catalyst' THEN 3 WHEN 'profession' THEN 4 ELSE 5 END,
                      i.name
        """
        rows = self.conn.execute(sql, args).fetchall()

        default_ilevel = ilevel
        if default_ilevel is None:
            cur = self.prof.items.get(slot_key)
            if cur:
                default_ilevel = cur.comment_ilevel or cur.ilevel

        out, merged = [], {}
        for r in rows:
            payload = self.payload(r["id"], default_ilevel)
            if not payload:
                continue
            payload["slot_key"] = slot_key
            payload["default_ilevel"] = default_ilevel
            if payload["category"] == "profession":
                key = self._craft_key(payload)
                prev = merged.get(key)
                if prev is not None:
                    prev["variants"].append(payload["name"])
                    prev["variant_count"] = len(prev["variants"]) + 1
                    continue
                merged[key] = payload
            out.append(payload)
        return out

    @staticmethod
    def _craft_key(p):
        """制造业同款的判定键：主属性 + 全部属性分配（含自选槽占比）。

        不含品质：紫色工程头和蓝色制皮头做出来都是同一装等、耐力+白板+自选槽
        一模一样，品质只决定材料。分组时保留排序第一的（品质最高），其余进 variants。
        单槽 7000 和双槽 3500+3500 键不同——能自选的属性个数不一样，是两回事。
        """
        return (p["primary"],
                tuple(sorted((s["code"], s["alloc"], s["optional"])
                             for s in p["stats"])))
