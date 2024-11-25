from __future__ import annotations
import typing
import pathlib
import importlib.util
import sys


def import_config(config_path: pathlib.Path) -> None:
    """
    After this function is called, `import config` will work to import the
    configuration file specified by `config_path`.
    """

    # TODO: Store the config somewhere proper

    # Cannot properly typehint these, since the types used aren't exposed :(

    MODULE_NAME: typing.Final[str] = "config"

    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, str(config_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)


__all__ = [
    "import_config",
]