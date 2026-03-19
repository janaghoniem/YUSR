import { app, BrowserWindow, Menu, ipcMain, screen } from "electron";
import path from "path";
import { fileURLToPath } from "url";

// ESM equivalents
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

app.setName("AURA");

let mainWindow = null;
let savedBounds = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    minWidth: 480,
    minHeight: 400,
    frame: false,            // No native title bar
    transparent: false,
    alwaysOnTop: true,       // Pinned above other windows like a widget
    show: false,
    icon: path.join(__dirname, "public/aura3.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
    },
  });

  // 1️⃣ Load local loading screen immediately
  mainWindow.loadFile(path.join(__dirname, "loading.html"));

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  // 2️⃣ Attempt to load localhost app in background
  waitForDevServer(mainWindow);
}

function waitForDevServer(win) {
  const loadApp = async () => {
    try {
      await win.loadURL("http://localhost:5173");
    } catch {
      // Retry until dev server is available
      setTimeout(loadApp, 300);
    }
  };

  loadApp();
}

/* ========== Window & Widget IPC Handlers ========== */

ipcMain.handle("window:close", () => {
  if (mainWindow) mainWindow.close();
});

ipcMain.handle("window:minimize", () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.handle("window:maximize", () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.handle("widget:enter", () => {
  if (!mainWindow) return;
  savedBounds = mainWindow.getBounds();

  const cursorPoint = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursorPoint);
  const { width: screenW, height: screenH, x: screenX, y: screenY } = display.workArea;
  const widgetW = 480;
  const widgetH = 72;

  mainWindow.setAlwaysOnTop(true, "floating");
  mainWindow.setSkipTaskbar(true);
  mainWindow.setResizable(false);
  mainWindow.setMinimumSize(widgetW, widgetH);
  mainWindow.setBounds({
    x: screenX + screenW - widgetW - 24,
    y: screenY + screenH - widgetH - 24,
    width: widgetW,
    height: widgetH,
  }, true);
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

/* ================================================ */

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
