"""The bounded log tail.

The log is rotated but can still be megabytes, and this runs on a Pi Zero 2 W,
so the tail seeks backwards in chunks rather than reading the file. That makes
two things worth testing that a naive `readlines()[-n:]` would never expose: a
multi-byte character split across a chunk boundary, and a file shorter than one
chunk.

The path is read from `config['main']['log']['path']` (SPEC F17). It is
`/etc/pwnagotchi/log/pwnagotchi.log` on this fork and `/var/log/pwnagotchi.log`
on others, which is exactly why hardcoding it is forbidden.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from plugin import companion


@pytest.fixture
def written(tmp_path):
    def _write(text: str, name: str = "sample.log"):
        path = tmp_path / name
        path.write_bytes(text.encode("utf-8"))
        return str(path)

    return _write


# ---------------------------------------------------------------------------
# tail_lines
# ---------------------------------------------------------------------------


def test_the_last_lines_come_back_oldest_first(written):
    path = written("one\ntwo\nthree\nfour\nfive\n")

    assert companion.tail_lines(path, 3) == ["three", "four", "five"]


def test_asking_for_more_lines_than_exist_returns_what_there_is(written):
    path = written("one\ntwo\n")

    assert companion.tail_lines(path, 200) == ["one", "two"]


def test_a_file_smaller_than_one_chunk(written):
    path = written("only one line\n")

    assert companion.tail_lines(path, 200) == ["only one line"]


def test_an_empty_file_tails_to_nothing(written):
    assert companion.tail_lines(written(""), 200) == []


def test_a_file_without_a_trailing_newline_keeps_its_last_line(written):
    path = written("one\ntwo\nthree")

    assert companion.tail_lines(path, 2) == ["two", "three"]


def test_the_fixture_log_tails_to_its_real_end(log_file):
    expected = log_file.read_text(encoding="utf-8").splitlines()[-25:]

    assert companion.tail_lines(str(log_file), 25) == expected


def test_a_multibyte_character_across_a_chunk_boundary_does_not_raise(tmp_path):
    # The face characters pwnagotchi logs are multi-byte, and a backwards seek
    # lands mid-sequence sooner or later.
    path = tmp_path / "faces.log"
    path.write_text("".join(f"line {index} (⌐■_■) ✔\n" for index in range(4000)), "utf-8")

    tail = companion.tail_lines(str(path), 10)

    assert len(tail) == 10
    assert tail[-1] == "line 3999 (⌐■_■) ✔"


def test_undecodable_bytes_are_replaced_not_fatal(tmp_path):
    path = tmp_path / "broken.log"
    path.write_bytes(b"good line\n\xff\xfe not utf-8 \xc3\ngood again\n")

    tail = companion.tail_lines(str(path), 3)

    assert len(tail) == 3
    assert tail[0] == "good line"
    assert tail[-1] == "good again"


def test_an_exotic_separator_byte_does_not_invent_a_line(tmp_path):
    # A line is delimited by \n in a log file. str.splitlines() also breaks on
    # \x0b, \x1c-\x1e and  , so a log line carrying one of those would be
    # reported as two lines that were never written, and `count` would then be
    # counting something else.
    path = tmp_path / "separators.log"
    path.write_bytes("first\x1estill first\nsecond\n".encode("utf-8"))

    assert companion.tail_lines(str(path), 2) == ["first\x1estill first", "second"]


@pytest.mark.parametrize("count", [0, -1])
def test_a_non_positive_count_returns_nothing(written, count):
    assert companion.tail_lines(written("one\ntwo\n"), count) == []


@given(
    lines=st.lists(
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cc", "Cs", "Zl", "Zp"), blacklist_characters="\n\r"
            ),
            max_size=40,
        ),
        min_size=0,
        max_size=120,
    ),
    count=st.integers(min_value=1, max_value=150),
)
@settings(max_examples=200, deadline=None)
def test_the_tail_is_always_the_real_tail(tmp_path_factory, lines, count):
    path = tmp_path_factory.mktemp("tail") / "generated.log"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")

    tail = companion.tail_lines(str(path), count)

    assert tail == lines[-count:] if lines else tail == []
    assert len(tail) <= count


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


def test_the_path_comes_from_the_config(router, log_file):
    reply = router.log_lines(None)

    assert reply["path"] == str(log_file)


def test_the_default_is_two_hundred_lines(router):
    assert len(router.log_lines(None)["lines"]) == companion.LOG_LINES_DEFAULT


def test_a_requested_count_is_honoured(router):
    assert len(router.log_lines(12)["lines"]) == 12


def test_the_count_is_capped(router, tmp_path, agent_factory, router_factory):
    big = tmp_path / "big.log"
    big.write_text("".join(f"line {index}\n" for index in range(3000)), "utf-8")
    agent = agent_factory(
        config={
            "bettercap": {"handshakes": str(tmp_path)},
            "main": {"log": {"path": str(big)}},
        }
    )

    lines = router_factory(agent).log_lines(5000)["lines"]

    assert len(lines) == companion.LOG_LINES_MAX


def test_a_missing_log_is_an_error_not_an_empty_list(router_factory, agent_factory, tmp_path):
    agent = agent_factory(
        config={
            "bettercap": {"handshakes": str(tmp_path)},
            "main": {"log": {"path": str(tmp_path / "absent.log")}},
        }
    )

    replies = router_factory(agent).handle({"type": "get_log"}, authenticated=True)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["data"]["code"] == "log_unavailable"


def test_get_log_replies_with_lines_and_path(router, log_file):
    reply = router.handle({"type": "get_log", "lines": 5}, authenticated=True)[-1]

    assert reply["type"] == "log_lines"
    assert reply["data"]["path"] == str(log_file)
    assert len(reply["data"]["lines"]) == 5
