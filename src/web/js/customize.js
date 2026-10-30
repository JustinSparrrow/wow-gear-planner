/* ══════════ 自选宝石 / 附魔 ══════════

   这两块和"换装备"是同一个面板的三种模式，共用选中的部位。
   为什么放一起而不是各开一个弹层：调配装的真实动作是"换戒指 → 顺手把宝石也换了"，
   分成两个入口每换一件就要来回切一次。

   宝石现在有图标了（data/icons/gems/，按品种配色），所以左边放真图标，
   不再用属性色块代替。分类按"它主要给哪条属性"分：玩家找宝石时想的是
   "我要急速的"，不会先想"我要翠榴石"——虽然翠榴石就是急速。
*/

const MAX_SOCKETS = 3;

// 卡片上的品质标签。颜色用游戏的品质色压低饱和度，
// 直接用 #a335ee 那种会在暗金界面里跳成荧光。
// 第三档叫"无瑕"而不是"完美"——客户端里的宝石名就是"无瑕迅捷榄石"。
const GEM_Q = {2:["普通","q2"], 3:["无瑕","q3"], 4:["史诗","q4"]};

/* 宝石 / 附魔的悬停预览。

   装备模式早就有悬停预览了（换上去属性条直接变成多少），但宝石和附魔
   没有——只能点了才知道变化，而"为了 +16 精通要不要拔掉 +7 暴击"正是
   来这儿要判断的事。走的是同一套 state.hover，属性条那一段不用改。 */
let customHoverTimer = null, customHoverSeq = 0;

function clearCustomHover(){
  if (customHoverTimer){ clearTimeout(customHoverTimer); customHoverTimer = null; }
  // 序号加一：已经发出去的请求回来时会被丢掉，不会把预览粘回界面上
  customHoverSeq++;
  if (!state.hover) return;
  state.hover = null;
  renderStats(); renderDoll();
}

