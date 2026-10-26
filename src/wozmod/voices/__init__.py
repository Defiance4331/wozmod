"""English dialogue and attack voice banks."""

from .build import PSP_VOICE_FILES, VOICE_DONOR_FILES, build_voice_banks
from .maps import (
    VoiceSlot, dialogue_voice_maps, lines_apart, psp_voice_map, slot_records, slot_table, stage_triggers, voice_slots,
)

__all__ = ["PSP_VOICE_FILES", "VOICE_DONOR_FILES", "VoiceSlot", "build_voice_banks", "dialogue_voice_maps", "lines_apart",
           "psp_voice_map", "slot_records", "slot_table", "stage_triggers", "voice_slots"]
