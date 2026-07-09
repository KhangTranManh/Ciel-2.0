import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// Voice output is intentionally NOT enabled here. To turn it on later:
//   import { enableSpeaker, browserSpeak } from "./io/output/speaker";
//   enableSpeaker(browserSpeak);   // or a Tauri/native TTS function
// Nothing else needs to change — it subscribes to the same bus the UI already uses.

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
