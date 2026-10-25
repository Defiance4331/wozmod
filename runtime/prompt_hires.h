#ifndef WOZMOD_PROMPT_HIRES_H
#define WOZMOD_PROMPT_HIRES_H

/* prompts-<family>-hires.bin (wozmod.art.build.prompt_hires): u32 magic "PHR1", slot count, scale count, the scales,
   the slots, each slot's width in stock texels (every slot is 32 tall); then scale by scale, slot by slot, BGRA rows.
   Where slot's copy at scale starts in data, its width in texels in *w; 0 when the file has none or is cut short. */
#define PROMPT_HIRES_MAGIC 0x31524850u
#define PROMPT_HIRES_MOST 256 /* slots, and scales, a file may hold */

static inline unsigned int prompt_hires_find(const unsigned char *data, unsigned int size, unsigned int slot,
                                             unsigned int scale, unsigned int *w)
{
    const unsigned int *head = (const unsigned int *)data;
    unsigned int slots, scales, at, k, j;
    *w = 0;
    if (!data || size < 12 || head[0] != PROMPT_HIRES_MAGIC || (slots = head[1]) > PROMPT_HIRES_MOST
        || (scales = head[2]) > PROMPT_HIRES_MOST || 12 + 4 * (scales + 2 * slots) > size)
        return 0;
    for (k = 0, at = 12 + 4 * (scales + 2 * slots); k < scales; k++)
        for (j = 0; j < slots; j++) {
            unsigned int width = head[3 + scales + slots + j] * head[3 + k];
            if (head[3 + k] == scale && head[3 + scales + j] == slot) {
                if (at + width * 32 * scale * 4 > size)
                    return 0;
                *w = width;
                return at;
            }
            at += width * 32 * head[3 + k] * 4;
        }
    return 0;
}

#endif
