import importlib

from configurationLoader import config

DEFAULT_BUILTIN_MODULES = ["Tools.builtin.core_tools"]
_LOADED_MODULES = set()


def _coerce_module_names(raw_value):
    if raw_value is None:
        return list(DEFAULT_BUILTIN_MODULES)
    if isinstance(raw_value, str):
        module_name = raw_value.strip()
        return [module_name] if module_name else list(DEFAULT_BUILTIN_MODULES)
    if isinstance(raw_value, list):
        modules = [str(item).strip() for item in raw_value if str(item).strip()]
        return modules or list(DEFAULT_BUILTIN_MODULES)
    raise TypeError("tools.builtin_modules must be a string or list of strings")


def get_builtin_module_names(modules=None):
    if modules is not None:
        return _coerce_module_names(modules)
    return _coerce_module_names(config.get("tools.builtin_modules", DEFAULT_BUILTIN_MODULES))


def load_builtin_tools(modules=None):
    imported_modules = []
    for module_name in get_builtin_module_names(modules):
        importlib.import_module(module_name)
        _LOADED_MODULES.add(module_name)
        imported_modules.append(module_name)
    return imported_modules
