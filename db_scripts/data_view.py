"""Write the first rows of one raw GDELT export shard to sample.csv, for a quick look at the columns."""
from pathlib import Path

import pandas as pd

SHARD = Path(__file__).resolve().parents[1] / "data" / "gdelt_2024_na_000000000001.csv"

df = pd.read_csv(SHARD, sep="\t", nrows=5, header=None)
df.to_csv("sample.csv")
