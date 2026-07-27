import React from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";
import Widget from "./Widget";
import "./styles.css";

// Two entry points share ONE build: the full dashboard (App) and the floating
// desktop widget (Widget). Window identity = Tauri window LABEL ("main" | "widget").
// Outside Tauri (`npm run dev` in a browser) we always load App.
//
// One-chat-surface policy: tauri.conf.json keeps the widget window `visible: false`
// by default so desktop does not open two WS clients / two chat histories. Widget
// code remains for an opt-in floating shell later.
let isWidget = false;
try {
  isWidget = getCurrentWindow().label === "widget";
} catch {
  isWidget = false;
}
if (isWidget) {
  // The Tauri "widget" window is transparent:true so the round bubble shows the
  // desktop behind it — but the base styles.css paints a solid dark body
  // background for the dashboard. This class scopes the transparent override to
  // ONLY the widget window without touching the dashboard's styling at all.
  document.body.classList.add("widget-mode");
}

// Voice output is opt-in via the 🔊 toggle in App / Widget (backendSpeak → POST /tts).
// Plain browser (`npm run dev`, no Tauri) always loads App — no widget shell.

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isWidget ? <Widget /> : <App />}
  </React.StrictMode>
);
