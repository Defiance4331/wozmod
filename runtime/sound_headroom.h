#ifndef WOZMOD_SOUND_HEADROOM_H
#define WOZMOD_SOUND_HEADROOM_H

/* Every sound the game plays, a fixed amount quieter. The stock mix has no headroom: a big fight sums to +7 to +12 dB
   over full scale, and the output clips. Volumes here are DirectSound millibels (hundredths of a dB): 0 is the
   sound's own level, -10000 (DSBVOLUME_MIN) silence.
   13 dB: the loudest fight measured (stage 40, melee, +12.1 dB true peak) then peaks at about -1 dB, under full
   scale (work/handbacks/audio-clipping.md). The same trim on every sound keeps the balance between sounds and what
   the BGM, SE and Voice options do to it. The trim is the mix's headroom: the master stage (sound_master.h) brings the
   finished mix back up after the sum, where nothing clips.
   The hooks (english_runtime.HEADROOM) sit on the game's every call that sets a volume the player hears, after its
   own clamp to -10000..0: the effect buffers (0x67A009 in 0x679FD0), the streams for music and dialogue (0x677EB6 in
   0x677E90, and their first volume at 0x678F42 in 0x678BA0), and the movie's IBasicAudio put_Volume (0x5E9D24 in
   0x5E9A90, 0x5E9F81 in 0x5E9E90). */
#define HEADROOM_MB 1300

/* A stock volume, trimmed. The result stays at or above -10000: Wine rejects a lower value and the buffer keeps its
   previous, louder volume. Silence stays silence. */
static inline long headroom_trim(long mb)
{
    return mb - HEADROOM_MB < -10000 ? -10000 : mb - HEADROOM_MB;
}

#endif
