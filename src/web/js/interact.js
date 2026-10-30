/* interact.js

悬停预览、选部位、换上、撤销，以及装备详情浮层。 */

/* 悬停预览：把"换上之后属性变成多少"算出来，不落地 */
let hoverTimer = null, hoverSeq = 0;

function clearHover(){
  if (hoverTimer){ clearTimeout(hoverTimer); hoverTimer = null; }
  if (!state.hover) return;
  state.hover = null;
  renderStats(); renderDoll(); renderAvg();
}

function hoverPreview(item){
  if (!item) return;
  if (item.craft_kind) return;      // 制造业要选属性，没法直接预览
  if (hoverTimer) clearTimeout(hoverTimer);
  const seq = ++hoverSeq;
  hoverTimer = setTimeout(async () => {
    const r = await fetch("/api/preview", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({slot: state.slot, id: item.id, ilevel: state.ilvl}),
    });
    const data = await r.json();
    if (seq !== hoverSeq) return;   // 鼠标已经挪走，丢弃过期结果
    if (!r.ok) return;
    state.hover = {slot: state.slot, item, ilevel: data.ilevel,
                   totals: data.totals, delta: data.delta, avg: data.avg_ilevel};
    renderStats(); renderDoll(); renderAvg();
  }, 110);
}

/* ══════════ 交互 ══════════ */
async function selectSlot(key){
  clearHover(); hideTip();
  state.slot = key; state.filter = ""; state.kind = ""; $("search").value = "";
  state.ilvl = null;
  renderDoll();
  // 默认装等 = 这个部位身上的装等
  const cur = slotMap()[key];
  if (cur && cur.ilevel) state.ilvl = cur.ilevel;
  renderIlvlBtn();
  await loadCands();
}

async function loadCands(){
  $("cands").innerHTML = `<div class="empty-tip">加载中…</div>`;
  const q = new URLSearchParams({slot: state.slot});
  if (state.ilvl) q.set("ilevel", state.ilvl);
  const r = await fetch(`/api/candidates?${q}`);
  const data = await r.json();
  if (!r.ok){ $("cands").innerHTML = `<div class="empty-tip">${data.error || "加载失败"}</div>`; return; }
  state.cands = data.items || [];
  if (!state.ilvl && data.items.length && data.items[0].default_ilevel){
    state.ilvl = data.items[0].default_ilevel;
    renderIlvlBtn();
  }
  renderTabs(); renderCands();
}

/* 点卡片 = 直接换。要选属性的（制造业/装绑）先弹层，确认后同样直接换。 */
function equipItem(item){
  if (item.craft_kind){ openPicker(item); return; }
  doEquip(item, state.ilvl || item.default_ilevel, []);
}

async function doEquip(item, ilevel, crafted){
  const slot = state.slot;
  const before = (state.snap && state.snap.totals) || {};
  const r = await fetch("/api/equip", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot, id: item.id, ilevel, crafted}),
  });
  const data = await r.json();
  if (!r.ok){ alert(data.error || "换装失败"); return; }
  const after = data.totals || {};
  const d = {};
  for (const k of new Set([...Object.keys(before), ...Object.keys(after)])){
    d[k] = (after[k] || 0) - (before[k] || 0);
  }
  state.snap = data;
  state.hover = null;
  state.lastDelta = d;
  renderAll();
  if (slot) await loadCands();
  toast(slot, item, ilevel);
  if (state.deltaTimer) clearTimeout(state.deltaTimer);
  state.deltaTimer = setTimeout(() => {
    state.lastDelta = null;
    renderStats();
  }, 2600);
}

let toastTimer = null;
function toast(slot, item, ilevel){
  const el = $("toast");
  const cn = SLOT_CN2[slot] || slot;
  el.innerHTML = `${cn} 已换上 <b>${esc(item.name)}</b>`
    + (ilevel ? ` · 装等 ${ilevel}` : "")
    + `<span style="color:var(--dimmer);margin-left:8px">右键装备栏可还原</span>`;
  el.classList.add("on");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 2600);
}

async function resetSlot(key){
  const r = await fetch("/api/reset", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({slot: key}),
  });
  const data = await r.json();
  if (!r.ok){ alert(data.error || "还原失败"); return; }
  state.snap = data;
  state.hover = null;
  renderAll();
  if (state.slot) await loadCands();
}

async function undo(){
  const r = await fetch("/api/undo", {method:"POST"});
  const data = await r.json();
  if (!r.ok){ alert(data.error || "没有可撤销的操作"); return; }
  state.snap = data;
  state.hover = null;
  renderAll();
  if (state.slot) await loadCands();
}

