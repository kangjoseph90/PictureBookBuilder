"""
Recent Projects Manager - QSettings-based storage for recent project history
"""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSettings


class RecentProjectsManager:
    """Manage recent projects list using QSettings"""
    
    MAX_PROJECTS = 10
    SETTINGS_KEY = "recent_projects"
    
    def __init__(self):
        self.settings = QSettings("PictureBookBuilder", "PictureBookBuilder")
    
    def get_recent_projects(self) -> list[dict]:
        """
        Get list of recent projects.
        Returns list of dicts with keys: path, title, modified
        """
        projects = self.settings.value(self.SETTINGS_KEY, [])
        if not projects:
            return []
        
        # Filter out non-existent files
        valid_projects = []
        for p in projects:
            if isinstance(p, dict) and p.get("path") and Path(p["path"]).exists():
                valid_projects.append(p)
        
        # Update storage if we filtered any
        if len(valid_projects) != len(projects):
            self._save_projects(valid_projects)
        
        return valid_projects
    
    def add_project(self, path: str, title: Optional[str] = None):
        """Add or update a project in the recent list"""
        path = os.path.normpath(path)
        
        if title is None:
            title = Path(path).stem
        
        projects = self.get_recent_projects()
        
        # Remove existing entry if present
        projects = [p for p in projects if os.path.normpath(p.get("path", "")) != path]
        
        # Add new entry at the beginning
        projects.insert(0, {
            "path": path,
            "title": title,
            "modified": datetime.now().isoformat()
        })
        
        # Limit to max projects
        projects = projects[:self.MAX_PROJECTS]
        
        self._save_projects(projects)
    
    def remove_project(self, path: str):
        """Remove a project from the recent list"""
        path = os.path.normpath(path)
        projects = self.get_recent_projects()
        projects = [p for p in projects if os.path.normpath(p.get("path", "")) != path]
        self._save_projects(projects)
    
    def clear(self):
        """Clear all recent projects"""
        self._save_projects([])
    
    def _save_projects(self, projects: list[dict]):
        """Save projects list to settings"""
        self.settings.setValue(self.SETTINGS_KEY, projects)
        self.settings.sync()


# Global instance
_manager: Optional[RecentProjectsManager] = None


def get_recent_projects_manager() -> RecentProjectsManager:
    """Get the global RecentProjectsManager instance"""
    global _manager
    if _manager is None:
        _manager = RecentProjectsManager()
    return _manager
