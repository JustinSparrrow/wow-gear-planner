/* render.js

主界面的渲染：属性条、装备栏、部位筛选、候选卡片。
只把 state 变成 DOM，不发请求、不改 state。 */

/* 顶部的平均装等。算法和游戏一致：16 格求和 ÷ 16，双手武器算两次。
   没处理这一点的话，猎人手里那把弓只能算一格，平均装等会平白低 20 多。 */
function renderAvg(){
  const s = state.snap, box = $("avg");
  if (!s){ box.style.display = "none"; return; }
  box.style.display = "";
  const base = s.avg_ilevel || 0;
  const hv = state.hover && state.hover.avg != null ? state.hover.avg : null;
  const show = hv != null ? hv : base;
  $("avg-val").textContent = show ? show.toFixed(1) : "—";
  const d = show - base;
  const el = $("avg-delta");
  if (hv == null || Math.abs(d) < 0.05){
    el.textContent = ""; el.className = "d";
  } else {
    el.textContent = (d > 0 ? "+" : "") + d.toFixed(1);
    el.className = "d " + (d > 0 ? "up" : "down");
  }
  box.title = `16 个格子求和 ÷ 16，双手武器算两次（已装 ${s.avg_filled} 格）`;
}

function renderWho(){
  const s = state.snap;
  if (!s){
    $("who").innerHTML = "";
    $("btn-reset-all").style.display = "none";
    $("btn-panel").style.display = "none";
    $("btn-save").style.display = "none";
    $("btn-export").style.display = "none";
    $("btn-undo").style.display = "none";
    $("avg").style.display = "none";
    return;
  }
  $("who").innerHTML = `
    <span class="cls" style="color:${classColor(s.class_key)}">${s.class_cn}</span>
    <span class="tag armor">${s.armor_cn}</span>
    <span class="tag">${s.level || "?"} 级</span>
    ${s.spec_cn ? `<span class="tag">${s.spec_cn}</span>` : ""}
    <span style="color:var(--dim)">${s.name || ""}</span>`;
  $("btn-reset-all").style.display = "";
  $("btn-panel").style.display = "";
  $("btn-save").style.display = "";
  $("btn-export").style.display = "";
  $("btn-undo").style.display = s.can_undo ? "" : "none";
  $("equip-count").textContent = s.slots.length + " 件";
}

function classColor(k){
  const m = {warrior:"#c69b6d", paladin:"#f48cba", hunter:"#aad372", rogue:"#fff468",
             priest:"#f0ebe0", shaman:"#0070dd", mage:"#3fc7eb", warlock:"#8788ee",
             monk:"#00ff98", druid:"#ff7c0a", demonhunter:"#a330c9",
             deathknight:"#c41e3a", evoker:"#33937f"};
  return m[k] || "var(--text)";
}

