import logging
import sys

from gi.repository import Gtk

log = logging.getLogger(__name__)


if sys.platform != "darwin" and Gtk.get_major_version() == 3:  # noqa C901
    import gi

    macos_app = None

    def open_file(macos_app, path, application):
        if path == __file__:
            return False

        application.new_session(filename=path)

        return True

    def block_termination(macos_app, application):
        quit = application.quit()
        return not quit

    def macos_init(application):
        try:
            gi.require_version("GtkosxApplication", "1.0")
        except ValueError:
            log.warning("GtkosxApplication not found")
            return

        from gi.repository import GtkosxApplication

        global macos_app
        if macos_app:
            return

        macos_app = GtkosxApplication.Application.get()

        macos_app.connect("NSApplicationOpenFile", open_file, application)
        macos_app.connect(
            "NSApplicationBlockTermination", block_termination, application
        )

elif sys.platform == "darwin" and Gtk.get_major_version() == 4:
    from gi.repository import GLib

    def new_shortcut_with_args(shortcut, signal, *args):
        shortcut = Gtk.Shortcut.new(
            trigger=Gtk.ShortcutTrigger.parse_string(shortcut),
            action=Gtk.SignalAction.new(signal),
        )
        if args:
            shortcut.set_arguments(GLib.Variant.new_tuple(*args))
        return shortcut

    def add_move_binding(widget_class, shortcut, step, count):
        widget_class.add_shortcut(
            new_shortcut_with_args(
                shortcut,
                "move-cursor",
                GLib.Variant.new_int32(step),
                GLib.Variant.new_int32(count),
                GLib.Variant.new_boolean(False),
            )
        )

        widget_class.add_shortcut(
            new_shortcut_with_args(
                "|".join(f"<Shift>{s}" for s in shortcut.split("|")),
                "move-cursor",
                GLib.Variant.new_int32(step),
                GLib.Variant.new_int32(count),
                GLib.Variant.new_boolean(True),
            )
        )

    for widget_class in (Gtk.Text, Gtk.TextView):
        for shortcut, signal in [
            ("<Meta>x", "cut-clipboard"),
            ("<Meta>c", "copy-clipboard"),
            ("<Meta>v", "paste-clipboard"),
        ]:
            widget_class.add_shortcut(new_shortcut_with_args(shortcut, signal))

        for shortcut, action in [
            ("<Meta>z", "text.undo"),
            ("<Meta><Shift>z", "text.redo"),
        ]:
            widget_class.add_shortcut(
                Gtk.Shortcut.new(
                    trigger=Gtk.ShortcutTrigger.parse_string(shortcut),
                    action=Gtk.NamedAction.new(action),
                )
            )

        for shortcut, step, count in [
            ("<Meta>Up|<Meta>KP_Up", Gtk.MovementStep.BUFFER_ENDS, -1),
            ("<Meta>Down|<Meta>KP_Down", Gtk.MovementStep.BUFFER_ENDS, 1),
            ("<Meta>Left|<Meta>KP_Left", Gtk.MovementStep.DISPLAY_LINE_ENDS, -1),
            ("<Meta>Right|<Meta>KP_Right", Gtk.MovementStep.DISPLAY_LINE_ENDS, 1),
            ("<Alt>Left|<Alt>KP_Left", Gtk.MovementStep.WORDS, -1),
            ("<Alt>Right|<Alt>KP_Right", Gtk.MovementStep.WORDS, 1),
        ]:
            add_move_binding(widget_class, shortcut, step, count)

    # Gtk.Text

    Gtk.Text.add_shortcut(
        Gtk.Shortcut.new(
            trigger=Gtk.ShortcutTrigger.parse_string("<Meta>a"),
            action=Gtk.CallbackAction.new(lambda self, data: self.select_region(0, -1)),
        )
    )
    Gtk.Text.add_shortcut(
        new_shortcut_with_args(
            "<Meta><Shift>a",
            "move-cursor",
            GLib.Variant.new_int32(Gtk.MovementStep.VISUAL_POSITIONS),
            GLib.Variant.new_int32(0),
            GLib.Variant.new_boolean(False),
        )
    )

    # Gtk.TextView

    Gtk.TextView.add_shortcut(
        new_shortcut_with_args("<Meta>a", "select-all", GLib.Variant.new_boolean(True))
    )
    Gtk.TextView.add_shortcut(
        new_shortcut_with_args(
            "<Meta><Shift>a", "select-all", GLib.Variant.new_boolean(False)
        )
    )

    def macos_init(application):
        pass

else:

    def macos_init(application):
        pass
