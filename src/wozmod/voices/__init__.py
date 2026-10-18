"""English dialogue and attack voice banks."""

from .build import VOICE_DONOR_FILES, build_voice_banks
from .maps import dialogue_voice_maps, wo1_voice_map, wo2_voice_map

__all__ = ["VOICE_DONOR_FILES", "build_voice_banks", "dialogue_voice_maps", "wo1_voice_map", "wo2_voice_map"]
