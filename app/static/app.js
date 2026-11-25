function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function renderSteps(steps){return '<ul class="steps">'+steps.map(s=>`<li class="${s.status}"><span class="dot"></span><span>${esc(s.label)}</span> <span class="muted small">${esc(s.detail)}</span></li>`).join("")+"</ul>"}
async function pollPrep(id,status){
  const r=await fetch(`/api/preps/${id}`); if(!r.ok) return setTimeout(()=>pollPrep(id,status),4000);
  const d=await r.json();
  if(d.status!==status){location.reload();return}
  if(d.run){const p=document.getElementById("progress"); if(p) p.innerHTML=renderSteps(d.run.steps);
    const c=document.getElementById("cost"); if(c) c.textContent=`Research and model cost so far: $${d.run.cost_usd}`}
  setTimeout(()=>pollPrep(id,status),3000)
}
async function pollRun(id,el){
  const r=await fetch(`/api/runs/${id}`); const d=await r.json();
  if(el) el.innerHTML=renderSteps(d.steps||[]);
  if(d.status==="running") setTimeout(()=>pollRun(id,el),3000); else location.reload();
}
function initDescribe(){
  const prod=document.getElementById("product"); if(!prod) return;
  const sync=()=>{const v=prod.value; document.querySelectorAll(".stage-box").forEach(b=>{const on=v==="both"||b.dataset.p===v; b.style.display=on?"":"none"; if(!on) b.querySelector("select").value=""})};
  prod.addEventListener("change",sync); sync();
  document.getElementById("add-att").onclick=()=>{const rows=document.querySelectorAll("#attendees .att"); const n=rows[rows.length-1].cloneNode(true); n.querySelectorAll("input").forEach(i=>i.value=""); document.getElementById("attendees").appendChild(n)};
  const lk=document.querySelector("select[name=lead_kind]"), ld=document.getElementById("lead_detail");
  const ph=()=>{const o=lk.selectedOptions[0]; ld.placeholder=o?(o.dataset.ph||""):""}; lk.addEventListener("change",ph); ph();
}
function initVerdicts(){
  document.querySelectorAll(".verdict").forEach(box=>{
    box.querySelectorAll("button").forEach(b=>b.onclick=async()=>{
      const v=b.dataset.v; const fd=new FormData(); fd.append("verdict",v);
      if(v==="Corrected"){const t=prompt("Your correction (stored as rep input and always wins over research):"); if(!t) return; fd.append("correction",t)}
      const r=await fetch(`/findings/${box.dataset.fid}/verdict`,{method:"POST",body:fd});
      if(r.ok){ if(v==="Corrected"||v==="Clear") location.reload(); else {box.querySelectorAll("button").forEach(x=>x.classList.remove("on")); b.classList.add("on")} }
      else alert("Could not save: "+(await r.text()));
    })
  })
}
function initBriefEdit(briefId){
  document.querySelectorAll("[data-edit]").forEach(el=>{
    el.classList.add("editable"); el.title="Click to edit";
    el.addEventListener("click",async e=>{
      if(e.target.closest("a,button,.pt-tags,.src")) return;
      const [section,index]=el.dataset.edit.split("|");
      const cur=el.querySelector(".pt-text").innerText;
      const t=prompt("Edit this point (leave empty to delete it):",cur); if(t===null||t===cur) return;
      let fact="0"; if(t && el.dataset.hasfindings==="1") fact=confirm("Is this a correction of a fact? OK stores it in the account profile as rep input (it then wins over research). Cancel changes only the wording of the brief.")?"1":"0";
      const fd=new FormData(); fd.append("section",section); fd.append("index",index); fd.append("text",t); fd.append("is_fact",fact);
      const r=await fetch(`/briefs/${briefId}/edit`,{method:"POST",body:fd}); const d=await r.json();
      if(d.ok) location.href=location.pathname.replace(/\/briefs\/\d+/,`/briefs/${d.brief_id}`)+location.hash;
    })
  })
}
async function pollBrief(briefId){
  const r=await fetch(`/api/briefs/${briefId}/latest`); const d=await r.json();
  if(d.latest!==briefId){location.href=location.pathname.replace(/\/briefs\/\d+/,`/briefs/${d.latest}`)+location.hash;return}
  if(d.running) setTimeout(()=>pollBrief(briefId),3000);
  else {const n=document.getElementById("redo-note"); if(n) n.textContent=d.error?("Redo failed: "+d.error):""}
}
