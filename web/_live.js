/* ================= живой режим =================
   Если рядом отвечает бэкенд, демо-данные заменяются настоящими и все
   действия уходят по HTTP. Если бэкенда нет — страница остаётся
   самодостаточным демо, как и была. */

let LIVE = false;
let stream = null;
let refreshTimer = null;

const PROV_IN  = p => (p === "telegram" ? "tg" : p === "max" ? "max" : p);
const PROV_OUT = p => (p === "tg" ? "telegram" : p);

async function api(path, options){
  const r = await fetch(path, Object.assign({headers:{"Content-Type":"application/json"}}, options));
  if(!r.ok){
    let detail = "HTTP " + r.status;
    try{ detail = (await r.json()).detail || detail; }catch(e){}
    throw new Error(detail);
  }
  return r.status === 204 ? null : r.json();
}

function mapConv(r){
  const hasIntake = r.license || r.org || r.phone || r.intake_status !== "none";
  return {
    id: "s" + r.id,
    sid: r.id,
    contactId: r.contact_id,
    client: "k" + r.contact_id,
    provider: PROV_IN(r.provider),
    ext: r.external_chat_id,
    name: r.display_name || "Без имени",
    un: r.username || "",
    intake: hasIntake ? {
      key: r.license || "—", org: r.org || "—",
      fio: r.display_name || "—", phone: r.phone || "—",
      at: r.created_at || r.last_ts,
      status: r.intake_status || "done"
    } : null,
    product: r.product || "—", ver: "—", support: "—",
    topic: r.topic || "Без темы",
    status: r.status,
    assignee: r.assignee || "—",
    folder: r.folder || "",
    folders: r.folders || (r.folder ? [r.folder] : []),
    archivedAt: r.archived_at || 0,
    tags: r.tags || [],
    note: r.note || "",
    unread: r.unread || 0,
    since: Date.now() - (r.created_at || Date.now()),
    pres: {
      state: r.presence_state || "left",
      since: r.presence_since || 0,
      typing: (Date.now() - (r.typing_at || 0)) < 60000
    },
    color: AVA[(r.contact_id || 0) % AVA.length],
    lastTs: r.last_ts || Date.now(),
    seq: 0,
    loaded: false,
    // Заглушка под превью: настоящие сообщения подтянем при открытии
    msgs: [{id:"p", dir: r.preview_dir === "out" ? "out" : "in",
            t: r.preview || "", ts: r.last_ts || Date.now(), st:"read", partial:true}]
  };
}

function mapClient(r){
  return {
    id: "k" + r.id,
    cid: r.id,
    name: r.display_name || "Без имени",
    un: r.username || "",
    provider: PROV_IN(r.provider),
    ext: r.external_user_id,
    color: AVA[r.id % AVA.length],
    org: r.org || "—", lic: r.license || "—", phone: r.phone || "—",
    product: r.product || "—", ver: "—", support: "—",
    since: Date.now() - (r.created_at || Date.now()),
    mood: r.mood || "", moodAt: r.mood_at || 0, moodBy: r.mood_by || "",
    lastTs: r.last_ts || 0,
    intakeDone: !!r.intake_done,
    convs: []
  };
}

function mapMsg(r){
  const a = (r.attachments || [])[0];
  return {
    id: "m" + r.id,
    dir: r.direction,
    t: r.text || "",
    ts: r.ts,
    st: r.delivery || "sent",
    att: a ? {n: a.name || "файл",
              s: a.size ? Math.round(a.size/1024) + " КБ" : "",
              k: (a.kind || "файл").slice(0,4).toUpperCase(),
              url: a.url || "",
              kind: a.kind || ""} : null
  };
}

async function loadAll(){
  const [active, archived, raw] = await Promise.all([
    api("/api/conversations?status=all"),
    api("/api/conversations?status=archived"),
    api("/api/clients")
  ]);

  const keepOpen = activeId;
  convs.length = 0;
  active.concat(archived).forEach(r => convs.push(mapConv(r)));

  clients.length = 0;
  Object.keys(CBY).forEach(k => delete CBY[k]);
  raw.forEach(r => {
    const cl = mapClient(r);
    CBY[cl.id] = cl;
    clients.push(cl);
  });
  convs.forEach(c => { if(CBY[c.client]) CBY[c.client].convs.push(c.id); });

  if(!byId(keepOpen)) activeId = (!chatClosed && convs.length) ? convs[0].id : null;
}

async function loadMessages(c){
  if(!LIVE || !c || c.loaded) return;
  const rows = await api("/api/conversations/" + c.sid + "/messages");
  c.msgs = rows.map(mapMsg);
  c.seq = c.msgs.length;
  c.loaded = true;
}

/* Бэкенд сам толкает изменения — перезапрашиваем не чаще раза в полсекунды */
function scheduleRefresh(){
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try{
      const open = byId(activeId);
      if(open) open.loaded = false;
      await loadAll();
      const still = byId(activeId);
      if(still) await loadMessages(still);
      renderFilters(); renderList(); renderThread(); renderCard();
      if(section === "archive") renderArchive();
      if(section === "clients") renderClients();
    }catch(e){ console.warn("обновление не прошло:", e.message); }
  }, 500);
}

function openStream(){
  try{
    stream = new EventSource("/api/stream");
    ["message","conversation","client","presence"].forEach(kind => {
      stream.addEventListener(kind, scheduleRefresh);
    });
    // EventSource переподключается сам; после обрыва перечитываем всё —
    // пока связи не было, события могли пройти мимо
    let broken = false;
    stream.onerror = () => { broken = true; };
    stream.onopen = () => { if(broken){ broken = false; scheduleRefresh(); } };
  }catch(e){ console.warn("SSE недоступен:", e); }
}

function markLive(channels){
  const tag = $("#demoTag");
  const on = Object.keys(channels || {}).filter(k => channels[k]);
  tag.textContent = on.length ? on.map(k => k === "telegram" ? "Telegram" : "MAX").join(" · ") : "нет каналов";
  tag.className = "badge " + (on.length ? "open" : "waiting");
  tag.title = on.length
    ? "Канал подключён: сообщения идут из мессенджера"
    : "Бэкенд работает, но токен бота не прописан";
}

async function liveBoot(){
  // Страницу отдал сервер — значит, бэкенд точно есть: не сдаёмся после
  // первой неудачи (туннель бывает медленным), а пробуем снова
  const served = !!window.TP_SERVER;
  let health = null;
  for(let attempt = 0; ; attempt++){
    try{
      health = await api("/api/health");
      break;
    }catch(e){
      if(!served) return false;   // бэкенда нет — остаёмся демо
      booting = "Нет связи с сервером, пробую снова…";
      renderList();
      await new Promise(r => setTimeout(r, Math.min(1000 * (attempt + 1), 5000)));
    }
  }
  if(!health || !health.ok) return false;

  LIVE = true;
  await loadAll();
  booting = "";
  const open = byId(activeId);
  if(open) await loadMessages(open);
  markLive(health.channels);
  openStream();
  renderFilters(); renderList(); renderThread(); renderCard();
  if(section === "archive") renderArchive();
  if(section === "clients") renderClients();

  // Страховка на случай, если туннель глотает поток событий:
  // раз в 20 секунд сверяемся с сервером сами
  setInterval(() => { if(!document.hidden) scheduleRefresh(); }, 20000);
  return true;
}
