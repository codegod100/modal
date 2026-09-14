/* ptyshim.c -- lets nix build derivations inside Modal containers.
 *
 * Nix runs every builder behind a pseudo-terminal and, right after fork,
 * reads the master for the child's one-byte handshake -- before the child
 * has opened the slave. On Linux that read blocks. Under gVisor (Modal's
 * sandbox) a master with no open slave returns EIO instead; nix takes it
 * as EOF, abandons the build, and the child runs to completion unheard:
 *
 *     error: unexpected EOF reading a line
 *
 * Fix: retry EIO on a pty master only until its first byte has ever
 * arrived, then step aside so the genuine hangup at build end still
 * reaches nix. Nothing is held open, so builds also *finish*.
 *
 *     gcc -shared -fPIC -O2 -o ptyshim.so ptyshim.c -ldl
 *     LD_PRELOAD=./ptyshim.so nix build ...
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>

static char pending[4096]; /* master fds that have not yet delivered a byte */
static int (*real_openpt)(int);
static ssize_t (*real_read)(int, void *, size_t);
static int (*real_close)(int);

static void init(void)
{
    if (!real_openpt) real_openpt = dlsym(RTLD_NEXT, "posix_openpt");
    if (!real_read)   real_read   = dlsym(RTLD_NEXT, "read");
    if (!real_close)  real_close  = dlsym(RTLD_NEXT, "close");
}

int posix_openpt(int flags)
{
    init();
    int m = real_openpt(flags);
    if (m >= 0 && m < 4096) pending[m] = 1;
    return m;
}

ssize_t read(int fd, void *buf, size_t n)
{
    init();
    ssize_t r = real_read(fd, buf, n);
    if (fd >= 0 && fd < 4096 && pending[fd]) {
        /* the handshake window: the child opens its slave within ms */
        for (int i = 0; i < 5000 && r == -1 && errno == EIO; i++) {
            struct timespec ts = { 0, 1000000 };
            nanosleep(&ts, 0);
            r = real_read(fd, buf, n);
        }
        if (r > 0) pending[fd] = 0; /* first byte seen: Linux semantics from here */
    }
    return r;
}

int close(int fd)
{
    init();
    if (fd >= 0 && fd < 4096) pending[fd] = 0;
    return real_close(fd);
}
