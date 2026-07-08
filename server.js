const express = require("express");
const http = require("http");
const { WebSocketServer } = require("ws");
const crypto = require("crypto");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");
const path = require("path");

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server, maxPayload: 65536 });

app.use(
  helmet({ contentSecurityPolicy: false, crossOriginEmbedderPolicy: false }),
);
app.use(express.json({ limit: "16kb" }));
app.use(
  "/api/",
  rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 200,
    standardHeaders: true,
    legacyHeaders: false,
  }),
);
app.use(express.static(path.join(__dirname, "public")));

const SESSION_TTL = 12 * 60 * 60 * 1000;
const PUBLIC_MSG_MAX = 500;
const PUBLIC_DISPLAY_MS = 20000;
const HISTORY_MAX = 200;

const AUTH = {
  moderator: {
    username: process.env.MODERATOR_USERNAME || "moderator",
    password: process.env.MODERATOR_PASSWORD || "moderator123",
  },
};

const sessions = new Map();
let boardWs = null;
const moderatorWsSet = new Set();
const submissionQueue = [];
const displayQueue = [];
const messageHistory = [];

function historySnapshot(limit = 50) {
  return messageHistory.slice(0, limit).map((h) => ({ ...h }));
}

function addHistory(entry) {
  messageHistory.unshift(entry);
  if (messageHistory.length > HISTORY_MAX) messageHistory.length = HISTORY_MAX;
}

function updateHistoryByMessageId(messageId, patch) {
  const idx = messageHistory.findIndex((h) => h.messageId === messageId);
  if (idx === -1) return;
  messageHistory[idx] = { ...messageHistory[idx], ...patch };
}

function recordQueuedMessage(entry, source) {
  addHistory({
    id: genMessageId(),
    messageId: entry.id,
    text: entry.text,
    source,
    action: "queued",
    autocenter: !!entry.autocenter,
    durationMs: entry.indefinite ? 0 : (entry.durationMs ?? PUBLIC_DISPLAY_MS),
    indefinite: !!entry.indefinite,
    at: Date.now(),
    endedAt: null,
    endAction: null,
  });
}

function genToken() {
  return crypto.randomBytes(32).toString("hex");
}

function genMessageId() {
  return crypto.randomBytes(8).toString("hex");
}

function getSession(token) {
  if (typeof token !== "string" || !token) return null;
  const s = sessions.get(token);
  if (!s || s.expiresAt < Date.now()) {
    sessions.delete(token);
    return null;
  }
  return s;
}

function authMiddleware(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  const session = getSession(token);
  if (!session || session.role !== "moderator") {
    res.status(401).json({ ok: false, error: "Unauthorized" });
    return;
  }
  req.session = session;
  next();
}

function safeSend(ws, obj) {
  if (ws && ws.readyState === 1) {
    try {
      ws.send(JSON.stringify(obj));
    } catch (_) {}
  }
}

function normalizeMessage(text) {
  if (typeof text !== "string") return "";
  return text.replace(/\r\n/g, "\n").trim().slice(0, PUBLIC_MSG_MAX);
}

function queueSnapshot() {
  const nowPlaying = displayQueue[0]
    ? {
        id: displayQueue[0].id,
        text: displayQueue[0].text,
        indefinite: !!displayQueue[0].indefinite,
        durationMs: displayQueue[0].durationMs ?? PUBLIC_DISPLAY_MS,
      }
    : null;
  return {
    pending: submissionQueue.map((m) => ({
      id: m.id,
      text: m.text,
      submittedAt: m.submittedAt,
    })),
    display: displayQueue.length,
    nowPlaying,
    boardOnline: !!(boardWs && boardWs.readyState === 1),
    moderatorOnline: moderatorWsSet.size > 0,
    moderatorCount: moderatorWsSet.size,
    history: historySnapshot(),
  };
}

function notifyModerator() {
  const payload = { type: "queue_update", ...queueSnapshot() };
  for (const ws of moderatorWsSet) {
    safeSend(ws, payload);
  }
}

