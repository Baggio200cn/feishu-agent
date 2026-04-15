"""
Plugin system for feishu-agent.

Quick start
-----------
1. Create a file in the project-level ``plugins/`` directory, e.g. ``plugins/my_plugin.py``.
2. Define a class that inherits from ``BasePlugin`` and implements ``run()``.
3. Run ``python main.py reload-plugins`` to load it without restarting.

See ``src/plugins/base.py`` for the full API and ``plugins/example_plugin.py``
for a working example.
"""
from src.plugins.base import BasePlugin
from src.plugins.registry import PluginRegistry, PluginLoadError

__all__ = ["BasePlugin", "PluginRegistry", "PluginLoadError"]
