from __future__ import annotations
import typing
import sys
import types


def make_config(**kwargs) -> types.ModuleType:
    assert "config" not in sys.modules, "config module already exists"
    
    module = types.ModuleType("config")
    
    for key, value in kwargs.items():
        setattr(module, key, value)
    
    sys.modules["config"] = module
    
    return module


def drop_config() -> None:
    del sys.modules["config"]


__all__ = [
    "make_config",
    "drop_config",
]
