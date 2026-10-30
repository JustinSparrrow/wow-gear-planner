/* picker.js

三张要填东西的弹层：属性选择器、面板校准、导入 simc。 */

/* ══════════ 属性选择 ══════════ */
function openPicker(item){
  const shares = item.slot_shares || [];
  const n = shares.length;
  const total = shares.reduce((a,b) => a+b, 0) || 1;
  state.picker = {item, stats:[], ilevel: state.ilvl || item.default_ilevel || 334};

  $("pk-title").textContent = item.name;
  $("pk-desc").innerHTML = n === 1
    ? "这件装备有 <b>1</b> 个自选槽，选一个属性占满。"
    : `这件装备有 <b>${n}</b> 个自选槽，选两个属性。` +
      (item.craft_kind === "boe" ? "装绑一大一小，先选的占大槽。" : "制造业两槽平分。");
  $("pk-shares").textContent = n === 1
    ? `槽位 ${shares[0]}（100%）`
    : `槽位 ${shares[0]} : ${shares[1]}（${Math.round(shares[0]/total*100)}% : ${Math.round(shares[1]/total*100)}%）`;

  $("pk-opts").innerHTML = Object.entries(STAT_ID).map(([k]) =>
    `<div class="opt" data-stat="${k}">${STAT_CN[k]}</div>`).join("");
  $("pk-opts").querySelectorAll(".opt").forEach(el => {
    el.onclick = () => toggleOpt(el, n);
  });

  $("pk-ilevel").value = state.picker.ilevel;
  $("pk-ilvl-hint").textContent = item.craft_kind === "boe"
    ? "拍卖行上标多少填多少" : "按成品装等填";
  $("mask-picker").classList.add("on");
}

function toggleOpt(el, need){
  const picked = state.picker.stats, key = el.dataset.stat;
  const i = picked.indexOf(key);
  if (i >= 0) picked.splice(i, 1);
  else {
    if (picked.length >= need) picked.shift();
    picked.push(key);
  }
  $("pk-opts").querySelectorAll(".opt").forEach(o => {
    o.classList.toggle("on", picked.includes(o.dataset.stat));
  });
}

function closePicker(){ $("mask-picker").classList.remove("on"); state.picker = null; }

function confirmPicker(){
  const p = state.picker;
  if (!p) return;
  const need = (p.item.slot_shares || []).length;
  if (p.stats.length !== need){ alert(`要选 ${need} 个属性`); return; }
  const ilevel = parseInt($("pk-ilevel").value, 10);
  if (!ilevel || ilevel < 1){ alert("装等填错了"); return; }
  const crafted = p.stats.map(s => STAT_ID[s]);
  closePicker();
  doEquip(p.item, ilevel, crafted);
}

/* ══════════ 面板校准 ══════════
   simc 里只有装备列表，没有面板属性；而"装备相加"和真实面板差得很远
   （天赋、等级基础值都不在装备里）。所以让玩家把面板评级填一次，
   之后所有显示 = 角色基础 + 装备，数字就能和游戏里对上。 */
const PANEL_FIELDS = [["agi","敏捷"],["sta","耐力"],["crit","暴击"],
                      ["haste","急速"],["mastery","精通"],["vers","全能"]];
// 只有战斗等级才有百分比。主属性/耐力在游戏面板上不显示百分比
const PCT_FIELDS = [["crit","暴击"],["haste","急速"],["mastery","精通"],["vers","全能"]];

function openPanel(){
  const s = state.snap || {};
  const p = s.panel || {};
  const pct = s.panel_pct || {};
  $("panel-grid").innerHTML = PANEL_FIELDS.map(([k, cn]) => {
    // 没填过的项，用"当前显示值"当占位，用户一眼就知道该填多少
    const now = Math.round((s.totals || {})[k] || 0) || "";
    const v = p[k] != null ? Math.round(p[k]) : "";
    return `<label><span>${cn}</span>
      <input id="pn-${k}" type="number" value="${v}" placeholder="${now}"></label>`;
  }).join("");
  // 百分比：填了就按它反推这个专精的换算率（精通每专精差好几倍）
  $("panel-pct").innerHTML = PCT_FIELDS.map(([k, cn]) => {
    const now = s.rating_per_pct && s.rating_per_pct[k]
      ? ((s.totals[k] || 0) / s.rating_per_pct[k]).toFixed(2) : "";
    const v = pct[k] != null ? pct[k] : "";
    return `<label><span>${cn}%</span>
      <input id="pnp-${k}" type="number" step="0.01" value="${v}" placeholder="${now}"></label>`;
  }).join("");

  // 面板基准按专精分开存：同一职业换了专精，天赋和基础属性都不一样，
  // 沿用上一套会让所有绝对值都偏。这里把"这套属于哪个专精"说清楚。
  const cur = [s.class_cn, s.spec_cn].filter(Boolean).join(" · ") || "当前角色";
  const others = (s.panel_specs || []).filter(x => x.key !== s.panel_key);
  const lines = [`当前专精：<b>${esc(cur)}</b>`];
  lines.push(Object.keys(p).length
    ? "这个专精已经校准过，改完保存就更新它。"
    : "这个专精还没校准。不同专精的基础属性和天赋不同，<b>每个专精要各填一次</b>——不然换了专精，数字还是上一套的。"
      + (s.base_source === "class"
         ? "<br>现在顶部显示的是<b>职业基础</b>（来自客户端数据表，不含天赋），会比游戏里低一点。"
         : ""));
  if (others.length){
    lines.push("已校准的其它专精：" + others.map(x => esc(x.label)).join("、")
      + "（切过去会各自生效）");
  }
  $("pn-spec").innerHTML = lines.join("<br>");
  $("mask-panel").classList.add("on");
}

