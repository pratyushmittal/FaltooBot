from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import Worker, WorkerState

from ..xray import (
    ChangeOverview,
    FileOverview,
    FileSymbolOverview,
    XrayReference,
    get_change_overview,
    get_file_overview,
)


@dataclass(slots=True)
class XrayTreeNode:
    label: str
    reference: XrayReference | None = None
    expand: bool = False
    children: list["XrayTreeNode"] = field(default_factory=list)


class XrayModal(ModalScreen[XrayReference | None]):
    BINDINGS = [
        Binding("escape", "cancel", priority=True, show=False),
        Binding("enter", "toggle_node", priority=True, show=False),
        Binding("shift+enter", "open_reference", priority=True, show=False),
    ]

    DEFAULT_CSS = """
    XrayModal {
        align: center middle;
    }

    #xray-dialog {
        width: 120;
        max-width: 120;
        height: 32;
        max-height: 32;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }

    #xray-title {
        margin-bottom: 1;
    }

    #xray-tree {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        nodes: list[XrayTreeNode] | None = None,
        load_nodes: Callable[[], Awaitable[list[XrayTreeNode]]] | None = None,
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.nodes = [] if nodes is None else nodes
        self.load_nodes = load_nodes
        self._ready = False
        self._worker: Worker[list[XrayTreeNode]] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="xray-dialog"):
            yield Static(self.dialog_title, id="xray-title")
            yield Tree("Overview", id="xray-tree")

    def on_mount(self) -> None:
        tree = self.query_one("#xray-tree", Tree)
        tree.show_root = False
        tree.focus()
        if self.load_nodes is None:
            self._set_nodes(self.nodes)
            return
        self._set_nodes([XrayTreeNode(label="Generating file overview...")])
        self._worker = self.run_worker(
            self.load_nodes(),
            group="xray-load",
            exclusive=True,
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_node(self) -> None:
        tree = self.query_one("#xray-tree", Tree)
        if tree.cursor_node is not None:
            tree.cursor_node.toggle()

    def action_open_reference(self) -> None:
        reference = self._selected_reference()
        if reference is not None:
            self.dismiss(reference)
            return
        tree = self.query_one("#xray-tree", Tree)
        if tree.cursor_node is not None:
            tree.cursor_node.toggle()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._worker:
            return
        if event.state is WorkerState.SUCCESS:
            nodes = event.worker.result
            if nodes is not None:
                self._set_nodes(nodes)
            return
        if event.state is WorkerState.ERROR:
            error = event.worker.error
            message = str(error) if error is not None else "Failed to load overview."
            self._set_nodes([XrayTreeNode(label=f"Error — {message}")])

    def _selected_reference(self) -> XrayReference | None:
        tree = self.query_one("#xray-tree", Tree)
        if tree.cursor_node is None:
            return None
        return tree.cursor_node.data

    def _set_nodes(self, nodes: list[XrayTreeNode]) -> None:
        self.nodes = nodes
        self._ready = False
        tree = self.query_one("#xray-tree", Tree)
        tree.clear()
        tree.root.expand()
        self._populate_tree(tree)

        def focus_initial_node() -> None:
            target = tree.root
            if tree.root.children:
                target = tree.root.children[0]
                if target.children:
                    target = target.children[0]
            tree.move_cursor(target)
            self._ready = True

        self.call_after_refresh(focus_initial_node)

    def _populate_tree(self, tree: Tree) -> None:
        for node in self.nodes:
            _add_tree_node(tree.root, node)


def _add_tree_node(
    parent: TreeNode[XrayReference | None],
    node: XrayTreeNode,
) -> TreeNode[XrayReference | None] | None:
    tree_node = parent.add(node.label, data=node.reference, expand=node.expand)
    first_reference = tree_node if node.reference is not None else None
    for child in node.children:
        child_reference = _add_tree_node(tree_node, child)
        if first_reference is None:
            first_reference = child_reference
    return first_reference


def change_overview_modal(workspace: Path, paths: list[Path]) -> XrayModal:
    async def load_nodes() -> list[XrayTreeNode]:
        overview = await get_change_overview(workspace, paths)
        return _change_overview_nodes(overview)

    return XrayModal(title="Change overview", load_nodes=load_nodes)


def file_overview_modal(workspace: Path, path: Path) -> XrayModal:
    async def load_nodes() -> list[XrayTreeNode]:
        overview = await get_file_overview(workspace, path)
        return _file_overview_nodes(workspace, path, overview)

    return XrayModal(title=f"File overview: {path.name}", load_nodes=load_nodes)


def _change_overview_nodes(overview: ChangeOverview) -> list[XrayTreeNode]:
    files = [
        XrayTreeNode(
            label=f"{file.path} — {file.about}",
            expand=True,
            children=[
                XrayTreeNode(label=_reference_label(reference), reference=reference)
                for reference in file.references
            ],
        )
        for file in overview.files
    ]
    return [
        XrayTreeNode(label=f"Summary — {overview.summary}", expand=True),
        XrayTreeNode(label="Files", expand=True, children=files),
    ]


def _file_overview_nodes(
    workspace: Path,
    path: Path,
    overview: FileOverview,
) -> list[XrayTreeNode]:
    return [
        XrayTreeNode(
            label="Important changes",
            expand=True,
            children=_spaced_nodes(
                _symbol_nodes(workspace, path, overview.important_changes)
            ),
        )
    ]


def _spaced_nodes(nodes: list[XrayTreeNode]) -> list[XrayTreeNode]:
    spaced: list[XrayTreeNode] = []
    for index, node in enumerate(nodes):
        if index:
            spaced.append(XrayTreeNode(label=""))
        spaced.append(node)
    return spaced


def _reference_label(reference: XrayReference) -> str:
    location = reference.path
    if reference.line_number is not None:
        location = f"{location}:{reference.line_number}"
    if reference.summary:
        return f"{location} — {reference.label} — {reference.summary}"
    return f"{location} — {reference.label}"


def _symbol_nodes(
    workspace: Path,
    path: Path,
    symbols: list[FileSymbolOverview],
) -> list[XrayTreeNode]:
    nodes: list[XrayTreeNode] = []
    for symbol in symbols:
        reference = (
            XrayReference(
                path=str(path.resolve().relative_to(workspace.resolve())),
                line_number=symbol.line_number,
                label=symbol.name,
                summary=symbol.summary,
            )
            if symbol.line_number is not None
            else None
        )
        calls = _symbol_nodes(workspace, path, symbol.calls)
        nodes.append(
            XrayTreeNode(
                label=f"{symbol.name} — {symbol.summary}",
                reference=reference,
                expand=False,
                children=calls,
            )
        )
    return nodes
