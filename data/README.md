# data — 数据与资源

运行时和构建期用到的所有落地数据。**除了数据库，这里的东西都算"原料/产物"**，
可以删了重新生成，唯独 `accounts.db` 不行。

## 文件

| 路径 | 性质 | 说明 | 可否重建 |
|---|---|---|---|
| `wowgear.db` | 产物 | 游戏数据，由 `build_db.py` 生成 | ✅ 可 `--build` 重建 |
| `accounts.db` | 用户数据 | 账号/历史/配装存档 | ❌ **不可重建** |
| `raw/` | 原料 | Raidbots 静态数据 + 图标索引 + 抓取结果 | ✅ 重下 |
| `icons/` | 产物 | 装备图标，本地缓存 | ✅ 重下 |
| `icons/gems/` | 产物 | 宝石图标 | ✅ 重下 |
| `icons/enchants/` | 产物 | 附魔图标 | ✅ 重下 |
| `stat_curve.csv` | 原料 | 属性曲线采样表，`sample_curve.py` 生成 | ✅ 重采 |
| `versions.json` | 元数据 | 每个数据集对应哪个游戏版本（`--check-update` 用） | ✅ 会自己重写 |
| `panel.json` | 用户配置 | 玩家填的面板基准，按「职业:专精」分存 | ⚠️ 重填 |
| `player_crafted.json` | 用户配置 | 玩家确认过的自选属性 | ⚠️ 重填 |

## `raw/` 里有什么

| 文件 | 来源 | 大小 | 用途 |
|---|---|---|---|
| `equippable-items.json` | Raidbots 静态数据 | 53MB | 装备主数据（11 万件） |
| `bonuses.json` / `bonus-upgrade-sets.json` | Raidbots | — | 属性加成、升级轨道 |
| `seasons.json` / `instances.json` / `item-sets.json` / `currency-types.json` | Raidbots | — | 赛季、副本、套装、纹章 |
| `icon-index.csv` / `icon-paths.txt` | wow-listfile | 1.4MB | 图标名→FileDataID |
| `gems.json` / `enchants.json` | 自抓（wowhead，腿部为系数推算） | — | 宝石/附魔数值 + 说明 |
| `names_zhcn.json` | 自抽（ItemSparse zhCN） | — | 装备/宝石/附魔的中文名 + 宝石装等 |
| `sockets.json` | 自抓（wowhead） | — | 装备插槽数 |
| `class_base.json` | 自抽（SimC 生成的 gt 表） | — | 每职业每级裸装基础属性 + 种族修正，含 `_meta.scale` |
| `spec_mastery.json` | 人工整理（客户端法术表） | — | 每专精的精通系数（每 1 点给多少 %） |
| `rating_curves.json` | Maxroll 12.0.1 | — | 绿字换算率 + 递减档位；**不含全局基础百分比** |
| `spec_stats.json` | 真实游戏面板反推 | — | 每专精「装备之外」的固定绿字百分比（天赋 + 被动），见下 |
| `talents.json` | 自抽（SimC trait_data.inc） | 755KB | 天赋节点表。**没有效果表**，见下 |
| `permanent-enchant.inc` | SimC 源码 | — | 附魔 id→名字映射 |
| `rand-prop-points.csv` | SimC | — | 属性预算参考 |
| `.item-sparse.csv` | wago.tools | — | 物品稀疏表（抓宝石附魔用） |
| `.item-sparse-zhCN.csv` / `-enUS.csv` | wago.tools | 各 48MB | 物品稀疏表的两种语言版本（抽中文名 / 按英文名对附魔卷轴用，用完可删） |

### `talents.json` 只有一半

它只有 `classes`（每个节点点了给什么），**没有 `effects`**（这个法术给多少属性）。
所以 `wowlib/talents.py` 能解出"点了哪些天赋"，但算不出加成，目前没接进计算。

留着是因为解码器本身是对的，找到效果数据源就能接上。详见 `docs/技术路线.md` 的「已知边界」。

### `spec_stats.json` 是角色观测值，不是客户端公式

这张表存的是**每个专精「装备之外」的固定绿字百分比**（猎人暴击 +15、守护德全能 +12 那种）。
和 `rating_curves.json` 性质完全不同：

- `rating_curves.json` 是**公式数据**，从客户端/公开资料拿，对所有角色成立
- `spec_stats.json` 是**观测值**，从真实游戏面板反推，只对采样时的那个版本成立

所以文件里记了 `build`。换赛季重建库时 `build_db.py` 会比对，不一致就打警告——
天赋改版会直接让这些数失效，**必须重采**。

采法（`基线 = 面板% − 递减后评级 / 分母`）和判定可信的门槛（同一专精换装备后基线不变）
都写在文件头的 `_note` / `_source` 里。每条还带 `confidence`：`high` 是有两套装备交叉验证过的，
`low` 是单人单次采样，可能被临时 buff 污染。

目前只有 5 个专精有数据。没数据的专精在界面上会如实标注"未含天赋"，
不会假装算全了——这比静默偏 10 个点要好。


## 两个"用户配置"为什么是 JSON 而不是进数据库

`panel.json` 和 `player_crafted.json` 是玩家在本机填的东西，和"游戏数据"性质不同：
游戏数据会随赛季整库重建，混进去会被冲掉。而且它们是纯文本，方便玩家自己改、也方便
版本管理里 diff。

## 空间（`main.py --size` 会打印）

`raw/` 占绝大部分体积（57MB），**建完库其实用不到了**——但删了下次 `--build` 要重下。
日常使用只保留 `wowgear.db` + `accounts.db` + `icons/` 就够了。
