/*
 * Hudiy's copy of the phone's contacts, cleaned up as Hudiy reads it.
 *
 * Phones send every contact over Bluetooth (PBAP), and the Bluetooth standard makes every contact card
 * carry a phone number field - so a contact without a number arrives with a blank one ("TEL:"). Hudiy's
 * call screen treats a blank number as matching every caller and shows that contact's name for every
 * call. Hudiy has no setting for it, so this small library is loaded into Hudiy (LD_PRELOAD, by
 * bin/hu-hudiy-run.sh) and changes only one thing: when Hudiy opens its downloaded phonebook
 * (~/.hudiy/cache/hfp/pb*.vcf or fav*.vcf) to read it, it gets a copy where
 *   - contacts with neither a phone number nor an address are left out, and
 *   - blank or too-short phone numbers (fewer than 3 digits) are taken off the contacts that stay.
 * Everything else reaches Hudiy byte for byte. Hudiy downloads the phonebook again each time the phone
 * connects and every read is cleaned again, so a contact that later gets a number or an address shows
 * up at the next connection. Call history files are left alone. The file on disk is not changed.
 *
 * Only active inside the program named hudiy (HU_VCF_FILTER_EXE to test it on another program); it
 * takes itself off LD_PRELOAD there, so the programs Hudiy starts (its web views) don't load it.
 * Anything unexpected (can't read, out of memory) falls back to the untouched file.
 *
 * Built by setup.d/90-hudiy.sh: gcc -O2 -shared -fPIC -o hu-vcf-filter.so hu-vcf-filter.c -ldl
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static int active = 0;

static void drop_self_from_preload(void) {
    const char *pre = getenv("LD_PRELOAD");
    if (!pre) return;
    char *copy = strdup(pre), *out = calloc(1, strlen(pre) + 1);
    if (!copy || !out) { free(copy); free(out); return; }
    for (char *tok = strtok(copy, ": "); tok; tok = strtok(NULL, ": ")) {
        if (strstr(tok, "hu-vcf-filter")) continue;
        if (*out) strcat(out, ":");
        strcat(out, tok);
    }
    if (*out) setenv("LD_PRELOAD", out, 1); else unsetenv("LD_PRELOAD");
    free(copy); free(out);
}

__attribute__((constructor)) static void hu_init(void) {
    char exe[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    if (n <= 0) return;
    exe[n] = 0;
    const char *base = strrchr(exe, '/'); base = base ? base + 1 : exe;
    const char *want = getenv("HU_VCF_FILTER_EXE");
    if (strcmp(base, want && *want ? want : "hudiy") == 0) { active = 1; drop_self_from_preload(); }
}

/* Hudiy's phonebook downloads: <dir>/pb*.vcf and fav*.vcf (not the call history: cch, ich, och, mch) */
static int is_phonebook(const char *path) {
    if (!active || !path || path[0] != '/') return 0;
    const char *dir = getenv("HU_VCF_FILTER_DIR");
    if (!strstr(path, dir && *dir ? dir : "/.hudiy/cache/hfp/")) return 0;
    const char *base = strrchr(path, '/') + 1;
    size_t len = strlen(base);
    if (len < 5 || strcasecmp(base + len - 4, ".vcf") != 0) return 0;
    return strncmp(base, "pb", 2) == 0 || strncmp(base, "fav", 3) == 0;
}

/* ---- the clean-up ---------------------------------------------------------------------------- */
typedef struct { char *p; size_t n, cap; } buf_t;
static int put(buf_t *b, const char *s, size_t n) {
    if (b->n + n > b->cap) {
        size_t cap = (b->cap ? b->cap * 2 : 65536); while (cap < b->n + n) cap *= 2;
        char *q = realloc(b->p, cap); if (!q) return -1;
        b->p = q; b->cap = cap;
    }
    memcpy(b->p + b->n, s, n); b->n += n; return 0;
}

