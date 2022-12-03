"""The main application window."""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

from gaphor.abc import ActionProvider, Service
from gaphor.core import event_handler, gettext
from gaphor.event import (
    ActionEnabled,
    ActiveSessionChanged,
    ModelLoaded,
    ModelSaved,
    SessionCreated,
    SessionShutdownRequested,
)
from gaphor.i18n import translated_ui_string
from gaphor.services.modelinglanguage import ModelingLanguageChanged
from gaphor.services.undomanager import UndoManagerStateChanged
from gaphor.ui.abc import UIComponent
from gaphor.ui.actiongroup import window_action_group
from gaphor.ui.layout import deserialize, is_maximized
from gaphor.ui.notification import InAppNotifier

log = logging.getLogger(__name__)


def new_builder():
    builder = Gtk.Builder()
    ui_file = "mainwindow.glade" if Gtk.get_major_version() == 3 else "mainwindow.ui"
    builder.add_from_string(translated_ui_string("gaphor.ui", ui_file))
    return builder


def create_hamburger_model(export_menu, tools_menu):
    model = Gio.Menu.new()

    part = Gio.Menu.new()
    part.append(gettext("New Model…"), "app.new-model")
    part.append(gettext("Open Model…"), "app.file-open")
    model.append_section(None, part)

    part = Gio.Menu.new()
    part.append(gettext("Save"), "win.file-save")
    part.append(gettext("Save As…"), "win.file-save-as")
    part.append_submenu(gettext("Export"), export_menu)
    model.append_section(None, part)

    part = Gio.Menu.new()
    part.append_submenu(gettext("Tools"), tools_menu)
    model.append_section(None, part)

    part = Gio.Menu.new()
    part.append(gettext("Keyboard Shortcuts"), "app.shortcuts")
    part.append(gettext("About Gaphor"), "app.about")
    model.append_section(None, part)

    return model


def create_modeling_language_model(modeling_language):
    model = Gio.Menu.new()
    for id, name in modeling_language.modeling_languages:
        menu_item = Gio.MenuItem.new(name, "win.select-modeling-language")
        menu_item.set_attribute_value("target", GLib.Variant.new_string(id))
        model.append_item(menu_item)
    return model


def create_diagram_types_model(modeling_language):
    model = Gio.Menu.new()

    part = Gio.Menu.new()
    for id, name, _ in modeling_language.diagram_types:
        menu_item = Gio.MenuItem.new(name, "win.create-diagram")
        menu_item.set_attribute_value("target", GLib.Variant.new_string(id))
        part.append_item(menu_item)
    model.append_section(None, part)

    part = Gio.Menu.new()
    menu_item = Gio.MenuItem.new(gettext("New Generic Diagram"), "win.create-diagram")
    menu_item.set_attribute_value("target", GLib.Variant.new_string(""))
    part.append_item(menu_item)
    model.append_section(None, part)

    return model


def popup_set_model(popup, model):
    if Gtk.get_major_version() == 3:
        popup.bind_model(model, None)
    else:
        popup.set_menu_model(model)


