
















#include <windows.h>
#include "text_fit.h"

typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;



struct site {
    u32 va;
    u16 pops;
    u8 id, flags, len, prologue[15];
};

#include "enrt_data.h"




static const u32 *enrt_table;


#define GAME_DRAW ((void (*)(const char *, u32, u32, u32, u32))0x54B7D0)
#define GAME_MEASURE ((u32 (*)(const char *, u32 *, u32 *))0x54BF80)
#define NAME_PART(i) (*(const char *const *)(0x6F6368 + 8 * (i)))
#define MESSAGE(i) (*(const u32 *)(0x6DE4F0 + 4 * (i)))
#define FONT_CELL ((volatile u8 *)0xC2AE0C)
#ifndef ENRT_CELL
#define ENRT_CELL 24
#endif

#define PROMPT_DRAW 0x55DFD8
#define PROMPT_TEXT ((const char *)0xC2F1B8)
#define MESSAGE_CELL ((volatile short *)0xC2AF36)
#define MESSAGE_X (*(volatile short *)0xC2AF3A)
#define MESSAGE_WIDTH (*(volatile short *)0xC2AF40)
#define MESSAGE_HEIGHT (*(volatile short *)0xC2AF42)
#define NOTICE_BOX_WIDTH (*(volatile short *)0xC2B088)
#define NAME_FORMAT_TITLED 0x7042C8
#define NAME_FORMAT_PLAIN 0x7042E0
#define HISTORY_DRAW 0x539F9F
#define HISTORY_WIDTH 756
#define WINDOWS 0xC2CF68
#define WINDOW_SIZE 0x128
#define WINDOW_MEASURE 0x54E807
#define WINDOW_PITCH 24

#define NARRATION_DRAW 0x561111
#define STILL_DRAW 0x59C7BD
#define STILL_PLATE 0x59C4B3
/* Z-story still name plate: the stock 406x70 plates (4102.0/1); their dark box is 183 wide over the name
   rows, centred where the stock centres the name by its Japanese glyph count (28 px each). */
#define STILL_Z_PLATE 0x59A3E2
#define STILL_Z_LANE 180
#define NAME_GLYPHS ((u32 (*)(u32))0x54A0A0)
#define BRIEFING_DRAW 0x548F52
/* Z-story chapter narration and still event: twins of the screens above, with their glyph counts. */
#define NARRATION_Z_DRAW 0x55F31C
#define STILL_Z_DRAW 0x59A785
#define NARRATION_Z_COUNT 0x55E724
#define STILL_Z_COUNTS 0x5990EE, 0x5995D9

#define NARRATION_CODE 0x560870
#define NARRATION_CODE_END 0x561900
#define STILL_CODE 0x59B000
#define STILL_CODE_END 0x59D000
#define BRIEFING_COUNT 0x548C7B
#define SCENARIO_TEXT_DRAW 0x588C17
/* Scenario text in the stock column at one smaller cell in every box, 5 lines, condensed only past them. */
#define SCENARIO_TEXT_WIDTH 290
#define SCENARIO_TEXT_CELL 20
#define SCENARIO_TEXT_PITCH 23
#define SCENARIO_TEXT_LINES 5
/* Dream summary at the stock cell and pitch in its column, at most 7 lines (clear of the help bar). */
#define DREAM_SUMMARY_PITCH 28
#define DREAM_SUMMARY_LINES 7
#define DREAM_SUMMARY_DRAW 0x547673
#define DREAM_SUMMARY_WIDTH 240
#define DREAM_SUMMARY_Y 408
/* Scenario army line (X360 0x822CFA48): names at cell 15 there (ours 18, an edit) from box+0x23, 34 px
   between names (gap 3 and the icon advance 31), RECORD rows from box+0x222. Names wider than the room
   are condensed together; the icons and gaps keep their width. */
#define ARMY_NAME_DRAW 0x588AC8
#define ARMY_NAME_ADVANCE 0x588AD9
#define ARMY_NAMES 0x6FB7C0
#define ARMY_GAP 34
#define ARMY_ROOM (0x380 - 2 * 0x24)

#define WEAPON_READING_DRAW 0x55BCFC
#define WEAPON_INFO_READING_DRAW 0x59536D
#define GAME_QUAD ((void (*)(u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32))0x5DDAB0)
#define GAME_FONT ((void (*)(u32, u32, u32))0x54C350)
#define FONT_MODE (*(volatile u32 *)0xC2AE00)
#define FONT_COLOUR (*(volatile u32 *)0xC2ADEC)
#define FONT_ALPHA (*(volatile u8 *)0xC2AE08)
#define SCREEN_W (*(volatile u32 *)0x7A4574)
#define TEXT_DEPTH (*(volatile u32 *)0xC2AE04)
#define SCREEN_H (*(volatile u32 *)0x7A4578)
#define RESOLUTION (*(volatile u8 *)0x80152D)
#define MODE_1080P 9

#define RESOLUTION_RAISED (*(volatile u8 *)0x801532)


#define BANNER_TEXT_DRAW 0x55083E
/* Pre-battle army list title: outline passes and face, centred on its plate. */
#define ARMY_TITLE_OUTLINE 0x536742
#define ARMY_TITLE 0x536785
#define BANNER_NAME_DRAW 0x550874
#define BANNER_TEXT_CELL 22
#define CAPTION_CUE 0x56A23F
#define CAPTION_WIDTH 1200



#define PAD_PROMPTS (*(volatile u8 *)0x1413FEC)


static u32 inner;
/* Window wrap in 24-cell pixels and drawn width (the stock box widths); the window is sized to the drawn width. */
static const u32 windows[2][2] = {{720, 720}, {1224, 1224}};
static const int squish = 1;
static struct text_fit active_fit = {1.0, 0.0};
static double fit_anchor;
static u32 fit_clean;
static u32 caption_run_width;
static u32 banner_width;

static u32 fnv(const u8 *s)
{
    u32 h = 2166136261u;
    while (*s)
        h = (h ^ *s++) * 16777619u;
    return h;
}

static int same(const u8 *a, const u8 *b)
{
    while (*a && *a == *b)
        a++, b++;
    return *a == *b;
}

static u32 len(const char *s)
{
    u32 n = 0;
    while (s[n])
        n++;
    return n;
}

static char *copy(char *d, const char *s)
{
    while ((*d = *s++))
        d++;
    return d;
}

const char *enrt_translate(const char *s)
{
    const u8 *base = (const u8 *)enrt_table;
    u32 mask = enrt_table[0], h = fnv((const u8 *)s), i = h;
    for (;; i++) {
        const u32 *slot = &enrt_table[2 + 3 * (i & mask)];
        if (!slot[1])
            return s;
        if (slot[0] == h && same(base + slot[1], (const u8 *)s)) {
            /* Donor text can carry console icon codes that the stock draw crashes on. */
            static char safe[4][512];
            static u32 next;
            const char *en = (const char *)(base + slot[2]);
            const char *kept = text_drop_bad_icons(en, safe[next & 3], sizeof safe[0]);
            next += kept != en;
            return kept;
        }
    }
}


struct frame {
    u32 edi, esi, ebp, esp, ebx, edx, ecx, eax, ret, arg[];
};

static char *put_hex(char *p, u32 v, int digits)
{
    static const char hex[] = "0123456789abcdef";
    while (digits--)
        *p++ = hex[v >> 4 * digits & 15];
    return p;
}

#ifdef ENRT_LOG
static void *log_file = (void *)-1;
static volatile long log_lock;
static u32 seen[1 << 16];


static int log_begin(u32 h)
{
    u32 i;
    if (!h)
        h = 1;
    while (InterlockedExchange(&log_lock, 1))
        ;
    for (i = h;; i++) {
        u32 *slot = &seen[i & 0xffff];
        if (*slot == h)
            return 0;
        if (!*slot) {
            *slot = h;
            break;
        }
    }
    if (log_file == (void *)-1)
        log_file = CreateFileA("enrt_log.txt", 0x40000000, 1, 0, 2, 0x80, 0);
    return 1;
}

static void log_end(const char *line, const char *end)
{
    DWORD written;
    if (line != end)
        WriteFile(log_file, line, end - line, &written, 0);
    InterlockedExchange(&log_lock, 0);
}



static void log_line(u32 id, const struct frame *f, const u8 *s, u32 key)
{
    char line[64 + 2 * 512 + 2], *p = line;
    u32 i;
    if (log_begin((key + id * 0x9e3779b1u) ^ f->ret)) {
        p = put_hex(p, id, 2);
        for (i = 0; i < 5; i++) {
            *p++ = ' ';
            p = put_hex(p, i ? f->arg[i - 1] : f->ret, 8);
        }
        *p++ = ' ';
        p = put_hex(p, *(const unsigned short *)0xc2ae0c, 4);
        *p++ = ' ';
        for (i = 0; s[i] && i < 512; i++)
            p = put_hex(p, s[i], 2);
        *p++ = '\n';
    }
    log_end(line, p);
}



static u32 texture(u32 handle);

static void log_quad(u32 id, const struct frame *f)
{
    char line[16 * 14], *p = line;
    u32 handle = f->arg[0], name, h = f->ret ^ handle, i;
    for (i = 5; i < 11; i++)
        h = (h ^ f->arg[i]) * 0x01000193u;
    if (log_begin(h ^ id)) {
        name = texture(handle);
        p = put_hex(p, id, 2);
        *p++ = ' ';
        p = put_hex(p, f->ret, 8);
        *p++ = ' ';
        p = put_hex(p, name, 8);
        for (i = 2; i < 13; i++) {
            *p++ = ' ';
            p = put_hex(p, f->arg[i], 8);
        }
        *p++ = '\n';
    }
    log_end(line, p);
}
#endif


static const u32 roots[][3] = {
    {0xc268e0, 85, 4060}, {0xc275d0, 23, 4061}, {0x89ad54, 1, 4068}, {0xc2fc94, 31, 4069},
    {0xc2e778, 60, 4064}, {0xc54afc, 277, 4077}, {0xc54c38, 255, 4079}, {0xc5d488, 59, 4080},
    {0xc5d4c0, 45, 4081}, {0xc5d554, 32, 4082}, {0xc5d608, 168, 4085}, {0xc275e8, 59, 4094},
    {0xc275ec, 96, 4095}, {0xc5d654, 17, 4098}, {0xc54ad8, 150, 4074}, {0xc5d620, 114, 4099},
};

#if defined(ENRT_LOG) || defined(ENRT_TEST)

static u32 texture(u32 handle)
{
    u32 i, j;
    for (i = 0; i < sizeof roots / sizeof *roots; i++) {
        const u32 *table = *(const u32 *const *)roots[i][0];
        for (j = 0; table && j < roots[i][1]; j++)
            if (table[2 * j] == handle)
                return roots[i][2] << 16 | j;
    }
    return handle;
}
#endif

#ifdef ENRT_TEST




#define TEST_VERSION 2
#define TEST_TEXTS 256
#define TEST_QUADS 2048
#define TEST_SETS 16
#define TEST_CHARS 124
#define WINDOW_SET_TEXT ((void (*)(u32, const char *, u32))0x54E780)
#define WINDOW_OPEN ((void (*)(u32))0x551970)
#define WINDOW_STYLE ((void (*)(u32))0x54D8F0)


#define SCRIPTS (*(u8 *const volatile *)0x9C919C)
#define SCRIPT_COUNT (*(volatile u32 *)0x9C91A0)
#define SCRIPT_STATE(i) ((*(u8 *const *const volatile *)0x9C91AC)[i])
#define BATTLE_FLAGS (*(volatile u32 *)0x801AA8)
#define QUEUE_SCRIPT ((u32 (*)(u32, u32, u32, u32, u32))0x4EEE60)


#define RUNNING ((volatile u8 *)0x9C9640)
#define STAGE_DATA (*(volatile u32 *)0x9C8FD4)
#define STAGE (*(volatile u32 *)0x801AA0)
#define STAGE_MAP (*(volatile u32 *)0x801AA4)
#define STAGE_TABLE (*(const u8 *const volatile *)0x9C8FC4)
#define OFFICERS 0x60
#define GAIN_EXP ((u32 (*)(u32, u32))0x47A410)
#define GIVE_WEAPON ((void (*)(u32, u32, const u32 *))0x475980)
#define MAX_EXP 220000


struct test_text {
    u32 hook, caller, x, y, width;
    char s[TEST_CHARS];
};


struct test_quad {
    u32 hook, caller, handle, name, x, y, width, height;
};


struct test_report {
    u32 frame, texts, quads;
    struct test_text text[TEST_TEXTS];
    struct test_quad quad[TEST_QUADS];
};

static struct test_block {
    char magic[16];
    u32 version, size, frame;
    volatile u32 command, done, op, arg, result;
    char input[1024];
    u32 sets;
    struct test_text set[TEST_SETS];
    struct test_report snapshot;
    volatile u32 reloads;
} enrt_test = {"ENRT-TEST-BLOCK", TEST_VERSION, sizeof(struct test_block)};

static struct test_report reports[2], *current = reports, *last = reports + 1;
static u32 next_stage;

static void words(void *dst, const void *src, u32 bytes)
{
    u32 *d = dst;
    const u32 *s = src;
    for (bytes /= 4; bytes--;)
        *d++ = *s++;
}

static void test_put(struct test_text *t, u32 hook, u32 caller, u32 x, u32 y, u32 width, const char *s)
{
    u32 i;
    t->hook = hook, t->caller = caller, t->x = x, t->y = y, t->width = width;
    for (i = 0; s && s[i] && i < TEST_CHARS - 1; i++)
        t->s[i] = s[i];
    t->s[i] = 0;
}

static void test_text(u32 hook, const struct frame *f, const char *s, u32 x, u32 y, u32 width)
{
    if (inner && active_fit.factor != 1.0) /* a squished draw: report the extent it is drawn at */
        x += (u32)(int)(active_fit.shift + 0.5), width = (u32)(width * active_fit.factor + 0.5);
    if (current->texts < TEST_TEXTS)
        test_put(&current->text[current->texts], hook, f->ret, x, y, width, s);
    current->texts++;
}

static void test_set(u32 hook, const struct frame *f, u32 which, const char *s)
{
    test_put(&enrt_test.set[enrt_test.sets++ % TEST_SETS], hook, f->ret, which, enrt_test.frame, 0, s);
}

static void test_quad(u32 hook, const struct frame *f)
{
    if (current->quads < TEST_QUADS) {
        struct test_quad *q = &current->quad[current->quads];
        q->hook = hook, q->caller = f->ret, q->handle = f->arg[0];
        q->x = f->arg[2], q->y = f->arg[3], q->width = f->arg[5], q->height = f->arg[6];
    }
    current->quads++;
}

static u32 test_review_set(u32 name, const u32 *rect);


static void test_frame(void)
{
    struct test_block *b = &enrt_test;
    struct test_report *r = &b->snapshot;
    u32 i;
    b->frame++;
    if (current->texts || current->quads) {
        struct test_report *done = current;
        current = last, last = done;
        last->frame = b->frame;
    }
    current->texts = current->quads = 0;
    if (b->command == b->done)
        return;
    b->result = 0;
    switch (b->op) {
    case 1:
        words(r, last, 12);
        words(r->text, last->text, sizeof *r->text * (last->texts < TEST_TEXTS ? last->texts : TEST_TEXTS));
        for (i = 0; i < last->quads && i < TEST_QUADS; i++) {
            words(&r->quad[i], &last->quad[i], sizeof *r->quad);
            r->quad[i].name = texture(r->quad[i].handle);
            if (r->quad[i].name == r->quad[i].handle)
                r->quad[i].name = 0;
        }
        break;
    case 2:
        b->input[sizeof b->input - 1] = 0;
        if (b->arg != 1 && b->arg != 2) {
            b->result = 1;
            break;
        }
        WINDOW_SET_TEXT(b->arg, b->input, 0);
        WINDOW_OPEN(b->arg);
        WINDOW_STYLE(b->arg);
        break;
    case 3:

    case 4:
        b->result = 3;
        if (!STAGE_DATA || BATTLE_FLAGS & 0x2000000)
            break;
        b->result = 5;
        for (i = 0; i < SCRIPT_COUNT && i < 0x100; i++) {
            u8 *script = SCRIPTS + ((const u32 *)(SCRIPTS + 0xC))[i];

            if ((b->op == 4 ? i == b->arg && script[5] : script[1] == b->arg) && !script[2] && !script[3]) {
                b->result = 4;
                if (b->op == 3) {
                    script[0] |= 1;
                    SCRIPT_STATE(i)[1] = 3;
                    if (QUEUE_SCRIPT(0, i, 3, script[1], 0))
                        b->result = 0;
                } else if (!(RUNNING[0] & 0x39)) {


                    script[0] |= 3;
                    RUNNING[1] = 3;
                    RUNNING[2] = i;
                    RUNNING[3] = 0;
                    RUNNING[0] = 5;
                    b->result = 0;
                }
                break;
            }
        }
        break;
    case 5:

        for (i = 0; i < OFFICERS; i++) {
            u32 tier, weapon[6] = {0, 0x10000};
            GAIN_EXP(i, MAX_EXP);
            for (tier = 0; tier < 4; tier++) {
                weapon[0] = i * 4 + tier;
                GIVE_WEAPON(i, tier, weapon);
            }
        }
        break;
    case 7:
        b->result = test_review_set(b->arg, (const u32 *)b->input);
        break;
    case 6:
        next_stage = b->arg + 1;
        break;
    default:
        b->result = 2;
    }
    b->done = b->command;
}
#endif

static u32 text(u32 s, u32 id, const struct frame *f)
{
    u32 en;
    if (inner || !s || !*(const u8 *)s)
        return s;
    en = (u32)enrt_translate((const char *)s);
#ifdef ENRT_LOG

    log_line(id | (en != s) << 7, f, (const u8 *)s, fnv((const u8 *)s));
#endif
    return en;
}




static u32 voiced_line(u32 line, u32 id, const struct frame *f)
{
    static char out[256];
    const char *s = (const char *)line, *at = 0, *p, *en;
    if (!s || !s[0] || !s[1])
        return line;
    for (p = s + 2; *p; p++)
        if (*p == '@')
            at = p;
    if (!at)
        return line;
    en = (const char *)text(line + 2, id, f);
    if (en == s + 2 || 2 + len(en) + len(at) >= sizeof out)
        return line;
    out[0] = s[0], out[1] = s[1];
    copy(copy(out + 2, en), at);
    return (u32)out;
}


