from __future__ import annotations
import typing


T = typing.TypeVar("T", bound="Singleton")


class _SingletonMeta(type):
    _instance_: T | None
    
    def __init__(cls: typing.Type[T], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        
        if cls is not Singleton:
            cls._instance_ = None
    
    # Note: no *args nor **kwargs, because singletons aren't
    # supposed to be instantiated with explicit arguments.
    def __call__(cls: typing.Type[T]) -> T:
        if cls is Singleton:
            raise TypeError("Singleton is an abstract class and cannot be instantiated.")
        
        if cls._instance_ is None:
            cls._instance_ = super().__call__()
        
        return cls._instance_


# A small hack to allow the Singleton class to be created
# (the metaclass constructor relies on the Singleton instance)
Singleton = None


class Singleton(metaclass=_SingletonMeta):
    """
    An abstract base class that makes your class a singleton.
    
    This means that creating an object will lazily call the constructor
    once, and then just return the same instance.
    
    If for whatever reason you need to access the instance explicitly,
    it's stored in `cls._instance_`.
    """


__all__ = [
    "Singleton",
]
