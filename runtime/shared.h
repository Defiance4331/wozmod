/* The defines the installer's build steps read as well as the DLL (wozmod.runtime.shared parses this file).
 *
 * Only these are part of the install steps' keys (wozmod.install.step_keys): a change anywhere else in the runtime
 * leaves every installed step in place. Move a define here when a build step starts reading it. */

#ifndef WOZMOD_SHARED_H
#define WOZMOD_SHARED_H

/* Text draw return addresses: the page boxes (wozmod.runtime.text_boxes) and the text inventory's draw sites. */
#define NARRATION_DRAW 0x561111
#define STILL_DRAW 0x59C7BD
#define BRIEFING_DRAW 0x548F52
#define NARRATION_Z_DRAW 0x55F31C
#define STILL_Z_DRAW 0x59A785
/* The cinematic cue read: the captions' draw site. */
#define CAPTION_CUE 0x56A23F

/* hires.bin: the row flags and the limits past which the runtime refuses the whole bank (wozmod.art.hires). */
#define HIRES_MAX 256
#define HIRES_SHEETS 32
#define HIRES_BANKS 4
#define HIRES_SCALE_MIN 2      /* a packing's texels per stock texel */
#define HIRES_SCALE_MAX 7
#define HIRES_LEVELS 3         /* most levels in a packing or a sheet */
#define HIRES_SIDE_MIN_LOG2 3  /* a sheet's sides: 8 to 4096 texels */
#define HIRES_SIDE_MAX_LOG2 12
#define HIRES_OVERLAY 0x80000000u
#define HIRES_RIGHT_GROW 0x40000000u
#define HIRES_SCALE_MASK 0x3fffffffu

#endif
