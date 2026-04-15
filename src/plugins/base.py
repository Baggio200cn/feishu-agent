"""
Plugin base class for feishu-agent.

A plugin is a Python file placed in the project-level `plugins/` directory.
It must contain exactly one class that subclasses BasePlugin.

Minimal example
---------------
from src.plugins.base import BasePlugin

class HelloPlugin(BasePlugin):
    name = "hello"
    description = "Prints a greeting"

    def run(self, *args, **kwargs):
        print("Hello from HelloPlugin!")
"""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BasePlugin(ABC):
    """Abstract base class every plugin must inherit from."""

    # Subclasses should set these class-level attributes.
    name: str = ""
    description: str = ""

    def on_load(self) -> None:
        """Called once after the plugin is first loaded. Override as needed."""

    def on_unload(self) -> None:
        """Called just before the plugin is unloaded/reloaded. Override as needed."""

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Entry point invoked when the plugin's command is executed."""

    def get_info(self) -> Dict[str, str]:
        """Return human-readable plugin metadata."""
        return {
            "name": self.name or self.__class__.__name__,
            "description": self.description,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
