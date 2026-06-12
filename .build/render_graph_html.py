# -*- coding: utf-8 -*-
"""Render skill-graph.json as a self-contained interactive HTML (force-directed,
filterable, click-for-details). No external CDN — the force sim + SVG render are
vanilla JS embedded inline. Output is UNTRACKED (see .gitignore). Read-only.

    $PY .build/render_graph_html.py            # -> skill-graph.html (repo root)
    $PY .build/render_graph_html.py out.html   # custom output path
"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <plugin> (portable; 4.8)
GRAPH = os.path.join(ROOT, "skill-graph.json")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "skill-graph.html")

with open(GRAPH, encoding="utf-8") as fh:
    g = json.load(fh)

HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI SDLC v2 — skill graph</title>
<style>
  :root{--bg:#0f1419;--panel:#171d26;--panel2:#1d2530;--line:#2a3441;--fg:#e6edf3;--mut:#8b97a6;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;overflow:hidden}
  #app{display:grid;grid-template-columns:260px 1fr 320px;height:100vh}
  .pane{background:var(--panel);overflow-y:auto;padding:14px 14px 40px}
  #left{border-right:1px solid var(--line)} #right{border-left:1px solid var(--line)}
  #mid{position:relative;background:radial-gradient(circle at 50% 40%,#131a23,#0f1419)}
  h1{font-size:15px;margin:0 0 2px} .sub{color:var(--mut);font-size:11px;margin:0 0 12px}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);
    margin:16px 0 7px;border-bottom:1px solid var(--line);padding-bottom:4px}
  label.row{display:flex;align-items:center;gap:7px;padding:3px 0;cursor:pointer;user-select:none}
  label.row:hover{color:#fff} input[type=checkbox]{accent-color:#4f9cff;width:14px;height:14px}
  .sw{width:11px;height:11px;border-radius:50%;flex:0 0 auto;border:1px solid #0006}
  .swl{width:16px;height:0;border-top-width:3px;border-top-style:solid;flex:0 0 auto}
  .cnt{margin-left:auto;color:var(--mut);font-variant-numeric:tabular-nums;font-size:11px}
  #search{width:100%;padding:7px 9px;background:var(--panel2);border:1px solid var(--line);
    border-radius:7px;color:var(--fg);font-size:12px;margin-bottom:4px}
  #search::placeholder{color:var(--mut)}
  svg{width:100%;height:100%;display:block;cursor:grab} svg.grab{cursor:grabbing}
  .edge{stroke-opacity:.34} .edge.hl{stroke-opacity:1}
  .node circle{stroke:#0a0e13;stroke-width:1.2;cursor:pointer}
  .node.dim{opacity:.12} .edge.dim{stroke-opacity:.04}
  .nlabel{fill:var(--fg);font-size:9px;pointer-events:none;paint-order:stroke;
    stroke:#0f1419;stroke-width:2.4px;stroke-linejoin:round}
  .nlabel.dim{opacity:.08}
  .stat{display:flex;justify-content:space-between;padding:2px 0;color:var(--mut)}
  .stat b{color:var(--fg);font-variant-numeric:tabular-nums}
  #det .empty{color:var(--mut);font-style:italic;margin-top:8px}
  #det h3{font-size:14px;margin:2px 0 2px;word-break:break-all}
  .badge{display:inline-block;font-size:10px;padding:1px 7px;border-radius:10px;margin:2px 4px 2px 0}
  .kv{margin:9px 0} .kv .k{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.05em}
  .chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:2px 7px;
    font-size:11px;cursor:pointer} .chip:hover{border-color:#4f9cff;color:#fff}
  .chip.skill{border-left:3px solid #4f9cff} .chip.agent{border-left:3px solid #b45cff}
  .chip.tool{border-left:3px solid #2ec4a6} .chip.file{border-left:3px solid #f2c14e}
  .hint{color:var(--mut);font-size:11px;margin-top:10px;line-height:1.5}
  kbd{background:var(--panel2);border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:10px}
  #legend .row{font-size:12px} button.mini{background:var(--panel2);color:var(--fg);
    border:1px solid var(--line);border-radius:6px;padding:5px 9px;cursor:pointer;font-size:11px;margin:2px 4px 2px 0}
  button.mini:hover{border-color:#4f9cff}
  #toast{position:absolute;left:50%;bottom:16px;transform:translateX(-50%);background:#000a;
    border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:11px;color:var(--mut);pointer-events:none}
</style></head><body><div id="app">
<div id="left" class="pane">
  <h1>AI SDLC v2</h1><p class="sub">skill dependency graph</p>
  <input id="search" placeholder="search nodes…" autocomplete="off">
  <div id="searchres"></div>
  <h2>Stats</h2><div id="stats"></div>
  <h2>Node types</h2><div id="ntypes"></div>
  <h2>Edge types</h2><div id="etypes"></div>
  <h2>View</h2>
  <button class="mini" id="reset">reset zoom</button>
  <button class="mini" id="reheat">re-layout</button>
  <p class="hint">Drag a node to pin · drag canvas to pan · scroll to zoom ·
    click a node for details · hover to trace neighbours.</p>
</div>
<div id="mid"><svg id="svg"><g id="vp"><g id="ed"></g><g id="nd"></g><g id="lb"></g></g></svg>
  <div id="toast"></div></div>
<div id="right" class="pane"><h2>Details</h2><div id="det"><p class="empty">Click any node.</p></div>
  <h2 id="lh">Legend</h2><div id="legend"></div></div>
</div>
<script>const GRAPH = """

