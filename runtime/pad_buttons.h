#ifndef WOZMOD_PAD_BUTTONS_H
#define WOZMOD_PAD_BUTTONS_H

/* The game takes a pad's buttons only from its DirectInput event buffer (0x5E4C90): 10 events between reads, shared
   with the sticks' axis events. Past that DirectInput drops events, so a lost release left a button held (a guard
   that never let go) and a lost press dropped a combo input. The state the same read returns is the pad's truth:
   held follows it, and a change the events missed becomes this frame's press or release, against the held the game
   kept from the frame before (was). The events' own press and release stay, so a tap shorter than a frame counts.
   One float per button, 1.0 or 0.0; state is DIJOYSTATE2's rgbButtons. */
static inline void pad_buttons_sync(float *held, float *press, float *release, const float *was,
                                    const volatile unsigned char *state, unsigned int count)
{
    unsigned int i;
    for (i = 0; i < count && i < 128; i++) {
        int down = state[i] >> 7;
        if (down && was[i] == 0.0f)
            press[i] = 1.0f;
        if (!down && was[i] != 0.0f)
            release[i] = 1.0f;
        held[i] = down ? 1.0f : 0.0f;
    }
}

#endif
