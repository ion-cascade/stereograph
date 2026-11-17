"""Local listener and Blender main-thread dispatch."""

from concurrent.futures import CancelledError, Future, TimeoutError
import hmac
import os
from queue import Empty, Full, Queue
import socket
import sys
from threading import Event, Thread
from uuid import uuid4

import bpy
from bpy.app.handlers import persistent

from . import runtime, wire

_bridge = None


class Bridge:
    def __init__(self, token, port, allow_python):
        self.token = token
        self.allow_python = allow_python
        self.session_id = uuid4().hex
        self.inbox = Queue(maxsize=8)
        self.stopping = Event()
        self.listener = socket.socket()
        try:
            if os.name != "nt":
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind((wire.HOST, port))
            self.listener.listen(4)
            self.listener.settimeout(0.2)
        except OSError:
            self.listener.close()
            raise
        self.worker = Thread(target=self.listen, name="Stereograph listener", daemon=True)

    def listen(self):
        while not self.stopping.is_set():
            try:
                peer, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with peer:
                peer.settimeout(2)
                try:
                    try:
                        request = wire.receive(peer)
                    except wire.InvalidMessage as exc:
                        response = wire.failure("bad_request", str(exc))
                    else:
                        response = self.request(request)
                    wire.send(peer, response)
                except (OSError, EOFError):
                    continue

    def request(self, request):
        supplied = request.get("token")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(errors="surrogatepass"), self.token):
            return wire.failure("unauthorized", "The client token could not be verified.")
        timeout = request.get("timeout")
        if not wire.valid_timeout(timeout, request.get("operation")):
            return wire.failure("bad_request", "Invalid request timeout.")
        future = Future()
        if self.stopping.is_set():
            return wire.failure("blender_unavailable", "The Blender bridge is stopping.")
        try:
            self.inbox.put_nowait((request, future))
        except Full:
            return wire.failure("busy", "The Blender bridge queue is full.")
        try:
            return future.result(timeout)
        except CancelledError:
            return wire.failure("blender_unavailable", "The Blender bridge stopped.")
        except TimeoutError:
            state = "not_started" if future.cancel() else "unknown"
            return wire.failure("timeout", "Blender did not complete the request before the deadline.", state)

    def tick(self):
        try:
            request, future = self.inbox.get_nowait()
        except Empty:
            return 0.02
        if future.set_running_or_notify_cancel():
            try:
                result = runtime.dispatch(bpy, request, self.session_id, self.allow_python)
            except Exception as exc:
                result = wire.failure("bridge_error", str(exc)[:1000], "unknown")
            future.set_result(result)
        return 0.02

    def shutdown(self):
        self.stopping.set()
        self.listener.close()
        while True:
            try:
                _, future = self.inbox.get_nowait()
            except Empty:
                break
            future.cancel()


@persistent
def _file_loaded(_):
    if _bridge is not None:
        _bridge.session_id = uuid4().hex


def start():
    global _bridge
    if _bridge is not None:
        return
    token = os.environ.get("STEREOGRAPH_TOKEN", "")
    if len(token) < 32:
        print("Stereograph bridge idle. Set STEREOGRAPH_TOKEN before starting Blender.")
        return
    port = wire.port_number(os.environ.get("STEREOGRAPH_PORT", wire.DEFAULT_PORT))
    bridge = Bridge(token.encode(), port, os.environ.get("STEREOGRAPH_ALLOW_PYTHON") == "1")
    _bridge = bridge
    bpy.app.handlers.load_post.append(_file_loaded)
    bpy.app.timers.register(bridge.tick, persistent=True)
    bridge.worker.start()


def stop():
    global _bridge
    bridge = _bridge
    if bridge is None:
        return
    _bridge = None
    bpy.app.handlers.load_post.remove(_file_loaded)
    if bpy.app.timers.is_registered(bridge.tick):
        bpy.app.timers.unregister(bridge.tick)
    for namespace in ("posing", "rigging"):
        module = sys.modules.get(f"{__package__}.{namespace}.dispatch")
        if module is not None:
            module.unregister()
    bridge.shutdown()
