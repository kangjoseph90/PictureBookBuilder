import copy
from typing import List, Optional, Any, Tuple
from dataclasses import dataclass, field

from .clip import TimelineClip

class Command:
    def undo(self):
        raise NotImplementedError

    def redo(self):
        raise NotImplementedError

    def text(self) -> str:
        return ""

class UndoStack:
    def __init__(self, parent=None):
        self.undo_stack: List[Command] = []
        self.redo_stack: List[Command] = []
        self.max_stack_size = 100
        self.clean_command: Optional[Command] = None

    def push(self, cmd: Command):
        self.undo_stack.append(cmd)
        if len(self.undo_stack) > self.max_stack_size:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> Optional[str]:
        if not self.undo_stack:
            return None
        cmd = self.undo_stack.pop()
        cmd.undo()
        self.redo_stack.append(cmd)
        return cmd.text()

    def redo(self) -> Optional[str]:
        if not self.redo_stack:
            return None
        cmd = self.redo_stack.pop()
        cmd.redo()
        self.undo_stack.append(cmd)
        return cmd.text()

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def set_clean(self):
        """Mark the current state as clean"""
        self.clean_command = self.undo_stack[-1] if self.undo_stack else None

    def is_clean(self) -> bool:
        """Check if the current state is clean"""
        current_command = self.undo_stack[-1] if self.undo_stack else None
        return current_command is self.clean_command

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.clean_command = None

class MacroCommand(Command):
    """Command that executes multiple commands together"""
    def __init__(self, commands: List[Command], description="Macro command"):
        self.commands = commands
        self.description = description

    def undo(self):
        # Undo in reverse order
        for cmd in reversed(self.commands):
            cmd.undo()

    def redo(self):
        for cmd in self.commands:
            cmd.redo()

    def text(self) -> str:
        return self.description

class ModifyClipsCommand(Command):
    """Command to modify specific clips (update attributes)"""
    def __init__(self, canvas: Any, modifications: List[Tuple[str, TimelineClip, TimelineClip]], description="Modify clips", callback=None):
        """
        Args:
            canvas: The TimelineCanvas instance
            modifications: List of tuples (clip_id, old_clip_state, new_clip_state)
            description: Description of the command
            callback: Optional callback to run after undo/redo (e.g. for update())
        """
        self.canvas = canvas
        self.modifications = modifications
        self.description = description
        self.callback = callback

    def _apply_state(self, state_map: dict):
        # Create a map of ID -> clip object in the current canvas list
        current_clip_map = {c.id: c for c in self.canvas.clips}

        for clip_id, state in state_map.items():
            if clip_id in current_clip_map:
                clip = current_clip_map[clip_id]
                # Update all fields from state (which is a TimelineClip copy)
                # We iterate over dataclass fields to copy
                for k, v in state.__dict__.items():
                    setattr(clip, k, copy.deepcopy(v))

        if self.callback:
            self.callback()

    def undo(self):
        state_map = {cid: old_s for cid, old_s, new_s in self.modifications}
        self._apply_state(state_map)

    def redo(self):
        state_map = {cid: new_s for cid, old_s, new_s in self.modifications}
        self._apply_state(state_map)

    def text(self) -> str:
        return self.description

class AddRemoveClipsCommand(Command):
    """Command to add or remove clips"""
    def __init__(self, canvas: Any, added: List[TimelineClip], removed: List[TimelineClip], description="Add/Remove clips", callback=None):
        self.canvas = canvas
        self.added = added
        self.removed = removed # These are the clips that were removed, we need to save them to restore
        self.description = description
        self.callback = callback

    def undo(self):
        # Inverse operation: remove added, add back removed
        ids_to_remove = {c.id for c in self.added}

        # Remove added clips
        to_remove = [c for c in self.canvas.clips if c.id in ids_to_remove]
        for c in to_remove:
            self.canvas.clips.remove(c)

        # Add back removed clips
        for c in self.removed:
            self.canvas.clips.append(c)

        if self.callback:
            self.callback()

    def redo(self):
        # Remove clips marked for removal
        ids_to_remove = {c.id for c in self.removed}
        to_remove = [c for c in self.canvas.clips if c.id in ids_to_remove]
        for c in to_remove:
            self.canvas.clips.remove(c)

        # Add clips
        for c in self.added:
            self.canvas.clips.append(c)

        if self.callback:
            self.callback()

    def text(self) -> str:
        return self.description

class ReplaceAllClipsCommand(Command):
    """Command to replace the entire list of clips"""
    def __init__(self, canvas: Any, old_clips: List[TimelineClip], new_clips: List[TimelineClip], description="Replace all clips", callback=None):
        self.canvas = canvas
        self.old_clips = old_clips
        self.new_clips = new_clips
        self.description = description
        self.callback = callback

    def undo(self):
        self.canvas.clips = [copy.deepcopy(c) for c in self.old_clips]
        if self.callback:
            self.callback()

    def redo(self):
        self.canvas.clips = [copy.deepcopy(c) for c in self.new_clips]
        if self.callback:
            self.callback()

    def text(self) -> str:
        return self.description