/* ══════════ 装备详情 ══════════ */
function showItemTip(it, ev){
  if (!it) return;
  const color = QCOLOR[it.quality] || "var(--q1)";
  const v = it.stat_values || {};
  const lines = [];
  for (const st of (it.stats || [])){
    const label = STAT_CN[st.code] || st.label || st.code;
    if (st.optional && v[st.code] == null){
      // 还没定属性的自选槽：code 还是占位 bonus1，没有数值可显示
      lines.push(`<div class="line opt"><span>${label}</span><span>由你指定</span></div>`);
    } else {
      const cls = SECONDARY.includes(st.code) ? "sec" : "";
      const mark = st.optional ? `<span class="om">自选</span>` : "";
      lines.push(`<div class="line ${cls}"><span>${label}${mark}</span>
        <span>${v[st.code] != null ? Math.round(v[st.code]).toLocaleString() : "—"}</span></div>`);
    }
  }
  if (!lines.length) lines.push(`<div class="line"><span style="color:var(--dim)">没有属性</span></div>`);

  // 宝石和附魔不并进上面的属性行：来源不同，调的时候思路也不同。
  // 而且附魔有一类是百分比特效（"暴击效果 +1%"），并进去会被当成 +1 暴击。
  const gemStats = it.gems || {};
  const enchStats = it.enchant || {};
  const enchTert = it.enchant_tertiary || {};
  // 固定顺序，别按 JSON 的键序：不然同一件装备的宝石会显示成"暴击 +16 精通 +7 敏捷 +32"，
  // 主属性跑到最后，读起来像算错了。和顶部属性栏用同一个顺序。
  const STAT_ORDER = ["agi", "str", "int", "sta", "crit", "haste", "mastery", "vers"];
  const byOrder = (a, b) => STAT_ORDER.indexOf(a) - STAT_ORDER.indexOf(b);
  const gemKeys = Object.keys(gemStats).sort(byOrder);
  const enchParts = [];
  for (const k of Object.keys(enchStats).sort(byOrder)) enchParts.push(`${STAT_CN[k] || k} +${Math.round(enchStats[k])}`);
  for (const k of Object.keys(enchTert)) enchParts.push(`${TERT_CN[k] || k} +${Math.round(enchTert[k])}`);
  const unknownEnch = it.enchant_known === false;

  if (gemKeys.length || enchParts.length || it.enchant_note || unknownEnch){
    lines.push(`<div class="sep"></div>`);
  }
  if (gemKeys.length){
    const txt = gemKeys.map(k => `${STAT_CN[k] || k} +${Math.round(gemStats[k])}`).join("  ");
    lines.push(`<div class="line extra gem"><span>宝石 ×${(it.gem_ids || []).length}</span><span>${txt}</span></div>`);
  }
  if (it.gem_missing && it.gem_missing.length){
    lines.push(`<div class="line extra gem"><span>未收录宝石</span><span>${it.gem_missing.join(" ")}</span></div>`);
  }
  if (enchParts.length){
    lines.push(`<div class="line extra ench"><span>附魔</span><span>${enchParts.join("  ")}</span></div>`);
  }
  if (it.enchant_note){
    lines.push(`<div class="line extra ench"><span>特效</span><span>${esc(it.enchant_note)}</span></div>`);
  }
  if (unknownEnch){
    lines.push(`<div class="line extra ench"><span>附魔</span><span style="color:var(--dimmer)">未收录，不计入</span></div>`);
  }

  const meta = [];
  if (it.armor_cn) meta.push(it.armor_cn);
  if (it.kind_cn) meta.push(it.kind_cn);
  if (it.is_reprint) meta.push("复刻副本");
  if (it.ilevel) meta.push(`装等 ${it.ilevel}`);

  const s = it.source;
  const srcLine = s && s.text
    ? `<div class="sep"></div><div class="src"><span class="kind ${s.kind}">${s.label}</span> ${esc(s.text)}</div>`
    : "";

  $("tip").innerHTML = `
    <div class="tn" style="color:${color}">${esc(it.name)}</div>
    ${it.name_en && it.name_en !== it.name
      ? `<div class="ten">${esc(it.name_en)}</div>` : ""}
    <div class="tsub">${meta.join(" · ")}</div>
    ${lines.join("")}
    ${srcLine}
    ${it.craft_kind ? `<div class="note">${it.kind_cn}属性可自选，点击会让你选</div>` : ""}`;
  $("tip").classList.add("on");
  moveTip(ev);
}

function showTip(item, ev){
  if (!item) return;
  showItemTip(item, ev);
  if (item.variant_count > 1 && item.variants && item.variants.length){
    const box = document.createElement("div");
    box.className = "src";
    box.style.marginTop = "6px";
    box.innerHTML = `<div style="color:var(--gold);margin-bottom:2px">同属性 ${item.variant_count} 款</div>`
      + item.variants.map(v => `<div class="tsub" style="margin:0">${esc(v)}</div>`).join("")
      + `<div class="note" style="margin-top:6px">属性完全一致，选任意一件结果相同</div>`;
    $("tip").appendChild(box);
    moveTip(ev);
  }
}

function moveTip(ev){
  const tip = $("tip");
  if (!tip.classList.contains("on")) return;
  const pad = 16, rect = tip.getBoundingClientRect();
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + rect.width > innerWidth - 8) x = ev.clientX - rect.width - pad;
  if (y + rect.height > innerHeight - 8) y = innerHeight - rect.height - 8;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}

function hideTip(){ $("tip").classList.remove("on"); }

