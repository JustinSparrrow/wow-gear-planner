/* boot.js

入口页（登录 / 注册 / 游客）与主界面的切换、事件绑定、启动。 */

/* ══════════ 入口：落地页 ↔ 主界面 ══════════

   三个判断，从上往下：
   - 带着登录 cookie 回来   → 直接进主界面
   - 这一趟已经点过游客     → 直接进主界面，刷新不再问一遍
   - 其它                    → 停在落地页，让人挑 登录 / 注册 / 游客

   游客标记放 sessionStorage 不放 localStorage：关掉标签页就该忘掉。
   放 localStorage 的话，第一次点了游客，往后永远看不到注册入口。 */
const GUEST_KEY = "wg.guest";
const ENTERED_KEY = "wg.entered";   // 这一趟已经进过主界面了

function showLanding(){
  const ld = $("landing");
  ld.hidden = false;
  // hidden 和 opacity 在同一帧里改的话没有过渡，要等一帧再摘 gone
  requestAnimationFrame(() => ld.classList.remove("gone"));
  $("app").hidden = true;
  state.snap = null; state.slot = null; state.hover = null;
  $("ld-err").textContent = "";
  setTimeout(() => { if (!$("ld-user").value) $("ld-user").focus(); }, 140);
}

async function enterApp(instant){
  $("app").hidden = false;
  const ld = $("landing");
  ld.classList.add("gone");
  if (instant) ld.hidden = true;
  else setTimeout(() => { ld.hidden = true; }, 460);
  await startApp();
}

/* 进主界面之后第一次取数。没角色就把导入框直接打开——
   让人自己去右上角找「导入 simc」，第一次用的人一定卡在这儿。

   游客第一次进来时故意不读服务端残留的那份 anon 会话：那可能是上一个
   人、或者昨天自己留下的装备，一开门就看到别人一身装备，会以为这工具
   在用他的数据。这一趟内刷新还是照常接上，标记存在 sessionStorage。 */
async function startApp(){
  const q = new URLSearchParams(location.search);
  // 带 ?slot= 进来的是深链接（分享"看这个部位的宝石"），不该再弹导入框拦一下
  const freshGuest = !state.user && !sessionStorage.getItem(ENTERED_KEY)
                     && !q.get("slot");
  sessionStorage.setItem(ENTERED_KEY, "1");
  if (freshGuest){
    state.snap = null; state.slot = null; state.hover = null;
    renderAll();
    $("mask-import").classList.add("on");
    return;
  }
  const r = await fetch("/api/snapshot");
  if (r.ok){
    state.snap = await r.json();
    state.slot = null; state.hover = null;
    renderAll();
    const want = q.get("slot");
    if (want) await selectSlot(want);
    const mode = q.get("mode");
    if (mode) setMode(mode);
    return;
  }
  state.snap = null;
  renderAll();
  $("mask-import").classList.add("on");
}

/* ── 落地页表单 ── */
let ldMode = "login";

function setLdMode(m){
  ldMode = m;
  document.querySelectorAll("#ld-tabs button").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === m));
  $("ld-submit").textContent = m === "login" ? "进 入" : "注 册";
  $("ld-pass").setAttribute("autocomplete",
    m === "login" ? "current-password" : "new-password");
  $("ld-err").textContent = "";
}

