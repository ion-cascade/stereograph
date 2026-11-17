import os
import socket
import unittest
from unittest.mock import patch

from stereograph import server
from stereograph_bridge import runtime, wire


class WireTests(unittest.TestCase):
    def test_frame_round_trip(self):
        sender, receiver = socket.socketpair()
        with sender, receiver:
            message = {"text": "Scene 雪", "values": [True, 1, None]}
            wire.send(sender, message)
            self.assertEqual(wire.receive(receiver), message)

    def test_invalid_json_is_rejected(self):
        for payload in (b"[1]", b"{", b'{"number":NaN}'):
            with self.subTest(payload=payload), self.assertRaises(wire.InvalidMessage):
                wire.decode(payload)

    def test_truncated_frame(self):
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.sendall(wire.HEADER.pack(2) + b"{")
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                wire.receive(receiver)

    def test_timeout_range(self):
        for value in (True, None, "1", 0, float("inf"), float("nan"), 121):
            with self.subTest(value=value):
                self.assertFalse(wire.valid_timeout(value))
        self.assertTrue(wire.valid_timeout(0.05))
        self.assertTrue(wire.valid_timeout(120))


class ExecutionTests(unittest.TestCase):
    def test_execution_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.execute_blender_code("result = 42", "session")["error"]["code"],
                             "python_disabled")
            self.assertEqual(server.execute_blender_code_for_cli("fixture.blend", "result = 42")["error"]["code"],
                             "python_disabled")
            self.assertEqual(server.blender_health()["error"]["code"], "unconfigured")

    def test_bridge_enforces_its_own_execution_setting(self):
        request = {"version": wire.VERSION, "operation": "execute", "expected_session": "current", "code": "pass"}
        self.assertEqual(runtime.dispatch(None, request, "current", False)["error"]["code"], "python_disabled")

    def test_output_is_bounded(self):
        result = runtime.run_python(None, "print('x' * 20000); result = 42")
        self.assertEqual(result["data"], 42)
        self.assertEqual(len(result["stdout"]), runtime.OUTPUT_LIMIT)
        result = runtime.run_python(None, "result = 'x' * 2000000")
        self.assertEqual(result["error"]["code"], "result_too_large")
        self.assertEqual(result["error"]["execution"], "completed")


if __name__ == "__main__":
    unittest.main()
