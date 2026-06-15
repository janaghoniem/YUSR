/* eslint-env node */
// main.js — Electron entry point

import { app, BrowserWindow, ipcMain, Menu, screen, shell } from "electron";
import { spawn } from "node:child_process";
import readline from "node:readline";
import path from "path";
import { fileURLToPath } from "url";
import process from "node:process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

app.setName("AURA");
let mainWindow = null;
let auraProcess = null;
let savedBounds = null;
let currentAuraConfig = { lang: "en", once: false };
const BACKEND_BASE_URL = process.env.AURA_BACKEND_URL || "http://localhost:8000";

function normalizeLang(lang) {
  return lang === "ar" ? "ar" : "en";
}

function sendAuraPayload(channel, payload) {
  if (!mainWindow?.webContents) return;
  mainWindow.webContents.send(channel, payload);
}

// ─────────────────────────────────────────────────────────────────────────────
// PATH RESOLUTION
// Dynamic resolution for development (.venv) vs Production (bundled exe)
// ─────────────────────────────────────────────────────────────────────────────

function getAuraSpawnConfig(options = {}) {
  const isPackaged = app.isPackaged;
  const lang = normalizeLang(options.lang || currentAuraConfig.lang || "en");

  if (isPackaged) {
    return {
      command: path.join(process.resourcesPath, "aura_engine.exe"),
      args: ["--lang", lang].concat(
        options.once
          ? ["--once", "--timeout-ms", String(options.timeoutMs || 10000)]
          : []
      ),
      cwd: process.resourcesPath,
    };
  } else {
    // Development: point to Python virtual environment
    const pythonPath = path.join(
      __dirname,
      "..", "..", // Up to AURA/
      "backend",
      ".venv",
      "Scripts",
      "python.exe"
    );
    const scriptPath = path.join(__dirname, "aura_engine.py");
    return {
      command: pythonPath,
      args: ["-u", scriptPath, "--lang", lang].concat(
        options.once
          ? ["--once", "--timeout-ms", String(options.timeoutMs || 10000)]
          : []
      ),
      cwd: path.join(__dirname, "..", ".."),
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SIDE CAR PROCESS MANAGEMENT
// ─────────────────────────────────────────────────────────────────────────────

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
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    const stdoutLines = readline.createInterface({ input: auraProcess.stdout });

    stdoutLines.on("line", (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;

      // Handle plain "wake" command (non-JSON) from sidecar
      if (trimmed === "wake") {
        console.log("[AURA] Plain wake command received from sidecar");
        handleAppWake();
        return;
      }

      // Otherwise treat as JSON
      sendAuraPayload("aura-log", { stream: "stdout", text: line });
      try {
        const payload = JSON.parse(trimmed);
        routeAuraMessage(payload);
      } catch (error) {
        console.warn("[AURA] Raw Python Log:", line);
        sendAuraPayload("aura-status", {
          state: "log",
          lang: desiredLang,
          message: line,
        });
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

    sendAuraPayload("aura-status", {
      state: "started",
      lang: desiredLang,
      pid: auraProcess.pid,
    });
    return { ok: true, lang: desiredLang, pid: auraProcess.pid };
  } catch (error) {
    console.error("[AURA] Failed to start sidecar:", error);
    return { ok: false, error: error?.message || String(error) };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// WAKE ROUTING
// Centralised so both the plain "wake" string and JSON wake_word messages
// go through the same logic.
// ─────────────────────────────────────────────────────────────────────────────

function handleAppWake() {
  if (!mainWindow) return;

  const isVisible =
    mainWindow.isVisible() && !mainWindow.isMinimized();

  if (!isVisible) {
    // App hidden/minimised → restore first, then tell the renderer to start
    // listening.  We send app-wake with action "restore" and the renderer
    // will call startRecording() once it becomes visible.
    mainWindow.show();
    mainWindow.restore();
    mainWindow.focus();
    mainWindow.webContents.send("app-wake", { action: "restore" });
    // Also fire aura-wake-word so the renderer immediately starts recording
    // even before the window finishes animating.
    mainWindow.webContents.send("aura-wake-word", {
      type: "wake_word",
      text: "hey aura",
      lang: currentAuraConfig.lang,
      action: "trigger",
    });
  } else {
    // Already visible → start listening immediately.
    mainWindow.webContents.send("app-wake", { action: "start-listening" });
    mainWindow.webContents.send("aura-wake-word", {
      type: "wake_word",
      text: "hey aura",
      lang: currentAuraConfig.lang,
      action: "trigger",
    });
  }
}

function routeAuraMessage(message) {
  if (!mainWindow) return;

  if (message.type === "partial") {
    mainWindow.webContents.send("aura-partial-text", message.text);
  } else if (message.type === "final") {
    mainWindow.webContents.send("aura-final-command", message);
  } else if (message.type === "wake_word") {
    // Forward the raw event to the renderer (for pulse animation etc.)
    mainWindow.webContents.send("aura-wake-word", message);
    mainWindow.webContents.send("aura-status", {
      state: "armed",
      lang: message.lang || currentAuraConfig.lang,
      text: message.text,
    });
    console.log("[MAIN] wake_word received, sending to renderer");
    // Also run the unified wake handler so recording starts
    handleAppWake();
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

function stopAuraProcess() {
  if (auraProcess) {
    console.log(`[AURA] Terminating sidecar PID: ${auraProcess.pid}`);
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", auraProcess.pid, "/f", "/t"]);
    } else {
      auraProcess.kill("SIGKILL");
    }
    auraProcess = null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// WINDOW CREATION
// ─────────────────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 800,
    minWidth: 480,
    minHeight: 400,
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    show: false,
    icon: path.join(__dirname, "public/aura_icon_colored.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "loading.html"));
  mainWindow.once("ready-to-show", () => mainWindow.show());

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

ipcMain.handle("window:close", () => mainWindow?.close());
ipcMain.handle("window:minimize", () => mainWindow?.minimize());
ipcMain.handle("window:maximize", () => {
  if (!mainWindow) return;
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
});

ipcMain.handle("shell:openExternal", async (_event, url) => {
  if (!url || typeof url !== "string") return { ok: false, error: "Missing URL" };
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

  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const { width: sw, height: sh, x: sx, y: sy } = display.workArea;
  const wW = 480, wH = 72;

  mainWindow.setAlwaysOnTop(true, "floating");
  mainWindow.setSkipTaskbar(true);
  mainWindow.setResizable(false);
  mainWindow.setMinimumSize(wW, wH);
  mainWindow.setBounds(
    { x: sx + sw - wW - 24, y: sy + sh - wH - 24, width: wW, height: wH },
    true
  );
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

ipcMain.handle("open-external", async (_event, url) => {
  try {
    if (!url) return { ok: false, error: "No URL provided" };
    await shell.openExternal(url);
    return { ok: true };
  } catch (err) {
    console.error("[open-external] failed to open", url, err);
    return { ok: false, error: String(err) };
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// IPC — Aura sidecar control
// ─────────────────────────────────────────────────────────────────────────────

ipcMain.handle("aura:init", (_event, options = {}) => {
  return startAuraProcess(options);
});

ipcMain.handle("stt:transcribe", async (_event, payload = {}) => {
  const timeoutMs = Number(payload?.timeoutMs || 20000);
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

    return { ok: response.ok, status: response.status, data };
  } catch (error) {
    console.warn("[STT][IPC] Proxy request failed", error?.message || error);
    return { ok: false, status: 0, error: error?.message || String(error) };
  } finally {
    clearTimeout(timer);
  }
});

ipcMain.handle("aura:disarm", async () => {
  if (auraProcess?.stdin) {
    auraProcess.stdin.write("disarm\n");
  }
  return { ok: true };
});

// ─────────────────────────────────────────────────────────────────────────────
// APP LIFECYCLE
// ─────────────────────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();
  startAuraProcess({ lang: "en" });
});

app.on("window-all-closed", () => {
  stopAuraProcess();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopAuraProcess();
});