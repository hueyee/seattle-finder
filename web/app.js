let map;
let markerLayer;
let isAnalyzing = false;
let currentResults = [];
let currentMarkers = [];
let selectedIndex = null;

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatPercent(value) {
  if (value === null || value === undefined) return "--";
  return `${(value * 100).toFixed(1)}%`;
}

function formatDelta(value) {
  if (value === null || value === undefined) return "--";
  const points = value * 100;
  const sign = points >= 0 ? "+" : "";
  return `${sign}${points.toFixed(1)} pts`;
}

function formatMinutes(value) {
  if (value === null || value === undefined) return "--";
  return `${value.toFixed(0)} min`;
}

function formatMoney(value) {
  if (value === null || value === undefined) return "--";
  return `$${value.toFixed(2)}`;
}

function hasValue(value) {
  return value !== null && value !== undefined;
}

function targetAgeRange() {
  const min = Number($("#target-age-min").value);
  const max = Number($("#target-age-max").value);
  return {
    min: Math.min(min, max),
    max: Math.max(min, max)
  };
}

function syncWeights() {
  const demographic = $("#demographic-weight");
  const commute = $("#commute-weight");
  const affordability = $("#affordability-weight");
  const total = Number(demographic.value) + Number(commute.value) + Number(affordability.value);

  $("#demographic-weight-label").textContent = `${demographic.value}%`;
  $("#commute-weight-label").textContent = `${commute.value}%`;
  $("#affordability-weight-label").textContent = `${affordability.value}%`;
  $("#weight-total").textContent = `${total}%`;
}

function addOfficeRow(office = {}) {
  const row = document.createElement("div");
  row.className = "office-row";
  row.innerHTML = `
    <label class="field">
      Name
      <input class="office-name" type="text" value="${escapeHtml(office.name ?? "")}" />
    </label>
    <label class="field">
      Address
      <input class="office-address" type="text" value="${escapeHtml(office.address ?? "")}" />
    </label>
    <button class="icon-button remove-office-button" type="button" title="Remove office" aria-label="Remove office">x</button>
  `;
  $("#office-rows").append(row);
}

function parseOffices() {
  const offices = [...document.querySelectorAll(".office-row")]
    .map((row) => {
      const name = row.querySelector(".office-name").value.trim();
      const address = row.querySelector(".office-address").value.trim();
      return {
        name: name || address,
        address
      };
    })
    .filter((office) => office.name || office.address);

  if (!offices.length) {
    throw new Error("Add at least one office before analyzing commute fit.");
  }

  const missingAddress = offices.find((office) => !office.address);
  if (missingAddress) {
    throw new Error(`Add an address for ${missingAddress.name}.`);
  }

  return offices;
}

function payloadFromForm() {
  const targetAge = targetAgeRange();
  return {
    home: $("#home").value.trim() || "Sammamish, WA",
    target_age_min: targetAge.min,
    target_age_max: targetAge.max,
    include_seattle_neighborhoods: $("#include-neighborhoods").checked,
    directions_limit: Number($("#directions-limit").value),
    weights: {
      demographics: Number($("#demographic-weight").value) / 100,
      commute: Number($("#commute-weight").value) / 100,
      affordability: Number($("#affordability-weight").value) / 100
    },
    rent: {
      enabled: true,
      bedrooms: 1,
      commute_days_per_month: Number($("#commute-days-per-month").value),
      apartment_list_csv: "data/apartment_list_rent_estimates.csv"
    },
    commute_schedule: {
      timezone: "America/Los_Angeles",
      morning_arrival_time: $("#morning-arrival-time").value || "09:00",
      evening_departure_time: $("#evening-departure-time").value || "17:00"
    },
    vehicle: {
      miles_per_gallon: Number($("#miles-per-gallon").value),
      fuel_price_per_gallon: Number($("#fuel-price-per-gallon").value),
      toll_passes: ["US_WA_GOOD_TO_GO"],
      emission_type: "GASOLINE"
    },
    office_locations: parseOffices()
  };
}

function estimatedRouteRequests(payload) {
  return payload.directions_limit * payload.office_locations.length * 3;
}

function setAnalyzingState(active) {
  isAnalyzing = active;
  const button = $("#analyze-button");
  if (!button) return;
  button.disabled = active;
  button.textContent = active ? "Analyzing..." : "Analyze";
}

