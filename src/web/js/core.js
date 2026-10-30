/* core.js

全局常量、状态、小工具，以及渲染入口 renderAll。
必须最先加载——其余文件只在函数体里用它，加载时不执行。 */

const $ = id => document.getElementById(id);
const STAT_CN = {agi:"敏捷",str:"力量",int:"智力",sta:"耐力",
                 crit:"暴击",haste:"急速",mastery:"精通",vers:"全能"};
const STAT_ORDER = ["agi","str","int","sta","crit","haste","mastery","vers"];
const PRIMARY = ["agi","str","int"];
const SECONDARY = ["crit","haste","mastery","vers"];
// 第三属性。不进六属性面板，但附魔里一大半给的是这个，
// 用户得看得见"这个附魔给了 44 吸血"，而不是以为它没生效。
const TERT_CN = {leech:"吸血", avoid:"闪避", speed:"移速"};
const STAT_ID = {crit:32, haste:36, vers:40, mastery:49};
const QCOLOR = ["var(--q0)","var(--q1)","var(--q2)","var(--q3)","var(--q4)","var(--q5)","var(--q6)"];

/* 装备栏布局照抄游戏里的角色面板，顺序也是游戏里的顺序 */
const DOLL_LEFT = ["head","neck","shoulder","back","chest","wrist","hands"];
const DOLL_RIGHT = ["waist","legs","feet","finger1","finger2","trinket1","trinket2"];
const DOLL_BOTTOM = ["main_hand","off_hand"];
const SLOT_CN2 = {
  head:"头部", neck:"颈部", shoulder:"肩部", chest:"胸部", waist:"腰部", legs:"腿部",
  back:"背部", wrist:"手腕", hands:"手部", feet:"脚部",
  finger1:"戒指1", finger2:"戒指2", trinket1:"饰品1", trinket2:"饰品2",
  main_hand:"主手", off_hand:"副手",
};
const KIND_ORDER = ["raid","mplus","delve","catalyst","craft","prey"];
const KIND_CN = {raid:"团本", mplus:"M+", delve:"地下堡", catalyst:"套装转化",
                 craft:"制造业", prey:"猎杀"};

const state = {
  snap:null, slot:null, cands:[], filter:"", kind:"",
  ilvl:null, hover:null, picker:null, lastDelta:null,
  user:null, me:{history:[], loadouts:[]}, tab:"loadouts",
  // 右侧面板的三种模式：换装备 / 插宝石 / 附魔。
  // 三者共用同一个部位选中状态，所以换部位时模式保持不变——
  // 连着给几个部位插宝石比每次回到"装备"再切过来顺手。
  mode:"equip",
  gemIndex:0,          // 当前选中要插的第几个孔
  gemGroup:"",         // 宝石分类："" 全部 / haste / mastery / crit / vers / main
  gemOpts:null,        // 宝石选项，拉一次就够，全表才 75 条
  enchOpts:null, enchSlot:null,   // 附魔选项按部位缓存
  enchSlots:null,      // 各部位附魔条数，用来区分"没有"和"没收录"
};

/* ══════════ 载入 ══════════ */
async function loadProfile(text){
  const r = await fetch("/api/profile", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({simc: text}),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "载入失败");
  state.snap = data; state.slot = null; state.hover = null; state.cands = [];
  state.ilvl = null; state.lastDelta = null;
  renderAll();
  return data;
}

/* ══════════ 渲染 ══════════ */
function renderAll(){
  renderWho(); renderAvg(); renderStats(); renderDoll(); renderTabs();
  renderIlvlBtn(); renderCands();
}


function esc(t){
  return String(t).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}


function fmtTime(ts){
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const p = n => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  const day = `${d.getMonth() + 1}/${d.getDate()}`;
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  const diff = (now - d) / 86400000;
  if (diff < 7) return `${day} ${hm}`;
  return `${d.getFullYear()}/${day} ${hm}`;
}

