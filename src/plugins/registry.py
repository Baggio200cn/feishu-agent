"""
Plugin registry — discovers, loads, reloads, and tracks plugins.

Plugin discovery
----------------
The registry scans a directory (default: ``plugins/`` at the project root) for
``*.py`` files.  Each file is imported as a module and inspected for a class
that subclasses ``BasePlugin``.  The *first* matching class found in a module
is registered; one plugin per file is the expected convention.

Usage
-----
    from src.plugins.registry import PluginRegistry

    registry = PluginRegistry()      # uses default plugins/ directory
    registry.load_all()              # discover & load every plugin

    registry.list_plugins()          # [{"name": ..., "description": ...}, ...]
    registry.reload_all()            # unload + reimport all plugins
    registry.reload("hello")         # reload a single plugin by name
"""
import importlib
import importlib.util
import inspect
import logging
import os
import sys
from typing import Dict, List, Optional

from src.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded."""


class PluginRegistry:
    """Manages the lifecycle of all plugins."""

    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = os.path.abspath(plugins_dir)
        # name -> (plugin_instance, module_name)
        self._plugins: Dict[str, tuple] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> List[str]:
        """Discover and load every plugin in *plugins_dir*.

        Returns a list of successfully loaded plugin names.
        """
        if not os.path.isdir(self.plugins_dir):
            logger.warning("插件目录不存在，跳过加载: %s", self.plugins_dir)
            return []

        loaded = []
        for filename in sorted(os.listdir(self.plugins_dir)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            try:
                name = self._load_file(os.path.join(self.plugins_dir, filename))
                if name:
                    loaded.append(name)
            except PluginLoadError as exc:
                logger.error("加载插件失败 [%s]: %s", filename, exc)
        return loaded

    def reload_all(self) -> Dict[str, str]:
        """Reload every currently known plugin *and* discover new ones.

        Returns a dict mapping plugin name -> "reloaded" | "loaded" | "error: <msg>".
        """
        results: Dict[str, str] = {}

        # Reload known plugins first (preserves order, cleans stale state).
        for name in list(self._plugins.keys()):
            results[name] = self._do_reload(name)

        # Discover any new files that haven't been loaded yet.
        if os.path.isdir(self.plugins_dir):
            known_files = {mod for _, (_, mod) in self._plugins.items()}
            for filename in sorted(os.listdir(self.plugins_dir)):
                if not filename.endswith(".py") or filename.startswith("_"):
                    continue
                mod_name = self._module_name(filename)
                if mod_name not in known_files:
                    try:
                        name = self._load_file(os.path.join(self.plugins_dir, filename))
                        if name:
                            results[name] = "loaded"
                    except PluginLoadError as exc:
                        results[filename] = f"error: {exc}"

        return results

    def reload(self, plugin_name: str) -> str:
        """Reload a single plugin by its registered name.

        Returns "reloaded", "loaded", or "error: <message>".
        """
        if plugin_name not in self._plugins:
            # Maybe it's a new file we haven't seen yet — try by filename.
            candidate = os.path.join(self.plugins_dir, f"{plugin_name}.py")
            if os.path.exists(candidate):
                try:
                    self._load_file(candidate)
                    return "loaded"
                except PluginLoadError as exc:
                    return f"error: {exc}"
            return f"error: 插件 '{plugin_name}' 未注册"
        return self._do_reload(plugin_name)

    def list_plugins(self) -> List[Dict[str, str]]:
        """Return metadata for all currently loaded plugins."""
        return [instance.get_info() for instance, _ in self._plugins.values()]

    def get(self, name: str) -> Optional[BasePlugin]:
        """Return the plugin instance for *name*, or None."""
        entry = self._plugins.get(name)
        return entry[0] if entry else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _module_name(self, filename: str) -> str:
        return f"_feishu_plugin_{os.path.splitext(filename)[0]}"

    def _load_file(self, filepath: str) -> Optional[str]:
        """Import *filepath*, find the BasePlugin subclass, register it.

        Returns the plugin's registered name, or None if no plugin class found.
        Raises PluginLoadError on import or instantiation failures.
        """
        filename = os.path.basename(filepath)
        mod_name = self._module_name(filename)

        try:
            spec = importlib.util.spec_from_file_location(mod_name, filepath)
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            raise PluginLoadError(f"无法导入 {filename}: {exc}") from exc

        plugin_class = self._find_plugin_class(module)
        if plugin_class is None:
            logger.debug("文件 %s 中未找到 BasePlugin 子类，跳过", filename)
            return None

        try:
            instance = plugin_class()
            instance.on_load()
        except Exception as exc:
            raise PluginLoadError(f"实例化 {plugin_class.__name__} 失败: {exc}") from exc

        reg_name = instance.name or plugin_class.__name__
        self._plugins[reg_name] = (instance, mod_name)
        logger.info("插件已加载: %s (%s)", reg_name, filename)
        return reg_name

    def _find_plugin_class(self, module):
        """Return the first BasePlugin subclass defined in *module*."""
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BasePlugin) and obj is not BasePlugin and obj.__module__ == module.__name__:
                return obj
        return None

    def _do_reload(self, name: str) -> str:
        """Unload and re-import a currently registered plugin."""
        instance, mod_name = self._plugins[name]
        try:
            instance.on_unload()
        except Exception as exc:
            logger.warning("插件 %s on_unload() 出错 (继续): %s", name, exc)

        # Remove stale module so exec_module gets a clean slate.
        sys.modules.pop(mod_name, None)
        del self._plugins[name]

        # Locate the source file again.
        filepath = self._find_filepath(mod_name)
        if filepath is None:
            logger.error("找不到插件源文件: %s", mod_name)
            return f"error: 找不到源文件 {mod_name}"

        try:
            self._load_file(filepath)
            return "reloaded"
        except PluginLoadError as exc:
            return f"error: {exc}"

    def _find_filepath(self, mod_name: str) -> Optional[str]:
        """Reverse-map a module name back to its .py file path."""
        # mod_name is _feishu_plugin_<stem>
        stem = mod_name.removeprefix("_feishu_plugin_")
        candidate = os.path.join(self.plugins_dir, f"{stem}.py")
        return candidate if os.path.exists(candidate) else None
