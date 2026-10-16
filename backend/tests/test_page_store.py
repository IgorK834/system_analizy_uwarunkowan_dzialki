"""Plikowy magazyn stron WFS (AU-106): skróty, token zapytania, atomowość i sprzątanie."""

from __future__ import annotations

import threading
from pathlib import Path

from app.modules.imports.infrastructure.page_store import FilePageStore


def test_saved_page_is_loaded_back_byte_for_byte(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    store.prepare("token-1")

    store.save(0, b"<a/>")
    store.save(10, "<b>zażółć</b>".encode())

    assert store.load(0) == b"<a/>"
    assert store.load(10) == "<b>zażółć</b>".encode()
    assert store.load(20) is None
    assert store.stored_pages() == 2


def test_pages_survive_a_new_process_with_the_same_token(tmp_path: Path) -> None:
    first = FilePageStore(tmp_path / "zones")
    first.prepare("token-1")
    first.save(10, b"<page/>")

    second = FilePageStore(tmp_path / "zones")
    second.prepare("token-1")

    assert second.load(10) == b"<page/>" and second.stored_pages() == 1


def test_a_different_token_means_a_different_service_state_and_drops_the_pages(tmp_path: Path) -> None:
    first = FilePageStore(tmp_path / "zones")
    first.prepare("token-1")
    first.save(10, b"<page/>")

    second = FilePageStore(tmp_path / "zones")
    second.prepare("token-2")

    assert second.load(10) is None and second.stored_pages() == 0
    assert not list((tmp_path / "zones").glob("page-*.xml"))


def test_corrupted_or_truncated_page_is_treated_as_missing(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    store.prepare("token-1")
    store.save(10, b"<page>kompletna</page>")
    path = tmp_path / "zones" / "page-0000000010.xml"

    path.write_bytes(b"<page>kompl")  # obcięty plik (inny rozmiar i skrót)
    assert store.load(10) is None
    path.write_bytes(b"<page>XXXXXXX</page>")  # ten sam rozmiar, inna treść
    assert store.load(10) is None


def test_missing_or_garbled_meta_starts_from_scratch(tmp_path: Path) -> None:
    directory = tmp_path / "zones"
    directory.mkdir()
    (directory / "meta.json").write_text("{nie json", encoding="utf-8")
    (directory / "page-0000000010.xml").write_bytes(b"<stare/>")

    store = FilePageStore(directory)
    store.prepare("token-1")

    assert store.load(10) is None and store.stored_pages() == 0


def test_no_partial_files_are_left_behind(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    store.prepare("token-1")
    for start in range(0, 100, 10):
        store.save(start, b"<p/>" * start)

    assert not list((tmp_path / "zones").glob("*.tmp"))


def test_concurrent_saves_from_worker_threads_are_all_recorded(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    store.prepare("token-1")

    threads = [threading.Thread(target=store.save, args=(start, f"<p>{start}</p>".encode())) for start in range(0, 400, 10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    reopened = FilePageStore(tmp_path / "zones")
    reopened.prepare("token-1")
    assert reopened.stored_pages() == 40
    assert all(reopened.load(start) == f"<p>{start}</p>".encode() for start in range(0, 400, 10))


def test_clear_removes_the_whole_directory(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    store.prepare("token-1")
    store.save(0, b"<p/>")

    store.clear()

    assert not (tmp_path / "zones").exists() and store.stored_pages() == 0