static void translate_args(const u8 *p, u32 *args, u32 id, const struct frame *f)
{
    while (*p) {
        if (*p++ != '%')
            continue;
        if (*p == '%') {
            p++;
            continue;
        }
        while (*p == '-' || *p == '+' || *p == ' ' || *p == '#' || *p == '0')
            p++;
        for (; *p == '*' || *p == '.' || (*p >= '0' && *p <= '9'); p++)
            if (*p == '*')
                args++;
        int wide = 0;
        if (p[0] == 'I' && p[1] == '6' && p[2] == '4')
            wide = 1, p += 3;
        else if (p[0] == 'l' && p[1] == 'l')
            wide = 1, p += 2;
        else if (*p == 'l' || *p == 'h')
            p++;
        switch (*p ? *p++ : 0) {
        case 's':
            *args = text(*args, id, f);
            break;
        case 'e': case 'E': case 'f': case 'g': case 'G':
            wide = 1;
            break;
        }
        args += 1 + wide;
    }
}

static u32 measure(const char *s)
{
    u32 w;
    inner++;
    w = GAME_MEASURE(s, 0, 0);
    inner--;
    return w;
}


static u32 width24(const char *s)
{
    u8 w = FONT_CELL[0], h = FONT_CELL[1];
    u32 width;
    FONT_CELL[0] = FONT_CELL[1] = 24;
    width = measure(s);
    FONT_CELL[0] = w, FONT_CELL[1] = h;
    return width;
}

typedef void (*text_draw)(const char *, u32, u32, u32, u32);

static void draw_fit_at(text_draw renderer, const char *s, u32 x, u32 y, u32 layer, u32 count, u32 width, u32 align, u32 run_width)
{
    struct text_fit saved = active_fit;
    double anchor = fit_anchor;
    u32 own = measure(s), clean = fit_clean;
    active_fit = text_fit(run_width ? run_width : own, own, width, align, squish && text_fit_latin(s));
    fit_anchor = (double)(int)x * SCREEN_W * 16 / 1280 + 32768;
    fit_clean = 0;
    inner++;
    renderer(s, x, y, layer, count);
    inner--;
    active_fit = saved, fit_anchor = anchor, fit_clean = clean;
}

static void draw_fit(const char *s, u32 x, u32 y, u32 layer, u32 count, u32 width, u32 align)
{
    draw_fit_at(GAME_DRAW, s, x, y, layer, count, width, align, 0);
}

/* Return addresses of the glyph finalizer calls (stock draw and each screen's glyph callback) and the
   register holding the glyph there: 0 esi, 1 edi. A call missing here draws its text unsquished. */
static const u32 glyph_sites[][2] = {
    {0x54A704, 0}, {0x54A8FF, 0}, {0x54AB57, 1}, {0x54ADC8, 0}, {0x54AF9D, 0}, {0x54B1A2, 0},
    {0x54B34F, 0}, {0x54B5B8, 0}, {0x54B77E, 0}, {0x561054, 0}, {0x59C6E2, 0}, {0x548ECF, 0},
    {0x54F2E6, 0}, {0x549F3E, 0}, {0x550DD9, 1}, {0x55F0BE, 1}, {0x55F293, 1}, {0x59A105, 1},
    {0x59A6C8, 0},
};

static void glyph_fit(u32 id, const struct frame *f)
{
    u32 i, *quad = (u32 *)f->arg[0];
    if (active_fit.factor == 1.0)
        return;
    for (i = 0; i < sizeof glyph_sites / sizeof *glyph_sites; i++)
        if (f->ret == glyph_sites[i][0]) {
            const u32 *glyph = (const u32 *)(glyph_sites[i][1] ? f->edi : f->esi);
            double pixels = (double)SCREEN_W * 16 / 1280;
            double pen = (double)(int)glyph[3] * pixels + 32768;
            text_fit_quad(quad, id == 69, fit_anchor, active_fit, pixels, pen, &fit_clean);
            break;
        }
}

static void draw(const char *s, u32 x, u32 y, u32 layer)
{
    inner++;
    GAME_DRAW(s, x, y, layer, 0);
    inner--;
}





static const char *wrap(const char *s, u32 width, char *out, u32 size)
{
    char *o = out, *line = out, *space = 0;
    if (!s || !*s || len(s) >= size)
        return s;
    while (*s) {
        u8 c = *s;
        char *at = o;
        *o++ = *s++;
        if ((c >= 0x81 && c <= 0x9f) || c >= 0xe0)
            if (*s)
                *o++ = *s++;

        if (c == '[' && s[0] && s[1])
            *o++ = *s++, *o++ = *s++;
        *o = 0;
        if (c == '\n')
            line = o, space = 0;
        else if (c == ' ')
            space = at > line ? at : 0;
        else if (space && measure(line) >= width)
            *space = '\n', line = space + 1, space = 0;
    }
    return out;
}

#define NAMES ((const u32 *)((const u8 *)enrt_table + enrt_table[1]))

static const char *part(u32 i)
{
    return i < NAMES[0] && NAMES[1 + i] ? (const char *)enrt_table + NAMES[1 + i] : NAME_PART(i);
}


static u32 part_index(u32 p)
{
    u32 i;
    for (i = 0; i < NAMES[0]; i++)
        if ((u32)NAME_PART(i) == p)
            return i;
    return -1;
}


static int prefix(const char *s)
{
    u32 n = len(s);
    return n && s[n - 1] == ' ';
}



static char *compose(char *dst, u32 surname, u32 given)
{
    const char *a = part(surname), *b = part(given);
    if (surname == given)
        copy(dst, a);
    else if (prefix(a))
        copy(copy(dst, a), b);
    else {
        char *d = copy(dst, b);
        *d++ = ' ';
        copy(d, a);
    }
    return dst;
}


static u32 name_cells(u32 surname, u32 given)
{
    char name[96];
    return (width24(compose(name, surname, given)) + 23) / 24;
}

static char wrapped[512], window_text[256], message_text[256], printed_name[96], line_text[512], flat_text[512];


static const u32 boxed[][2] = {
    {0x49DACE, 1152}, {0x49DB4C, 1152},
    {0x53C225, 578}, {0x53C2A5, 578},
    {0x5A686C, 705}, {0x580876, 705},
    {0x5530FC, 416},
    {0x526BE8, 641},
    {0x595679, 413}, {0x5956FA, 413},
    {0x598125, 604}, {0x59818B, 604},
    {0x593E56, 480}, {0x593EAE, 480},
};

/* X360 wrap width in 24-cell pixels where it differs from the box; the draw squishes into the box. */
static const u32 reflow[][2] = {
    {0x595F93, 589}, {0x5975C6, 589}, {0x593B98, 762},
    {0x595679, 516}, {0x5956FA, 516},
    {0x53C225, 680}, {0x53C2A5, 680},
    {0x526BE8, 733},
    {0x539F9F, 840},
};

static const char *reflow_wrap(u32 caller, const char *s, u32 box, char *out, u32 size)
{
    u32 i;
    for (i = 0; i < sizeof reflow / sizeof *reflow; i++)
        if (caller == reflow[i][0])
            box = reflow[i][1];
    return wrap(s, box, out, size);
}






/* MOZ message box: widened in 24 px steps (centred) until the wrapped text fits its height, up to 1120. */
static void fit_message(const char *s, u32 extra)
{
    static short stock_w, stock_x, grown_w, grown_x;
    u8 w = FONT_CELL[0], h = FONT_CELL[1];
    u32 tw, th;
    if (MESSAGE_WIDTH != grown_w || MESSAGE_X != grown_x)
        stock_w = MESSAGE_WIDTH, stock_x = MESSAGE_X;
    MESSAGE_WIDTH = stock_w, MESSAGE_X = stock_x;
    FONT_CELL[0] = (u8)MESSAGE_CELL[0], FONT_CELL[1] = (u8)MESSAGE_CELL[1];
    for (;;) {
        inner++;
        GAME_MEASURE(wrap(s, MESSAGE_WIDTH, wrapped, sizeof wrapped), &tw, &th);
        inner--;
        if (th + extra <= (u32)MESSAGE_HEIGHT || MESSAGE_WIDTH >= 1120)
            break;
        MESSAGE_WIDTH += 24, MESSAGE_X -= 12;
    }
    grown_w = MESSAGE_WIDTH, grown_x = MESSAGE_X;
    FONT_CELL[0] = w, FONT_CELL[1] = h;
}

static const char *flat(const char *s)
{
    char *p;
    if (!s || len(s) >= sizeof flat_text)
        return s;
    copy(flat_text, s);
    for (p = flat_text; *p; p++)
        if (*p == '\n')
            *p = ' ';
    return flat_text;
}



/* Page cell (w, h in 24-cell units), pitch, origin, wrap at that cell and box. Cell 0 keeps the stock
   cell and pitch and centres the lines on y (the MOZ narration). */
static const u32 pages[][8] = {
    {NARRATION_DRAW, 0, 0, 0, 160, 535, 960, 960},
    {STILL_DRAW, 25, 21, 28, 150, 531, 972, 980},
    {NARRATION_Z_DRAW, 24, 25, 35, 200, 507, 742, 757},
    {STILL_Z_DRAW, 25, 21, 28, 150, 531, 972, 980},
    {BRIEFING_DRAW, 24, 24, 34, 248, 486, 784, 784},
};
/* Pages that wrap wider, up to this many lines, and condense back into their box. */
static const u32 page_lines[][2] = {{BRIEFING_DRAW, 4}};

/* Wrap at width, widened in 4 px steps (up to twice the width) until the text takes at most `lines`
   lines; the draw then condenses it back into width. */
static const char *wrap_lines(const char *s, u32 width, u32 lines)
{
    const char *w = s, *p;
    u32 at, n;
    for (at = width; at < 2 * width; at += 4) {
        w = wrap(s, at, line_text, sizeof line_text);
        for (n = 1, p = w; p && *p; p++)
            n += *p == '\n';
        if (n <= lines)
            break;
    }
    return w;
}

static u32 page(struct frame *f, const u32 *site)
{
    u8 w = FONT_CELL[0], h = FONT_CELL[1], pitch = FONT_CELL[2];
    const char *s, *p;
    u32 i, lines = 0, y = site[5];
    if (!f->arg[0])
        return 0;
    if (site[1])
        FONT_CELL[0] = (u8)site[1], FONT_CELL[1] = (u8)site[2], FONT_CELL[2] = (u8)site[3];
    for (i = 0; i < sizeof page_lines / sizeof *page_lines; i++)
        if (site[0] == page_lines[i][0])
            lines = page_lines[i][1];
    s = flat((const char *)f->arg[0]);
    s = lines ? wrap_lines(s, site[6], lines) : wrap(s, site[6], line_text, sizeof line_text);
    if (!site[1]) {
        for (lines = 1, p = s; *p; p++)
            lines += *p == '\n';
        y -= (FONT_CELL[1] + (lines - 1) * FONT_CELL[2]) / 2;
    }
#ifdef ENRT_TEST
    test_text(1, f, s, site[4], y, measure(s));
#endif
    draw_fit(s, site[4], y, f->arg[3], f->arg[4], site[7], 0);
    FONT_CELL[0] = w, FONT_CELL[1] = h, FONT_CELL[2] = pitch;
    return 1;
}



static const char *history(const char *s)
{
    static char out[512];
    const char *en;
    char *p;
    if (len(s) >= sizeof out)
        return s;
    copy(out, s);
    for (p = out; *p; p++)
        if (p[0] == '[' && p[1] == '1' && p[2] >= '2' && p[2] <= '5')
            p[1] = '0', p[2] = "3142"[p[2] - '2'];
    en = enrt_translate(out);
    if (en == out || len(en) >= sizeof out)
        return s;
    copy(out, en);
    for (p = out; *p; p++)
        if (p[0] == '[' && p[1] == '0' && p[2] >= '1' && p[2] <= '4')
            p[1] = '1', p[2] = "3524"[p[2] - '1'];
    return out;
}




static u32 description(struct frame *f, u32 width, u8 pitch)
{
    u8 stock = FONT_CELL[2];
    FONT_CELL[2] = pitch;
    draw_fit(reflow_wrap(f->ret, flat((const char *)f->arg[0]), width, line_text, sizeof line_text),
             f->arg[1], f->arg[2], f->arg[3], f->arg[4], width, 0);
    FONT_CELL[2] = stock;
    return 1;
}



/* A column of at most `lines` lines at cell (0: stock) and pitch, condensed into width past them. */
static u32 fit_lines(struct frame *f, u32 width, u32 lines, u8 cell, u8 pitch)
{
    u8 cell_w = FONT_CELL[0], cell_h = FONT_CELL[1], stock = FONT_CELL[2];
    if (cell)
        FONT_CELL[0] = FONT_CELL[1] = cell;
    FONT_CELL[2] = pitch;
    draw_fit(wrap_lines(flat((const char *)f->arg[0]), width, lines), f->arg[1], f->arg[2], f->arg[3], f->arg[4], width, 0);
    FONT_CELL[0] = cell_w, FONT_CELL[1] = cell_h, FONT_CELL[2] = stock;
    return 1;
}

#define DESCRIBED_PITCH 24
static const u32 described[][2] = {
    {0x595F93, 530},
    {0x5975C6, 490},
    {0x593B98, 686},
};

static const u32 single_line[][2] = {
    {0x595BF9, 287}, {0x556ED0, 289}, {0x55B699, 293},
    {0x53693D, 269}, {0x535EE7, 305}, {0x594F77, 229},
    {0x543382, 175}, {0x5950EC, 303}, {0x52B6FD, 181},
    {0x5983CB, 276}, {0x52E78F, 196}, {0x533469, 170},
    {0x538C65, 835}, {0x536742, 252}, {0x536785, 252},
    {0x5A6805, 705}, {0x580811, 705},
};

static u32 single_width(u32 caller)
{
    u32 i;
    for (i = 0; i < sizeof single_line / sizeof *single_line; i++)
        if (caller == single_line[i][0])
            return single_line[i][1];
    return 0;
}

static double army_fit = 1.0;

/* The factor for the whole army line, from the caller's loop frame: count, army ids [esp+0x1C..0x28]
   and stage [esp+0xE8]. The middle name (side 2) is skipped as the stock loop skips it. */
static void army_line(const struct frame *f)
{
    const u32 *loop = f->arg + 5;
    u32 count = loop[7], stage = loop[58], i, names = 0, fixed = 0, shown = 0;
    u32 flag = *(const u32 *)(*(const u32 *)0xC54AF0 + 0xF4);
    unsigned long long mask = flag ? 0x15971a0cull : 0x04c3b52f2b964eb4ull;
    u8 shift = (u8)(flag ? stage : stage - 0x48);
    for (i = 0; i < 3; i++) {
        u32 side = i == 2 ? 1 : i ? 2 : 0;
        const char *s;
        if (side == 2 && (count == 2 || !loop[10] || shift >= 64 || !(mask >> shift & 1)))
            continue;
        s = enrt_translate(*(const char *const *)(ARMY_NAMES + 4 * loop[8 + side]));
        *(text_fit_latin(s) ? &names : &fixed) += measure(s);
        shown++;
    }
    army_fit = text_fit_names(names, fixed + (shown ? shown - 1 : 0) * ARMY_GAP, ARMY_ROOM);
}

static u32 army_width(const char *s)
{
    u32 w = measure(s);
    return text_fit_latin(s) ? (u32)(w * army_fit) : w;
}

/* X360 rows: the attribute level follows the drawn name; costs and skill counts end at a right edge. */
static int name_end;

static void row_columns(struct frame *f)
{
    const char *s = (const char *)f->arg[0];
    u32 w, lane;
    if (!s)
        return;
    switch (f->ret) {
    case 0x52E78F: case 0x52E8BF:
        w = measure(s), lane = single_width(f->ret);
        name_end = (int)f->arg[1] + (int)(lane && w > lane ? lane : w);
        break;
    case 0x52E7C3: case 0x52E8E9:
        f->arg[1] = (u32)(name_end + 0x43 - 0x39);
        break;
    case 0x52E7EE: case 0x52E910:
        f->arg[1] += 0x154 - 0xE0 - measure(s);
        break;
    case 0x533497:
        f->arg[1] += 0xF8 - 0x65 - measure(s);
        break;
    case 0x5973F5: case 0x5974CC:  /* Conditions STATUS "OK": X360 keeps 20 px a byte for it, stock 12 */
        f->arg[1] -= 8 * (u32)lstrlenA(s);
        break;
    }
}




#define PLAIN_BOX_CALL 0x594A0D
#define BOXES 64
static struct box { u32 site, turn; int x, y, w, h, grow; } boxes[2][BOXES];
static u32 box_count[2], box_now;
static char box_text[512];

static void box_drawn(u32 id, struct frame *f)
{
    struct box *b, *last = boxes[box_now ^ 1];
    u32 i, turn = 0;
    if ((id == 42 && f->ret == PLAIN_BOX_CALL) || box_count[box_now] == BOXES)
        return;
    for (i = 0; i < box_count[box_now]; i++)
        turn += boxes[box_now][i].site == f->ret;
    b = &boxes[box_now][box_count[box_now]++];
    b->site = f->ret, b->turn = turn, b->grow = 0;
    b->x = (int)f->arg[0], b->y = (int)f->arg[1], b->w = (int)f->arg[3], b->h = (int)f->arg[4];
    for (i = 0; i < box_count[box_now ^ 1]; i++)
        if (last[i].site == b->site && last[i].turn == turn)
            f->arg[4] += last[i].grow;
}




