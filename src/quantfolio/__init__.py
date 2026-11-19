"""Quantfolio research, with deterministic numerical settings for frozen V6 replay."""

import pandas as pd

# V6 was validated with NumPy evaluation. Preserve this setting: the original
# Windows runtime produced inconsistent feature masks when numexpr was enabled.
pd.set_option("compute.use_numexpr", False)

__version__ = "0.6.0"
