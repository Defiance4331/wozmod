#ifndef WOZMOD_VOLUME_CURVE_H
#define WOZMOD_VOLUME_CURVE_H

/* The BGM, SE and Voice options (the bytes at 0x80151B, 0x80151C, 0x80151D: levels 0 to 15), as decibels. Stock
   turns a level L into the gain L/15 of the category's ceiling, a straight line in amplitude: the top half of the
   slider moved the sound 5.5 dB in all, and level 1 was still -23.5 dB. Each level is now 3 dB under the next:
   level 15 is the stock full level (0 dB), level L is -3 * (15 - L) dB, level 1 is -42 dB, level 0 is silent. 3 dB
   is the owner's choice: with the 13 dB trim after it (sound_headroom.h) level 1 plays near -62 LUFS, faint but
   heard.
   The game also passes its setters a level under the option to duck a category (music 3L/4 in an event scene, music L/2
   on some screens; a stage line's duck is a fixed depth, stage_duck.h). So the curve scales the gain the game computed,
   stock ceiling, stock duck and stock rounding included, by curve gain over stock gain at the option's level: every
   duck keeps its stock ratio at every slider position, and level 15 is stock bit for bit. The hooks
   (english_runtime.VOLUME) sit where each of the six sites holds its stock gain (14-bit, 0x3FFF = 1.0) in edx.

   volume_scale[L] = 65536 * 10^(-3 * (15 - L) / 20) / (L / 15), rounded; 0 for level 0. Each is within 0.001 dB of
   its step, and 65536 at level 15 keeps the stock gain exactly. */
#define VOLUME_LEVELS 16
static const unsigned volume_scale[VOLUME_LEVELS] = {
    0, 7809, 5515, 5193, 5502, 6217, 7318, 8861, 10952, 13751, 17481, 22448, 29066, 37899, 49710, 65536,
};

/* The gain a site sets: ``stock``, what the game computed for this call (at most 0x3FFF), on the curve of the
   option at ``level``. A level the sliders cannot set (a damaged save) keeps the stock gain, as it would in stock. */
static inline unsigned volume_scaled(unsigned stock, unsigned level)
{
    return level < VOLUME_LEVELS ? (stock * volume_scale[level] + 0x8000) >> 16 : stock;
}

#endif