function renderStats(){
  const s = state.snap, box = $("stats");
  // 没角色时整条属性栏一起收掉。只清 innerHTML 的话 min-height:64px 还在，
  // 顶上会留一条空的深色横带，看着像渲染失败
  box.style.display = s ? "" : "none";
  if (!s){ box.innerHTML = ""; return; }
  const h = state.hover;
  const totals = h ? h.totals : s.totals;
  const delta = h ? h.delta : (state.lastDelta || null);
  const order = STAT_ORDER.filter(k => (totals[k] || 0) > 0 ||
                                        (delta && Math.abs(delta[k] || 0) > 0.5));
  const calibrated = !!(s.panel && Object.keys(s.panel).length);
  // 换装备时是"按某装等预览"，宝石/附魔没有装等这回事，只有在换哪一颗，
  // 所以预览文案跟着 label 走，不硬写"XX 装等"
  const note = h
    ? `<span style="color:#a8d0ff">${h.label ? "预览：" + esc(h.label) : "预览中 " + h.ilevel + " 装等"}</span><br>${h.label ? "点一下落地" : "点卡片即替换"}`
    : state.lastDelta ? `<span style="color:var(--up)">刚换上</span><br>点卡片即替换`
    : calibrated ? "面板值<br>含角色基础"
    // 没填面板时用客户端的职业基础表兜底：那个数不含天赋，会偏低一点，
    // 但比"只算装备本体"（敏捷少一千多）有参考价值得多，所以如实标出来
    : s.base_source === "class"
      ? `<span style="color:var(--gold)">职业基础值</span><br>主属性未含天赋`
      : `<span style="color:var(--gold)">未校准</span><br>只算装备本体`;
  const rates = s.rating_per_pct || {};
  const pctMap = s.pct || {};
  const fsrcMap = s.flat_source || {};
  // 绿字百分比是不是每一项都有天赋兜底。只填了主属性/耐力不算——那种情况下
  // 敏捷是准的，但暴击仍然只有装备那部分，比游戏里低一截。
  // 以前只用「填没填面板」一个开关，于是填了敏捷就显示"面板值"，绿字却没提示。
  const pctThin = ["crit", "haste", "mastery", "vers"]
    .some(k => pctMap[k] != null && fsrcMap[k] !== "panel");
  box.innerHTML = order.map(k => {
    const v = totals[k] || 0;
    const d = delta ? (delta[k] || 0) : 0;
    const cls = d > 0.5 ? "up" : d < -0.5 ? "down" : "";
    const txt = Math.abs(d) > 0.5 ? (d > 0 ? "+" : "") + Math.round(d).toLocaleString() : "";
    const isMain = PRIMARY.concat("sta").includes(k);
    // 主属性和耐力没有百分比，只有战斗等级（暴击/急速/精通/全能）才有
    // 服务端按递减档位算好了百分比（s.pct）；旧快照没有这个字段时才退回线性
    const pctDone = s.pct || {};
    const rate = rates[k];
    // 百分比拆成两截，悬停能看到「装备给了多少、天赋/被动给了多少」。
    // 这个拆分不是炫技：用户拿它跟游戏面板对不上时，能立刻判断是装备算错了
    // 还是天赋没算进去——以前只有一个总数，对不上就只能猜。
    const fv = (s.flat || {})[k];
    const fsrc = (s.flat_source || {})[k] || "none";
    let tip = "";
    if (pctDone[k] != null){
      if (fsrc === "none"){
        tip = ` title="只算了装备，未含天赋和专精被动——填面板可校准"`;
      } else if (fv != null){
        const gearPart = pctDone[k] - fv;
        const label = fsrc === "panel" ? "天赋（面板反推）"
                    : fsrc === "base"  ? "客户端基础暴击（职业天赋未算）"
                    : "专精默认基线";
        tip = ` title="装备 ${gearPart.toFixed(2)}% ＋ ${label} ${fv.toFixed(2)}%"`;
      }
    }
    // 缺天赋的那些项打个星号：数字本身没错（装备算得对），但比游戏里低，
    // 必须让用户一眼看出来，不然他会以为整个工具不准
    const mark = (pctDone[k] != null && fsrcMap[k] && fsrcMap[k] !== "panel")
      ? `<i class="est" title="这一项未含天赋">*</i>` : "";
    const shown = pctDone[k] != null
      ? `<span class="pct"${tip}>${pctDone[k].toFixed(2)}%</span>${mark}`
      : (rate && v > 0 ? `<span class="pct">${(v / rate).toFixed(1)}%</span>` : "");
    return `<div class="stat ${isMain ? "main" : ""}">
      <div class="k">${STAT_CN[k]}</div>
      <div class="v">${Math.round(v).toLocaleString()}${shown}</div>
      <div class="d ${cls}">${txt}</div>
    </div>`;
  }).join("") + `<div class="stat-note">${note}`
    + (pctThin && !h
        ? `<br><span class="thin">* 绿字只算装备，填「面板校准」可补上天赋</span>` : "")
    + `</div>`;
}

function iconHtml(name, cls){
  if (!name) return `<div class="${cls} empty"></div>`;
  return `<img class="${cls}" src="/icon/${name}" loading="lazy"
    onerror="this.classList.add('empty');this.removeAttribute('src')">`;
}

