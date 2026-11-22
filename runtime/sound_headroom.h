#ifndef WOZMOD_SOUND_HEADROOM_H
#define WOZMOD_SOUND_HEADROOM_H

/* Every sound the game plays, a fixed amount quieter, and dialogue a little less so. The stock mix has no headroom: a
   big fight sums to +7 to +12 dB over full scale, and the output clips. Volumes here are DirectSound millibels
   (hundredths of a dB): 0 is the sound's own level, -10000 (DSBVOLUME_MIN) silence.
   13 dB: the loudest fight measured (stage 40, melee, +12.1 dB true peak) then peaks at about -1 dB, under full
   scale (work/handbacks/audio-clipping.md). The same trim on every sound keeps the balance between sounds and what
   the BGM, SE and Voice options do to it. The trim is the mix's headroom: the master stage (sound_master.h) brings the
   finished mix back up after the sum, where nothing clips.
   The hooks (english_runtime.HEADROOM) sit on the game's every call that sets a volume the player hears, after its
   own clamp to -10000..0: the effect buffers (0x67A009 in 0x679FD0), the streams for music and dialogue (0x677EB6 in
   0x677E90, and their first volume at 0x678F42 in 0x678BA0), and the movie's IBasicAudio put_Volume (0x5E9D24 in
   0x5E9A90, 0x5E9F81 in 0x5E9E90). */
#define HEADROOM_MB 1300

/* Dialogue plays 3 dB over the stock mix against music and effects (runtime ledger row 44): a dialogue stream is
   trimmed 3 dB less, plus its clip's own gain (voice/Gains.bin: the WO2 PSP lines, wozmod.formats.audio.voice_gains).
   Raising the Voice option's ceiling instead (0x2EEE of 0x3FFF) leaves only 2.7 dB, and changes the stock gain path. */
#define DIALOGUE_RAISE_MB 300

/* A stock volume, trimmed, and raised by raise (dialogue_raise). Silence stays silence, and the result stays inside
   -10000..0: Wine rejects a value outside it, and the buffer keeps its previous volume. */
static inline long headroom_trim(long mb, long raise)
{
    long v = mb - HEADROOM_MB + raise;
    return mb <= -10000 || v < -10000 ? -10000 : v > 0 ? 0 : v;
}

/* Which stream is dialogue: what the stream is, not the volume it carries. The Ksl stream engine makes its streams in
   two places, one class (0x675070) for both:
   - the engine's own stream, at engine + 8 (the engine 0x672BE0, [[[0xE003C8] + 4] + 0xC]), which the BGM calls
     drive (0x5D4DF0 through 0x670440, its group gain from the BGM option through 0x670460): the music;
   - the channel streams (0x677AE0, from 0x674630 at channels + 4): an array at [channels + 0x20] of
     [channels + 0x24] elements of 0x588 bytes, element n's index n at +0 and its stream at +8. Only the voice frame
     (0x5D8590) starts one (0x5D8A00 and 0x5D8A80, through 0x670870 and 0x670880, are their only callers), for its
     buffer k on stream k (mask 1 << k: 0x674370 picks the record whose stream index, record + 8, is in the mask),
     with channels = [[voice manager + 4] + 0xC]: the dialogue.
   Every volume call passes the stream's player, stream + 0x390, as this (all eight calls of 0x677E90, and 0x679030 to
   0x678BA0), and both hook sites hold it in esi: so each volume a stream ever gets, its fades too (0x675BB0 at
   0x675D2E), tells its kind. The index of a channel stream from its player, or -1 for any other (the music). */
#define KSL_PLAYER 0x390u
#define KSL_CHANNELS_ARRAY 0x20u  /* in the channel object */
#define KSL_CHANNELS_COUNT 0x24u
#define KSL_CHANNEL_STREAM 8u     /* in an element */
#define KSL_CHANNEL_BYTES 0x588u
static inline int ksl_channel_index(unsigned long base, unsigned long count, unsigned long player)
{
    unsigned long at = player - KSL_PLAYER - KSL_CHANNEL_STREAM - base;
    return base && at < count * KSL_CHANNEL_BYTES && !(at % KSL_CHANNEL_BYTES) ? (int)(at / KSL_CHANNEL_BYTES) : -1;
}

/* The channel streams, as the voice frame starts its first (english_runtime: hook 161), and each voice buffer's clip
   gain: taken at its load (hook 36, where the clip's id in Voice.bin is known), and handed to the buffer's stream as
   the voice frame starts it. The stream's gain then holds until its next start, which comes only once it is quiet, so
   a fade-out keeps its own clip's gain while the buffer takes the next clip's load. */
struct dialogue_level {
    volatile unsigned long base, count;
    volatile short loaded[2], playing[2];
};

/* At a voice load into the buffer at record (the voice manager vm keeps two of 0x40 bytes from +0xC), its clip's gain. */
static inline void dialogue_load(struct dialogue_level *d, const unsigned char *vm, unsigned long record, int gain)
{
    unsigned long k = (record - (unsigned long)vm - 0xC) / 0x40;
    if (vm && k < 2 && record == (unsigned long)vm + 0xC + 0x40 * k)
        d->loaded[k] = (short)gain;
}

/* As the voice frame starts buffer k on channel stream k, the channel streams at base, count of them. */
static inline void dialogue_start(struct dialogue_level *d, unsigned long base, unsigned long count, unsigned long k)
{
    d->count = count, d->base = base;
    if (k < 2)
        d->playing[k] = d->loaded[k];
}

/* The raise for the stream playing from player: DIALOGUE_RAISE_MB and its clip's gain for dialogue, 0 for music. */
static inline long dialogue_raise(const struct dialogue_level *d, unsigned long player)
{
    int k = ksl_channel_index(d->base, d->count, player);
    return k < 0 ? 0 : DIALOGUE_RAISE_MB + (k < 2 ? d->playing[k] : 0);
}

#endif