/* the property name of a logical line ("item1.TEL;TYPE=CELL:..." -> TEL), upper case, into name */
static void prop_name(const char *s, size_t n, char *name, size_t cap) {
    size_t i = 0, start = 0, end = 0;
    while (i < n && s[i] != ':' && s[i] != ';') { if (s[i] == '.') start = i + 1; i++; }
    end = i;
    size_t k = 0;
    for (size_t j = start; j < end && k + 1 < cap; j++) {
        char c = s[j]; name[k++] = (c >= 'a' && c <= 'z') ? c - 32 : c;
    }
    name[k] = 0;
}
static const char *value_of(const char *s, size_t n) { const char *c = memchr(s, ':', n); return c ? c + 1 : s + n; }
static int params_have(const char *s, size_t n, const char *what) {
    const char *c = memchr(s, ':', n); size_t m = c ? (size_t)(c - s) : n, w = strlen(what);
    for (size_t i = 0; i + w <= m; i++) if (strncasecmp(s + i, what, w) == 0) return 1;
    return 0;
}
static size_t digits(const char *v, const char *end) { size_t d = 0; for (; v < end; v++) if (*v >= '0' && *v <= '9') d++; return d; }
static int has_text(const char *v, const char *end, int qp) {
    for (; v < end; v++) {
        char c = *v;
        if (c == ';' || c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ',') continue;
        if (qp && c == '=' ) { if (v + 1 < end && (v[1] == '\r' || v[1] == '\n')) continue; return 1; }
        return 1;
    }
    return 0;
}

/* in -> out: whole cards kept or dropped, blank numbers taken off. 0 = ok. */
static int hu_vcf_clean(const char *in, size_t n, buf_t *out) {
    size_t i = 0;
    buf_t card = {0}; int in_card = 0, has_tel = 0, has_adr = 0;
    while (i < n) {
        /* one logical line: a physical line plus its continuations (folded lines starting with a space
           or tab; quoted-printable soft breaks: a line ending in '=') */
        size_t s = i, e;
        int qp = 0, first = 1;
        for (;;) {
            const char *nl = memchr(in + i, '\n', n - i);
            e = nl ? (size_t)(nl - in) + 1 : n;
            if (first) { qp = params_have(in + s, e - s, "QUOTED-PRINTABLE"); first = 0; }
            size_t t = e; while (t > i && (in[t - 1] == '\n' || in[t - 1] == '\r')) t--;
            i = e;
            if (i >= n) break;
            if (in[i] == ' ' || in[i] == '\t') continue;
            if (qp && t > s && in[t - 1] == '=') continue;
            break;
        }
        const char *line = in + s; size_t len = e - s;
        char name[32]; prop_name(line, len, name, sizeof name);
        const char *v = value_of(line, len), *vend = line + len;
        if (!in_card) {
            if (strcmp(name, "BEGIN") == 0 && strncasecmp(v, "VCARD", 5) == 0) {
                in_card = 1; has_tel = has_adr = 0; card.n = 0;
                if (put(&card, line, len)) goto fail;
            } else if (put(out, line, len)) goto fail;
            continue;
        }
        if (strcmp(name, "END") == 0 && strncasecmp(v, "VCARD", 5) == 0) {
            if (put(&card, line, len)) goto fail;
            if ((has_tel || has_adr) && put(out, card.p, card.n)) goto fail;
            in_card = 0; continue;
        }
        if (strcmp(name, "TEL") == 0) {
            if (digits(v, vend) < 3) continue;                 /* a blank number: left out */
            has_tel = 1;
        } else if (strcmp(name, "ADR") == 0 && has_text(v, vend, qp)) {
            has_adr = 1;
        }
        if (put(&card, line, len)) goto fail;
    }
    if (in_card && put(out, card.p, card.n)) goto fail;        /* a cut-off last card: as it came */
    free(card.p); return 0;
fail:
    free(card.p); return -1;
}

/* ---- opening Hudiy's phonebook --------------------------------------------------------------- */
typedef int (*open_fn)(const char *, int, ...);
typedef int (*openat_fn)(int, const char *, int, ...);
typedef FILE *(*fopen_fn)(const char *, const char *);
#define REAL(fn, name) static fn real = NULL; if (!real) real = (fn)dlsym(RTLD_NEXT, name)

