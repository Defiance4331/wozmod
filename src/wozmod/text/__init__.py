"""Build-time English text resolution."""

from .resolver import DonorResolver, layout_sample
from .build import build_table

__all__ = ["DonorResolver", "build_table", "layout_sample"]
