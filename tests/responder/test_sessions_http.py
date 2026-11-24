"""`GET /sessions` over real HTTP, with a stand-in callable (design spec
`2026-10-01-session-listing-and-run-requests-design.md` section 2.1). The
database half is in `tests/schema/test_session_listing.py`."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler


@pytest.fixture
def serve_sessions():
    started = []

    def _start(sessions_list_fn=_unused, *, with_sessions=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, sessions_list_fn=sessions_list_fn)
                       if with_sessions else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_sessions):
    calls = []
    base = serve_sessions(lambda since: calls.append(since) or {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions", method="GET")[0] == 401
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 3, "sessions": []})
    assert _request(f"{base}/sessions?since=2", method="GET", token=TOKEN)[0] == 200
    assert calls == [None, 2]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since="])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_sessions, query):
    base = serve_sessions()
    status, body = _request(f"{base}/sessions?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_a_host_without_the_listing_does_not_answer_it(serve_sessions):
    base = serve_sessions(with_sessions=False)
    assert _request(f"{base}/sessions", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/sessions?since=1", method="GET", token=TOKEN)[0] == 404


def test_only_get_answers_it_and_a_query_elsewhere_is_still_404(serve_sessions):
    base = serve_sessions(lambda since: {"cursor": 0, "sessions": []})
    assert _request(f"{base}/sessions", method="POST", token=TOKEN, body={})[0] == 405
    assert _request(f"{base}/health?since=1", method="GET", token=TOKEN)[0] == 404


def test_a_failure_behind_it_is_a_clean_500(serve_sessions):
    def broken(_since):
        raise RuntimeError("the database went away")

    status, body = _request(f"{serve_sessions(broken)}/sessions", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})


def test_get_sessions_lists_the_task_hash(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """One landed session, through HTTP: each run lists the hash of the task
    file it ran (the synthetic rig's start rows carry one)."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions

    daemon.activate_all(prefix=prefix)
    _recipe, key = _session(tmp_path_factory, subject="slhttp1", session_id="2025-08-01_01", runs=True,
                                 rig_record={"session_kind": "recording", "fields": True})
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since: list_sessions(since, prefix=prefix))
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    (entry,) = [entry for entry in json.loads(body)["sessions"] if entry["subject"] == key["subject"]]
    assert [run["task_hash"] for run in entry["runs"]] == ["sha256:synthetic", "sha256:synthetic"]


def test_one_reversed_session_does_not_break_the_page(serve_sessions, dj_conn, prefix, tmp_path_factory):
    """A run, a block and a file that end before they start, written into
    `core` after the session landed, are left out of its entry and flagged
    (design spec `2026-11-24-wl-works-requests-design.md` section 4.5). The
    page still answers, and lists the other session whole."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.sessions import list_sessions
    from wl_preproc.schema import core

    daemon.activate_all(prefix=prefix)
    _recipe, bad = _session(tmp_path_factory, subject="slhttp2", session_id="2025-08-04_01", runs=True,
                            rig_record={"session_kind": "recording", "fields": True})
    _recipe, good = _session(tmp_path_factory, subject="slhttp3", session_id="2025-08-05_01", runs=True,
                             rig_record={"session_kind": "recording", "fields": True})
    daemon.run_once(prefix=prefix)
    base = serve_sessions(lambda since: list_sessions(since, prefix=prefix))
    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    before = {entry["subject"]: entry for entry in json.loads(body)["sessions"]}

    first_run, second_run = (core.Run & bad).to_dicts(order_by="run_number")
    core.Run.update1({**first_run, "run_stop_time": first_run["run_start_time"] - 0.25})
    block = (core.BlockTime & bad & f"start_s > {second_run['run_start_time']}").to_dicts(order_by="start_s")[0]
    core.BlockTime.update1({**block, "stop_s": block["start_s"] - 0.5})
    (segment,) = (core.Segment & bad & {"system": "spikeglx"}).to_dicts()
    core.Segment.update1({**segment, "end_s": segment["start_s"] - 1.0})

    status, body = _request(f"{base}/sessions", method="GET", token=TOKEN)
    assert status == 200
    after = {entry["subject"]: entry for entry in json.loads(body)["sessions"]}
    assert after[good["subject"]] == before[good["subject"]]
    entry = after[bad["subject"]]
    assert [run["run_number"] for run in entry["runs"]] == [second_run["run_number"]]
    assert block["block_id"] not in [listed["block_number"] for listed in entry["runs"][0]["blocks"]]
    assert entry["segments"] == [] and entry["runs"][0]["segments"] == []
    withheld = [(flag["run_number"], flag["block_number"], flag["segment_id"])
                for flag in entry["flags"] if flag["code"] == "ends_before_start"]
    assert withheld == [(None, None, segment["segment_id"]), (first_run["run_number"], None, None),
                        (second_run["run_number"], block["block_id"], None)]
