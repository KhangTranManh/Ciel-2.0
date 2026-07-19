import React from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";
import Widget from "./Widget";
import "./styles.css";

// Two entry points share ONE build: the full dashboard (App) and the floating
// desktop widget (Widget, chat+voice only).
//
// Which window is which is decided by the Tauri window LABEL (from
// src-tauri/tauri.conf.json — "main" vs "widget"), NOT a "?widget=1" URL query
// param. The query-param approach was tried first and is suspected unreliable in
// `tauri dev`: composing a per-window `url` (with a query string) on top of
// `devUrl` is not guaranteed to behave the same as it does in a production build,
// and live testing showed the widget window rendering the FULL dashboard (orb,
// SkillGrid) instead of the small chat panel — consistent with that failure mode.
// `getCurrentWindow().label` is a plain synchronous field Tauri sets directly (not
// derived from the page URL at all — confirmed in @tauri-apps/api's own type
// definitions), so it can't be affected by however the URL ended up being loaded.
// Falls back to the full dashboard (App) if this throws outside a Tauri context
// (e.g. `npm run dev` in a plain browser tab), matching the previous default.
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

// Voice output is intentionally NOT enabled by default in the full dashboard. To
// turn it on there too: import { enableSpeaker, browserSpeak } from
// "./io/output/speaker"; enableSpeaker(browserSpeak); — subscribes to the same bus
// the UI already uses. The widget manages its own speaker toggle internally.

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isWidget ? <Widget /> : <App />}
  </React.StrictMode>
);
