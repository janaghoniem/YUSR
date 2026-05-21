/* eslint-env node */
// main.js — Electron entry point

/* eslint-env node */
// Add Menu and screen here
import { app, BrowserWindow, ipcMain, Menu, screen, shell } from "electron";
import { spawn } from "node:child_process";
import readline from "node:readline";
import path from "path";
import { fileURLToPath } from "url";
import process from "node:process";

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

app.setName("AURA");
let mainWindow  = null;
let auraProcess = null;
let savedBounds = null; // Add this line
let currentAuraConfig = { lang: "en", once: false };
const BACKEND_BASE_URL = process.env.AURA_BACKEND_URL || "http://localhost:8000";

function normalizeLang(lang) {
  return lang === "ar" ? "ar" : "en";
}

function sendAuraPayload(channel, payload) {
  if (!mainWindow?.webContents) return;
  mainWindow.webContents.send(channel, payload);
}

/**
 * PATH RESOLUTION
 * Dynamic resolution for development (.venv) vs Production (bundled exe)
 */
function getAuraSpawnConfig(options = {}) {
  const isPackaged = app.isPackaged;
  const lang = normalizeLang(options.lang || currentAuraConfig.lang || "en");
  
  if (isPackaged) {
    return {
      command: path.join(process.resourcesPath, "aura_engine.exe"),
      args: ["--lang", lang].concat(options.once ? ["--once", "--timeout-ms", String(options.timeoutMs || 10000)] : []),
      cwd: process.resourcesPath
    };
  } else {
    // Navigate from vite-project/src/main up to AURA root, then to backend/.venv
    const pythonPath = path.join(
      __dirname, 
      '..', '..', // Up to AURA/
      'backend', 
      '.venv', 
      'Scripts', 
      'python.exe'
    );
    
    // The script is in your vite-project root (adjust if it's elsewhere)
    const scriptPath = path.join(__dirname, 'aura_engine.py');
    
    return {
      command: pythonPath,
      args: ["-u", scriptPath, "--lang", lang].concat(options.once ? ["--once", "--timeout-ms", String(options.timeoutMs || 10000)] : []), // -u for unbuffered I/O (critical for real-time)
      cwd: path.join(__dirname, '..', '..')
    };
  }
}

/**
 * SIDE CAR PROCESS MANAGEMENT
 */
function startAuraProcess(options = {}) {
  const desiredLang = normalizeLang(options.lang);

  if (auraProcess) {
    if (currentAuraConfig.lang === desiredLang) {
      return { ok: true, running: true, lang: desiredLang };
    }
    stopAuraProcess();
  }

  currentAuraConfig = {
    lang: desiredLang,
    once: Boolean(options.once),
  };

  const { command, args, cwd } = getAuraSpawnConfig(options);
  console.log(`[AURA] Spawning: ${command} ${args.join(" ")} in ${cwd}`);
  sendAuraPayload("aura-status", { state: "starting", lang: desiredLang });
  
  try {
    auraProcess = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    const stdoutLines = readline.createInterface({ input: auraProcess.stdout });
    
    stdoutLines.on("line", (line) => {
      if (!line.trim()) return;
      sendAuraPayload("aura-log", { stream: "stdout", text: line });
      try {
        const payload = JSON.parse(line);
        // This routes to your existing UI logic
        routeAuraMessage(payload); 
      } catch (error) {
        console.warn("[AURA] Raw Python Log:", line);
        sendAuraPayload("aura-status", { state: "log", lang: desiredLang, message: line });
      }
    });

    auraProcess.stderr?.on("data", (data) => {
      const text = data.toString().trim();
      if (!text) return;
      console.warn(`[PYTHON-ERROR]: ${text}`);
      sendAuraPayload("aura-log", { stream: "stderr", text });
    });

    auraProcess.on("exit", (code, signal) => {
      console.log(`[AURA] Sidecar exited: code=${code} signal=${signal || "none"}`);
      sendAuraPayload("aura-status", {
        state: code === 0 ? "stopped" : "error",
        lang: currentAuraConfig.lang,
        code,
        signal,
      });
      auraProcess = null;
    });

    sendAuraPayload("aura-status", { state: "started", lang: desiredLang, pid: auraProcess.pid });
    return { ok: true, lang: desiredLang, pid: auraProcess.pid };
  } catch (error) {
    console.error("[AURA] Failed to start sidecar:", error);
    return { ok: false, error: error?.message || String(error) };
  }
}

function routeAuraMessage(message) {
  if (!mainWindow) return;

  // Standardized routing for your Egyptian Arabic workflow
  if (message.type === "partial") {
    mainWindow.webContents.send("aura-partial-text", message.text);
  } else if (message.type === "final") {
    mainWindow.webContents.send("aura-final-command", message);
  } else if (message.type === "wake_word") {
    mainWindow.webContents.send("aura-wake-word", message);
    mainWindow.webContents.send("aura-status", {
      state: "armed",
      lang: message.lang || currentAuraConfig.lang,
      text: message.text,
    });
  } else if (message.type === "status") {
    mainWindow.webContents.send("aura-status", message);
  } else if (message.type === "error") {
    mainWindow.webContents.send("aura-status", {
      state: "error",
      lang: currentAuraConfig.lang,
      detail: message.message || "Aura sidecar error",
    });
  }
}

