"use strict";

const metricsQuery = new URLSearchParams(window.location.search).get("metrics");
window.ENERGY_CITY_CONFIG = metricsQuery
  ? JSON.parse(metricsQuery)
  : { demandScale: 0, forecastScale: [] };

const metricNumber = (value, digits = 0) => {
  const numeric = Number(value);
  return value === null || value === undefined || !Number.isFinite(numeric)
    ? null
    : new Intl.NumberFormat("es-ES", {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(numeric);
};
const metricValue = (value, unit, digits = 0) => value === null
  ? "—" : `${value} <small>${unit}</small>`;
const setMetricValue = (id, value) => {
  document.getElementById(id).innerHTML = value;
};

function renderMetrics(data) {
  const predictions = Array.isArray(data.forecast) ? data.forecast : [];
  setMetricValue("demand", metricValue(metricNumber(data.demand), "MW"));
  setMetricValue("next-hour", metricValue(metricNumber(predictions[0]), "MW"));
  setMetricValue("next-day", metricValue(metricNumber(predictions[23]), "MW"));

  const status = document.getElementById("status");
  status.textContent = `${data.apiState || "API no disponible"} · ${data.freshness || "Sin datos"}`;
  status.classList.toggle("warn", !data.demandFresh && Boolean(data.demandTime));
  status.classList.toggle("down", !String(data.apiState || "").includes("operativa"));

  // The API reports €/MWh; the card shows €/kWh, the unit of household bills.
  const perKwh = (value) => (value === null || value === undefined ? null : Number(value) / 1000);
  const observed = metricNumber(perKwh(data.price), 4);
  const estimate = metricNumber(perKwh(data.priceEstimate), 4);
  if (observed !== null && !data.priceStale) {
    document.getElementById("price-label").textContent = "PVPC OBSERVADO";
    setMetricValue("price", metricValue(observed, "€/kWh"));
    document.getElementById("price-detail").textContent = "Indicador 1001 · último dato publicado";
  } else if (estimate !== null) {
    document.getElementById("price-label").textContent = "PRECIO ESTIMADO · MODELO";
    setMetricValue("price", metricValue(estimate, "€/kWh"));
    document.getElementById("price-detail").textContent = "Siguiente hora · no es precio observado";
  } else if (data.priceStale) {
    document.getElementById("price-label").textContent = "PVPC DESACTUALIZADO";
    document.getElementById("price-detail").textContent = "El dato antiguo no se muestra como actual";
  }
  const demandTime = data.demandTime
    ? `Demanda real: ${data.demandTime}` : "Demanda real: sin observación";
  const priceTime = observed !== null && !data.priceStale
    ? `PVPC: ${data.priceTime}`
    : estimate !== null ? `Modelo precio: ${data.priceEstimateTime}`
      : data.priceStale ? `PVPC observado: ${data.priceTime}` : "Precio: sin dato disponible";
  document.getElementById("timestamps").textContent = `${demandTime} · ${priceTime}`;
}

renderMetrics(window.ENERGY_CITY_CONFIG);
window.addEventListener("error", (event) => {
  if (event.target instanceof HTMLScriptElement && event.target.type === "module") {
    const message = event.message || "No se pudo iniciar el módulo WebGL.";
    document.getElementById("error").textContent = `Error de carga 3D: ${message}`;
    document.getElementById("error").style.display = "block";
  }
}, true);
window.addEventListener("unhandledrejection", (event) => {
  const message = event.reason?.message || String(event.reason || "Error desconocido");
  document.getElementById("error").textContent = `Error de carga 3D: ${message}`;
  document.getElementById("error").style.display = "block";
});
