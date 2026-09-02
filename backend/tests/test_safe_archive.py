"""Współdzielona, bezpieczna ekstrakcja ZIP: Zip Slip, zip bomb i limity."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.shared.safe_archive import (
    ArchiveLimits,
    BadArchiveError,
    UnsafeArchiveError,
    extract_zip,
    read_zip_members,
    safe_member_path,
)


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, content)
    return buffer.getvalue()


def _bomb_zip(name: str, size: int) -> bytes:
    """Buduje ZIP z jednym silnie kompresowalnym wpisem (wysoki ratio)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # Nazwa jako str (nie ZipInfo) wymusza kompresję archiwum; ZipInfo bez
        # jawnego compress_type domyślnie zapisałby wpis nieskompresowany.
        archive.writestr(name, b"\x00" * size)
    return buffer.getvalue()


def test_safe_member_path_accepts_relative_and_normalizes_backslash() -> None:
    assert safe_member_path("plan.gml") == "plan.gml"
    assert safe_member_path("dir\\plan.gml") == "dir/plan.gml"
    assert safe_member_path("./a/./b.gml") == "a/b.gml"


def test_safe_member_path_rejects_traversal_and_absolute() -> None:
    assert safe_member_path("/etc/passwd") is None
    assert safe_member_path("../../etc/passwd") is None
    assert safe_member_path("a/../../b") is None
    assert safe_member_path("") is None


def test_extract_zip_writes_only_allowed_suffixes(tmp_path: Path) -> None:
    content = _zip({"plan.gml": b"<gml/>", "readme.txt": b"hi"})
    extracted = extract_zip(content, tmp_path, allowed_suffixes=(".gml",))
    assert [path.name for path in extracted] == ["plan.gml"]
    assert (tmp_path / "plan.gml").read_bytes() == b"<gml/>"
    assert not (tmp_path / "readme.txt").exists()


def test_extract_zip_rejects_zip_slip(tmp_path: Path) -> None:
    # zipfile odmawia zapisania "../" przez ZipInfo API, więc budujemy wpis ręcznie.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.gml", b"x")
    with pytest.raises(UnsafeArchiveError, match="Zip Slip"):
        extract_zip(buffer.getvalue(), tmp_path)


def test_extract_zip_enforces_entry_count(tmp_path: Path) -> None:
    content = _zip({f"f{i}.gml": b"x" for i in range(5)})
    limits = ArchiveLimits(max_entries=3)
    with pytest.raises(UnsafeArchiveError, match="wpisów"):
        extract_zip(content, tmp_path, limits=limits)


def test_extract_zip_enforces_total_size(tmp_path: Path) -> None:
    content = _zip({"a.gml": b"a" * 100, "b.gml": b"b" * 100})
    limits = ArchiveLimits(max_total_uncompressed_bytes=150)
    with pytest.raises(UnsafeArchiveError, match="przekracza limit"):
        extract_zip(content, tmp_path, limits=limits)


def test_extract_zip_enforces_single_entry_size(tmp_path: Path) -> None:
    content = _zip({"a.gml": b"a" * 500})
    limits = ArchiveLimits(max_entry_uncompressed_bytes=100)
    with pytest.raises(UnsafeArchiveError, match="dekompresji"):
        extract_zip(content, tmp_path, limits=limits)


def test_extract_zip_detects_compression_ratio_bomb(tmp_path: Path) -> None:
    # 2 MB zer kompresuje się do kilkuset bajtów -> bardzo wysoki ratio.
    content = _bomb_zip("bomb.gml", 2 * 1024 * 1024)
    limits = ArchiveLimits(
        max_total_uncompressed_bytes=10 * 1024 * 1024,
        max_entry_uncompressed_bytes=10 * 1024 * 1024,
        max_compression_ratio=50.0,
    )
    with pytest.raises(UnsafeArchiveError, match="współczynnik kompresji"):
        extract_zip(content, tmp_path, limits=limits)


def test_read_zip_members_returns_in_memory_entries() -> None:
    content = _zip({"plan.gml": b"<gml/>", "note.txt": b"x"})
    members = read_zip_members(content, allowed_suffixes=(".gml",))
    assert members == [("plan.gml", b"<gml/>")]


def test_bad_zip_raises_bad_archive_error(tmp_path: Path) -> None:
    with pytest.raises(BadArchiveError):
        extract_zip(b"not a zip", tmp_path)