function initMap() {
  if (map || !window.L) return;
  map = L.map("map", { scrollWheelZoom: true }).setView([47.61, -122.16], 10);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
  window.setTimeout(() => map.invalidateSize(), 0);
}

function markerColor(score) {
  if (score >= 0.75) return "#0f766e";
  if (score >= 0.5) return "#2563eb";
  if (score >= 0.25) return "#d97706";
  return "#9a3412";
}

function addMarker(result, index) {
  if (!result.location) return null;
  const color = markerColor(result.score);
  const marker = L.circleMarker([result.location.lat, result.location.lng], {
    radius: Math.max(7, 16 - (index + 1) * 0.25),
    color,
    fillColor: color,
    fillOpacity: 0.82,
    weight: 2
  });

  marker.bindPopup(`
    <strong>${index + 1}. ${escapeHtml(result.name)}</strong><br>
    Score: ${(result.score * 100).toFixed(0)}<br>
    Young-adult share: ${formatPercent(result.target_share)}<br>
    Avg commute: ${formatMinutes(result.average_commute_minutes)}<br>
    Total monthly cost: ${formatMoney(result.total_monthly_cost)}
    ${renderCommuteList(result, "popup")}
  `);
  marker.on("click", () => selectResult(index, { panToMarker: false }));
  marker.addTo(markerLayer);
  return marker;
}

function renderMap(results) {
  const located = results.filter((result) => result.location);
  if (!window.L) {
    $("#map").innerHTML = '<div class="map-empty">Map library could not load.</div>';
    return;
  }
  initMap();
  markerLayer.clearLayers();
  currentMarkers = [];

  results.slice(0, 40).forEach((result, index) => {
    const marker = addMarker(result, index);
    currentMarkers[index] = marker;
  });

  const markers = currentMarkers.filter(Boolean);
  if (markers.length > 0) {
    const group = L.featureGroup(markers);
    map.fitBounds(group.getBounds().pad(0.18));
  } else if (!located.length) {
    map.setView([47.61, -122.16], 10);
  }
  window.setTimeout(() => map.invalidateSize(), 0);
}

function resultCard(result, index) {
  const deltaClass = result.home_delta >= 0 ? "positive" : "negative";
  const score = Math.round(result.score * 100);
  return `
    <button class="result-card" type="button" data-index="${index}" aria-label="Open details for ${escapeHtml(result.name)}">
      <div class="card-topline">
        <div>
          <div class="place-name">${escapeHtml(result.name)}</div>
          <div class="source">${escapeHtml(result.source)}</div>
        </div>
        <span class="rank-badge">#${index + 1}</span>
      </div>
      <div class="quality-bar" aria-hidden="true"><span style="--bar-value: ${score}%"></span></div>
      <div class="mini-metrics">
        <div class="mini-metric">
          <span>Score</span>
          <strong><span class="score-pill">${score}</span></strong>
        </div>
        <div class="mini-metric">
          <span>Target share</span>
          <strong>${formatPercent(result.target_share)}</strong>
        </div>
        <div class="mini-metric">
          <span>Vs. home</span>
          <strong class="${deltaClass}">${formatDelta(result.home_delta)}</strong>
        </div>
        <div class="mini-metric">
          <span>Avg commute</span>
          <strong>${formatMinutes(result.average_commute_minutes)}</strong>
        </div>
        <div class="mini-metric">
          <span>1BR rent</span>
          <strong>${formatMoney(result.monthly_rent_1br)}</strong>
        </div>
        <div class="mini-metric">
          <span>Total/mo</span>
          <strong>${formatMoney(result.total_monthly_cost)}</strong>
        </div>
      </div>
    </button>
  `;
}

function renderResults(results) {
  currentResults = results;
  selectedIndex = null;
  const body = $("#results-body");
  $("#detail-panel").hidden = true;
  $("#drawer-count").textContent = `${results.length} shown`;

  if (!results.length) {
    body.innerHTML = '<p class="empty-state">No results returned.</p>';
    return;
  }

  body.innerHTML = results.map(resultCard).join("");
}

