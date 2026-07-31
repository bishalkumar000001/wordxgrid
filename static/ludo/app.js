/* ============================================================
   Ludo King Mini App — Complete JavaScript Application
   Socket.IO + Telegram WebApp SDK + Board Rendering
   ============================================================ */

'use strict';

// ── Telegram WebApp ───────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.enableClosingConfirmation();
}

// ── Board coordinate constants ────────────────────────────────

const TRACK = [
  [6,1],[6,2],[6,3],[6,4],[6,5],
  [5,5],[4,5],[3,5],[2,5],[1,5],[0,5],
  [0,6],[0,7],
  [1,8],[2,8],[3,8],[4,8],[5,8],[6,8],
  [6,9],[6,10],[6,11],[6,12],[6,13],[6,14],
  [7,14],[8,14],
  [8,13],[8,12],[8,11],[8,10],[8,9],[8,8],[8,7],
  [9,7],[10,7],[11,7],[12,7],[13,7],
  [14,7],[14,6],
  [13,6],[12,6],[11,6],[10,6],[9,6],[8,6],
  [8,5],[8,4],[8,3],[8,2],[8,1],
];

const SAFE_ABS = new Set([0, 8, 13, 21, 26, 34, 39, 47]);

const COLOR_OFFSETS = { red: 0, green: 13, yellow: 26, blue: 39 };

const HOME_COLS = {
  red:    [[7,1],[7,2],[7,3],[7,4],[7,5],[7,6]],
  green:  [[1,7],[2,7],[3,7],[4,7],[5,7],[6,7]],
  yellow: [[7,13],[7,12],[7,11],[7,10],[7,9],[7,8]],
  blue:   [[13,7],[12,7],[11,7],[10,7],[9,7],[8,7]],
};

const HOME_BASES = {
  red:    [[1,1],[1,3],[3,1],[3,3]],
  green:  [[1,11],[1,13],[3,11],[3,13]],
  yellow: [[11,11],[11,13],[13,11],[13,13]],
  blue:   [[11,1],[11,3],[13,1],[13,3]],
};

const CENTER = [7, 7];

const COLOR_ORDER = ['red','green','yellow','blue'];

const DICE_EMOJI = { 1:'⚀', 2:'⚁', 3:'⚂', 4:'⚃', 5:'⚄', 6:'⚅' };

const MEDALS = ['🥇','🥈','🥉','4️⃣'];

// ── Application State ─────────────────────────────────────────
const state = {
  socket:       null,
  user:         null,
  room:         null,
  gameState:    null,
  myColor:      null,
  validMoves:   [],
  selectedDice: null,
  turnTimer:    null,
  timerVal:     60,
  maxPlayers:   4,
  isPrivate:    false,
};

// ── Cell map (15x15) ──────────────────────────────────────────
let BOARD_MAP = null;

function buildBoardMap() {
  const map = [];
  for (let r = 0; r < 15; r++) {
    map.push([]);
    for (let c = 0; c < 15; c++) {
      map[r].push({ type: 'blank', color: null, extra: null });
    }
  }

  // Home area backgrounds (6×6 corners)
  const homeAreas = [
    { color: 'red',    rs: 0, re: 5, cs: 0, ce: 5 },
    { color: 'green',  rs: 0, re: 5, cs: 9, ce: 14 },
    { color: 'yellow', rs: 9, re: 14, cs: 9, ce: 14 },
    { color: 'blue',   rs: 9, re: 14, cs: 0, ce: 5 },
  ];
  for (const { color, rs, re, cs, ce } of homeAreas) {
    for (let r = rs; r <= re; r++)
      for (let c = cs; c <= ce; c++)
        map[r][c] = { type: 'home', color, extra: null };
  }

  // Home base yard slots (circles inside home area)
  for (const [color, slots] of Object.entries(HOME_BASES)) {
    for (const [r, c] of slots)
      map[r][c] = { type: 'yard', color, extra: null };
  }

  // Main track
  TRACK.forEach(([r, c], idx) => {
    let type = 'path';
    let extra = null;
    if (SAFE_ABS.has(idx)) {
      // Entry cells (colored) vs regular safe cells
      if (idx === 0)  { type = 'safe'; extra = 'entry-red'; }
      else if (idx === 13) { type = 'safe'; extra = 'entry-green'; }
      else if (idx === 26) { type = 'safe'; extra = 'entry-yellow'; }
      else if (idx === 39) { type = 'safe'; extra = 'entry-blue'; }
      else { type = 'safe'; }
    }
    map[r][c] = { type, color: null, extra, trackIdx: idx };
  });

  // Home columns (override some cells with colored path)
  for (const [color, cells] of Object.entries(HOME_COLS)) {
    cells.forEach(([r, c], idx) => {
      map[r][c] = { type: 'homecol', color, extra: null, homeIdx: idx };
    });
  }

  // Center 3×3
  const centerTriangles = [
    { r: 6, c: 6, tri: 'red' },
    { r: 6, c: 8, tri: 'green' },
    { r: 8, c: 8, tri: 'yellow' },
    { r: 8, c: 6, tri: 'blue' },
  ];
  for (const { r, c, tri } of centerTriangles)
    map[r][c] = { type: 'center-tri', color: tri, extra: null };
  map[7][7] = { type: 'center', color: null, extra: null };

  return map;
}

