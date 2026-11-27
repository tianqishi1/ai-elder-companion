
const L={
 dialect:{mandarin:"普通话",sichuan:"四川话",cantonese:"粤语"},
 hearing:{ok:"听力正常",mild_loss:"轻度听障"},
 need:{lonely_chatty:"孤独倾诉型",taciturn:"寡言少语型",health_anxious:"健康焦虑型",hearing_loss:"听力障碍型",cognitive_decline:"认知下降型",wary_refusal:"警惕拒接型"},
 ctype:{companion_chat:"陪伴聊天",med_remind:"用药提醒",health_check:"健康问询",safety_check:"平安确认"},
 rtype:{chest_pain:"胸痛/胸闷",fall_cannot_rise:"跌倒无法起身",suicide_ideation:"自杀意念",scam_in_progress:"疑似正在被骗",fall:"跌倒",med_stopped:"自行停药",no_food:"断粮/没吃饭",persistent_low:"持续情绪低落",poor_sleep:"睡眠差",low_appetite:"食欲下降",lonely:"孤独感"},
 action:{transfer_human_in_call:"通话中转人工",notify_family_immediate:"立即通知家属",notify_community:"通知社区",work_order_24h:"24小时工单",notify_family:"通知家属",notify_family_app:"家属端提醒",add_next_must_ask:"下次通话跟进"},
 emotion:{neutral:"平稳",low:"低落",anxious:"焦虑",happy:"愉快"},
 comp:{ok:"正常",hard:"听不清",clarify:"待澄清"},
 eng:{high:"高",normal:"一般",low:"低"},
 strat:{continue:"继续聊",empathy_first:"共情优先",slow_repeat:"降速复述",wrap_up:"准备收尾",escalate:"紧急升级",clarify:"诚实澄清"},
 task:{retry_call:"重试外呼",sms_notify_family:"短信告知家属",work_order_home_visit:"上门探访工单",companion_chat:"陪伴聊天",med_remind:"用药提醒",health_check:"健康问询",safety_check:"平安确认"},
 source:{weekly_plan:"周计划",no_answer_chain:"未接通升级"},
 channel:{"phone+sms":"电话+短信",app:"家属小程序",workbench:"社区工作台"},
 level:{none:"无",yellow:"黄色关注",orange:"橙色事件",red:"红色事件"}
};
const lb=(m,k)=>(L[m]&&L[m][k])||k;
L.role={worker:"社工/运营",admin:"管理员"};
const sigText=j=>{try{return JSON.parse(j).map(s=>`<span class="tag ${s.severity}">${lb("rtype",s.type)}</span>「${esc(s.quote)}」`).join(" ")}catch(_){return j}};
const actText=j=>{try{return JSON.parse(j).map(a=>lb("action",a)).join("、")}catch(_){return j}};
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let CSRF=null;
async function ensureCsrf(){ if(!CSRF){ try{ const r=await fetch("/api/csrf"); if(r.ok) CSRF=(await r.json()).token; }catch(_){} } }
let ME=null, CALL=null, voiceOn=false, rec=null;
const TABS=[["elders","老人档案"],["call","关怀通话"],["orders","工单与风险"],["ledger","探访台账"],["dash","数据看板"],["family","家属视图"],["settings","系统设置"]];
async function api(path,body){
  let opt={};
  if(body){ await ensureCsrf();
    opt={method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":CSRF||""},body:JSON.stringify(body)}; }
  let r=await fetch(path,opt);
  if(r.status===403&&body){ CSRF=null; await ensureCsrf();
    opt.headers["X-CSRF-Token"]=CSRF||""; r=await fetch(path,opt); }
  const j=await r.json();
  if(r.status===401){showLogin();throw new Error("未登录")}
  if(!r.ok)throw new Error(j.error||r.status);
  return j;
}
function showLogin(){$("loginView").classList.remove("hidden");$("appView").classList.add("hidden")}
async function login(){
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({username:$("loginName").value,password:$("loginPw").value})});
    const j=await r.json(); if(!r.ok)throw new Error(j.error||r.status);
    CSRF=null; ME=await api("/api/me");enterApp();
  }catch(e){alert(e.message)}
}
async function logout(){await api("/api/logout",{});ME=null;showLogin()}
function enterApp(){
  $("loginView").classList.add("hidden");$("appView").classList.remove("hidden");
  $("whoami").textContent=ME.session.name+"（"+(ME.session.role==="admin"?"管理员":"社工")+"）";
  $("modeBadge").textContent="LLM: "+(ME.llm_mode==="real"?"真实底座":"演示模式");
  $("nav").innerHTML=TABS.filter(t=>t[0]!=="settings"||ME.session.role==="admin")
    .map(t=>`<button onclick="tab('${t[0]}')" id="tab-${t[0]}">${t[1]}</button>`).join("");
  tab("elders");
}
function tab(name){
  TABS.forEach(t=>$("v-"+t[0]).classList.toggle("hidden",t[0]!==name));
  TABS.forEach(t=>{const b=$("tab-"+t[0]);if(b)b.classList.toggle("on",t[0]===name)});
  ({elders:loadElders,call:loadCallView,orders:loadOrders,ledger:loadLedger,dash:loadDash,settings:loadSettings}[name]||(()=>{}))();
}


