import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./cv-library.css";
import App from "./App.jsx";
import { LanguageProvider } from "./context/LanguageContext.jsx";
import "./styles.css";
import "./viewport-fit.css";

// After a deploy, a tab opened on the old release may ask for a chunk that no longer exists:
// reload once to pick up the new index.html instead of showing a blank interview room.
window.addEventListener("vite:preloadError", event => {
  try {
    if (sessionStorage.getItem("chunk-reload") === "1") return;
    sessionStorage.setItem("chunk-reload", "1");
  } catch { /* storage blocked: still try once */ }
  event.preventDefault();
  window.location.reload();
});
window.addEventListener("load", () => {
  window.setTimeout(() => { try { sessionStorage.removeItem("chunk-reload"); } catch { /* ignore */ } }, 10000);
});

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <LanguageProvider>
        <App />
      </LanguageProvider>
    </BrowserRouter>
  </React.StrictMode>
);