// ── Board rendering ───────────────────────────────────────────

function initBoard() {
  BOARD_MAP = buildBoardMap();
  const board = document.getElementById('ludo-board');
  board.innerHTML = '';

  for (let r = 0; r < 15; r++) {
    for (let c = 0; c < 15; c++) {
      const cell = document.createElement('div');
      cell.className = 'bcell';
      const info = BOARD_MAP[r][c];
      switch (info.type) {
        case 'blank':      cell.classList.add('blank'); break;
        case 'home':       cell.classList.add(`home-${info.color}`); break;
        case 'yard':       cell.classList.add(`home-${info.color}`, `yard-${info.color}`); break;
        case 'path':       cell.classList.add('path'); break;
        case 'safe':
          cell.classList.add('safe');
          if (info.extra) cell.classList.add(info.extra);
          break;
        case 'homecol':    cell.classList.add(`col-${info.color}`); break;
        case 'center-tri': cell.classList.add(`center-tri-${info.color}`); break;
        case 'center':     cell.classList.add('center'); break;
      }
      cell.dataset.row = r;
      cell.dataset.col = c;
      board.appendChild(cell);
    }
  }
}

function getCellElement(row, col) {
  return document.querySelector(`#ludo-board .bcell[data-row="${row}"][data-col="${col}"]`);
}

function getCellPercent(row, col) {
  return { x: (col + 0.5) / 15 * 100, y: (row + 0.5) / 15 * 100 };
}

// ── Tokens ────────────────────────────────────────────────────

const _tokenElements = {};

function getOrCreateToken(color, pieceIdx) {
  const id = `${color}_${pieceIdx}`;
  if (!_tokenElements[id]) {
    const el = document.createElement('div');
    el.className = `token ${color}`;
    el.dataset.color = color;
    el.dataset.pieceIdx = pieceIdx;
    el.textContent = pieceIdx + 1;
    el.addEventListener('click', () => onTokenClick(color, pieceIdx));
    document.getElementById('tokens-layer').appendChild(el);
    _tokenElements[id] = el;
  }
  return _tokenElements[id];
}

function placeToken(color, pieceIdx, relPos) {
  const el = getOrCreateToken(color, pieceIdx);

  let coords;
  if (relPos === -1) {
    const slot = HOME_BASES[color][pieceIdx];
    coords = getCellPercent(slot[0], slot[1]);
  } else if (relPos === 58) {
    const offset = { red:[-0.5,-0.5], green:[-0.5,0.5], yellow:[0.5,0.5], blue:[0.5,-0.5] }[color];
    coords = { x: 50 + offset[1]*3.2, y: 50 + offset[0]*3.2 };
    el.classList.add('finished');
  } else if (relPos >= 52) {
    const slot = HOME_COLS[color][relPos - 52];
    coords = getCellPercent(slot[0], slot[1]);
    el.classList.remove('finished');
  } else {
    const absPos = (relPos + COLOR_OFFSETS[color]) % 52;
    const slot = TRACK[absPos];
    coords = getCellPercent(slot[0], slot[1]);
    el.classList.remove('finished');
  }

  // Offset tokens that share a cell
  el.style.left = `${coords.x}%`;
  el.style.top  = `${coords.y}%`;
}

