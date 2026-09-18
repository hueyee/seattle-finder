let map;
let markerLayer;

const $ = (selector) => document.querySelector(selector);

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

function targetAgeRange() {
  const min = Number($("#target-age-min").value);
  const max = Number($("#target-age-max").value);
  return {
    min: Math.min(min, max),
    max: Math.max(min, max)
  };
}

function parseOffices() {
  return $("#office-locations")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, address] = line.split("|").map((part) => part.trim());
      const resolvedAddress = address || name;
      if (!/\d/.test(resolvedAddress)) {
        throw new Error("Office locations need exact street addresses, one per line.");
      }
      return { name: name || resolvedAddress, address: resolvedAddress };
    });
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
      demographics: Number($("#demographic-weight").value),
      commute: Number($("#commute-weight").value)
    },
    office_locations: parseOffices()
  };
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

function addMarker(result, rank) {
  if (!result.location) return null;
  const color = markerColor(result.score);
  const marker = L.circleMarker([result.location.lat, result.location.lng], {
    radius: Math.max(7, 16 - rank * 0.25),
    color,
    fillColor: color,
    fillOpacity: 0.82,
    weight: 2
  });
  const firstRoute = result.commutes.find((commute) => commute.route_url);
  marker.bindPopup(`
    <strong>${rank}. ${result.name}</strong><br>
    Score: ${(result.score * 100).toFixed(0)}<br>
    Young-adult share: ${formatPercent(result.target_share)}<br>
    Avg commute: ${formatMinutes(result.average_commute_minutes)}
    ${firstRoute ? `<br><a href="${firstRoute.route_url}" target="_blank" rel="noreferrer">Open route</a>` : ""}
  `);
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

  const markers = located.slice(0, 40).map((result, index) => addMarker(result, index + 1)).filter(Boolean);
  if (markers.length > 0) {
    const group = L.featureGroup(markers);
    map.fitBounds(group.getBounds().pad(0.18));
  } else {
    map.setView([47.61, -122.16], 10);
  }
  window.setTimeout(() => map.invalidateSize(), 0);
}

function renderTable(results) {
  const body = $("#results-body");
  if (!results.length) {
    body.innerHTML = '<tr><td colspan="7">No results returned.</td></tr>';
    return;
  }

  body.innerHTML = results
    .map((result, index) => {
      const deltaClass = result.home_delta >= 0 ? "positive" : "negative";
      const route = result.commutes.find((commute) => commute.route_url);
      return `
        <tr>
          <td>${index + 1}</td>
          <td>
            <div class="place-name">${result.name}</div>
            ${route ? `<a class="route-link" href="${route.route_url}" target="_blank" rel="noreferrer">Route</a>` : ""}
          </td>
          <td><span class="score-pill">${(result.score * 100).toFixed(0)}</span></td>
          <td>${formatPercent(result.target_share)}</td>
          <td class="${deltaClass}">${formatDelta(result.home_delta)}</td>
          <td>${formatMinutes(result.average_commute_minutes)}</td>
          <td><span class="source">${result.source}</span></td>
        </tr>
      `;
    })
    .join("");
}

function renderMetrics(payload) {
  const targetAge = targetAgeRange();
  $("#metric-candidates").textContent = payload.status.candidate_count;
  $("#metric-scored").textContent = payload.status.scored_count;
  $("#metric-reference").textContent = payload.reference ? formatPercent(payload.reference.target_share) : "--";
  $("#metric-reference-label").textContent = `Sammamish ${targetAge.min}-${targetAge.max}`;
}

function renderStatus(payload) {
  const sourceWarning = payload.status.warnings?.length ? " Census place data needs CENSUS_API_KEY." : "";
  const commuteStatuses = [
    ...new Set(payload.results.flatMap((result) => result.commutes.map((commute) => commute.status)))
  ].filter((status) => status && status !== "OK" && status !== "missing_google_maps_api_key");
  const hasCommuteTimes = payload.results.some((result) => result.average_commute_minutes !== null);

  if (commuteStatuses.length > 0) {
    $("#status").textContent = `Ranked by demographics. Google Routes returned ${commuteStatuses.join(", ")}.${sourceWarning}`;
    return;
  }

  $("#status").textContent =
    payload.status.maps_enabled && hasCommuteTimes
      ? `Ranked with live Google Routes commute data.${sourceWarning}`
      : `Ranked by demographics. Set up Google Routes for commute times and mapped candidate locations.${sourceWarning}`;
}

async function analyze(event) {
  event?.preventDefault();
  $("#status").textContent = "Loading demographic data and commute times...";
  $("#results-body").innerHTML = '<tr><td colspan="7">Analyzing...</td></tr>';

  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadFromForm())
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "Analysis failed");
  }

  renderMetrics(payload);
  renderTable(payload.results);
  renderMap(payload.results);
  renderStatus(payload);
}

async function loadDefaults() {
  const response = await fetch("/api/config");
  const config = await response.json();
  $("#home").value = config.home;
  $("#target-age-min").value = config.target_age_min ?? 24;
  $("#target-age-max").value = config.target_age_max ?? 29;
  $("#directions-limit").value = config.directions_limit;
  $("#demographic-weight").value = config.weights.demographics;
  $("#commute-weight").value = config.weights.commute;
  $("#include-neighborhoods").checked = config.include_seattle_neighborhoods;
  $("#office-locations").value = config.office_locations
    .map((office) => `${office.name}|${office.address}`)
    .join("\n");
}

window.addEventListener("DOMContentLoaded", async () => {
  $("#config-form").addEventListener("submit", (event) => {
    analyze(event).catch((error) => {
      $("#status").textContent = error.message;
      $("#results-body").innerHTML = `<tr><td colspan="7">${error.message}</td></tr>`;
    });
  });
  await loadDefaults();
  analyze().catch((error) => {
    $("#status").textContent = error.message;
  });
});
