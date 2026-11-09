"""Fixed one-operation OS credential helper; no database, config lookup or plaintext fallback."""

from __future__ import annotations

import os
import sys

from ...domain.contracts import BusinessError
from ...domain.credential_ipc import ERRORS, MAX_FRAME, VERSION, control, decode, encode, request, response


def perform(query: dict, store) -> dict:
    result = {
        "version": VERSION,
        "request_id": query["request_id"],
        "operation": query["operation"],
        "phase": "result",
        "ok": False,
        "value": None,
        "error_code": "credential_store_unavailable",
    }
    try:
        if query["operation"] == "get":
            result["value"] = store.get(query["reference"])
        elif query["operation"] == "set":
            store.set(query["reference"], query["value"])
        else:
            store.delete(query["reference"])
        result.update(ok=True, error_code=None)
        return response(query, result)
    except BusinessError as error:
        result.update(
            ok=False, value=None, error_code=error.code if error.code in ERRORS else "credential_store_unavailable"
        )
    except Exception:  # noqa: BLE001 -- native exception messages may contain secret values
        result.update(ok=False, value=None, error_code="credential_store_unavailable")
    return result


def run(reader, writer, *, factory=None) -> int:
    initial = reader.readline(MAX_FRAME + 1)
    query = request(decode(initial))
    writer.write(encode(control(query, "ready")))
    writer.flush()
    permission = reader.readline(MAX_FRAME + 1)
    if len(initial) + len(permission) > MAX_FRAME or decode(permission) != control(query, "permit"):
        return 2
    if factory is None:
        from ..platform.secrets import SystemSecrets

        factory = SystemSecrets
    # Neither importing/constructing a native backend nor prompting happens
    # before an exact parent permit. Only Python composition/tests inject factories.
    result = perform(query, factory(query["namespace"]))
    writer.write(encode(result))
    writer.flush()
    return 0


def main() -> int:
    # Provider output cannot corrupt the protocol or leave secret-bearing text
    # in captured stdout. This redirection is confined to this isolated helper.
    try:
        descriptor = os.dup(sys.stdout.fileno())
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "wb", buffering=0) as protocol, open(os.devnull, "wb") as discard:
            os.dup2(discard.fileno(), sys.stdout.fileno())
            return run(sys.stdin.buffer, protocol)
    except Exception:  # noqa: BLE001 -- never export a credential traceback through either pipe
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
