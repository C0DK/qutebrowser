# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""Completion view for statusbar command section.

Defines a CompletionView which uses CompletionFiterModel and CompletionModel
subclasses to provide completions.
"""

from PyQt5.QtWidgets import QStyle, QTreeView, QSizePolicy
from PyQt5.QtCore import pyqtSlot, pyqtSignal, Qt, QItemSelectionModel

from qutebrowser.commands import cmdutils
from qutebrowser.config import config, style
from qutebrowser.widgets import completiondelegate
from qutebrowser.utils import completer, usertypes, qtutils


class CompletionView(QTreeView):

    """The view showing available completions.

    Based on QTreeView but heavily customized so root elements show as category
    headers, and children show as flat list.

    Highlights completions based on marks in the Role.marks data.

    Class attributes:
        COLUMN_WIDTHS: A list of column widths, in percent.

    Attributes:
        completer: The Completer instance to use.
        enabled: Whether showing the CompletionView is enabled.
        _height: The height to use for the CompletionView.
        _height_perc: Either None or a percentage if height should be relative.
        _delegate: The item delegate used.

    Signals:
        resize_completion: Emitted when the completion should be resized.
    """

    # Drawing the item foreground will be done by CompletionItemDelegate, so we
    # don't define that in this stylesheet.
    STYLESHEET = """
        QTreeView {
            {{ font['completion'] }}
            {{ color['completion.bg'] }}
            outline: 0;
        }

        QTreeView::item:disabled {
            {{ color['completion.category.bg'] }}
            border-top: 1px solid
                {{ color['completion.category.border.top'] }};
            border-bottom: 1px solid
                {{ color['completion.category.border.bottom'] }};
        }

        QTreeView::item:selected, QTreeView::item:selected:hover {
            border-top: 1px solid
                {{ color['completion.item.selected.border.top'] }};
            border-bottom: 1px solid
                {{ color['completion.item.selected.border.bottom'] }};
            {{ color['completion.item.selected.bg'] }}
        }

        QTreeView:item::hover {
            border: 0px;
        }
    """
    COLUMN_WIDTHS = (20, 70, 10)

    # FIXME style scrollbar

    resize_completion = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.completer = completer.Completer(self)
        self.enabled = config.get('completion', 'show')

        self._delegate = completiondelegate.CompletionItemDelegate(self)
        self.setItemDelegate(self._delegate)
        style.set_register_stylesheet(self)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.setHeaderHidden(True)
        self.setIndentation(0)
        self.setItemsExpandable(False)
        self.setExpandsOnDoubleClick(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # WORKAROUND
        # This is a workaround for weird race conditions with invalid
        # item indexes leading to segfaults in Qt.
        #
        # Some background: http://bugs.quassel-irc.org/issues/663
        # The proposed fix there was later reverted because it didn't help.
        self.setUniformRowHeights(True)
        self.hide()
        # FIXME set elidemode

    def __repr__(self):
        return '<{}>'.format(self.__class__.__name__)

    def _resize_columns(self):
        """Resize the completion columns based on COLUMN_WIDTHS."""
        width = self.size().width()
        pixel_widths = [(width * perc // 100) for perc in self.COLUMN_WIDTHS]
        if self.verticalScrollBar().isVisible():
            pixel_widths[-1] -= self.style().pixelMetric(
                QStyle.PM_ScrollBarExtent) + 5
        for i, w in enumerate(pixel_widths):
            self.setColumnWidth(i, w)

    def _next_idx(self, upwards):
        """Get the previous/next QModelIndex displayed in the view.

        Used by tab_handler.

        Args:
            upwards: Get previous item, not next.

        Return:
            A QModelIndex.
        """
        idx = self.selectionModel().currentIndex()
        if not idx.isValid():
            # No item selected yet
            return self.model().first_item()
        while True:
            idx = self.indexAbove(idx) if upwards else self.indexBelow(idx)
            # wrap around if we arrived at beginning/end
            if not idx.isValid() and upwards:
                return self.model().last_item()
            elif not idx.isValid() and not upwards:
                idx = self.model().first_item()
                self.scrollTo(idx.parent())
                return idx
            elif idx.parent().isValid():
                # Item is a real item, not a category header -> success
                return idx

    def _next_prev_item(self, prev):
        """Handle a tab press for the CompletionView.

        Select the previous/next item and write the new text to the
        statusbar.

        Args:
            prev: True for prev item, False for next one.
        """
        if not self.isVisible():
            # No completion running at the moment, ignore keypress
            return
        idx = self._next_idx(prev)
        qtutils.ensure_valid(idx)
        self.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.ClearAndSelect |
            QItemSelectionModel.Rows)

    def set_model(self, model):
        """Switch completion to a new model.

        Called from on_update_completion().

        Args:
            model: The model to use.
        """
        self.setModel(model)
        self.expandAll()
        self._resize_columns()
        model.rowsRemoved.connect(self.maybe_resize_completion)
        model.rowsInserted.connect(self.maybe_resize_completion)
        self.maybe_resize_completion()

    @pyqtSlot()
    def maybe_resize_completion(self):
        """Emit the resize_completion signal if the config says so."""
        if config.get('completion', 'shrink'):
            self.resize_completion.emit()

    @pyqtSlot(str, str)
    def on_config_changed(self, section, option):
        """Update self.enabled when the config changed."""
        if section == 'completion' and option == 'show':
            self.enabled = config.get('completion', 'show')
        elif section == 'aliases':
            self._init_command_completion()

    @pyqtSlot()
    def on_clear_completion_selection(self):
        """Clear the selection model when an item is activated."""
        selmod = self.selectionModel()
        if selmod is not None:
            selmod.clearSelection()
            selmod.clearCurrentIndex()

    @cmdutils.register(instance='mainwindow.completion', hide=True,
                       modes=[usertypes.KeyMode.command])
    def completion_item_prev(self):
        """Select the previous completion item."""
        self._next_prev_item(prev=True)

    @cmdutils.register(instance='mainwindow.completion', hide=True,
                       modes=[usertypes.KeyMode.command])
    def completion_item_next(self):
        """Select the next completion item."""
        self._next_prev_item(prev=False)

    def selectionChanged(self, selected, deselected):
        """Extend selectionChanged to call completers selection_changed."""
        super().selectionChanged(selected, deselected)
        self.completer.selection_changed(selected, deselected)

    def resizeEvent(self, e):
        """Extend resizeEvent to adjust column size."""
        super().resizeEvent(e)
        self._resize_columns()
