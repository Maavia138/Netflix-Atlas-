/**
 * Netflix Atlas — Frontend Controller
 * =====================================
 * Handles API communication, catalog rendering, pagination,
 * title detail display, and content-based recommendations.
 */

"use strict";

// ── Configuration ─────────────────────────────────────────────────────────
const API_BASE = "http://127.0.0.1:5000/api";

// ── State ──────────────────────────────────────────────────────────────────
const state = {
    page:          1,
    pageSize:      12,
    totalPages:    1,
    selectedShowId: null,
};

// ── DOM references ─────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const els = {
    statsGrid:      $("stats-grid"),
    catalogGrid:    $("catalog-grid"),
    detailsCard:    $("details-card"),
    recommendations: $("recommendations"),
    resultsMeta:    $("results-meta"),
    pageLabel:      $("page-label"),
    apiStatus:      $("api-status"),
    recordCount:    $("record-count"),
    searchInput:    $("search-input"),
    typeFilter:     $("type-filter"),
    countryFilter:  $("country-filter"),
    genreFilter:    $("genre-filter"),
    searchBtn:      $("search-btn"),
    prevPage:       $("prev-page"),
    nextPage:       $("next-page"),
    toast:          $("toast"),
};

// ── Toast notification ─────────────────────────────────────────────────────
let toastTimer = null;

function showToast(message, durationMs = 4000) {
    els.toast.textContent = message;
    els.toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove("visible"), durationMs);
}

// ── Generic fetch wrapper ──────────────────────────────────────────────────
async function fetchJson(url) {
    const controller = new AbortController();
    const timeout    = setTimeout(() => controller.abort(), 10_000);

    try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.error || `HTTP ${response.status}`);
        }
        return await response.json();
    } finally {
        clearTimeout(timeout);
    }
}

// ── Skeleton placeholders ──────────────────────────────────────────────────
function renderSkeletons(count = 6) {
    els.catalogGrid.innerHTML = Array.from({ length: count })
        .map(() => `<div class="skeleton" aria-hidden="true"></div>`)
        .join("");
}

// ── Health check ───────────────────────────────────────────────────────────
async function loadHealth() {
    try {
        const data = await fetchJson(`${API_BASE}/health`);
        els.apiStatus.textContent  = data.status === "ok" ? "✓ Connected" : "⚠ Degraded";
        els.recordCount.textContent = `${data.records.toLocaleString()} titles ready`;
    } catch {
        els.apiStatus.textContent   = "✗ Offline";
        els.recordCount.textContent = "Start the Flask server";
        showToast("Backend is offline — run: python src/api.py");
    }
}

// ── Summary statistics ─────────────────────────────────────────────────────
async function loadSummary() {
    try {
        const s = await fetchJson(`${API_BASE}/summary`);
        const cards = [
            { label: "Total Titles",   value: s.total_titles.toLocaleString() },
            { label: "Movies",         value: s.movies.toLocaleString() },
            { label: "TV Shows",       value: s.tv_shows.toLocaleString() },
            { label: "Latest Release", value: s.latest_release_year },
            { label: "Top Country",    value: s.top_country },
            { label: "Top Genre",      value: s.top_genre },
        ];

        els.statsGrid.innerHTML = cards
            .map(({ label, value }) => `
                <article class="stat-card">
                    <span>${label}</span>
                    <strong>${value}</strong>
                </article>
            `)
            .join("");
    } catch (err) {
        console.warn("Summary load failed:", err.message);
    }
}

// ── Build catalog query string ─────────────────────────────────────────────
function buildQuery() {
    const params = new URLSearchParams({
        page:      String(state.page),
        page_size: String(state.pageSize),
        search:    els.searchInput.value.trim(),
        type:      els.typeFilter.value,
        country:   els.countryFilter.value.trim(),
        genre:     els.genreFilter.value.trim(),
    });
    return params.toString();
}

