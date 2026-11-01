"""Настройки загружаются только при обращении к ним."""

__all__ = ["settings"]


def __getattr__(name):
    if name == "settings":
        from .settings import settings

        globals()[name] = settings
        return settings
    raise AttributeError(name)
