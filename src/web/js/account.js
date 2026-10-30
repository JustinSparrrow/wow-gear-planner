/* account.js

登录注册、账号抽屉、历史 simc、配装存档。 */

/* ══════════ 账号 / 历史 / 配装 ══════════
   账号是为了"这套配装下次还能找回来"。所以真正有用的不是登录本身，
   而是两件事：导入过的 simc 自动留档、调好的装备能命名保存。 */

function renderAcc(){
  const u = state.user, b = $("btn-acc");
  if (u){
    b.classList.add("on");
    b.innerHTML = `<span class="dot"></span>${esc(u.username)}`;
    b.title = "我的配装与历史";
  } else {
    b.classList.remove("on");
    b.textContent = "登录";
    b.title = "现在游客状态，登录后可以保存配装";
  }
  // 游客点「存配装」会直接弹登录框。按钮不藏起来——藏了他就不知道
  // 这工具本来能存档，只会觉得功能少。
  const save = $("btn-save");
  if (save) save.title = u ? "" : "游客不能存档，点一下去登录";
}

async function refreshMe(){
  const r = await fetch("/api/me");
  const d = await r.json();
  state.user = d.user;
  state.me = {history: d.history || [], loadouts: d.loadouts || []};
  renderAcc();
  if ($("drawer").classList.contains("on")) renderDrawer();
}

/* ── 登录 / 注册 ── */
let authMode = "login";

function openAuth(mode){
  authMode = mode || "login";
  const login = authMode === "login";
  $("au-title").textContent = login ? "登录" : "注册";
  $("au-ok").textContent = login ? "登录" : "注册并登录";
  $("au-switch").textContent = login ? "去注册" : "已有账号，去登录";
  $("au-desc").innerHTML = login
    ? "登录后导入的 simc 会自动进历史，调好的配装也能命名存档。"
    : "用户名 + 密码即可，只存在本机 data/accounts.db。<br>密码用 PBKDF2 加盐存哈希，不存明文。";
  $("au-err").textContent = "";
  $("mask-auth").classList.add("on");
  setTimeout(() => $("au-user").focus(), 30);
}

async function submitAuth(){
  const username = $("au-user").value.trim();
  const password = $("au-pass").value;
  if (!username || !password){ $("au-err").textContent = "用户名和密码都要填"; return; }
  const r = await fetch(authMode === "login" ? "/api/login" : "/api/register", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({username, password}),
  });
  const d = await r.json();
  if (!r.ok){ $("au-err").textContent = d.error || "失败了"; return; }
  state.user = d.user;
  $("mask-auth").classList.remove("on");
  $("au-pass").value = "";
  // 服务端把未登录时那套装备过继给了账号，重新拉一次快照接上
  const snap = await fetch("/api/snapshot");
  if (snap.ok){ state.snap = await snap.json(); renderAll(); }
  await refreshMe();
  toastMsg(`已登录 ${d.user.username}`);
}

async function logout(){
  await fetch("/api/logout", { method:"POST" });
  state.user = null;
  state.me = {history:[], loadouts:[]};
  sessionStorage.removeItem(GUEST_KEY);
  sessionStorage.removeItem(ENTERED_KEY);
  closeDrawer(); renderAcc();
  $("mask-save").classList.remove("on");
  // 退出就回入口重新选身份，不留在主界面里当"半个游客"——
  // 停在主界面上，人分不清自己现在到底是登录态还是游客态
  showLanding();
}

/* ── 抽屉 ── */
function openDrawer(){
  if (!state.user){ openAuth("login"); return; }
  $("dw-who").textContent = state.user.username;
  $("drawer").classList.add("on");
  $("drawer-mask").classList.add("on");
  renderDrawer();
}

function closeDrawer(){
  $("drawer").classList.remove("on");
  $("drawer-mask").classList.remove("on");
}

function renderDrawer(){
  document.querySelectorAll(".dw-tab").forEach(t =>
    t.classList.toggle("on", t.dataset.tab === state.tab));
  const box = $("dw-body");
  if (state.tab === "loadouts") renderLoadouts(box);
  else renderHistory(box);
}

function renderLoadouts(box){
  const list = state.me.loadouts || [];
  if (!list.length){
    box.innerHTML = `<div class="dw-empty">还没有保存的配装。<br>
      调好装备后点右上角<b>存配装</b>，起个名字就留下了。<br>
      同名保存会覆盖旧的，不会堆一堆"未命名"。</div>`;
    return;
  }
  // 和 simc 历史一样按角色分组：一个人多个角色的存档混在一列里找不着
  const groups = new Map();
  list.forEach(l => {
    const key = l.char_name || "未命名角色";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(l);
  });
  box.innerHTML = `<div class="dw-hint">点一条载入 · 右上角 × 删除</div>` +
    [...groups.entries()].map(([name, rows]) => {
      const head = rows[0];
      return `<div class="dw-group">
        <div class="dw-group-h">
          <span class="cn">${esc(name)}</span>
          <span class="ct">${esc(head.class_cn || "")}</span>
          <span class="n">${rows.length} 套</span>
        </div>
        ${rows.map(l => `<div class="item-row" data-id="${l.id}">
          <div class="t">${esc(l.name)}</div>
          <div class="m">
            ${l.spec_cn ? `<span>${esc(l.spec_cn)}</span>` : ""}
            <span class="n">${(l.avg_ilevel || 0).toFixed(1)} 装等</span>
            <span>换了 ${l.change_count} 件</span>
            <span class="w">${fmtTime(l.updated_at)}</span>
          </div>
          ${l.note ? `<div class="note">${esc(l.note)}</div>` : ""}
          <span class="x" data-del="${l.id}">×</span>
        </div>`).join("")}
      </div>`;
    }).join("");

  box.querySelectorAll(".item-row").forEach(el => {
    el.onclick = () => loadLoadout(Number(el.dataset.id));
  });
  box.querySelectorAll(".x").forEach(x => {
    x.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm("删除这套配装？删了找不回来。")) return;
      const r = await fetch("/api/loadout/delete", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({id: Number(x.dataset.del)}),
      });
      const d = await r.json();
      if (r.ok){ state.me.loadouts = d.loadouts; renderDrawer(); }
    };
  });
}

