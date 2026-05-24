"""
Netflix Atlas — Flask REST API
================================
Serves catalog browsing, full-text search, title details, and
TF-IDF content-based recommendations.

Run:
    python src/api.py
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("netflix_atlas")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "netflix_titles.csv"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)


# ---------------------------------------------------------------------------
# Catalog Service
# ---------------------------------------------------------------------------
class NetflixCatalogService:
    """
    Encapsulates all data-access and ML logic for the Netflix catalog.

    Attributes
    ----------
    df : pd.DataFrame
        Cleaned and feature-enriched catalog.
    similarity : np.ndarray
        Pairwise cosine similarity matrix computed via TF-IDF.
    """

    def __init__(self, data_path: Path) -> None:
        if not data_path.exists():
            raise FileNotFoundError(f"Dataset not found at: {data_path}")
        self.data_path = data_path
        logger.info("Loading dataset from %s", data_path)
        self.df = self._load_data()
        logger.info("Building TF-IDF similarity index over %d titles …", len(self.df))
        self._vectorizer, self.similarity = self._build_similarity_index()
        logger.info("Service ready.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_data(self) -> pd.DataFrame:
        df = pd.read_csv(self.data_path)

        # ── Deduplication ──────────────────────────────────────────────
        before = len(df)
        df.drop_duplicates(subset="show_id", inplace=True)
        logger.info("Dropped %d duplicate rows.", before - len(df))

        # ── Fill missing values ────────────────────────────────────────
        text_cols = ["director", "cast", "country", "rating", "duration", "listed_in", "description"]
        df[text_cols] = df[text_cols].fillna("")

        # ── Numeric duration ───────────────────────────────────────────
        df["duration_value"] = (
            df["duration"]
            .str.extract(r"(\d+)", expand=False)
            .fillna(0)
            .astype(int)
        )

        # ── Search blob (lowercase for fast contains-filtering) ────────
        df["search_blob"] = (
            df[["title", "listed_in", "description", "director", "cast", "country"]]
            .fillna("")
            .agg(" ".join, axis=1)
            .str.lower()
        )

        return df

    def _build_similarity_index(self) -> tuple[TfidfVectorizer, Any]:
        """
        Build a pairwise cosine-similarity matrix using TF-IDF
        over the search blob.  linear_kernel is used instead of
        cosine_similarity because the TF-IDF vectors are already
        L2-normalised, making dot-product == cosine similarity
        and avoiding an unnecessary sqrt pass.
        """
        vectorizer = TfidfVectorizer(
            stop_words="english",
            min_df=2,        # ignore terms that appear only once
            max_df=0.95,     # ignore near-universal terms
            sublinear_tf=True,  # apply log normalisation to TF
        )
        matrix = vectorizer.fit_transform(self.df["search_blob"])
        similarity = linear_kernel(matrix, matrix)
        return vectorizer, similarity

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_summary(self) -> dict[str, Any]:
        movies = int((self.df["type"] == "Movie").sum())
        shows  = int((self.df["type"] == "TV Show").sum())

        top_country = (
            self.df.loc[self.df["country"] != "", "country"]
            .value_counts()
            .index[0] if (self.df["country"] != "").any() else "N/A"
        )
        top_genre = (
            self.df.loc[self.df["listed_in"] != "", "listed_in"]
            .value_counts()
            .index[0] if (self.df["listed_in"] != "").any() else "N/A"
        )

        return {
            "total_titles":       int(len(self.df)),
            "movies":             movies,
            "tv_shows":           shows,
            "latest_release_year": int(self.df["release_year"].max()),
            "top_country":        top_country,
            "top_genre":          top_genre,
        }

    def list_titles(
        self,
        search: str = "",
        content_type: str = "",
        country: str = "",
        genre: str = "",
        page: int = 1,
        page_size: int = 12,
    ) -> dict[str, Any]:
        mask = pd.Series([True] * len(self.df), index=self.df.index)

        if search:
            mask &= self.df["search_blob"].str.contains(search.lower(), regex=False, na=False)
        if content_type:
            mask &= self.df["type"].str.lower() == content_type.lower()
        if country:
            mask &= self.df["country"].str.contains(country, case=False, regex=False, na=False)
        if genre:
            mask &= self.df["listed_in"].str.contains(genre, case=False, regex=False, na=False)

        filtered = self.df[mask]
        total    = int(len(filtered))
        page     = max(page, 1)
        start    = (page - 1) * page_size
        end      = start + page_size
        records  = (
            filtered
            .iloc[start:end]
            [["show_id", "title", "type", "country", "release_year", "listed_in", "duration"]]
            .to_dict(orient="records")
        )

        return {
            "items":       records,
            "page":        page,
            "page_size":   page_size,
            "total":       total,
            "total_pages": max((total + page_size - 1) // page_size, 1),
        }

    def get_title(self, show_id: str) -> dict[str, Any] | None:
        match = self.df[self.df["show_id"] == show_id]
        if match.empty:
            return None
        row = match.iloc[0].copy()
        # Drop internal helper columns before sending to client
        row.drop(labels=["search_blob", "duration_value"], errors="ignore", inplace=True)
        return row.to_dict()

    def recommend_titles(self, title: str, limit: int = 6) -> list[dict[str, Any]]:
        normalized = title.strip().lower()
        matches    = self.df[self.df["title"].str.lower() == normalized]
        if matches.empty:
            logger.warning("No title found matching '%s' for recommendations.", title)
            return []

        idx    = matches.index[0]
        scores = self.similarity[idx]

        # Get top-N indices (excluding the title itself)
        top_indices = scores.argsort()[::-1][1: limit + 1]

        cols = ["show_id", "title", "type", "country", "release_year", "listed_in", "description"]
        return self.df.loc[top_indices, cols].to_dict(orient="records")


# ---------------------------------------------------------------------------
# Singleton service — loaded once at startup
# ---------------------------------------------------------------------------
try:
    catalog_service = NetflixCatalogService(DATA_PATH)
except FileNotFoundError as exc:
    logger.critical(str(exc))
    catalog_service = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _service_guard():
    """Return 503 if the service failed to initialise."""
    if catalog_service is None:
        return jsonify({"error": "Dataset not found. Check DATA_PATH."}), 503
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def home() -> Any:
    return jsonify({
        "message": "Netflix Atlas API is running.",
        "endpoints": {
            "GET /api/health":                   "Health check",
            "GET /api/summary":                  "Catalog statistics",
            "GET /api/catalog":                  "Paginated / filtered title list",
            "GET /api/title/<show_id>":          "Full title metadata",
            "GET /api/recommendations?title=…":  "Content-based recommendations",
        },
    })


@app.get("/api/health")
def health() -> Any:
    if catalog_service is None:
        return jsonify({"status": "error", "message": "Dataset not loaded."}), 503
    return jsonify({
        "status":         "ok",
        "dataset_loaded": True,
        "records":        len(catalog_service.df),
    })


@app.get("/api/summary")
def summary() -> Any:
    if (guard := _service_guard()):
        return guard
    return jsonify(catalog_service.get_summary())


@app.get("/api/catalog")
def catalog() -> Any:
    if (guard := _service_guard()):
        return guard

    try:
        page      = max(int(request.args.get("page",      1)),  1)
        page_size = min(int(request.args.get("page_size", 12)), 100)  # cap at 100
    except ValueError:
        return jsonify({"error": "page and page_size must be integers."}), 400

    payload = catalog_service.list_titles(
        search       = request.args.get("search",  "").strip(),
        content_type = request.args.get("type",    "").strip(),
        country      = request.args.get("country", "").strip(),
        genre        = request.args.get("genre",   "").strip(),
        page         = page,
        page_size    = page_size,
    )
    return jsonify(payload)


@app.get("/api/title/<show_id>")
def title_details(show_id: str) -> Any:
    if (guard := _service_guard()):
        return guard
    item = catalog_service.get_title(show_id.strip())
    if item is None:
        return jsonify({"error": f"No title found with show_id '{show_id}'."}), 404
    return jsonify(item)


@app.get("/api/recommendations")
def recommendations() -> Any:
    if (guard := _service_guard()):
        return guard

    title = request.args.get("title", "").strip()
    if not title:
        return jsonify({"error": "Query parameter 'title' is required."}), 400

    try:
        limit = min(int(request.args.get("limit", 6)), 20)
    except ValueError:
        return jsonify({"error": "'limit' must be an integer."}), 400

    return jsonify({"items": catalog_service.recommend_titles(title, limit=limit)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info("Starting Netflix Atlas API on port %d (debug=%s)", port, debug)
    app.run(debug=debug, host="127.0.0.1", port=port)
