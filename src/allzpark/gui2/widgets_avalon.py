
import logging
from typing import List
from ._vendor.Qt5 import QtCore, QtGui, QtWidgets
from .common import SlidePageWidget, WorkspaceBase, BaseScopeModel
from ..backend_avalon import Entrance, Project, Asset, Task, MEMBER_ROLE
from ..util import singledispatchmethod, elide

log = logging.getLogger(__name__)


ASSET_MUST_BE_TASKED = True


class AvalonWidget(WorkspaceBase):
    icon_path = ":/icons/avalon.svg"

    def __init__(self, *args, **kwargs):
        super(AvalonWidget, self).__init__(*args, **kwargs)

        project_list = ProjectListWidget()
        asset_tree = AssetTreeWidget()

        asset_page = QtWidgets.QWidget()
        current_project = ScopeLineLabel("current project..")
        current_asset = ScopeLineLabel("current asset..")
        current_task = ScopeLineLabel("current task..")
        home = QtWidgets.QPushButton()
        tasks = QtWidgets.QComboBox()
        only_tasked = QtWidgets.QCheckBox("Show Tasked Only")

        layout = QtWidgets.QGridLayout(asset_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(home, 0, 0, 1, 1)
        layout.addWidget(current_project, 0, 1, 1, 6)
        layout.addWidget(only_tasked, 1, 0, 1, 3)
        layout.addWidget(tasks, 1, 3, 1, 4)
        layout.addWidget(asset_tree, 2, 0, 1, 7)
        layout.addWidget(current_asset, 3, 0, 1, 4)
        layout.addWidget(current_task, 3, 4, 1, 3)

        slider = SlidePageWidget()
        slider.addWidget(project_list)
        slider.addWidget(asset_page)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider)

        home.clicked.connect(self._on_home_clicked)
        project_list.scope_selected.connect(self.workspace_changed.emit)
        asset_tree.scope_selected.connect(self._on_asset_selected)
        tasks.currentTextChanged.connect(asset_tree.on_task_selected)
        only_tasked.stateChanged.connect(asset_tree.on_asset_filtered)

        self._entrance = None
        self._projects = project_list
        self._assets = asset_tree
        self._tasks = tasks
        self._slider = slider
        self._page = 0
        self._current_project = current_project
        self._current_asset = current_asset
        self._current_task = current_task
        self._current_scope = None

    def _on_home_clicked(self):
        assert self._entrance is not None
        self.workspace_changed.emit(self._entrance)

    def _on_asset_selected(self, asset: Asset):
        current = self._tasks.currentText()
        tasks = asset.iter_children()
        task = next((t for t in tasks if t.name == current), None)
        if task:
            self.workspace_changed.emit(task)
        else:
            log.warning(f"No matched task for {asset.name!r}.")

    def set_page(self, page):
        current = self._slider.currentIndex()
        if current == page and self._page == page:
            return

        direction = "right" if page > current else "left"
        self._page = page
        self._slider.slide_view(page, direction=direction)

    @singledispatchmethod
    def enter_workspace(self, scope):
        raise NotImplementedError(f"Unknown scope {elide(scope)!r}")

    @enter_workspace.register
    def _(self, scope: Entrance):
        self._entrance = scope
        self.set_page(0)

    @enter_workspace.register
    def _(self, scope: Project):
        _ = scope
        self.set_page(1)
        self._tasks.clear()
        self._tasks.addItems(scope.tasks)

    @enter_workspace.register
    def _(self, scope: Asset):
        pass

    @enter_workspace.register
    def _(self, scope: Task):
        pass

    @singledispatchmethod
    def update_workspace(self, scope, scopes):
        raise NotImplementedError(f"Unknown scope {elide(scope)!r}")

    @update_workspace.register
    def _(self, scope: Entrance, scopes: List[Project]):
        _ = scope
        return self._projects.model().refresh(scopes)

    @update_workspace.register
    def _(self, scope: Project, scopes: List[Asset]):
        _ = scope
        return self._assets.model().refresh(scopes)

    @update_workspace.register
    def _(self, scope: Asset, scopes: List[Task]):
        pass

    @update_workspace.register
    def _(self, scope: Task, scopes):
        pass


class ScopeLineLabel(QtWidgets.QLineEdit):

    def __init__(self, placeholder="", *args, **kwargs):
        super(ScopeLineLabel, self).__init__(*args, **kwargs)
        self.setReadOnly(True)
        self.setPlaceholderText(placeholder)