static u32 fit_box(struct frame *f)
{
    const char *s = (const char *)f->arg[0];
    int x = (int)f->arg[1], y = (int)f->arg[2], inset, width, lines = 1, foot;
    struct box *b = 0, *c = boxes[box_now];
    u32 i;
    for (i = 0; i < box_count[box_now]; i++, c++)
        if (c->x <= x && x < c->x + c->w && c->y <= y && y < c->y + c->h && (!b || c->w * c->h < b->w * b->h))
            b = c;
    if (!b)
        return 0;
    inset = x - b->x < 8 ? 8 : x - b->x > 16 ? 16 : x - b->x;
    width = b->x + b->w - inset - x;
    if (width < 96)
        return 0;
    if ((int)measure(s) > width)
        f->arg[0] = (u32)(s = wrap(s, (u32)width, box_text, sizeof box_text));
    for (; *s; s++)
        lines += *s == '\n';
    foot = y + FONT_CELL[1] + (lines - 1) * FONT_CELL[2] - (b->y + b->h);
    if (foot > 0 && foot + inset > b->grow)
        b->grow = foot + inset;
    return (u32)width;
}



#define KEY_GAP 14
#define KEY_TEXT 56
static const u32 key_first[][2] = {
    {0x593525, 0x593537},
    {0x59360F, 0x59361F},
    {0x59365A, 0x593666},
};



#define DIALOG_PLATE_1 0x54F5B7
#define DIALOG_PLATE_2 0x54F691
static struct plate { char name[96]; u32 x, y, layer, mode, colour; u8 alpha; } plates[2];
static u32 plate_count;

static WCHAR root[MAX_PATH];
static HANDLE runtime_log = INVALID_HANDLE_VALUE;
static int english;
static u32 text_saved, voice_saved;
static u32 rows;
static u32 reloads;
static FILETIME table_stamp;
#define VOICE_BANK L"voice\\Voice.bin"
#define FONT_ENTRY 4059

static void note(const char *s, u32 value);
static int write_at(u32 va, const u8 *bytes, u32 n);
static int stock_at(u32 va, const u8 *stock, u32 n);

static WCHAR *path(WCHAR *out, const WCHAR *name)
{
    lstrcpyW(out, root);
    lstrcatW(out, name);
    return out;
}


static u8 *load(const WCHAR *name, u32 *size)
{
    HANDLE file = CreateFileW(name, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, 0,
                              OPEN_EXISTING, 0, 0);
    u8 *data = 0;
    DWORD got;
    if (file == INVALID_HANDLE_VALUE)
        return 0;
    *size = GetFileSize(file, 0);
    if (*size != INVALID_FILE_SIZE && (data = HeapAlloc(GetProcessHeap(), 0, *size + 1))) {
        if (ReadFile(file, data, *size, &got, 0) && got == *size)
            data[*size] = 0;
        else
            HeapFree(GetProcessHeap(), 0, data), data = 0;
    }
    CloseHandle(file);
    return data;
}


static const u32 *load_table(void)
{
    WCHAR name[MAX_PATH];
    u32 size;
    u32 *t = (u32 *)load(path(name, L"table.bin"), &size);
    if (t && (size < 12 || size % 4 || t[0] & (t[0] + 1) || 20 + 12ull * t[0] > size || t[1] % 4
              || t[1] > size - 4 || t[t[1] / 4] > (size - t[1]) / 4 - 1)) {
        HeapFree(GetProcessHeap(), 0, t);
        t = 0;
    }
    return t;
}

static int table_time(FILETIME *stamp)
{
    WCHAR name[MAX_PATH];
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(path(name, L"table.bin"), GetFileExInfoStandard, &a))
        return 0;
    *stamp = a.ftLastWriteTime;
    return 1;
}


static void reload(void)
{
    static DWORD checked;
    FILETIME stamp;
    const u32 *t;
    if (!english || GetTickCount() - checked < 1000)
        return;
    checked = GetTickCount();
    if (!table_time(&stamp) || !CompareFileTime(&stamp, &table_stamp))
        return;
    table_stamp = stamp;
    if ((t = load_table())) {


        enrt_table = t;
        reloads++;
        note("reloaded table.bin", reloads);
    } else
        note("reload refused: table.bin is damaged", 0);
}



static struct loose { u32 key, size; } loose[256];
static u32 loose_count;
#define ARCHIVE_WHOLE ((u32 (__attribute__((thiscall)) *)(u32, u32))0x5D0540)
#define GAME_HEAP ((u32 (*)(void))0x5D3670)
#define HEAP_ALLOC ((u8 *(__attribute__((thiscall)) *)(u32, u32, u32))0x5D3500)
#define GAME_FREE ((void (*)(void *))0x41BE80)

static void index_entries(void)
{
    WCHAR name[MAX_PATH];
    WIN32_FIND_DATAW found;
    HANDLE find = FindFirstFileW(path(name, L"entries\\*.bin"), &found);
    if (find == INVALID_HANDLE_VALUE)
        return;
    do {
        const WCHAR *c = found.cFileName;
        u32 key = 0;
        for (; *c >= '0' && *c <= '9'; c++)
            key = key * 10 + *c - '0';
        if (c != found.cFileName && !lstrcmpiW(c, L".bin") && (english || key == FONT_ENTRY) && loose_count < 256)
            loose[loose_count].key = key, loose[loose_count++].size = found.nFileSizeLow;
    } while (FindNextFileW(find, &found));
    FindClose(find);
    note("entries served:", loose_count);
}

static struct loose *served(u32 key)
{
    u32 i;
    for (i = 0; i < loose_count; i++)
        if (loose[i].key == key)
            return &loose[i];
    return 0;
}





#define VOICE_KEY 0x161C
#define VOICE_SLOT 0x4EF84
#define VOICE_TAG 0x80000000u
#define VOICE_MODE (*(volatile u32 *)0x801F24)
#define VOICE_STAGE (*(volatile u8 *)0x801AA0)
static HANDLE voice_bank = INVALID_HANDLE_VALUE;
static u32 *voice_table, voice_ids;






#define SE_KEY 0xFD9
#define SE_BANK L"voice\\SE.bin"
static HANDLE se_bank = INVALID_HANDLE_VALUE;
static u32 *se_table, se_banks;



static HANDLE open_bank(const WCHAR *file, u32 max, u32 words, u32 **table, u32 *count)
{
    WCHAR name[MAX_PATH];
    DWORD got;
    u32 n;
    HANDLE f = CreateFileW(path(name, file), GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
    if (f == INVALID_HANDLE_VALUE)
        return f;
    if (ReadFile(f, &n, 4, &got, 0) && got == 4 && n && n <= max
        && (*table = HeapAlloc(GetProcessHeap(), 0, 4 * words * n))
        && ReadFile(f, *table, 4 * words * n, &got, 0) && got == 4 * words * n)
        return *count = n, f;
    CloseHandle(f);
    return INVALID_HANDLE_VALUE;
}

#define LIPS_BANK L"voice\\Lips.bin"
static u8 *lips;
static u32 lips_size;

static void open_voices(void)
{
    WCHAR name[MAX_PATH];
    voice_bank = open_bank(VOICE_BANK, 0x5889, 2, &voice_table, &voice_ids);
    note("English voice ids:", voice_ids);
    lips = load(path(name, LIPS_BANK), &lips_size);
    note("Lips.bin bytes:", lips_size);
    se_bank = open_bank(SE_BANK, 0x7E, 4, &se_table, &se_banks);
    note("English SE banks:", se_banks);
}


static const u32 *english_voice(u32 id)
{
    const u32 *e = voice_table + 2 * id;
    return !voice_saved && id < voice_ids && e[1] && e[1] <= VOICE_SLOT ? e : 0;
}

/* Lips.bin: u32 id count, (u32 offset, u32 size) per voice id, the blocks. The stock lip block of a spoken
 * line is timed to the Japanese clip; an English clip gets the block the installer timed to it. index is
 * the line record's voice index, which the voice start (0x513720) adds to the story's base. */
static const u8 *english_lips(u32 index)
{
    const u32 *e;
    u32 id = (VOICE_MODE ? 0x8F4 : 0xAF3) + index;
    if (!lips || index >= (VOICE_MODE ? 0x1FFu : 0x150u) || !english_voice(id) || lips_size < 4
        || id >= *(const u32 *)lips || 4 + 8 * id + 8 > lips_size)
        return 0;
    e = (const u32 *)(lips + 4) + 2 * id;
    return e[1] >= 0x20 && e[0] <= lips_size && e[1] <= lips_size - e[0] ? lips + e[0] : 0;
}

static u32 read_voice(struct frame *f, HANDLE bank)
{
    OVERLAPPED at = {0};
    u8 *dst = (u8 *)f->arg[1];
    u32 n = f->arg[4];
    DWORD got = 0;
    at.Offset = f->arg[2] & ~VOICE_TAG;
    if (!dst)
        dst = HEAP_ALLOC(GAME_HEAP(), 16, n);
    f->eax = dst && ReadFile(bank, dst, n, &got, &at) && got == n ? (u32)dst : 0;
    if (!f->eax) {
        note("read failed: English voice at", at.Offset);
        if (dst && !f->arg[1])
            GAME_FREE(dst);
    }
    return 1;
}



static u32 read_loose(struct frame *f)
{
    struct loose *e = served(f->arg[0]);
    WCHAR name[MAX_PATH], *p;
    u32 key = f->arg[0], off = f->arg[2], maxlen = f->arg[4], n, digits;
    u8 *dst = (u8 *)f->arg[1];
    DWORD got = 0;
    HANDLE file;
    if (key == VOICE_KEY && off & VOICE_TAG && voice_bank != INVALID_HANDLE_VALUE)
        return read_voice(f, voice_bank);
    if (key == SE_KEY && off & VOICE_TAG && se_bank != INVALID_HANDLE_VALUE)
        return read_voice(f, se_bank);
    if (!e)
        return 0;
    if (ARCHIVE_WHOLE(f->ecx, key))
        off = maxlen = 0;
    if (f->arg[3] || off >= e->size)
        off = 0;
    n = e->size - off;
    if (maxlen && n >= maxlen)
        n = maxlen;
    p = path(name, L"entries\\");
    p += lstrlenW(p);
    for (digits = 1; key / digits >= 10; digits *= 10)
        ;
    for (; digits; digits /= 10)
        *p++ = '0' + key / digits % 10;
    lstrcpyW(p, L".bin");
    f->eax = 0;
    file = CreateFileW(name, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
    if (file == INVALID_HANDLE_VALUE)
        return note("read failed: entry", key), 1;
    if (!f->arg[1])
        dst = HEAP_ALLOC(GAME_HEAP(), 16, n);
    if (dst && SetFilePointer(file, off, 0, FILE_BEGIN) == off && ReadFile(file, dst, n, &got, 0) && got == n)
        f->eax = (u32)dst;
    else {
        note("read failed: entry", key);
        if (dst && !f->arg[1])
            GAME_FREE(dst);
    }
    CloseHandle(file);
    return 1;
}


#define GAME_SOUND ((void (*)(u32, u32, u32, u32, u32))0x512EB0)
#define SETTINGS_CURSOR (*(const volatile u32 *)(*(const u32 *)0xC2FC90 + 0x4C))
#define INPUT_BLOCKED (*(const volatile u32 *)0x120A1E0)
#define LABEL_CELL 24
#define LABEL_CENTRE 101
#define VALUE_CENTRE 417
#define VALUE_STEP 180
#define SHADOW 2
#ifdef ENRT_RENDER_DISTANCE
#define LAST_ROW 5 /* the render distance row ends the list */
#else
#define LAST_ROW 4 /* the frame rate row ends the list */
#endif



#define PALETTE_INDEX(c) (((u32)&(c) - 0x712F38) / 4)
static const u32 label_colour = 0x80a8a8a8, chosen_colour = 0x80ffffff, lit_colour = 0x809df1ff,
                 dim_colour = 0x809d9f9d, shadow_colour = 0x80181818;
#define NOTE_CELL 18
static const char *const language_label[2][2] = {
    {"TEXT LANGUAGE", "VOICE LANGUAGE"},
    {"\x95\x5c\x8e\xa6\x8c\xbe\x8c\xea", "\x89\xb9\x90\xba\x8c\xbe\x8c\xea"},
};
static const char *const language_value[2][2] = {
    {"ENGLISH", "JAPANESE"},
    {"\x89\x70\x8c\xea", "\x93\xfa\x96\x7b\x8c\xea"},
};
static const char *const restart_note[2] = {
    "The text language changes the next time you start the game.",

    "\x95\x5c\x8e\xa6\x8c\xbe\x8c\xea\x82\xcd\x8e\x9f\x89\xf1\x82\xcc\x8b\x4e\x93\xae\x8e\x9e\x82\xc9"
    "\x90\xd8\x82\xe8\x91\xd6\x82\xed\x82\xe8\x82\xdc\x82\xb7\x81\x42",
};


static void shadowed(const char *s, u32 x, u32 y, u32 cell, const u32 *colour)
{
    GAME_FONT(cell, cell, PALETTE_INDEX(shadow_colour));
    draw(s, x + SHADOW, y + SHADOW, 0);
    GAME_FONT(cell, cell, PALETTE_INDEX(*colour));
    draw(s, x, y, 0);
}


static void choice_row(const char *label, const char *const *values, u32 value, u32 x, u32 y, int chosen)
{
    u32 mode = FONT_MODE, colour = FONT_COLOUR, v;
    u8 w = FONT_CELL[0], h = FONT_CELL[1], alpha = FONT_ALPHA;
    if ((int)x < 0)
        return;
    y += (42 - LABEL_CELL) / 2;
    shadowed(label, x + LABEL_CENTRE - measure(label) / 2, y, LABEL_CELL, chosen ? &chosen_colour : &label_colour);
    for (v = 0; v < 2; v++)
        shadowed(values[v], x + VALUE_CENTRE + v * VALUE_STEP - measure(values[v]) / 2, y, LABEL_CELL,
                 v == value ? &lit_colour : &dim_colour);
    FONT_CELL[0] = w, FONT_CELL[1] = h, FONT_MODE = mode, FONT_COLOUR = colour, FONT_ALPHA = alpha;
}

static void language_row(u32 row, u32 x, u32 y)
{
    choice_row(language_label[!english][row], language_value[!english], row ? voice_saved : text_saved, x, y,
               SETTINGS_CURSOR == 3 + row);
}


static void language_input(u32 row, u32 repeat)
{
    WCHAR name[MAX_PATH];
    u32 *value = row ? &voice_saved : &text_saved;
    if (INPUT_BLOCKED || row >= rows || !(repeat & 0x0C))
        return;
    *value ^= 1;
    WritePrivateProfileStringW(L"language", row ? L"voice" : L"text", *value ? L"japanese" : L"english",
                               path(name, L"settings.ini"));
    GAME_SOUND(1, 0, 0x2CDC, 0x2CDC, 0x1000);
}



#define FOV_MIN 100
#define FOV_MAX 150
#define FOV_STEP 5
#define CAMERA_FOV(camera) (*(float *)((camera) + 0x224))
static u32 fov = FOV_MIN;
static const char *const fov_label[2] = {"FIELD OF VIEW", "\x8e\x8b\x96\xec\x8a\x70"};


static void fov_row(u32 x, u32 y, int chosen)
{
    u32 mode = FONT_MODE, colour = FONT_COLOUR;
    u8 w = FONT_CELL[0], h = FONT_CELL[1], alpha = FONT_ALPHA;
    char value[] = "< 100% >";
    if ((int)x < 0)
        return;
    value[2] = '0' + fov / 100, value[3] = '0' + fov / 10 % 10, value[4] = '0' + fov % 10;
    y += (42 - LABEL_CELL) / 2;
    shadowed(fov_label[!english], x + LABEL_CENTRE - measure(fov_label[!english]) / 2, y, LABEL_CELL,
             chosen ? &chosen_colour : &label_colour);
    shadowed(value, x + VALUE_CENTRE + VALUE_STEP / 2 - measure(value) / 2, y, LABEL_CELL, &lit_colour);
    FONT_CELL[0] = w, FONT_CELL[1] = h, FONT_MODE = mode, FONT_COLOUR = colour, FONT_ALPHA = alpha;
}


static void fov_input(u32 repeat)
{
    WCHAR name[MAX_PATH], value[] = L"100";
    u32 was = fov;
    if (repeat & 0x04 && fov > FOV_MIN)
        fov -= FOV_STEP;
    else if (repeat & 0x08 && fov < FOV_MAX)
        fov += FOV_STEP;
    if (fov == was)
        return;
    value[0] = '0' + fov / 100, value[1] = '0' + fov / 10 % 10, value[2] = '0' + fov % 10;
    WritePrivateProfileStringW(L"camera", L"fov", value, path(name, L"settings.ini"));
    GAME_SOUND(1, 0, 0x2CDC, 0x2CDC, 0x1000);
}











#ifdef ENRT_RENDER_DISTANCE
#define VIEW_GETTER 0x475BA0
#define CROWD_GETTER 0x475B60
#define VIEW_SCALE 2.0f
static const u8 view_getter[37] = {
    0x0f, 0xb6, 0x05, 0x27, 0x15, 0x80, 0x00, 0x85, 0xc0, 0x75, 0x07, 0xd9, 0x05, 0x90, 0x38, 0x6d, 0x00, 0xc3, 0x83,
    0xf8, 0x01, 0x75, 0x07, 0xd9, 0x05, 0x1c, 0xf1, 0x6c, 0x00, 0xc3, 0xd9, 0x05, 0x28, 0xf0, 0x6c, 0x00, 0xc3};
static const u8 crowd_getter[31] = {
    0x0f, 0xb6, 0x05, 0x26, 0x15, 0x80, 0x00, 0x85, 0xc0, 0x75, 0x06, 0xb8, 0x65, 0x00, 0x00, 0x00, 0xc3, 0x83,
    0xe8, 0x01, 0xf7, 0xd8, 0x1b, 0xc0, 0x83, 0xe0, 0xe7, 0x83, 0xc0, 0x4c, 0xc3};
static const u8 view_load[3] = {0x0D, 0x19, 0x20};
static const float view_stock[3] = {15000.0f, 12500.0f, 10000.0f};
static float view[3];
static u32 distance = 1, distance_ok;
static const char *const distance_label[2] = {"RENDER DISTANCE", "\x95\x60\x89\xe6\x8b\x97\x97\xa3"};
static const char *const switch_value[2] = {"ON", "OFF"};

static void distance_apply(void)
{
    u8 full = distance ? 120 : 101, many = distance ? 90 : 76, normal = distance ? 60 : 51, step = normal - many;
    u32 i;
    for (i = 0; i < 3; i++)
        view[i] = view_stock[i] * (distance ? VIEW_SCALE : 1.0f);
    write_at(CROWD_GETTER + 0x0C, &full, 1);
    write_at(CROWD_GETTER + 0x1A, &step, 1);
    write_at(CROWD_GETTER + 0x1D, &many, 1);
    FlushInstructionCache(GetCurrentProcess(), 0, 0);
}


static void distance_install(void)
{
    u32 i, va;
    if (!stock_at(VIEW_GETTER, view_getter, sizeof view_getter) || !stock_at(CROWD_GETTER, crowd_getter, sizeof crowd_getter)) {
        note("refused: RENDER DISTANCE, a getter differs from stock at", VIEW_GETTER);
        return;
    }
    distance_apply();
    for (i = 0; i < 3; i++)
        if (va = (u32)&view[i], !write_at(VIEW_GETTER + view_load[i], (const u8 *)&va, 4)) {
            note("failed: could not write", VIEW_GETTER + view_load[i]);
            return;
        }
    distance_ok = 1;
}

static void distance_input(u32 repeat)
{
    WCHAR name[MAX_PATH];
    if (!distance_ok || !(repeat & 0x0C))
        return;
    distance ^= 1;
    distance_apply();
    WritePrivateProfileStringW(L"display", L"distance", distance ? L"on" : L"off", path(name, L"settings.ini"));
    GAME_SOUND(1, 0, 0x2CDC, 0x2CDC, 0x1000);
}
#endif




#define JOY_STATE ((volatile int *)0x140D120)
#define PLAYER_JOY ((volatile u32 *)0x7AA8B4)
#define PLAYER_MAP(p) (*(u8 *const *)(0x140C48C + 16 * (p)))
#define PAD_AUTO ((u32(*)(u32))0x5E2D30)
#define XINPUT_SHAPE(pad) (*(const u32 *)((pad) + 0x160) == 5 && *(const u32 *)((pad) + 0x168) == 10)
#define PADS ((void **const volatile *)0x140D440)



static void bind_camera(u32 player)
{
    static const u8 axis[4] = {4, 4, 3, 3}, negative[4] = {1, 0, 1, 0};
    u32 a;
    for (a = 0; a < 4; a++) {
        u8 *b = PLAYER_MAP(player) + (17 + a) * 0x28 + 8 + 16;
        if (*(u32 *)b != 10)
            continue;
        *(u32 *)b = PLAYER_JOY[player], b[4] = 1, *(u32 *)(b + 8) = negative[a], *(u32 *)(b + 12) = axis[a];
    }
}







#define PROMPT_TABLE (*(unsigned long long *const *)0xC54C38)
#define PACKAGE_ITEM ((const void *(*)(const void *, u32))0x5AD170)
#define TEXTURE_HANDLE ((unsigned long long (*)(const void *, u32, u32))0x5E02C0)
#define START_WIDTH 0x549CC4
#define STICK_WIDTH 0x549E68        /* F0 55 quad width (texture 0xB3) */
#define WIDE_LAST_DRAW 0x54BAE1     /* last double-advance icon code: draw, then measure */
#define WIDE_LAST_MEASURE 0x54C041
#define KEYS_HELD ((const volatile u8 *)0x140C4E0)
static const u8 prompt_slot[19] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 44, 178, 179};
static unsigned long long prompt_handle[2][19];
static int prompts_ready, prompt_set;
static int pad_last;

static void prompts_load(void)
{
    WCHAR name[MAX_PATH];
    u32 size, i;
    u8 *data = load(path(name, L"prompts-xbox.bin"), &size);
    if (!data)
        return;
    for (i = 0; i < sizeof prompt_slot; i++) {
        prompt_handle[0][i] = PROMPT_TABLE[prompt_slot[i]];
        prompt_handle[1][i] = TEXTURE_HANDLE(PACKAGE_ITEM(data, prompt_slot[i]), 0x1F00, 0x3EE3);
    }
    HeapFree(GetProcessHeap(), 0, data);
    prompts_ready = 1;
}







struct hires {
    u32 entry, item, scale, w, h, band, per_col, offset, size;
};
#define HIRES_MAX 256
#define HIRES_OVERLAY 0x80000000u
#define HIRES_RIGHT_GROW 0x40000000u
#define HIRES_SCALE_MASK 0x3fffffffu
static struct hires hires_row[HIRES_MAX];
static unsigned long long hires_handle[HIRES_MAX];
static const u32 *hires_root[HIRES_MAX];
static u32 hires_rows;
static int hires_native;
/* The row after the last stock one, entry 0: the sheet of the Game Settings rows wozmod adds (1-based, 0 = none). */
static u32 option_sheet;

static u32 hires_width(u32 entry, u32 item)
{
    u32 i;
    for (i = 0; i < hires_rows; i++)
        if (hires_row[i].entry == entry && hires_row[i].item == item)
            return hires_row[i].scale & HIRES_RIGHT_GROW ? 0 : hires_row[i].band >> 16;
    return 0;
}

static void hires_load(void)
{
    WCHAR name[MAX_PATH];
    u32 size, i, j, count;
    u8 *data = english ? load(path(name, L"hires.bin"), &size) : 0;
    if (!data)
        return;
    count = *(const u32 *)data;
    for (i = 0; i < count && i < HIRES_MAX; i++) {
        struct hires *r = hires_row + hires_rows;
        *r = ((const struct hires *)(data + 4))[i];
        for (j = 0; r->entry && j < sizeof roots / sizeof *roots; j++)
            if (roots[j][2] == r->entry && r->item < roots[j][1])
                break;
        if ((r->entry && j == sizeof roots / sizeof *roots) || r->offset + r->size > size || !r->scale) {
            note("refused: hires.bin row", i);
            continue;
        }
        hires_root[hires_rows] = r->entry ? (const u32 *)roots[j][0] : 0;
        if (!r->entry)
            option_sheet = hires_rows + 1;
        hires_handle[hires_rows] = TEXTURE_HANDLE(data + r->offset, 0x1F00, 0x3EE4 + i);
        hires_rows++;
    }
    HeapFree(GetProcessHeap(), 0, data);
    note("hi-res sheets:", hires_rows);
}






static void camp_help_position(const struct hires *r, struct frame *f, u32 width)
{
    static const u32 rows[][5] = {
        {2,548,1092,1175,90}, {3,544,1086,1174,90},
        {4,392,664,1175,89}, {5,400,785,1175,89},
        {6,476,610,1175,89}, {7,420,614,1175,88},
        {8,448,683,1175,87}, {9,528,1050,1175,90},
        {10,560,1103,0,90}, {11,632,1259,1175,90},
        {12,0,971,1175,90}, {13,648,1011,1175,90},
    };
    u32 i, growth, right, minimum = r->band >> 16;


    if (r->entry != 4060 || f->ret != 0x53326A || f->arg[7] || f->arg[8]
        || f->arg[5] != width || f->arg[6] != f->arg[10]
        || f->arg[10] < 28 || f->arg[10] > 32)
        return;
    for (i = 0; i < sizeof rows / sizeof *rows; i++) {
        if (rows[i][0] != r->item)
            continue;
        if (r->scale & HIRES_RIGHT_GROW)
            growth = minimum;
        else if (rows[i][1])
            growth = minimum > rows[i][1] ? minimum - rows[i][1] : 0;
        else if (!minimum)
            growth = 0;
        else
            return;
        right = rows[i][2] + 2 * growth;
        if (right > 2 * width)
            return;


        if (rows[i][3])
            f->arg[2] = rows[i][3] - (right + 1) / 2;
        f->arg[3] = rows[i][4];
        return;
    }
}


static void hires_quad(u32 id, struct frame *f)
{
    u32 i, x = f->arg[7], y = f->arg[8], w = f->arg[9], h = f->arg[10];
    if (hires_native)
        return;
    for (i = 0; i < hires_rows; i++) {
        const struct hires *r = hires_row + i;
        u32 scale = r->scale & HIRES_SCALE_MASK, minimum = r->band >> 16;
        u32 original_w = w, expanded;
        const u32 *table = hires_root[i] ? *(const u32 *const *)hires_root[i] : 0;
        if (!table || table[2 * r->item] != f->arg[0])
            continue;
        if (!scale || !w || !h || x > r->w || w > r->w - x ||
            y > r->h || h > r->h - y)
            return;
        if (!x) {
            if (r->scale & HIRES_RIGHT_GROW) {


                if (minimum > r->w - w)
                    return;
                w += minimum;
            } else if (w < minimum) {
                w = minimum;
                if (w > r->w)
                    return;
            }
        }
        if (r->per_col) {
            u32 col_h = r->per_col * (r->band & 0xFFFF), col = y / col_h;
            if ((y + h - 1) / col_h != col)
                return;
            y -= col * col_h, x += col * r->w;
        }
        if (x + w > 4095 / scale || y + h > 4095 / scale)
            return;


        if ((r->entry == 4069 && r->item <= 1) && f->arg[3] == 88)
            f->arg[3] = 24;
        if (w != original_w) {
            expanded = (f->arg[5] * w + original_w / 2) / original_w;
            if ((r->scale & HIRES_RIGHT_GROW) ||
                (r->entry == 4060 && r->item >= 2 && r->item <= 13) ||
                (r->entry == 4069 && r->item <= 1) ||
                (r->entry == 4074 && r->item == 10))
                f->arg[2] -= expanded - f->arg[5];
            f->arg[5] = expanded;
        }
        camp_help_position(r, f, w);
        if (r->scale & HIRES_OVERLAY) {
            u32 *a = f->arg;
            void (*quad)(u32,u32,u32,u32,u32,u32,u32,u32,u32,u32,u32,u32,u32) =
                id == 21 ? GAME_QUAD : (void *)0x5DE620;


            a[9] = w;
            hires_native = 1;
            quad(a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7],a[8],a[9],a[10],a[11],a[12]);
            hires_native = 0;
        }
        f->arg[0] = (u32)hires_handle[i], f->arg[1] = (u32)(hires_handle[i] >> 32);
        f->arg[7] = x * scale, f->arg[8] = y * scale, f->arg[9] = w * scale, f->arg[10] = h * scale;
        return;
    }
}

