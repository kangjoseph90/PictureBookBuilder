"""
Modern Dark Theme for PictureBookBuilder
"""
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QPalette

class ModernDarkTheme:
    """Modern Dark Theme with Flat Design"""
    
    # Color Palette
    BG_DARK = "#1E1E1E"        # Main Background
    BG_LIGHT = "#252526"       # Secondary Background (Panels)
    BG_LIGHTER = "#333333"     # Borders / Hover
    
    ACCENT = "#007ACC"         # VS Code Blue
    ACCENT_HOVER = "#0098FF"
    
    TEXT_MAIN = "#D4D4D4"
    TEXT_DIM = "#858585"
    
    # Stylesheet
    STYLESHEET = """
        QMainWindow, QDialog {
            background-color: #1E1E1E;
        }
        QWidget {
            background-color: #1E1E1E;
            color: #D4D4D4;
            font-family: "Segoe UI", "Malgun Gothic", sans-serif;
            font-size: 14px;
        }
        
        /* Interactive Elements */
        QPushButton {
            background-color: #333333;
            border: 1px solid #333333;
            border-radius: 4px;
            padding: 6px 12px;
            color: #D4D4D4;
            min-height: 24px;
        }
        QPushButton:hover {
            background-color: #444444;
            border: 1px solid #444444;
        }
        QPushButton:pressed {
            background-color: #2D2D2D;
        }
        QPushButton:disabled {
            background-color: #252526;
            color: #555555;
            border: 1px solid #252526;
        }
        
        /* Primary/Action Buttons (Custom property can be used) */
        QPushButton[class="primary"] {
            background-color: #007ACC;
            border: 1px solid #007ACC;
            color: white;
        }
        QPushButton[class="primary"]:hover {
            background-color: #0098FF;
            border: 1px solid #0098FF;
        }
        
        /* Input Fields */
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
            background-color: #252526;
            border: 1px solid #333333;
            border-radius: 2px;
            color: #F0F0F0;
            padding: 4px;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #007ACC;
        }
        
        /* SpinBox Buttons */
        QSpinBox::up-button, QDoubleSpinBox::up-button {
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 18px;
            background-color: #333333;
            border-left: 1px solid #1E1E1E;
            border-bottom: 1px solid #1E1E1E;
        }
        QSpinBox::down-button, QDoubleSpinBox::down-button {
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 18px;
            background-color: #333333;
            border-left: 1px solid #1E1E1E;
        }
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
        QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
            background-color: #444444;
        }
        
        /* Combo Box */
        QComboBox {
            background-color: #252526;
            border: 1px solid #333333;
            border-radius: 2px;
            padding: 4px 8px;
            color: #F0F0F0;
        }
        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 20px;
            border-left: 1px solid #333333;
        }
        QComboBox:on {
            border: 1px solid #007ACC;
        }
        QComboBox QAbstractItemView {
            background-color: #1E1E1E;
            border: 1px solid #333333;
            selection-background-color: #094771;
        }
        
        QGroupBox {
            border: 1px solid #333333;
            border-radius: 4px;
            margin-top: 10px;
            padding-top: 5px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 5px;
            color: #858585;
            background-color: #1E1E1E;
        }
        
        /* Scrollbars */
        QScrollBar:vertical {
            background: #1E1E1E;
            width: 14px;
            margin: 0px 0 0px 0;
        }
        QScrollBar::handle:vertical {
            background: #424242;
            min-height: 20px;
            border-radius: 7px;
            margin: 2px;
        }
        QScrollBar::handle:vertical:hover {
            background: #4F4F4F;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar:horizontal {
            background: #1E1E1E;
            height: 14px;
            margin: 0px 0 0px 0;
        }
        QScrollBar::handle:horizontal {
            background: #424242;
            min-width: 20px;
            border-radius: 7px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #4F4F4F;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        
        /* Lists and Tables */
        QListWidget, QTableWidget {
            background-color: #252526;
            border: 1px solid #333333;
            gridline-color: #444444;
            outline: none;
        }
        QListWidget::item, QTableWidget::item {
            padding: 6px;
            border-bottom: 1px solid #2D2D2D;
        }
        QListWidget::item:selected, QTableWidget::item:selected {
            background-color: #37373D;
            color: white;
            border-radius: 2px;
        }
        QListWidget::item:hover, QTableWidget::item:hover {
            background-color: #2A2D2E;
        }
        
        QHeaderView::section {
            background-color: #2D2D2D;
            color: #CCCCCC;
            padding: 5px;
            border: none;
            border-bottom: 2px solid #3E3E42;
            border-right: 1px solid #3E3E42;
            font-weight: bold;
        }
        
        QTableCornerButton::section {
            background-color: #2D2D2D;
            border: none;
            border-bottom: 2px solid #3E3E42;
            border-right: 1px solid #3E3E42;
        }
        
        /* Menus */
        QMenuBar {
            background-color: #1E1E1E;
            color: #CCCCCC;
            border-bottom: 1px solid #333333;
        }
        QMenuBar::item {
            background-color: transparent;
            padding: 8px 12px;
        }
        QMenuBar::item:selected {
            background-color: #333333;
        }
        
        QMenu {
            background-color: #252526;
            border: 1px solid #333333;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 6px 24px 6px 12px; /* right padding for shortcut */
            border-radius: 2px;
        }
        QMenu::item:selected {
            background-color: #094771;
            color: white;
        }
        QMenu::separator {
            height: 1px;
            background: #333333;
            margin: 4px 0;
        }
        
        /* ToolBar */
        QToolBar {
            background-color: #1E1E1E;
            border-bottom: 1px solid #333333;
            spacing: 8px;
            padding: 4px;
        }
        QToolBar::separator {
            width: 1px;
            background: #333333;
            margin: 4px;
        }
        
        /* Tab Widget */
        QTabWidget::pane {
            border: 1px solid #333333;
            background-color: #1E1E1E;
        }
        QTabBar::tab {
            background-color: #2D2D2D;
            color: #858585;
            padding: 8px 16px;
            border: none;
            border-right: 1px solid #333333;
        }
        QTabBar::tab:selected {
            background-color: #1E1E1E;
            color: #D4D4D4;
            border-top: 2px solid #007ACC;
        }
        QTabBar::tab:hover {
            background-color: #333333;
        }
        
        /* Splitter */
        QSplitter::handle {
            background-color: #1E1E1E;
        }
        QSplitter::handle:hover {
            background-color: #007ACC;
        }
    """

    @staticmethod
    def apply(app: QApplication):
        """Apply theme to application"""
        app.setStyle("Fusion")
        
        # Set Palette for consistent Fusion style behavior
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#1E1E1E"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#252526"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1E1E1E"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#252526"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#333333"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#D4D4D4"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#007ACC"))
        
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#007ACC"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        
        app.setPalette(palette)
        
        # Apply Stylesheet
        app.setStyleSheet(ModernDarkTheme.STYLESHEET)