function renderCommuteList(result, context) {
  if (!result.commutes?.length) return "";
  const className = context === "popup" ? "commute-list commute-list-popup" : "commute-list";
  return `
    <div class="${className}">
      ${result.commutes
        .map((commute) => {
          const summary =
            hasValue(commute.morning_duration_minutes) && hasValue(commute.evening_duration_minutes)
              ? `AM ${formatMinutes(commute.morning_duration_minutes)} / PM ${formatMinutes(commute.evening_duration_minutes)}`
              : hasValue(commute.duration_minutes)
                ? `${formatMinutes(commute.duration_minutes)} avg`
                : escapeHtml(commute.status);
          const cost = hasValue(commute.daily_total_cost) ? `${formatMoney(commute.daily_total_cost)}/day` : "";
          const routeLink = commute.route_url
            ? `<a class="route-link" href="${escapeHtml(commute.route_url)}" target="_blank" rel="noreferrer">Route</a>`
            : "";
          return `
            <div class="commute-item">
              <span class="commute-office">${escapeHtml(commute.office_name)}</span>
              <span class="commute-cost">${cost}</span>
              <span class="commute-summary">${summary}</span>
              ${routeLink}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderDetail(result, index) {
  const panel = $("#detail-panel");
  const deltaClass = result.home_delta >= 0 ? "positive" : "negative";
  panel.innerHTML = `
    <div class="detail-topline">
      <div>
        <h2>${escapeHtml(result.name)}</h2>
        <div class="source">Rank #${index + 1} / ${escapeHtml(result.source)}</div>
      </div>
      <button class="close-button" type="button" aria-label="Close details">x</button>
    </div>
    <div class="detail-grid">
      <div class="detail-stat">
        <span>Score</span>
        <strong>${Math.round(result.score * 100)}</strong>
      </div>
      <div class="detail-stat">
        <span>Target share</span>
        <strong>${formatPercent(result.target_share)}</strong>
      </div>
      <div class="detail-stat">
        <span>Vs. home</span>
        <strong class="${deltaClass}">${formatDelta(result.home_delta)}</strong>
      </div>
      <div class="detail-stat">
        <span>1BR rent</span>
        <strong>${formatMoney(result.monthly_rent_1br)}</strong>
      </div>
      <div class="detail-stat">
        <span>Total monthly</span>
        <strong>${formatMoney(result.total_monthly_cost)}</strong>
      </div>
      <div class="detail-stat">
        <span>Monthly commute</span>
        <strong>${formatMoney(result.monthly_commute_cost)}</strong>
      </div>
      <div class="detail-stat">
        <span>Rent source</span>
        <strong>${escapeHtml(result.rent_source ?? "--")}</strong>
      </div>
    </div>
    <h2>Office commutes</h2>
    ${renderCommuteList(result, "detail")}
  `;
  panel.hidden = false;
}

function selectResult(index, options = {}) {
  const result = currentResults[index];
  if (!result) return;
  selectedIndex = index;
  document.querySelectorAll(".result-card").forEach((card) => {
    card.classList.toggle("is-selected", Number(card.dataset.index) === index);
  });
  renderDetail(result, index);

  const marker = currentMarkers[index];
  if (marker && options.panToMarker !== false) {
    map.panTo(marker.getLatLng());
    marker.openPopup();
  }
}

function renderMetrics(payload) {
  const targetAge = targetAgeRange();
  $("#metric-candidates").textContent = payload.status.candidate_count;
  $("#metric-scored").textContent = payload.status.scored_count;
  $("#metric-reference").textContent = payload.reference ? formatPercent(payload.reference.target_share) : "--";
  $("#metric-reference-label").textContent = `Sammamish ${targetAge.min}-${targetAge.max}`;
}

function renderStatus(payload) {
  const sourceWarning = payload.status.warnings?.length ? ` ${payload.status.warnings.join(" ")}` : "";
  const commuteStatuses = [
    ...new Set(payload.results.flatMap((result) => result.commutes.map((commute) => commute.status)))
  ].filter((status) => status && status !== "OK" && status !== "missing_google_maps_api_key");
  const hasCommuteTimes = payload.results.some((result) => result.average_commute_minutes !== null);
  const hasRent = payload.results.some((result) => result.monthly_rent_1br !== null);

  if (commuteStatuses.length > 0) {
    $("#status").textContent = `Ranked with available demographics and rent data. Google Routes returned ${commuteStatuses.join(", ")}.${sourceWarning}`;
    return;
  }

  $("#status").textContent =
    payload.status.maps_enabled && hasCommuteTimes
      ? `Ranked with commute and ${hasRent ? "rent" : "available"} data.${sourceWarning}`
      : `Ranked by demographics${hasRent ? " and rent" : ""}. Set up Google Routes for commute times and mapped candidate locations.${sourceWarning}`;
}

function renderError(message) {
  $("#status").textContent = message;
  $("#results-body").innerHTML = `<p class="empty-state">${escapeHtml(message)}</p>`;
  $("#drawer-count").textContent = "0 shown";
  $("#detail-panel").hidden = true;
}

async function analyze(event) {
  event?.preventDefault();
  if (isAnalyzing) return;

  const payload = payloadFromForm();
  const routeRequests = estimatedRouteRequests(payload);
  setAnalyzingState(true);
  $("#status").textContent = `Loading demographics, rents, and ${routeRequests} Google Routes estimates...`;
  $("#results-body").innerHTML = '<p class="empty-state">Analyzing commute candidates...</p>';
  $("#drawer-count").textContent = "Working";
  $("#detail-panel").hidden = true;

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok || result.error) {
      throw new Error(result.error || "Analysis failed");
    }
    if (!result.status?.api_version || result.status.api_version < 3) {
      throw new Error("The Python server is still running older backend code. Stop it and restart python3 server.py.");
    }

    renderMetrics(result);
    renderResults(result.results);
    renderMap(result.results);
    renderStatus(result);
    if (result.results.length && window.matchMedia("(min-width: 901px)").matches) {
      selectResult(0, { panToMarker: false });
    }
  } finally {
    setAnalyzingState(false);
  }
}

