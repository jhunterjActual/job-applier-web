"""Offline immutable repository bundles; callers own repository locks and custody.

This module never contacts a provider or establishes restore acceptance. The input
generation must be a freshly captured complete source file-set under that lock.
Filesystem operations deliberately require POSIX no-follow, descriptor-relative
opens. Failed operations retain partial, exclusively created outputs for explicit
reconciliation. Exceptions contain no private paths or provider information.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import re
import stat
import tarfile


SCHEMA = "careertrellis.pgbackrest-bundle/v1"
GENERATION_SCHEMA = "careertrellis.pgbackrest-file-set/v1"
MAX_BYTES = 512 * 1024 ** 2
MAX_MANIFEST = 32 * 1024 ** 2
MAX_FILES = 100000
MAX_DIRECTORIES = 20000
MAX_DEPTH = 16
METADATA = frozenset(("archive/careertrellis/archive.info",
    "archive/careertrellis/archive.info.copy", "backup/careertrellis/backup.info",
    "backup/careertrellis/backup.info.copy", "backup/careertrellis/latest"))
REQUIRED_METADATA = METADATA - {"backup/careertrellis/latest"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?\Z")


class BundleError(Exception):
    """A fixed, privacy-safe bundle refusal."""


def need(value):
    if not value:
        raise BundleError("repository_bundle_refused")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def valid_path(value):
    return (isinstance(value, str) and len(value) <= 240
        and re.fullmatch(r"(?:archive|backup)/careertrellis/[A-Za-z0-9_./-]+", value)
        and len(value.split("/")) <= MAX_DEPTH
        and all(part not in ("", ".", "..") for part in value.split("/")))


def _path_budget(files):
    """Bound extraction inodes before creating any output directories."""
    parents = set()
    for name in files:
        need(valid_path(name))
        parts = name.split("/")
        for index in range(1, len(parts)):
            parents.add("/".join(parts[:index]))
            need(len(parents) <= MAX_DIRECTORIES)


def validate_manifest(value):
    need(type(value) is dict and set(value) == {"schema", "backup_label",
        "generation_sha256", "files", "total_bytes"} and value["schema"] == SCHEMA)
    need(isinstance(value["backup_label"], str) and LABEL.fullmatch(value["backup_label"]))
    need(isinstance(value["generation_sha256"], str) and HEX.fullmatch(value["generation_sha256"]))
    files = value["files"]
    need(type(files) is dict and 0 < len(files) <= MAX_FILES)
    _path_budget(files)
    total = 0
    for path, entry in files.items():
        need(valid_path(path) and type(entry) is dict and set(entry) == {"size", "sha256"})
        need(type(entry["size"]) is int and 0 <= entry["size"] <= MAX_BYTES)
        need(isinstance(entry["sha256"], str) and HEX.fullmatch(entry["sha256"]))
        total += entry["size"]
    need(type(value["total_bytes"]) is int and value["total_bytes"] == total <= MAX_BYTES)
    need(REQUIRED_METADATA <= set(files))
    for label in {value["backup_label"], value["backup_label"].split("_")[0]}:
        need("backup/careertrellis/" + label + "/backup.manifest" in files)
    return value


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result)
            result[key] = value
        return result
    need(len(raw) <= MAX_MANIFEST)
    value = json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: need(False))
    need(canonical(value) == raw)
    return validate_manifest(value)


def _supported():
    need(hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
         and os.mkdir in os.supports_dir_fd and os.scandir in os.supports_fd)


def _identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_nlink,
            meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


@contextmanager
def _directory(path):
    """Open every absolute ancestor without following links, including root."""
    _supported()
    path = Path(os.path.abspath(path))
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _parent(root, name, create=False):
    descriptor = os.dup(root)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _scan(root, prefix="", directory_count=None):
    if directory_count is None:
        directory_count = [0, 0]
    files = {}
    with os.scandir(root) as iterator:
        names = [entry.name for entry in itertools.islice(iterator, MAX_FILES + MAX_DIRECTORIES + 1)]
    need(len(names) <= MAX_FILES + MAX_DIRECTORIES)
    for leaf in sorted(names):
        name = prefix + leaf
        meta = os.stat(leaf, dir_fd=root, follow_symlinks=False)
        if stat.S_ISDIR(meta.st_mode):
            directory_count[0] += 1
            need(directory_count[0] <= MAX_DIRECTORIES and len(name.split("/")) <= MAX_DEPTH)
            need(name in ("archive", "backup", "archive/careertrellis", "backup/careertrellis")
                 or valid_path(name))
            child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                files.update(_scan(child, name + "/", directory_count))
            finally:
                os.close(child)
        else:
            directory_count[1] += 1
            need(directory_count[1] <= MAX_FILES)
            need(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and valid_path(name))
            files[name] = _identity(meta)
        need(len(files) <= MAX_FILES)
    return files


@contextmanager
def _source(root, name, expected):
    with _parent(root, name) as (parent, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            need(_identity(os.fstat(fd)) == expected)
            yield stream
            need(_identity(os.fstat(fd)) == expected
                 and _identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == expected)


def _hash(stream, size):
    value = hashlib.sha256()
    remaining = size
    while remaining:
        block = stream.read(min(1024 * 1024, remaining))
        need(bool(block))
        value.update(block)
        remaining -= len(block)
    need(not stream.read(1))
    return value.hexdigest()


def _generation(value):
    need(type(value) is dict and set(value) == {"schema", "backup_label", "started_at",
        "files", "metadata_sha256"} and value["schema"] == GENERATION_SCHEMA)
    need(isinstance(value["backup_label"], str) and LABEL.fullmatch(value["backup_label"]))
    need(isinstance(value["started_at"], str)
         and dt.datetime.fromisoformat(value["started_at"].replace("Z", "+00:00")).utcoffset()
         == dt.timedelta(0))
    need(type(value["files"]) is dict and 0 < len(value["files"]) <= MAX_FILES)
    _path_budget(value["files"])
    need(type(value["metadata_sha256"]) is dict
         and set(value["metadata_sha256"]) == set(value["files"]) & METADATA
         and REQUIRED_METADATA <= set(value["metadata_sha256"]))
    for name, size in value["files"].items():
        need(valid_path(name) and type(size) is int and 0 <= size <= MAX_BYTES)
    for value_digest in value["metadata_sha256"].values():
        need(isinstance(value_digest, str) and HEX.fullmatch(value_digest))
    need(sum(value["files"].values()) <= MAX_BYTES)


def _member(name, size):
    value = tarfile.TarInfo(name)
    value.size, value.mode, value.mtime = size, 0o600, 0
    value.uid = value.gid = 0
    value.uname = value.gname = ""
    return value


def _plain(member):
    return (member is not None and member.type == tarfile.REGTYPE
            and not member.linkname and not member.pax_headers and not member.sparse
            and member.offset_data == member.offset + 512
            and member.mode == 0o600 and member.uid == member.gid == member.mtime == 0
            and member.uname == member.gname == "")


def _payload_end(manifest_bytes, files):
    blocks = 1 + (manifest_bytes + 511) // 512
    blocks += sum(1 + (entry["size"] + 511) // 512 for entry in files.values())
    return blocks * 512


def _archive_size(payload_end):
    return ((payload_end + 1024 + 10239) // 10240) * 10240


def build_bundle(repository, archive, generation):
    """Create a new uncompressed deterministic tar and return its digest/manifest.

    `generation` is the complete pgbackrest-file-set/v1 document. All source
    entries must match it exactly. Output is exclusive and must be outside the
    source tree. Caller must bind the returned generation digest to its receipt.
    """
    try:
        _generation(generation)
        archive, repository = Path(archive).absolute(), Path(repository).absolute()
        need(repository != archive and repository not in archive.parents)
        with _directory(repository) as root, _directory(archive.parent) as output:
            snapshot = _scan(root)
            need(set(snapshot) == set(generation["files"]))
            entries = {}
            for name in sorted(snapshot):
                size = generation["files"][name]
                need(snapshot[name][4] == size)
                with _source(root, name, snapshot[name]) as stream:
                    digest = _hash(stream, size)
                if name in METADATA:
                    need(digest == generation["metadata_sha256"][name])
                entries[name] = {"size": size, "sha256": digest}
            manifest = validate_manifest({"schema": SCHEMA,
                "backup_label": generation["backup_label"],
                "generation_sha256": hashlib.sha256(canonical(generation)).hexdigest(),
                "files": entries, "total_bytes": sum(row["size"] for row in entries.values())})
            payload = canonical(manifest)
            need(len(payload) <= MAX_MANIFEST)
            need(_archive_size(_payload_end(len(payload), entries)) <= MAX_BYTES)
            fd = os.open(archive.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=output)
            with os.fdopen(fd, "w+b") as result:
                with tarfile.open(fileobj=result, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                    tar.addfile(_member("manifest.json", len(payload)), io.BytesIO(payload))
                    for name in sorted(entries):
                        with _source(root, name, snapshot[name]) as stream:
                            tar.addfile(_member("repository/" + name, entries[name]["size"]), stream)
                            stream.seek(0)
                            need(_hash(stream, entries[name]["size"]) == entries[name]["sha256"])
                need(_scan(root) == snapshot)
                result.flush()
                os.fsync(result.fileno())
                size = result.tell()
                need(size <= MAX_BYTES)
                result.seek(0)
                digest = _hash(result, size)
            os.fsync(output)
        return {"sha256": digest, "archive_bytes": size, "manifest": manifest}
    except BundleError:
        raise
    except Exception:
        raise BundleError("repository_bundle_refused") from None


def extract_bundle(archive, destination, expected_sha256):
    """Verify downloaded tar digest and extract to a new private destination.

    No tarfile extraction helpers are used. Only exact regular members named by
    the canonical manifest are accepted; links, sparse/PAX extensions, unknown
    entries, duplicates, malformed metadata and oversized content refuse. The
    caller retains failed partial outputs until identity-bound reconciliation.
    """
    try:
        need(isinstance(expected_sha256, str) and HEX.fullmatch(expected_sha256))
        archive, destination = Path(archive).absolute(), Path(destination).absolute()
        with _directory(archive.parent) as source_parent:
            fd = os.open(archive.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=source_parent)
            with os.fdopen(fd, "rb") as source:
                before = os.fstat(source.fileno())
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                     and 0 < before.st_size <= MAX_BYTES)
                need(_hash(source, before.st_size) == expected_sha256)
                source.seek(0)
                with tarfile.open(fileobj=source, mode="r:") as tar:
                    first = tar.next()
                    need(_plain(first) and first.name == "manifest.json" and first.offset == 0
                         and 0 < first.size <= MAX_MANIFEST)
                    manifest = _decode(tar.extractfile(first).read(MAX_MANIFEST + 1))
                    payload_end = _payload_end(first.size, manifest["files"])
                    need(before.st_size == _archive_size(payload_end))
                    source.seek(payload_end)
                    padding = source.read(before.st_size - payload_end + 1)
                    need(len(padding) == before.st_size - payload_end and not any(padding))
                    offset = 512 + ((first.size + 511) // 512) * 512
                    with _directory(destination.parent) as parent:
                        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
                        root = os.open(destination.name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            for name, entry in sorted(manifest["files"].items()):
                                member = tar.next()
                                need(_plain(member) and member.name == "repository/" + name
                                     and member.offset == offset and member.size == entry["size"])
                                offset += 512 + ((entry["size"] + 511) // 512) * 512
                                stream = tar.extractfile(member)
                                with _parent(root, name, create=True) as (leaf_parent, leaf):
                                    outfd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                                    | os.O_NOFOLLOW, 0o600, dir_fd=leaf_parent)
                                    with os.fdopen(outfd, "wb") as result:
                                        digest = hashlib.sha256()
                                        remaining = entry["size"]
                                        while remaining:
                                            block = stream.read(min(1024 * 1024, remaining))
                                            need(bool(block))
                                            result.write(block)
                                            digest.update(block)
                                            remaining -= len(block)
                                        need(not stream.read(1) and digest.hexdigest() == entry["sha256"])
                                        result.flush()
                                        os.fsync(result.fileno())
                                    os.fsync(leaf_parent)
                            need(tar.next() is None)
                            need(set(_scan(root)) == set(manifest["files"]))
                            os.fsync(root)
                        finally:
                            os.close(root)
                        os.fsync(parent)
                need(_identity(os.fstat(source.fileno())) == _identity(before)
                     and _identity(os.stat(archive.name, dir_fd=source_parent,
                                          follow_symlinks=False)) == _identity(before))
        return manifest
    except BundleError:
        raise
    except Exception:
        raise BundleError("repository_bundle_refused") from None