#ifdef ENRT_TEST



struct review_row { u32 entry, item, w, h, offset, size, base_offset, base_size; };
static u8 *review_data;
static u32 review_size, review_count, review_name, review_rect[4], review_frame;
static unsigned long long review_ref[HIRES_MAX], review_base[HIRES_MAX], review_bg;
static int review_drawing;

static u32 test_review_set(u32 name, const u32 *rect)
{
    WCHAR path_name[MAX_PATH];
    u32 i, j;
    if (!name) {
        review_name = 0;
        return 0;
    }
    if (!review_data) {
        review_data = load(path(path_name, L"review.bin"), &review_size);
        if (!review_data || review_size < 4)
            return 6;
        review_count = *(const u32 *)review_data;
        if (review_count > HIRES_MAX || 4 + review_count * sizeof(struct review_row) > review_size)
            return 6;
    }
    for (i = 1; i < review_count; i++) {
        const struct review_row *r = (const struct review_row *)(review_data + 4) + i;
        if ((r->entry << 16 | r->item) != name)
            continue;
        for (j = 0; j < hires_rows; j++)
            if (hires_row[j].entry == r->entry && hires_row[j].item == r->item)
                break;
        if (j == hires_rows || !rect[2] || !rect[3] || rect[0] + rect[2] > hires_row[j].w ||
            rect[1] + rect[3] > hires_row[j].h || rect[2] > 1110 || rect[3] > 200 ||
            r->offset > review_size || r->size > review_size - r->offset ||
            r->base_offset > review_size || r->base_size > review_size - r->base_offset)
            return 6;
        if (!review_ref[i]) {
            review_ref[i] = TEXTURE_HANDLE(review_data + r->offset, 0x1F00, 0x6000 + 2 * i);
            review_base[i] = TEXTURE_HANDLE(review_data + r->base_offset, 0x1F00, 0x6001 + 2 * i);
        }
        if (!review_bg) {
            const struct review_row *bg = (const struct review_row *)(review_data + 4);
            if (bg->offset > review_size || bg->size > review_size - bg->offset)
                return 6;
            review_bg = TEXTURE_HANDLE(review_data + bg->offset, 0x1F00, 0x6000);
        }
        review_name = i;
        words(review_rect, rect, sizeof review_rect);
        return 0;
    }
    return 6;
}

static void review_quad(unsigned long long handle, u32 x, u32 y, u32 w, u32 h, u32 sx, u32 sy, u32 sw, u32 sh)
{
    GAME_QUAD((u32)handle, (u32)(handle >> 32), x,y,0,w,h,sx,sy,sw,sh,0x80,4);
}

static void test_review_draw(void)
{
    u32 i, sx = review_rect[0], sy = review_rect[1], w = review_rect[2], h = review_rect[3];
    const struct review_row *r;
    if (!review_name || review_drawing || review_frame == enrt_test.frame)
        return;
    review_frame = enrt_test.frame;
    review_drawing = hires_native = 1;
    r = (const struct review_row *)(review_data + 4) + review_name;
    review_quad(review_bg, 120,148,1126, 2 * h + 96, 0,0,4,4);
    if (sx < r->w && sy < r->h) {
        u32 rw = w < r->w - sx ? w : r->w - sx, rh = h < r->h - sy ? h : r->h - sy;
        review_quad(review_ref[review_name],128,160,rw,rh,sx,sy,rw,rh);
    }
    for (i = 0; i < hires_rows; i++) {
        const struct hires *v = hires_row + i;
        u32 scale = v->scale & HIRES_SCALE_MASK, vx = sx, vy = sy;
        if (v->entry != r->entry || v->item != r->item)
            continue;
        if (v->scale & HIRES_OVERLAY)
            review_quad(review_base[review_name],128,208+h,w,h,sx,sy,w,h);
        if (v->per_col) {
            u32 col_h = v->per_col * (v->band & 0xFFFF), col = sy / col_h;
            vx += col * v->w, vy -= col * col_h;
        }
        review_quad(hires_handle[i],128,208+h,w,h,vx*scale,vy*scale,w*scale,h*scale);
        break;
    }
    review_drawing = hires_native = 0;
}
#endif



static int rebinding;

static void prompts_show(int xbox)
{
    static const u8 width[2] = {30, 60}, wide_last[2] = {0x14, 0x15};
    u32 i;
    if (!prompts_ready || xbox == prompt_set)
        return;
    prompt_set = xbox;
    PAD_PROMPTS = (u8)xbox;
    for (i = 0; i < sizeof prompt_slot; i++)
        PROMPT_TABLE[prompt_slot[i]] = prompt_handle[xbox][i];
    write_at(START_WIDTH, &width[xbox], 1);
    write_at(STICK_WIDTH, &width[xbox], 1);
    write_at(WIDE_LAST_DRAW, &wide_last[xbox], 1);
    write_at(WIDE_LAST_MEASURE, &wide_last[xbox], 1);
}





#define MENU_WORD(p, repeat) (*(const volatile u32 *)(0xC87F70 + 0xF0 * (p) + 4 * (repeat)))
static u32 menu_word(u32 player, int repeat, u32 caller)
{
    u32 w = MENU_WORD(player, repeat);
    return pad_last && caller - 0x520000 < 0x90000 ? (w & ~0xC0u) | (w & 0x40) << 1 | (w & 0x80) >> 1 : w;
}


static void keys_read(void)
{
    static u8 held[256];
    u32 i, pressed = 0;
    for (i = 0; i < 256; i++) {
        u8 now = KEYS_HELD[i] & 0x80;
        pressed |= now & ~held[i];
        held[i] = now;
    }
    if (pressed)
        pad_last = 0;
}





#define GAME_NEW ((char *(*)(u32))0x69BC72)
static int xbox_pad_seen;

static int pad_label(const u8 *binding, char **out)
{
    static const char *const button[10] = {"A", "B", "X", "Y", "LB", "RB", "Back", "Start", "LS Click", "RS Click"};
    static const char *const axis[5][2] = {
        {"LS Right", "LS Left"}, {"LS Down", "LS Up"}, {"LT", "RT"}, {"RS Right", "RS Left"}, {"RS Down", "RS Up"}};
    u32 code = *(const u32 *)(binding + 12);
    const char *name;
    if (!xbox_pad_seen || *(const u32 *)binding >= 8)
        return 0;
    if (binding[4])
        name = code < 5 ? axis[code][*(const u32 *)(binding + 8) != 0] : 0;
    else
        name = code < 10 ? button[code] : 0;
    if (!name)
        return 0;
    *out = lstrcpyA(GAME_NEW(30), name);
    return 1;
}






#define MOVE_ACTION ((const u32 *)0x79EC68)
#define GAME_DELETE ((void (*)(void *))0x69BD3A)
static int move_key(const struct frame *f)
{
    static const u8 art[12] = {1, 0, 12, 11, 7, 9, 14, 13, 179, 178, 8, 10};
    const u8 *b = PLAYER_MAP(f->edx) + MOVE_ACTION[f->eax] * 0x28 + 8 + 16;
    u32 code = *(const u32 *)(b + 12), slot = 0xFF, w = 0;
    char *name;
    if (!prompt_set)
        return 0;
    if (*(const u32 *)b < 8)
        slot = !b[4] && code < 10 ? art[code] : b[4] && code == 2 ? art[10 + (*(const u32 *)(b + 8) != 0)] : 0xFF;
    if (slot != 0xFF) {
        unsigned long long h = PROMPT_TABLE[slot];
        w = slot >= 13 ? 60 : 30;
        GAME_QUAD((u32)h, (u32)(h >> 32), f->arg[0], f->arg[1], 0, w, 30, 0, 0, w, 30, 0x80, 4);
    } else if (pad_label(b, &name)) {
        w = GAME_MEASURE(name, 0, 0);
        GAME_DRAW(name, f->arg[0], f->arg[1], 0, 0);
        GAME_DELETE(name);
    }
    if (f->ebx)
        *(u32 *)f->ebx = w;
    return 1;
}


/* Windows names every XInput pad this in DirectInput; Wine passes a third-party pad's own name (an
 * 8BitDo's, a Switch Pro's), for which the auto-setting picks layout 0, where Start reads as held and
 * the title screen runs on by itself. The name is the pad object's first 260 bytes. */
