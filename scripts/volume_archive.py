#!/usr/bin/env python3
"""Stream the application file volume; reject links and unsafe restore paths."""

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile


def validate_member(member):
    path = PurePosixPath(member.name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or not (member.isfile() or member.isdir())
    ):
        raise ValueError("Archive contains an unsafe path or unsupported file type: " + member.name)


def validate(archive):
    seen = set()
    files = set()
    with tarfile.open(archive, "r:*") as source:
        for member in source:
            validate_member(member)
            path = PurePosixPath(member.name)
            if path in seen or any(parent in files for parent in path.parents):
                raise ValueError("Archive contains duplicate or conflicting paths.")
            seen.add(path)
            if member.isfile():
                if any(path in other.parents for other in seen):
                    raise ValueError("Archive file conflicts with a directory.")
                files.add(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["pack", "unpack", "validate", "empty"])
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/app/data"))
    parser.add_argument("--archive")
    args = parser.parse_args()
    root = Path(args.data_dir)
    if args.operation == "validate":
        validate(args.archive)
        return
    if args.operation == "empty":
        if root.exists() and any(p.is_file() or p.is_symlink() for p in root.rglob("*")):
            raise SystemExit("Refusing restore: application volume already contains files.")
        return
    if args.operation == "pack":
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise SystemExit(
                        "Application storage contains an unsupported link or special file."
                    )
                archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
        return
    root.mkdir(parents=True, exist_ok=True)
    if any(p.is_file() or p.is_symlink() for p in root.rglob("*")):
        raise SystemExit("Refusing restore: application volume already contains files.")
    # Keep extraction private until every archive entry has passed validation.
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=root))
    try:
        source_file = open(args.archive, "rb") if args.archive else sys.stdin.buffer
        try:
            with tarfile.open(fileobj=source_file, mode="r|*") as archive:
                for member in archive:
                    validate_member(member)
                    destination = staging.joinpath(*PurePosixPath(member.name).parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            archive.extractfile(member) as source,
                            destination.open("xb") as target,
                        ):
                            shutil.copyfileobj(source, target)
                        destination.chmod(0o600)
        finally:
            if args.archive:
                source_file.close()
        # Named volume creation may contain the empty documents directory.
        for child in root.iterdir():
            if child != staging and child.is_dir():
                shutil.rmtree(child)
        for child in staging.iterdir():
            child.rename(root / child.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