function renderTokens(gameState) {
  if (!gameState) return;
  for (const player of gameState.players) {
    const { color, pieces } = player;
    pieces.forEach((pos, idx) => placeToken(color, idx, pos));
  }
}

function clearHighlights() {
  document.querySelectorAll('.token.selectable').forEach(t => t.classList.remove('selectable'));
  document.querySelectorAll('.bcell.highlight').forEach(c => c.classList.remove('highlight'));
}

function highlightValidPieces(validMoves) {
  clearHighlights();
  for (const pieceIdx of validMoves) {
    const el = _tokenElements[`${state.myColor}_${pieceIdx}`];
    if (el) el.classList.add('selectable');
  }
}

function onTokenClick(color, pieceIdx) {
  if (color !== state.myColor) return;
  if (!state.validMoves.includes(pieceIdx)) return;
  emitMovePiece(pieceIdx);
}

// ── Position helpers ──────────────────────────────────────────

function relToAbs(rel, color) {
  return (rel + COLOR_OFFSETS[color]) % 52;
}

function getValidMoves(player, dice) {
  const valid = [];
  player.pieces.forEach((pos, i) => {
    if (pos === 58) return;
    if (pos === -1) { if (dice === 6) valid.push(i); }
    else { if (pos + dice <= 58) valid.push(i); }
  });
  return valid;
}

// ── Turn timer ────────────────────────────────────────────────

function startTurnTimer(seconds = 60) {
  stopTurnTimer();
  state.timerVal = seconds;
  const el = document.getElementById('turn-timer');
  _renderTimer(el, state.timerVal);
  state.turnTimer = setInterval(() => {
    state.timerVal--;
    _renderTimer(el, state.timerVal);
    if (state.timerVal <= 0) stopTurnTimer();
  }, 1000);
}

function stopTurnTimer() {
  if (state.turnTimer) { clearInterval(state.turnTimer); state.turnTimer = null; }
}

function _renderTimer(el, val) {
  el.textContent = Math.max(0, val);
  if (val <= 10) el.classList.add('warning');
  else el.classList.remove('warning');
}

// ── Socket.IO connection ──────────────────────────────────────

