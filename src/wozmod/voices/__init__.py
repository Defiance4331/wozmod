"""English dialogue and attack voice banks."""

from .build import PSP_VOICE_FILES, VOICE_DONOR_FILES, build_voice_banks
from .maps import dialogue_voice_maps, psp_voice_map, wo1_voice_map, wo2_voice_map

__all__ = ["PSP_VOICE_FILES", "VOICE_DONOR_FILES", "build_voice_banks", "dialogue_voice_maps", "psp_voice_map", "wo1_voice_map", "wo2_voice_map"]
