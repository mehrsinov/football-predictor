# -*- coding: utf-8 -*-
"""Generate the professional single-file web app (docs/index.html).

SofaScore-style Persian RTL analytics dashboard: today's matches, match detail
with probabilities + scoreline heatmap + full market menu + sources, team
comparison with client-side Poisson simulation, computed standings, search,
dark/light mode, glassmorphism. Data is embedded inline so the single HTML file
works on GitHub Pages, locally, and as a platform artifact with zero servers.
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "output")
DOCS = os.path.join(HERE, "..", "docs")


def build():
    data_path = os.path.join(OUT, "webapp_data.json")
    # فعلاً برای دیباگ، fallback به فایل قدیمی به‌طور پیش‌فرض خاموش است تا هر
    # خطایی در webapp_data.build() بلافاصله با traceback کامل دیده شود و برنامه
    # متوقف شود (باگ «گیر کردن روی دیتای قدیمی»). برای برگرداندن رفتار امنِ
    # production کافی است متغیر محیطی WEBAPP_ALLOW_STALE=1 تنظیم شود.
    allow_stale = os.environ.get("WEBAPP_ALLOW_STALE", "0") == "1"
    try:  # always rebuild with today's fresh pipeline outputs
        import webapp_data
        webapp_data.build()
    except Exception as ex:
        # خطای کامل و با جزئیات را روی stderr چاپ کن تا دقیقاً معلوم شود کدام
        # بخش از webapp_data.py شکست خورده (مثلاً ImportError مربوط به jdatetime).
        print(
            "\n" + "=" * 72 +
            "\n[site_gen] webapp_data.build() FAILED — دیتای داشبورد ساخته نشد."
            f"\n[site_gen] نوع خطا: {type(ex).__name__}: {ex}"
            "\n" + "=" * 72,
            file=sys.stderr,
        )
        traceback.print_exc()
        sys.stderr.flush()

        if not allow_stale:
            # حالت دیباگ: خطای اصلی را بالا بده تا پایپ‌لاین با علت واقعی
            # (نه یک fallback خاموش) متوقف شود.
            raise
        if not os.path.exists(data_path):
            # حتی در حالت stale هم اگر فایل قدیمی وجود ندارد، چاره‌ای جز
            # شکست نیست.
            print(
                "[site_gen] WEBAPP_ALLOW_STALE=1 اما هیچ فایل قدیمی "
                f"({data_path}) وجود ندارد؛ ادامه ممکن نیست.",
                file=sys.stderr,
            )
            raise
        print(
            "\n[site_gen] هشدار جدی: WEBAPP_ALLOW_STALE=1 فعال است — از دیتای "
            f"قدیمی {data_path} استفاده می‌شود و تاریخ داشبورد به‌روزرسانی "
            "نخواهد شد!\n",
            file=sys.stderr,
        )
    with open(data_path, encoding="utf-8") as f:
        data_json = f.read()

    # merge assistant-layer files into the single data blob
    blob = json.loads(data_json)
    for name, key in (("curated_picks.json", "curated"), ("ai_brief.json", "ai")):
        p = os.path.join(OUT, name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    blob[key] = json.load(f)
            except Exception:
                pass
    data_json = json.dumps(blob, ensure_ascii=False, separators=(",", ":"))

    html = HTML_TEMPLATE.replace("__DATA__", data_json)
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"web app written: docs/index.html ({os.path.getsize(out)//1024} KB)")


HTML_TEMPLATE = r'''<!DOCTYPE html><html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>⚽ پیش‌بین فوتبال — داشبورد تحلیل</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;600;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg1:#070d1a;--bg2:#0c1526;--glass:rgba(255,255,255,.055);--glass2:rgba(255,255,255,.09);
  --bd:rgba(255,255,255,.09);--txt:#eaf1fb;--mut:#8fa3c4;--acc:#22c55e;--acc2:#38bdf8;
  --gold:#fbbf24;--red:#f87171;--vio:#c084fc;--shadow:0 8px 32px rgba(0,0,0,.35);
}
html[data-theme="light"]{
  --bg1:#eef3fa;--bg2:#e2eaf6;--glass:rgba(255,255,255,.65);--glass2:rgba(255,255,255,.9);
  --bd:rgba(15,40,80,.1);--txt:#0e1a2e;--mut:#5b6d8c;--shadow:0 8px 24px rgba(30,60,120,.12);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Vazirmatn,Tahoma,sans-serif;color:var(--txt);line-height:1.7;min-height:100vh;
  background:radial-gradient(1200px 600px at 85% -5%,rgba(34,197,94,.13),transparent 60%),
             radial-gradient(1000px 500px at -10% 30%,rgba(56,189,248,.11),transparent 55%),
             linear-gradient(165deg,var(--bg1),var(--bg2));background-attachment:fixed}
.wrap{max-width:1080px;margin:0 auto;padding:0 14px 80px}
/* header */
header{position:sticky;top:0;z-index:50;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  background:linear-gradient(180deg,rgba(7,13,26,.75),rgba(7,13,26,.45));border-bottom:1px solid var(--bd)}
html[data-theme="light"] header{background:linear-gradient(180deg,rgba(255,255,255,.8),rgba(255,255,255,.5))}
.hrow{max-width:1080px;margin:0 auto;padding:12px 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.brand{font-weight:900;font-size:1.15rem;display:flex;align-items:center;gap:8px;white-space:nowrap}
.brand .dot{width:9px;height:9px;border-radius:50%;background:var(--acc);box-shadow:0 0 12px var(--acc);animation:pulse 2.2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.date{color:var(--mut);font-size:.78rem}
.search{flex:1;min-width:150px;position:relative}
.search input{width:100%;padding:9px 38px 9px 12px;border-radius:12px;border:1px solid var(--bd);
  background:var(--glass);color:var(--txt);font-family:inherit;font-size:.9rem;outline:none;transition:.25s}
.search input:focus{border-color:var(--acc2);box-shadow:0 0 0 3px rgba(56,189,248,.15)}
.search .ic{position:absolute;right:12px;top:50%;transform:translateY(-50%);opacity:.55}
.tbtn{border:1px solid var(--bd);background:var(--glass);color:var(--txt);border-radius:12px;
  padding:8px 12px;cursor:pointer;font-family:inherit;font-size:1rem;transition:.25s}
.tbtn:hover{background:var(--glass2)}
/* tabs */
nav{display:flex;gap:8px;max-width:1080px;margin:14px auto 0;padding:0 14px;overflow-x:auto;scrollbar-width:none}
nav::-webkit-scrollbar{display:none}
.tab{border:1px solid var(--bd);background:var(--glass);color:var(--mut);padding:8px 18px;border-radius:14px;
  cursor:pointer;font-family:inherit;font-weight:600;font-size:.9rem;white-space:nowrap;transition:.25s}
.tab.on{color:#08120a;background:linear-gradient(135deg,var(--acc),#4ade80);border-color:transparent;box-shadow:0 4px 18px rgba(34,197,94,.35)}
/* stat chips */
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 4px}
.chip{background:var(--glass);border:1px solid var(--bd);border-radius:12px;padding:6px 12px;font-size:.78rem;color:var(--mut);backdrop-filter:blur(10px)}
.chip b{color:var(--acc);font-size:.95rem;margin-left:4px}
/* cards */
.lgroup{margin-top:22px;animation:fadeUp .5s ease both}
.lgroup h3{font-size:.95rem;color:var(--mut);font-weight:700;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.lgroup h3::before{content:"";width:4px;height:16px;border-radius:3px;background:linear-gradient(180deg,var(--acc),var(--acc2))}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(305px,1fr));gap:12px}
.card{background:var(--glass);border:1px solid var(--bd);border-radius:18px;padding:14px;cursor:pointer;
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);box-shadow:var(--shadow);
  transition:transform .22s,background .22s,border-color .22s;animation:fadeUp .5s ease both}
.card:hover{transform:translateY(-3px);background:var(--glass2);border-color:rgba(34,197,94,.35)}
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.mrow{display:flex;justify-content:space-between;align-items:center;gap:8px}
.tname{font-weight:700;font-size:.95rem;flex:1}
.tname.away{text-align:left}
.timebx{font-size:.72rem;color:var(--mut);background:var(--glass2);border-radius:8px;padding:2px 8px;white-space:nowrap}
.pbar{display:flex;height:9px;border-radius:6px;overflow:hidden;margin:10px 0 4px;background:rgba(255,255,255,.06)}
.pbar div{transition:width .6s ease}
.pbar .h{background:linear-gradient(90deg,#16a34a,#22c55e)}.pbar .d{background:#64748b}.pbar .a{background:linear-gradient(90deg,#0ea5e9,#38bdf8)}
.plabels{display:flex;justify-content:space-between;font-size:.72rem;color:var(--mut)}
.pickline{margin-top:9px;font-size:.82rem;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.badge{border-radius:9px;padding:2px 9px;font-size:.72rem;background:var(--glass2);border:1px solid var(--bd)}
.badge.gold{color:#000;background:var(--gold);border:none;font-weight:800}
.badge.inj{color:#fca5a5}
.badge.src{color:var(--acc)}
/* detail */
.back{cursor:pointer;color:var(--acc2);font-size:.88rem;margin:16px 0 6px;display:inline-flex;gap:6px;align-items:center}
.hero{background:var(--glass);border:1px solid var(--bd);border-radius:22px;padding:22px;text-align:center;
  backdrop-filter:blur(16px);box-shadow:var(--shadow);animation:fadeUp .4s both}
.hero .lg{color:var(--mut);font-size:.8rem}
.hero .teams{display:flex;align-items:center;justify-content:center;gap:14px;margin:10px 0;flex-wrap:wrap}
.hero .teams b{font-size:1.25rem;font-weight:900}
.hero .vs{color:var(--mut);font-weight:300}
.hero .xg{font-size:.82rem;color:var(--mut)}
.donut{display:flex;justify-content:center;gap:18px;margin-top:14px;flex-wrap:wrap}
.dn{text-align:center}
.dn svg{transform:rotate(-90deg)}
.dn .lbl{font-size:.75rem;color:var(--mut)}
.dn .val{font-weight:900;font-size:1.05rem;margin-top:-46px;margin-bottom:22px}
.sect{background:var(--glass);border:1px solid var(--bd);border-radius:18px;padding:16px;margin-top:14px;
  backdrop-filter:blur(14px);animation:fadeUp .5s both}
.sect h4{font-size:.95rem;margin-bottom:12px;display:flex;gap:8px;align-items:center}
.sect h4::before{content:"";width:4px;height:14px;border-radius:3px;background:linear-gradient(180deg,var(--acc2),var(--vio))}
table{width:100%;border-collapse:collapse;font-size:.83rem}
td,th{padding:7px 8px;border-bottom:1px solid var(--bd);text-align:right}
th{color:var(--mut);font-weight:600;font-size:.75rem}
tr:last-child td{border:0}
.fair{color:var(--acc2);font-weight:700}
.mkodds{color:var(--gold);font-weight:700}
.ev-pos{color:var(--acc);font-weight:700}.ev-neg{color:var(--mut)}
.srcs{font-size:.7rem;color:var(--mut)}
/* heatmap */
.hm{display:grid;grid-template-columns:auto repeat(7,1fr);gap:3px;max-width:430px;margin:0 auto}
.hm div{aspect-ratio:1;display:grid;place-items:center;font-size:.66rem;border-radius:6px;color:var(--mut)}
.hm .cell{color:#eaf1fb;font-weight:600;transition:transform .15s}
.hm .cell:hover{transform:scale(1.12);z-index:2}
.hm .ax{background:transparent;font-weight:700}
/* form chips */
.frm{display:flex;gap:5px}
.frm span{width:24px;height:24px;border-radius:7px;display:grid;place-items:center;font-size:.7rem;font-weight:800;color:#fff}
.frm .W{background:#16a34a}.frm .D{background:#64748b}.frm .L{background:#dc2626}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.duo{grid-template-columns:1fr}}
.statbar{display:flex;align-items:center;gap:8px;font-size:.8rem;margin:6px 0}
.statbar .nm{width:110px;color:var(--mut);font-size:.74rem}
.statbar .tr{flex:1;height:8px;background:rgba(255,255,255,.07);border-radius:5px;overflow:hidden;display:flex}
.statbar .fl{background:linear-gradient(90deg,var(--acc),#4ade80);height:100%;border-radius:5px}
.statbar .fl.b{background:linear-gradient(90deg,var(--acc2),#7dd3fc)}
/* compare */
select{width:100%;padding:10px;border-radius:12px;border:1px solid var(--bd);background:var(--glass);
  color:var(--txt);font-family:inherit;font-size:.9rem;outline:none}
option{background:var(--bg2);color:var(--txt)}
.cmpbtn{margin-top:12px;width:100%;padding:12px;border:none;border-radius:14px;cursor:pointer;font-family:inherit;
  font-weight:800;font-size:1rem;color:#08120a;background:linear-gradient(135deg,var(--acc),#4ade80);transition:.25s}
.cmpbtn:hover{filter:brightness(1.1)}
.note{font-size:.75rem;color:var(--mut);margin-top:8px}
.foot{margin-top:26px;padding:16px;background:var(--glass);border:1px solid var(--bd);border-radius:16px;color:var(--mut);font-size:.8rem}
.foot b{color:var(--gold)}
.empty{color:var(--mut);text-align:center;padding:40px 0}
/* assistant feed */
.pickcard{margin-top:12px}
.rk2{width:30px;height:30px;border-radius:10px;background:linear-gradient(135deg,var(--gold),#f59e0b);color:#000;
  font-weight:900;display:grid;place-items:center;flex-shrink:0}
.pkpick{font-size:1.05rem;font-weight:800}
.pkp{color:var(--acc);font-size:1.15rem;margin-right:4px}
.pkmatch{color:var(--mut);font-size:.85rem;margin-top:2px}
.lg2{color:var(--mut);font-size:.75rem;font-weight:400}
.rsn{margin:10px 24px 0 0;font-size:.84rem;line-height:1.9}
.rsn li{list-style:none;position:relative;padding-right:18px}
.rsn li::before{content:"◂";position:absolute;right:0;color:var(--acc)}
.aibox{margin-top:10px;padding:10px 12px;border-radius:12px;font-size:.83rem;line-height:1.8;
  background:linear-gradient(135deg,rgba(192,132,252,.1),rgba(56,189,248,.08));border:1px solid rgba(192,132,252,.25)}
.aibrief{border-color:rgba(192,132,252,.3);font-size:.9rem;margin-top:16px}
.mixbox{background:var(--glass);border:1px dashed var(--gold);border-radius:16px;padding:13px 15px;margin-top:12px;backdrop-filter:blur(12px)}
.mixhd{font-weight:800;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.mixlegs{display:flex;flex-direction:column;gap:3px;margin-top:7px;font-size:.82rem;color:var(--mut)}
</style></head><body>
<header><div class="hrow">
  <div class="brand"><span class="dot"></span>پیش‌بین فوتبال</div>
  <div class="date" id="hdate"></div>
  <div class="search"><input id="q" placeholder="جستجوی تیم یا لیگ…"><span class="ic">🔍</span></div>
  <button class="tbtn" id="theme" title="حالت روشن/تاریک">🌙</button>
</div></header>
<nav>
  <button class="tab on" data-v="picks">🎯 پیشنهادهای امروز</button>
  <button class="tab" data-v="home">⚽ همه بازی‌ها</button>
  <button class="tab" data-v="cmp">⚔️ مقایسه تیم‌ها</button>
  <button class="tab" data-v="std">📊 جدول لیگ‌ها</button>
  <button class="tab" data-v="tr">📏 شفافیت</button>
</nav>
<div class="wrap"><div id="view"></div></div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('DATA').textContent);
const V = document.getElementById('view');
const fmtP = p => Math.round(p*100)+'٪';
const esc = s => String(s??'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('hdate').textContent = D.meta.date_fa + ' · ' + (D.meta.date||'');

/* theme (localStorage is unavailable in sandboxed iframes -> safe wrappers) */
const store = {get(k){try{return localStorage.getItem(k)}catch(e){return null}},
               set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const T = document.getElementById('theme');
const setTheme = t => {document.documentElement.dataset.theme=t;store.set('fbTheme',t);T.textContent=t==='light'?'☀️':'🌙';};
setTheme(store.get('fbTheme')||'dark');
T.onclick = ()=> setTheme(document.documentElement.dataset.theme==='light'?'dark':'light');

/* tabs */
let cur='picks', q='';
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); cur=b.dataset.v; render();
});
document.getElementById('q').oninput = e=>{q=e.target.value.trim().toLowerCase(); if(cur!=='home'){cur='home';document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.v==='home'));} render();};

/* ---------- home ---------- */
function chips(){
  const b=D.meta.backtest||{};
  return `<div class="chips">
    <div class="chip">بازی امروز<b>${D.meta.n_fixtures}</b></div>
    <div class="chip">تیپ جمع‌شده<b>${D.meta.n_tips}</b></div>
    <div class="chip">گزینه تحلیلی<b>${D.meta.n_options}</b></div>
    ${b.acc_at_conf_70?`<div class="chip">دقت در اطمینان ۷۰٪+<b>${fmtP(b.acc_at_conf_70.accuracy)}</b></div>`:''}
  </div>`;
}
function matchCard(f){
  const bl=f.blend||{p_home:f.model.p_home,p_draw:f.model.p_draw,p_away:f.model.p_away};
  const best=(f.options&&f.options[0])||null;
  const inj=f.injuries||{};
  return `<div class="card" onclick="openMatch(${f.id})">
    <div class="mrow"><span class="tname">${esc(f.home)}</span><span class="timebx">${esc(f.time||'—')}</span><span class="tname away">${esc(f.away)}</span></div>
    <div class="pbar"><div class="h" style="width:${bl.p_home*100}%"></div><div class="d" style="width:${bl.p_draw*100}%"></div><div class="a" style="width:${bl.p_away*100}%"></div></div>
    <div class="plabels"><span>برد ${fmtP(bl.p_home)}</span><span>مساوی ${fmtP(bl.p_draw)}</span><span>برد ${fmtP(bl.p_away)}</span></div>
    <div class="pickline">
      ${best?`<span class="badge gold">🎯 ${esc(lblOf(best))} ${fmtP(best.p)}</span>`:''}
      ${best&&best.n_src?`<span class="badge src">🤝 ${best.n_src} منبع</span>`:''}
      ${(inj.home||inj.away)?`<span class="badge inj">🚑 ${(inj.home||0)+(inj.away||0)} مصدوم</span>`:''}
      ${f.model.known_teams?'':'<span class="badge">⚠️ داده کم</span>'}
    </div></div>`;
}
const MK={'1X2.1':'برد میزبان','1X2.X':'مساوی','1X2.2':'برد مهمان','DC.1X':'میزبان نمی‌بازد','DC.X2':'مهمان نمی‌بازد','DC.12':'مساوی نمی‌شود','OU15.Over':'بالای ۱.۵ گل','OU15.Under':'زیر ۱.۵ گل','OU25.Over':'بالای ۲.۵ گل','OU25.Under':'زیر ۲.۵ گل','OU35.Over':'بالای ۳.۵ گل','OU35.Under':'زیر ۳.۵ گل','BTTS.Yes':'هر دو گل می‌زنند','BTTS.No':'هر دو گل نمی‌زنند'};
const lblOf = o => o.label_fa || MK[o.market+'.'+o.pick] || (o.market+' '+o.pick);
function home(){
  let fx=D.fixtures;
  if(q) fx=fx.filter(f=>(f.home+f.away+f.league).toLowerCase().includes(q));
  if(!fx.length) return chips()+`<div class="empty">چیزی پیدا نشد ⚽</div>`;
  const groups={};
  fx.forEach(f=>{(groups[f.league]=groups[f.league]||[]).push(f)});
  return chips()+Object.entries(groups).map(([lg,list])=>
    `<div class="lgroup"><h3>${esc(lg)} <span style="color:var(--mut);font-weight:400">(${list.length})</span></h3>
     <div class="grid">${list.map(matchCard).join('')}</div></div>`).join('');
}

/* ---------- match detail ---------- */
window.openMatch = id=>{cur='match:'+id; render(); scrollTo({top:0,behavior:'smooth'});};
function donut(p,color,label){
  const r=26,c=2*Math.PI*r;
  return `<div class="dn"><svg width="70" height="70"><circle cx="35" cy="35" r="${r}" fill="none" stroke="rgba(128,150,190,.18)" stroke-width="8"/>
    <circle cx="35" cy="35" r="${r}" fill="none" stroke="${color}" stroke-width="8" stroke-linecap="round"
    stroke-dasharray="${c*p} ${c}"/></svg><div class="val">${fmtP(p)}</div><div class="lbl">${label}</div></div>`;
}
function heat(mx){
  if(!mx) return '';
  let mp=0; mx.forEach(r=>r.forEach(v=>mp=Math.max(mp,v)));
  let h='<div class="hm"><div class="ax"></div>';
  for(let b=0;b<7;b++) h+=`<div class="ax">${b}</div>`;
  for(let a=0;a<7;a++){
    h+=`<div class="ax">${a}</div>`;
    for(let b=0;b<7;b++){
      const v=mx[a][b], t=Math.pow(v/mp,.6);
      h+=`<div class="cell" style="background:rgba(34,197,94,${(t*.85).toFixed(2)})" title="${a}-${b}: ${(v*100).toFixed(1)}%">${v>=0.02?Math.round(v*100):''}</div>`;
    }
  }
  return h+'</div><div class="note" style="text-align:center">سطر: گل میزبان · ستون: گل مهمان · اعداد: درصد احتمال</div>';
}
function formChips(fm){
  if(!fm||!fm.length) return '<span class="note">داده‌ای نیست</span>';
  return `<div class="frm">${fm.map(m=>`<span class="${m.res}" title="${esc(m.vs)} ${m.score}">${{W:'ب',D:'م',L:'ش'}[m.res]}</span>`).join('')}</div>`;
}
function statDuo(a,b,name,va,vb){
  if(va==null&&vb==null) return '';
  const mx=Math.max(va||0,vb||0)||1;
  return `<div class="statbar"><span class="nm">${name}</span>
    <div class="tr" style="direction:ltr"><div class="fl" style="width:${(va||0)/mx*100}%"></div></div><b style="font-size:.78rem;width:34px">${va??'—'}</b>
    <div class="tr"><div class="fl b" style="width:${(vb||0)/mx*100}%"></div></div><b style="font-size:.78rem;width:34px">${vb??'—'}</b></div>`;
}
function matchView(id){
  const f=D.fixtures.find(x=>x.id===id); if(!f) return home();
  const bl=f.blend||{p_home:f.model.p_home,p_draw:f.model.p_draw,p_away:f.model.p_away};
  const th=(D.teams[f.league]||{})[f.home]||{}, ta=(D.teams[f.league]||{})[f.away]||{};
  const menu=(f.menu||[]).slice(0,18);
  const srcOpts=f.options||[];
  const findSrc=(mk,pk)=>{const o=srcOpts.find(o=>o.market===mk&&o.pick===pk);return o?o:null;};
  const rows=menu.map(o=>{
    const s=findSrc(o.market,o.pick);
    return `<tr><td>${esc(o.label_fa)}</td><td><b>${fmtP(o.p)}</b></td><td class="fair">${o.fair_odds}</td>
      <td>${s&&s.odds?`<span class="mkodds">${s.odds}</span>${s.ev!=null?` <span class="${s.ev>=0.05?'ev-pos':'ev-neg'}">${s.ev>=0?'+':''}${Math.round(s.ev*100)}٪${s.value?' 💎':''}</span>`:''}`:'—'}</td>
      <td class="srcs">${s&&s.sources&&s.sources.length?('🤝 '+s.sources.slice(0,3).map(esc).join('، ')+(s.n_src>3?' +'+(s.n_src-3):'')):'—'}</td></tr>`;
  }).join('');
  const h2h=(f.h2h||[]).map(m=>`<tr><td>${m.date}</td><td>${esc(m.home)}</td><td><b>${m.score}</b></td><td>${esc(m.away)}</td></tr>`).join('');
  const st=th.stats||{}, sa=ta.stats||{};
  const statsHtml=[statDuo(f.home,f.away,'شوت (میانگین)',st.shots,sa.shots),
    statDuo(f.home,f.away,'شوت در چارچوب',st.sot,sa.sot),
    statDuo(f.home,f.away,'کرنر',st.corners,sa.corners),
    statDuo(f.home,f.away,'کارت زرد',st.yellows,sa.yellows)].join('');
  return `<div class="back" onclick="cur='home';render()">→ برگشت به بازی‌ها</div>
  <div class="hero"><div class="lg">${esc(f.league)} · ${esc(f.time||'')} · ${f.date}</div>
    <div class="teams"><b>${esc(f.home)}</b><span class="vs">vs</span><b>${esc(f.away)}</b></div>
    <div class="xg">xG مدل: ${f.model.xg_home} — ${f.model.xg_away}${f.model.known_teams?'':' · ⚠️ داده یک تیم محدود'}</div>
    <div class="donut">${donut(bl.p_home,'#22c55e','برد '+esc(f.home))}${donut(bl.p_draw,'#94a3b8','مساوی')}${donut(bl.p_away,'#38bdf8','برد '+esc(f.away))}</div>
    ${(f.injuries&&(f.injuries.home||f.injuries.away))?`<div class="note">🚑 مصدوم/محروم: ${f.injuries.home?'میزبان '+f.injuries.home:''} ${f.injuries.away?'· مهمان '+f.injuries.away:''}</div>`:''}
  </div>
  <div class="duo">
    <div class="sect"><h4>فرم ۵ بازی اخیر — ${esc(f.home)}</h4>${formChips(th.form)}</div>
    <div class="sect"><h4>فرم ۵ بازی اخیر — ${esc(f.away)}</h4>${formChips(ta.form)}</div>
  </div>
  <div class="sect"><h4>🗺️ نقشه حرارتی نتایج (Heatmap)</h4>${heat(f.matrix)}</div>
  <div class="sect"><h4>🎯 همه آپشن‌ها (${menu.length} از ${f.menu?f.menu.length:0}) — با ضریب منصفانه و منابع</h4>
    <div style="overflow-x:auto"><table><tr><th>آپشن</th><th>احتمال</th><th>ضریب منصفانه</th><th>ضریب بازار / ارزش</th><th>منابع پیشنهاددهنده</th></tr>${rows}</table></div>
    <div class="note">ضریب منصفانه = ۱ ÷ احتمال؛ هر ضریب بازار بالاتر از آن یعنی ارزش مثبت.</div></div>
  ${statsHtml?`<div class="sect"><h4>📊 مقایسه آماری (میانگین ۲۰ بازی اخیر)</h4><div class="note" style="margin:0 0 8px">راست: ${esc(f.home)} · چپ: ${esc(f.away)}</div>${statsHtml}</div>`:''}
  ${h2h?`<div class="sect"><h4>🔄 رودررو (۵ دیدار اخیر)</h4><table>${h2h}</table></div>`:''}`;
}

/* ---------- compare (client-side Poisson simulation) ---------- */
function pois(k,l){let f=1;for(let i=2;i<=k;i++)f*=i;return Math.exp(-l)*Math.pow(l,k)/f;}
function simulate(lg,h,a){
  const P=D.league_params[lg], th=D.teams[lg][h], ta=D.teams[lg][a];
  const lam=Math.min(Math.exp(th.atk+ta.dfn+P.home_adv)*P.avg_goals,8);
  const mu=Math.min(Math.exp(ta.atk+th.dfn)*P.avg_goals,8);
  let m=[],s=0;
  for(let i=0;i<8;i++){m[i]=[];for(let j=0;j<8;j++){let v=pois(i,lam)*pois(j,mu);
    if(i===0&&j===0)v*=1-lam*mu*P.rho; if(i===0&&j===1)v*=1+lam*P.rho;
    if(i===1&&j===0)v*=1+mu*P.rho; if(i===1&&j===1)v*=1-P.rho;
    m[i][j]=Math.max(v,0); s+=m[i][j];}}
  let ph=0,pd=0,pa=0,o25=0,btts=0;
  for(let i=0;i<8;i++)for(let j=0;j<8;j++){const v=m[i][j]/s;
    if(i>j)ph+=v; else if(i===j)pd+=v; else pa+=v;
    if(i+j>=3)o25+=v; if(i>0&&j>0)btts+=v;}
  return{lam,mu,ph,pd,pa,o25,btts};
}
function cmp(){
  const lgs=Object.keys(D.teams).sort();
  const opts=lgs.map(l=>`<option>${esc(l)}</option>`).join('');
  return `<div class="sect" style="margin-top:18px"><h4>⚔️ مقایسه و شبیه‌سازی دو تیم (هم‌لیگ)</h4>
    <div class="duo"><div><label class="note">لیگ</label><select id="clg">${opts}</select></div><div></div></div>
    <div class="duo" style="margin-top:10px">
      <div><label class="note">تیم میزبان</label><select id="cth"></select></div>
      <div><label class="note">تیم مهمان</label><select id="cta"></select></div></div>
    <button class="cmpbtn" onclick="runCmp()">شبیه‌سازی مسابقه ⚡</button>
    <div id="cout"></div>
    <div class="note">شبیه‌سازی با همان مدل دیکسون-کولز، این‌بار زنده در مرورگر تو. مقایسه فقط بین تیم‌های یک لیگ معنی‌دار است.</div></div>`;
}
function fillTeams(){
  const lg=document.getElementById('clg').value;
  const ts=Object.keys(D.teams[lg]||{}).sort();
  const o=ts.map(t=>`<option>${esc(t)}</option>`).join('');
  document.getElementById('cth').innerHTML=o;
  document.getElementById('cta').innerHTML=o;
  if(ts.length>1)document.getElementById('cta').selectedIndex=1;
}
window.runCmp=()=>{
  const lg=document.getElementById('clg').value,h=document.getElementById('cth').value,a=document.getElementById('cta').value;
  if(h===a){document.getElementById('cout').innerHTML='<div class="note">دو تیم متفاوت انتخاب کن 😉</div>';return;}
  const r=simulate(lg,h,a), th=D.teams[lg][h], ta=D.teams[lg][a];
  document.getElementById('cout').innerHTML=`
    <div class="donut" style="margin-top:16px">${donut(r.ph,'#22c55e','برد '+esc(h))}${donut(r.pd,'#94a3b8','مساوی')}${donut(r.pa,'#38bdf8','برد '+esc(a))}</div>
    <div class="duo" style="margin-top:8px">
      <div><div class="note">xG شبیه‌سازی: <b>${r.lam.toFixed(2)}</b> · فرم:</div>${formChips(th.form)}</div>
      <div><div class="note">xG شبیه‌سازی: <b>${r.mu.toFixed(2)}</b> · فرم:</div>${formChips(ta.form)}</div></div>
    <table style="margin-top:10px"><tr><th>آپشن</th><th>احتمال</th><th>ضریب منصفانه</th></tr>
      <tr><td>بالای ۲.۵ گل</td><td><b>${fmtP(r.o25)}</b></td><td class="fair">${(1/r.o25).toFixed(2)}</td></tr>
      <tr><td>زیر ۲.۵ گل</td><td><b>${fmtP(1-r.o25)}</b></td><td class="fair">${(1/(1-r.o25)).toFixed(2)}</td></tr>
      <tr><td>هر دو تیم گل می‌زنند</td><td><b>${fmtP(r.btts)}</b></td><td class="fair">${(1/r.btts).toFixed(2)}</td></tr>
      <tr><td>شانس دوبل ۱X</td><td><b>${fmtP(r.ph+r.pd)}</b></td><td class="fair">${(1/(r.ph+r.pd)).toFixed(2)}</td></tr></table>`;
};

/* ---------- standings ---------- */
function std(){
  const lgs=Object.keys(D.standings);
  if(!lgs.length) return '<div class="empty">جدولی در دسترس نیست</div>';
  return lgs.map(lg=>{
    const rows=D.standings[lg].map((t,i)=>`<tr><td>${i+1}</td><td>${esc(t.team)}</td><td>${t.p}</td><td>${t.w}</td><td>${t.d}</td><td>${t.l}</td><td>${t.gf}-${t.ga}</td><td><b>${t.pts}</b></td></tr>`).join('');
    return `<div class="sect"><h4>${esc(lg)}</h4><div style="overflow-x:auto"><table>
      <tr><th>#</th><th>تیم</th><th>بازی</th><th>برد</th><th>مساوی</th><th>باخت</th><th>گل</th><th>امتیاز</th></tr>${rows}</table></div>
      <div class="note">جدول تقریبی، محاسبه‌شده از نتایج ثبت‌شده.</div></div>`;
  }).join('');
}

/* ---------- transparency ---------- */
function tr_(){
  const b=D.meta.backtest||{};
  const row=(k,l)=>b[k]?`<tr><td>${l}</td><td><b>${fmtP(b[k].accuracy)}</b></td><td>${b[k].n} بازی</td></tr>`:'';
  return `<div class="sect" style="margin-top:18px"><h4>📏 شفافیت عملکرد — بک‌تست واقعی، بدون گلچین</h4>
    <table><tr><th>سطح اطمینان گزینه</th><th>دقت واقعی</th><th>نمونه</th></tr>
    ${row('acc_at_conf_70','≥۷۰٪')}${row('acc_at_conf_65','≥۶۵٪')}${row('acc_at_conf_60','≥۶۰٪')}</table>
    <div class="note" style="margin-top:10px">بک‌تست ${b.window||''} روی <b>${b.n_total||'—'}</b> بازی از ${b.leagues||'—'} لیگ. دقت کلی مدل ${b.model_pick_accuracy?fmtP(b.model_pick_accuracy):'—'} مقابل ${b.market_pick_accuracy?fmtP(b.market_pick_accuracy):'—'} بازار — بازار قوی‌ترین پیش‌بین است؛ قدرت این سیستم کالیبراسیون و پوشش مارکت‌های بدون ضریب است.</div>
    <div class="foot">⚠️ هیچ پیش‌بینی‌ای قطعی نیست؛ اعداد احتمال کالیبره‌اند نه قول. <b>مدیریت سرمایه:</b> حداکثر ۱–۲٪ بانک روی هر گزینه، از جبران باخت پرهیز کن. شرط‌بندی ریسک مالی واقعی دارد.</div></div>`;
}

/* ---------- assistant picks feed ---------- */
function pickCard(p,i){
  const ai=(D.ai&&D.ai.picks&&D.ai.picks[String(p.fixture_id)])||null;
  return `<div class="card pickcard" onclick="openMatch(${p.fixture_id})">
    <div class="mrow">
      <span class="rk2">${i+1}</span>
      <div style="flex:1">
        <div class="pkpick">🎯 ${esc(p.label_fa)} <b class="pkp">${fmtP(p.p)}</b>
          ${p.odds?`<span class="odds">ضریب ${p.odds}</span>`:''} ${p.value?'💎':''}</div>
        <div class="pkmatch">${esc(p.home)} — ${esc(p.away)} <span class="lg2">· ${esc(p.league)}${p.time?' · '+esc(p.time):''}</span></div>
      </div>
    </div>
    <ul class="rsn">${p.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>
    ${ai?`<div class="aibox">🤖 ${esc(ai)}</div>`:''}
    ${p.n_sources?`<div class="srcs" style="margin-top:8px">🤝 ${p.n_sources} منبع: ${p.sources.slice(0,5).map(esc).join('، ')}${p.sources.length>5?' +'+(p.sources.length-5):''}</div>`:'<div class="srcs" style="margin-top:8px">منبع: مدل آماری با اطمینان بالا</div>'}
  </div>`;
}
function picksView(){
  const c=D.curated||{picks:[],mixes:[]};
  if(!c.picks||!c.picks.length)
    return chips()+`<div class="empty">امروز پیک برگزیده‌ای از فیلترها عبور نکرد — صادقانه بهتر از پیشنهاد بی‌کیفیته. تب «همه بازی‌ها» را ببین ⚽</div>`;
  const brief=(D.ai&&D.ai.daily_brief)?`<div class="sect aibrief">🤖 <b>جمع‌بندی روز:</b> ${esc(D.ai.daily_brief)}</div>`:'';
  const mixes=(c.mixes||[]).map(m=>
    `<div class="mixbox"><div class="mixhd">🎲 ${esc(m.name)} <span class="odds">ضریب ${m.odds}</span> <span class="lg2">احتمال ${fmtP(m.p)}</span></div>
     <div class="mixlegs">${m.legs.map(l=>`<span>◂ ${esc(l.m)}: <b>${esc(l.pick)}</b> (${l.odds})</span>`).join('')}</div></div>`).join('');
  return chips()+brief+mixes+
    `<div class="lgroup"><h3>پیک‌های برگزیده (${c.picks.length}) — با دلیل منطقی</h3></div>`+
    c.picks.map(pickCard).join('')+
    `<div class="note" style="margin-top:10px">${esc(c.note||'')}</div>`;
}

/* ---------- router ---------- */
function render(){
  if(cur.startsWith('match:')) V.innerHTML=matchView(+cur.split(':')[1]);
  else if(cur==='cmp'){V.innerHTML=cmp();document.getElementById('clg').onchange=fillTeams;fillTeams();}
  else if(cur==='std') V.innerHTML=std();
  else if(cur==='tr') V.innerHTML=tr_();
  else if(cur==='home') V.innerHTML=home();
  else V.innerHTML=picksView();
}
render();
</script></body></html>'''


if __name__ == "__main__":
    build()
