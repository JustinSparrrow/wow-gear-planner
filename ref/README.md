# ref — SimulationCraft 源码参考

**这里不存源码，只记"去上游哪个文件看"。**

核心算法（属性曲线、装等轨道、套装转化、装备行解析）都是对照 SimC 源码逆向出来的。
以前把几个文件摘下来放在这个目录，但 SimC 是 **GPL-3.0**，整份复制进仓库会让本项目的
许可变得麻烦，所以只留位置。

```bash
git clone --depth 1 https://github.com/simulationcraft/simc
```

（国内拉不动可以在 URL 前加 `https://ghfast.top/` 反代。）

| 要查的东西 | 上游文件 | 什么时候看 |
|---|---|---|
| 属性/槽位/物品类型枚举 | `engine/dbc/data_enums.hh` | 对 `wowgear/const.py` 里的 ID 有疑问时 |
| **装备行解析** | `engine/item/item.cpp` | `gem_id` / `enchant_id` / `bonus_id` / `ilevel` 怎么处理；**为什么导出不能写装备名**——`option_name_str != name_str` 就 `sim->error`，整个模拟失败 |
| 属性换百分比 | `engine/player/rating.cpp`、`engine/player/player.cpp` | `apply_combat_rating_dr`、`composite_*_rating` |
| 战斗评级表 | `engine/dbc/generated/sc_scale_data.inc` | `__combat_ratings[24][90]`，90 级那列 ×100 就是我们的 `base_rate`（46 / 44 / 54 / 46） |
| 裸装基础属性 | 同上 | `gtClassStatsByLevel` / `gtOCT*`。注意**这批 gt 表是压缩前的标尺**，见 `docs/技术路线.md` 里的 `scale: 1.5` |
| 精通系数 | `engine/dbc/spell_data.hpp` | `mastery_value()` = `_sp_coeff * 0.01` |
| 被动属性累加 | `engine/player/player.cpp` | `get_passive_player_value()`——基础百分比是扫 aura 累加出来的，不是查表 |
| 物品数据表结构 | `engine/dbc/item_data.hpp`、`engine/item/item_database.hpp` | `RandPropPoints` 预算、`bonus_id` 怎么改属性 |
| 法术效果表 | `engine/dbc/generated/sc_spell_data.inc` | `__spelleffect_data`，5.3 万条；`A_MOD_ALL_CRIT_CHANCE=290` 之类 |

## 我们和 SimC 的关系

**没有代码依赖，只有认知依赖。** Python 这边一行 SimC 代码都没跑，也跑不了（它是 C++）。
所以本项目自己的许可是干净的——参照公式属于对算法和事实的使用，不是复制代码。

真要核对某个具体数值，最省事的还是去上游搜那几个函数名：它们的注释常常直接写着
"这个 1.5 是压缩前的标尺"这种关键信息，比读代码本身有用。
