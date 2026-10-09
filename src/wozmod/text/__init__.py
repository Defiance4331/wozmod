"""Build-time English text resolution."""

from .resolver import DonorResolver, layout_sample
from .build import build_table
from .inputs import extract_text_program

__all__ = ["DonorResolver", "build_table", "extract_text_program", "layout_sample"]
