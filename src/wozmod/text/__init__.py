"""Build-time English text resolution. The table build is wozmod.text.build: importing it loads the DLL builder,
which the art build must not load (its cache key would change with every runtime edit)."""

from .resolver import DonorResolver, layout_sample

__all__ = ["DonorResolver", "layout_sample"]