static int xbox_named(u8 *pad)
{
    if (!XINPUT_SHAPE(pad))
        return 0;
    lstrcpyA((char *)pad, "Controller (XBOX 360 For Windows)");
    return 1;
}

/* With no saved Key Config, the input map load (0x5E38E0) auto-sets each player's pad by name before
 * the first poll, so name the pads first. A saved Key Config is loaded as it is. Logs each pad once,
 * with its own name and its shape as axes << 8 | buttons. */
static void pads_named(void)
{
    static int logged;
    char line[80];
    u8 *pad;
    u32 i, count = PADS[0] ? (u32)(PADS[1] - PADS[0]) / 2 : 0;
    if (!logged)
        note("pads at start", count);
    for (i = 0; i < count; i++) {
        pad = PADS[0][2 * i + 1];
        if (!logged) {
            lstrcpyA(line, "pad: ");
            lstrcpynA(line + 5, (const char *)pad, sizeof line - 5);
            note(line, *(const u32 *)(pad + 0x160) << 8 | *(const u32 *)(pad + 0x168));
        }
        xbox_named(pad);
    }
    logged = 1;
}

static void pad_read(const u8 *pad)
{
    static int bound, was_active;
    volatile int *s = JOY_STATE;
    u32 i, active = 0;
    u32 pov = (u32)s[8], dir;
    static const signed char dx[8] = {0, 1, 1, 1, 0, -1, -1, -1}, dy[8] = {-1, -1, 0, 1, 1, 1, 0, -1};
    if (!xbox_named((u8 *)pad))  /* a pad plugged in after the start */
        return;
    xbox_pad_seen = 1;
    if (!bound) {
        bound = 1;
        if (PLAYER_JOY[0] == 10)
            note(PAD_AUTO(0) ? "pad: player 1 auto-set" : "pad: player 1 auto-set failed", 0);
        if (PLAYER_JOY[0] < 8)
            bind_camera(0);
        if (PLAYER_JOY[1] < 8)
            bind_camera(1);
    }
    s[5] = s[3];
    if ((pov & 0xFFFF) != 0xFFFF) {
        dir = (pov + 2250) / 4500 % 8;
        s[0] = dx[dir] * 255, s[1] = dy[dir] * 255;
    }
    for (i = 0; i < 10; i++)
        active |= ((const volatile u8 *)(s + 12))[i] & 0x80;
    for (i = 0; i < 6; i++)
        active |= s[i] > 128 || s[i] < -128;
    if (active && !was_active)
        pad_last = 1;
    was_active = active;
}








#define DINPUT (*(void *const volatile *)0x140D3F8)
#define DINPUT_CREATE ((long(__stdcall *)(HINSTANCE, u32, const void *, void **, void *))0x602710)
#define IID_DINPUT8A ((const void *)0x75C2A4)
#define PAD_ADD ((BOOL(__stdcall *)(const u8 *, void *))0x5E6230)
#define PAD_INIT ((void(__attribute__((thiscall)) *)(void *, const char *, u32, u32, u32, float))0x5E7940)
#define PAD_NUMBER ((void(__attribute__((thiscall)) *)(void *, u32))0x5E8420)
#define GAME_WINDOW (*(const volatile u32 *)0x140C718)
typedef long(__stdcall *com2)(void *, u32);
typedef long(__stdcall *com3)(void *, u32, u32);
typedef long(__stdcall *com4)(void *, u32, u32, u32);
typedef long(__stdcall *com5)(void *, u32, u32, u32, u32);
#define COM(object, offset, n) ((com##n)(*(void ***)(object))[(offset) / 4])

static volatile u32 attached;


static BOOL __stdcall pad_sign(const u8 *instance, void *ref)
{
    const u32 *guid = (const u32 *)(instance + 4);
    *(u32 *)ref = *(u32 *)ref * 31 + (guid[0] ^ guid[1] ^ guid[2] ^ guid[3]);
    return 1;
}

static DWORD __stdcall pad_watch(void *unused)
{
    void *input;
    u32 sign;
    (void)unused;
    if (DINPUT_CREATE(GetModuleHandleA(0), 0x800, IID_DINPUT8A, &input, 0) < 0)
        return 0;
    for (;; Sleep(2000)) {
        sign = 1;
        COM(input, 0x10, 5)(input, 4, (u32)pad_sign, (u32)&sign, 1);
        attached = sign;
    }
}

struct pad_scan {
    u32 count, guid[8][4];
};

static BOOL __stdcall pad_found(const u8 *instance, void *ref)
{
    struct pad_scan *scan = ref;
    const u32 *guid = (const u32 *)(instance + 4);
    u32 i;
    for (i = 0; i < scan->count; i++)
        if (scan->guid[i][0] == guid[0] && scan->guid[i][1] == guid[1] && scan->guid[i][2] == guid[2]
            && scan->guid[i][3] == guid[3])
            return 1;
    return PAD_ADD(instance, 0);
}

static void pads_rescan(void)
{
    static HANDLE watcher;
    static u32 seen;
    struct pad_scan scan = {0};
    u32 info[0x244 / 4], caps[0x2C / 4], buffer[5] = {0x14, 0x10, 0, 0, 10};
    u32 i, j, count;
    void *device, *pad;
    if (!DINPUT)
        return;
    if (!watcher)
        watcher = CreateThread(0, 0, pad_watch, 0, 0, 0);
    if (attached == seen)
        return;
    seen = attached;
    count = PADS[0] ? (u32)(PADS[1] - PADS[0]) / 2 : 0;
    if (count >= 8)
        return;
    for (i = 0; i < count; i++) {
        device = PADS[0][2 * i];
        info[0] = sizeof info;
        if (COM(device, 0x3C, 2)(device, (u32)info) >= 0)
            for (j = 0, scan.count++; j < 4; j++)
                scan.guid[scan.count - 1][j] = info[1 + j];
    }
    COM(DINPUT, 0x10, 5)(DINPUT, 4, (u32)pad_found, (u32)&scan, 1);
    for (i = count; PADS[0] && i < (u32)(PADS[1] - PADS[0]) / 2; i++) {
        device = PADS[0][2 * i], pad = PADS[0][2 * i + 1];
        COM(device, 0x2C, 2)(device, 0x75C12C);
        COM(device, 0x34, 3)(device, GAME_WINDOW, 5);
        COM(device, 0x18, 3)(device, 1, (u32)buffer);
        caps[0] = sizeof caps;
        COM(device, 0x0C, 2)(device, (u32)caps);
        COM(device, 0x10, 4)(device, 0x5E4570, (u32)(PADS[0] + 2 * i), 3);
        info[0] = sizeof info;
        COM(device, 0x3C, 2)(device, (u32)info);
        PAD_INIT(pad, (const char *)info + 0x12C, caps[3], caps[4], caps[5], 0.4f);
        PAD_NUMBER(pad, i);
        note("pad: added device", i);
    }
}













#define FPS_LIMITER 0x5CA36D
#define SCENE (*(volatile u32 *)0x7C8518)
#define BATTLE_DRAW ((void (*)(void))0x4B0D60)




#define FLASH_DRAW ((void (*)(void))0x55DF00)
#define WINDOWS_DRAW ((void (*)(void))0x550F20)
#define NOTICES_DRAW ((void (*)(void))0x551680)
#define FRAME_END_FROM_FADES ((void (*)(void))0x4010C9)
#define FADES ((u8 *)0xC27620)
#define FADES_BYTES 12
#define CAMERA_REBUILD ((void (*)(u32))0x4F9BD0)
#define CAMERA0 0x9DA958
#define CAMERA_POSE(camera) ((float *)((camera) + 0x1C0))


#define SCENE_BEGUN (*(volatile u32 *)0xCB9280)
#define SCENE_BEGIN ((u32 (__attribute__((thiscall)) *)(u32, u32, u32, float, u32))0x5CA290)
#define SCENE_BEGAN ((void (*)(void))0x508740)
#define DRAW_PREPARED ((u32 (*)(void))0x57F7E0)
#define DRAW_PREPARE ((void (*)(void))0x5801C0)
#define DRAW_PREPARE_2 ((void (*)(u32))0x57F7B0)



#define FX_MANAGER 0x822D88
#define FX_MODEL_COUNT (*(volatile u32 *)(FX_MANAGER + 0x6954))
#define FX_MODEL(i) ((volatile u32 *)(FX_MANAGER + 0x4954 + 8 * (i)))
#define FX_MODELS_RELEASE ((void (__attribute__((thiscall)) *)(u32))0x47F2A0)



static volatile u32 *const deferred_count[4] = {
    (volatile u32 *)0x9B28B4, (volatile u32 *)0x9B2EF8, (volatile u32 *)0x9B3004, (volatile u32 *)0x9B3648};


#define HUD_TARGETS (*(volatile u16 *)(0xC2FCF8 + 0x1C902))
/* Battle HUD state the HUD steps once per draw (0x573CF0's step block), so the extra frame would step it again:
 * the full-Musou pulse (gauge +0xA), the life trail and team-switch slide (0x5792C0), the KO bump and hundreds
 * flash (0x574AA0), the target officer's gauge timer and drain (0x574F40), the HUD pop timers (0x573AC0) and an
 * overlay counter (0x58EB20). Put back after the extra frame, they step once per tick. */
#define HUD_STEPS X(0xC52588, 0x3C) X(0xC4D1E8, 0x10) X(0xC54618, 0x20) X(0xC549F8, 0x0C) X(0xC5D478, 4)
#define X(at, n) {at, n},
static const struct { u32 at, bytes; } hud_steps[] = {HUD_STEPS};
#undef X
#define X(at, n) + n
enum { HUD_STEP_BYTES = 0 HUD_STEPS };
#undef X



#define RENDER_RING (*(volatile u32 *)(0xC884E8 + 0x5140))





#define PACKET_INDEX (*(volatile u32 *)0x140A290)
#define PACKET_BUFFERS ((volatile u32 *)0xE01360)




#define TRAILS 6
#define TRAIL(i) ((u8 *)0x854970 + 0x17C0 * (i))
#define TRAIL_COPY(t, c) ((t) + 0x40 + 0xBC0 * (c))
#define TRAIL_BYTES 0xBC0
#define TRAIL_READ (*(volatile u32 *)0x140A290 & 1)
static u8 trail_saved[TRAILS][TRAIL_BYTES], *trail_filled[TRAILS], trail_newest[TRAILS];



#define SCENE_CAMERA_BUILD ((void (*)(void *, u32))0x4F98B0)
#define SCENE_CAMERA0 ((const u8 *)0x9CAA50)
#define FAR_CAMERA ((u8 *)0xC884E8 + 0x1EC + 4)
#define FAR_CAMERA_BYTES 0x154
static u8 far_camera[FAR_CAMERA_BYTES];





#define CHARACTERS 255
#define CHARACTER(p) (((p) - 0x9DE420) / 0x2200)
#define CHARACTER_AT(i) ((const float *)(0x9DE420 + 0x2200 * (i) + 0xC0))
static float character_prev[CHARACTERS][3], head_prev[CHARACTERS][3];
static u32 head_stamp[CHARACTERS], head_of = CHARACTERS;
static u32 fps = 60;
static u32 fps_ok;
static u32 extra;
static u32 hfr_busy;
static u32 hfr_drawn;
static u32 fx_tick_from;
static u32 fx_queued;
static u32 fx_kept[0x400][2];
static float cam_prev[8], cam_cur[8];
static int have_prev;



static struct { char magic[16]; u32 frames, extras, limiter, presents, lerped, mismatched; u32 interval[40]; } hfr_stats = {"ENRT-HFR-STATS"};
#ifdef ENRT_TEST
static void trace(u32 type, u32 a, u32 b, u32 c, u32 d, u32 e, u32 g, const void *data, u32 bytes);
static void trace_start(void);
static void trace_present(void);
static void trace_hooks(void *device);
#else
#define trace(...) ((void)0)
#define trace_start() ((void)0)
#define trace_present() ((void)0)
#define trace_hooks(device) ((void)0)
#endif
static const char *const fps_label[2] = {"FRAME RATE", "\x83\x74\x83\x8c\x81\x5b\x83\x80\x83\x8c\x81\x5b\x83\x67"};
static const char *const fps_value[2][2] = {{"60 FPS", "120 FPS"}, {"60FPS", "120FPS"}};

static void limiter(u8 rate)
{
    if (*(const u8 *)(FPS_LIMITER + 1) != rate) {
        write_at(FPS_LIMITER + 1, &rate, 1);
        note("hfr: limiter", rate);
    }
    hfr_stats.limiter = rate;
}



static void fps_input(u32 repeat)
{
    WCHAR name[MAX_PATH];
    if (!fps_ok || !(repeat & 0x0C))
        return;
    fps = fps == 120 ? 60 : 120;
    WritePrivateProfileStringW(L"display", L"fps", fps == 120 ? L"120" : L"60", path(name, L"settings.ini"));
    GAME_SOUND(1, 0, 0x2CDC, 0x2CDC, 0x1000);
}

#define UPLOADS 1024
static float uploads[UPLOADS][256 * 4];
static u32 upload_key[UPLOADS], upload_count, upload_index;



static void trail_fill(int mid)
{
    u32 i, v, k;
    for (i = 0; i < TRAILS; i++) {
        u8 *t = TRAIL(i), *out = TRAIL_COPY(t, TRAIL_READ), *now = TRAIL_COPY(t, t[0xB] & 1);
        int blend = mid && t[0xB] != trail_newest[i];
        const float *prev = (const float *)(out == now ? TRAIL_COPY(t, ~t[0xB] & 1) : trail_saved[i]);
        float *xyz = (float *)out;
        trail_filled[i] = 0;
        if (!t[0xC] || (out == now && !blend))
            continue;
        CopyMemory(trail_saved[i], out, TRAIL_BYTES);
        trail_filled[i] = out;
        if (out != now)
            CopyMemory(out, now, TRAIL_BYTES);
        if (blend)
            for (v = 0; v < t[9]; v++)
                for (k = 16 + 12 * v; k < 19 + 12 * v; k++)
                    xyz[k] = (prev[k] + xyz[k]) * 0.5f;
    }
}

static void trail_put_back(void)
{
    u32 i;
    for (i = 0; i < TRAILS; i++)
        if (trail_filled[i])
            CopyMemory(trail_filled[i], trail_saved[i], TRAIL_BYTES);
}


static u32 hfr_draw(void)
{
    float *cam = CAMERA_POSE(CAMERA0);
    u32 i, deferred[4], ring, parity = PACKET_INDEX, buffer;
    u16 targets;
    u8 fades[FADES_BYTES], hud[HUD_STEP_BYTES], *h;
    if (fps != 120 || hfr_busy || SCENE != 2)
        return 0;
    for (i = 0; i < 8; i++)
        cam_cur[i] = cam[i];
    hfr_busy = 1;
    if (have_prev && *(const u8 *)(CAMERA0 + 0x230) & 1) {
        for (i = 0; i < 7; i++)
            if (i != 3)
                cam[i] = (cam_prev[i] + cam_cur[i]) * 0.5f;
        CAMERA_REBUILD(CAMERA0);
    }
    extra = 1;
    upload_index = 0;
    fx_queued = FX_MODEL_COUNT;
    for (i = 0; i < 4; i++)
        deferred[i] = *deferred_count[i];
    targets = HUD_TARGETS;
    for (i = 0, h = hud; i < sizeof hud_steps / sizeof *hud_steps; h += hud_steps[i++].bytes)
        CopyMemory(h, (const void *)hud_steps[i].at, hud_steps[i].bytes);
    ring = RENDER_RING;
    CopyMemory(far_camera, FAR_CAMERA, FAR_CAMERA_BYTES);
    SCENE_CAMERA_BUILD((void *)SCENE_CAMERA0, CAMERA0);
    CopyMemory(FAR_CAMERA, SCENE_CAMERA0 + 4, FAR_CAMERA_BYTES);
    trail_fill(1);
    BATTLE_DRAW();
    trail_put_back();
    FLASH_DRAW();
    WINDOWS_DRAW();
    NOTICES_DRAW();
    CopyMemory(fades, FADES, FADES_BYTES);
    FRAME_END_FROM_FADES();
    CopyMemory(FADES, fades, FADES_BYTES);
    if (PACKET_INDEX != parity) {
        buffer = PACKET_BUFFERS[0], PACKET_BUFFERS[0] = PACKET_BUFFERS[1], PACKET_BUFFERS[1] = buffer;
        PACKET_INDEX = parity;
    }
    CopyMemory(FAR_CAMERA, far_camera, FAR_CAMERA_BYTES);
    for (i = 0; i < 4; i++)
        *deferred_count[i] = deferred[i];
    HUD_TARGETS = targets;
    for (i = 0, h = hud; i < sizeof hud_steps / sizeof *hud_steps; h += hud_steps[i++].bytes)
        CopyMemory((void *)hud_steps[i].at, h, hud_steps[i].bytes);
    RENDER_RING = ring;
    extra = 0;
    hfr_stats.extras++;
    for (i = 0; i < 8; i++)
        cam[i] = cam_prev[i] = cam_cur[i];
    have_prev = 1;
    if (*(const u8 *)(CAMERA0 + 0x230) & 1)
        CAMERA_REBUILD(CAMERA0);
    if (!SCENE_BEGUN) {
        SCENE_BEGUN = SCENE_BEGIN(0x7A4568, 0, 0, 1.0f, 0);
        SCENE_BEGAN();
    }
    if (*(const u32 *)0x801AA8 & 0x1000 && DRAW_PREPARED()) {
        DRAW_PREPARE();
        DRAW_PREPARE_2(0);
    }
    upload_index = 0;
    trail_fill(0);
    BATTLE_DRAW();
    trail_put_back();
    for (i = 0; i < TRAILS; i++)
        trail_newest[i] = TRAIL(i)[0xB];
    for (i = 0; i < CHARACTERS; i++)
        CopyMemory(character_prev[i], CHARACTER_AT(i), sizeof *character_prev);
    upload_count = upload_index < UPLOADS ? upload_index : UPLOADS;
    hfr_stats.frames++;
    hfr_busy = 0, hfr_drawn = 1;
    return 1;
}







