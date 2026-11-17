"""Blender extension entry points. The shared protocol is also importable outside Blender."""


def register():
    from . import live

    live.start()


def unregister():
    from . import live

    live.stop()
