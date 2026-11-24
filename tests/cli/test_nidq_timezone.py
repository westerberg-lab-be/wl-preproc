"""`--nidq-timezone` on the daemon's pass, which stores each session's t = 0
with it (`timebase.ClockReference`; design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendment 46), and
not on `wlpp nwb build`, which reads the stored value and could do nothing
with a zone (the controller's ruling P9). No path reaches the database here."""

from __future__ import annotations

import pytest

_BUILD = ["nwb", "build", "--subject", "pico", "--session-datetime", "2024-01-01T09:00:00", "--montage-id", "0",
          "--activation-id", "0", "--nwb-root", "unused"]
_COMMANDS = {"daemon": ["daemon"]}


class _Stopped(Exception):
    """Raised where each command would first reach the database."""


@pytest.fixture
def clock(monkeypatch):
    """The session clock's module, its zone unset for the test and restored
    after it: the command sets it unguarded."""
    from wl_preproc.timebase import clock

    monkeypatch.setattr(clock, "NIDQ_TIMEZONE", None)
    return clock


@pytest.fixture
def stopped(monkeypatch):
    """Each command stopped where it would first reach the database: the
    daemon's pass, and the build's `activate_all`. The detectors' libraries
    are reported present, so the daemon reaches its pass on any host."""
    from wl_preproc import daemon
    from wl_preproc.eye.detect import registry

    def stop(*_args, **_kwargs):
        raise _Stopped

    monkeypatch.setattr(daemon, "run_once", stop)
    monkeypatch.setattr(daemon, "activate_all", stop)
    monkeypatch.setattr(registry, "unavailable_detectors", lambda: {})


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_an_unknown_zone_is_refused_before_anything_runs(command, clock, stopped, capsys):
    from wl_preproc.cli.main import main

    with pytest.raises(SystemExit) as raised:
        main([*_COMMANDS[command], "--nidq-timezone", "Mars/Base"])
    assert raised.value.code == 2
    assert "--nidq-timezone: 'Mars/Base' is not a time zone" in capsys.readouterr().err
    assert clock.NIDQ_TIMEZONE is None


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_a_zone_reaches_the_session_clock_before_the_database(command, clock, stopped):
    from wl_preproc.cli.main import main

    with pytest.raises(_Stopped):
        main([*_COMMANDS[command], "--nidq-timezone", "Europe/Berlin"])
    assert clock.NIDQ_TIMEZONE == "Europe/Berlin"


@pytest.mark.parametrize("command", sorted(_COMMANDS))
def test_without_the_option_the_zone_stays_unset(command, clock, stopped):
    from wl_preproc.cli.main import main

    with pytest.raises(_Stopped):
        main(_COMMANDS[command])
    assert clock.NIDQ_TIMEZONE is None


def test_nwb_build_refuses_a_zone(clock, stopped, capsys):
    """A file's t = 0 is read as the daemon stored it, so a zone given to a
    build by hand would be silently ignored: the parser refuses it instead,
    before anything runs."""
    from wl_preproc.cli.main import main

    # `main` returns argparse's usage status rather than raising it.
    assert main([*_BUILD, "--nidq-timezone", "Europe/Berlin"]) == 2
    assert "unrecognized arguments: --nidq-timezone Europe/Berlin" in capsys.readouterr().err
    assert clock.NIDQ_TIMEZONE is None