function slotMap(){
  const m = {};
  (state.snap?.slots || []).forEach(s => { m[s.slot_key] = s; });
  return m;
}

function eqCell(key, map){
  const it = map[key];
  const cn = SLOT_CN2[key] || key;
  const cls = [
    "eq",
    state.slot === key ? "on" : "",
    it && it.changed ? "changed" : "",
    state.hover && state.hover.slot === key ? "preview" : "",
  ].filter(Boolean).join(" ");

  if (!it){
    return `<div class="${cls}" data-slot="${key}">
      <div class="ic empty"></div>
      <div class="body2">
        <div class="top"><span>${cn}</span></div>
        <div class="nm" style="color:var(--dimmer)">空</div>
      </div>
    </div>`;
  }
  const color = QCOLOR[it.quality] || "var(--q1)";
  // data-q 只喂给 CSS 做品质描边，别的地方不用它
  return `<div class="${cls}" data-slot="${key}" data-q="${it.quality ?? 1}">
    ${iconHtml(it.icon, "ic")}
    <div class="body2">
      <div class="top">
        <span>${cn}</span>
        ${it.kind_cn ? `<span style="color:#7fb8ff">${it.kind_cn}</span>` : ""}
        ${(it.gem_ids && it.gem_ids.length) ? `<span class="mk gem" title="有宝石">◆</span>` : ""}
        ${it.enchant_id ? `<span class="mk ench" title="有附魔">✦</span>` : ""}
        <span class="lv">${it.ilevel || "?"}</span>
      </div>
      <div class="nm" style="color:${it.changed ? "" : color}">${it.display_name || it.name || "?"}</div>
      ${secLine(it)}
    </div>
    ${it.changed ? `<span class="rev">还原</span>` : ""}
  </div>`;
}

/* 格子里的副属性。只列绿字——主属性顶部那条是总数，装备本身那一两百点
   看了也判断不了什么；绿字才是换装时要对着比的。按数值降序只留两条，
   多的折成 +N，项链这种四条绿字的格子才不会被挤爆。 */
function secLine(it){
  const v = it.stat_values || {};
  const list = SECONDARY.filter(k => v[k]).sort((a, b) => v[b] - v[a]);
  if (!list.length){
    // 饰品类只有主属性，也要给个说法，不能空着让人以为没读到数据
    return `<div class="sec none">无绿字</div>`;
  }
  const parts = list.slice(0, 2).map(k =>
    `<span>${STAT_CN[k]}<b>${Math.round(v[k]).toLocaleString()}</b></span>`);
  if (list.length > 2) parts.push(`<span class="more">+${list.length - 2}</span>`);
  const full = list.map(k => `${STAT_CN[k]} ${Math.round(v[k]).toLocaleString()}`).join(" · ");
  return `<div class="sec" title="${full}">${parts.join("")}</div>`;
}

function renderDoll(){
  const s = state.snap, box = $("doll");
  if (!s){
    box.innerHTML = `<div class="doll-empty">
      <b>还没有角色</b>
      <span>游戏里打 <kbd>/simc</kbd>，把弹出的整段文本<br>粘进右上角的「导入 simc」</span>
    </div>`;
    return;
  }
  const m = slotMap();
  // 两列交错排列：左1 右1 左2 右2…，这样同一行的两格高度一定相同。
  // 分成两个独立 flex 列的话，腰部比头部多一行、两列就会错开，看着像没对齐。
  const cells = [];
  for (let i = 0; i < Math.max(DOLL_LEFT.length, DOLL_RIGHT.length); i++){
    if (DOLL_LEFT[i]) cells.push(eqCell(DOLL_LEFT[i], m));
    if (DOLL_RIGHT[i]) cells.push(eqCell(DOLL_RIGHT[i], m));
  }
  box.innerHTML = cells.join("") + DOLL_BOTTOM.map(k => eqCell(k, m)).join("");

  box.querySelectorAll(".eq").forEach(el => {
    const key = el.dataset.slot;
    el.onclick = () => selectSlot(key);
    el.oncontextmenu = (ev) => {
      ev.preventDefault();
      const item = m[key];
      if (item && item.changed) resetSlot(key);
    };
    if (m[key]){
      el.onmouseenter = (ev) => showItemTip(m[key], ev);
      el.onmousemove = moveTip;
      el.onmouseleave = hideTip;
    }
  });
}

