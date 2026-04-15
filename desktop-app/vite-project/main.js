// main.js — Electron entry point
// Req 12: adds setPermissionRequestHandler to auto-grant microphone / STT
// so local speech recognition never shows a browser permission dialog.

import { app, BrowserWindow, Menu, ipcMain, screen, session } from "electron";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

app.setName("AURA");

let mainWindow  = null;
let savedBounds = null;

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

// ─────────────────────────────────────────────────────────────────────────────
// APP LIFECYCLE
// ─────────────────────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  // Req 12: register permission handlers BEFORE creating the window
  // so the first load already has them in place.
  setupPermissions();

  Menu.setApplicationMenu(null);
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});