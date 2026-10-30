# src/wowgear — 核心层

数据与规则，**不依赖任何上层**。这一层只回答「有什么、是多少」，不关心角色穿了什么、
能不能撤销——那些是 `wowlib` 的事。

## 铁律

- **不允许 import `wowlib` 或 `server`**。出现 `import simc` 就说明分层破了，会直接
  ImportError——这是刻意的，用报错把边界钉死。
- 所有路径走 `paths.py`，不自己拼 `../data/...`。路径散落十几个文件时，哪天挪目录
  就要全改，而且改漏了要到运行时报错才发现。

  这条是有代价换来的：`src/` 从扁平改成按层分目录时，`session` / `calibrate` / `simc` /
  `stat_picker` / `serve` 五个文件各自用 `dirname(dirname(__file__))` 拼了一遍路径，
  一挪**全部算错一层**，而且不会报错——它会安静地指向一个不存在的目录。现在统一
  `from wowgear.paths import DB_PATH`。

## 文件

| 文件 | 职责 | 关键点 |
|---|---|---|
| `paths.py` | 所有路径的唯一来源 | 其它模块一律 `from .paths import DB_PATH` |
| `const.py` | 职业/槽位/属性/中文名等常量表 | `CLASSES`（职业→护甲→主属性）、`SLOT_LABELS`、`STAT_CN` |
| `curve.py` | 属性曲线：装等→主属性/耐力/绿字系数 | 三条曲线分开；jewel 部位独立一档 |
| `stats.py` | 单件装备换算成数值 | alloc×系数×1e-4；三桶各走一条曲线 |
| `source.py` | 装备从哪来（副本/boss/专业/套装） | 优先挑"instance+encounter 都是具体名"的，聚合桶兜底 |
| `store.py` | 玩家自己填的配置（自选属性、面板基准） | 读/写 `player_crafted.json`、`panel.json`；面板按 `职业:专精` 分存 |
| `accounts.py` | 账号/登录令牌/simc 历史/配装存档 | 独立 `accounts.db`，PBKDF2 加盐 |

## 面板为什么按「职业:专精」分存

`panel.json` 的键是 `hunter:beast_mastery` 这种形式（`store.panel_key()`），不是一个全局字典。

理由是踩出来的：同一个职业换专精，主属性和天赋都会变（圣骑神圣吃智力、防护吃力量），
一份全局面板会让另一个专精的 base 全错——而且错得很隐蔽，数字看起来很正常。

`load_panel()` 还负责**老格式迁移**：早期版本只有一份扁平结构，第一次按专精读取时
认领到该专精名下并立刻写回新格式，别的专精不会跟着套用。

## 为什么 `catalog.py` 不在这里

`catalog.py`（装备目录）原来在 `wowgear` 里，但它要判断"职业能不能穿"，得用 `wowlib/simc`
里的 `class_primary`——数据层反过来依赖解析层。所以挪到 `wowlib`，分层才真正成立。