// main.js
function stopAuraProcess() {
  if (auraProcess) {
    console.log(`[AURA] Terminating sidecar PID: ${auraProcess.pid}`);
    if (process.platform === "win32") {
      // Force kill the whole process tree (/T)
      spawn("taskkill", ["/pid", auraProcess.pid, "/f", "/t"]);
    } else {
      auraProcess.kill('SIGKILL');
    }
    auraProcess = null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// WINDOW CREATION
// ─────────────────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width:     1100,
    height:    800,
    minWidth:  480,
    minHeight: 400,
    frame:     false,
    transparent: false,
    alwaysOnTop: true,
    show:      false,
    icon: path.join(__dirname, "public/aura_icon_colored.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      // nodeIntegration MUST stay false for security — preload handles IPC
      nodeIntegration:        false,
      contextIsolation:       true,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "loading.html"));

  mainWindow.once("ready-to-show", () => mainWindow.show());
  // mainWindow.webContents.openDevTools();

  waitForDevServer(mainWindow);
}

function waitForDevServer(win) {
  const loadApp = async () => {
    try {
      await win.loadURL("http://localhost:5173");
    } catch {
      setTimeout(loadApp, 300);
    }
  };
  loadApp();
}

// ─────────────────────────────────────────────────────────────────────────────
// IPC — Window controls
// ─────────────────────────────────────────────────────────────────────────────

ipcMain.handle("window:close",    () => mainWindow?.close());
ipcMain.handle("window:minimize", () => mainWindow?.minimize());
ipcMain.handle("window:maximize", () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});

ipcMain.handle("shell:openExternal", async (_event, url) => {
  if (!url || typeof url !== "string") {
    return { ok: false, error: "Missing URL" };
  }
  try {
    await shell.openExternal(url, { activate: true });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error?.message || String(error) };
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// IPC — Widget mode (compact execution overlay)
// ─────────────────────────────────────────────────────────────────────────────

ipcMain.handle("widget:enter", () => {
  if (!mainWindow) return;
  savedBounds = mainWindow.getBounds();

  const cursorPoint  = screen.getCursorScreenPoint();
  const display      = screen.getDisplayNearestPoint(cursorPoint);
  const { width: sw, height: sh, x: sx, y: sy } = display.workArea;
  const wW = 480, wH = 72;

  mainWindow.setAlwaysOnTop(true, "floating");
  mainWindow.setSkipTaskbar(true);
  mainWindow.setResizable(false);
  mainWindow.setMinimumSize(wW, wH);
  mainWindow.setBounds({ x: sx + sw - wW - 24, y: sy + sh - wH - 24, width: wW, height: wH }, true);
});

ipcMain.handle("widget:exit", () => {
  if (!mainWindow) return;
  mainWindow.setAlwaysOnTop(true);
  mainWindow.setSkipTaskbar(false);
  mainWindow.setResizable(true);
  mainWindow.setMinimumSize(480, 400);
  if (savedBounds) {
    mainWindow.setBounds(savedBounds, true);
    savedBounds = null;
  } else {
    mainWindow.setBounds({ width: 900, height: 700 }, true);
    mainWindow.center();
  }
});

// main.js
// main.js - Update your aura:init handler
ipcMain.handle("aura:init", (_event, options = {}) => {
  // Keep continuous sidecar disabled by default; renderer can still request one-shot fallback.
  return { ok: false, disabled: true, reason: "vosk-disabled" };
});

ipcMain.handle("stt:transcribe", async (_event, payload = {}) => {
  const timeoutMs = Number(payload?.timeoutMs || 20000);
  // Build request body without the electron-only timeoutMs field
  const { timeoutMs: _dropped, ...requestBody } = payload;
  console.info("[STT][IPC] Forwarding transcription request to backend", {
    bytes: String(requestBody?.audio_data || "").length,
    session_id: requestBody?.session_id,
  });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${BACKEND_BASE_URL}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
      signal: controller.signal,
    });

    const raw = await response.text();
    let data = {};
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      data = { detail: raw };
    }

    return {
      ok: response.ok,
      status: response.status,
      data,
    };
  } catch (error) {
    console.warn("[STT][IPC] Proxy request failed", error?.message || error);
    return {
      ok: false,
      status: 0,
      error: error?.message || String(error),
    };
  } finally {
    clearTimeout(timer);
  }
});


// ─────────────────────────────────────────────────────────────────────────────
// APP LIFECYCLE
// ─────────────────────────────────────────────────────────────────────────────

// Update the bottom of main.js
app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();
  // Vosk sidecar startup disabled: Google/Web Speech only.
});

app.on("window-all-closed", () => {
  stopAuraProcess();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopAuraProcess();
});