/* eslint-env node */
// main.js — Electron entry point
// Req 12: adds setPermissionRequestHandler to auto-grant microphone / STT
// so local speech recognition never shows a browser permission dialog.

/* eslint-env node */
// Add Menu and screen here
import { app, BrowserWindow, ipcMain, session, Menu, screen } from "electron";
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

async function transcribeOnce(options = {}) {
  const { command, args, cwd } = getAuraSpawnConfig({
    once: true,
    lang: options?.lang === "ar" ? "ar" : "en",
    timeoutMs: Number(options?.timeoutMs || 10000),
  });

  return await new Promise((resolve) => {
    let finished = false;
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
      },
    });

    const cleanup = (result) => {
      if (finished) return;
      finished = true;
      try {
        child.kill();
      } catch {
        // best effort
      }
      resolve(result);
    };

    const timer = setTimeout(() => {
      cleanup({ ok: false, error: "timeout" });
    }, Number(options?.timeoutMs || 10000) + 2000);

    const stdoutLines = readline.createInterface({ input: child.stdout });
    stdoutLines.on("line", (line) => {
      if (!line.trim()) return;
      try {
        const payload = JSON.parse(line);
        if (payload?.type === "final" && payload?.text) {
          clearTimeout(timer);
          cleanup({ ok: true, text: payload.text, payload });
        } else if (payload?.type === "error") {
          clearTimeout(timer);
          cleanup({ ok: false, error: payload.message || "transcription failed", payload });
        }
      } catch {
        // ignore non-JSON lines
      }
    });

    child.stderr?.on("data", (chunk) => {
      const text = chunk.toString().trim();
      if (text) {
        console.warn("[AURA][stderr]", text);
      }
    });

    child.on("error", (error) => {
      clearTimeout(timer);
      cleanup({ ok: false, error: error?.message || String(error) });
    });

    child.on("exit", (code) => {
      clearTimeout(timer);
      if (!finished) {
        cleanup({ ok: false, error: `sidecar exited with code ${code}` });
      }
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// REQ 12 — Permission handler
// Must be registered BEFORE the BrowserWindow loads any URL.
// Chromium routes all getUserMedia / SpeechRecognition requests through
// session.setPermissionRequestHandler; without it the OS-level dialog appears.
// ─────────────────────────────────────────────────────────────────────────────

function setupPermissions() {
  const ALLOWED = new Set([
    "media",             // navigator.mediaDevices.getUserMedia (mic + camera)
    "microphone",        // explicit mic permission string
    "audioCapture",      // Chrome internal for audio capture
    "speech-recognition", // Web Speech API
    "speechRecognition",  // Chrome internal for Web Speech API
    "notifications",     // optional — allow if you want push notifications
  ]);

  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      if (ALLOWED.has(permission)) {
        console.log(`[Permissions] ✅ Auto-granting: ${permission}`);
        callback(true);
      } else {
        console.log(
          `[Permissions] ❌ Denying: ${permission}`,
          details?.requestingUrl || ""
        );
        callback(false);
      }
    }
  );

  // Also handle synchronous permission checks from getUserMedia internals
  session.defaultSession.setPermissionCheckHandler(
    (webContents, permission /*, requestingOrigin, details*/) => {
      const allow = ALLOWED.has(permission);
      console.log(
        `[PermissionCheck] ${allow ? "✅" : "❌"} ${permission}`
      );
      return allow;
    }
  );

  console.log("[Permissions] Permission handlers registered.");
}

// ─────────────────────────────────────────────────────────────────────────────
// WINDOW CREATION
// ─────────────────────────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width:     900,
    height:    700,
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
      // Allow getUserMedia without the browser prompting
      // (setPermissionRequestHandler above is the real gate)
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
  if (!mainWindow) return { ok: false, error: "main window unavailable" };

  // If already running, just return success instead of spawning a second one
  if (auraProcess) {
    console.log("[AURA] Sidecar already running, skipping re-init");
    return { ok: true };
  }

  const result = startAuraProcess(options);
  return result;
});


// ─────────────────────────────────────────────────────────────────────────────
// APP LIFECYCLE
// ─────────────────────────────────────────────────────────────────────────────

// Update the bottom of main.js
app.whenReady().then(() => {
  setupPermissions();
  Menu.setApplicationMenu(null);
  createWindow();

  // Only start it once here
  startAuraProcess({ lang: currentAuraConfig.lang, once: false });
});

app.on("window-all-closed", () => {
  stopAuraProcess();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopAuraProcess();
});