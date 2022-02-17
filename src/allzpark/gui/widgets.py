
import json
import logging
from typing import List
from ._vendor.Qt5 import QtCore, QtGui, QtWidgets
from ._vendor import qoverview
from .. import lib
from ..core import AbstractScope, SuiteTool
from . import resources as res
from .models import (
    parse_icon,
    QSingleton,
    JsonModel,
    ToolsModel,
    ResolvedEnvironmentModel,
    ResolvedEnvironmentProxyModel,
    ContextDataModel,
)


log = logging.getLogger("allzpark")


def _load_backends():

    def try_avalon_backend():
        from .widgets_avalon import AvalonWidget
        return AvalonWidget

    def try_sg_sync_backend():
        from .widgets_sg_sync import ShotGridSyncWidget
        return ShotGridSyncWidget

    return {
        "avalon": try_avalon_backend,
        "sg_sync": try_sg_sync_backend,
        # could be ftrack, or shotgrid, could be... (see core module)
    }


class ComboBox(QtWidgets.QComboBox):

    def __init__(self, *args, **kwargs):
        super(ComboBox, self).__init__(*args, **kwargs)
        delegate = QtWidgets.QStyledItemDelegate(self)
        self.setItemDelegate(delegate)
        # https://stackoverflow.com/a/21019371
        # also see `app.AppProxyStyle`


class BusyEventFilterSingleton(QtCore.QObject, metaclass=QSingleton):
    overwhelmed = QtCore.Signal(str)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (
            QtCore.QEvent.Scroll,
            QtCore.QEvent.KeyPress,
            QtCore.QEvent.KeyRelease,
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonRelease,
            QtCore.QEvent.MouseButtonDblClick,
        ):
            self.overwhelmed.emit("Not allowed at this moment.")
            return True
        return False


class BusyWidget(QtWidgets.QWidget):
    """
    Instead of toggling QWidget.setEnabled() to block user inputs and makes
    the appearance looks glitchy between short time processes, install an
    eventFilter to block keyboard and mouse events plus a busy cursor looks
    better.
    """
    _instances = []

    def __init__(self, *args, **kwargs):
        super(BusyWidget, self).__init__(*args, **kwargs)
        self._busy_works = set()
        self._entered = False
        self._filter = BusyEventFilterSingleton(self)
        self._instances.append(self)

    @classmethod
    def instances(cls):
        return cls._instances[:]

    @QtCore.Slot(str)  # noqa
    def set_overwhelmed(self, worker: str):
        if not self._busy_works:
            if self._entered:
                self._over_busy_cursor(True)
            self._block_children(True)

        self._busy_works.add(worker)

    @QtCore.Slot(str)  # noqa
    def pop_overwhelmed(self, worker: str):
        if worker in self._busy_works:
            self._busy_works.remove(worker)

        if not self._busy_works:
            if self._entered:
                self._over_busy_cursor(False)
            self._block_children(False)

    def enterEvent(self, event):
        if self._busy_works:
            self._over_busy_cursor(True)
        self._entered = True
        super(BusyWidget, self).enterEvent(event)

    def leaveEvent(self, event):
        if self._busy_works:
            self._over_busy_cursor(False)
        self._entered = False
        super(BusyWidget, self).leaveEvent(event)

    def _over_busy_cursor(self, over):
        if over:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.BusyCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _block_children(self, block):

        def action(w):
            if block:
                w.installEventFilter(self._filter)
            else:
                w.removeEventFilter(self._filter)

        def iter_children(w):
            for c in w.children():
                yield c
                for gc in iter_children(c):
                    yield gc

        for child in list(iter_children(self)):
            action(child)
        action(self)