function connectSocket() {
  const socket = io({ transports: ['websocket', 'polling'], reconnectionDelay: 1000 });
  state.socket = socket;

  socket.on('connect', () => {
    console.log('Connected:', socket.id);
    const initData = tg?.initData || '';
    socket.emit('authenticate', { initData });
  });

  socket.on('disconnect', () => {
    console.log('Disconnected');
    showToast('Connection lost. Reconnecting…');
  });

  socket.on('connect_error', (err) => {
    console.error('Connection error:', err);
  });

  // ── Auth ──────────────────────────────────────────────────────
  socket.on('authenticated', (data) => {
    state.user = data.user;
    renderUserAvatar();

    if (data.reconnected_room) {
      const room = data.reconnected_room;
      state.room = room;
      if (room.status === 'playing' && room.game_state) {
        state.gameState = room.game_state;
        setMyColor(room);
        showScreen('game');
        renderGame(room.game_state);
        updateTurnUI(room.game_state);
      } else {
        showScreen('room');
        renderRoom(room);
      }
    } else {
      showScreen('lobby');
      loadPublicRooms();
    }
  });

  socket.on('error', (data) => {
    showToast(`⚠️ ${data.message || data.code}`);
  });

  // ── Room events ───────────────────────────────────────────────
  socket.on('room_joined', (room) => {
    state.room = room;
    setMyColor(room);
    showScreen('room');
    renderRoom(room);
  });

  socket.on('room_update', (room) => {
    state.room = room;
    setMyColor(room);
    if (currentScreen() === 'room') renderRoom(room);
  });

  socket.on('kicked', (data) => {
    state.room = null;
    state.myColor = null;
    showScreen('lobby');
    showToast(`❌ ${data.reason || 'You were kicked'}`);
    loadPublicRooms();
  });

  socket.on('left_room', () => {
    state.room = null;
    state.myColor = null;
    showScreen('lobby');
    loadPublicRooms();
  });

  socket.on('rooms_list', (data) => {
    renderRoomsList(data.rooms || []);
  });

  // ── Game events ────────────────────────────────────────────────
  socket.on('game_started', (data) => {
    state.room = data;
    state.gameState = data.game_state;
    setMyColor(data);
    initBoard();
    showScreen('game');
    renderGame(data.game_state);
    updateTurnUI(data.game_state);
    showToast('🎲 Game Started!');
  });

  socket.on('game_state_sync', (data) => {
    state.room = data;
    state.gameState = data.game_state;
    setMyColor(data);
    if (currentScreen() !== 'game') {
      initBoard();
      showScreen('game');
    }
    renderGame(data.game_state);
    updateTurnUI(data.game_state);
  });

  socket.on('dice_rolled', (data) => {
    const { player_idx, dice, valid_moves, penalty, game_state } = data;
    state.gameState = game_state;
    state.validMoves = valid_moves || [];

    // Animate dice
    animateDice(dice, () => {
      if (penalty) {
        showToast(`😱 3 sixes! Piece sent home!`);
      }
      const isMyTurn = isMyPlayerTurn(game_state);
      if (isMyTurn && state.validMoves.length > 0) {
        highlightValidPieces(state.validMoves);
        showToast('🎯 Choose a piece to move!');
      } else if (isMyTurn && state.validMoves.length === 0) {
        showToast('😞 No valid moves. Turn skipped.');
      }
    });
  });

  socket.on('piece_moved', (data) => {
    const { player_idx, piece_idx, dice, captures, piece_finished, game_state, game_over } = data;
    state.gameState = game_state;
    state.validMoves = [];
    clearHighlights();

    // Animate token movement
    renderTokens(game_state);

    // Show captures
    if (captures && captures.length > 0) {
      for (const cap of captures) {
        showCapture(`💥 ${cap.player_name}'s piece sent home!`);
        const capEl = _tokenElements[`${cap.color}_${cap.piece_idx}`];
        if (capEl) {
          capEl.classList.add('captured');
          setTimeout(() => capEl.classList.remove('captured'), 500);
        }
      }
    }
    if (piece_finished) showToast('🏠 Piece reached home!');

    // Update player chips
    renderPlayerChips(game_state);
  });

  socket.on('extra_turn', (data) => {
    state.gameState = data.game_state;
    stopTurnTimer();
    if (isMyPlayerTurn(data.game_state)) {
      showToast('🎲 Extra turn! Roll again!');
      enableRollButton();
      startTurnTimer(60);
    }
    updateTurnBar(data.game_state);
  });

  socket.on('turn_changed', (data) => {
    state.gameState = data.game_state;
    state.validMoves = [];
    clearHighlights();
    stopTurnTimer();
    updateTurnUI(data.game_state);
    renderTokens(data.game_state);
  });

  socket.on('game_over', (data) => {
    const { rankings, players, game_state } = data;
    state.gameState = game_state;
    stopTurnTimer();
    clearHighlights();
    setTimeout(() => showResult(rankings, players), 600);
  });

  return socket;
}

// ── Helpers ───────────────────────────────────────────────────

function setMyColor(room) {
  if (!state.user || !room.players) return;
  const me = room.players.find(p => p.user_id === state.user.id);
  state.myColor = me ? me.color : null;
}

function isMyPlayerTurn(gs) {
  if (!gs || !state.myColor) return false;
  const cur = gs.players[gs.current_player_idx];
  return cur && cur.color === state.myColor;
}

function currentScreen() {
  const s = document.querySelector('.screen.active');
  return s ? s.id.replace('screen-', '') : null;
}

function enableRollButton() {
  const btn = document.getElementById('btn-roll-dice');
  btn.disabled = !isMyPlayerTurn(state.gameState);
}

// ── Screen navigation ─────────────────────────────────────────

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const target = document.getElementById(`screen-${name}`);
  if (target) {
    requestAnimationFrame(() => target.classList.add('active'));
  }
}

// ── Lobby UI ──────────────────────────────────────────────────

function renderUserAvatar() {
  const el = document.getElementById('lobby-avatar');
  if (!el || !state.user) return;
  const name = state.user.first_name || '?';
  if (state.user.photo_url) {
    el.innerHTML = `<img src="${state.user.photo_url}" alt="${name}">`;
  } else {
    el.textContent = name.charAt(0).toUpperCase();
  }
}