function hoverCustomize(url, body, label){
  if (customHoverTimer) clearTimeout(customHoverTimer);
  const seq = ++customHoverSeq;
  customHoverTimer = setTimeout(async () => {
    const r = await fetch(url, {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (seq !== customHoverSeq) return;   // 鼠标已经挪走
    if (!r.ok) return;
    state.hover = {slot: state.slot, totals: d.totals, delta: d.delta,
                   avg: null, label};
    renderStats(); renderDoll();
  }, 90);
}

const STAT_COLOR = {
  crit:"#e05a5a", haste:"#e0c85a", mastery:"#b06ee0", vers:"#5a9ee0",
  main:"#e0955a", agi:"#e05a5a", str:"#e07a5a", int:"#5ab0e0", sta:"#5ac97a",
  leech:"#c95a8a", avoid:"#5ac9b8", speed:"#8ac95a",
};

/* 一颗宝石/一条附魔用哪个颜色：取它给的第一项属性。
   全属性宝石（主属性）排在最前，所以不会出现忽红忽蓝。 */
function statColor(stats){
  for (const k of ["main", "agi", "str", "int", "crit", "haste", "mastery", "vers"]){
    if (stats && stats[k]) return STAT_COLOR[k] || "#8a7fa0";
  }
  for (const k of Object.keys(stats || {})){
    if (STAT_COLOR[k]) return STAT_COLOR[k];
  }
  return "#8a7fa0";
}

function statText(stats){
  const ORDER = ["main","agi","str","int","sta","crit","haste","mastery","vers",
                 "leech","avoid","speed"];
  const CN = {main:"主属性", leech:"吸血", avoid:"闪避", speed:"移速"};
  return ORDER.filter(k => stats && stats[k])
    .map(k => `${CN[k] || STAT_CN[k] || k} +${Math.round(stats[k])}`).join("  ");
}

async function ensureGemOpts(){
  if (state.gemOpts) return state.gemOpts;
  const r = await fetch("/api/gem-options");
  const d = await r.json();
  state.gemOpts = d.gems || [];
  return state.gemOpts;
}

async function ensureEnchOpts(slotKey){
  if (state.enchSlot === slotKey && state.enchOpts) return state.enchOpts;
  const r = await fetch(`/api/enchant-options?slot=${encodeURIComponent(slotKey)}`);
  const d = await r.json();
  if (!r.ok){ state.enchOpts = []; state.enchSlot = slotKey; return []; }
  state.enchOpts = d.enchants || [];
  state.enchSlots = d.slots || {};
  state.enchSlot = slotKey;
  return state.enchOpts;
}

/* 库里的槽位名（finger/trinket）-> 界面上用的 simc 槽位键（finger1）
   只是为了让"本赛季收录了：戒指 9"这行字能翻成中文。 */
function slotKeyOf(dbSlot){
  return ({finger: "finger1", trinket: "trinket1"})[dbSlot] || dbSlot;
}

/* 槽位的中文名，去掉 simc 键尾部的 1/2（"戒指1" -> "戒指"）。
   附魔是按类别收录的，写成"戒指1 / 戒指2"反而像两条不同的规则。 */
function slotCnOf(dbSlot){
  return (SLOT_CN2[slotKeyOf(dbSlot)] || dbSlot).replace(/\d+$/, "");
}

/* ── 宝石 ── */
async function renderGemList(){
  const box = $("cands");
  const cur = slotMap()[state.slot];
  $("cand-title").textContent = `${SLOT_CN2[state.slot] || state.slot} · 宝石`;
  $("cand-count").textContent = "";
  $("search").style.display = "none";
  $("ilvl-btn").style.display = "none";
  // 那一排"全部/副本/M+/制造业"是装备的来源筛选，对宝石毫无意义
  $("tabs").innerHTML = "";

  const gems = await ensureGemOpts();
  const sockets = cur?.sockets || 0;
  const have = cur?.gem_ids || [];

  if (!cur){
    box.innerHTML = `<div class="opt-head">
        <div class="hint">这个部位还没有装备，先选装备再插宝石</div>
      </div>`;
    return;
  }

  // 没有孔、这个部位又不能开孔：到这里就结束了。
  // 能开孔的头/腰/腕不在这里返回——下方会给出"用打孔器开一个孔"的按钮。
  if (!sockets && !cur.can_drill && !cur.drilled){
    box.innerHTML = `<div class="opt-head">
        <div class="hint">「${esc(cur.display_name || "这件装备")}」没有插槽，插不了宝石
          <br>只有头、腰带、护腕能用打孔器开孔（每件限一个），其它部位本来就没有孔</div>
      </div>`;
    return;
  }

  const byId = {};
  gems.forEach(g => { byId[g.id] = g; });
  const holes = Math.min(sockets, MAX_SOCKETS);
  if (state.gemIndex >= holes) state.gemIndex = 0;

  // 唯一宝石（永歌钻石）已经装在哪儿了。当前这个孔里那颗不算占用——
  // 换掉它就是替换，不违反"每人一颗"。
  const usedGroups = {};
  (state.snap?.slots || []).forEach(sl => {
    (sl.gem_ids || []).forEach((gid, i) => {
      if (!gid) return;
      if (sl.slot_key === state.slot && i === state.gemIndex) return;
      const g = byId[gid];
      if (g && g.unique_group) usedGroups[g.unique_group] = g.name;
    });
  });

  const sockHtml = Array.from({length: holes}, (_, i) => {
    const gid = have[i];
    const g = gid ? byId[gid] : null;
    const on = i === state.gemIndex;
    const tip = g ? `${g.full_name || g.name}${g.ilvl ? ` · 装等 ${g.ilvl}` : ""}
${g.text || statText(g.stats)}` : "空孔";
    return `<button class="sock ${on ? "on" : ""} ${g ? "" : "empty"}"
        data-i="${i}" title="${esc(tip)}">
      <b>${i + 1}</b>
      <span>${g ? esc(g.name) : "空孔"}${g && g.ilvl ? `<i class="slv">${g.ilvl}</i>` : ""}</span>
      ${g ? `<i class="x" data-del="${i}">✕</i>` : ""}
    </button>`;
  }).join("");

  // 分类放在原来"来源筛选"那一条上：位置一样，都是"筛下面这堆卡片"，
  // 换模式就换内容，不会多出一条空行。
  const counts = {};
  gems.forEach(g => { counts[g.group] = (counts[g.group] || 0) + 1; });
  const tabs = $("tabs");
  const order = ["haste", "mastery", "crit", "vers", "main"];
  tabs.innerHTML =
    `<button class="tab ${state.gemGroup === "" ? "on" : ""}" data-g="">全部
       <span class="c">${gems.length}</span></button>` +
    order.filter(k => counts[k]).map(k =>
      `<button class="tab ${state.gemGroup === k ? "on" : ""}" data-g="${k}">
        ${k === "main" ? "主属性" : STAT_CN[k]}<span class="c">${counts[k]}</span>
      </button>`).join("");
  tabs.querySelectorAll(".tab").forEach(b => {
    b.onclick = () => { state.gemGroup = b.dataset.g; renderGemList(); };
  });

  const list = state.gemGroup
    ? gems.filter(g => g.group === state.gemGroup) : gems;
  const curGemId = have[state.gemIndex] || 0;
  const haveCount = have.filter(x => x).length;
  // 这件装备上所有宝石合计给了多少。逐颗看得到，合计要自己加就没意义了。
  const sum = {};
  have.forEach(gid => {
    const g = gid ? byId[gid] : null;
    if (!g) return;
    for (const [k, v] of Object.entries(g.stats || {})) sum[k] = (sum[k] || 0) + v;
  });
  const sumText = statText(sum);

  // 打孔器：只有头 / 腰带 / 护腕能用，每件限一个。开孔是玩家状态，游戏里
  // 不写在物品上，simc 导出也不带——所以只能在这里点一下。
  const drillRow = cur.can_drill
    ? `<button class="drill" data-drill="1"
         title="Radiant Jewelbinder：花 6 个低保币给这件装备开一个孔，每件限一次">
         ＋ 用打孔器开一个孔 <i>头 / 腰带 / 护腕，每件限一个</i></button>`
    : cur.drilled
      ? `<button class="drill on" data-drill="0"
           title="再点一下取消这个孔（孔里还插着宝石的话要先把宝石拔下来）">
           打孔器 · 已开 1 个孔 <i>点这里取消</i></button>`
      : "";

  box.innerHTML = `
    <div class="opt-head">
      <div class="socks">${sockHtml}</div>
      ${drillRow}
      <div class="hint">
        ${holes
          ? `先点一个孔，再点下面的宝石插进去　·　${holes} 个插槽${
              haveCount ? `，已插 ${haveCount} 颗${sumText ? `（合计 ${sumText}）` : ""}` : ""}`
          : "这件装备还没有孔，先用上面的打孔器开一个"}
        <br>鼠标停在宝石上，属性条会显示换上去变成多少${
          curGemId ? `；第 ${state.gemIndex + 1} 孔已插，点别的就是替换` : ""}
      </div>
    </div>
    ${holes ? `<div class="opt-grid">
      ${list.map(g => {
        const q = GEM_Q[g.quality] || GEM_Q[2];
        // 名字里的"完美"由右边的品质标签表达，免得同一颗宝石的两个装等版本
        // 看起来更像；这件事在服务端 _gem_name 里已经做掉了。
        const icon = g.icon
          ? `<img class="gico" src="/icon/gems/${encodeURIComponent(g.icon)}" alt="">`
          : `<span class="dot" style="background:${statColor(g.stats)}"></span>`;
        const isCur = g.id === curGemId;
        // 唯一宝石已经装在别处：卡片压暗、标"已装备"，点了给出原因
        const usedBy = g.unique_group ? usedGroups[g.unique_group] : null;
        const tip = [g.full_name || g.name,
                     g.ilvl ? `装等 ${g.ilvl}` : "",
                     g.unique_group ? "每人限一颗" : "",
                     usedBy ? `已装备（${usedBy}）` : ""]
                    .filter(Boolean).join(" · ");
        return `<div class="card gem ${isCur ? "cur" : ""} ${usedBy ? "used" : ""}"
            data-gem="${g.id}" title="${esc(tip)}">
          ${icon}
          <div class="body2">
            <div class="nm">
              <span class="t">${esc(g.name)}</span>
              <i class="q ${q[1]}">${q[0]}</i>
              ${g.ilvl ? `<i class="lv">${g.ilvl}</i>` : ""}
              ${g.unique_group ? `<i class="uq">唯一</i>` : ""}
            </div>
            <div class="sec">${esc(statText(g.stats))}</div>
          </div>
          ${isCur ? `<span class="now">本孔</span>`
                  : usedBy ? `<span class="now used">已装备</span>` : ""}
        </div>`;
      }).join("")}
    </div>` : ""}`;

  const drillBtn = box.querySelector(".drill");
  if (drillBtn){
    drillBtn.onclick = () => {
      clearCustomHover();
      setDrilled(state.slot, drillBtn.dataset.drill === "1");
    };
  }

  box.querySelectorAll(".sock").forEach(b => {
    const i = +b.dataset.i;
    if (have[i]){
      // 悬停已插的孔 = 预览"拔掉它属性会少多少"，拔之前心里有数
      b.onmouseenter = () => hoverCustomize(
        "/api/preview-gem", {slot: state.slot, index: i, gem_id: 0}, "拔掉这颗");
      b.onmouseleave = clearCustomHover;
    }
    b.onclick = (ev) => {
      if (ev.target.dataset.del != null){
        ev.stopPropagation();
        clearCustomHover();
        return removeGem(state.slot, +ev.target.dataset.del);
      }
      state.gemIndex = i;
      clearCustomHover();
      renderGemList();
    };
  });
  box.querySelectorAll(".card[data-gem]").forEach(c => {
    const gem = list.find(g => g.id === +c.dataset.gem);
    const usedBy = gem && gem.unique_group ? usedGroups[gem.unique_group] : null;
    c.onclick = () => {
      clearCustomHover();
      // 唯一宝石已经装在别的孔/别的装备上，点下去也插不上，直接说清楚
      if (usedBy){
        toastMsg(`「${esc(gem.name)}」每人只能装一颗，${esc(usedBy)} 上已经有一颗了`);
        return;
      }
      pickGem(state.slot, state.gemIndex, +c.dataset.gem);
    };
    c.onmouseenter = () => {
      if (gem && !usedBy) hoverCustomize("/api/preview-gem",
        {slot: state.slot, index: state.gemIndex, gem_id: gem.id}, gem.name);
    };
    c.onmouseleave = clearCustomHover;
  });
}

/* 换完宝石/附魔后的统一收尾：重画 + 属性条挂 2.6 秒增减 + 一句话提示。

   换装备一直有这个反馈（"刚换上"那行），宝石/附魔以前点完只是数字悄悄变，
   容易以为没生效。 */
function applyCustomize(d, before, html){
  const after = d.totals || {};
  const delta = {};
  for (const k of new Set([...Object.keys(before), ...Object.keys(after)])){
    delta[k] = (after[k] || 0) - (before[k] || 0);
  }
  applySnapshot(d);
  state.lastDelta = delta;
  renderStats();
  if (state.deltaTimer) clearTimeout(state.deltaTimer);
  state.deltaTimer = setTimeout(() => { state.lastDelta = null; renderStats(); }, 2600);
  if (html) toastMsg(html);
}

async function pickGem(slotKey, index, gemId){
  const before = (state.snap && state.snap.totals) || {};
  const r = await fetch("/api/gem", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot: slotKey, index, gem_id: gemId}),
  });
  const d = await r.json();
  if (!r.ok){ toastMsg(d.error || "插不进去"); return; }
  const g = (state.gemOpts || []).find(x => x.id === gemId);
  applyCustomize(d, before, g ? `第 ${index + 1} 孔 插上 <b>${esc(g.name)}</b>` : "已插上");
}