class SlidePageWidget(QtWidgets.QStackedWidget):
    """Stacked widget that nicely slides between its pages"""

    directions = {
        "left": QtCore.QPoint(-1, 0),
        "right": QtCore.QPoint(1, 0),
        "up": QtCore.QPoint(0, 1),
        "down": QtCore.QPoint(0, -1)
    }

    def slide_view(self, index, direction="right"):
        if self.currentIndex() == index:
            return

        offset_direction = self.directions.get(direction)
        if offset_direction is None:
            log.warning("BUG: invalid slide direction: {}".format(direction))
            return

        width = self.frameRect().width()
        height = self.frameRect().height()
        offset = QtCore.QPoint(
            offset_direction.x() * width,
            offset_direction.y() * height
        )

        new_page = self.widget(index)
        new_page.setGeometry(0, 0, width, height)
        curr_pos = new_page.pos()
        new_page.move(curr_pos + offset)
        new_page.show()
        new_page.raise_()

        current_page = self.currentWidget()

        b_pos = QtCore.QByteArray(b"pos")

        anim_old = QtCore.QPropertyAnimation(current_page, b_pos, self)
        anim_old.setDuration(250)
        anim_old.setStartValue(curr_pos)
        anim_old.setEndValue(curr_pos - offset)
        anim_old.setEasingCurve(QtCore.QEasingCurve.OutQuad)

        anim_new = QtCore.QPropertyAnimation(new_page, b_pos, self)
        anim_new.setDuration(250)
        anim_new.setStartValue(curr_pos + offset)
        anim_new.setEndValue(curr_pos)
        anim_new.setEasingCurve(QtCore.QEasingCurve.OutQuad)

        anim_group = QtCore.QParallelAnimationGroup(self)
        anim_group.addAnimation(anim_old)
        anim_group.addAnimation(anim_new)

        def slide_finished():
            self.setCurrentWidget(new_page)

        anim_group.finished.connect(slide_finished)
        anim_group.start()


class ScopeLineLabel(QtWidgets.QLineEdit):

    def __init__(self, placeholder="", *args, **kwargs):
        super(ScopeLineLabel, self).__init__(*args, **kwargs)
        self.setReadOnly(True)
        self.setPlaceholderText(placeholder)


class ClearCacheWidget(QtWidgets.QWidget):
    clear_clicked = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super(ClearCacheWidget, self).__init__(*args, **kwargs)
        clear_cache = QtWidgets.QPushButton()
        clear_cache.setObjectName("ClearCacheBtn")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(clear_cache)
        clear_cache.clicked.connect(self.clear_clicked)


class WorkspaceWidget(BusyWidget):
    tools_requested = QtCore.Signal(AbstractScope)
    workspace_changed = QtCore.Signal(AbstractScope)
    workspace_refreshed = QtCore.Signal(AbstractScope, bool)
    backend_changed = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super(WorkspaceWidget, self).__init__(*args, **kwargs)
        self.setObjectName("WorkspaceWidget")

        void_page = QtWidgets.QWidget()
        void_text = QtWidgets.QLabel("No Available Backend")
        entrances = QtWidgets.QStackedWidget()
        backend_sel = ComboBox()

        layout = QtWidgets.QVBoxLayout(void_page)
        layout.addWidget(void_text)

        entrances.addWidget(void_page)  # index 0

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 4)
        layout.addWidget(entrances)
        layout.addWidget(backend_sel)

        backend_sel.currentTextChanged.connect(self._on_backend_changed)

        self._stack = entrances
        self._combo = backend_sel

    def _on_backend_changed(self, name):
        # possibly need to do some cleanup before/after signal emitted ?
        self.backend_changed.emit(name)

    def on_workspace_entered(self, scope):
        backend_changed = False
        if scope.upstream is None:  # is entrance object, backend changed
            index = self._combo.findText(scope.name)
            if index < 0:
                log.critical(f"Unknown root level {scope.name}.")
            # + 1 because we have a void_page underneath
            index += 1
            if index != self._stack.currentIndex():
                self._stack.setCurrentIndex(index)
                backend_changed = True

        widget = self._stack.currentWidget()
        widget.enter_workspace(scope, backend_changed)

    def on_workspace_updated(self, scopes):
        widget = self._stack.currentWidget()
        widget.update_workspace(scopes)

    def on_cache_cleared(self):
        widget = self._stack.currentWidget()
        widget.on_cache_cleared()

    def register_backends(self, names: List[str]):
        if self._stack.count() > 1:
            return

        possible_backends = _load_backends()

        self.blockSignals(True)

        for name in names:
            widget_getter = possible_backends.get(name)
            if widget_getter is None:
                log.error(f"No widget for backend {name!r}.")
                continue

            try:
                widget_cls = widget_getter()
            except Exception as e:
                log.error(f"Failed to get widget for backend {name!r}: {str(e)}")
                continue

            w_icon = getattr(widget_cls, "icon_path", ":/icons/server.svg")
            widget = widget_cls()
            # these four signals and slots are the essentials
            widget.tools_requested.connect(self.tools_requested.emit)
            widget.workspace_changed.connect(self.workspace_changed.emit)
            widget.workspace_refreshed.connect(self.workspace_refreshed.emit)
            assert callable(widget.enter_workspace)
            assert callable(widget.update_workspace)
            assert callable(widget.on_cache_cleared)

            self._stack.addWidget(widget)
            self._combo.addItem(QtGui.QIcon(w_icon), name)

        self.blockSignals(False)

        if self._combo.count():
            self._on_backend_changed(self._combo.currentText())
        else:
            log.error("No valid backend registered.")


