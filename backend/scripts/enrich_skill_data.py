#!/usr/bin/env python3
"""
Enrich skill data files (skill_aliases.json, canonical_skills.json, skill_clusters.json)
using ESCO skills taxonomy and embedding-based similarity.

Usage:
    python scripts/enrich_skill_data.py

    # Use a bundled ESCO CSV instead of the API:
    python scripts/enrich_skill_data.py --esco-csv path/to/esco_skills.csv

    # Dry run — show what would change without writing:
    python scripts/enrich_skill_data.py --dry-run

This script:
1. Loads current skill_aliases.json, canonical_skills.json, skill_clusters.json
2. Fetches skills from the ESCO API (or reads from a bundled CSV)
3. Adds new canonical skills and their aliases
4. Uses SentenceTransformer to find similar skills (cosine > 0.82) as aliases
5. Re-clusters all skills using KMeans (k=40)
6. Backs up originals as *.bak before overwriting
"""

import argparse
import csv
import json
import logging
import pathlib
import shutil
import sys
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Paths
_SCRIPT_DIR = pathlib.Path(__file__).parent
_DATA_DIR = _SCRIPT_DIR.parent / "src" / "data"

ALIASES_PATH = _DATA_DIR / "skill_aliases.json"
CANONICAL_PATH = _DATA_DIR / "canonical_skills.json"
CLUSTERS_PATH = _DATA_DIR / "skill_clusters.json"


def load_json(path: pathlib.Path):
    """Load a JSON file and return its contents."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: pathlib.Path, data, *, backup: bool = True) -> None:
    """Save data to JSON, creating a .bak backup first."""
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        logger.info("Backup created: %s", bak)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# ESCO data ingestion
# ---------------------------------------------------------------------------

def fetch_esco_skills_api(limit: int = 500) -> List[Dict]:
    """
    Fetch skills from the ESCO REST API.

    Returns list of dicts with keys: preferred_label, alt_labels.
    Falls back gracefully if the API is unreachable.
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed — cannot fetch ESCO API. Use --esco-csv instead.")
        return []

    skills: List[Dict] = []
    url = "https://ec.europa.eu/esco/api/search"
    params = {
        "text": "*",
        "type": "skill",
        "language": "en",
        "limit": min(limit, 100),
        "offset": 0,
    }

    try:
        while len(skills) < limit:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("_embedded", {}).get("results", [])
            if not results:
                break
            for r in results:
                preferred = r.get("title", "")
                # Fetch detail to get altLabels
                uri = r.get("uri", "")
                alt_labels: List[str] = []
                if uri:
                    try:
                        detail = requests.get(
                            "https://ec.europa.eu/esco/api/resource/skill",
                            params={"uri": uri, "language": "en"},
                            timeout=15,
                        ).json()
                        alt_labels = detail.get("alternativeLabel", {}).get("en", [])
                    except Exception:
                        pass
                skills.append({"preferred_label": preferred, "alt_labels": alt_labels})
            params["offset"] += len(results)
            if len(results) < params["limit"]:
                break
        logger.info("Fetched %d skills from ESCO API.", len(skills))
    except Exception as e:
        logger.warning("ESCO API fetch failed: %s", e)

    return skills


def load_esco_csv(csv_path: str) -> List[Dict]:
    """
    Load skills from a bundled ESCO CSV file.

    Expected columns: preferredLabel, altLabels (pipe-separated).
    """
    skills: List[Dict] = []
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                preferred = row.get("preferredLabel", row.get("preferred_label", "")).strip()
                alt_raw = row.get("altLabels", row.get("alt_labels", ""))
                alt_labels = [a.strip() for a in alt_raw.split("|") if a.strip()] if alt_raw else []
                if preferred:
                    skills.append({"preferred_label": preferred, "alt_labels": alt_labels})
        logger.info("Loaded %d skills from CSV: %s", len(skills), csv_path)
    except Exception as e:
        logger.error("Failed to load ESCO CSV: %s", e)
    return skills


# ---------------------------------------------------------------------------
# Enrichment logic
# ---------------------------------------------------------------------------

def enrich_canonical_and_aliases(
    canonical: List[str],
    aliases: Dict[str, str],
    esco_skills: List[Dict],
) -> Tuple[List[str], Dict[str, str], int]:
    """
    Add ESCO skills not already present in canonical_skills.

    Returns updated (canonical, aliases, new_count).
    """
    canonical_set_lower = {s.lower() for s in canonical}
    alias_keys_lower = set(aliases.keys())
    new_count = 0

    for skill in esco_skills:
        preferred = skill["preferred_label"]
        if preferred.lower() in canonical_set_lower:
            continue

        # Add to canonical
        canonical.append(preferred)
        canonical_set_lower.add(preferred.lower())
        new_count += 1

        # Add alt labels as aliases
        for alt in skill.get("alt_labels", []):
            alt_lower = alt.lower().strip()
            if alt_lower and alt_lower not in alias_keys_lower:
                aliases[alt_lower] = preferred
                alias_keys_lower.add(alt_lower)

    return canonical, aliases, new_count


