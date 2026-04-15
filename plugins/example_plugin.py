"""
Example plugin — demonstrates the feishu-agent plugin interface.

Drop any *.py file (not starting with _) into this directory and run:
    python main.py reload-plugins

to load it at runtime without restarting the agent.
"""
from src.plugins.base import BasePlugin


class ExamplePlugin(BasePlugin):
    name = "example"
    description = "示例插件：展示插件系统的基本用法"

    def on_load(self) -> None:
        print(f"[{self.name}] 插件已加载")

    def on_unload(self) -> None:
        print(f"[{self.name}] 插件已卸载")

    def run(self, *args, **kwargs):
        print(f"[{self.name}] 插件运行中 — args={args}, kwargs={kwargs}")
        return {"status": "ok", "plugin": self.name}