JS = r""";
// ---------- palette ----------
const FILE_FMT = {json:"#f2c14e",markdown:"#e07a5f",html:"#ef476f",directory:"#8d99ae","n/a":"#6c757d",other:"#adb5bd"};
const COL = {
  "skill-main":"#2f7fe0","skill-deleg":"#7fc0ff","agent":"#b45cff",
  "tool-methodology-tool":"#2ec4a6","tool-external-package":"#e8a33d","tool-harness":"#8a94a3",
};
const EDGE = {
  reads:["#6fb0ff",1], creates:["#34d399",1.6], updates:["#e8a33d",1.4],
  appends:["#f4a261",1.3], spawns:["#b45cff",1.8], uses_tool:["#7c8aa0",1],
  uses_harness:["#46505e",.8], hands_off_to:["#ef476f",1.8],
};
const EDGE_DASH = {hands_off_to:"7 5", spawns:"2 3"};
// ---------- build node + link sets ----------
const NS = "http://www.w3.org/2000/svg";
const nodes = [], byId = new Map();
function add(o){ if(byId.has(o.id))return; o.deg=0; nodes.push(o); byId.set(o.id,o); }
GRAPH.nodes.skills.forEach(s=>add({...s,cls:"skill",
  type: s.runs_in==="main-agent"?"skill-main":"skill-deleg", label:s.id}));
GRAPH.nodes.agents.forEach(a=>add({id:a.id,cls:"agent",type:"agent",label:a.id}));
GRAPH.nodes.tools.forEach(t=>add({...t,cls:"tool",type:"tool-"+t.type,label:t.id}));
GRAPH.nodes.files.forEach(f=>add({...f,cls:"file",type:"file",
  fmt:f.v2_format,label:f.id.replace(/^<vault>\//,"").replace(/^\.\//,"")}));
const links = [];
for(const e of GRAPH.edges){ const a=byId.get(e.from), b=byId.get(e.to);
  if(!a||!b) continue; a.deg++; b.deg++; links.push({s:a,t:b,type:e.type}); }
const adj = new Map(nodes.map(n=>[n.id,new Set()]));
const inc = new Map(nodes.map(n=>[n.id,[]]));
for(const l of links){ adj.get(l.s.id).add(l.t.id); adj.get(l.t.id).add(l.s.id);
  inc.get(l.s.id).push(l); inc.get(l.t.id).push(l); }
function nodeColor(n){ return n.cls==="file" ? (FILE_FMT[n.fmt]||"#adb5bd") : COL[n.type]; }
function rad(n){ return n.cls==="skill"?7.5 : 4 + Math.min(7, Math.sqrt(n.deg)*1.6); }

// ---------- filter state ----------
const showNode = {skill:true, agent:true, tool:true, file:true};
const showTool = {"methodology-tool":true,"external-package":true,"harness":false}; // harness off (clutter)
const showEdge = {reads:true,creates:true,updates:true,appends:true,spawns:true,
  uses_tool:true,uses_harness:false,hands_off_to:true};
function nodeVisible(n){
  if(!showNode[n.cls]) return false;
  if(n.cls==="tool") return showTool[n.type.replace("tool-","")];
  return true;
}
function edgeVisible(l){ return showEdge[l.type] && nodeVisible(l.s) && nodeVisible(l.t); }

// ---------- layout (vanilla force sim) ----------
const svg=document.getElementById("svg"), vp=document.getElementById("vp");
const gEd=document.getElementById("ed"), gNd=document.getElementById("nd"), gLb=document.getElementById("lb");
let W=svg.clientWidth, H=svg.clientHeight;
// seed positions in a type-clustered ring to avoid initial overlap
const TYPE_ANG={skill:0, file:Math.PI, agent:Math.PI*0.5, tool:Math.PI*1.5};
let seed=1; const rng=()=>{ seed=(seed*1103515245+12345)&0x7fffffff; return seed/0x7fffffff; };
nodes.forEach((n,i)=>{ const a=(TYPE_ANG[n.cls]||0)+(rng()-.5)*1.4; const r=140+rng()*220;
  n.x=W/2+Math.cos(a)*r; n.y=H/2+Math.sin(a)*r; n.vx=0; n.vy=0; n.fixed=false; });
let alpha=1;
function tick(){
  const vis=nodes.filter(nodeVisible);
  const K_REP=2400, K_SPR=0.035, L=70, CENTER=0.012;
  for(let i=0;i<vis.length;i++){ const a=vis[i];
    for(let j=i+1;j<vis.length;j++){ const b=vis[j];
      let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy||0.01; if(d2>90000) continue;
      const f=K_REP/d2, d=Math.sqrt(d2), fx=f*dx/d, fy=f*dy/d;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy; } }
  for(const l of links){ if(!edgeVisible(l)) continue; const a=l.s,b=l.t;
    let dx=b.x-a.x, dy=b.y-a.y, d=Math.sqrt(dx*dx+dy*dy)||.01;
    const f=K_SPR*(d-L), fx=f*dx/d, fy=f*dy/d;
    a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy; }
  for(const n of vis){ if(n.fixed) continue;
    n.vx+=(W/2-n.x)*CENTER; n.vy+=(H/2-n.y)*CENTER;
    n.vx*=0.82; n.vy*=0.82; n.x+=n.vx*alpha; n.y+=n.vy*alpha; }
  alpha*=0.985; if(alpha<0.02) alpha=0.02;
}
// ---------- render ----------
const elEd=new Map(), elNd=new Map(), elLb=new Map();
function build(){
  gEd.innerHTML=gNd.innerHTML=gLb.innerHTML=""; elEd.clear(); elNd.clear(); elLb.clear();
  for(const l of links){ if(!edgeVisible(l)) continue;
    const ln=document.createElementNS(NS,"line"); ln.setAttribute("class","edge");
    const [c,w]=EDGE[l.type]; ln.setAttribute("stroke",c); ln.setAttribute("stroke-width",w);
    if(EDGE_DASH[l.type]) ln.setAttribute("stroke-dasharray",EDGE_DASH[l.type]);
    gEd.appendChild(ln); elEd.set(l,ln); }
  for(const n of nodes){ if(!nodeVisible(n)) continue;
    const grp=document.createElementNS(NS,"g"); grp.setAttribute("class","node");
    const c=document.createElementNS(NS,"circle"); c.setAttribute("r",rad(n));
    c.setAttribute("fill",nodeColor(n));
    if(n.cls==="skill"&&n.type==="skill-deleg") c.setAttribute("stroke-dasharray","2 2");
    grp.appendChild(c); grp.addEventListener("mousedown",e=>startDrag(e,n));
    grp.addEventListener("mouseenter",()=>hover(n)); grp.addEventListener("mouseleave",unhover);
    grp.addEventListener("click",e=>{e.stopPropagation();select(n);});
    gNd.appendChild(grp); elNd.set(n,grp);
    if(n.cls==="skill"||n.cls==="agent"){ const t=document.createElementNS(NS,"text");
      t.setAttribute("class","nlabel"); t.setAttribute("text-anchor","middle");
      t.textContent=n.label; gLb.appendChild(t); elLb.set(n,t); } }
  draw();
}
function draw(){
  for(const [l,ln] of elEd){ ln.setAttribute("x1",l.s.x);ln.setAttribute("y1",l.s.y);
    ln.setAttribute("x2",l.t.x);ln.setAttribute("y2",l.t.y); }
  for(const [n,grp] of elNd) grp.setAttribute("transform",`translate(${n.x},${n.y})`);
  for(const [n,t] of elLb){ t.setAttribute("x",n.x); t.setAttribute("y",n.y-rad(n)-3); }
}
let raf; function loop(){ tick(); draw(); raf=requestAnimationFrame(loop); }
// ---------- zoom / pan ----------
let zx=0, zy=0, zk=1;
function applyVp(){ vp.setAttribute("transform",`translate(${zx},${zy}) scale(${zk})`); }
svg.addEventListener("wheel",e=>{ e.preventDefault(); const r=svg.getBoundingClientRect();
  const mx=e.clientX-r.left, my=e.clientY-r.top; const f=e.deltaY<0?1.12:1/1.12;
  const nk=Math.max(.15,Math.min(4,zk*f)); zx=mx-(mx-zx)*(nk/zk); zy=my-(my-zy)*(nk/zk); zk=nk; applyVp();
},{passive:false});
let panning=false, px, py;
svg.addEventListener("mousedown",e=>{ if(e.target.closest(".node"))return;
  panning=true; px=e.clientX; py=e.clientY; svg.classList.add("grab"); deselect(); });
window.addEventListener("mousemove",e=>{ if(panning){ zx+=e.clientX-px; zy+=e.clientY-py;
  px=e.clientX; py=e.clientY; applyVp(); } });
window.addEventListener("mouseup",()=>{ panning=false; svg.classList.remove("grab"); endDrag(); });
// ---------- drag node ----------
let drag=null;
function startDrag(e,n){ e.stopPropagation(); drag=n; n.fixed=true; alpha=Math.max(alpha,.5); }
window.addEventListener("mousemove",e=>{ if(!drag)return; const r=svg.getBoundingClientRect();
  drag.x=(e.clientX-r.left-zx)/zk; drag.y=(e.clientY-r.top-zy)/zk; drag.vx=drag.vy=0; });
function endDrag(){ drag=null; }
// ---------- hover / select ----------
function setDim(focus){
  const keep=focus?new Set([focus.id,...adj.get(focus.id)]):null;
  for(const [n,grp] of elNd) grp.classList.toggle("dim", keep&&!keep.has(n.id));
  for(const [n,t] of elLb) t.classList.toggle("dim", keep&&!keep.has(n.id));
  for(const [l,ln] of elEd){ const on=focus&&(l.s.id===focus.id||l.t.id===focus.id);
    ln.classList.toggle("hl", !!on); ln.classList.toggle("dim", keep&&!on); }
}
let selected=null;
function hover(n){ if(!selected) setDim(n); }
function unhover(){ if(!selected) setDim(null); }
function select(n){ selected=n; setDim(n); showDetails(n); }
function deselect(){ if(!selected)return; selected=null; setDim(null); det.innerHTML='<p class="empty">Click any node.</p>'; }
svg.addEventListener("click",e=>{ if(!e.target.closest(".node")) deselect(); });
// ---------- details panel ----------
const det=document.getElementById("det");
function chips(ids){ if(!ids||!ids.length) return '<span style="color:var(--mut)">—</span>';
  return '<div class="chips">'+ids.map(id=>{ const o=byId.get(id);
    const cls=o?o.cls:"skill"; const lbl=o?o.label:id;
    return `<span class="chip ${cls}" data-id="${esc(id)}">${esc(lbl)}</span>`;}).join("")+'</div>'; }
function esc(s){ return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function showDetails(n){
  let h=`<h3>${esc(n.label)}</h3>`;
  const sw=`<span class="badge" style="background:${nodeColor(n)}22;border:1px solid ${nodeColor(n)};color:${nodeColor(n)}">`;
  if(n.cls==="skill"){ h+=sw+(n.runs_in)+`</span>`;
    h+=`<span class="badge" style="background:#fff1;border:1px solid var(--line)">${n.requires_full_context?"needs full context":"delegable"}</span>`;
    if(n.spawns_generic_subagent) h+=`<span class="badge" style="background:#b45cff22;border:1px solid #b45cff;color:#b45cff">spawns subagent</span>`;
    const outs=links.filter(l=>l.s.id===n.id), readf=outs.filter(l=>l.type==="reads").map(l=>l.t.id);
    const creo=outs.filter(l=>l.type==="creates").map(l=>l.t.id), edio=outs.filter(l=>["updates","appends"].includes(l.type)).map(l=>l.t.id);
    const hands=outs.filter(l=>l.type==="hands_off_to").map(l=>l.t.id);
    const ag=outs.filter(l=>l.type==="spawns").map(l=>l.t.id), tl=outs.filter(l=>l.type==="uses_tool").map(l=>l.t.id);
    h+=kv("full id",`<code>${esc(n.id)}</code>`);
    h+=kv("reads",chips(readf)); h+=kv("creates",chips(creo)); h+=kv("edits (update/append)",chips(edio));
    h+=kv("spawns agents",chips(ag)); h+=kv("uses tools",chips(tl)); h+=kv("hands off to",chips(hands));
  } else if(n.cls==="file"){ h+=sw+n.fmt+`</span>`+`<span class="badge" style="background:#fff1;border:1px solid var(--line)">${esc(n.kind||"")}</span>`;
    h+=kv("full id",`<code>${esc(n.id)}</code>`);
    h+=kv("created by",chips(n.created_by)); h+=kv("edited by",chips(n.edited_by));
    h+=kv("read by",chips(n.read_by)); h+=kv("validated by",chips(n.validated_by));
  } else if(n.cls==="agent"){ h+=sw+`agent</span>`;
    const cb=links.filter(l=>l.t.id===n.id&&l.type==="spawns").map(l=>l.s.id);
    h+=kv("spawned by",chips(cb)); }
  else if(n.cls==="tool"){ h+=sw+n.type.replace("tool-","")+`</span>`;
    if("found" in n) h+=`<span class="badge" style="background:#fff1;border:1px solid var(--line)">${n.found?"bundled ✓":"external"}</span>`;
    const ub=links.filter(l=>l.t.id===n.id).map(l=>l.s.id);
    h+=kv("used by",chips([...new Set(ub)])); }
  det.innerHTML=h;
  det.querySelectorAll(".chip").forEach(c=>c.addEventListener("click",()=>{
    const o=byId.get(c.dataset.id); if(o&&nodeVisible(o)){ focusOn(o); select(o); } }));
}
function kv(k,v){ return `<div class="kv"><div class="k">${k}</div>${v}</div>`; }
function focusOn(n){ zk=1.1; zx=W/2-n.x*zk; zy=H/2-n.y*zk; applyVp(); }
// ---------- panels: stats / filters / legend / search ----------
const S=GRAPH.stats;
document.getElementById("stats").innerHTML=Object.entries({
  skills:S.skills, "  main-agent":S.skills_main_agent, "  delegable":S.skills_delegable,
  agents:S.named_agents_used, tools:S.tools, "file nodes":S.file_nodes,
  edges:S.edges, "md→json":S.md_to_json_targets,
}).map(([k,v])=>`<div class="stat"><span>${k.replace(/ /g,"&nbsp;")}</span><b>${v}</b></div>`).join("");
function countNodes(cls){ return nodes.filter(n=>n.cls===cls).length; }
function countTool(sub){ return nodes.filter(n=>n.type==="tool-"+sub).length; }
function countEdge(t){ return links.filter(l=>l.type===t).length; }
const ntypes=document.getElementById("ntypes");
const NT=[["skill","skills",COL["skill-main"]],["agent","agents",COL.agent],
  ["tool","tools",COL["tool-methodology-tool"]],["file","files",FILE_FMT.json]];
ntypes.innerHTML=NT.map(([k,lbl,c])=>
  `<label class="row"><input type="checkbox" data-nt="${k}" checked>
   <span class="sw" style="background:${c}"></span>${lbl}<span class="cnt">${countNodes(k)}</span></label>`).join("")
 +`<div style="margin:6px 0 0 22px">`+
  [["methodology-tool","methodology",COL["tool-methodology-tool"]],
   ["external-package","external",COL["tool-external-package"]],
   ["harness","harness",COL["tool-harness"]]].map(([k,lbl,c])=>
   `<label class="row"><input type="checkbox" data-tt="${k}" ${showTool[k]?"checked":""}>
    <span class="sw" style="background:${c}"></span>${lbl}<span class="cnt">${countTool(k)}</span></label>`).join("")+`</div>`;
ntypes.querySelectorAll("[data-nt]").forEach(cb=>cb.addEventListener("change",()=>{showNode[cb.dataset.nt]=cb.checked;refilter();}));
ntypes.querySelectorAll("[data-tt]").forEach(cb=>cb.addEventListener("change",()=>{showTool[cb.dataset.tt]=cb.checked;refilter();}));
const etypes=document.getElementById("etypes");
etypes.innerHTML=Object.keys(EDGE).map(t=>{const [c,w]=EDGE[t];
  return `<label class="row"><input type="checkbox" data-et="${t}" ${showEdge[t]?"checked":""}>
   <span class="swl" style="border-top-color:${c};${EDGE_DASH[t]?'border-top-style:dashed':''}"></span>
   ${t}<span class="cnt">${countEdge(t)}</span></label>`;}).join("");
etypes.querySelectorAll("[data-et]").forEach(cb=>cb.addEventListener("change",()=>{showEdge[cb.dataset.et]=cb.checked;refilter();}));
document.getElementById("legend").innerHTML=
  `<div class="kv"><div class="k">skills</div>
   <label class="row"><span class="sw" style="background:${COL["skill-main"]}"></span>main-agent</label>
   <label class="row"><span class="sw" style="background:${COL["skill-deleg"]};border-style:dashed"></span>delegable (dashed)</label></div>
   <div class="kv"><div class="k">files by format</div>`+
   Object.entries(FILE_FMT).map(([k,c])=>`<label class="row"><span class="sw" style="background:${c}"></span>${k}</label>`).join("")+`</div>
   <div class="kv"><div class="k">node size</div><span style="color:var(--mut)">∝ degree (connections)</span></div>`;
function refilter(){ build(); alpha=Math.max(alpha,.6); if(selected&&!nodeVisible(selected)) deselect(); }
// search
const sb=document.getElementById("search"), sr=document.getElementById("searchres");
sb.addEventListener("input",()=>{ const q=sb.value.toLowerCase().trim(); if(!q){sr.innerHTML="";return;}
  const hits=nodes.filter(n=>n.id.toLowerCase().includes(q)||n.label.toLowerCase().includes(q)).slice(0,12);
  sr.innerHTML='<div class="chips" style="margin:4px 0 2px">'+hits.map(n=>
    `<span class="chip ${n.cls}" data-id="${esc(n.id)}">${esc(n.label)}</span>`).join("")+'</div>';
  sr.querySelectorAll(".chip").forEach(c=>c.addEventListener("click",()=>{ const o=byId.get(c.dataset.id);
    if(!o)return; if(!nodeVisible(o)){ showNode[o.cls]=true; if(o.cls==="tool")showTool[o.type.replace("tool-","")]=true; refilter(); }
    focusOn(o); select(o); })); });
document.getElementById("reset").onclick=()=>{ zx=zy=0; zk=1; applyVp(); };
document.getElementById("reheat").onclick=()=>{ nodes.forEach(n=>{n.fixed=false;}); alpha=1; };
window.addEventListener("resize",()=>{ W=svg.clientWidth; H=svg.clientHeight; });
document.getElementById("toast").textContent=`${nodes.length} nodes · ${links.length} edges · harness hidden by default`;
setTimeout(()=>document.getElementById("toast").style.opacity=0,4200);
document.querySelector("#toast").style.transition="opacity .8s";
// go
build(); loop();
</script></body></html>"""

html = HEAD + json.dumps(g, ensure_ascii=False) + JS
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(html)
print(f"wrote {OUT}  ({len(html):,} bytes)")
print(f"nodes: {len(g['nodes']['skills'])} skills + {len(g['nodes']['agents'])} agents "
      f"+ {len(g['nodes']['tools'])} tools + {len(g['nodes']['files'])} files · {len(g['edges'])} edges")
