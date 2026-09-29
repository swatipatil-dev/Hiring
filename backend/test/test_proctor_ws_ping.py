"""Pre-test connectivity probe (proctoring/routes.py::ws_ping).

The candidate's system check opens this socket before a proctored test to prove that
WebSockets reach the proctor service and to time a frame-sized upload. These pin the
contract the frontend relies on (a pong carrying the byte count) and the bounds that
keep an unauthenticated socket from being held open or used as a sink.
"""
import pytest

# Runs in CI stage 2 (testing branch), which installs the proctoring extras.
pytestmark = pytest.mark.proctor

# routes.py imports cv2 at module scope; same guard as the other proctor tests.
pytest.importorskip("cv2", reason="proctoring extras not installed")

from fastapi import FastAPI                        # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from proctoring import routes                      # noqa: E402

URL = "/api/v1/proctor/ws/ping"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_pong_reports_the_bytes_received(client):
    with client.websocket_connect(URL) as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"event": "pong", "bytes": 4}
        payload = "x" * (64 * 1024)
        ws.send_text(payload)
        assert ws.receive_json() == {"event": "pong", "bytes": len(payload)}


def test_oversized_payload_is_refused(client):
    with client.websocket_connect(URL) as ws:
        ws.send_text("x" * (routes._PING_MAX_BYTES + 1))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1009


def test_socket_closes_after_the_message_budget(client):
    with client.websocket_connect(URL) as ws:
        for _ in range(routes._PING_MAX_MESSAGES):
            ws.send_text("ping")
            ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 1000