class WorkHistoryWidget(QtWidgets.QWidget):

    def __init__(self, *args, **kwargs):
        super(WorkHistoryWidget, self).__init__(*args, **kwargs)


class ToolsView(QtWidgets.QWidget):
    tool_cleared = QtCore.Signal()
    tool_selected = QtCore.Signal(SuiteTool)
    tool_launched = QtCore.Signal(SuiteTool)

    def __init__(self, *args, **kwargs):
        super(ToolsView, self).__init__(*args, **kwargs)
        self.setObjectName("ToolsView")

        model = ToolsModel()
        view = QtWidgets.QListView()
        view.setModel(model)
        selection = view.selectionModel()

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(view)

        selection.selectionChanged.connect(self._on_selection_changed)
        view.doubleClicked.connect(self._on_double_clicked)

        self._view = view
        self._model = model

    def _on_selection_changed(self, selected, _):
        indexes = selected.indexes()
        if indexes and indexes[0].isValid():
            index = indexes[0]  # SingleSelection view
            tool = index.data(self._model.ToolRole)
            self.tool_selected.emit(tool)
        else:
            self.tool_cleared.emit()

    def _on_double_clicked(self, index):
        if index.isValid():
            tool = index.data(self._model.ToolRole)
            self.tool_launched.emit(tool)

    def on_tools_updated(self, tools):
        self._model.update_tools(tools)

    def on_cache_cleared(self):
        self._view.clearSelection()


class WorkDirWidget(QtWidgets.QWidget):

    def __init__(self, *args, **kwargs):
        super(WorkDirWidget, self).__init__(*args, **kwargs)

        line = QtWidgets.QLineEdit()
        line.setObjectName("WorkDirLineRead")
        line.setReadOnly(True)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line)

        self._line = line

    def on_work_dir_obtained(self, path):
        self._line.setText(path)

    def on_work_dir_resetted(self):
        self._line.setText("")


class ToolContextWidget(QtWidgets.QWidget):

    def __init__(self, *args, **kwargs):
        super(ToolContextWidget, self).__init__(*args, **kwargs)

        launcher = ToolLaunchWidget()
        environ = ResolvedEnvironment()
        context = ResolvedContextView()

        tabs = QtWidgets.QTabBar()
        stack = QtWidgets.QStackedWidget()
        stack.setObjectName("TabStackWidget")
        tabs.setExpanding(True)
        tabs.setDocumentMode(True)
        # QTabWidget's frame (pane border) will not be rendered if documentMode
        # is enabled, so we make our own with bar + stack with border.
        tabs.addTab("Tool")
        stack.addWidget(launcher)
        tabs.addTab("Context")
        stack.addWidget(context)
        tabs.addTab("Environ")
        stack.addWidget(environ)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(0)
        layout.addWidget(tabs)
        layout.addWidget(stack)

        tabs.currentChanged.connect(stack.setCurrentIndex)
        # environ.hovered.connect(self.env_hovered.emit)  # todo: env hover

        self._launcher = launcher
        self._environ = environ
        self._context = context

    @QtCore.Slot(SuiteTool)  # noqa
    def on_tool_selected(self, suite_tool: SuiteTool, work_env: dict):
        context = suite_tool.context
        env = context.get_environ()
        env.update(work_env)
        self._context.load(context)
        self._environ.model().load(env)
        self._environ.model().note(lib.ContextEnvInspector.inspect(context))
        self._launcher.set_tool(suite_tool)

    def on_tool_cleared(self):
        self._context.reset()
        self._environ.model().clear()
        self._launcher.reset()


class TreeView(qoverview.VerticalExtendedTreeView):

    def __init__(self, *args, **kwargs):
        super(TreeView, self).__init__(*args, **kwargs)
        self.setAllColumnsShowFocus(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)


