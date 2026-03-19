import sys
from typing import Any, cast

from faltoobot.config import Config

from .bridge import RuntimeBridge
from .formatting import queue_text, status_line, transcript_text

MenuItem = tuple[str, str, str]


def app_menu_items() -> tuple[MenuItem, ...]:
    return (("Quit Faltoobot", "terminate:", "q"),)


def edit_menu_items() -> tuple[MenuItem, ...]:
    return (
        ("Undo", "undo:", "z"),
        ("Redo", "redo:", "Z"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    )


def should_submit_for_selector(selector: Any) -> bool:
    return str(selector) == "insertNewline:"


def run_macos_chat_app(config: Config | None = None, name: str | None = None) -> None:  # noqa: C901, PLR0915
    if sys.platform != "darwin":
        raise SystemExit("The macOS desktop app is available on macOS only.")

    import importlib

    AppKit = cast(Any, importlib.import_module("AppKit"))
    objc = cast(Any, importlib.import_module("objc"))
    foundation = cast(Any, importlib.import_module("Foundation"))
    AppHelper = cast(Any, importlib.import_module("PyObjCTools.AppHelper"))
    NSMakeRect = foundation.NSMakeRect
    NSObject = foundation.NSObject

    def schedule_on_main(fn: Any) -> None:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    def show_alert(message: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Faltoobot")
        alert.setInformativeText_(message)
        alert.runModal()

    def make_label(frame: Any, *, bold: bool = False) -> Any:
        field = AppKit.NSTextField.labelWithString_("")
        field.setFrame_(frame)
        if bold:
            field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12))
        return field

    def make_text_view(frame: Any, *, editable: bool) -> tuple[Any, Any]:
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(AppKit.NSBezelBorder)
        view = AppKit.NSTextView.alloc().initWithFrame_(frame)
        view.setEditable_(editable)
        view.setAllowsUndo_(editable)
        view.setRichText_(False)
        view.setAutomaticQuoteSubstitutionEnabled_(False)
        view.setAutomaticDashSubstitutionEnabled_(False)
        view.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(13, 0))
        scroll.setDocumentView_(view)
        return scroll, view

    def build_menu() -> None:
        app = AppKit.NSApplication.sharedApplication()
        menubar = AppKit.NSMenu.alloc().init()
        app.setMainMenu_(menubar)

        app_menu_item = AppKit.NSMenuItem.alloc().init()
        menubar.addItem_(app_menu_item)
        app_menu = AppKit.NSMenu.alloc().initWithTitle_("Faltoobot")
        for title, action, key in app_menu_items():
            app_menu.addItem_(
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, key
                )
            )
        app_menu_item.setSubmenu_(app_menu)

        edit_menu_item = AppKit.NSMenuItem.alloc().init()
        menubar.addItem_(edit_menu_item)
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in edit_menu_items():
            edit_menu.addItem_(
                AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, action, key
                )
            )
        edit_menu_item.setSubmenu_(edit_menu)

    class WindowController(AppKit.NSObject):
        bridge: RuntimeBridge
        closed: bool

        def initWithConfig_name_(
            self, app_config: Config | None, app_name: str | None
        ) -> Any:
            self = objc.super(WindowController, self).init()
            if self is None:
                return None
            self.bridge = RuntimeBridge(config=app_config, name=app_name)
            self.closed = False
            self.build_window()
            return self

        def build_window(self) -> None:
            rect = NSMakeRect(0, 0, 980, 760)
            style = (
                AppKit.NSWindowStyleMaskTitled
                | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskResizable
                | AppKit.NSWindowStyleMaskMiniaturizable
            )
            self.window = (
                AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                    rect,
                    style,
                    AppKit.NSBackingStoreBuffered,
                    False,
                )
            )
            self.window.setTitle_("Faltoobot")
            self.window.center()
            self.window.setDelegate_(self)

            content = self.window.contentView()
            self.session_label = make_label(NSMakeRect(20, 724, 940, 20), bold=True)
            self.workspace_label = make_label(NSMakeRect(20, 702, 940, 18))
            self.queue_label = make_label(NSMakeRect(20, 610, 300, 18), bold=True)
            self.input_label = make_label(NSMakeRect(20, 180, 300, 18), bold=True)
            self.status_label = make_label(NSMakeRect(20, 18, 940, 18))
            self.queue_label.setStringValue_("Queue")
            self.input_label.setStringValue_("Message")

            self.transcript_scroll, self.transcript_view = make_text_view(
                NSMakeRect(20, 282, 940, 410), editable=False
            )
            self.queue_scroll, self.queue_view = make_text_view(
                NSMakeRect(20, 202, 940, 100), editable=False
            )
            self.input_scroll, self.input_view = make_text_view(
                NSMakeRect(20, 50, 820, 120), editable=True
            )
            self.input_view.setDelegate_(self)

            self.send_button = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(852, 124, 108, 32)
            )
            self.send_button.setTitle_("Send")
            self.send_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.send_button.setTarget_(self)
            self.send_button.setAction_("sendAction:")

            self.interrupt_button = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(852, 84, 108, 32)
            )
            self.interrupt_button.setTitle_("Interrupt")
            self.interrupt_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.interrupt_button.setTarget_(self)
            self.interrupt_button.setAction_("interruptAction:")

            self.reset_button = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(852, 44, 108, 32)
            )
            self.reset_button.setTitle_("New Chat")
            self.reset_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.reset_button.setTarget_(self)
            self.reset_button.setAction_("resetAction:")

            for view in (
                self.session_label,
                self.workspace_label,
                self.queue_label,
                self.input_label,
                self.status_label,
                self.transcript_scroll,
                self.queue_scroll,
                self.input_scroll,
                self.send_button,
                self.interrupt_button,
                self.reset_button,
            ):
                content.addSubview_(view)

        def refresh_ui(self) -> None:
            snapshot = self.bridge.snapshot()
            self.session_label.setStringValue_(snapshot.session_title)
            self.workspace_label.setStringValue_(snapshot.workspace)
            self.transcript_view.setString_(transcript_text(snapshot.entries))
            self.queue_view.setString_(
                queue_text((item.content, item.paused) for item in snapshot.queued)
            )
            self.status_label.setStringValue_(
                status_line(
                    snapshot.status,
                    replying=snapshot.replying,
                    queued=len(snapshot.queued),
                )
            )
            self.interrupt_button.setEnabled_(snapshot.replying)
            self.send_button.setTitle_("Queue" if snapshot.replying else "Send")
            self.scroll_to_bottom(self.transcript_view)
            self.scroll_to_bottom(self.queue_view)

        def scroll_to_bottom(self, view: Any) -> None:
            text = str(view.string())
            view.scrollRangeToVisible_((len(text), 0))

        def schedule_refresh(self) -> None:
            schedule_on_main(self.refresh_ui)

        def start(self) -> None:
            self.bridge.start(self.schedule_refresh)
            self.refresh_ui()

        def closeBridge(self) -> None:
            if self.closed:
                return
            self.closed = True
            self.bridge.close()

        def sendAction_(self, _sender: Any) -> None:
            prompt = str(self.input_view.string())
            if not prompt.strip():
                return
            self.input_view.setString_("")
            if not self.bridge.submit(prompt):
                self.window.performClose_(None)
                return
            self.refresh_ui()

        def textView_doCommandBySelector_(self, view: Any, selector: Any) -> bool:
            if view != self.input_view:
                return False
            if not should_submit_for_selector(selector):
                return False
            self.sendAction_(None)
            return True

        def interruptAction_(self, _sender: Any) -> None:
            self.bridge.interrupt()
            self.refresh_ui()

        def resetAction_(self, _sender: Any) -> None:
            self.input_view.setString_("/reset")
            self.sendAction_(None)

        def windowWillClose_(self, _notification: Any) -> None:
            self.closeBridge()
            AppKit.NSApplication.sharedApplication().terminate_(None)

    class AppDelegate(NSObject):
        def initWithConfig_name_(
            self, app_config: Config | None, app_name: str | None
        ) -> Any:
            self = objc.super(AppDelegate, self).init()
            if self is None:
                return None
            self.window_controller = WindowController.alloc().initWithConfig_name_(
                app_config, app_name
            )
            return self

        def applicationDidFinishLaunching_(self, _notification: Any) -> None:
            try:
                self.window_controller.start()
            except Exception as exc:  # noqa: BLE001
                show_alert(str(exc))
                AppKit.NSApplication.sharedApplication().terminate_(None)
                return
            self.window_controller.window.makeKeyAndOrderFront_(None)
            self.window_controller.window.makeFirstResponder_(
                self.window_controller.input_view
            )
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        def applicationShouldTerminateAfterLastWindowClosed_(self, _app: Any) -> bool:
            return True

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    build_menu()
    delegate = AppDelegate.alloc().initWithConfig_name_(config, name)
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