class ProjectListWidget(QtWidgets.QWidget):
    scope_selected = QtCore.Signal(object)

    def __init__(self, *args, **kwargs):
        super(ProjectListWidget, self).__init__(*args, **kwargs)

        search_bar = QtWidgets.QLineEdit()
        search_bar.setPlaceholderText("search projects..")

        model = ProjectListModel()
        view = QtWidgets.QListView()
        view.setModel(model)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(search_bar)
        layout.addWidget(view)

        view.clicked.connect(self._on_item_clicked)

        self._view = view
        self._model = model

    def model(self):
        return self._model

    def _on_item_clicked(self, index):
        scope = index.data(BaseScopeModel.ScopeRole)
        self.scope_selected.emit(scope)


class AssetTreeWidget(QtWidgets.QWidget):
    scope_selected = QtCore.Signal(object)

    def __init__(self, *args, **kwargs):
        super(AssetTreeWidget, self).__init__(*args, **kwargs)

        search_bar = QtWidgets.QLineEdit()
        search_bar.setPlaceholderText("search assets..")

        model = AssetTreeModel()
        proxy = AssetTreeProxyModel()
        proxy.setSourceModel(model)
        view = QtWidgets.QTreeView()
        view.setModel(proxy)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(search_bar)
        layout.addWidget(view)

        model.modelAboutToBeReset.connect(proxy.invalidate)
        view.clicked.connect(self._on_item_clicked)  # todo: should be selection change
        search_bar.textChanged.connect(self._on_asset_searched)

        self._view = view
        self._model = model
        self._proxy = proxy

    def model(self):
        return self._model

    def on_task_selected(self, task_name):
        self._model.set_task(task_name)
        self._proxy.invalidate()
        # todo: after asset filtered by the task, change current scope
        #   if asset selection changed.

    def on_asset_filtered(self, enabled):
        self._proxy.set_filter_by_task(bool(enabled))
        self._proxy.invalidate()

    def _on_asset_searched(self, text):
        self._proxy.setFilterRegExp(text)

    def _on_item_clicked(self, index):
        index = self._proxy.mapToSource(index)
        scope = index.data(BaseScopeModel.ScopeRole)
        self.scope_selected.emit(scope)


class ProjectListModel(BaseScopeModel):
    Headers = ["Name"]

    def refresh(self, scopes):
        self.reset()

        for project in scopes:
            # todo: this should be toggleable
            if project.is_active \
                    and (not project.roles or MEMBER_ROLE in project.roles):
                item = QtGui.QStandardItem()
                item.setText(project.name)
                item.setData(project, self.ScopeRole)

                self.appendRow(item)


class AssetTreeModel(BaseScopeModel):
    Headers = ["Name"]

    def __init__(self, *args, **kwargs):
        super(AssetTreeModel, self).__init__(*args, **kwargs)
        self._task = None

    def task(self):
        return self._task

    def set_task(self, name):
        self._task = name

    def refresh(self, scopes):
        self.reset()

        _asset_items = dict()
        for asset in scopes:
            if not asset.is_hidden:
                item = QtGui.QStandardItem()
                item.setText(asset.name)
                item.setData(asset, self.ScopeRole)

                _asset_items[asset.name] = item

                if asset.parent is None:
                    self.appendRow(item)

                else:
                    parent = _asset_items[asset.parent.name]
                    parent.appendRow(item)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        """
        :param QtCore.QModelIndex index:
        :param int role:
        :return:
        """
        if not index.isValid():
            return

        if role == QtCore.Qt.FontRole:
            scope = index.data(self.ScopeRole)  # type: Asset
            if not is_asset_tasked(scope, self._task):
                font = QtGui.QFont()
                font.setItalic(True)
                return font

        return super(AssetTreeModel, self).data(index, role)


class AssetTreeProxyModel(QtCore.QSortFilterProxyModel):

    def __init__(self, *args, **kwargs):
        super(AssetTreeProxyModel, self).__init__(*args, **kwargs)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._filter_by_task = False

    def set_filter_by_task(self, enabled: bool):
        self._filter_by_task = enabled

    def filterAcceptsRow(self, source_row, source_parent):
        """
        :param int source_row:
        :param QtCore.QModelIndex source_parent:
        :rtype: bool
        """
        accepted = super(AssetTreeProxyModel, self).filterAcceptsRow(
            source_row, source_parent
        )
        if accepted and self._filter_by_task:
            model = self.sourceModel()  # type: AssetTreeModel
            index = model.index(source_row, 0, source_parent)
            scope = index.data(AssetTreeModel.ScopeRole)
            return \
                not scope.is_silo \
                and is_asset_tasked(scope, model.task())

        return accepted


def is_asset_tasked(scope: Asset, task_name: str) -> bool:
    if ASSET_MUST_BE_TASKED:
        return task_name in scope.tasks
    return not scope.tasks or task_name in scope.tasks