// ---- elders ----
let ELDERS=[];
async function loadElders(){
  ELDERS=await api("/api/elders");
  $("callElder").innerHTML=ELDERS.map(e=>`<option value="${esc(e.id)}">${esc(e.name)}</option>`).join("");
  renderElders();
}
function renderElders(){
  const d=$("fltDialect").value,n=$("fltNeed").value,k=($("fltKw").value||"").trim().toLowerCase();
  const rows=ELDERS.filter(e=>(!d||e.dialect===d)&&(!n||e.need_type===n)&&(!k||e.name.toLowerCase().includes(k)||e.id.toLowerCase().includes(k)));
  $("elderRows").innerHTML=rows.map(e=>`<tr><td>${esc(e.id)}</td><td>${esc(e.name)}</td><td>${lb("dialect",e.dialect)} / ${lb("hearing",e.hearing)}</td><td>${lb("need",e.need_type)}</td><td><code>${esc(e.family_code)}</code></td><td><button class="btn sec" onclick="detail('${e.id}')">详情</button></td></tr>`).join("")||"<tr><td colspan=6>无匹配老人</td></tr>";
}
function showElderForm(){$("elderForm").classList.toggle("hidden")}
async function createElder(){
  if(!$("f_consent").checked)return alert("必须签署知情同意");
  const meds=$("f_meds").value.split(";").filter(Boolean).map(s=>{const[n,t]=s.split(",");return{name:n.trim(),time:(t||"").trim()}});
  const family=$("f_family").value.split(";").filter(Boolean).map(s=>{const[r,n]=s.split(":");return{rel:(r||"").trim(),name:(n||"").trim()}});
  try{
    const r=await api("/api/elders",{name:$("f_name").value,age:+$("f_age").value,gender:$("f_gender").value,dialect:$("f_dialect").value,
      hearing:$("f_hearing").value,need_type:$("f_need").value,meds,family,interests:[],
      consent:{recording:true,ai_identity_ack:true}});
    alert("已创建，家属访问码："+r.family_code);$("elderForm").classList.add("hidden");loadElders();
  }catch(e){alert(e.message)}
}
async function detail(id){
  const d=await api("/api/elder?id="+id);
  const e=d.elder;
  $("elderDetail").classList.remove("hidden");
  $("elderDetail").innerHTML=`<h2>${esc(e.name)} · 详情</h2>
   <h3>情节记忆（个性化开场来源）</h3><table><tbody>${d.episodes.map(x=>`<tr><td>${esc(x.topic)}</td><td>${lb("emotion",x.emotion)}</td></tr>`).join("")||"<tr><td>暂无</td></tr>"}</tbody></table>
   <h3>通话摘要</h3><table><tbody>${d.calls.map(c=>`<tr><td>${lb("ctype",c.type)}</td><td>${esc(c.summary)}</td><td>${c.duration_s}s / ${c.cost}元</td></tr>`).join("")||"<tr><td>暂无</td></tr>"}</tbody></table>
   <h3>通知记录</h3><table><tbody>${d.notifications.map(n=>`<tr><td>${esc(n.channel)}</td><td>${esc(n.target)}</td><td>${esc(n.message)}</td></tr>`).join("")||"<tr><td>暂无</td></tr>"}</tbody></table>
   ${ME.session.role==="admin"?`<div style="margin-top:10px"><button class="btn sec" onclick="unsub('${e.id}')">退订并删除数据</button></div>`:""}`;
}
async function unsub(id){if(confirm("退订即删：转写/记忆删除，台账匿名化保留。确认？")){await api("/api/unsubscribe",{elder_id:id});detail(id);loadElders()}}