function boardPlayEntry(entry) {
  if (!boardWs || boardWs.readyState !== 1 || !entry) return;
  safeSend(boardWs, {
    type: "play_public_message",
    id: entry.id,
    text: entry.text,
    durationMs: entry.indefinite ? 0 : (entry.durationMs ?? PUBLIC_DISPLAY_MS),
    indefinite: !!entry.indefinite,
    autocenter: !!entry.autocenter,
  });
}

function playNextOnBoard() {
  if (!displayQueue.length) return;
  boardPlayEntry(displayQueue[0]);
}

function clearBoardDisplay(id) {
  safeSend(boardWs, { type: "clear_public_message", id: id || null });
}

function parseModeratorDuration(body) {
  if (body?.indefinite) {
    return { indefinite: true, durationMs: 0 };
  }
  const sec = Number.parseInt(body?.durationSeconds, 10);
  const durationMs =
    Number.isFinite(sec) && sec > 0
      ? Math.min(sec * 1000, 24 * 60 * 60 * 1000)
      : PUBLIC_DISPLAY_MS;
  return { indefinite: false, durationMs };
}

function enqueueDisplay(entry) {
  displayQueue.push(entry);
  if (displayQueue.length === 1) playNextOnBoard();
  notifyModerator();
}

function onPublicMessageDone(id) {
  if (displayQueue.length && displayQueue[0].id === id) {
    displayQueue.shift();
  } else {
    const idx = displayQueue.findIndex((m) => m.id === id);
    if (idx !== -1) displayQueue.splice(idx, 1);
  }
  updateHistoryByMessageId(id, {
    endAction: "completed",
    endedAt: Date.now(),
  });
  playNextOnBoard();
  notifyModerator();
}

app.get("/api/health", (_, res) => {
  res.json({
    ok: true,
    boardOnline: !!(boardWs && boardWs.readyState === 1),
    pending: submissionQueue.length,
    display: displayQueue.length,
  });
});

app.post("/api/auth/moderator", (req, res) => {
  const { username, password } = req.body || {};
  if (
    username === AUTH.moderator.username &&
    password === AUTH.moderator.password
  ) {
    const token = genToken();
    sessions.set(token, {
      role: "moderator",
      expiresAt: Date.now() + SESSION_TTL,
    });
    res.json({ ok: true, token });
    return;
  }
  res.status(401).json({ ok: false, error: "Invalid credentials" });
});

app.post(
  "/api/submit-message",
  rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 20,
    standardHeaders: true,
    legacyHeaders: false,
  }),
  (req, res) => {
    const text = normalizeMessage(req.body?.text);
    if (!text) {
      res.status(400).json({ ok: false, error: "Message is required" });
      return;
    }
    if (!boardWs || boardWs.readyState !== 1) {
      res.status(503).json({ ok: false, error: "Board is offline" });
      return;
    }
    const entry = {
      id: genMessageId(),
      text,
      autocenter: !!req.body?.autocenter,
      submittedAt: Date.now(),
    };
    submissionQueue.push(entry);
    notifyModerator();
    res.json({ ok: true, id: entry.id, message: "Submitted for approval" });
  },
);

app.get("/api/moderator/queue", authMiddleware, (_, res) => {
  res.json({ ok: true, ...queueSnapshot() });
});

app.get("/api/moderator/history", authMiddleware, (req, res) => {
  const limit = Math.min(
    Math.max(Number.parseInt(req.query.limit, 10) || 50, 1),
    HISTORY_MAX,
  );
  res.json({ ok: true, history: historySnapshot(limit) });
});

app.post("/api/moderator/approve/:id", authMiddleware, (req, res) => {
  const idx = submissionQueue.findIndex((m) => m.id === req.params.id);
  if (idx === -1) {
    res.status(404).json({ ok: false, error: "Message not found" });
    return;
  }
  const entry = submissionQueue.splice(idx, 1)[0];
  entry.approvedAt = Date.now();
  entry.durationMs = PUBLIC_DISPLAY_MS;
  entry.indefinite = false;
  recordQueuedMessage(entry, "public");
  enqueueDisplay(entry);
  res.json({ ok: true, message: entry });
});

