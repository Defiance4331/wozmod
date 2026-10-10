"""English dialogue and attack voice banks."""

from .build import VOICE_DONOR_FILES, VoiceBuildResult, build_voice_banks
from .maps import block_voice_map, dialogue_voice_maps, slot_voice_map, wo1_voice_map, wo2_voice_map

__all__ = [
    "VOICE_DONOR_FILES", "VoiceBuildResult", "build_voice_banks", "block_voice_map",
    "dialogue_voice_maps", "slot_voice_map", "wo1_voice_map", "wo2_voice_map",
]