function loadPublicRooms() {
  if (state.socket) state.socket.emit('list_rooms');
}

function renderRoomsList(rooms) {
  const container = document.getElementById('rooms-list');
  if (!rooms.length) {
    container.innerHTML = `<div class="empty-state"><span class="empty-icon">🎮</span><p>No public rooms yet.<br>Create one!</p></div>`;
    return;
  }
  container.innerHTML = rooms.map(r => {
    const dotsHtml = r.players.map(p =>
      `<div class="room-dot ${p.color}"></div>`
    ).join('') + Array(r.max_players - r.players.length).fill('<div class="room-dot"></div>').join('');
    const host = r.players[0]?.name || 'Unknown';
    return `<div class="room-item" data-room-id="${r.room_id}">
      <div class="room-dots">${dotsHtml}</div>
      <div class="room-info">
        <div class="room-host">${escHtml(host)}'s Room</div>
        <div class="room-meta">${r.player_count}/${r.max_players} players</div>
      </div>
      <div class="room-count">${r.player_count}/${r.max_players}</div>
    </div>`;
  }).join('');

  container.querySelectorAll('.room-item').forEach(item => {
    item.addEventListener('click', () => {
      const roomId = item.dataset.roomId;
      state.socket.emit('join_room_by_id', { room_id: roomId });
    });
  });
}

// ── Room UI ───────────────────────────────────────────────────

function renderRoom(room) {
  if (!room) return;

  document.getElementById('room-invite-code').textContent = room.invite_code || '------';

  const countEl = document.getElementById('room-player-count');
  countEl.textContent = `${room.players.length}/${room.max_players}`;

  const listEl = document.getElementById('room-players-list');
  listEl.innerHTML = room.players.map(p => {
    const isHost = p.user_id === room.host_id;
    const isMe   = p.user_id === state.user?.id;
    let tags = '';
    if (isHost) tags += `<span class="player-tag host">👑 Host</span>`;
    if (p.ready && !isHost) tags += `<span class="player-tag ready">✅ Ready</span>`;
    else if (!p.ready && !isHost) tags += `<span class="player-tag waiting">⏳ Waiting</span>`;
    if (!p.connected) tags += `<span class="player-tag disconnected">⚡ AFK</span>`;

    const canKick = !isMe && state.user?.id === room.host_id && !isHost && room.status === 'waiting';
    const kickBtn = canKick ? `<button class="btn-kick" data-uid="${p.user_id}">Kick</button>` : '';

    return `<div class="player-row">
      <div class="player-color-dot ${p.color}"></div>
      <div class="player-name-wrap">
        <div class="player-name">${escHtml(p.name)}${isMe ? ' <small style="color:var(--accent)">(You)</small>' : ''}</div>
        <div class="player-sub">${p.color.charAt(0).toUpperCase() + p.color.slice(1)}</div>
      </div>
      ${tags}${kickBtn}
    </div>`;
  }).join('');

  // Kick buttons
  listEl.querySelectorAll('.btn-kick').forEach(btn => {
    btn.addEventListener('click', () => {
      const uid = parseInt(btn.dataset.uid);
      state.socket.emit('kick_player', { user_id: uid });
    });
  });

  // Status message
  const statusEl = document.getElementById('room-status-msg');
  const allReady = room.players.every(p => p.ready || p.user_id === room.host_id);
  const enoughPlayers = room.players.length >= 2;
  if (room.status === 'playing') {
    statusEl.textContent = '🎮 Game is in progress…';
  } else if (room.players.length < 2) {
    statusEl.textContent = '⏳ Waiting for more players…';
  } else if (!allReady) {
    statusEl.textContent = '⏳ Waiting for all players to be ready…';
  } else {
    statusEl.textContent = `✅ ${room.players.length} players ready!`;
  }

  // Buttons
  const isHost = state.user?.id === room.host_id;
  const isMe = room.players.find(p => p.user_id === state.user?.id);
  const myReady = isMe?.ready || false;

  const readyBtn = document.getElementById('btn-ready');
  const startBtn = document.getElementById('btn-start-game');

  if (isHost) {
    readyBtn.style.display = 'none';
    startBtn.style.display = 'block';
    startBtn.disabled = !(enoughPlayers && allReady);
    startBtn.textContent = enoughPlayers && allReady ? '▶️ Start Game!' : `▶️ Need ${2 - room.players.length} more players`;
  } else {
    readyBtn.style.display = 'block';
    startBtn.style.display = 'none';
    readyBtn.textContent = myReady ? '✅ Ready! (Click to unready)' : '⬜ Mark as Ready';
    readyBtn.className = `btn btn-full ${myReady ? 'btn-secondary' : 'btn-primary'}`;
    readyBtn.dataset.ready = myReady ? '1' : '0';
  }
}