class MainWindow(Service, ActionProvider):
    """The main window for the application.

    It contains a Namespace-based tree view and a menu and a statusbar.
    """

    size = property(lambda s: s.properties.get("ui.window-size", (860, 580)))

    def __init__(
        self,
        event_manager,
        component_registry,
        properties,
        modeling_language,
        export_menu,
        tools_menu,
    ):
        self.event_manager = event_manager
        self.component_registry = component_registry
        self.properties = properties
        self.modeling_language = modeling_language
        self.export_menu = export_menu
        self.tools_menu = tools_menu

        self.window: Gtk.Window = None
        self.action_group: Gio.ActionGroup = None
        self.title: Gtk.Label = None
        self.subtitle: Gtk.Label = None
        self.filename: Path | None = None
        self.model_changed = False
        self.modeling_language_name = None
        self.diagram_types = None
        self.in_app_notifier = None

        event_manager.subscribe(self._on_file_manager_state_changed)

    def shutdown(self):
        if self.window:
            self.window.destroy()
            self.window = None

        em = self.event_manager
        em.unsubscribe(self._on_file_manager_state_changed)
        em.unsubscribe(self._on_undo_manager_state_changed)
        em.unsubscribe(self._on_action_enabled)
        em.unsubscribe(self._on_modeling_language_selection_changed)
        if self.in_app_notifier:
            em.unsubscribe(self.in_app_notifier.handle)
            self.in_app_notifier = None

    def get_ui_component(self, name):
        return self.component_registry.get(UIComponent, name)

    def open(self, gtk_app=None):
        """Open the main window."""

        builder = new_builder()
        self.window = builder.get_object("main-window")
        self.window.set_application(gtk_app)

        select_modeling_language = builder.get_object("select-modeling-language")
        popup_set_model(
            select_modeling_language,
            create_modeling_language_model(self.modeling_language),
        )
        self.modeling_language_name = builder.get_object("modeling-language-name")

        self.diagram_types = builder.get_object("diagram-types")
        popup_set_model(
            self.diagram_types, create_diagram_types_model(self.modeling_language)
        )

        hamburger = builder.get_object("hamburger")
        popup_set_model(
            hamburger,
            create_hamburger_model(self.export_menu.menu, self.tools_menu.menu),
        )

        self.title = builder.get_object("title")
        self.subtitle = builder.get_object("subtitle")
        self.set_title()

        self.window.set_default_size(*self.size)

        def _factory(name):
            comp = self.get_ui_component(name)
            return comp.open()

        main_content = builder.get_object("main-content")
        deserialize(
            main_content,
            (importlib.resources.files("gaphor.ui") / "layout.xml").read_text(),
            _factory,
            self.properties,
        )

        self.action_group, shortcuts = window_action_group(self.component_registry)
        self.window.insert_action_group("win", self.action_group)

        self._on_modeling_language_selection_changed()

        self.window.set_resizable(True)
        if Gtk.get_major_version() == 3:
            self.window.show_all()
            self.window.add_accel_group(shortcuts)
            self.window.connect("delete-event", self._on_window_close_request)
            self.window.connect("size-allocate", self._on_window_size_allocate)
        else:
            self.window.add_controller(Gtk.ShortcutController.new_for_model(shortcuts))
            self.window.connect("close-request", self._on_window_close_request)
            self.window.connect("notify::default-height", self._on_window_size_changed)
            self.window.connect("notify::default-width", self._on_window_size_changed)
            self.window.show()

        self.window.connect("notify::is-active", self._on_window_active)

        self.in_app_notifier = InAppNotifier(builder)
        em = self.event_manager
        em.subscribe(self._on_undo_manager_state_changed)
        em.subscribe(self._on_action_enabled)
        em.subscribe(self._on_modeling_language_selection_changed)
        em.subscribe(self.in_app_notifier.handle)

    def set_title(self):
        """Sets the window title."""
        if not self.window:
            return

        if self.filename:
            p = self.filename
            title = p.stem
            subtitle = str(p).replace(str(Path.home()), "~")
        else:
            title = "Gaphor"
            subtitle = gettext("New model")
        if self.model_changed:
            title += " [" + gettext("edited") + "]"
        self.title.set_text(title)
        self.subtitle.set_text(subtitle)
        self.window.set_title(title)

    # Signal callbacks:

    @event_handler(SessionCreated, ModelLoaded, ModelSaved)
    def _on_file_manager_state_changed(
        self, event: SessionCreated | ModelLoaded | ModelSaved
    ):
        self.model_changed = False
        self.filename = Path(event.filename) if event.filename else None
        self.set_title()
        if self.window:
            self.window.present()

    @event_handler(UndoManagerStateChanged)
    def _on_undo_manager_state_changed(self, event):
        undo_manager = event.service
        if self.model_changed != undo_manager.can_undo():
            self.model_changed = undo_manager.can_undo()
            self.set_title()

    @event_handler(ActionEnabled)
    def _on_action_enabled(self, event):
        if self.action_group and event.scope == "win":
            a = self.action_group.lookup_action(event.name)
            a.set_enabled(event.enabled)

    @event_handler(ModelingLanguageChanged)
    def _on_modeling_language_selection_changed(self, event=None):
        if self.modeling_language_name:
            self.modeling_language_name.set_label(
                gettext("Profile: {name}").format(name=self.modeling_language.name)
            )
        if self.diagram_types:
            popup_set_model(
                self.diagram_types, create_diagram_types_model(self.modeling_language)
            )

    def _on_window_active(self, window, prop):
        self.event_manager.handle(ActiveSessionChanged(self))

    def _on_window_close_request(self, window, event=None):
        self.event_manager.handle(SessionShutdownRequested(self))
        return True

    if Gtk.get_major_version() == 3:

        def _on_window_size_allocate(self, window, allocation):
            if not is_maximized(window):
                width, height = window.get_size()
                self.properties.set("ui.window-size", (width, height))

    else:

        def _on_window_size_changed(self, window, gspec):
            if not is_maximized(window):
                width, height = window.get_default_size()
                self.properties.set("ui.window-size", (width, height))
