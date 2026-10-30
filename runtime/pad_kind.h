#ifndef WOZMOD_PAD_KIND_H
#define WOZMOD_PAD_KIND_H

/* Whether a DirectInput device is an XInput pad, from its device path (DIPROP_GUIDANDPATH). Microsoft's test: the
   device ID of an XInput pad holds IG_, as in VID_045E&PID_028E&IG_00 ("XInput and DirectInput", learn.microsoft.com,
   whose sample says this cannot be found from DirectInput otherwise). The path is the HID interface's, made from that
   ID, and need not keep its case, so the mark matches in either. Wine's winexinput.sys gives the same mark to each pad
   it presents in the Xbox layout (winexinput.sys/main.c, "&IG_00"). Every XInput pad has the Xbox button order,
   whatever its name and button count: Windows named a player's Xbox One pad "Controller (Xbox One For Windows)", with
   16 buttons (issue 13). path holds at most size characters and ends at the first 0. */
static inline int pad_path_xinput(const unsigned short *path, unsigned int size)
{
    unsigned int i;
    for (i = 0; i + 2 < size && path[i] && path[i + 1] && path[i + 2]; i++)
        if ((path[i] | 0x20) == 'i' && (path[i + 1] | 0x20) == 'g' && path[i + 2] == '_')
            return 1;
    return 0;
}

#endif
