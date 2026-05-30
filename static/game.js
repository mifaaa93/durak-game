// ── CONFIG ────────────────────────────────────────────────────────────────────
const SERVER = location.origin.replace(/^http/, 'ws');
const tg = window.Telegram?.WebApp;
if (tg) { tg.expand(); tg.ready(); }

// ── STATE ─────────────────────────────────────────────────────────────────────
let ws = null, myId = null, myName = '', roomId = '';
let intentionalClose = false;
let gameState = null;
let selectedHandCard = null;
let surrenderConfirm = false;
let prevPairsSig = null, prevPhase = null;

// ── DRAG STATE ────────────────────────────────────────────────────────────────
const DRAG_THRESHOLD = 7;
let drag = null;

// ── AUDIO ─────────────────────────────────────────────────────────────────────
let audioCtx = null;
function getACtx() {
  if (!audioCtx) try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch(e){}
  return audioCtx;
}
function tone(freq, type, dur, vol, delay) {
  try {
    const ctx = getACtx(); if (!ctx) return;
    const t = ctx.currentTime + (delay||0);
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.type = type||'sine'; o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(vol||.25, t);
    g.gain.exponentialRampToValueAtTime(.001, t+dur);
    o.start(t); o.stop(t+dur+.01);
  } catch(e){}
}
function playSound(n) {
  switch(n) {
    case 'attack':  tone(400,'triangle',.13,.28); break;
    case 'defend':  tone(700,'sine',.09,.22); tone(920,'sine',.07,.16,.06); break;
    case 'take':    tone(180,'sawtooth',.3,.18); tone(120,'sawtooth',.22,.12,.12); break;
    case 'deal':    [0,1,2,3].forEach(i=>tone(500+i*55,'sine',.07,.13,i*.058)); break;
    case 'win':     [330,415,495,660].forEach((f,i)=>tone(f,'sine',.28,.22,i*.12)); break;
    case 'error':   tone(130,'square',.15,.18); break;
  }
}

// ── ROOMS LIST ────────────────────────────────────────────────────────────────
let roomsRefreshTimer = null;
async function loadRooms() {
  try {
    const resp = await fetch('/rooms');
    const list = await resp.json();
    const el = document.getElementById('rooms-list');
    if (!el) return;
    if (!list.length) {
      el.innerHTML='<div class="rooms-empty">Немає відкритих кімнат</div>'; return;
    }
    el.innerHTML=list.map(r=>`
      <div class="room-item" onclick="quickJoin('${esc(r.room_id)}')">
        <span class="room-code">${esc(r.room_id)}</span>
        <span class="room-players">${r.player_count}/${r.max_players} грав.</span>
        <span class="room-join-arr">→</span>
      </div>`).join('');
  } catch(e) {
    const el=document.getElementById('rooms-list');
    if(el) el.innerHTML='<div class="rooms-empty">—</div>';
  }
}
function quickJoin(rid) {
  document.getElementById('inp-room').value=rid.toUpperCase();
  roomId=rid.toUpperCase();
  getACtx();
  connectWS();
}
function startRoomsRefresh() {
  stopRoomsRefresh();
  loadRooms();
  roomsRefreshTimer=setInterval(loadRooms, 5000);
}
function stopRoomsRefresh() {
  if (roomsRefreshTimer) { clearInterval(roomsRefreshTimer); roomsRefreshTimer=null; }
}

