import os

from backend.config import CityConfig


def save_results(top_candidates, cfg: CityConfig, filename="top_sites.gpkg"):
    os.makedirs(cfg.processed_dir, exist_ok=True)
    path = os.path.join(cfg.processed_dir, filename)
    top_candidates.to_file(path, driver="GPKG")
    print(f"Results saved to {path}")


def get_top_candidates(scored_candidates, top_n=5):
    # Tie-break on total reachable population so all-zero scores stay deterministic.
    by = ["score", "score_total"] if "score_total" in scored_candidates.columns else "score"
    top = scored_candidates.sort_values(by, ascending=False).head(top_n).copy()
    top["rank"] = range(1, len(top) + 1)  # fewer candidates than top_n is valid
    if len(top) < top_n:
        print(f"\nTop {len(top)} candidate sites (only {len(top)} candidates "
              f"survived filtering; {top_n} requested):")
    else:
        print(f"\nTop {top_n} candidate sites:")
    for _, row in top.iterrows():
        print(f"  Rank {int(row['rank'])}: score={int(row['score'])} | coords=({row.geometry.y:.4f}, {row.geometry.x:.4f})")
    return top
