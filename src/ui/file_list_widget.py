from PyQt6.QtWidgets import (
    QApplication, QListWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect, QMimeData, QUrl
from PyQt6.QtGui import QPalette, QFontMetrics

class ImageGridDelegate(QStyledItemDelegate):
    """Custom delegate to render icons with text below in ListMode"""
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        
        # Draw standard background (selection/hover)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, option.widget)
        
        # Layout metrics
        rect = option.rect
        # Dynamic icon size from option (set by view)
        icon_size = option.decorationSize.width() if not option.decorationSize.isEmpty() else 64
        spacing_text = 2 # Closer text
        
        # Draw Icon (Centered horizontally, Top aligned)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        actual_icon_h = 0
        if icon:
            pixmap = icon.pixmap(icon_size, icon_size)
            if not pixmap.isNull():
                # Center pixmap horizontally in the cell
                x = rect.x() + (rect.width() - pixmap.width()) // 2
                y = rect.y() + 5 # Small top padding
                painter.drawPixmap(x, y, pixmap)
                actual_icon_h = pixmap.height()
            
        # Draw Text (Centered horizontally, Below icon)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            # Text area below icon - dynamic Y based on actual icon height
            y_offset = (actual_icon_h if actual_icon_h > 0 else icon_size) + 5 + spacing_text
            text_rect = QRect(rect.x(), rect.y() + int(y_offset), rect.width(), 20)
            
            # Elide text if needed
            fm = QFontMetrics(option.font)
            elided_text = fm.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width() - 4)
            
            # Text color
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
            if option.state & QStyle.StateFlag.State_Selected:
                 painter.setPen(option.palette.color(QPalette.ColorRole.HighlightedText))
                 
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, elided_text)
            
    def sizeHint(self, option, index):
        # Dynamic size based on icon size
        icon_size = option.decorationSize.width() if not option.decorationSize.isEmpty() else 64
        # Add padding for text (approx 36px: 5 top + icon + 5 spacing + 20 text + bottom)
        total_size = icon_size + 36
        return QSize(total_size, total_size)


class DraggableImageListWidget(QListWidget):
    """Custom QListWidget that provides file URLs when dragging for external drop targets"""
    
    zoom_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_zoom = 32
        self.max_zoom = 256
        self.zoom_step = 16

    def wheelEvent(self, event):
        """Handle zoom with Ctrl+Wheel"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_in(self):
        self._adjust_zoom(1)

    def zoom_out(self):
        self._adjust_zoom(-1)

    def _adjust_zoom(self, direction):
        current_size = self.iconSize().width()
        new_size = current_size + (self.zoom_step * direction)
        new_size = max(self.min_zoom, min(new_size, self.max_zoom))

        if new_size != current_size:
            self.setIconSize(QSize(new_size, new_size))
            # Adjust grid size to accommodate icon + text padding
            # Matches calculation in Delegate sizeHint
            padding = 36
            self.setGridSize(QSize(new_size + padding, new_size + padding))
            self.zoom_changed.emit(new_size)

    def mimeData(self, items):
        """Override to include file URLs in the mime data for drag operations"""
        mime = super().mimeData(items)
        
        # Add file URLs for the dragged items
        urls = []
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                urls.append(QUrl.fromLocalFile(path))
        
        if urls:
            mime.setUrls(urls)
        
        return mime
    
    def supportedDropActions(self):
        """Support copy action for external drops while maintaining internal move"""
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