// ---- call ----
async function loadCallView(){loadElders();loadTasks()}
async function loadTasks(){
  const rows=await api("/api/tasks");
  $("taskRows").innerHTML=rows.map(t=>`<tr><td>${esc(t.name||t.elder_id)}</td><td>${lb("task",t.type)}</td><td>${lb("source",t.source)}</td><td>${new Date(t.due_at*1000).toLocaleString()}</td><td><button class="btn sec" onclick="doneTask(${t.id})">完成</button></td></tr>`).join("")||"<tr><td colspan=5>暂无待呼任务</td></tr>";
}
async function doneTask(id){await api("/api/task/done",{id});loadTasks()}
async function startCall(){
  try{
    const act=await api("/api/call/active?elder_id="+$("callElder").value);
    if(act.call_id){resumeCall(act);return}
    const r=await api("/api/call/start",{elder_id:$("callElder").value,type:$("callType").value});
    CALL=r.call_id;$("chat").classList.remove("hidden");$("inputRow").classList.remove("hidden");
    $("btnEnd").classList.remove("hidden");$("btnStart").disabled=true;$("callResult").innerHTML="";$("escBanner").innerHTML="";
    $("chat").innerHTML="";addMsg("sys","AI 语音关怀通话 · 老人已知情同意 · 通话内容按约定保存");
    addMsg("ai",r.opening);speak(r.opening,0.85);
    $("stateChips").textContent="必问项："+r.must_ask.join("、");
  }catch(e){alert(e.message)}
}
function resumeCall(act){
  CALL=act.call_id;$("chat").classList.remove("hidden");$("inputRow").classList.remove("hidden");
  $("btnEnd").classList.remove("hidden");$("btnStart").disabled=true;$("callResult").innerHTML="";
  $("chat").innerHTML="";addMsg("sys","已恢复进行中的通话（刷新/断线重连）");
  act.transcript.forEach(m=>addMsg(m.role,m.text));
  $("stateChips").textContent="必问项："+act.must_ask.join("、")+" · 已完成："+(act.must_ask_done.join("、")||"无");
  const last=act.transcript.filter(m=>m.role==="ai").pop();
  if(last)speak(last.text,0.85);
}
function addMsg(role,text){
  const d=document.createElement("div");d.className="msg "+role;
  d.innerHTML=`<div class="who">${role==="ai"?"小暖":role==="sys"?"系统":"老人"}</div><div class="bub"></div>`;
  d.querySelector(".bub").textContent=text;$("chat").appendChild(d);$("chat").scrollTop=1e9;
}
async function sendTurn(text){
  text=text||$("elderInput").value.trim();if(!text||!CALL)return;
  $("elderInput").value="";addMsg("elder",text);
  try{
    const r=await api("/api/call/turn",{call_id:CALL,text});
    addMsg("ai",r.reply);speak(r.reply,r.tts.speed);
    $("stateChips").textContent=`情绪:${lb("emotion",r.state.emotion)} · 理解:${lb("comp",r.state.comprehension)} · 兴致:${lb("eng",r.state.engagement)} · 策略:${lb("strat",r.state.strategy_next)} · 必问完成:${r.must_ask_done.join("、")||"无"}`;
    if(r.escalation)$("escBanner").innerHTML=`<div class="banner red">🚨 红色风险实时升级：${r.escalation.message}</div>`;
    if(r.state.strategy_next==="wrap_up")setTimeout(endCall,1500);
  }catch(e){alert(e.message)}
}
async function endCall(){
  if(!CALL)return;const cid=CALL;CALL=null;stopVoice();
  const r=await api("/api/call/end",{call_id:cid});
  $("btnStart").disabled=false;$("btnEnd").classList.add("hidden");$("inputRow").classList.add("hidden");
  const lv=r.risk.level;
  $("callResult").innerHTML=`<div class="banner ${lv==="red"?"red":"info"}">通话结束：${esc(r.summary)}<br>时长 ${r.duration_s}s · 成本 ${r.cost_yuan}元 · 风险 <span class="tag ${lv}">${lb("level",lv)}</span> ${r.risk.actions.map(a=>lb("action",a)).join("、")||"无"} · 台账已写入</div>`;
}
async function markNoAnswer(){
  const r=await api("/api/call/noanswer",{elder_id:$("callElder").value});
  alert("未接通升级链已入任务队列：\n"+r.chain.map(s=>s.t+" → "+lb("task",s.action)).join("\n"));loadTasks();
}
// ---- voice (Web Speech API) ----
let sr=null, srActive=false, currentAudio=null, aiSpeaking=false, serverVoice=false, srBroken=false;
function voiceNote(msg){ $("voiceNote").innerHTML = msg ? `<div class="banner info">${msg}</div>` : ""; }
function toggleVoice(){
  voiceOn=!voiceOn;
  serverVoice=voiceOn&&ME&&ME.llm_mode==="real";
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  $("btnVoice").textContent="🎙 语音模式："+(voiceOn?(SR?"开":"开（仅语音输出）"):"关");
  $("btnTalk").classList.toggle("hidden",!(voiceOn&&SR&&!srBroken));
  if(!voiceOn){stopVoice();voiceNote("");return}
  if(!SR||srBroken){voiceNote("当前浏览器语音输入不可用：小暖会用真人语音说话，您用文字回复即可。");return}
  voiceNote("点「开始说话」后直接说，识别文字会实时显示；说完自动发送。小暖说话时麦克风自动让路。");
}
function talkToggle(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR)return;
  if(srActive){try{sr.stop()}catch(_){}return}
  if(currentAudio){try{currentAudio.pause()}catch(_){}}
  if(window.speechSynthesis)speechSynthesis.cancel();
  aiSpeaking=false;
  sr=new SR();sr.lang="zh-CN";sr.interimResults=true;sr.continuous=false;
  let finalText="";
  sr.onresult=e=>{
    let interim="";
    for(let i=e.resultIndex;i<e.results.length;i++){
      if(e.results[i].isFinal)finalText+=e.results[i][0].transcript;
      else interim+=e.results[i][0].transcript;
    }
    $("elderInput").value=finalText+interim;
    if(finalText){srActive=false;$("btnTalk").textContent="🎙 开始说话";const txt=finalText;finalText="";sendTurn(txt)}
  };
  sr.onerror=e=>{srActive=false;$("btnTalk").textContent="🎙 开始说话";
    if(["network","not-allowed","service-not-allowed"].includes(e.error)){
      srBroken=true;$("btnTalk").classList.add("hidden");
      voiceNote("当前环境语音输入不可用（浏览器在线识别被网络拦截，且本订阅不含服务端识别）。小暖仍用真人语音说话，您用文字回复即可；完整双向语音需标准 DashScope Key 或电话线路接入。");
    }else{voiceNote("语音识别出错（"+e.error+"）。请再试一次，或直接用文字输入；您的话不会被丢弃。")}};
  sr.onend=()=>{if(srActive){srActive=false;$("btnTalk").textContent="🎙 开始说话";
    if(!$("elderInput").value)voiceNote("没听到完整的话。请再按「开始说话」，或文字输入。")}};
  try{sr.start();srActive=true;$("btnTalk").textContent="⏹ 停止";voiceNote("🎙 录音中…请说话")}
  catch(e){voiceNote("无法启动麦克风："+e.message)}
}
function stopVoice(){if(sr){try{sr.stop()}catch(_){}sr=null}srActive=false;
  if(currentAudio){try{currentAudio.pause()}catch(_){}currentAudio=null}
  if(window.speechSynthesis)speechSynthesis.cancel();aiSpeaking=false;
  $("btnTalk").textContent="🎙 开始说话";}