async function submitLanding(ev){
  ev.preventDefault();
  const err = $("ld-err"), btn = $("ld-submit");
  const username = $("ld-user").value.trim();
  const password = $("ld-pass").value;
  if (!username || !password){ err.textContent = "用户名和密码都要填"; return; }
  btn.disabled = true;
  try {
    const r = await fetch(ldMode === "login" ? "/api/login" : "/api/register", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json();
    if (!r.ok){ err.textContent = d.error || "没成功，再试一次"; return; }
    state.user = d.user;
    $("ld-pass").value = "";
    sessionStorage.removeItem(GUEST_KEY);      // 真登录了，游客身份就作废
    renderAcc();
    await refreshMe();
    await enterApp();
  } catch (e){
    err.textContent = "连不上本地服务，确认 python main.py --web 还在跑";
  } finally {
    btn.disabled = false;
  }
}

/* ══════════ 初始化 ══════════ */
function bindEvents(){
  document.querySelectorAll("#ld-tabs button").forEach(b => {
    b.onclick = () => setLdMode(b.dataset.mode);
  });
  $("ld-form").addEventListener("submit", submitLanding);
  $("ld-guest").onclick = async () => {
    sessionStorage.setItem(GUEST_KEY, "1");
    await enterApp();
  };

  document.querySelectorAll("#modes button").forEach(b => {
    b.onclick = () => setMode(b.dataset.mode);
  });

  $("btn-import").onclick = () => $("mask-import").classList.add("on");
  $("btn-close-import").onclick = () => $("mask-import").classList.remove("on");
  $("btn-load").onclick = submitImport;
  $("btn-sample").onclick = useSample;
  $("btn-undo").onclick = undo;
  $("btn-panel").onclick = openPanel;
  $("btn-save").onclick = openSave;
  $("btn-export").onclick = openExport;
  $("ex-copy").onclick = copyExport;
  $("ex-close").onclick = () => $("mask-export").classList.remove("on");
  $("btn-acc").onclick = () => (state.user ? openDrawer() : openAuth("login"));
  $("dw-close").onclick = closeDrawer;
  $("drawer-mask").onclick = closeDrawer;
  $("dw-logout").onclick = logout;
  document.querySelectorAll(".dw-tab").forEach(t => {
    t.onclick = () => { state.tab = t.dataset.tab; renderDrawer(); };
  });
  $("au-ok").onclick = submitAuth;
  $("au-switch").onclick = () => openAuth(authMode === "login" ? "register" : "login");
  $("au-cancel").onclick = () => $("mask-auth").classList.remove("on");
  for (const id of ["au-user", "au-pass"]){
    $(id).addEventListener("keydown", ev => { if (ev.key === "Enter") submitAuth(); });
  }
  $("sv-ok").onclick = submitSave;
  $("sv-cancel").onclick = () => $("mask-save").classList.remove("on");
  $("sv-name").addEventListener("keydown", ev => { if (ev.key === "Enter") submitSave(); });
  $("pn-ok").onclick = savePanel;
  $("pn-clear").onclick = async () => {
    const r = await fetch("/api/panel", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({panel: {}}),
    });
    const data = await r.json();
    if (!r.ok) return;
    state.snap = data; state.hover = null;
    $("mask-panel").classList.remove("on");
    renderAll();
    if (state.slot) await loadCands();
  };
  $("pn-cancel").onclick = () => $("mask-panel").classList.remove("on");

  $("btn-reset-all").onclick = async () => {
    if (!state.snap) return;
    const resp = await fetch("/api/reset-all", {method:"POST"});
    state.snap = await resp.json();
    state.hover = null; state.lastDelta = null;
    renderAll();
    if (state.slot) await loadCands();
  };

  $("ilvl-btn").onclick = (ev) => { ev.stopPropagation(); openIlvlPop(); };
  $("pk-ok").onclick = confirmPicker;
  $("pk-cancel").onclick = closePicker;
  $("search").oninput = (e) => { state.filter = e.target.value.trim(); renderCands(); };

  for (const id of ["mask-import","mask-picker","mask-panel","mask-auth",
                  "mask-save","mask-export"]){
    $(id).addEventListener("click", (ev) => {
      if (ev.target.id === id) $(id).classList.remove("on");
    });
  }
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest(".ilvl-wrap") && !ev.target.closest(".ilvl-pop")){
      $("ilvl-pop").classList.remove("on");
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if ($("mask-save").classList.contains("on")) return $("mask-save").classList.remove("on");
    if ($("mask-export").classList.contains("on")) return $("mask-export").classList.remove("on");
    if ($("mask-auth").classList.contains("on")) return $("mask-auth").classList.remove("on");
    if ($("drawer").classList.contains("on")) return closeDrawer();
    if ($("mask-panel").classList.contains("on")) return $("mask-panel").classList.remove("on");
    if ($("mask-picker").classList.contains("on")) return closePicker();
    if ($("mask-import").classList.contains("on")) return $("mask-import").classList.remove("on");
    if ($("ilvl-pop").classList.contains("on")) return $("ilvl-pop").classList.remove("on");
    clearHover();
  });
  $("cands").addEventListener("scroll", () => { hideTip(); clearHover(); });
}

async function boot(){
  let data = {};
  try {
    const r = await fetch("/api/bootstrap");
    data = await r.json();
  } catch (e){
    // 服务没起来也得把落地页画出来，否则打开就是一片黑，连哪儿错了都看不出来
  }
  $("season").textContent = data.season ? `${data.season} · ${data.build || ""}` : "";
  // 内测包的版本号放在落地页右下角。没有 VERSION 文件时（开发环境）就只显示 BUILD，
  // 不硬写死一串版本号——那样每次发版都要回来改代码。
  $("ld-ver").textContent = [
    data.version ? `内测版 ${data.version}` : "",
    data.build ? `BUILD ${data.build}` : "",
  ].filter(Boolean).join(" · ");
  state.user = data.user || null;
  renderAcc();
  if (state.user) await refreshMe();
  bindEvents();

  if (state.user || sessionStorage.getItem(GUEST_KEY) === "1"){
    return enterApp(true);
  }
  showLanding();
}

boot();
