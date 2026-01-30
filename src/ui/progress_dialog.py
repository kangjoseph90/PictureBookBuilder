from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal

class ProgressDialog(QDialog):
    """Processing progress dialog with refined UI matching settings style"""
    
    cancelled = pyqtSignal()
    
    def __init__(self, parent=None, title="오디오 처리"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(400)
        self.setWindowFlags(
            Qt.WindowType.Dialog | 
            Qt.WindowType.CustomizeWindowHint | 
            Qt.WindowType.WindowTitleHint
        )
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 20)
        layout.setSpacing(15)
        
        # Status label with slightly better font/color
        self.status_label = QLabel("준비 중...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-weight: bold; color: #CCCCCC;")
        layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        layout.addWidget(self.progress_bar)
        
        # Spacer
        layout.addSpacing(5)
        
        # Cancel button centered
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setFixedWidth(100)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        self._is_cancelled = False
        
        # Auto-adjust height
        self.adjustSize()
        self.setFixedSize(self.width(), self.sizeHint().height())
    
    def update_progress(self, percent: int, message: str):
        """Update progress bar and status message"""
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)
    
    def _on_cancel(self):
        """Handle cancel button click"""
        self._is_cancelled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("취소 중...")
        self.status_label.setText("취소 중... 잠시 기다려주세요")
        self.cancelled.emit()
    
    def closeEvent(self, event):
        """Prevent closing dialog by X button during processing"""
        if not self._is_cancelled:
            event.ignore()
        else:
            event.accept()