class JsonView(TreeView):

    def __init__(self, parent=None):
        super(JsonView, self).__init__(parent)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_right_click)

    def on_right_click(self, position):
        index = self.indexAt(position)

        if not index.isValid():
            # Clicked outside any item
            return

        model_ = index.model()
        menu = QtWidgets.QMenu(self)
        copy = QtWidgets.QAction("Copy JSON", menu)
        copy_full = QtWidgets.QAction("Copy full JSON", menu)

        menu.addAction(copy)
        menu.addAction(copy_full)
        menu.addSeparator()

        def on_copy():
            text = str(model_.data(index, JsonModel.JsonRole))
            app = QtWidgets.QApplication.instance()
            app.clipboard().setText(text)

        def on_copy_full():
            if isinstance(model_, QtCore.QSortFilterProxyModel):
                data = model_.sourceModel().json()
            else:
                data = model_.json()

            text = json.dumps(data,
                              indent=4,
                              sort_keys=True,
                              ensure_ascii=False)

            app = QtWidgets.QApplication.instance()
            app.clipboard().setText(text)

        copy.triggered.connect(on_copy)
        copy_full.triggered.connect(on_copy_full)

        menu.move(QtGui.QCursor.pos())
        menu.show()


class ToolLaunchWidget(QtWidgets.QWidget):
    tool_launched = QtCore.Signal(SuiteTool)
    shell_launched = QtCore.Signal(SuiteTool)

    def __init__(self, *args, **kwargs):
        super(ToolLaunchWidget, self).__init__(*args, **kwargs)

        head = QtWidgets.QWidget()
        icon = QtWidgets.QLabel()
        label = QtWidgets.QLineEdit()
        label.setObjectName("SuiteToolLabel")
        label.setReadOnly(True)
        label.setPlaceholderText("App name")

        body = QtWidgets.QWidget()

        ctx_name = QtWidgets.QLineEdit()
        ctx_name.setReadOnly(True)
        ctx_name.setPlaceholderText("Workspace setup name")
        tool_name = QtWidgets.QLineEdit()
        tool_name.setObjectName("SuiteToolName")
        tool_name.setReadOnly(True)
        tool_name.setPlaceholderText("App command")

        launch_bar = QtWidgets.QWidget()
        launch = QtWidgets.QPushButton("Launch App")
        launch.setObjectName("ToolLaunchBtn")
        shell = QtWidgets.QPushButton()
        shell.setObjectName("ShellLaunchBtn")

        layout = QtWidgets.QHBoxLayout(head)
        layout.addWidget(icon)
        layout.addWidget(label, alignment=QtCore.Qt.AlignBottom)

        layout = QtWidgets.QHBoxLayout(launch_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(launch)
        layout.addWidget(shell)

        layout = QtWidgets.QVBoxLayout(body)
        layout.addWidget(ctx_name)
        layout.addWidget(tool_name)
        layout.addStretch(True)
        layout.addWidget(launch_bar)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(head)
        layout.addWidget(body)

        launch.clicked.connect(self._on_launch_tool_clicked)
        shell.clicked.connect(self._on_launch_shell_clicked)

        self._label = label
        self._icon = icon
        self._ctx = ctx_name
        self._name = tool_name
        self._launch = launch
        self._shell = shell
        self._tool = None

        self.reset()

    def _on_launch_tool_clicked(self):
        self.tool_launched.emit(self._tool)

    def _on_launch_shell_clicked(self):
        self.shell_launched.emit(self._tool)

    def reset(self):
        icon = QtGui.QIcon(":/icons/joystick.svg")
        size = QtCore.QSize(res.px(64), res.px(64))

        self._ctx.setText("")
        self._name.setText("")
        self._label.setText("")
        self._icon.setPixmap(icon.pixmap(size))
        self._shell.setEnabled(False)
        self._launch.setEnabled(False)

    def set_tool(self, tool: SuiteTool):
        icon = parse_icon(tool.variant.root, tool.metadata.icon)
        size = QtCore.QSize(res.px(64), res.px(64))

        self._icon.setPixmap(icon.pixmap(size))
        self._label.setText(tool.metadata.label)
        self._ctx.setText(tool.ctx_name)
        self._name.setText(tool.name)
        self._tool = tool
        self._shell.setEnabled(True)
        self._launch.setEnabled(True)


class ResolvedEnvironment(QtWidgets.QWidget):
    hovered = QtCore.Signal(str, int)

    def __init__(self, *args, **kwargs):
        super(ResolvedEnvironment, self).__init__(*args, **kwargs)

        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Search environ var..")
        search.setClearButtonEnabled(True)
        switch = QtWidgets.QCheckBox()
        switch.setObjectName("EnvFilterSwitch")
        inverse = QtWidgets.QCheckBox("Inverse")

        model = ResolvedEnvironmentModel()
        proxy = ResolvedEnvironmentProxyModel()
        proxy.setSourceModel(model)
        view = JsonView()
        view.setModel(proxy)
        view.setTextElideMode(QtCore.Qt.ElideMiddle)
        header = view.header()
        header.setSectionResizeMode(0, header.ResizeToContents)
        header.setSectionResizeMode(1, header.Stretch)

        _layout = QtWidgets.QHBoxLayout()
        _layout.setContentsMargins(0, 0, 0, 0)
        _layout.addWidget(search, stretch=True)
        _layout.addWidget(switch)
        _layout.addWidget(inverse)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(_layout)
        layout.addWidget(view)

        view.setMouseTracking(True)
        view.entered.connect(self._on_entered)
        search.textChanged.connect(self._on_searched)
        switch.stateChanged.connect(self._on_switched)
        inverse.stateChanged.connect(self._on_inverse)

        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._deferred_search)

        self._view = view
        self._proxy = proxy
        self._model = model
        self._timer = timer
        self._search = search
        self._switch = switch

        switch.setCheckState(QtCore.Qt.Checked)

    def model(self):
        return self._model

    def leaveEvent(self, event: QtCore.QEvent):
        super(ResolvedEnvironment, self).leaveEvent(event)
        self.hovered.emit("", 0)  # clear

    def _on_entered(self, index):
        if not index.isValid():
            return
        index = self._proxy.mapToSource(index)
        column = index.column()

        if column == 0:
            self.hovered.emit("", 0)  # clear

        elif column > 0:
            parent = index.parent()
            if parent.isValid():
                key = self._model.index(parent.row(), 0).data()
            else:
                key = self._model.index(index.row(), 0).data()

            if column == 1:
                value = index.data()
                scope = self._model.index(index.row(), 2, parent).data()
            else:
                value = self._model.index(index.row(), 1, parent).data()
                scope = index.data()

            self.hovered.emit(f"{key} | {value} <- {scope}", 0)

    def _on_searched(self, _):
        self._timer.start(400)

    def _on_switched(self, state):
        if state == QtCore.Qt.Checked:
            self._switch.setText("On Key")
            self._proxy.filter_by_key()
        else:
            self._switch.setText("On Value")
            self._proxy.filter_by_value()

    def _on_inverse(self, state):
        self._proxy.inverse_filter(state)
        text = self._search.text()
        self._view.expandAll() if len(text) > 1 else self._view.collapseAll()
        self._view.reset_extension()

    def _deferred_search(self):
        # https://doc.qt.io/qt-5/qregexp.html#introduction
        text = self._search.text()
        self._proxy.setFilterRegExp(text)
        self._view.expandAll() if len(text) > 1 else self._view.collapseAll()
        self._view.reset_extension()


