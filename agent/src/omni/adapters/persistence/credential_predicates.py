"""One unreviewed credential authority predicate for diagnostics and retention guards."""

from . import schema as s


def unreviewed_credentials():
    return s.credential_references.c.state == "unknown"