// ── Game UI ───────────────────────────────────────────────────

function renderGame(gs) {
  if (!gs) return;
  renderTokens(gs);
  renderPlayerChips(gs);
}

function renderPlayerChips(gs) {
  const container = document.getElementById('players-mini');
  if (!gs || !container) return;
  container.innerHTML = gs.players.map((p, idx) => {
    const isActive = idx === gs.current_player_idx;
    const finished = p.finished_count >= 4;
    const finCount = p.finished_count || 0;
    return `<div class="player-chip ${isActive ? 'active' : ''} ${finished ? 'finished' : ''}" data-idx="${idx}">
      <div class="chip-dot ${p.color}"></div>
      <span>${escHtml(p.name.split(' ')[0])}</span>
      <span class="chip-score">${finCount}/4🏠</span>
    </div>`;
  }).join('');
}

function updateTurnUI(gs) {
  if (!gs) return;
  updateTurnBar(gs);

  const isMyTurn = isMyPlayerTurn(gs);
  const btn = document.getElementById('btn-roll-dice');
  btn.disabled = !(isMyTurn && !gs.dice_rolled);

  if (isMyTurn && !gs.dice_rolled) {
    startTurnTimer(60);
    showToast('🎲 Your turn! Roll the dice!');
  } else {
    stopTurnTimer();
    const cur = gs.players[gs.current_player_idx];
    if (cur) {
      const name = cur.name.split(' ')[0];
      showToast(`⏳ ${name}'s turn`);
    }
  }

  clearHighlights();
  state.validMoves = [];
}

function updateTurnBar(gs) {
  if (!gs) return;
  const cur = gs.players[gs.current_player_idx];
  if (!cur) return;
  const dot = document.getElementById('turn-dot');
  const nameEl = document.getElementById('turn-name');
  dot.style.background = colorToCSS(cur.color);
  const isMe = cur.color === state.myColor;
  nameEl.textContent = isMe ? `Your turn (${cur.color})` : `${cur.name}'s turn`;
}

function colorToCSS(color) {
  return { red:'#e84040', green:'#36c25e', yellow:'#f5c842', blue:'#4092d9' }[color] || '#888';
}

// ── Dice animation ────────────────────────────────────────────

function animateDice(value, callback) {
  const diceEl = document.getElementById('dice');
  const faceEl = document.getElementById('dice-face');
  diceEl.classList.add('rolling');

  let frames = 0;
  const interval = setInterval(() => {
    faceEl.textContent = DICE_EMOJI[Math.floor(Math.random() * 6) + 1];
    frames++;
    if (frames >= 8) {
      clearInterval(interval);
      faceEl.textContent = DICE_EMOJI[value] || value;
      diceEl.classList.remove('rolling');
      if (callback) callback();
    }
  }, 60);
}

// ── Result screen ─────────────────────────────────────────────

function showResult(rankings, players) {
  document.getElementById('result-title').textContent =
    rankings[0] !== undefined ? `${players[rankings[0]].name} Wins! 🎉` : 'Game Over!';

  const rankingsEl = document.getElementById('result-rankings');
  rankingsEl.innerHTML = rankings.map((pIdx, rank) => {
    const p = players[pIdx];
    if (!p) return '';
    const isMe = p.color === state.myColor;
    return `<div class="rank-row">
      <span class="rank-medal">${MEDALS[rank] || `${rank+1}.`}</span>
      <div class="rank-color-dot ${p.color}"></div>
      <span class="rank-name">${escHtml(p.name)}${isMe ? ' <span class="rank-you">(You)</span>' : ''}</span>
    </div>`;
  }).join('');

  showScreen('result');
}