/* ══════════ 装等选择 ══════════ */
function renderIlvlBtn(){
  const s = state.snap;
  const opts = s?.ilevel_options || [];
  const extras = opts.find(g => g.track === "Extra") || {notes:{}};
  if (!state.slot || !state.ilvl){
    $("ilvl-val").textContent = "—";
    $("ilvl-btn").style.display = state.snap ? "" : "none";
    return;
  }
  const note = extras.notes[String(state.ilvl)];
  // 整块重写按钮内容时必须把 id 带上——之前单写过 #ilvl-val 再重写 innerHTML，
  // 第二次调用拿到的就是 null，直接抛错，装等切换看着像没反应
  $("ilvl-btn").innerHTML = `装等 <b id="ilvl-val">${state.ilvl}</b>`
    + (note ? ` <span class="tagx">${note}</span>` : "")
    + ` <i>▾</i>`;
}

function openIlvlPop(){
  const s = state.snap;
  if (!s || !state.slot) return;
  const opts = s.ilevel_options || [];
  $("ilvl-pop").innerHTML = `
    <div class="cols">${opts.map(g => `
      <div class="col ${g.track === "Extra" ? "special" : ""}">
        <div class="h">${g.label}</div>
        ${g.levels.map(lv => `<button class="lv ${lv === state.ilvl ? "on" : ""}"
            data-lv="${lv}" title="${g.notes[lv] || g.label}">${lv}</button>`).join("")}
      </div>`).join("")}</div>
    <div class="note">341 = 额外升级（神话满级 +7）　344 = 神话团本尾王掉落<br>
      属性按所选装等重算；同一件装备换个装等，绿字差别很大。</div>`;
  const pop = $("ilvl-pop");
  pop.classList.add("on");
  const r = $("ilvl-btn").getBoundingClientRect();
  pop.style.left = Math.min(r.left, innerWidth - pop.offsetWidth - 12) + "px";
  pop.style.top = (r.bottom + 6) + "px";
  // 事件委托：面板每次重建 innerHTML，逐个绑容易漏
  pop.onclick = (ev) => {
    const btn = ev.target.closest(".lv");
    if (!btn) return;
    ev.stopPropagation();
    pop.classList.remove("on");
    setIlvl(Number(btn.dataset.lv));
  };
}

function setIlvl(lv){
  state.ilvl = lv;
  renderIlvlBtn();
  if (state.slot) loadCands();
}

/* ══════════ 来源筛选 ══════════ */
function renderTabs(){
  const s = state.snap, box = $("tabs");
  // 宝石/附魔模式下这条栏归 customize.js 管（放的是宝石分类），
  // 这里提前返回，否则刚画好的分类会被来源筛选覆盖掉。
  if (!s || !state.slot || state.mode !== "equip"){ box.innerHTML = ""; return; }
  const counts = {};
  state.cands.forEach(i => {
    const k = i.source ? i.source.kind : "other";
    counts[k] = (counts[k] || 0) + 1;
  });
  const total = state.cands.length;
  const parts = [`<button class="tab ${state.kind === "" ? "on" : ""}" data-kind="">
    全部<span class="c">${total}</span></button>`];
  for (const k of KIND_ORDER){
    if (!counts[k]) continue;
    parts.push(`<button class="tab ${state.kind === k ? "on" : ""}" data-kind="${k}">
      ${KIND_CN[k]}<span class="c">${counts[k]}</span></button>`);
  }
  box.innerHTML = parts.join("");
  box.querySelectorAll(".tab").forEach(b => {
    b.onclick = () => {
      state.kind = b.dataset.kind;
      renderTabs(); renderCands();
    };
  });
}

