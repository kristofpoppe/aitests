const express = require('express');
const { WebSocketServer } = require('ws');
const QRCode = require('qrcode');
const http = require('http');
const os = require('os');

const PORT = process.env.PORT || 3000;

function getLocalIP() {
  for (const ifaces of Object.values(os.networkInterfaces())) {
    for (const iface of ifaces) {
      if (iface.family === 'IPv4' && !iface.internal) return iface.address;
    }
  }
  return '127.0.0.1';
}

const LOCAL_IP = getLocalIP();
const BASE = `http://${LOCAL_IP}:${PORT}`;

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

app.use(express.static(__dirname));

// QR code image: /qr/1 or /qr/2
app.get('/qr/:team', async (req, res) => {
  const team = parseInt(req.params.team);
  if (team !== 1 && team !== 2) return res.sendStatus(400);
  const url = `${BASE}/monkey-banana-join.html?team=${team}`;
  try {
    const buf = await QRCode.toBuffer(url, {
      width: 180,
      margin: 1,
      color: {
        dark: team === 1 ? '#ff6b6b' : '#48dbfb',
        light: '#0d0d1a'
      }
    });
    res.setHeader('Content-Type', 'image/png');
    res.setHeader('Cache-Control', 'no-cache');
    res.send(buf);
  } catch (e) {
    res.sendStatus(500);
  }
});

// Expose server info to the game page
app.get('/api/info', (_req, res) => res.json({ ip: LOCAL_IP, port: PORT, base: BASE }));

// ─── State ────────────────────────────────────────────────────────────────────
let gameClient = null;
let players = { 1: new Map(), 2: new Map() }; // team -> Map(id -> player)
let serverState = 'lobby';   // lobby | countdown | fired
let activeTeam = null;
let countdownTimer = null;
let nextId = 1;

function send(ws, data) {
  if (ws?.readyState === 1) ws.send(JSON.stringify(data));
}

function pushStatus() {
  const base = { type: 'status', t1: players[1].size, t2: players[2].size, state: serverState, activeTeam };
  send(gameClient, base);
  for (const t of [1, 2]) {
    for (const p of players[t].values()) send(p.ws, { ...base, myTeam: t });
  }
}

function avgParams(team) {
  const list = [...players[team].values()];
  if (!list.length) return { angle: 45, speed: 65 };
  return {
    angle: Math.round(list.reduce((s, p) => s + p.angle, 0) / list.length),
    speed: Math.round(list.reduce((s, p) => s + p.speed, 0) / list.length)
  };
}

function tick(sec, team) {
  const base = { type: 'countdown', sec, activeTeam: team };
  send(gameClient, base);
  for (const t of [1, 2]) {
    for (const p of players[t].values()) send(p.ws, { ...base, myTeam: t });
  }
}

function startCountdown(team) {
  if (serverState !== 'lobby') return;
  serverState = 'countdown';
  activeTeam = team;
  let sec = 10;
  tick(sec, team);
  countdownTimer = setInterval(() => {
    sec--;
    tick(sec, team);
    if (sec <= 0) {
      clearInterval(countdownTimer);
      countdownTimer = null;
      const params = avgParams(team);
      const fireMsg = { type: 'fire', team, params };
      send(gameClient, fireMsg);
      for (const t of [1, 2]) {
        for (const p of players[t].values()) send(p.ws, { ...fireMsg, myTeam: t });
      }
      serverState = 'fired';
      // Reset to lobby after a few seconds so next throw can start
      setTimeout(() => {
        serverState = 'lobby';
        activeTeam = null;
        pushStatus();
      }, 4000);
    }
  }, 1000);
}

// ─── WebSocket connections ────────────────────────────────────────────────────
wss.on('connection', (ws, req) => {
  const qs = new URLSearchParams((req.url.split('?')[1]) || '');
  const type = qs.get('type');
  const team = parseInt(qs.get('team')) === 2 ? 2 : 1;

  if (type === 'game') {
    gameClient = ws;
    send(ws, { type: 'init', ip: LOCAL_IP, port: PORT });
    pushStatus();

    ws.on('message', raw => {
      try {
        const m = JSON.parse(raw);
        if (m.type === 'start') startCountdown(m.team || 1);
        if (m.type === 'reset') {
          if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
          serverState = 'lobby';
          activeTeam = null;
          pushStatus();
        }
      } catch {}
    });
    ws.on('close', () => { if (gameClient === ws) gameClient = null; });

  } else if (type === 'player') {
    const id = nextId++;
    const player = { ws, id, team, angle: 45, speed: 65 };
    players[team].set(id, player);

    // Tell the player they joined, and current state
    send(ws, { type: 'joined', id, team, state: serverState, activeTeam });
    pushStatus();

    ws.on('message', raw => {
      try {
        const m = JSON.parse(raw);
        if (m.type === 'params') {
          player.angle = Math.max(0, Math.min(90, +m.angle || 45));
          player.speed = Math.max(10, Math.min(150, +m.speed || 65));
        }
      } catch {}
    });
    ws.on('close', () => {
      players[team].delete(id);
      pushStatus();
    });
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n  🐒  Monkey Banana — Multiplayer Server`);
  console.log(`  ─────────────────────────────────────────`);
  console.log(`  Game screen  →  http://localhost:${PORT}/monkey-banana.html`);
  console.log(`  Network      →  http://${LOCAL_IP}:${PORT}/monkey-banana.html`);
  console.log(`\n  Players scan the QR codes shown on the game screen.`);
  console.log(`  The game works in local mode without running this server.\n`);
});