app.post("/api/moderator/reject/:id", authMiddleware, (req, res) => {
  const idx = submissionQueue.findIndex((m) => m.id === req.params.id);
  if (idx === -1) {
    res.status(404).json({ ok: false, error: "Message not found" });
    return;
  }
  const entry = submissionQueue.splice(idx, 1)[0];
  addHistory({
    id: genMessageId(),
    messageId: entry.id,
    text: entry.text,
    source: "public",
    action: "rejected",
    autocenter: !!entry.autocenter,
    at: Date.now(),
    submittedAt: entry.submittedAt,
  });
  notifyModerator();
  res.json({ ok: true });
});

app.post("/api/moderator/post", authMiddleware, (req, res) => {
  const text = normalizeMessage(req.body?.text);
  if (!text) {
    res.status(400).json({ ok: false, error: "Message is required" });
    return;
  }
  if (!boardWs || boardWs.readyState !== 1) {
    res.status(503).json({ ok: false, error: "Board is offline" });
    return;
  }
  const timing = parseModeratorDuration(req.body);
  const entry = {
    id: genMessageId(),
    text,
    autocenter: !!req.body?.autocenter,
    postedAt: Date.now(),
    source: "moderator",
    ...timing,
  };
  recordQueuedMessage(entry, "moderator");
  displayQueue.unshift(entry);
  boardPlayEntry(entry);
  notifyModerator();
  res.json({ ok: true, message: entry });
});

app.post("/api/moderator/clear", authMiddleware, (req, res) => {
  if (!boardWs || boardWs.readyState !== 1) {
    res.status(503).json({ ok: false, error: "Board is offline" });
    return;
  }
  const removed = displayQueue.length ? displayQueue.shift() : null;
  if (removed) {
    updateHistoryByMessageId(removed.id, {
      endAction: "cleared",
      endedAt: Date.now(),
    });
  }
  clearBoardDisplay(removed?.id);
  playNextOnBoard();
  notifyModerator();
  res.json({ ok: true, cleared: !!removed, id: removed?.id || null });
});

wss.on("connection", (ws) => {
  ws.isAlive = true;
  ws.role = null;

  ws.on("pong", () => {
    ws.isAlive = true;
  });

  ws.on("message", (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch (_) {
      return;
    }
    if (!msg || typeof msg.type !== "string") return;

    switch (msg.type) {
      case "register_board": {
        if (boardWs && boardWs.readyState === 1) {
          try {
            boardWs.close();
          } catch (_) {}
        }
        boardWs = ws;
        ws.role = "board";
        safeSend(ws, { type: "registered" });
        if (displayQueue.length) boardPlayEntry(displayQueue[0]);
        console.log("Board connected");
        notifyModerator();
        break;
      }

      case "authenticate": {
        const session = getSession(msg.token);
        if (!session || session.role !== "moderator") {
          safeSend(ws, { type: "error", message: "Invalid or expired login" });
          return;
        }
        moderatorWsSet.add(ws);
        ws.role = "moderator";
        safeSend(ws, { type: "authenticated", ...queueSnapshot() });
        console.log(`Moderator connected (${moderatorWsSet.size} active)`);
        break;
      }

      case "public_message_done": {
        if (ws.role !== "board") return;
        onPublicMessageDone(msg.id);
        break;
      }

      case "get_queue": {
        if (ws.role !== "moderator") return;
        safeSend(ws, { type: "queue_update", ...queueSnapshot() });
        break;
      }
    }
  });

  ws.on("close", () => {
    if (ws === boardWs) {
      boardWs = null;
      console.log("Board disconnected");
      notifyModerator();
    }
    if (ws.role === "moderator") {
      moderatorWsSet.delete(ws);
      console.log(`Moderator disconnected (${moderatorWsSet.size} active)`);
      notifyModerator();
    }
  });

  ws.on("error", () => {});
});

const hb = setInterval(() => {
  wss.clients.forEach((ws) => {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false;
    ws.ping();
  });
}, 30000);
wss.on("close", () => clearInterval(hb));

const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || "0.0.0.0";
server.listen(PORT, HOST, () => {
  console.log(`\n  Split-flap board server on http://${HOST}:${PORT}\n`);
  console.log(`  Board:     /board.html`);
  console.log(`  Moderator: /moderator.html`);
  console.log(`  Submit:    /submit.html\n`);
});