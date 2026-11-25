"""Streaming file digests used for input and evidence identity."""

import hashlib


def sha256(path):
    with path.open("rb") as stream:
        out = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            out.update(chunk)
    return out.hexdigest()
