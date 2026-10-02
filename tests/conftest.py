from __future__ import annotations

import logging
import sys
import tempfile
import types
from enum import Enum
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


if "astrbot" not in sys.modules:
    astrbot = _module("astrbot")
    api = _module("astrbot.api")
    api.logger = logging.getLogger("astrbot-test")
    api.AstrBotConfig = dict
    astrbot.api = api

    components = _module("astrbot.api.message_components")

    class _Component:
        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Plain(_Component):
        def __init__(self, text: str = "") -> None:
            super().__init__(text=text)

    class At(_Component):
        pass

    class Image(_Component):
        @classmethod
        def fromBytes(cls, payload: bytes) -> "Image":
            return cls(payload=payload)

        @classmethod
        def fromFileSystem(cls, path: str) -> "Image":
            return cls(path=path)

    class Node(_Component):
        pass

    class Nodes(_Component):
        pass

    components.At = At
    components.Image = Image
    components.Node = Node
    components.Nodes = Nodes
    components.Plain = Plain
    api.message_components = components

    class _EventType(Enum):
        ALL = "all"

    class _Filter:
        EventMessageType = _EventType

        @staticmethod
        def command(_name: str):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def event_message_type(_event_type: _EventType):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def llm_tool(name: str | None = None, **_kwargs: object):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def on_llm_request():
            def decorator(func):
                return func

            return decorator

    class MessageChain:
        def __init__(self, chain: list[Any] | None = None) -> None:
            self.chain = chain or []

        def message(self, text: str) -> "MessageChain":
            self.chain.append(Plain(text))
            return self

    event = _module("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = MessageChain
    event.filter = _Filter
    api.event = event

    class Context:
        def __init__(self, config: dict[str, Any] | None = None) -> None:
            self._config = config or {}
            self.platform_manager = types.SimpleNamespace(platform_insts=[])

        def get_config(self, _umo: str | None = None) -> dict[str, Any]:
            return self._config

    class Star:
        def __init__(self, context: Context, config: dict[str, Any] | None = None) -> None:
            self.context = context
            self.config = config or {}

    star = _module("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    api.star = star

    core = _module("astrbot.core")
    core.message = _module("astrbot.core.message")
    result = _module("astrbot.core.message.message_event_result")
    result.MessageChain = MessageChain
    core.message.message_event_result = result
    core.platform = _module("astrbot.core.platform")
    core.platform.astr_message_event = _module(
        "astrbot.core.platform.astr_message_event"
    )
    core.utils = _module("astrbot.core.utils")
    paths = _module("astrbot.core.utils.astrbot_path")

    test_data_path = Path(tempfile.mkdtemp(prefix="astrbot-cs2-tests-"))

    def get_astrbot_data_path() -> str:
        return str(test_data_path)

    paths.get_astrbot_data_path = get_astrbot_data_path
    core.utils.astrbot_path = paths
    astrbot.core = core