function speak(text,rate){
  if(!voiceOn)return;
  if(currentAudio){try{currentAudio.pause()}catch(_){}}
  if(serverVoice){
    fetch("/api/voice/tts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text})})
      .then(r=>r.json()).then(j=>{
        if(j.url){currentAudio=new Audio(j.url);aiSpeaking=true;
          currentAudio.onended=()=>{aiSpeaking=false};
          currentAudio.play().catch(()=>{aiSpeaking=false;fallbackSpeak(text,rate)})}
        else fallbackSpeak(text,rate);
      }).catch(()=>fallbackSpeak(text,rate));
    return;
  }
  fallbackSpeak(text,rate);
}
function fallbackSpeak(text,rate){
  if(!window.speechSynthesis)return;
  speechSynthesis.cancel();
  const u=new SpeechSynthesisUtterance(text);u.lang="zh-CN";u.rate=Math.max(.6,Math.min(1.2,rate||.85));
  aiSpeaking=true;u.onend=()=>{aiSpeaking=false};
  speechSynthesis.speak(u);
}
// ---- orders / risks ----
async function loadOrders(){
  const os=await api("/api/orders");
  $("orderRows").innerHTML=os.map(o=>`<tr><td>${o.name||o.elder_id}</td><td><span class="tag ${o.level}">${o.level}</span></td><td>${lb("rtype",o.type)}</td><td>${esc(o.note)}</td><td>${o.status==="open"?"待处置":"已闭环"}</td><td>${o.status==="open"?`<button class="btn sec" onclick="closeOrder(${o.id})">闭环</button>`:""}</td></tr>`).join("")||"<tr><td colspan=6>暂无工单</td></tr>";
  const rs=await api("/api/risks");
  $("riskRows").innerHTML=rs.map(r=>`<tr><td>${r.name||r.elder_id}</td><td><span class="tag ${r.level}">${r.level}</span></td><td>${sigText(r.signals_json)}</td><td>${actText(r.actions_json)}</td><td>${r.reviewed?"已复核":`<button class="btn sec" onclick="review(${r.id})">复核</button>`}</td></tr>`).join("")||"<tr><td colspan=5>暂无风险事件</td></tr>";
}
async function closeOrder(id){const note=prompt("处置说明（闭环留痕）");if(note===null)return;await api("/api/order/close",{id,note});loadOrders()}
async function review(id){await api("/api/risk/review",{id});loadOrders()}
// ---- ledger / dash ----
async function loadLedger(){
  const rows=await api("/api/ledger");
  $("ledgerRows").innerHTML=rows.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.visit_time)}</td><td>${esc(r.method)}</td><td>${esc(r.content)}</td><td>${esc(r.result)}</td></tr>`).join("")||"<tr><td colspan=5>暂无台账</td></tr>";
}
async function loadDash(){
  const m=await api("/api/metrics");
  const items=[["服务老人",m.elders],["已完成通话",m.calls],["平均时长",m.avg_duration_s+"s"],["任务完成率",m.task_completion==null?"—":Math.round(m.task_completion*100)+"%"],["工单闭环率",m.order_closure_rate==null?"—":Math.round(m.order_closure_rate*100)+"%"],["待处置工单",m.orders_open],["累计成本",m.total_cost_yuan+"元"],["红色事件",(m.risk_by_level.red||0)]];
  $("kpis").innerHTML=items.map(([l,v])=>`<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
}
// ---- family ----
async function loadFamily(){
  const d=await api("/api/family?code="+encodeURIComponent($("famCode").value));
  if(d.error){$("famView").innerHTML=`<div class="banner red">${d.error}</div>`;return}
  $("famView").innerHTML=`<h3>${esc(d.name)} · 近期关怀摘要</h3>
   <table><tbody>${d.summaries.map(s=>`<tr><td>${esc(s.time)}</td><td>${esc(s.summary)}</td><td>${s.duration_s}s</td></tr>`).join("")||"<tr><td>暂无</td></tr>"}</tbody></table>
   <h3>告警与提醒</h3><table><tbody>${d.alerts.map(a=>`<tr><td>${lb("channel",a.channel)}</td><td>${esc(a.message)}</td></tr>`).join("")||"<tr><td>暂无</td></tr>"}</tbody></table>
   <p class="note">情绪趋势：${d.emotion_trend.map(e=>lb("emotion",e)).join(" → ")||"暂无"}。通话全文与录音不对家属开放（老人隐私边界）。</p>`;
}
// ---- settings ----
async function loadUsers(){
  const us=await api("/api/users");
  $("userRows").innerHTML=us.map(u=>`<tr><td>${esc(u.username)}</td><td>${lb("role",u.role)||esc(u.role)}</td></tr>`).join("");
}
async function createUser(){
  try{ await api("/api/users",{username:$("nu_name").value,password:$("nu_pw").value,role:$("nu_role").value});
    alert("已创建"); loadUsers(); }catch(e){alert(e.message)}
}
async function loadSettings(){ loadUsers();
  const s=await api("/api/settings");
  $("s_base").value=s.llm_base;$("s_model").value=s.llm_model;
}
async function saveSettings(){
  await api("/api/settings",{llm_base:$("s_base").value,llm_model:$("s_model").value,llm_key:$("s_key").value});
  ME=await api("/api/me");$("modeBadge").textContent="LLM: "+(ME.llm_mode==="real"?"真实底座":"演示模式");
  alert("已保存");
}
async function runEval(){
  $("evalReport").classList.remove("hidden");$("evalReport").textContent="运行中…";
  const r=await api("/api/eval",{});
  $("evalReport").textContent=r.report+(r.exit===0?"\n✅ 门禁全部通过":"\n❌ 门禁未通过");
}
async function runQuality(){
  $("qualityReport").classList.remove("hidden");$("qualityReport").textContent="评测中…";
  const r=await api("/api/eval/quality",{});
  $("qualityReport").textContent=r.report;
}
async function purge(){const r=await api("/api/purge",{days:180});alert("已清理转写 "+r.deleted_transcripts+" 条")}
// ---- boot ----
(async()=>{try{ME=await api("/api/me");if(ME.session)enterApp();else showLogin()}catch(_){showLogin()}})();