typedef long (__stdcall *vs_const_fn)(void *, u32, const float *, u32);
typedef long (__stdcall *create_device_fn)(void *, u32, u32, void *, u32, void *, void **);
typedef long (__stdcall *present_fn)(void *, const void *, const void *, void *, const void *);
static vs_const_fn real_vs_const;
static present_fn real_present;
static create_device_fn real_create_device;
static float lerped[256 * 4];







#define PALETTE_STEP 200.0f
static float palette_gap(const float *a, const float *b, u32 count, float least)
{
    float gap = 0, g;
    u32 i;
    for (i = 0; i < count * 4 && gap < least; i++)
        if ((g = a[i] > b[i] ? a[i] - b[i] : b[i] - a[i]) > gap)
            gap = g;
    return gap;
}

static u32 pair(u32 n, u32 reg, const float *data, u32 count)
{
    u32 d, k, m, key = reg << 16 | count, best = upload_count;
    float gap, least = PALETTE_STEP;
    for (d = 0; d < upload_count && least > 0; d++)
        for (k = 0; k <= (d != 0); k++) {
            m = k ? n - d : n + d;
            if (m < upload_count && upload_key[m] == key && (gap = palette_gap(uploads[m], data, count, least)) < least)
                best = m, least = gap;
        }
    return best;
}


static void halfway(float *point, const float *prev, const float *now)
{
    u32 k;
    for (k = 0; k < 3; k++)
        if (prev[k] - now[k] > PALETTE_STEP || now[k] - prev[k] > PALETTE_STEP)
            return;
    for (k = 0; k < 3; k++)
        point[k] += (prev[k] - now[k]) * 0.5f;
}



static void head_point(float *point, const float *bone)
{
    u32 i = head_of;
    if (!hfr_busy || i >= CHARACTERS)
        return;
    if (!extra) {
        CopyMemory(head_prev[i], bone, sizeof *head_prev);
        head_stamp[i] = hfr_stats.frames + 1;
    } else if (have_prev && head_stamp[i] == hfr_stats.frames)
        halfway(point, head_prev[i], bone);
}


static void marker_point(float *point)
{
    u32 i;
    if (!extra || !have_prev)
        return;
    for (i = 0; i < CHARACTERS; i++)
        if (CHARACTER_AT(i)[0] == point[0] && CHARACTER_AT(i)[2] == point[2]) {
            halfway(point, character_prev[i], CHARACTER_AT(i));
            return;
        }
}

static long __stdcall vs_const(void *device, u32 reg, const float *data, u32 count)
{
    u32 n = upload_index, m = UPLOADS, i;
    if (hfr_busy && count <= 256 && extra && count % 3 == 0)
        m = pair(n, reg, data, count);
    trace(1, reg, count, extra, (u32)__builtin_return_address(0), m, 0, data, count * 16);
    if (!hfr_busy || count > 256)
        return real_vs_const(device, reg, data, count);
    upload_index++;
    if (!extra) {
        if (n < UPLOADS) {
            upload_key[n] = reg << 16 | count;
            for (i = 0; i < count * 4; i++)
                uploads[n][i] = data[i];
        }
    } else if (count % 3 == 0) {
        if (m < upload_count) {
            for (i = 0; i < count * 4; i++)
                lerped[i] = (uploads[m][i] + data[i]) * 0.5f;
            data = lerped;
            hfr_stats.lerped++;
        } else
            hfr_stats.mismatched++;
    }
    return real_vs_const(device, reg, data, count);
}

static void hook_slot(void *object, u32 slot, void *fn, void *real)
{
    void **vtable = *(void ***)object;
    DWORD old;
    *(void **)real = vtable[slot];
    if (VirtualProtect(vtable + slot, 4, PAGE_READWRITE, &old)) {
        vtable[slot] = fn;
        VirtualProtect(vtable + slot, 4, old, &old);
    }
}

#ifdef ENRT_TEST






#define TRACE_CAP (64u << 20)
#define TRACE_DATA_MAX 0x40000
static struct { char magic[16]; volatile u32 armed, frames, bytes, fps; } hfr_trace = {"ENRT-HFR-TRACE"};
static u8 *trace_buf;
static u32 trace_size, trace_on;
static void trace(u32 type, u32 a, u32 b, u32 c, u32 d, u32 e, u32 g, const void *data, u32 bytes)
{
    u32 *r, i;
    if (bytes > TRACE_DATA_MAX)
        bytes = TRACE_DATA_MAX;
    if (!trace_on || !data)
        bytes = 0;
    if (!trace_on || trace_size + 32 + (bytes + 3) / 4 * 4 > TRACE_CAP)
        return;
    r = (u32 *)(trace_buf + trace_size);
    r[0] = type, r[1] = bytes, r[2] = a, r[3] = b, r[4] = c, r[5] = d, r[6] = e, r[7] = g;
    for (i = 0; i < bytes; i++)
        ((u8 *)(r + 8))[i] = ((const u8 *)data)[i];
    trace_size += 32 + (bytes + 3) / 4 * 4;
}

static void trace_start(void)
{
    if (hfr_trace.armed != 1 || trace_on)
        return;
    if (!trace_buf && !(trace_buf = VirtualAlloc(0, TRACE_CAP, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE))) {
        hfr_trace.armed = 0;
        return;
    }
    trace_size = 0, hfr_trace.frames = 0, hfr_trace.armed = 2, trace_on = 1;
}

static void trace_present(void)
{
    WCHAR name[MAX_PATH];
    HANDLE file;
    DWORD written;
    if (hfr_trace.fps && (trace_on ? hfr_trace.frames == 3 : hfr_trace.armed != 1))
        fps = hfr_trace.fps, hfr_trace.fps = 0;
    if (!trace_on) {
        trace_start();
        return;
    }
    trace(10, hfr_trace.frames, extra, 0, 0, 0, 0, 0, 0);
    if (++hfr_trace.frames < 6)
        return;
    trace_on = 0;
    file = CreateFileW(path(name, L"d3d-trace.bin"), GENERIC_WRITE, 0, 0, CREATE_ALWAYS, 0, 0);
    if (file != INVALID_HANDLE_VALUE)
        WriteFile(file, trace_buf, trace_size, &written, 0), CloseHandle(file);
    hfr_trace.bytes = trace_size, hfr_trace.armed = 0;
}

static u32 prim_vertices(u32 type, u32 count)
{
    return type == 1 ? count : type == 2 ? 2 * count : type == 3 ? count + 1 : type == 4 ? 3 * count : count + 2;
}

typedef long (__stdcall *fn2)(void *, u32);
typedef long (__stdcall *fn3)(void *, u32, u32);
typedef long (__stdcall *fn4)(void *, u32, u32, u32);
typedef long (__stdcall *fn5)(void *, u32, u32, u32, u32);
typedef long (__stdcall *fn7)(void *, u32, u32, u32, u32, u32, u32);
typedef long (__stdcall *fn9)(void *, u32, u32, u32, u32, u32, u32, u32, u32);
static fn2 real_set_vs, real_set_ps, real_set_indices, real_set_decl, real_set_fvf, real_set_viewport;
static fn3 real_set_texture, real_set_transform, real_set_target;
static fn4 real_draw;
static fn5 real_draw_up, real_set_stream;
static fn7 real_draw_indexed;
static fn9 real_draw_indexed_up;
static long (__stdcall *real_vb_lock)(void *, u32, u32, void **, u32);
static long (__stdcall *real_vb_unlock)(void *);
static struct { void *vb, *data; u32 offset, size, flags, caller; } locks[64];

static long __stdcall t_set_vs(void *d, u32 a) { trace(7, a, 0, 0, 0, 0, 0, 0, 0); return real_set_vs(d, a); }
static long __stdcall t_set_ps(void *d, u32 a) { trace(15, a, 0, 0, 0, 0, 0, 0, 0); return real_set_ps(d, a); }
static long __stdcall t_set_indices(void *d, u32 a) { trace(16, a, 0, 0, 0, 0, 0, 0, 0); return real_set_indices(d, a); }
static long __stdcall t_set_decl(void *d, u32 a) { trace(14, a, 0, 0, 0, 0, 0, 0, 0); return real_set_decl(d, a); }
static long __stdcall t_set_fvf(void *d, u32 a) { trace(17, a, 0, 0, 0, 0, 0, 0, 0); return real_set_fvf(d, a); }
static long __stdcall t_set_viewport(void *d, u32 a) { trace(18, 0, 0, 0, 0, 0, 0, (void *)a, 24); return real_set_viewport(d, a); }
static long __stdcall t_set_texture(void *d, u32 a, u32 b) { trace(9, a, b, 0, 0, 0, 0, 0, 0); return real_set_texture(d, a, b); }
static long __stdcall t_set_transform(void *d, u32 a, u32 b) { trace(6, a, 0, 0, 0, 0, 0, (void *)b, 64); return real_set_transform(d, a, b); }
static long __stdcall t_set_target(void *d, u32 a, u32 b) { trace(13, a, b, 0, 0, 0, 0, 0, 0); return real_set_target(d, a, b); }

static u32 stack_calls(const u32 *sp, u32 *out)
{
    u32 i, n = 0;
    for (i = 0; i < 256 && n < 16; i++)
        if (sp[i] > 0x401006 && sp[i] < 0x6C0000
            && (((const u8 *)sp[i])[-5] == 0xE8 || ((const u8 *)sp[i])[-2] == 0xFF || ((const u8 *)sp[i])[-3] == 0xFF
                || ((const u8 *)sp[i])[-6] == 0xFF))
            out[n++] = sp[i];
    return n;
}
static long __stdcall t_draw(void *d, u32 a, u32 b, u32 c)
{
    u32 calls[16];
    trace(2, a, b, c, 0, 0, 0, calls, 4 * stack_calls((const u32 *)&d, calls));
    return real_draw(d, a, b, c);
}
static long __stdcall t_draw_up(void *d, u32 a, u32 b, u32 c, u32 e)
{
    trace(4, a, b, e, 0, 0, 0, (void *)c, prim_vertices(a, b) * e);
    return real_draw_up(d, a, b, c, e);
}
static long __stdcall t_draw_indexed(void *d, u32 a, u32 b, u32 c, u32 e, u32 g, u32 h)
{
    u32 calls[16];
    trace(3, a, b, c, e, g, h, calls, 4 * stack_calls((const u32 *)&d, calls));
    return real_draw_indexed(d, a, b, c, e, g, h);
}
static long __stdcall t_draw_indexed_up(void *d, u32 a, u32 b, u32 c, u32 e, u32 g, u32 h, u32 i, u32 j)
{
    trace(5, a, b, c, e, h, j, (const u8 *)i + b * j, c * j);
    return real_draw_indexed_up(d, a, b, c, e, g, h, i, j);
}
static long __stdcall t_vb_lock(void *vb, u32 offset, u32 size, void **data, u32 flags)
{
    long r = real_vb_lock(vb, offset, size, data, flags);
    u32 i, desc[6];
    if (trace_on && r >= 0 && !size && ((long (__stdcall *)(void *, u32 *))(*(void ***)vb)[13])(vb, desc) >= 0)
        size = desc[4] - offset;
    if (trace_on && r >= 0)
        for (i = 0; i < 64; i++)
            if (!locks[i].vb) {
                locks[i].vb = vb, locks[i].data = *data, locks[i].offset = offset, locks[i].size = size, locks[i].flags = flags;
                locks[i].caller = (u32)__builtin_return_address(0);
                break;
            }
    return r;
}
static long __stdcall t_vb_unlock(void *vb)
{
    u32 i;
    for (i = 0; i < 64; i++)
        if (locks[i].vb == vb) {
            trace(19, (u32)vb, locks[i].offset, locks[i].size, locks[i].flags, locks[i].caller, 0, locks[i].data, locks[i].size);
            locks[i].vb = 0;
            break;
        }
    return real_vb_unlock(vb);
}
static long __stdcall t_set_stream(void *d, u32 a, u32 b, u32 c, u32 e)
{
    trace(8, a, b, c, e, 0, 0, 0, 0);
    if (b && !real_vb_lock)
        hook_slot((void *)b, 11, (void *)t_vb_lock, &real_vb_lock), hook_slot((void *)b, 12, (void *)t_vb_unlock, &real_vb_unlock);
    return real_set_stream(d, a, b, c, e);
}

static void trace_hooks(void *device)
{
    hook_slot(device, 92, (void *)t_set_vs, &real_set_vs);
    hook_slot(device, 107, (void *)t_set_ps, &real_set_ps);
    hook_slot(device, 104, (void *)t_set_indices, &real_set_indices);
    hook_slot(device, 87, (void *)t_set_decl, &real_set_decl);
    hook_slot(device, 89, (void *)t_set_fvf, &real_set_fvf);
    hook_slot(device, 47, (void *)t_set_viewport, &real_set_viewport);
    hook_slot(device, 65, (void *)t_set_texture, &real_set_texture);
    hook_slot(device, 44, (void *)t_set_transform, &real_set_transform);
    hook_slot(device, 37, (void *)t_set_target, &real_set_target);
    hook_slot(device, 81, (void *)t_draw, &real_draw);
    hook_slot(device, 83, (void *)t_draw_up, &real_draw_up);
    hook_slot(device, 82, (void *)t_draw_indexed, &real_draw_indexed);
    hook_slot(device, 84, (void *)t_draw_indexed_up, &real_draw_indexed_up);
    hook_slot(device, 100, (void *)t_set_stream, &real_set_stream);
}
#endif

static long __stdcall present(void *device, const void *from, const void *to, void *window, const void *dirty)
{
    static LARGE_INTEGER last, now, rate;
    QueryPerformanceCounter(&now);
    if (!rate.QuadPart)
        QueryPerformanceFrequency(&rate);
    else if (last.QuadPart) {
        if (*(const u8 *)(FPS_LIMITER + 1) == 120)
            while (now.QuadPart - last.QuadPart < rate.QuadPart / 120)
                Sleep(0), QueryPerformanceCounter(&now);
        LONGLONG half_ms = (now.QuadPart - last.QuadPart) * 2000 / rate.QuadPart;
        hfr_stats.interval[half_ms < 39 ? half_ms : 39]++;
    }
    last = now;
    hfr_stats.presents++;
    trace_present();
    return real_present(device, from, to, window, dirty);
}

/* A display Reset (resolution, full screen, a lost device) after a battle left every menu sprite a grey box. Before
 * the Reset the engine forgets its current shaders (0x605BE0, from 0x5C9120) and trusts the Reset to unbind them,
 * as D3D9 says it does; DXVK keeps them bound, so the menus, which unbind only what the engine thinks is bound,
 * drew through the battle's shaders. Unbinding both after the Reset matches what the engine expects. */
typedef long (__stdcall *reset_fn)(void *, void *);
static reset_fn real_reset;

static long __stdcall reset(void *device, void *params)
{
    long result = real_reset(device, params);
    if (result >= 0)
        COM(device, 0x170, 2)(device, 0), COM(device, 0x1AC, 2)(device, 0);  /* SetVertexShader, SetPixelShader */
    return result;
}

static long __stdcall create_device(void *d3d, u32 adapter, u32 type, void *window, u32 flags, void *params, void **device)
{
    long result = real_create_device(d3d, adapter, type, window, flags, params, device);
    if (result >= 0 && !real_reset)
        hook_slot(*device, 16, (void *)reset, &real_reset);
    if (result >= 0 && fps_ok && !real_vs_const)
        hook_slot(*device, 94, (void *)vs_const, &real_vs_const), hook_slot(*device, 17, (void *)present, &real_present),
            trace_hooks(*device);
    return result;
}