/* ══════════ 候选 ══════════ */
function statRows(it){
  const v = it.stat_values || {};
  const rows = [];
  const prim = PRIMARY.concat("sta").filter(k => v[k]);
  for (const k of prim){
    rows.push(`<div class="sv ${k === "sta" ? "" : "prim"}">
      <span>${STAT_CN[k]}</span><b>${Math.round(v[k]).toLocaleString()}</b></div>`);
  }
  for (const k of SECONDARY){
    if (!v[k]) continue;
    rows.push(`<div class="sv sec">
      <span>${STAT_CN[k]}</span><b>${Math.round(v[k]).toLocaleString()}</b></div>`);
  }
  // 自选槽已经指定过的（player_crafted.json 里有记录），属性已经算进上面了，
  // 再挂一行"待指定"会让人以为没选
  if ((it.stats || []).some(s => s.optional)
      && !(it.crafted_stats && it.crafted_stats.length)){
    // 光写"待指定"等于没给信息。把每个槽能提供多少点写出来，
    // 才知道这件换成自选的暴击能加多少。
    const opts = it.optional_each || [];
    const each = !opts.length ? "待指定"
      : new Set(opts).size === 1 ? `${opts[0]} × ${opts.length}`
      : opts.join(" / ");
    rows.push(`<div class="sv opt"><span>自选槽</span><b>${each}</b></div>`);
  }
  if (!rows.length) rows.push(`<div class="sv"><span style="color:var(--dimmer)">无属性</span></div>`);
  return rows.join("");
}

function diffRow(it, cur){
  const a = it.stat_values || {};
  const b = (cur && cur.stat_values) || {};
  const parts = [];
  for (const k of STAT_ORDER){
    const d = (a[k] || 0) - (b[k] || 0);
    if (Math.abs(d) < 0.5) continue;
    parts.push(`<span class="${d > 0 ? "u" : "d"}">${STAT_CN[k]} ${d > 0 ? "+" : ""}${Math.round(d).toLocaleString()}</span>`);
  }
  // 自选槽还没定属性的，差异里天然没有绿字，不说清楚会看成"这件没绿字"
  if (it.optional_each && it.optional_each.length){
    const tot = it.optional_each.reduce((x, y) => x + y, 0);
    parts.push(`<span class="hint">+ 自选绿字 ${tot}</span>`);
  }
  if (!parts.length){
    return `<span class="same">${cur ? "与当前相同" : "当前部位是空的"}</span>`;
  }
  return parts.join("");
}

function srcHtml(it){
  const s = it.source;
  if (!s) return "";
  const kind = it.craft_kind ? "" : `<span class="kind ${s.kind}">${s.label}</span>`;
  // 副本 + boss 都有才用分隔点；制造业/套装转化只有一个名字，
  // 前面挂个孤零零的 · 会显得像漏了字
  let txt = "";
  if (s.instance && s.encounter) txt = `${esc(s.instance)} <em>·</em> ${esc(s.encounter)}`;
  else if (s.encounter) txt = esc(s.encounter);
  else if (s.instance) txt = esc(s.instance);
  else txt = esc(s.label || "");
  return `<div class="src">${kind}<span class="txt">${txt}</span></div>`;
}

