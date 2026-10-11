"""Menu art build tooling."""


def __getattr__(name):
    if name == "compose":
        from .compose import compose
        globals()["compose"] = compose
        return compose
    raise AttributeError(name)

__all__ = ["compose"]