static int real_open_ro(const char *path, int flags) {
    REAL(open_fn, "open");
    return real(path, flags);
}

/* a memory file with the cleaned phonebook, or -1 (then the caller opens the real file) */
static int cleaned_fd(const char *path, int flags) {
    int fd = real_open_ro(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st; char *data = NULL; size_t got = 0;
    if (fstat(fd, &st) != 0 || st.st_size <= 0 || st.st_size > 256 * 1024 * 1024) { close(fd); return -1; }
    data = malloc((size_t)st.st_size);
    if (!data) { close(fd); return -1; }
    while (got < (size_t)st.st_size) {
        ssize_t r = read(fd, data + got, (size_t)st.st_size - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        got += (size_t)r;
    }
    close(fd);
    buf_t out = {0};
    int mfd = -1;
    if (hu_vcf_clean(data, got, &out) == 0) {
        mfd = memfd_create("hudiy-phonebook.vcf", (flags & O_CLOEXEC) ? MFD_CLOEXEC : 0);
        if (mfd >= 0) {
            size_t w = 0;
            while (w < out.n) { ssize_t r = write(mfd, out.p + w, out.n - w); if (r < 0 && errno == EINTR) continue; if (r <= 0) break; w += (size_t)r; }
            if (w != out.n || lseek(mfd, 0, SEEK_SET) != 0) { close(mfd); mfd = -1; }
        }
    }
    free(data); free(out.p);
    return mfd;
}

static int wants_clean(const char *path, int flags) {
    return (flags & O_ACCMODE) == O_RDONLY && !(flags & (O_CREAT | O_TRUNC | O_DIRECTORY | O_PATH)) && is_phonebook(path);
}

#define OPEN_BODY(realname)                                                         \
    mode_t mode = 0;                                                                \
    if (flags & (O_CREAT | O_TMPFILE)) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); } \
    if (wants_clean(path, flags)) { int fd = cleaned_fd(path, flags); if (fd >= 0) return fd; } \
    REAL(open_fn, realname); return real(path, flags, mode);

int open(const char *path, int flags, ...) { OPEN_BODY("open") }
int open64(const char *path, int flags, ...) { OPEN_BODY("open64") }
int __open_2(const char *path, int flags) {
    if (wants_clean(path, flags)) { int fd = cleaned_fd(path, flags); if (fd >= 0) return fd; }
    REAL(open_fn, "__open_2"); return real(path, flags);
}
int __open64_2(const char *path, int flags) {
    if (wants_clean(path, flags)) { int fd = cleaned_fd(path, flags); if (fd >= 0) return fd; }
    REAL(open_fn, "__open64_2"); return real(path, flags);
}

#define OPENAT_BODY(realname)                                                       \
    mode_t mode = 0;                                                                \
    if (flags & (O_CREAT | O_TMPFILE)) { va_list ap; va_start(ap, flags); mode = va_arg(ap, int); va_end(ap); } \
    (void)dirfd; /* only absolute paths are Hudiy's phonebook */ \
    if (wants_clean(path, flags)) { int fd = cleaned_fd(path, flags); if (fd >= 0) return fd; } \
    REAL(openat_fn, realname); return real(dirfd, path, flags, mode);

int openat(int dirfd, const char *path, int flags, ...) { OPENAT_BODY("openat") }
int openat64(int dirfd, const char *path, int flags, ...) { OPENAT_BODY("openat64") }

static FILE *fopen_common(const char *path, const char *m, const char *realname) {
    if (m && m[0] == 'r' && !strchr(m, '+') && is_phonebook(path)) {
        int fd = cleaned_fd(path, strchr(m, 'e') ? O_CLOEXEC : 0);
        if (fd >= 0) { FILE *f = fdopen(fd, "r"); if (f) return f; close(fd); }
    }
    REAL(fopen_fn, realname); return real(path, m);
}
FILE *fopen(const char *path, const char *m) { return fopen_common(path, m, "fopen"); }
FILE *fopen64(const char *path, const char *m) { return fopen_common(path, m, "fopen64"); }