async function loadDefaults() {
  const response = await fetch("/api/config");
  const config = await response.json();
  $("#home").value = config.home;
  $("#target-age-min").value = config.target_age_min ?? 24;
  $("#target-age-max").value = config.target_age_max ?? 29;
  $("#directions-limit").value = config.directions_limit;
  $("#morning-arrival-time").value = config.commute_schedule?.morning_arrival_time ?? "09:00";
  $("#evening-departure-time").value = config.commute_schedule?.evening_departure_time ?? "17:00";
  $("#miles-per-gallon").value = config.vehicle?.miles_per_gallon ?? 15;
  $("#fuel-price-per-gallon").value = config.vehicle?.fuel_price_per_gallon ?? 5.0;
  $("#commute-days-per-month").value = config.rent?.commute_days_per_month ?? 20;
  $("#demographic-weight").value = Math.round((config.weights.demographics ?? 0.45) * 100);
  $("#commute-weight").value = Math.round((config.weights.commute ?? 0.25) * 100);
  $("#affordability-weight").value = Math.round((config.weights.affordability ?? 0.30) * 100);
  syncWeights();
  $("#include-neighborhoods").checked = config.include_seattle_neighborhoods;
  $("#office-rows").innerHTML = "";
  config.office_locations.forEach(addOfficeRow);
}

window.addEventListener("DOMContentLoaded", async () => {
  $("#config-form").addEventListener("submit", (event) => {
    analyze(event).catch((error) => renderError(error.message));
  });
  $("#add-office-button").addEventListener("click", () => addOfficeRow());
  $("#office-rows").addEventListener("click", (event) => {
    if (!event.target.matches(".remove-office-button")) return;
    event.target.closest(".office-row").remove();
    if (!document.querySelector(".office-row")) addOfficeRow();
  });
  $("#results-body").addEventListener("click", (event) => {
    const card = event.target.closest(".result-card");
    if (!card) return;
    selectResult(Number(card.dataset.index));
  });
  $("#detail-panel").addEventListener("click", (event) => {
    if (!event.target.matches(".close-button")) return;
    $("#detail-panel").hidden = true;
    selectedIndex = null;
    document.querySelectorAll(".result-card").forEach((card) => card.classList.remove("is-selected"));
  });
  $("#demographic-weight").addEventListener("input", syncWeights);
  $("#commute-weight").addEventListener("input", syncWeights);
  $("#affordability-weight").addEventListener("input", syncWeights);

  await loadDefaults();
  $("#status").textContent = "Ready. Adjust settings and click Analyze.";
  renderMap([]);
});
