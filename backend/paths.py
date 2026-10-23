from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LITERATURE_DATA=ROOT/'data/literature'
LITERATURE_DATA.mkdir(parents=True,exist_ok=True)