static u32 english_hook(u32 id, struct frame *f)
{
    switch (id) {
    case 1:
    case 2: {
        u32 i, before, width = single_width(f->ret), align = f->ret == ARMY_TITLE_OUTLINE || f->ret == ARMY_TITLE;
        if (f->ret == 0x54E76F)
            width = MESSAGE_WIDTH > *(volatile short *)0xC2AF44 ?
                MESSAGE_WIDTH - *(volatile short *)0xC2AF44 : 0;
        else if (f->ret == 0x55194F)
            width = NOTICE_BOX_WIDTH > *(volatile short *)0xC2B08C ?
                NOTICE_BOX_WIDTH - *(volatile short *)0xC2B08C : 0;
        else if (f->ret == 0x54F8CF)
            width = f->arg[4] == 0xC2D090 ? windows[0][1] : f->arg[4] == 0xC2D1B8 ? windows[1][1] : 0;
        else if (f->ret == BANNER_TEXT_DRAW)
            width = banner_width;
        else if (f->ret == PROMPT_DRAW) {
            u32 total = *(volatile u32 *)0xC2F214;
            width = total > 0x83 ? total - 0x83 : 0;
            align = 2;
        }
#if ENRT_CELL != 24


        static u8 scaled[2];
        if (FONT_CELL[0] != scaled[0] || FONT_CELL[1] != scaled[1])
            FONT_CELL[0] = scaled[0] = (u8)(FONT_CELL[0] * ENRT_CELL / 24),
            FONT_CELL[1] = scaled[1] = (u8)(FONT_CELL[1] * ENRT_CELL / 24);
#endif
        if (f->ret == WEAPON_READING_DRAW || f->ret == WEAPON_INFO_READING_DRAW)
            return 1;
        f->arg[0] = before = text(f->arg[0], id, f);
        if (id == 1)
            row_columns(f);
        if (id == 1 && f->ret == ARMY_NAME_DRAW && f->arg[0]) {
            army_line(f);
            width = army_width((const char *)f->arg[0]);
        } else if (id == 2 && f->ret == ARMY_NAME_ADVANCE && f->arg[0]) {
            f->eax = army_width((const char *)f->arg[0]);
            return 1;
        }
        for (i = 0; i < sizeof key_first / sizeof *key_first; i++)
            if (f->ret == key_first[i][0]) {
                inner++;
                GAME_MEASURE((const char *)f->arg[0], (u32 *)f->arg[1], (u32 *)f->arg[2]);
                inner--;
                f->eax = *(u32 *)f->arg[1] = (u32)-KEY_GAP;
                return 1;
            } else if (f->ret == key_first[i][1])
                f->arg[1] += KEY_TEXT;
        if (id == 2 && f->ret == WINDOW_MEASURE && f->arg[1]) {
            u32 slot = (f->esi - WINDOWS) / WINDOW_SIZE;
            u8 pitch = FONT_CELL[2];
            if (slot == 1 || slot == 2)
                FONT_CELL[2] = WINDOW_PITCH;
            inner++;
            GAME_MEASURE((const char *)f->arg[0], (u32 *)f->arg[1], (u32 *)f->arg[2]);
            inner--;
            FONT_CELL[2] = pitch;
            if ((slot == 1 || slot == 2) && *(u32 *)f->arg[1] > windows[slot - 1][1])
                *(u32 *)f->arg[1] = windows[slot - 1][1];
            f->eax = *(u32 *)f->arg[1];
            return 1;
        }
        if (f->ret == SCENARIO_TEXT_DRAW)
            return fit_lines(f, SCENARIO_TEXT_WIDTH, SCENARIO_TEXT_LINES, SCENARIO_TEXT_CELL, SCENARIO_TEXT_PITCH);
        if (f->ret == DREAM_SUMMARY_DRAW) {
            f->arg[2] = DREAM_SUMMARY_Y;
            return fit_lines(f, DREAM_SUMMARY_WIDTH, DREAM_SUMMARY_LINES, 0, DREAM_SUMMARY_PITCH);
        }
        for (i = 0; i < sizeof described / sizeof *described; i++)
            if (f->ret == described[i][0])
                return description(f, described[i][1], DESCRIBED_PITCH);
        for (i = 0; i < sizeof boxed / sizeof *boxed; i++)
            if (f->ret == boxed[i][0]) {
                width = boxed[i][1];
                f->arg[0] = (u32)reflow_wrap(f->ret, (const char *)f->arg[0], boxed[i][1], line_text, sizeof line_text);
            }
        for (i = 0; i < sizeof pages / sizeof *pages; i++)
            if (f->ret == pages[i][0])
                return page(f, pages[i]);
        if (f->ret == HISTORY_DRAW) {
            width = HISTORY_WIDTH;
            f->arg[0] = (u32)reflow_wrap(f->ret, history((const char *)f->arg[0]), HISTORY_WIDTH, line_text, sizeof line_text);
        }
        if (id == 1 && !inner && !width && f->arg[0] == before && f->arg[0] && f->ret != PROMPT_DRAW)
            width = fit_box(f);
#ifdef ENRT_TEST
        if (id == 1 && f->arg[0])
            test_text(id, f, (const char *)f->arg[0], f->arg[1], f->arg[2], measure((const char *)f->arg[0]));
#endif
        if (id == 1 && !inner && f->ret == 0x54F8CF && width && f->arg[0]) {
            u8 pitch = FONT_CELL[2];
            FONT_CELL[2] = WINDOW_PITCH;
            draw_fit((const char *)f->arg[0], f->arg[1], f->arg[2], f->arg[3], f->arg[4], width, 0);
            FONT_CELL[2] = pitch;
            return 1;
        }
        if (id == 1 && !inner && width && f->arg[0] && squish && text_fit_latin((const char *)f->arg[0])
            && measure((const char *)f->arg[0]) > width) {
            draw_fit((const char *)f->arg[0], f->arg[1], f->arg[2], f->arg[3], f->arg[4], width, align);
            return 1;
        }
        break;
    }
    case 44: {
        static char cue[512];
        u32 s = text(f->ecx, id, f), width, breaks;
        u8 w = FONT_CELL[0], h = FONT_CELL[1];
        const char *c;
        if (s != f->ecx) {
            FONT_CELL[0] = FONT_CELL[1] = 28;


            if ((width = measure((const char *)s)) > CAPTION_WIDTH)
                for (width /= 2; width <= CAPTION_WIDTH; width += 8) {
                    f->ecx = (u32)wrap((const char *)s, width, cue, sizeof cue);
                    for (c = (const char *)f->ecx, breaks = 0; *c; c++)
                        breaks += *c == '\n';
                    if (breaks < 2)
                        break;
                }
            else
                f->ecx = s;
            caption_run_width = measure((const char *)f->ecx);
            FONT_CELL[0] = w, FONT_CELL[1] = h;
        } else
            caption_run_width = 0;
        break;
    }
    case 4:
        f->arg[0] = text(f->arg[0], id, f);
#ifdef ENRT_TEST
        if (f->arg[0])
            test_text(id, f, (const char *)f->arg[0], f->arg[1], f->arg[2], 0);
#endif
        if (!inner && f->arg[0] && squish && text_fit_latin((const char *)f->arg[0])
            && (caption_run_width ? caption_run_width : measure((const char *)f->arg[0])) > CAPTION_WIDTH) {
            draw_fit_at((text_draw)0x54BBB0, (const char *)f->arg[0], f->arg[1], f->arg[2],
                        f->arg[3], f->arg[4], CAPTION_WIDTH, 1, caption_run_width);
            return 1;
        }
        break;
    case 7:
        f->arg[0] = text(f->arg[0], id, f);
        if (f->arg[0])
            fit_message((const char *)f->arg[0], f->arg[1] ? 70 : 0);
#ifdef ENRT_TEST
        test_set(id, f, f->arg[1], (const char *)f->arg[0]);
#endif
        break;
    case 9:
        f->arg[2] = voiced_line(f->arg[2], id, f);
        break;
    case 3:
        if (!inner && f->arg[0] != f->arg[1])
            f->arg[0] = (u32)wrap((const char *)text(f->arg[0], id, f), f->arg[2], wrapped, sizeof wrapped);
        break;
    case 5:
        f->arg[1] = text(f->arg[1], id, f);
        if (f->arg[1] == NAME_FORMAT_TITLED || f->arg[1] == NAME_FORMAT_PLAIN) {

            u32 *name = f->arg + (f->arg[1] == NAME_FORMAT_TITLED ? 4 : 3);
            u32 a = part_index(name[0]), b = part_index(name[1]);
            if (a != (u32)-1 && b != (u32)-1) {
                name[0] = (u32)compose(printed_name, a, b);
                f->arg[1] = (u32)(f->arg[1] == NAME_FORMAT_TITLED ? "%s %s %s" : "%s %s");
            }
        }
        translate_args((const u8 *)f->arg[1], f->arg + 2, 9, f);
        break;
    case 6:
        f->arg[1] = text(f->arg[1], id, f);
        if (f->arg[1] && !f->arg[2] && (f->arg[0] == 1 || f->arg[0] == 2)) {
            FONT_CELL[0] = FONT_CELL[1] = 24;
            f->arg[1] = (u32)wrap((const char *)f->arg[1], windows[f->arg[0] - 1][0],
                                  window_text, sizeof window_text);
        }
#ifdef ENRT_TEST
        test_set(id, f, f->arg[0], (const char *)f->arg[1]);
#endif
        break;
    case 10:
        f->eax = text(f->eax, id, f);
        break;
    case 11:
        f->edx = width24(PROMPT_TEXT) + 0x83;
        break;
    case 12:
        f->eax = (u32)compose((char *)f->arg[0], f->arg[1], f->arg[2]);
        return 1;
    case 13: {
        char name[96];
        u32 *quad = (u32 *)f->arg[5], i;
        for (i = 0; i < 8; i++, quad += 20)
            quad[12] = quad[13] = quad[16] = quad[17] = 0;
        compose(name, f->arg[0], f->arg[1]);
        f->eax = (width24(name) + 23) / 24;
        if ((f->ret == DIALOG_PLATE_1 || f->ret == DIALOG_PLATE_2) && plate_count < 2) {
            struct plate *p = &plates[plate_count++];
            for (i = 0; (p->name[i] = name[i]); i++)
                ;
            p->x = f->arg[2], p->y = f->arg[3], p->layer = f->arg[4];
            p->mode = FONT_MODE, p->colour = FONT_COLOUR, p->alpha = FONT_ALPHA;
        } else
            draw(name, f->arg[2], f->arg[3], f->arg[4]);
        return 1;
    }
    case 26: {
        u32 mode = FONT_MODE, colour = FONT_COLOUR, i;
        u8 w = FONT_CELL[0], h = FONT_CELL[1], alpha = FONT_ALPHA;
        for (i = 0; i < plate_count; i++) {
            FONT_CELL[0] = FONT_CELL[1] = 24;
            FONT_MODE = plates[i].mode, FONT_COLOUR = plates[i].colour, FONT_ALPHA = plates[i].alpha;
            draw(plates[i].name, plates[i].x, plates[i].y, plates[i].layer);
        }
        plate_count = 0;
        FONT_CELL[0] = w, FONT_CELL[1] = h, FONT_MODE = mode, FONT_COLOUR = colour, FONT_ALPHA = alpha;
        break;
    }
    case 27:
        if (RESOLUTION_RAISED != 'E') {
            RESOLUTION_RAISED = 'E';
            if (RESOLUTION < MODE_1080P)
                RESOLUTION = MODE_1080P;
        }
        break;
    case 14:
        *(char *)f->arg[0] = 0;
        f->eax = f->arg[0];
        return 1;
    case 15:
    case 16:
        f->arg[0] = (u32)wrap((const char *)text(f->arg[0], id, f),
                              id == 16 ? NOTICE_BOX_WIDTH : MESSAGE_WIDTH,
                              message_text, sizeof message_text);
        break;
    case 17: {
        char name[96];
        int x = (int)f->arg[2], cells = (int)name_cells(f->arg[0], f->arg[1]);
        compose(name, f->arg[0], f->arg[1]);
        if (f->ret == BANNER_NAME_DRAW && *(volatile u8 *)0xC2AF70 == 5)
            x += 0x93 - 0x85;
        u8 cell_w = FONT_CELL[0], cell_h = FONT_CELL[1];
        if (f->ret == STILL_Z_PLATE) {
            int glyphs = (int)(NAME_GLYPHS(f->arg[0]) + NAME_GLYPHS(f->arg[1])) * 28;
            x += (f->arg[0] == f->arg[1] ? glyphs / 2 : glyphs) / 2 - (int)measure(name) / 2;
        }
        if (f->ret == STILL_PLATE) {
            /* X360 616-wide plates at x 26 and 640; the name is drawn at cell 28 and centred on plate+284 or plate+332. */
            FONT_CELL[0] = 34, FONT_CELL[1] = 28;
            x += 8 - (266 - cells * 24) / 2;
            x += (x < 640 ? 284 : 332) - (int)measure(name) / 2;
        }
        draw_fit(name, x, f->arg[3], f->arg[4], 0,
                 f->ret == STILL_PLATE ? 400 : f->ret == STILL_Z_PLATE ? STILL_Z_LANE
                 : f->ret == BANNER_NAME_DRAW ? banner_width : single_width(f->ret),
                 f->ret == STILL_PLATE || f->ret == STILL_Z_PLATE);
        FONT_CELL[0] = cell_w, FONT_CELL[1] = cell_h;
        f->eax = (width24(name) + 23) / 24;
#ifdef ENRT_LOG
        log_line(id, f, (const u8 *)name, fnv((const u8 *)name));
#endif
        return 1;
    }
    case 32: {



        char name[96];
        const union { u32 u; float f; } *at = (const void *)f->eax, *cell = (const void *)&f->arg[4];
        u32 *quad = (u32 *)f->ecx, i, colour = FONT_COLOUR, depth = TEXT_DEPTH;
        u8 w = FONT_CELL[0], h = FONT_CELL[1], alpha = FONT_ALPHA, size;
        int centre;
        for (i = 0; i < 16; i++, quad += 20)
            quad[12] = quad[13] = quad[16] = quad[17] = 0;
        centre = (int)((at[0].f + name_cells(f->arg[0], f->arg[1]) * (SCREEN_W * 24 / 1280) / 2.0f) * 1280 / SCREEN_W);
        size = (u8)(int)(cell[1].f * 720 / SCREEN_H);
        FONT_CELL[0] = FONT_CELL[1] = size, FONT_COLOUR = f->arg[2], FONT_ALPHA = (u8)f->arg[3];
        TEXT_DEPTH = 0xFFFE - (int)at[2].f;
        compose(name, f->arg[0], f->arg[1]);
        draw(name, centre - (int)measure(name) / 2, (int)(at[1].f * 720 / SCREEN_H), 0);
        FONT_CELL[0] = w, FONT_CELL[1] = h, FONT_COLOUR = colour, FONT_ALPHA = alpha, TEXT_DEPTH = depth;
        return 1;
    }
    case 33:
    case 34:
    case 35: {
        /* X360 banner width: text room plus inset, right edge 1280; the stock slide is rescaled to it. */
        static const int stock[] = {367, 294, 203}, inset[] = {224, 147, 157};
        u32 type = *(volatile u8 *)0xC2AF70, s = id - 33, room = 126;
        const char *label = *(const char *const *)(0x6FB7C0 + 4 * *(volatile u8 *)0xC2AF73);
        if (type == 1 || type == 2 || type == 5)
            room = 300;
        else if (s == 1 && label) {
            u8 w = FONT_CELL[0], h = FONT_CELL[1];
            FONT_CELL[0] = BANNER_TEXT_CELL, FONT_CELL[1] = 24;
            room = measure(enrt_translate(label));
            FONT_CELL[0] = w, FONT_CELL[1] = h;
        }
        banner_width = room;
        f->esi = (u32)(1280 - (int)(room + inset[s])
                       + ((int)f->esi - (1280 - stock[s])) * (int)(room + inset[s]) / stock[s]);
        break;
    }
    case 42:
    case 43:
        box_drawn(id, f);
        break;
    case 18:
        f->eax = name_cells(f->arg[0], f->arg[1]);
        return 1;
    case 19:
        f->eax = text(MESSAGE(f->arg[0]), id, f);
        return 1;
    case 20: {
        static const u32 z_counts[] = {NARRATION_Z_COUNT, STILL_Z_COUNTS};
        u32 z = 0, i;
        for (i = 0; i < sizeof z_counts / sizeof *z_counts; i++)
            z |= f->ret == z_counts[i];
        if ((f->ret >= NARRATION_CODE && f->ret < NARRATION_CODE_END)
            || (f->ret >= STILL_CODE && f->ret < STILL_CODE_END) || f->ret == BRIEFING_COUNT || z)
            f->arg[0] = (u32)flat((const char *)text(f->arg[0], id, f));
        break;
    }
    case 66:
    case 67: {
        u32 width = hires_width(4080, f->arg[0] + 6);
        if (f->arg[6] < width) {


            if (f->arg[0] == 1 && f->arg[1] < 300)
                f->arg[1] -= width - f->arg[6];
            f->arg[6] = width;
        }
        break;
    }
#ifdef ENRT_LOG
    case 8:
        log_line(id, f, (const u8 *)f->ecx + 0x18, f->arg[1]);
        break;
#endif
    }
    return 0;
}


/* The added Game Settings rows, drawn from the option sheet (build.OPTION_ROWS) the way the stock rows are: a 310x42
   quad per text, white art tinted by the quad colour. Bands: 0-3 labels (text, voice, field of view, frame rate),
   4-7 values (English, Japanese, 60, 120), 8 on the field of view numbers from 100. */
#define OPTION_W 310
#define OPTION_BAND 42
#define OPTION_SCALE 2
#define LABEL_LEFT (-54)
#define BAND_ENGLISH 4
#define BAND_60 6
#define BAND_FOV 8
#define VALUE_NEXT_LANGUAGE 563
#define VALUE_NEXT_FPS 557
#define FOV_CENTRE (VALUE_CENTRE + 70)
static const u32 cursor_tint = 0x80808080, idle_tint = 0x80404040, value_lit = 0x804E777F, value_dim = 0x804E4E4E;

static void option_text(u32 band, int x, u32 y, u32 tint)
{
    unsigned long long handle = hires_handle[option_sheet - 1];
    u32 *quad;
    hires_native = 1;
    quad = ((u32 *(*)(u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32))0x5DDAB0)(
        (u32)handle, (u32)(handle >> 32), x, y, 0, OPTION_W, OPTION_BAND, 0, band * OPTION_BAND * OPTION_SCALE,
        OPTION_W * OPTION_SCALE, OPTION_BAND * OPTION_SCALE, 0x80, 1);
    hires_native = 0;
    quad[8] = tint, quad[9] = 0x3f800000;
    ((void (*)(u32 *, u32))0x5DCA30)(quad, 0);
    ((void (*)(u32))0x5DD700)(0);
}

static void option_row(u32 row, u32 x, u32 y)
{
    u32 label, first = BAND_ENGLISH, chosen = 0, next = VALUE_NEXT_LANGUAGE;
    if ((int)x < 0)
        return;
    if (row < rows)
        label = row, chosen = row ? voice_saved : text_saved;
    else if (row == rows)
        label = 2, first = 0;
    else
        label = 3, first = BAND_60, chosen = fps == 120, next = VALUE_NEXT_FPS;
    option_text(label, x + LABEL_LEFT, y, SETTINGS_CURSOR == 3 + row ? cursor_tint : idle_tint);
    if (row == rows)
        return option_text(BAND_FOV + (fov - FOV_MIN) / FOV_STEP, x + FOV_CENTRE - OPTION_W / 2, y, value_lit);
    option_text(first, x + VALUE_CENTRE - OPTION_W / 2, y, chosen == 0 ? value_lit : value_dim);
    option_text(first + 1, x + next - OPTION_W / 2, y, chosen == 1 ? value_lit : value_dim);
}

