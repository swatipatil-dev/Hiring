"""Two companies, each with an admin, an HR user, a working sender and API keys.

Import the ``world`` fixture into a test module to use it. Setup is committed
(the db fixture turns a commit into a savepoint release) so that a route which
rolls back on a validation error cannot take the fixture's rows with it.
"""
from types import SimpleNamespace

import pytest

from src.email_api import api_keys, constants as C, ssrf
from test.email_factories import make_company, make_smtp_sender, make_route
from test.test_tenant_isolation import _make_user, _login


def _company(db, client, name):
    co = make_company(db, name)
    sender, conn = make_smtp_sender(db, co.id, "HR", email=f"hr@{name.lower()}.example",
                                    display_name=f"{name} Recruitment")
    make_route(db, co.id, C.DEFAULT_ROUTE, sender.id)
    admin_email, hr_email = f"admin@{name.lower()}.example", f"hr-user@{name.lower()}.example"
    _make_user(db, admin_email, "admin", co.id)
    _make_user(db, hr_email, "hr", co.id)
    live, live_token = api_keys.create(db, company_id=co.id, name="live", mode="live",
                                       scopes=C.SCOPES, actor_user_id=None)
    test, test_token = api_keys.create(db, company_id=co.id, name="test", mode="test",
                                       scopes=C.SCOPES, actor_user_id=None)
    db.commit()
    return SimpleNamespace(
        id=co.id, company=co, sender=sender, conn=conn,
        admin=_login(client, admin_email), hr=_login(client, hr_email),
        live={"Authorization": f"Bearer {live_token}"}, live_key=live,
        test={"Authorization": f"Bearer {test_token}"}, test_key=test,
    )


@pytest.fixture()
def world(client, db, monkeypatch):
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)
    monkeypatch.setattr(ssrf, "_system_resolve", lambda host, port: ["93.184.216.34"])
    monkeypatch.delenv("EMAIL_SYNC", raising=False)
    return SimpleNamespace(a=_company(db, client, "Alpha"), b=_company(db, client, "Beta"))


def send_body(**kw):
    body = {"to": [{"email": "candidate@example.net", "name": "Candidate"}],
            "subject": "Interview invitation", "html": "<p>Your interview is scheduled.</p>"}
    body.update(kw)
    return body