// ── Catalog listing ────────────────────────────────────────────────────────
async function loadCatalog() {
    renderSkeletons();
    els.resultsMeta.textContent = "Searching…";

    try {
        const data = await fetchJson(`${API_BASE}/catalog?${buildQuery()}`);
        state.totalPages = data.total_pages;

        els.resultsMeta.textContent  = `${data.total.toLocaleString()} result${data.total !== 1 ? "s" : ""}`;
        els.pageLabel.textContent    = `Page ${data.page} of ${data.total_pages}`;
        els.prevPage.disabled        = data.page <= 1;
        els.nextPage.disabled        = data.page >= data.total_pages;

        if (!data.items.length) {
            els.catalogGrid.innerHTML = `<p class="empty-state">No titles matched your filters.</p>`;
            return;
        }

        els.catalogGrid.innerHTML = data.items
            .map((item) => `
                <button
                    class="title-card${state.selectedShowId === item.show_id ? " active" : ""}"
                    data-show-id="${escapeHtml(item.show_id)}"
                    data-title="${escapeHtml(item.title)}"
                    aria-label="View details for ${escapeHtml(item.title)}"
                    role="listitem"
                >
                    <span class="pill">${escapeHtml(item.type)}</span>
                    <h3>${escapeHtml(item.title)}</h3>
                    <p>${escapeHtml(item.listed_in || "Genre unavailable")}</p>
                    <small>${escapeHtml(item.country || "Unknown country")} · ${item.release_year}</small>
                </button>
            `)
            .join("");

        els.catalogGrid.querySelectorAll(".title-card").forEach((card) => {
            card.addEventListener("click", () =>
                selectTitle(card.dataset.showId, card.dataset.title)
            );
        });
    } catch (err) {
        els.catalogGrid.innerHTML = `<p class="empty-state">Failed to load catalog: ${escapeHtml(err.message)}</p>`;
        showToast(`Catalog error: ${err.message}`);
    }
}

// ── Title details + recommendations ───────────────────────────────────────
async function selectTitle(showId, title) {
    state.selectedShowId = showId;

    // Update active state on cards
    els.catalogGrid.querySelectorAll(".title-card").forEach((c) => {
        c.classList.toggle("active", c.dataset.showId === showId);
    });

    // Details
    els.detailsCard.innerHTML = `<div class="skeleton" style="height:200px"></div>`;
    try {
        const d = await fetchJson(`${API_BASE}/title/${encodeURIComponent(showId)}`);
        els.detailsCard.innerHTML = `
            <h3>${escapeHtml(d.title)}</h3>
            ${detailRow("Type",        d.type)}
            ${detailRow("Director",    d.director)}
            ${detailRow("Cast",        d.cast)}
            ${detailRow("Country",     d.country)}
            ${detailRow("Rating",      d.rating)}
            ${detailRow("Duration",    d.duration)}
            ${detailRow("Genres",      d.listed_in)}
            ${detailRow("Description", d.description)}
        `;
    } catch (err) {
        els.detailsCard.innerHTML = `<p class="placeholder-text">Could not load details: ${escapeHtml(err.message)}</p>`;
    }

    // Recommendations
    els.recommendations.innerHTML = `<div class="skeleton" style="height:80px"></div>`;
    try {
        const recs = await fetchJson(`${API_BASE}/recommendations?title=${encodeURIComponent(title)}`);
        els.recommendations.innerHTML = recs.items.length
            ? recs.items.map((r) => `
                <article class="recommendation-card">
                    <h4>${escapeHtml(r.title)}</h4>
                    <p>${escapeHtml(r.listed_in || "")}</p>
                    <small>${escapeHtml(r.country || "Unknown")} · ${r.release_year}</small>
                </article>
              `).join("")
            : `<p class="placeholder-text">No recommendations found for this title.</p>`;
    } catch (err) {
        els.recommendations.innerHTML = `<p class="placeholder-text">Recommendations unavailable.</p>`;
    }
}

// ── Helper: render a detail row (skip if empty) ────────────────────────────
function detailRow(label, value) {
    if (!value) return "";
    return `<p><strong>${label}:</strong> ${escapeHtml(String(value))}</p>`;
}

// ── XSS guard ──────────────────────────────────────────────────────────────
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ── Debounce utility ───────────────────────────────────────────────────────
function debounce(fn, delay) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

// ── Pagination ─────────────────────────────────────────────────────────────
function goToPage(delta) {
    const newPage = state.page + delta;
    if (newPage < 1 || newPage > state.totalPages) return;
    state.page = newPage;
    loadCatalog();
    window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── Event listeners ────────────────────────────────────────────────────────
els.searchBtn.addEventListener("click", () => { state.page = 1; loadCatalog(); });
els.prevPage.addEventListener("click",  () => goToPage(-1));
els.nextPage.addEventListener("click",  () => goToPage(+1));

const debouncedSearch = debounce(() => { state.page = 1; loadCatalog(); }, 350);

[els.searchInput, els.countryFilter, els.genreFilter].forEach((input) => {
    input.addEventListener("input", debouncedSearch);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { state.page = 1; loadCatalog(); }
    });
});

els.typeFilter.addEventListener("change", () => { state.page = 1; loadCatalog(); });

// ── Initialisation ─────────────────────────────────────────────────────────
async function init() {
    await loadHealth();
    await Promise.all([loadSummary(), loadCatalog()]);
}

init().catch((err) => {
    els.statsGrid.innerHTML = `<p class="empty-state">Initialisation failed: ${err.message}. Is the Flask server running?</p>`;
    console.error("Init error:", err);
});
