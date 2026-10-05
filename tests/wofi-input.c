/* Runtime-only instrumentation: inspect real GTK geometry and send GDK input.
 * No production callbacks or widget allocations are replaced by this probe. */
#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static GtkWidget *window, *scroll, *flow, *input;
static const char *directory;
static unsigned serial;

static void discover(GtkWidget *widget, gpointer unused) {
  (void)unused;
  if (GTK_IS_WINDOW(widget)) window = widget;
  if (GTK_IS_SCROLLED_WINDOW(widget)) scroll = widget;
  if (GTK_IS_FLOW_BOX(widget)) flow = widget;
  if (GTK_IS_ENTRY(widget)) input = widget;
  if (GTK_IS_CONTAINER(widget)) gtk_container_foreach(GTK_CONTAINER(widget), discover, NULL);
}

static void snapshot(void) {
  char *path = g_build_filename(directory, "state.tmp", NULL);
  FILE *out = fopen(path, "w");
  if (!out) exit(2);
  GtkAdjustment *a = gtk_scrolled_window_get_hadjustment(GTK_SCROLLED_WINDOW(scroll));
  GtkAdjustment *v = gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(scroll));
  fprintf(out, "{\"serial\":%u,\"horizontal\":%.3f,\"upper\":%.3f,\"page\":%.3f,"
          "\"vertical\":%.3f,\"vertical_upper\":%.3f,\"vertical_page\":%.3f,\"tiles\":[",
          serial, gtk_adjustment_get_value(a), gtk_adjustment_get_upper(a),
          gtk_adjustment_get_page_size(a), gtk_adjustment_get_value(v),
          gtk_adjustment_get_upper(v), gtk_adjustment_get_page_size(v));
  GList *children = gtk_container_get_children(GTK_CONTAINER(flow));
  gboolean first = TRUE;
  for (GList *c = children; c; c = c->next) {
    GtkWidget *child = c->data;
    GtkAllocation box;
    gtk_widget_get_allocation(child, &box);
    fprintf(out, "%s{\"index\":%d,\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d,"
            "\"visible\":%s,\"selected\":%s}", first ? "" : ",",
            gtk_flow_box_child_get_index(GTK_FLOW_BOX_CHILD(child)), box.x, box.y, box.width, box.height,
            gtk_widget_get_child_visible(child) ? "true" : "false",
            gtk_flow_box_child_is_selected(GTK_FLOW_BOX_CHILD(child)) ? "true" : "false");
    first = FALSE;
  }
  g_list_free(children);
  fprintf(out, "]}\n");
  fclose(out);
  char *target = g_build_filename(directory, "state.json", NULL);
  if (rename(path, target)) exit(2);
  g_free(target);
  g_free(path);
}

static void inject(const char *command, double x, double y) {
  GdkEventType type = GDK_SCROLL;
  if (!strcmp(command, "TOUCH_BEGIN")) type = GDK_TOUCH_BEGIN;
  if (!strcmp(command, "TOUCH_UPDATE")) type = GDK_TOUCH_UPDATE;
  if (!strcmp(command, "TOUCH_END")) type = GDK_TOUCH_END;
  GdkEvent *event = gdk_event_new(type);
  GdkWindow *surface = gtk_widget_get_window(flow);
  event->any.window = g_object_ref(surface);
  event->any.send_event = TRUE;
  GdkSeat *seat = gdk_display_get_default_seat(gtk_widget_get_display(window));
  gdk_event_set_device(event, gdk_seat_get_pointer(seat));
  guint32 time = (guint32)(g_get_monotonic_time() / 1000);
  if (type == GDK_SCROLL) {
    event->scroll.time = time;
    event->scroll.x = 400;
    event->scroll.y = 150;
    event->scroll.direction = !strcmp(command, "WHEEL") ? GDK_SCROLL_DOWN : GDK_SCROLL_SMOOTH;
    event->scroll.delta_x = x;
    event->scroll.delta_y = y;
    /* Deliver to the real scrolled-window signal and GTK class handler. */
    gtk_widget_event(scroll, event);
  } else {
    event->touch.time = time;
    event->touch.x = x;
    event->touch.y = y;
    event->touch.x_root = x;
    event->touch.y_root = y;
    event->touch.sequence = (GdkEventSequence *)0x1;
    event->touch.emulating_pointer = FALSE;
    gtk_main_do_event(event);
  }
  gdk_event_free(event);
}

static gboolean tick(gpointer unused) {
  (void)unused;
  if (!window) {
    GList *windows = gtk_window_list_toplevels();
    for (GList *w = windows; w; w = w->next) discover(w->data, NULL);
    g_list_free(windows);
  }
  if (!flow || !scroll || !input) return TRUE;
  char *path = g_build_filename(directory, "command", NULL);
  FILE *in = fopen(path, "r");
  if (in) {
    unsigned id;
    char command[40];
    double x = 0, y = 0;
    if (fscanf(in, "%u %39s %lf %lf", &id, command, &x, &y) >= 2 && id > serial) {
      serial = id;
      inject(command, x, y);
    }
    fclose(in);
  }
  g_free(path);
  snapshot();
  return TRUE;
}

void gtk_main(void) {
  void (*original)(void) = dlsym(RTLD_NEXT, "gtk_main");
  directory = getenv("KWAK_WOFI_TEST_DIR");
  if (directory) g_timeout_add(40, tick, NULL);
  original();
}