function renderHistory(box){
  const list = state.me.history || [];
  if (!list.length){
    box.innerHTML = `<div class="dw-empty">还没有导入记录。<br>
      导入一次 simc，这里就会自动留一份，<br>以后随时能点回来。</div>`;
    return;
  }
  // 按角色分组：同一个人的兽王/射击/生存是三条不同记录，混在一列里
  // 等记录多了根本找不到"我上周那套生存猎"。
  const groups = new Map();
  list.forEach(h => {
    const key = h.char_name || "未命名角色";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(h);
  });
  box.innerHTML = `<div class="dw-hint">每次导入的原文都会留档，同一份配置只更新时间</div>` +
    [...groups.entries()].map(([name, rows]) => {
      const head = rows[0];
      return `<div class="dw-group">
        <div class="dw-group-h">
          <span class="cn">${esc(name)}</span>
          <span class="ct">${esc(head.class_cn || "")}</span>
          <span class="n">${rows.length} 条</span>
        </div>
        ${rows.map(h => `<div class="item-row" data-id="${h.id}">
          <div class="t">${esc(h.spec_cn || h.label || "未命名")}</div>
          <div class="m">
            <span class="n">${(h.avg_ilevel || 0).toFixed(1)} 装等</span>
            <span class="w">${fmtTime(h.updated_at)}</span>
          </div>
          <span class="x" data-del="${h.id}">×</span>
        </div>`).join("")}
      </div>`;
    }).join("");

  box.querySelectorAll(".item-row").forEach(el => {
    el.onclick = () => loadHistory(Number(el.dataset.id));
  });
  box.querySelectorAll(".x").forEach(x => {
    x.onclick = async (ev) => {
      ev.stopPropagation();
      const r = await fetch("/api/history/delete", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({id: Number(x.dataset.del)}),
      });
      const d = await r.json();
      if (r.ok){ state.me.history = d.history; renderDrawer(); }
    };
  });
}

async function applyLoadedSnap(d, label){
  const keep = state.slot;
  state.snap = d;
  state.cands = []; state.hover = null; state.lastDelta = null; state.slot = null;
  closeDrawer();
  renderAll();
  // 停在原来那个部位：载入完还得继续比装备，被踢回空白页很烦
  if (keep && slotMap()[keep]) await selectSlot(keep);
  const skipped = (d.loaded && d.loaded.skipped) || [];
  toastMsg(label + (skipped.length
    ? `，${skipped.length} 件没找到（库里可能已经没有这件了）` : ""));
}

async function loadLoadout(id){
  const r = await fetch("/api/loadout/load", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id}),
  });
  const d = await r.json();
  if (!r.ok){ alert(d.error || "载入失败"); return; }
  applyLoadedSnap(d, `已载入「${d.loaded.name}」`);
}

async function loadHistory(id){
  const r = await fetch("/api/history/load", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id}),
  });
  const d = await r.json();
  if (!r.ok){ alert(d.error || "载入失败"); return; }
  applyLoadedSnap(d, "已载入那次导入");
}

/* ── 保存配装 ── */
function openSave(){
  if (!state.user){ openAuth("login"); return; }
  if (!state.snap){ $("mask-import").classList.add("on"); return; }
  const d = new Date(), p = n => String(n).padStart(2, "0");
  const changed = (state.snap.changed_slots || []).length;
  $("sv-name").value = `${state.snap.name || "我的"} · ${d.getMonth() + 1}/${d.getDate()} `
    + `${p(d.getHours())}:${p(d.getMinutes())}`;
  $("sv-note").value = "";
  $("sv-err").textContent = "";
  $("sv-desc").innerHTML = `${esc(state.snap.class_cn)} · 平均装等 <b>${state.snap.avg_ilevel}</b>
    · 改动 ${changed} 件<br>
    存的是「导入原文 + 改动」，以后属性公式或装备库更新，载入时会按新数据重算。`;
  $("mask-save").classList.add("on");
  setTimeout(() => { $("sv-name").focus(); $("sv-name").select(); }, 30);
}

async function submitSave(){
  const name = $("sv-name").value.trim();
  if (!name){ $("sv-err").textContent = "起个名字吧，不然列表里认不出来"; return; }
  const r = await fetch("/api/loadout/save", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ name, note: $("sv-note").value.trim() }),
  });
  const d = await r.json();
  if (!r.ok){ $("sv-err").textContent = d.error || "保存失败"; return; }
  state.me.loadouts = d.loadouts;
  $("mask-save").classList.remove("on");
  toastMsg(d.replaced ? `已更新「${name}」` : `已保存「${name}」`);
}

function toastMsg(html){
  const el = $("toast");
  el.innerHTML = html;
  el.classList.add("on");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 2800);
}

