/* A format whose English names the game's arguments in another order than the Japanese: the text build writes each
   placeholder with the number of the argument it prints (%2$d prints the second; wozmod.text.build
   _in_argument_order). The game's sprintf reads placeholders only in turn, so before it runs, the format loses its
   numbers and the arguments move into the order the English prints them. */

/* Into out (size bytes): in without argument numbers, and args (count of them) in the order in prints them.
   Returns 0, changing nothing, for a format without numbers or one that does not fit. */
static int format_order(const char *in, char *out, unsigned int size, unsigned int *args, unsigned int count)
{
    unsigned int moved[16], used = 0, i, n;
    char *o = out, *end = out + size - 1;
    while (*in) {
        if (o + 1 >= end)
            return 0;
        if (in[0] == '%' && in[1] == '%') {
            *o++ = *in++, *o++ = *in++;
            continue;
        }
        if (in[0] == '%') {
            const char *p = in + 1;
            for (n = 0; *p >= '0' && *p <= '9' && n < 1000; p++)
                n = n * 10 + (unsigned int)(*p - '0');
            if (*p != '$')
                return 0;
            if (n == 0 || n > count || used == sizeof moved / sizeof *moved)
                return 0;
            moved[used++] = args[n - 1];
            *o++ = '%';
            in = p + 1;
            continue;
        }
        *o++ = *in++;
    }
    if (!used)
        return 0;
    *o = 0;
    for (i = 0; i < used; i++)
        args[i] = moved[i];
    return 1;
}