// ── Captures log ──────────────────────────────────────────────

function showCapture(msg) {
  const log = document.getElementById('captures-log');
  const el = document.createElement('div');
  el.className = 'capture-msg';
  el.textContent = msg;
  log.innerHTML = '';
  log.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── Toast ─────────────────────────────────────────────────────

let _toastTimeout = null;
function showToast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  if (_toastTimeout) clearTimeout(_toastTimeout);
  _toastTimeout = setTimeout(() => el.classList.remove('show'), duration);
}

// ── Utility ───────────────────────────────────────────────────

function escHtml(str) {
  return String(str || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── SocketIO emitters ─────────────────────────────────────────

function emitRollDice() {
  if (state.socket) state.socket.emit('roll_dice');
}

function emitMovePiece(pieceIdx) {
  if (state.socket) state.socket.emit('move_piece', { piece_idx: pieceIdx });
  clearHighlights();
  state.validMoves = [];
  document.getElementById('btn-roll-dice').disabled = true;
}

// ── Event Listeners ───────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {

  // ── Lobby: max players segmented control
  document.querySelectorAll('.seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.maxPlayers = parseInt(btn.dataset.val);
    });
  });

  // ── Lobby: private toggle
  document.getElementById('toggle-private').addEventListener('change', e => {
    state.isPrivate = e.target.checked;
  });

  // ── Lobby: create room
  document.getElementById('btn-create-room').addEventListener('click', () => {
    if (!state.socket) return;
    state.socket.emit('create_room', {
      is_private:  state.isPrivate,
      max_players: state.maxPlayers,
    });
  });

  // ── Lobby: join by code
  document.getElementById('btn-join-code').addEventListener('click', () => {
    const code = document.getElementById('invite-code-input').value.trim().toUpperCase();
    if (!code) { showToast('Enter an invite code'); return; }
    if (state.socket) state.socket.emit('join_room_by_code', { invite_code: code });
  });
  document.getElementById('invite-code-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('btn-join-code').click();
  });

  // ── Lobby: refresh rooms
  document.getElementById('btn-refresh-rooms').addEventListener('click', loadPublicRooms);

  // ── Room: leave
  document.getElementById('btn-leave-room').addEventListener('click', () => {
    if (state.socket) state.socket.emit('leave_room');
  });

  // ── Room: copy invite code
  document.getElementById('btn-copy-invite').addEventListener('click', () => {
    const code = document.getElementById('room-invite-code').textContent;
    navigator.clipboard?.writeText(code).then(() => showToast('📋 Code copied!')).catch(() => {
      // Fallback
      const el = document.createElement('textarea');
      el.value = code;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      showToast('📋 Code copied!');
    });
  });

  // ── Room: share invite code
  document.getElementById('btn-share-invite').addEventListener('click', () => {
    const code = document.getElementById('room-invite-code').textContent;
    const msg = `Join my Ludo game! Code: ${code}`;
    if (tg?.shareToStory) {
      tg.shareToStory(msg);
    } else if (navigator.share) {
      navigator.share({ text: msg }).catch(() => {});
    } else {
      showToast('📤 Share: ' + code);
    }
  });

  // ── Room: ready button
  document.getElementById('btn-ready').addEventListener('click', () => {
    const btn = document.getElementById('btn-ready');
    const ready = btn.dataset.ready !== '1';
    if (state.socket) state.socket.emit('player_ready', { ready });
  });

  // ── Room: start game
  document.getElementById('btn-start-game').addEventListener('click', () => {
    if (state.socket) state.socket.emit('start_game');
  });

  // ── Game: roll dice
  document.getElementById('btn-roll-dice').addEventListener('click', () => {
    if (!isMyPlayerTurn(state.gameState)) return;
    if (state.gameState?.dice_rolled) return;
    emitRollDice();
    document.getElementById('btn-roll-dice').disabled = true;
  });

  // ── Result: play again
  document.getElementById('btn-play-again').addEventListener('click', () => {
    state.room = null;
    state.gameState = null;
    state.myColor = null;
    state.validMoves = [];
    clearHighlights();
    stopTurnTimer();
    showScreen('lobby');
    loadPublicRooms();
  });

  // ── Start connection ───────────────────────────────────────────
  connectSocket();
});
