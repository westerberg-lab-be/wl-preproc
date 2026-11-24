"""`GET /nwb` and `PUT /nwb/active` over real HTTP, with stand-in callables
(design spec `2026-09-29-nwb-publishing-design.md` section 6). The database
half is exercised end to end in `tests/schema/test_nwb_build.py`."""

from __future__ import annotations

import datetime
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.responder.test_http import TOKEN, _health_ok, _request, _unused
from wl_preproc.responder.handler import make_handler

KEY = {"subject": "monk01", "session_datetime": "2027-01-12T09:00:00", "montage_id": 0, "activation_id": 0}


@pytest.fixture
def serve_nwb():
    started = []

    def _start(nwb_list_fn=_unused, nwb_active_fn=_unused, *, with_nwb=True):
        handler_cls = (make_handler(TOKEN, _health_ok, _unused, nwb_list_fn, nwb_active_fn) if with_nwb
                       else make_handler(TOKEN, _health_ok, _unused))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_the_listing_is_behind_the_token_and_takes_an_optional_cursor(serve_nwb):
    calls = []
    base = serve_nwb(nwb_list_fn=lambda since, limit: calls.append((since, limit)) or {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb", method="GET")[0] == 401
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (200, {"cursor": 7, "files": []})
    assert _request(f"{base}/nwb?since=5", method="GET", token=TOKEN)[0] == 200
    assert calls == [(None, None), (5, None)]


@pytest.mark.parametrize("query", ["since=x", "since=-1", "since=1&since=2", "other=1", "since=",
                                   "since=%C2%B2", "since=%D9%A3"])
def test_a_cursor_that_is_not_one_non_negative_integer_is_422(serve_nwb, query):
    base = serve_nwb(nwb_list_fn=_unused)
    status, body = _request(f"{base}/nwb?{query}", method="GET", token=TOKEN)
    assert status == 422 and "since" in json.loads(body)["error"]


def test_the_active_set_is_accepted_whole(serve_nwb):
    received = []

    def active(request):
        received.append(request)
        return {"accepted": len(request.activations), "unknown": []}

    base = serve_nwb(nwb_active_fn=active)
    status, body = _request(f"{base}/nwb/active", method="PUT", token=TOKEN,
                            body={"activations": [KEY], "requested_by": "jw"})
    assert (status, json.loads(body)) == (202, {"accepted": 1, "unknown": []})
    (request,) = received
    assert request.requested_by == "jw" and request.activations[0].subject == "monk01"


@pytest.mark.parametrize("body", [{"activations": [KEY], "extra": 1}, {"activations": [{**KEY, "activation_id": -1}]},
                                  b"not json"])
def test_a_malformed_active_set_is_422(serve_nwb, body):
    base = serve_nwb(nwb_active_fn=_unused)
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body=body)[0] == 422


def test_a_failure_behind_either_endpoint_is_a_clean_500(serve_nwb):
    def broken(*_args):
        raise RuntimeError("the database went away")

    base = serve_nwb(nwb_list_fn=broken, nwb_active_fn=broken)
    status, body = _request(f"{base}/nwb", method="GET", token=TOKEN)
    assert (status, json.loads(body)) == (500, {"error": "RuntimeError: the database went away"})
    assert _request(f"{base}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 500


def test_the_older_endpoints_answer_as_before(serve_nwb):
    """A query string anywhere but `/nwb` is still a path this host does not
    answer, PUT anywhere but `/nwb/active` is still 405, and a handler built
    without the NWB callables has no NWB paths at all."""
    base = serve_nwb()
    assert _request(f"{base}/health?x=1", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{base}/health", method="PUT", token=TOKEN)[0] == 405
    assert _request(f"{base}/nwb/active", method="GET", token=TOKEN)[0] == 405
    bare = serve_nwb(with_nwb=False)
    assert _request(f"{bare}/nwb", method="GET", token=TOKEN)[0] == 404
    assert _request(f"{bare}/nwb/active", method="PUT", token=TOKEN, body={"activations": []})[0] == 404


def test_files_page_by_limit(serve_nwb, dj_conn, prefix, tmp_path_factory):
    """Five changed activations and `limit=2` give pages of 2, 2 and 1, ordered by each file's latest
    change; each cursor is the page's latest `change_seq`, the short page ends it, and an empty page's
    cursor is `since` (design spec `2026-11-24-wl-works-requests-design.md` section 4.4)."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.responder.nwb import list_files
    from wl_preproc.schema import core
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema import request

    daemon.activate_all(prefix=prefix)
    _recipe, session = _session(tmp_path_factory, subject="nwbpage1", session_id="2025-08-10_01")
    now = datetime.datetime(2027, 6, 1, 12, 0)
    core.Montage.insert1({**session, "montage_id": 3, "start_s": 0.0, "end_s": 12.0}, skip_duplicates=True)
    request.Request.insert1({"idempotency_key": "nwbpage1-key", "task_type": "neural", "origin": "wl_works",
                             "payload": {}, "requested_at": now})
    floor = int(max(list(nwb_schema.NwbChange.to_arrays("change_seq")) or [0]))
    keys = [{**session, "montage_id": 3, "activation_id": number} for number in range(5)]
    try:
        for key in keys:
            request.Activation.insert1({**key, "role": "canonical", "request_key": "nwbpage1-key", "created_at": now})
            nwb_schema.NwbFile.insert1({**key, "status": "refused", "built_at": now, "reason": "no blocks"})
        # Activation 0 changes again after the others, so its latest change is the last.
        for key in keys + keys[:1]:
            nwb_schema.NwbChange.insert1({**key, "kind": "built", "changed_at": now}, ignore_extra_fields=True)
        base = serve_nwb(nwb_list_fn=lambda since, limit: list_files(since, limit, prefix=prefix))
        seqs = {}
        for row in nwb_schema.NwbChange & keys:
            seqs[row["activation_id"]] = max(seqs.get(row["activation_id"], 0), row["change_seq"])
        order = sorted(seqs, key=seqs.get)
        assert order == [1, 2, 3, 4, 0]

        since, pages = floor, []
        for _ in range(3):
            status, body = _request(f"{base}/nwb?since={since}&limit=2", method="GET", token=TOKEN)
            assert status == 200
            page = json.loads(body)
            pages.append([file["activation"]["activation_id"] for file in page["files"]])
            assert page["cursor"] == max(seqs[number] for number in pages[-1])
            since = page["cursor"]
        assert pages == [[1, 2], [3, 4], [0]]
        status, body = _request(f"{base}/nwb?since={since}&limit=2", method="GET", token=TOKEN)
        assert (status, json.loads(body)) == (200, {"cursor": since, "files": []})
    finally:
        (nwb_schema.NwbChange & keys).delete(prompt=False)
        (nwb_schema.NwbFile & keys).delete(prompt=False)
        (request.Activation & keys).delete(prompt=False)
        (request.Request & {"idempotency_key": "nwbpage1-key"}).delete(prompt=False)
        (core.Montage & {**session, "montage_id": 3}).delete(prompt=False)