// ── INIT ──────────────────────────────────────────────────────────────────────
window.onload = () => {
  const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  if (!tg?.initDataUnsafe?.user && !isLocal) {
    document.getElementById('s-join').innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;padding:2rem;text-align:center;gap:1.5rem">
        <div class="logo">♠ ДУРЕНЬ ♠</div>
        <div style="color:var(--purple);font-size:15px;line-height:1.7">
          Гра доступна лише<br>через Telegram бота.<br><br>
          Відкрийте бота та натисніть<br><b style="color:var(--gold)">🃏 Створити гру</b>
        </div>
      </div>`;
    return;
  }

  if (tg?.initDataUnsafe?.user) {
    const u = tg.initDataUnsafe.user;
    myName = u.first_name || u.username || 'Гравець';
    myId = String(u.id);
  } else {
    myId = 'user_'+Math.random().toString(36).slice(2,8);
    myName = 'Dev_' + myId.slice(-4);
  }
  initDragListeners();

  const p = new URLSearchParams(location.search), rp = p.get('room')||'';
  if (rp) {
    document.getElementById('inp-room').value = rp.toUpperCase();
    roomId = rp.toUpperCase();
    getACtx();
    connectWS();
  } else {
    startRoomsRefresh();
  }
};

// ── CONNECT ───────────────────────────────────────────────────────────────────
async function createAndJoin() {
  try {
    const resp = await fetch('/room/create', {method:'POST'});
    if (!resp.ok) throw new Error();
    const data = await resp.json();
    roomId = data.room_id;
    document.getElementById('inp-room').value = roomId;
    getACtx();
    connectWS();
  } catch(e) {
    toast('Помилка створення кімнати');
  }
}
function joinRoom() {
  roomId = document.getElementById('inp-room').value.trim().toUpperCase();
  if (!roomId) { toast('Введіть код кімнати'); return; }
  getACtx();
  connectWS();
}
function connectWS() {
  intentionalClose = false;
  if (ws) { try { ws.close(); } catch(e){} }
  ws = new WebSocket(`${SERVER}/ws/${roomId}/${myId}/${encodeURIComponent(myName)}`);
  ws.onopen = () => showScreen('s-lobby');
  ws.onmessage = e => handleMsg(JSON.parse(e.data));
  ws.onerror = () => toast('Помилка підключення');
  ws.onclose = () => { if (!intentionalClose) { toast("З'єднання втрачено..."); setTimeout(connectWS,2000); } };
  setInterval(()=>{ if(ws?.readyState===1) ws.send(JSON.stringify({action:'ping'})); },25000);
}
function send(obj) { if(ws?.readyState===1) ws.send(JSON.stringify(obj)); }

// ── MESSAGES ──────────────────────────────────────────────────────────────────
function handleMsg(msg) {
  if (msg.type==='state') { gameState=msg.state; applyState(); }
  else if (msg.type==='error') { playSound('error'); toast(msg.msg); }
  else if (msg.type==='player_disconnected') { toast('Гравець відключився'); }
}

function applyState() {
  const s = gameState;
  cancelDrag();
  surrenderConfirm = false;

  const sig = JSON.stringify((s.pairs||[]).map(p=>({
    a: p.attack?p.attack.rank+p.attack.suit:'',
    d: p.defend?p.defend.rank+p.defend.suit:''
  })));
  if (prevPairsSig !== null) {
    const prev=JSON.parse(prevPairsSig), curr=JSON.parse(sig);
    if (prevPhase==='lobby' && s.phase==='attack') playSound('deal');
    else if (curr.length>prev.length) playSound('attack');
    else if (curr.length>0 && curr.length===prev.length && curr.some((c,i)=>c.d&&(!prev[i]||!prev[i].d))) playSound('defend');
    else if (curr.length===0 && prev.length>0) playSound(s.message?.includes('взяв')?'take':'deal');
  }
  prevPairsSig=sig; prevPhase=s.phase;

  if (s.phase==='lobby') { renderLobby(); return; }
  if (s.phase==='end')   { renderEnd();   return; }
  showScreen('s-game');
  renderTopBar(); renderOpponents(); renderTable();
  renderActions(); renderHand();
  document.getElementById('msg-bar').textContent = s.message||'';
}

// ── LOBBY ─────────────────────────────────────────────────────────────────────
function renderLobby() {
  showScreen('s-lobby');
  const s=gameState;
  document.getElementById('lobby-room-id').textContent='Кімната: '+s.room_id;
  document.getElementById('player-list').innerHTML=s.players.map(p=>`
    <div class="player-item">
      <div class="player-avatar">${p.name[0].toUpperCase()}</div>
      <div class="player-info">
        <div class="player-name">${esc(p.name)}${p.id===myId?' (ви)':''}</div>
        <div class="player-tag">${p.id===s.host_id?'👑 Хост':'Гравець'}</div>
      </div>
    </div>`).join('');
  const isHost=s.host_id===myId;
  document.getElementById('btn-start').style.display=isHost&&s.players.length>=2?'block':'none';
  document.getElementById('lobby-hint').textContent=
    isHost?(s.players.length<2?'Чекаємо ще гравців...':'Можна починати!'):'Чекаємо хоста...';
  document.getElementById('btn-leave').style.display='block';
}
function startGame() { send({action:'start'}); }
function leaveRoom() {
  send({action:'leave_room'});
  intentionalClose = true;
  if (ws) { try { ws.close(); } catch(e){} ws=null; }
  roomId='';
  document.getElementById('inp-room').value='';
  showScreen('s-join');
  loadRooms();
}

// ── GAME RENDER ───────────────────────────────────────────────────────────────
function renderTopBar() {
  const s=gameState, tc=s.trump_card;
  const mini=document.getElementById('deck-trump-mini');
  const rank=document.getElementById('dtm-rank');
  const suit=document.getElementById('dtm-suit');
  if (tc && tc.rank) {
    rank.textContent=tc.rank; suit.textContent=tc.suit;
    mini.className='deck-trump-mini trump '+((tc.suit==='♥'||tc.suit==='♦')?'red':'black');
  } else {
    rank.textContent='—'; suit.textContent='';
    mini.className='deck-trump-mini black';
  }
  document.getElementById('deck-num').textContent=s.deck_count;
  const att=s.players.find(p=>p.id===s.attacker_id);
  const def=s.players.find(p=>p.id===s.defender_id);
  document.getElementById('turn-text').textContent=(att?.name||'?')+' → '+(def?.name||'?');
}

function renderOpponents() {
  const s=gameState;
  document.getElementById('opponents-row').innerHTML=s.players.map(p=>{
    let cls='opp-chip';
    if(p.out) cls+=' is-out';
    else if(p.id===s.attacker_id) cls+=' is-attacker';
    else if(p.id===s.defender_id) cls+=' is-defender';
    const role=p.out?'вийшов':(p.id===s.attacker_id?'атакує':(p.id===s.defender_id?'відбиває':''));
    return `<div class="${cls}">
      <div class="opp-name">${esc(p.name)}${p.id===myId?' (я)':''}</div>
      <div class="opp-count">${p.out?'✓':p.card_count}</div>
      <div class="opp-role">${role}</div>
    </div>`;
  }).join('');
}

function renderTable() {
  const s=gameState;
  const zone=document.getElementById('table-zone');
  const hintBar=document.getElementById('hint-bar');
  if (!s.pairs||s.pairs.length===0) {
    zone.innerHTML='<div class="table-empty">Стіл порожній</div>';
    hintBar.innerHTML=''; return;
  }
  const amDef=myId===s.defender_id;
  const tapDefend=amDef&&s.phase==='defend'&&selectedHandCard;
  hintBar.innerHTML=tapDefend?'<div class="drag-hint">Натисніть або перетягніть на карту яку хочете відбити</div>':'';

  zone.innerHTML='<div class="pairs-wrap">'+s.pairs.map((pair,i)=>{
    const hasDef=!!pair.defend;
    const isTappable=tapDefend&&!hasDef;
    let aCls='card '+clr(pair.attack)+' card-atk'+(hasDef?' beaten':'')+(isTappable?' tappable':'');
    if(trump(pair.attack)) aCls+=' trump';
    const aClick=isTappable?`onclick="onTableTap(${i})"` : '';
    const aHtml=`<div class="${aCls}" ${aClick} data-pair="${i}">
      <div class="card-rank">${esc(pair.attack.rank)}</div>
      <div class="card-suit">${pair.attack.suit}</div>
    </div>`;
    let dHtml='';
    if (hasDef) {
      let dCls='card '+clr(pair.defend)+' card-def';
      if(trump(pair.defend)) dCls+=' trump trump-def';
      dHtml=`<div class="${dCls}"><div class="card-rank">${esc(pair.defend.rank)}</div><div class="card-suit">${pair.defend.suit}</div></div>`;
    }
    return `<div class="pair">${aHtml}${dHtml}</div>`;
  }).join('')+'</div>';
}

function renderHand() {
  const s=gameState, me=s.players.find(p=>p.id===myId);
  if (!me) return;
  document.getElementById('hand-label').textContent=me.name+' — '+me.card_count+' карт';
  const row=document.getElementById('hand-row');
  if (!me.hand||me.hand.length===0) {
    row.innerHTML='<span style="color:var(--purple);font-size:12px">Немає карт</span>'; return;
  }
  const amAtt=myId===s.attacker_id, amDef=myId===s.defender_id;
  const active=(amAtt&&(s.phase==='attack'||s.phase==='defend'||s.phase==='taking'))||(amDef&&s.phase==='defend');
  row.innerHTML=me.hand.map((card,i)=>{
    const sel=selectedHandCard&&card.rank===selectedHandCard.rank&&card.suit===selectedHandCard.suit;
    let cls='card '+clr(card)+' card-hand';
    if(trump(card)) cls+=' trump';
    if(sel) cls+=' selected';
    if(!active) cls+=' dim';
    return `<div class="${cls}" data-hidx="${i}">
      <div class="card-rank">${esc(card.rank)}</div>
      <div class="card-suit">${card.suit}</div>
    </div>`;
  }).join('');
}

function renderActions() {
  const s=gameState, bar=document.getElementById('actions-bar');
  bar.innerHTML='';
  const amAtt=myId===s.attacker_id, amDef=myId===s.defender_id;
  const undefended=(s.pairs||[]).filter(p=>!p.defend).length;
  const me=s.players.find(p=>p.id===myId);

  // ── TAKING phase: wait for all non-defenders to pass ──────────────────────
  if (s.phase==='taking') {
    if (!amDef && me && !me.out) {
      const hasPassed=Array.isArray(s.pass_set)&&s.pass_set.includes(myId);
      if (!hasPassed)
        addBtn(bar,'✓ Пас','btn-blue',()=>send({action:'pass'}));
    }
    if (me&&!me.out) {
      if (surrenderConfirm) {
        const lbl=document.createElement('span');
        lbl.className='surr-confirm'; lbl.textContent='Здатися?';
        bar.appendChild(lbl);
        addBtn(bar,'Так','btn-red',()=>{surrenderConfirm=false;send({action:'surrender'});});
        addBtn(bar,'Ні','btn-ghost',()=>{surrenderConfirm=false;renderActions();});
      } else {
        addBtn(bar,'🏳️ Здатися','btn-ghost',()=>{surrenderConfirm=true;renderActions();});
      }
    }
    return;
  }

  // ── DEFEND / ATTACK phases ─────────────────────────────────────────────────
  if (amDef&&s.phase==='defend')
    addBtn(bar,'🤚 Взяти карти','btn-red',()=>send({action:'take'}));
  if (amAtt&&s.phase==='attack'&&s.pairs&&s.pairs.length>0&&undefended===0)
    addBtn(bar,'✓ Завершити хід','btn-blue',()=>{selectedHandCard=null;send({action:'end_turn'});});
  if (selectedHandCard&&amDef&&s.phase==='defend')
    addBtn(bar,'Скасувати','btn-ghost',()=>{selectedHandCard=null;renderHand();renderTable();renderActions();});

  if (me&&!me.out) {
    if (surrenderConfirm) {
      const lbl=document.createElement('span');
      lbl.className='surr-confirm'; lbl.textContent='Здатися?';
      bar.appendChild(lbl);
      addBtn(bar,'Так','btn-red',()=>{surrenderConfirm=false;send({action:'surrender'});});
      addBtn(bar,'Ні','btn-ghost',()=>{surrenderConfirm=false;renderActions();});
    } else {
      addBtn(bar,'🏳️ Здатися','btn-ghost',()=>{surrenderConfirm=true;renderActions();});
    }
  }
}

function addBtn(bar,text,cls,fn) {
  const b=document.createElement('button');
  b.className='action-btn '+cls; b.textContent=text; b.onclick=fn; bar.appendChild(b);
}

// ── DRAG SYSTEM ───────────────────────────────────────────────────────────────
function initDragListeners() {
  const handRow = document.getElementById('hand-row');
  handRow.addEventListener('mousedown', onDragPointerDown);
  handRow.addEventListener('touchstart', onDragPointerDown, { passive: false });
  document.addEventListener('mousemove', onDragPointerMove);
  document.addEventListener('touchmove', onDragPointerMove, { passive: false });
  document.addEventListener('mouseup', onDragPointerUp);
  document.addEventListener('touchend', onDragPointerUp);
  document.addEventListener('touchcancel', cancelDrag);
}

function getXY(e) {
  if (e.changedTouches?.length) return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
  if (e.touches?.length)        return { x: e.touches[0].clientX,        y: e.touches[0].clientY };
  return { x: e.clientX, y: e.clientY };
}

function onDragPointerDown(e) {
  cancelDrag();
  const cardEl = e.target.closest('.card-hand');
  if (!cardEl) return;
  if (cardEl.classList.contains('dim')) return;

  const s = gameState;
  if (!s) return;
  const amAtt = myId===s.attacker_id, amDef = myId===s.defender_id;
  if (!amAtt && !amDef) return;
  if (amAtt && s.phase!=='attack' && s.phase!=='defend' && s.phase!=='taking') return;
  if (amDef && s.phase!=='defend') return;

  const idx = parseInt(cardEl.dataset.hidx);
  const me = s.players.find(p=>p.id===myId);
  if (!me||!me.hand||idx>=me.hand.length) return;

  const {x,y} = getXY(e);
  drag = { card: me.hand[idx], idx, role: amAtt?'attacker':'defender',
           startX:x, startY:y, active:false, ghostEl:null, srcEl:cardEl };

  if (e.touches) e.preventDefault();
}

function onDragPointerMove(e) {
  if (!drag) return;
  const {x,y} = getXY(e);
  const dx = x-drag.startX, dy = y-drag.startY;

  if (!drag.active) {
    if (Math.sqrt(dx*dx+dy*dy) < DRAG_THRESHOLD) return;
    drag.active = true;
    drag.ghostEl = document.createElement('div');
    drag.ghostEl.className = 'card '+clr(drag.card)+' card-ghost';
    if (trump(drag.card)) drag.ghostEl.className += ' trump';
    drag.ghostEl.innerHTML = `<div class="card-rank">${esc(drag.card.rank)}</div><div class="card-suit">${drag.card.suit}</div>`;
    document.body.appendChild(drag.ghostEl);
    drag.srcEl.classList.add('is-dragging');
    if (drag.role==='attacker')
      document.getElementById('table-zone').classList.add('drop-target');
  }

  if (drag.active) {
    e.preventDefault();
    drag.ghostEl.style.left = (x-26)+'px';
    drag.ghostEl.style.top  = (y-37)+'px';
    if (drag.role==='defender') highlightDefendTarget(x, y);
  }
}

function highlightDefendTarget(x, y) {
  document.querySelectorAll('.card-atk').forEach(el=>el.classList.remove('drop-hover'));
  drag.ghostEl.style.display='none';
  const el = document.elementFromPoint(x, y);
  drag.ghostEl.style.display='';
  const atkEl = el?.closest?.('.card-atk');
  if (atkEl && !atkEl.classList.contains('beaten'))
    atkEl.classList.add('drop-hover');
}

function onDragPointerUp(e) {
  if (!drag) return;
  const {x,y} = getXY(e);

  if (!drag.active) {
    cleanupDragVisuals();
    const d = drag; drag = null;
    onHandTap(d);
    return;
  }

  if (drag.ghostEl) { drag.ghostEl.style.display='none'; }
  const el = document.elementFromPoint(x, y);
  if (drag.ghostEl) drag.ghostEl.style.display='';

  if (drag.role==='attacker') {
    const zone = document.getElementById('table-zone');
    const rect = zone.getBoundingClientRect();
    if (x>=rect.left && x<=rect.right && y>=rect.top && y<=rect.bottom) {
      send({ action:'attack', card:drag.card });
    }
  } else {
    const atkEl = el?.closest?.('.card-atk');
    if (atkEl && !atkEl.classList.contains('beaten')) {
      const pairIdx = parseInt(atkEl.dataset.pair);
      const pair = gameState?.pairs?.[pairIdx];
      if (pair && !pair.defend) {
        send({ action:'defend', attack_card:pair.attack, defend_card:drag.card });
        selectedHandCard = null;
      }
    }
  }

  cleanupDragVisuals();
  drag = null;
}

function cleanupDragVisuals() {
  document.querySelectorAll('.card-ghost').forEach(el => el.remove());
  if (drag?.srcEl) drag.srcEl.classList.remove('is-dragging');
  document.querySelectorAll('.card-hand.is-dragging').forEach(el => el.classList.remove('is-dragging'));
  document.getElementById('table-zone')?.classList.remove('drop-target');
  document.querySelectorAll('.card-atk').forEach(el => el.classList.remove('drop-hover'));
}

function cancelDrag() {
  cleanupDragVisuals();
  drag = null;
}

// ── TAP HANDLERS ──────────────────────────────────────────────────────────────
function onHandTap(d) {
  const s = gameState;
  if (d.role==='defender' && s.phase==='defend') {
    selectedHandCard = (selectedHandCard?.rank===d.card.rank&&selectedHandCard?.suit===d.card.suit)?null:d.card;
    renderHand(); renderTable(); renderActions();
  }
}

function onTableTap(pairIdx) {
  if (!selectedHandCard) { toast('Спочатку оберіть карту в руці'); return; }
  const pair = gameState?.pairs?.[pairIdx];
  if (!pair||pair.defend) { toast('Ця карта вже відбита'); return; }
  send({ action:'defend', attack_card:pair.attack, defend_card:selectedHandCard });
  selectedHandCard = null;
}

// ── END ───────────────────────────────────────────────────────────────────────
function renderEnd() {
  playSound('win');
  showScreen('s-end');
  const s=gameState;
  const sorted=[...s.players].sort((a,b)=>(a.finish_pos||9999)-(b.finish_pos||9999));
  const loser=sorted[sorted.length-1];
  document.getElementById('end-sub').textContent=loser?'🃏 Дурень: '+loser.name:'';
  const medals=['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣'];
  document.getElementById('rank-list').innerHTML=sorted.map((p,i)=>`
    <div class="rank-row">
      <span class="rank-medal">${medals[i]||'•'}</span>
      <span class="rank-name">${esc(p.name)}${p.id===myId?' (ви)':''}</span>
      <span class="rank-tag">${i===sorted.length-1?'🃏 Дурень':(i===0?'Переможець':'')}</span>
    </div>`).join('');
}

function backToLobby() { send({action:'rematch'}); }
function goHome() {
  send({action:'leave_end'});
  intentionalClose = true;
  if (ws) { try { ws.close(); } catch(e){} ws=null; }
  roomId='';
  document.getElementById('inp-room').value='';
  showScreen('s-join');
  startRoomsRefresh();
}

// ── UTILS ─────────────────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (id==='s-join') startRoomsRefresh(); else stopRoomsRefresh();
}
function toast(msg) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2200);
}
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function clr(c) { return(c.suit==='♥'||c.suit==='♦')?'red':'black'; }
function trump(c) { return gameState&&c.suit===gameState.trump_suit; }