async function removeGem(slotKey, index){
  const before = (state.snap && state.snap.totals) || {};
  const slot = slotMap()[slotKey];
  const gid = slot && (slot.gem_ids || [])[index];
  const r = await fetch("/api/gem", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot: slotKey, index, gem_id: 0}),
  });
  const d = await r.json();
  if (!r.ok){ toastMsg(d.error || "拔不下来"); return; }
  const g = (state.gemOpts || []).find(x => x.id === gid);
  applyCustomize(d, before, g ? `拔下 <b>${esc(g.name)}</b>` : "拔下一颗宝石");
}

/* 用打孔器开 / 取消一个孔。孔本身不带属性，所以不挂"刚换上"的增减，
   只重画面板 + 一句提示。 */
async function setDrilled(slotKey, on){
  const r = await fetch("/api/drill", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot: slotKey, on}),
  });
  const d = await r.json();
  if (!r.ok){ toastMsg(d.error || "开不了这个孔"); return; }
  state.snap = d;
  state.hover = null;
  state.lastDelta = null;
  renderAll();
  renderCands();
  toastMsg(on ? "已开一个孔，可以插宝石了" : "已取消这个孔");
}

/* ── 附魔 ── */
async function renderEnchantList(){
  const box = $("cands"), s = state.snap;
  const cur = slotMap()[state.slot];
  $("cand-title").textContent = `${SLOT_CN2[state.slot] || state.slot} · 附魔`;
  $("cand-count").textContent = "";
  $("search").style.display = "none";
  $("ilvl-btn").style.display = "none";
  $("tabs").innerHTML = "";

  const list = await ensureEnchOpts(state.slot);
  const curId = cur?.enchant_id;

  if (!list.length){
    // "没有可附的魔"和"这条附魔没收录"是两件事，混成一句话会让人
    // 以为游戏里这个部位真没附魔（腿部是有附魔的，只是 id 不在 SimC 清单里）
    const covered = Object.entries(state.enchSlots || {})
      .map(([k, n]) => `${slotCnOf(k)} ${n}`).join(" · ");
    const unknown = cur && curId && cur.enchant_known === false;
    box.innerHTML = `<div class="opt-head">
        <div class="hint">${
          unknown
            ? `这件装备上的附魔（id ${curId}）没收录，本地属性里没算它。`
            : "这个部位没有可选的附魔。"
        }${covered ? `<br>本赛季收录到的附魔：${covered}` : ""}</div>
      </div>`;
    return;
  }

  // 已选的那条要能取消，否则选错了只能去改存档
  const curRow = curId
    ? `<button class="sock on" data-clear="1"><b>✓</b><span>当前：${
        esc(cur.enchant_note || statText(cur.enchant) || statText(cur.enchant_tertiary) || "已附魔")
      }</span><i class="x" data-clear="1">✕</i></button>`
    : `<button class="sock empty"><b>–</b><span>还没附魔</span></button>`;

  // 附魔的第三属性（吸血/闪避）和百分比特效都要写出来——
  // 它们不加面板，但玩家选附魔时看的正是这个。
  // 第一条当主行，剩下的并到副行，避免三行把卡片撑高。
  const card = (e, isCur) => {
    // 一行主信息 + 一行补充：
    //   有属性 → "耐力 +186"；触发类 → "触发：急速 +99，持续 15 秒"；
    //   都没有 → 手写的描述（流血/护盾这类），最后才是"无属性"。
    // desc 只在没有数字可显示时才占主行——腿部那几条的"数值按系数推算"
    // 挂在每张卡上会把名字挤掉，放到 title 里就够。
    const statTxt = statText(e.stats) || statText(e.tertiary);
    const parts = [];
    const main = statTxt || e.note || e.desc || "无属性";
    if (e.note && e.note !== main) parts.push(`特效：${e.note}`);
    const rest = parts.join("　");
    const tip = [e.full_name || e.name, statTxt ? e.desc : ""].filter(Boolean).join("　");
    // 附魔图标就是游戏里的附魔羊皮纸（按品质分红/绿/蓝），
    // 38 条里只有 3 张不同的图，但那正是游戏里真实的观感。
    const icon = e.icon
      ? `<img class="gico" src="/icon/enchants/${encodeURIComponent(e.icon)}" alt="">`
      : "";
    return `<div class="card ench ${isCur ? "cur" : ""}" data-ench="${e.id}"
        title="${esc(tip)}">
      ${icon}
      <div class="body2">
        <div class="nm">${esc(e.name)}${isCur ? `<i class="now">当前</i>` : ""}</div>
        <div class="sec">${esc(main)}${rest ? `　<i class="mut">${esc(rest)}</i>` : ""}</div>
      </div>
    </div>`;
  };

  box.innerHTML = `
    <div class="opt-head">
      <div class="socks">${curRow}</div>
      <div class="hint">点一下直接换　·　鼠标停着可以看换上去属性变多少
        <br>第三属性（吸血/闪避/移速）和百分比特效不计入面板</div>
    </div>
    <div class="opt-grid">
      ${list.map(e => card(e, e.id === curId)).join("")}
    </div>`;

  box.querySelectorAll("[data-clear]").forEach(b => {
    b.onclick = (ev) => { ev.stopPropagation(); clearCustomHover(); pickEnchant(state.slot, 0); };
  });
  box.querySelectorAll(".card[data-ench]").forEach(c => {
    const e = list.find(x => x.id === +c.dataset.ench);
    c.onclick = () => { clearCustomHover(); pickEnchant(state.slot, +c.dataset.ench); };
    c.onmouseenter = () => {
      if (e && e.id !== curId){
        hoverCustomize("/api/preview-enchant",
          {slot: state.slot, enchant_id: e.id}, e.name);
      }
    };
    c.onmouseleave = clearCustomHover;
  });
}

async function pickEnchant(slotKey, enchantId){
  const before = (state.snap && state.snap.totals) || {};
  const r = await fetch("/api/enchant", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot: slotKey, enchant_id: enchantId}),
  });
  const d = await r.json();
  if (!r.ok){ toastMsg(d.error || "换不了这条附魔"); return; }
  const e = (state.enchOpts || []).find(x => x.id === enchantId);
  applyCustomize(d, before,
    e ? `已附上 <b>${esc(e.name)}</b>` : "已取下附魔");
}

/* 三个模式共用的"换完数据怎么办"：
   重画装备栏和属性条，但保持当前模式和部位，否则插完一颗宝石
   界面就跳回装备列表，插第二颗还得再点一次。 */
function applySnapshot(d){
  state.snap = d;
  state.hover = null;
  renderAll();
  renderCands();
}

function setMode(m){
  if (state.mode === m) return;
  state.mode = m;
  state.gemIndex = 0;
  document.querySelectorAll("#modes button").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === m));
  renderCands();
}