class ResolvedContextView(QtWidgets.QWidget):

    def __init__(self, *args, **kwargs):
        super(ResolvedContextView, self).__init__(*args, **kwargs)

        top_bar = QtWidgets.QWidget()
        top_bar.setObjectName("ButtonBelt")
        attr_toggle = QtWidgets.QPushButton("T")
        attr_toggle.setCheckable(True)
        attr_toggle.setChecked(True)

        model = ContextDataModel()
        view = TreeView()
        view.setObjectName("ResolvedContextTreeView")
        view.setModel(model)
        view.setTextElideMode(QtCore.Qt.ElideMiddle)
        view.setHeaderHidden(True)

        header = view.header()
        header.setSectionResizeMode(0, header.ResizeToContents)
        header.setSectionResizeMode(1, header.Stretch)

        layout = QtWidgets.QHBoxLayout(top_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(attr_toggle, alignment=QtCore.Qt.AlignLeft)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(top_bar)
        layout.addWidget(view)

        attr_toggle.toggled.connect(self._on_attr_toggled)

        self._view = view
        self._model = model

    def _on_attr_toggled(self, show_pretty):
        self._model.on_pretty_shown(show_pretty)
        self._view.update()

    def load(self, context):
        self._model.load(context)
        self._view.reset_extension()

    def reset(self):
        self._update_placeholder_color()  # set color for new model instance
        self._model.pending()
        self._view.reset_extension()

    def changeEvent(self, event):
        super(ResolvedContextView, self).changeEvent(event)
        if event.type() == QtCore.QEvent.StyleChange:
            # update color when theme changed
            self._update_placeholder_color()

    def _update_placeholder_color(self):
        color = self._view.palette().color(QtGui.QPalette.PlaceholderText)
        self._model.set_placeholder_color(color)
