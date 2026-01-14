"""
Start Screen - Application launch screen with recent projects
"""
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QWidget, QFileDialog, QFrame,
    QSizePolicy, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QCursor

from .recent_projects import get_recent_projects_manager


class ProjectListItem(QWidget):
    """Custom widget for project list item"""
    
    def __init__(self, title: str, path: str, modified: str, parent=None):
        super().__init__(parent)
        self.path = path
        
        # Make widget background transparent so list selection/hover shows through
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # Pass clicks to list item
        self.setStyleSheet("background: transparent;")
        
        # Main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(16)
        
        # Text container
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        
        # Title
        title_label = QLabel(title)
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #E0E0E0;")
        text_layout.addWidget(title_label)
        
        # Path (with ellipsis for long paths)
        path_label = QLabel(path)
        path_label.setStyleSheet("color: #858585; font-size: 11px;")
        path_label.setWordWrap(False)
        # Enable text elision when path is too long
        path_label.setMinimumWidth(100)
        path_label.setMaximumWidth(500)
        from PyQt6.QtCore import QTimer
        # Use a timer to elide after layout is set
        def elide_text():
            fm = path_label.fontMetrics()
            elided = fm.elidedText(path, Qt.TextElideMode.ElideMiddle, path_label.width())
            path_label.setText(elided)
        QTimer.singleShot(0, elide_text)
        text_layout.addWidget(path_label)
        layout.addLayout(text_layout)
        
        layout.addStretch()
        
        # Modified time
        time_text = self._format_time(modified)
        time_label = QLabel(time_text)
        time_label.setStyleSheet("color: #6A9955; font-size: 11px; font-weight: bold;")
        layout.addWidget(time_label)
    
    def _format_time(self, iso_time: str) -> str:
        """Format ISO time to human-readable relative time"""
        try:
            dt = datetime.fromisoformat(iso_time)
            now = datetime.now()
            diff = now - dt
            
            if diff.days == 0:
                hours = diff.seconds // 3600
                if hours == 0:
                    minutes = diff.seconds // 60
                    if minutes == 0:
                        return "방금 전"
                    return f"{minutes}분 전"
                return f"{hours}시간 전"
            elif diff.days == 1:
                return "어제"
            elif diff.days < 7:
                return f"{diff.days}일 전"
            elif diff.days < 30:
                weeks = diff.days // 7
                return f"{weeks}주 전"
            else:
                return dt.strftime("%Y-%m-%d")
        except:
            return ""


class StartScreen(QDialog):
    """Application start screen with recent projects"""
    
    project_selected = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_path = None
        self._create_ui()
    
    def _create_ui(self):
        """Create the UI"""
        self.setWindowTitle("PictureBookBuilder")
        self.resize(1200, 800) # Match MainWindow size
        
        # Use standard window flags to behave like a main window
        self.setWindowFlags(Qt.WindowType.Window)
        
        # Main Layout
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- Left Panel (Welcome & Actions) ---
        left_panel = QWidget()
        left_panel.setStyleSheet("background-color: #252526; border: none;")  # Remove border-right
        left_panel.setFixedWidth(400)
        
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(40, 60, 40, 40)
        left_layout.setSpacing(20)
        
        # Strings are vertically spaced
        
        # Logo/Title Area
        title_label = QLabel("PictureBookBuilder")
        # Use stylesheet for font to ensure application
        title_label.setStyleSheet("""
            color: #007ACC; 
            border: none;
            font-family: 'Segoe UI', sans-serif;
            font-size: 28px;
            font-weight: bold;
        """)
        left_layout.addWidget(title_label)
        
        left_layout.addSpacing(40)  # Increased spacing since subtitle is gone
        
        # "Start" Section
        start_label = QLabel("시작하기")
        start_label.setStyleSheet("color: #858585; font-size: 13px; font-weight: bold; margin-bottom: 8px;")
        left_layout.addWidget(start_label)
        
        # New Project Button
        new_btn = QPushButton("새 프로젝트 만들기")
        new_btn.setIcon(QIcon()) # Placeholder if needed
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedHeight(50)
        new_btn.setStyleSheet("""
            QPushButton {
                background-color: #007ACC;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                text-align: left;
                padding-left: 20px;
            }
            QPushButton:hover {
                background-color: #0098FF;
            }
            QPushButton:pressed {
                background-color: #005F9E;
            }
        """)
        new_btn.clicked.connect(self._on_new_project)
        left_layout.addWidget(new_btn)
        
        # Open Project Button
        open_btn = QPushButton("기존 프로젝트 열기")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setFixedHeight(50)
        open_btn.setStyleSheet("""
            QPushButton {
                background-color: #333333;
                color: #E0E0E0;
                border: 1px solid #3E3E42;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                text-align: left;
                padding-left: 20px;
            }
            QPushButton:hover {
                background-color: #3E3E42;
            }
            QPushButton:pressed {
                background-color: #2D2D2D;
            }
        """)
        open_btn.clicked.connect(self._on_open_project)
        left_layout.addWidget(open_btn)
        
        left_layout.addStretch()
        
        # Version/Footer
        version_label = QLabel("Version 1.1")
        version_label.setStyleSheet("color: #555555; font-size: 12px;")
        left_layout.addWidget(version_label)
        
        main_layout.addWidget(left_panel)
        
        # --- Right Panel (Recent Projects) ---
        right_panel = QWidget()
        right_panel.setStyleSheet("background-color: #1E1E1E;")
        
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(40, 60, 40, 40)
        right_layout.setSpacing(16)
        
        recent_header = QLabel("최근 사용한 프로젝트")
        recent_header.setStyleSheet("color: #E0E0E0; font-size: 16px; font-weight: bold;")
        right_layout.addWidget(recent_header)
        
        # List Container
        self.project_list = QListWidget()
        self.project_list.setFrameShape(QFrame.Shape.NoFrame)
        self.project_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                margin-bottom: 0px;
                border-bottom: 1px solid #2D2D2D;
            }
            QListWidget::item:hover {
                background-color: #2A2D2E;
            }
            QListWidget::item:selected {
                background-color: #37373D;
            }
        """)
        self.project_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.project_list.itemClicked.connect(self._on_project_clicked)  # Single click to open
        right_layout.addWidget(self.project_list)
        
        main_layout.addWidget(right_panel)
        
        # Load data
        self._load_recent_projects()

    def _load_recent_projects(self):
        """Load recent projects into the list"""
        self.project_list.clear()
        
        manager = get_recent_projects_manager()
        projects = manager.get_recent_projects()
        
        if not projects:
            # Empty State
            empty_widget = QWidget()
            empty_layout = QVBoxLayout(empty_widget)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            msg_label = QLabel("최근 사용한 프로젝트가 없습니다.")
            msg_label.setStyleSheet("color: #666666; font-size: 14px;")
            empty_layout.addWidget(msg_label)
            
            # Use as a placeholder in the layout instead of list item for better centering
            self.project_list.hide()
            self.layout().itemAt(1).widget().layout().addWidget(empty_widget)
            return

        for project in projects:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 80))
            item.setData(Qt.ItemDataRole.UserRole, project.get("path"))
            
            widget = ProjectListItem(
                title=project.get("title", "Untitled"),
                path=project.get("path", ""),
                modified=project.get("modified", "")
            )
            
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, widget)

    def _on_new_project(self):
        self.selected_path = None
        self.accept()

    def _on_open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "프로젝트 열기", "",
            "PictureBookBuilder Project (*.pbb);;All Files (*)"
        )
        if path:
            self.selected_path = path
            self.accept()

    def _on_project_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self.selected_path = path
            self.accept()
