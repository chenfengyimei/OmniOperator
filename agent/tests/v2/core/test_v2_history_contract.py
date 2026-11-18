"""History cursors reject ambiguous encodings, expired generations and cross-query reuse without I/O."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from uuid import UUID

import pytest

from omni.domain.contracts import BusinessError
from omni.domain.history import FORMAT, MAX_CURSOR_LENGTH, MAX_INTEGER, decode_cursor, encode_cursor

EPOCH = str(UUID(int=1))
SCOPE = str(UUID(int=2))
RUN = str(UUID(int=3))


def context(**changes):
    return {"epoch": EPOCH, "kind": "events", "scope": SCOPE, "revision": 4, **changes}


def raw_cursor(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def payload(**changes):
    return {"format": FORMAT, **context(), "position": 10, "filters": {}, **changes}


def pack(fields):
    return raw_cursor(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode())


def assert_error(call, code="invalid_history_cursor", status=422):
    with pytest.raises(BusinessError) as failure:
        call()
    assert (failure.value.code, failure.value.status, failure.value.retryable) == (code, status, False)
    assert "private-canary" not in str(failure.value)


def test_roundtrip_is_bounded_canonical_and_accepts_zero_and_int64_boundaries():
    for kind, revision, position in (("events", 0, 0), ("audit", MAX_INTEGER, MAX_INTEGER), ("instance_audit", 4, 10)):
        query = context(kind=kind, revision=revision)
        value = encode_cursor(**query, position=position)
        assert len(value) <= MAX_CURSOR_LENGTH and "=" not in value
        assert decode_cursor(value, **query, high_water=position) == position
        assert value == pack(payload(kind=kind, revision=revision, position=position))


def test_filters_are_copied_bound_to_events_and_empty_filters_have_one_encoding():
    filters = {"run_id": RUN}
    before = deepcopy(filters)
    value = encode_cursor(**context(), position=10, filters=filters)
    assert filters == before
    assert decode_cursor(value, **context(), high_water=10, filters=before) == 10
    filters["run_id"] = str(UUID(int=5))
    assert decode_cursor(value, **context(), high_water=10, filters=before) == 10
    assert_error(lambda: decode_cursor(value, **context(), high_water=10, filters=filters), "history_scope_mismatch")
    assert encode_cursor(**context(), position=10) == encode_cursor(**context(), position=10, filters={})


@pytest.mark.parametrize("change", [{"epoch": str(UUID(int=99))}, {"revision": 5}, {"revision": 3}])
def test_changed_instance_or_trim_generation_expires_instead_of_silent_empty_history(change):
    value = encode_cursor(**context(), position=10)
    assert_error(lambda: decode_cursor(value, **context(**change), high_water=20), "history_cursor_expired", 410)


@pytest.mark.parametrize("change", [{"kind": "audit"}, {"scope": str(UUID(int=99))}, {"filters": {"run_id": RUN}}])
def test_stream_scope_and_filters_cannot_be_reused_even_if_generation_changed(change):
    value = encode_cursor(**context(), position=10)
    assert_error(
        lambda: decode_cursor(value, **context(epoch=str(UUID(int=99)), **change), high_water=20),
        "history_scope_mismatch",
    )


def test_cursor_cannot_point_after_the_persisted_high_water():
    value = encode_cursor(**context(), position=10)
    assert decode_cursor(value, **context(), high_water=10) == 10
    assert_error(lambda: decode_cursor(value, **context(), high_water=9))


@pytest.mark.parametrize(
    "change",
    [
        {"epoch": "00000000000000000000000000000001"},
        {"scope": "ABCDEF00-0000-0000-0000-000000000001"},
        {"kind": "messages"},
        {"revision": True},
        {"revision": -1},
        {"position": False},
        {"position": MAX_INTEGER + 1},
        {"position": 1.0},
        {"filters": []},
        {"filters": {"run_id": "private-canary"}},
        {"filters": {"agent_id": RUN}},
        {"kind": "audit", "filters": {"run_id": RUN}},
    ],
)
def test_encoder_rejects_invalid_identity_integer_and_filter_inputs(change):
    arguments = {**context(), "position": 10, **change}
    assert_error(lambda: encode_cursor(**arguments))


@pytest.mark.parametrize("high_water", [True, -1, MAX_INTEGER + 1, 10.0])
def test_decoder_validates_caller_high_water_instead_of_coercing_it(high_water):
    value = encode_cursor(**context(), position=10)
    assert_error(lambda: decode_cursor(value, **context(), high_water=high_water))


@pytest.mark.parametrize(
    "change",
    [
        {"format": "omni-history-cursor-2"},
        {"extra": "private-canary"},
        {"filters": None},
        {"filters": {"run_id": RUN, "extra": "private-canary"}},
        {"revision": False},
        {"position": -1},
        {"position": MAX_INTEGER + 1},
        {"scope": "private-canary"},
    ],
)
def test_decoder_validates_the_entire_untrusted_payload(change):
    assert_error(lambda: decode_cursor(pack(payload(**change)), **context(), high_water=MAX_INTEGER))


def test_missing_fields_and_duplicate_json_keys_are_invalid():
    fields = payload()
    del fields["filters"]
    assert_error(lambda: decode_cursor(pack(fields), **context(), high_water=10))
    raw = json.dumps(payload(), sort_keys=True, separators=(",", ":"))
    duplicate = raw[:-1] + ',"scope":"' + SCOPE + '"}'
    assert_error(lambda: decode_cursor(raw_cursor(duplicate.encode()), **context(), high_water=10))
    duplicate_filter = pack(payload(filters={"run_id": RUN}))
    raw = base64.urlsafe_b64decode(duplicate_filter + "=" * (-len(duplicate_filter) % 4)).decode()
    raw = raw.replace('"run_id":"' + RUN + '"', '"run_id":"' + RUN + '","run_id":"' + RUN + '"')
    assert_error(lambda: decode_cursor(raw_cursor(raw.encode()), **context(), high_water=10, filters={"run_id": RUN}))


def test_noncanonical_json_is_not_an_alternative_cursor_encoding():
    canonical = json.dumps(payload(), sort_keys=True, separators=(",", ":"))
    alternatives = [
        json.dumps(payload()).encode(),
        (" " + canonical).encode(),
        canonical.replace("00000000", r"\u00300000000", 1).encode(),
        canonical.replace('"position":10', '"position":NaN').encode(),
        b"\xef\xbb\xbf" + canonical.encode(),
        b"[]",
        b"null",
        b"\xff",
        canonical[:-1].encode(),
    ]
    for raw in alternatives:
        assert_error(lambda: decode_cursor(raw_cursor(raw), **context(), high_water=10))


def test_base64url_is_unpadded_strict_and_bounded_before_decoding():
    value = encode_cursor(**context(), position=10)
    alternatives = [None, 123, "", "A", value + "=", " " + value, value + "\n", "+" + value, "/" + value, "a" * 1025]
    for bad in alternatives:
        assert_error(lambda: decode_cursor(bad, **context(), high_water=10))
    raw = json.dumps(payload(), sort_keys=True, separators=(",", ":")).encode()
    while len(raw) % 3 == 0:
        raw += b" "
    value = raw_cursor(raw)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    replacement = alphabet[alphabet.index(value[-1]) + 1]
    noncanonical_padding_bits = value[:-1] + replacement
    assert base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)) == base64.urlsafe_b64decode(
        noncanonical_padding_bits + "=" * (-len(noncanonical_padding_bits) % 4)
    )
    assert_error(lambda: decode_cursor(noncanonical_padding_bits, **context(), high_water=10))