def add_similarity_aliases(
    canonical: List[str],
    aliases: Dict[str, str],
    model,
    threshold: float = 0.82,
    top_k: int = 5,
) -> Dict[str, str]:
    """
    For each canonical skill, find the top-K most similar skills
    (cosine similarity > threshold) and add them as aliases.
    """
    logger.info("Computing embeddings for %d canonical skills...", len(canonical))
    embeddings = model.encode(canonical, show_progress_bar=True, convert_to_numpy=True)

    # Normalize for cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embeddings_norm = embeddings / norms

    # Compute pairwise similarity
    sim_matrix = embeddings_norm @ embeddings_norm.T

    alias_keys_lower = set(aliases.keys())
    added = 0

    for i, skill in enumerate(canonical):
        sims = sim_matrix[i]
        # Get top-K indices (excluding self)
        top_indices = np.argsort(sims)[::-1][1:top_k + 1]
        for j in top_indices:
            if sims[j] < threshold:
                break
            similar_skill = canonical[j]
            # Add the similar skill as an alias pointing to this skill
            key = similar_skill.lower()
            if key not in alias_keys_lower and key != skill.lower():
                aliases[key] = skill
                alias_keys_lower.add(key)
                added += 1

    logger.info("Added %d similarity-based aliases (threshold=%.2f).", added, threshold)
    return aliases


def recluster_skills(
    canonical: List[str],
    model,
    k: int = 40,
) -> Dict[str, List[str]]:
    """
    Cluster all canonical skills using KMeans on embeddings.

    Returns dict mapping cluster_name -> list of skill names.
    """
    from sklearn.cluster import KMeans

    logger.info("Clustering %d skills into %d clusters...", len(canonical), k)
    embeddings = model.encode(canonical, show_progress_bar=True, convert_to_numpy=True)

    # Adjust k if fewer skills than clusters
    actual_k = min(k, len(canonical))
    kmeans = KMeans(n_clusters=actual_k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(embeddings)

    clusters: Dict[str, List[str]] = {}
    for idx, label in enumerate(labels):
        cluster_name = f"cluster_{label}"
        clusters.setdefault(cluster_name, []).append(canonical[idx])

    # Try to name clusters by the most central skill
    named_clusters: Dict[str, List[str]] = {}
    for cname, members in clusters.items():
        if members:
            # Use the first member's canonical name as a readable cluster name
            # Build a descriptive key from common words
            name_key = members[0].lower().replace(" ", "_")
            # Avoid collisions
            if name_key in named_clusters:
                name_key = f"{name_key}_{cname}"
            named_clusters[name_key] = members

    logger.info("Created %d skill clusters.", len(named_clusters))
    return named_clusters


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the skill data enrichment pipeline."""
    parser = argparse.ArgumentParser(description="Enrich skill data files with ESCO taxonomy.")
    parser.add_argument("--esco-csv", help="Path to bundled ESCO skills CSV (skips API).")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing files.")
    parser.add_argument("--skip-clusters", action="store_true", help="Skip re-clustering step.")
    parser.add_argument("--threshold", type=float, default=0.82, help="Cosine similarity threshold for aliases.")
    parser.add_argument("--k", type=int, default=40, help="Number of KMeans clusters.")
    args = parser.parse_args()

    # Load current data
    canonical: List[str] = load_json(CANONICAL_PATH)
    aliases: Dict[str, str] = load_json(ALIASES_PATH)
    clusters: Dict[str, List[str]] = load_json(CLUSTERS_PATH)

    logger.info("Current data: %d canonical, %d aliases, %d clusters.",
                len(canonical), len(aliases), len(clusters))

    # Step 1: Ingest ESCO skills
    if args.esco_csv:
        esco_skills = load_esco_csv(args.esco_csv)
    else:
        esco_skills = fetch_esco_skills_api()

    if esco_skills:
        canonical, aliases, new_count = enrich_canonical_and_aliases(canonical, aliases, esco_skills)
        logger.info("Added %d new canonical skills from ESCO.", new_count)
    else:
        logger.warning("No ESCO skills loaded. Proceeding with existing data for similarity/clustering.")

    # Step 2: Load SentenceTransformer
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        logger.error("sentence-transformers is required. pip install sentence-transformers")
        sys.exit(1)

    # Step 3: Add similarity-based aliases
    aliases = add_similarity_aliases(canonical, aliases, model, threshold=args.threshold)

    # Step 4: Re-cluster
    if not args.skip_clusters:
        clusters = recluster_skills(canonical, model, k=args.k)

    # Deduplicate canonical list
    seen: Set[str] = set()
    deduped: List[str] = []
    for s in canonical:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    canonical = deduped

    logger.info("Final data: %d canonical, %d aliases, %d clusters.",
                len(canonical), len(aliases), len(clusters))

    if args.dry_run:
        logger.info("Dry run — no files written.")
        return

    # Step 5: Save with backups
    save_json(CANONICAL_PATH, canonical)
    save_json(ALIASES_PATH, aliases)
    save_json(CLUSTERS_PATH, clusters)

    logger.info("Done. All skill data files updated.")


if __name__ == "__main__":
    main()