static int always(u32 id, struct frame *f, u32 *replace)
{
    struct loose *e;
    const u32 *voice, *se;
    const u8 *lip = 0;
    *replace = 0;
    switch (id) {
    case 68:
    case 69:
        glyph_fit(id, f);
        return 1;
    case 24:
        reload();
        keys_read();
        pads_rescan();
        prompts_show(pad_last && !rebinding);
        rebinding = 0;
        box_now ^= 1, box_count[box_now] = 0;
        if (fps_ok && !hfr_busy) {
            limiter(hfr_drawn ? 120 : 60);
            if (!hfr_drawn)
                have_prev = 0;
            hfr_drawn = 0;
        }
#ifdef ENRT_TEST
        enrt_test.reloads = reloads;
        test_frame();
#endif
        return 1;
    case 28:
        if (option_sheet && f->ebp - 3 <= rows + 1)
            option_row(f->ebp - 3, f->edi, f->ebx);
        else if (f->ebp - 3 < rows)
            language_row(f->ebp - 3, f->edi, f->ebx);
        else if (f->ebp - 3 == rows)
            fov_row(f->edi, f->ebx, SETTINGS_CURSOR == 3 + rows);
        else if (f->ebp - 4 == rows)
            choice_row(fps_label[!english], fps_value[!english], fps == 120, f->edi, f->ebx, SETTINGS_CURSOR == 4 + rows);
#ifdef ENRT_RENDER_DISTANCE
        else if (f->ebp - 5 == rows)
            choice_row(distance_label[!english], switch_value, !distance, f->edi, f->ebx, SETTINGS_CURSOR == 5 + rows);
#endif
        else
            return 1;
        if (f->ebp - LAST_ROW == rows && (int)f->edi >= 0 && text_saved != (u32)!english)
            shadowed(restart_note[!english], f->edi + VALUE_CENTRE - VALUE_STEP / 2, f->ebx + 42, NOTE_CELL,
                     &dim_colour);
        f->edi = (u32)-1000;
        return 1;
    case 29:
        if (*(const u32 *)(f->ebp + 0x1C) - 3 == rows && !INPUT_BLOCKED)
            fov_input(f->eax);
        else if (*(const u32 *)(f->ebp + 0x1C) - 4 == rows && !INPUT_BLOCKED)
            fps_input(f->eax);
#ifdef ENRT_RENDER_DISTANCE
        else if (*(const u32 *)(f->ebp + 0x1C) - 5 == rows && !INPUT_BLOCKED)
            distance_input(f->eax);
#endif
        else
            language_input(*(const u32 *)(f->ebp + 0x1C) - 3, f->eax);
        return 1;
    case 45:
    case 46:
        if (fov != FOV_MIN)
            CAMERA_FOV(f->ebp) *= fov / 100.0f;
        return 1;
    case 47:
        pad_read((const u8 *)f->esi);
        return 1;
    case 70:
        pads_named();
        return 1;
    case 48:
        prompts_load();
        hires_load();
        return 1;
    case 49:
    case 50:
        f->eax = menu_word(f->arg[0], id == 50, f->ret), *replace = 1;
        return 1;
    case 52:
        rebinding = 1;
        prompts_show(0);
        return 1;
    case 51:
        *replace = pad_label((const u8 *)f->arg[0], (char **)f->arg[1]);
        return 1;
    case 65:
        *replace = move_key(f);
        return 1;
    case 53:
        *replace = hfr_draw();
        return 1;
    case 54:
    case 55:
    case 56:
    case 60:
        *replace = extra;
        return 1;
    case 61:
        if (extra)
            f->eax = 0, *replace = 1;
        return 1;
    case 58:
        if (!extra)
            fx_tick_from = FX_MODEL_COUNT;
        *replace = extra;
        return 1;
    case 59:
        if (!extra)
            fx_tick_from = 0;
        else {
            u32 to = fx_queued < FX_MODEL_COUNT ? fx_queued : FX_MODEL_COUNT;
            u32 from = fx_tick_from < to ? fx_tick_from : to, i;
            for (i = from; i < to; i++)
                fx_kept[i - from][0] = FX_MODEL(i)[0], fx_kept[i - from][1] = FX_MODEL(i)[1], FX_MODEL(i)[0] = 0;
            FX_MODELS_RELEASE(FX_MANAGER);
            for (i = 0; i < to - from; i++)
                FX_MODEL(i)[0] = fx_kept[i][0], FX_MODEL(i)[1] = fx_kept[i][1];
            FX_MODEL_COUNT = to - from, *replace = 1;
        }
        return 1;
    case 62:
        head_of = CHARACTER(f->eax);
        return 1;
    case 64:
        head_point((float *)f->ebx, (const float *)f->ecx);
        return 1;
    case 63:
        marker_point((float *)f->arg[1]);
        return 1;
    case 57:
        if (hfr_busy) {
            union { u32 u; float s; } dt = {f->arg[1]};
            dt.s *= 0.5f, f->arg[1] = dt.u;
        }
        return 1;
    case 30:
        *replace = read_loose(f);
        return 1;
    case 31:
        if ((e = served(f->arg[0])))
            f->eax = e->size, *replace = 1;
        else if (f->arg[0] == SE_KEY && f->ret == 0x5D2AEA && se_bank != INVALID_HANDLE_VALUE)
            f->eax = ~0u, *replace = 1;

        return 1;
    case 36:
    case 37:
        if ((voice = english_voice(f->arg[1])))
            f->eax = id == 36 ? VOICE_TAG | voice[0] : voice[1], *replace = 1;
#ifdef ENRT_LOG
        if (id == 36) {
            char line[] = "voice ja 0000 mode 0 stage 00";
            if (voice)
                line[6] = 'e', line[7] = 'n';
            put_hex(line + 9, f->arg[1], 4), line[19] = '0' + (VOICE_MODE & 1), put_hex(line + 27, VOICE_STAGE, 2);
            note(line, 0);
        }
#endif
        return 1;
    case 71:  /* the speak command (0x49E560) calls with edi at the line record: u32 -1, u16 voice index, u16 size;
               * the state's +0x5C already holds the next line's index once the voice has started */
        if (f->ret == 0x49E65F && f->edi + 4 + *(const u16 *)(f->edi + 6) == f->arg[2]
            && (lip = english_lips(*(const u16 *)(f->edi + 4))))
            f->arg[2] = (u32)lip;
#ifdef ENRT_LOG
        if (f->ret == 0x49E65F) {
            char line[] = "lips ja 0000 index 000 frames";
            if (lip)
                line[5] = 'e', line[6] = 'n';
            put_hex(line + 8, (VOICE_MODE ? 0x8F4 : 0xAF3) + *(const u16 *)(f->edi + 4), 4);
            put_hex(line + 19, *(const u16 *)(f->edi + 4), 3);
            note(line, *(const u32 *)(f->arg[2] + 12));
        }
#endif
        return 1;
    case 38:
    case 39:
    case 40:
    case 41:
        if (!voice_saved && f->arg[0] < se_banks && (se = se_table + 4 * f->arg[0])[1])
            f->eax = se[id - 38] | (id & 1 ? 0 : VOICE_TAG), *replace = 1;
        return 1;
    case 21:
    case 22:
#ifdef ENRT_TEST
        test_review_draw();
        if (review_name && !review_drawing) {




            f->arg[2] = f->arg[3] = 5000;
        }
#endif
#ifdef ENRT_LOG
        log_quad(id, f);
#endif
#ifdef ENRT_TEST
        test_quad(id, f);
#endif
        hires_quad(id, f);
        return 1;
#ifdef ENRT_TEST
    case 25:
        if (next_stage) {
            STAGE = next_stage - 1;
            STAGE_MAP = STAGE == 0x90 ? 0x8E : STAGE_TABLE[STAGE * 0x26];
            next_stage = 0;
        }
        return 1;
#endif
    }
    return 0;
}

u32 enrt_hook(u32 id, struct frame *f)
{
    u32 replace;
    if (always(id, f, &replace))
        return replace;
    if (english)
        return english_hook(id, f);
#ifdef ENRT_TEST

    if (id == 1 && f->arg[0])
        test_text(id, f, (const char *)f->arg[0], f->arg[1], f->arg[2], measure((const char *)f->arg[0]));
    else if (id == 4 && f->arg[0])
        test_text(id, f, (const char *)f->arg[0], f->arg[1], f->arg[2], 0);
    else if (id == 6)
        test_set(id, f, f->arg[0], (const char *)f->arg[1]);
    else if (id == 7)
        test_set(id, f, f->arg[1], (const char *)f->arg[0]);
#endif
    return 0;
}

static void note(const char *s, u32 value)
{
    char line[160], *p = line;
    DWORD written;
    while (*s && p < line + 140)
        *p++ = *s++;
    if (value) {
        *p++ = ' ', *p++ = '0', *p++ = 'x';
        p = put_hex(p, value, 8);
    }
    *p++ = '\r', *p++ = '\n';
    if (runtime_log != INVALID_HANDLE_VALUE)
        WriteFile(runtime_log, line, p - line, &written, 0);
}

#define ROR(x, n) ((x) >> (n) | (x) << (32 - (n)))

static void sha256_blocks(u32 h[8], const u8 *p, u32 blocks)
{
    static const u32 k[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    };
    for (; blocks--; p += 64) {
        u32 w[64], s[8], i, t1, t2;
        for (i = 0; i < 16; i++)
            w[i] = (u32)p[4 * i] << 24 | (u32)p[4 * i + 1] << 16 | (u32)p[4 * i + 2] << 8 | p[4 * i + 3];
        for (; i < 64; i++)
            w[i] = w[i - 16] + (ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 7]
                 + (ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10));
        for (i = 0; i < 8; i++)
            s[i] = h[i];
        for (i = 0; i < 64; i++) {
            t1 = s[7] + (ROR(s[4], 6) ^ ROR(s[4], 11) ^ ROR(s[4], 25)) + ((s[4] & s[5]) ^ (~s[4] & s[6])) + k[i] + w[i];
            t2 = (ROR(s[0], 2) ^ ROR(s[0], 13) ^ ROR(s[0], 22)) + ((s[0] & s[1]) ^ (s[0] & s[2]) ^ (s[1] & s[2]));
            s[7] = s[6], s[6] = s[5], s[5] = s[4], s[4] = s[3] + t1;
            s[3] = s[2], s[2] = s[1], s[1] = s[0], s[0] = t1 + t2;
        }
        for (i = 0; i < 8; i++)
            h[i] += s[i];
    }
}


static int stock_exe(void)
{
    WCHAR name[MAX_PATH];
    u32 h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    u32 size, rest, blocks, i;
    u8 *data, tail[128] = {0};
    unsigned long long bits;
    if (!GetModuleFileNameW(0, name, MAX_PATH) || !(data = load(name, &size)))
        return 0;
    sha256_blocks(h, data, size / 64);
    rest = size % 64;
    for (i = 0; i < rest; i++)
        tail[i] = data[size - rest + i];
    HeapFree(GetProcessHeap(), 0, data);
    tail[rest] = 0x80;
    blocks = rest < 56 ? 1 : 2;
    for (bits = (unsigned long long)size * 8, i = 0; i < 8; i++)
        tail[blocks * 64 - 1 - i] = (u8)(bits >> 8 * i);
    sha256_blocks(h, tail, blocks);
    for (i = 0; i < 32; i++)
        if ((u8)(h[i / 4] >> (24 - 8 * (i % 4))) != enrt_stock_sha256[i])
            return 0;
    return 1;
}


static int wanted(u8 g)
{
    return g == 0 ? english : g == rows;
}

static int hooked(const struct site *s)
{
#ifdef ENRT_TEST
    (void)s;
    return 1;
#else
    return english || !(s->flags & 1);
#endif
}

static int stock_at(u32 va, const u8 *stock, u32 n)
{
    const u8 *p = (const u8 *)va;
    while (n--)
        if (*p++ != *stock++)
            return 0;
    return 1;
}

static int write_at(u32 va, const u8 *bytes, u32 n)
{
    DWORD old;
    u8 *p = (u8 *)va;
    if (!VirtualProtect(p, n, PAGE_EXECUTE_READWRITE, &old))
        return 0;
    while (n--)
        *p++ = *bytes++;
    VirtualProtect((void *)va, p - (u8 *)va, old, &old);
    return 1;
}

static void put32(u8 *p, u32 v)
{
    p[0] = (u8)v, p[1] = (u8)(v >> 8), p[2] = (u8)(v >> 16), p[3] = (u8)(v >> 24);
}



static u8 *hook_stub(u8 *p, const struct site *s)
{
    u32 i;
    *p++ = 0x60, *p++ = 0x54, *p++ = 0x6a, *p++ = s->id;
    *p++ = 0xe8, put32(p, (u32)enrt_hook - (u32)(p + 4)), p += 4;
    *p++ = 0x83, *p++ = 0xc4, *p++ = 0x08;
    if (s->flags & 2)
        *p++ = 0x85, *p++ = 0xc0, *p++ = 0x61, *p++ = 0x75, *p++ = s->len + 5;
    else
        *p++ = 0x61;
    for (i = 0; i < s->len; i++)
        *p++ = s->prologue[i];
    *p++ = 0xe9, put32(p, s->va + s->len - (u32)(p + 4)), p += 4;
    if (s->flags & 2 && s->pops)
        *p++ = 0xc2, *p++ = (u8)s->pops, *p++ = (u8)(s->pops >> 8);
    else if (s->flags & 2)
        *p++ = 0xc3;
    return p;
}

#define HOOK_COUNT (sizeof enrt_hooks / sizeof *enrt_hooks)
#define EDIT_LEN(e) ((u32)(e)[5] | (u32)(e)[6] << 8)
#define EDIT_VA(e) ((u32)(e)[1] | (u32)(e)[2] << 8 | (u32)(e)[3] << 16 | (u32)(e)[4] << 24)


static int patch(void)
{
    const u8 *e;
    u8 *stubs, *p, jump[16];
    u32 i, j, widths = (u32)enrt_widths, count = 0;
    for (e = enrt_edits; *e != 0xFF; e += 7 + 2 * EDIT_LEN(e))
        if (wanted(*e) && !stock_at(EDIT_VA(e), e + 7, EDIT_LEN(e)))
            return note("refused: the code differs from stock at", EDIT_VA(e)), 0;
    for (i = 0; i < HOOK_COUNT; i++)
        if (hooked(&enrt_hooks[i]) && !stock_at(enrt_hooks[i].va, enrt_hooks[i].prologue, enrt_hooks[i].len))
            return note("refused: the code differs from stock at", enrt_hooks[i].va), 0;
    if (english && *(const u32 *)WIDTH_POINTER)
        return note("refused: the width pointer is set at", WIDTH_POINTER), 0;
    if (!(stubs = VirtualAlloc(0, 48 * HOOK_COUNT, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)))
        return note("refused: no memory for the hook stubs", 0), 0;
    for (e = enrt_edits; *e != 0xFF; e += 7 + 2 * EDIT_LEN(e))
        if (wanted(*e) && !write_at(EDIT_VA(e), e + 7 + EDIT_LEN(e), EDIT_LEN(e)))
            return note("failed: could not write", EDIT_VA(e)), 0;
    for (i = 0, p = stubs; i < HOOK_COUNT; i++) {
        const struct site *s = &enrt_hooks[i];
        if (!hooked(s))
            continue;
        jump[0] = 0xe9, put32(jump + 1, (u32)p - (s->va + 5));
        for (j = 5; j < s->len; j++)
            jump[j] = 0x90;
        p = hook_stub(p, s);
        if (!write_at(s->va, jump, s->len))
            return note("failed: could not write", s->va), 0;
        count++;
    }
    if (english && !write_at(WIDTH_POINTER, (const u8 *)&widths, 4))
        return note("failed: could not write", WIDTH_POINTER), 0;
    FlushInstructionCache(GetCurrentProcess(), 0, 0);
    note(english ? "installed: English text, hooks" : "installed: Japanese text, hooks", count);
    return 1;
}

static void install(HINSTANCE self)
{
    WCHAR name[MAX_PATH], value[16];
    u32 n = GetModuleFileNameW(self, root, MAX_PATH - 40);
    if (!n || n >= MAX_PATH - 40)
        return;
    while (n && root[n - 1] != '\\' && root[n - 1] != '/')
        n--;
    lstrcpyW(root + n, L"wozmod\\");
    runtime_log = CreateFileW(path(name, L"runtime.log"), GENERIC_WRITE, FILE_SHARE_READ, 0, CREATE_ALWAYS, 0, 0);
    GetPrivateProfileStringW(L"language", L"text", L"english", value, 16, path(name, L"settings.ini"));
    text_saved = !lstrcmpiW(value, L"japanese");
    GetPrivateProfileStringW(L"language", L"voice", L"english", value, 16, name);
    voice_saved = !lstrcmpiW(value, L"japanese");
    fov = GetPrivateProfileIntW(L"camera", L"fov", FOV_MIN, name);
    fov = fov < FOV_MIN ? FOV_MIN : fov > FOV_MAX ? FOV_MAX : fov / FOV_STEP * FOV_STEP;
    if (!(fps_ok = stock_at(FPS_LIMITER, (const u8 *)"\x6a\x3c", 2)))
        note("refused: 120 FPS, the present limiter differs from stock at", FPS_LIMITER);
    fps = fps_ok && GetPrivateProfileIntW(L"display", L"fps", 60, name) == 120 ? 120 : 60;
#ifdef ENRT_RENDER_DISTANCE
    GetPrivateProfileStringW(L"display", L"distance", L"on", value, 16, name);
    distance = !!lstrcmpiW(value, L"off");
#endif
    english = !text_saved;
    rows = GetFileAttributesW(path(name, VOICE_BANK)) == INVALID_FILE_ATTRIBUTES ? 1 : 2;
    if (!stock_exe()) {
        note("refused: OROCHI_Z.exe is not the stock executable", 0);
        return;
    }
    if (english && !(enrt_table = load_table())) {
        note("refused: wozmod\\table.bin is missing or damaged", 0);
        return;
    }
    table_time(&table_stamp);
    index_entries();
    open_voices();
    if (patch()) {
#ifdef ENRT_RENDER_DISTANCE
        distance_install();
#endif
    }
}


__declspec(dllexport) void *WINAPI Direct3DCreate9(UINT version)
{
    static void *(WINAPI *real)(UINT);
    if (!real) {
        WCHAR name[MAX_PATH];
        UINT n = GetSystemDirectoryW(name, MAX_PATH - 10);
        HMODULE system;
        if (!n || n >= MAX_PATH - 10)
            return 0;
        lstrcpyW(name + n, L"\\d3d9.dll");
        if (!(system = LoadLibraryW(name)) || !(real = (void *(WINAPI *)(UINT))GetProcAddress(system, "Direct3DCreate9")))
            return 0;
    }
    {
        void *d3d = real(version);
        if (d3d && !real_create_device)
            hook_slot(d3d, 16, (void *)create_device, &real_create_device);
        return d3d;
    }
}

__declspec(dllexport) int WINAPI D3DPERF_BeginEvent(DWORD colour, LPCWSTR name)
{
    (void)colour, (void)name;
    return 0;
}

__declspec(dllexport) int WINAPI D3DPERF_EndEvent(void)
{
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE self, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(self);
        install(self);
    }
    return TRUE;
}
