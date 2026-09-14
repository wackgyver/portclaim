/* Optional integration-test observer. Opens only the supplied synthetic device.
 * cc -O2 -Wall -Wextra tools/native-libinput-check.c $(pkg-config --cflags --libs libinput) -o /tmp/native-libinput-check
 * The compositor MUST have the synthetic test device disabled before injecting.
 */
#include <errno.h>
#include <fcntl.h>
#include <libinput.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int open_device(const char *path, int flags, void *data) {
    (void)data;
    int fd = open(path, flags);
    if (fd < 0) fprintf(stderr, "open %s: %s\n", path, strerror(errno));
    return fd < 0 ? -errno : fd;
}
static void close_device(int fd, void *data) { (void)data; close(fd); }
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    const struct libinput_interface iface = {open_device, close_device};
    struct libinput *li = libinput_path_create_context(&iface, NULL);
    if (!li) { fprintf(stderr, "libinput context creation failed\n"); return 3; }
    libinput_log_set_priority(li, LIBINPUT_LOG_PRIORITY_DEBUG);
    struct libinput_device *dev = libinput_path_add_device(li, argv[1]);
    if (!dev) { fprintf(stderr, "libinput rejected device\n"); libinput_unref(li); return 4; }
    double w = 0, h = 0;
    libinput_device_get_size(dev, &w, &h);
    int fingers = libinput_device_config_tap_get_finger_count(dev);
    int drag = libinput_device_config_3fg_drag_get_finger_count(dev);
    libinput_device_config_tap_set_enabled(dev, LIBINPUT_CONFIG_TAP_ENABLED);
    libinput_device_config_tap_set_drag_enabled(dev, LIBINPUT_CONFIG_DRAG_DISABLED);
    libinput_device_config_click_set_method(dev, LIBINPUT_CONFIG_CLICK_METHOD_CLICKFINGER);
    libinput_device_config_scroll_set_method(dev, LIBINPUT_CONFIG_SCROLL_2FG);
    libinput_device_config_3fg_drag_set_enabled(dev, LIBINPUT_CONFIG_3FG_DRAG_ENABLED_3FG);
    printf("READY size=%.2fx%.2fmm tap-fingers=%d drag-fingers=%d\n", w, h, fingers, drag);
    fflush(stdout);
    int motion = 0, scroll = 0, down = 0, up = 0;
    double dx = 0, dy = 0;
    struct pollfd p = {libinput_get_fd(li), POLLIN, 0};
    double deadline = now() + 10;
    while (now() < deadline) {
        poll(&p, 1, 50);
        libinput_dispatch(li);
        struct libinput_event *event;
        while ((event = libinput_get_event(li))) {
            enum libinput_event_type type = libinput_event_get_type(event);
            if (type == LIBINPUT_EVENT_POINTER_MOTION) {
                struct libinput_event_pointer *ptr = libinput_event_get_pointer_event(event);
                ++motion;
                dx += libinput_event_pointer_get_dx(ptr);
                dy += libinput_event_pointer_get_dy(ptr);
            } else if (type == LIBINPUT_EVENT_POINTER_SCROLL_FINGER) {
                ++scroll;
            } else if (type == LIBINPUT_EVENT_POINTER_BUTTON) {
                struct libinput_event_pointer *ptr = libinput_event_get_pointer_event(event);
                if (libinput_event_pointer_get_button_state(ptr) == LIBINPUT_BUTTON_STATE_PRESSED) ++down;
                else ++up;
                printf("BUTTON code=%u state=%d\n", libinput_event_pointer_get_button(ptr), libinput_event_pointer_get_button_state(ptr));
            }
            libinput_event_destroy(event);
        }
    }
    printf("RESULT motion=%d dx=%.3f dy=%.3f finger-scroll=%d button-down=%d button-up=%d\n", motion, dx, dy, scroll, down, up);
    libinput_unref(li);
    return (motion > 0 && scroll > 0 && down > 0 && up == down) ? 0 : 5;
}