async function savePanel(){
  const panel = {}, pct = {};
  for (const [k] of PANEL_FIELDS){
    const v = parseFloat($("pn-" + k).value);
    if (v > 0) panel[k] = v;
  }
  for (const [k] of PCT_FIELDS){
    const v = parseFloat($("pnp-" + k).value);
    if (v > 0) pct[k] = v;
  }
  if (Object.keys(pct).length) panel.pct = pct;
  const r = await fetch("/api/panel", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({panel}),
  });
  const data = await r.json();
  if (!r.ok){ alert(data.error || "保存失败"); return; }
  state.snap = data;
  state.hover = null; state.lastDelta = null;
  $("mask-panel").classList.remove("on");
  renderAll();
  if (state.slot) await loadCands();
}

/* ══════════ 导入 ══════════ */
async function submitImport(){
  $("import-err").textContent = "";
  try{
    await loadProfile($("simc-input").value);
    $("mask-import").classList.remove("on");
    $("simc-input").value = "";
    // 登录状态下这次导入已经自动进历史，列表跟着更新
    if (state.user) await refreshMe();
  } catch (e){ $("import-err").textContent = e.message; }
}

async function useSample(){
  const r = await fetch("/api/sample");
  const data = await r.json();
  if (data.simc) $("simc-input").value = data.simc;
}


/* ══════════ 导出 simc ══════════
   换完装备得让 Raidbots 按这套跑一次，才知道"换了到底强多少"。
   导出的文本粘进 Quick Sim 即可；没换过的部位和导入原文一字不差，
   所以不会因为重新生成而丢掉套装转化的源装备 id 这类字段。 */
async function openExport(){
  const r = await fetch("/api/export-simc");
  const d = await r.json();
  if (!r.ok){ toastMsg(d.error || "导出失败"); return; }
  $("ex-text").value = d.simc || "";
  $("ex-meta").textContent = d.meta || "";
  // 打孔器开的孔：SimC 的 item 选项里没有 sockets=，孔的多少来自它自己的
  // 物品数据，所以这一段粘进 Raidbots 之后那个孔不会被算进去。
  // 不说清楚的话，本地显示 -16 暴击 +7 全能、Raidbots 跑出来却没变，人会以为工具算错了。
  const drilled = d.drilled || [];
  const notes = [];
  if (drilled.length){
    notes.push(`<b>${drilled.length} 件装备用了打孔器</b>（${drilled.map(k => SLOT_CN2[k] || k).join("、")}）：
      simc 格式里没有"额外插槽"这个概念，Raidbots 不会算这个孔，模拟结果会比这里少这点属性。`);
  }
  if (!d.checksum){
    // 改过装备就不带 `# Checksum` —— 它证明的是"插件原样输出"，改完就不成立了。
    // 说明一句，免得有人拿这段去 Top Gear 被拒还以为是导出的锅。
    notes.push(`已去掉 <code># Checksum</code>：那是插件"原样输出"的凭证，改过装备后就不该再带。粘进 Quick Sim 不受影响。`);
  }
  $("ex-warn").innerHTML = notes.join("<br>");
  $("ex-warn").style.display = notes.length ? "" : "none";
  $("ex-err").textContent = "";
  $("mask-export").classList.add("on");
}

async function copyExport(){
  const ta = $("ex-text"), err = $("ex-err");
  try {
    await navigator.clipboard.writeText(ta.value);
    const btn = $("ex-copy");
    btn.textContent = "已复制";
    setTimeout(() => { btn.textContent = "复制全文"; }, 1400);
    err.textContent = "";
  } catch (e){
    // 剪贴板权限被拒（或非安全上下文）时退化成"帮你全选"
    ta.focus(); ta.select();
    err.textContent = "自动复制被拦下了，已经全选，按 Ctrl+C";
  }
}