function renderCands(){
  const s = state.snap, box = $("cands");
  if (!s){ box.innerHTML = `<div class="empty-tip">先导入 simc 字符串</div>`; return; }
  if (!state.slot){
    // 标题也要清掉：载入存档后部位没了，标题还留着"胸部 11 件"会前后矛盾
    $("cand-title").textContent = "选择部位";
    $("cand-count").textContent = "";
    $("search").style.display = "none";
    box.innerHTML = `<div class="empty-tip">← 点左侧部位开始</div>`;
    return;
  }

  // 宝石 / 附魔是同一个面板的另外两种模式，全权交给 customize.js 画
  if (state.mode === "gem"){ return renderGemList(); }
  if (state.mode === "enchant"){ return renderEnchantList(); }

  $("cand-title").textContent = SLOT_CN2[state.slot] || state.slot;
  $("search").style.display = "";
  $("ilvl-btn").style.display = "";

  let list = state.cands;
  if (state.kind){
    list = list.filter(i => (i.source ? i.source.kind : "other") === state.kind);
  }
  if (state.filter){
    const kw = state.filter.toLowerCase();
    list = list.filter(i => (i.name + " " + i.name_en + " " +
      ((i.source && i.source.text) || "")).toLowerCase().includes(kw));
  }
  const m = slotMap();
  const cur = m[state.slot];
  // 当前装备置顶，方便对照
  if (cur){
    list = list.slice();
    const i = list.findIndex(x => x.id === cur.id);
    if (i > 0) list.unshift(list.splice(i, 1)[0]);
  }
  $("cand-count").textContent = `${list.length} 件 · 点卡片直接换上`;

  if (!list.length){
    box.innerHTML = `<div class="empty-tip">${state.filter || state.kind
      ? "这个筛选下没有装备" : "这个部位没有可换的装备"}</div>`;
    return;
  }

  box.innerHTML = list.map(it => {
    const color = QCOLOR[it.quality] || "var(--q1)";
    const isCur = cur && cur.id === it.id;
    const badges = [];
    if (isCur) badges.push(`<span class="badge now">当前</span>`);
    if (it.craft_kind) badges.push(`<span class="badge ${it.craft_kind}">${it.kind_cn}</span>`);
    if (it.is_reprint) badges.push(`<span class="badge reprint">复刻</span>`);
    if (it.variant_count > 1) badges.push(`<span class="badge multi">同款 ${it.variant_count}</span>`);

    // 套装转化件：库里存的那套绿字只是模板，实际转化出来继承原装备。
    // 拿模板值当属性显示会误导——猎人头模板是急速+精通，实际身上是暴击+精通。
    const inherit = it.category === "catalyst" && !isCur && !it.stat_from;
    // 当前装备用 snapshot 那份（带 redirected_base_stats，等于身上的真实绿字），
    // 不能用 candidates 那份——候选里的套装转化件只有模板属性
    const shown = isCur ? cur : it;
    const setName = (it.source && it.source.encounter) || "";
    const mid = inherit
      ? `<div class="svals inherit">属性继承<b>转化前的装备</b>，转化本身不改绿字`
        + (setName ? `<br>套装：<b>${esc(setName)}</b>` : "")
        + `</div>`
      : `<div class="svals">${statRows(shown)}</div>`;
    const diffs = isCur
      ? `<span class="hint">当前装备 · 装等 ${it.ilevel || "?"}</span>`
      : inherit
        ? `<span class="hint">看套装效果，绿字跟着你拿去转的那件走</span>`
        : diffRow(it, cur);
    return `<div class="card ${isCur ? "cur" : ""}" data-id="${it.id}" data-q="${it.quality ?? 1}">
      <div class="head">
        ${iconHtml(it.icon, "ic")}
        <div class="title">
          <div class="nm" style="color:${color}">${esc(it.name)}</div>
          ${it.name_en && it.name_en !== it.name
            ? `<div class="en">${esc(it.name_en)}</div>` : ""}
          ${badges.length ? `<div class="badges">${badges.join("")}</div>` : ""}
        </div>
        <div class="ilv">${it.ilevel || "?"}<small>装等</small></div>
      </div>
      ${srcHtml(it)}
      ${mid}
      <div class="diffs">${diffs}</div>
    </div>`;
  }).join("");

  box.querySelectorAll(".card").forEach(el => {
    const id = Number(el.dataset.id);
    const item = list.find(x => x.id === id);
    el.onmouseenter = (ev) => { showTip(item, ev); hoverPreview(item); };
    el.onmousemove = moveTip;
    el.onmouseleave = () => { hideTip(); clearHover(); };
    el.onclick = () => equipItem(item);
  });
}

