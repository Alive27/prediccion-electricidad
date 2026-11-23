"use strict";

// Scene inputs sent by components/city_scene.py in the ?config= query parameter:
// demandScale and forecastScale (0-1 light intensity) and the sidebar key of each page.
const configQuery = new URLSearchParams(window.location.search).get("config");
window.ENERGY_CITY_CONFIG = configQuery
  ? JSON.parse(configQuery)
  : { demandScale: 0, forecastScale: [], navigation: {} };

function showSceneError(message) {
  const box = document.getElementById("error");
  box.textContent = `Error de carga 3D: ${message}`;
  box.style.display = "block";
}

window.addEventListener("error", (event) => {
  if (event.target instanceof HTMLScriptElement && event.target.type === "module") {
    showSceneError(event.message || "No se pudo iniciar el módulo WebGL.");
  }
}, true);
window.addEventListener("unhandledrejection", (event) => {
  showSceneError(event.reason?.message || String(event.reason || "Error desconocido"));
});
