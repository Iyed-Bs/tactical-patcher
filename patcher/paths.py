"""Where the data lives.

Raw sources are large and stay outside the repo, in the folder named by the
PATCHER_DATA environment variable (default: ./data, ignored by git). The
warehouse built from them is a single DuckDB file in the same folder.
"""
import os
from pathlib import Path

DATA = Path(os.environ.get("PATCHER_DATA", "data"))
TRANSFERMARKT = DATA / "transfermarkt-datasets.duckdb"  # CC0, dcaribou/transfermarkt-datasets
FIFA = DATA / "fifa_male_players_legacy.csv"            # FIFA 15-23 ratings (sofifa scrape, HF mirror)
WYSCOUT = DATA / "wyscout"                               # Pappalardo et al. 2019, CC BY 4.0
CLUBELO = DATA / "clubelo"                               # api.clubelo.com yearly snapshots
WAREHOUSE = DATA / "warehouse.duckdb"
