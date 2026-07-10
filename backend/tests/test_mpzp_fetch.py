import io
import socket
import zipfile

import httpx
import pytest
import respx

import app.services.mpzp_fetch as mpzp_fetch
from app.services.mpzp_fetch import (
    DocumentBlob,
    MPZP_MAX_DOCUMENT_SIZE_BYTES,
    MPZP_MAX_REDIRECTS,
    MpzpDocumentFetchError,
    MpzpDocumentNotFoundError,
    MpzpDocumentSecurityError,
    _extract_best_pdf_from_zip,
    _find_pdf_link_in_html,
    _looks_like_html,
    _looks_like_pdf,
    _looks_like_zip,
    _pick_best_pdf_candidate,
    _safe_zip_member_path,
    fetch_mpzp_document,
)

PUBLIC_URL = "https://bip.example.test/dokument"
PDF_BYTES = b"%PDF-1.4\n%mock pdf content for tests\n%%EOF"
HTML_WITH_PDF_LINK = (
    b'<html><body><a href="/pliki/uchwala.pdf">Pobierz uchwale</a></body></html>'
)
HTML_WITHOUT_PDF_LINK = (
    b"<html><body><p>Tresc uchwaly MPZP wprost na stronie...</p></body></html>"
)


class ChunkedAsyncStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def getaddrinfo(hostname: str, port: int | None):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def make_zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.asyncio
@respx.mock
async def test_direct_pdf_is_recognized_as_application_pdf() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=PDF_BYTES
        )
    )

    result = await fetch_mpzp_document(PUBLIC_URL)

    assert isinstance(result, DocumentBlob)
    assert result.media_type == "application/pdf"
    assert result.content == PDF_BYTES
    assert result.filename == "dokument"
    assert result.source_metadata.response_status == 200
    assert result.source_metadata.manual_review_required is False


@pytest.mark.asyncio
@respx.mock
async def test_html_with_pdf_link_is_parsed_and_pdf_fetched() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=HTML_WITH_PDF_LINK,
        )
    )
    pdf_url = "https://bip.example.test/pliki/uchwala.pdf"
    respx.get(pdf_url).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=PDF_BYTES
        )
    )

    result = await fetch_mpzp_document(PUBLIC_URL)

    assert result.media_type == "application/pdf"
    assert result.content == PDF_BYTES
    assert result.filename == "uchwala.pdf"
    assert result.source_metadata.source_url == pdf_url


@pytest.mark.asyncio
@respx.mock
async def test_html_without_pdf_link_returns_html_with_manual_review() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=HTML_WITHOUT_PDF_LINK,
        )
    )

    result = await fetch_mpzp_document(PUBLIC_URL)

    assert result.media_type == "text/html"
    assert result.content == HTML_WITHOUT_PDF_LINK
    assert result.source_metadata.manual_review_required is True
    assert result.source_metadata.confidence == 0.5


@pytest.mark.asyncio
@respx.mock
async def test_html_link_target_masquerading_as_pdf_is_blocked() -> None:
    second_html = b'<html><a href="/third.pdf">PDF</a></html>'
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, content=HTML_WITH_PDF_LINK
        )
    )
    respx.get("https://bip.example.test/pliki/uchwala.pdf").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, content=second_html
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)

    assert len(respx.calls) == 2


@pytest.mark.asyncio
@respx.mock
async def test_zip_with_multiple_pdfs_picks_best_by_keyword_and_size() -> None:
    zip_bytes = make_zip_bytes(
        [
            ("readme.pdf", PDF_BYTES),
            ("uchwala_nr_5.pdf", PDF_BYTES + b" sredni"),
            ("zalacznik_duzy.pdf", PDF_BYTES + b"x" * 100),
        ]
    )
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/zip"}, content=zip_bytes
        )
    )

    result = await fetch_mpzp_document(PUBLIC_URL)

    assert result.filename == "uchwala_nr_5.pdf"
    assert result.media_type == "application/pdf"
    assert result.content.startswith(PDF_BYTES)


def test_zip_blocks_zip_slip_entry() -> None:
    zip_bytes = make_zip_bytes([("../../evil.pdf", PDF_BYTES)])

    with pytest.raises(MpzpDocumentNotFoundError):
        _extract_best_pdf_from_zip(zip_bytes)


def test_safe_zip_member_path_rejects_traversal() -> None:
    assert _safe_zip_member_path("../evil.pdf") is None
    assert _safe_zip_member_path("nested/../../evil.pdf") is None
    assert _safe_zip_member_path("/etc/evil.pdf") is None
    assert _safe_zip_member_path("..\\evil.pdf") is None
    assert _safe_zip_member_path("normal/plan.pdf") == "normal/plan.pdf"


def test_zip_without_any_pdf_raises_not_found() -> None:
    zip_bytes = make_zip_bytes([("readme.txt", b"tekst")])

    with pytest.raises(MpzpDocumentNotFoundError):
        _extract_best_pdf_from_zip(zip_bytes)


@pytest.mark.asyncio
@respx.mock
async def test_zip_bad_zipfile_raises_fetch_error_not_security_error() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=b"PK\x03\x04uszkodzone",
        )
    )

    with pytest.raises(MpzpDocumentFetchError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
async def test_private_ip_host_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 80))
        ],
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document("http://internal.example.test/plan.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "169.254.169.254"])
async def test_local_and_link_local_hosts_are_blocked(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, 80, 0, 0) if family == socket.AF_INET6 else (address, 80)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(family, socket.SOCK_STREAM, 0, "", sockaddr)],
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document("http://blocked.example.test/plan.pdf")


@pytest.mark.asyncio
@respx.mock
async def test_invalid_scheme_is_blocked_before_network_call() -> None:
    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document("ftp://example.test/plan.pdf")

    assert len(respx.calls) == 0


@pytest.mark.asyncio
async def test_url_without_hostname_is_blocked() -> None:
    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document("https:///plan.pdf")


@pytest.mark.asyncio
async def test_dns_resolution_failure_is_security_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolution(host: str, port: int | None):
        raise socket.gaierror("dns failed")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_redirect_to_private_ip_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(host: str, port: int | None):
        address = "10.0.0.5" if host == "internal.example.test" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            302, headers={"location": "http://internal.example.test/secret"}
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)

    assert len(respx.calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_redirect_without_location_is_fetch_error() -> None:
    respx.get(PUBLIC_URL).mock(return_value=httpx.Response(302))

    with pytest.raises(MpzpDocumentFetchError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_too_many_redirects_is_blocked() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(302, headers={"location": PUBLIC_URL})
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)

    assert len(respx.calls) == MPZP_MAX_REDIRECTS + 1


@pytest.mark.asyncio
@respx.mock
async def test_oversized_declared_content_length_is_blocked() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={
                "content-type": "application/pdf",
                "content-length": str(MPZP_MAX_DOCUMENT_SIZE_BYTES + 1),
            },
            content=PDF_BYTES,
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_invalid_content_length_is_blocked() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf", "content-length": "NaN"},
            content=PDF_BYTES,
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_oversized_body_is_blocked_during_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mpzp_fetch, "MPZP_MAX_DOCUMENT_SIZE_BYTES", 10)
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            stream=ChunkedAsyncStream([b"123456", b"789012"]),
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_disallowed_content_type_is_blocked() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-msdownload"},
            content=b"MZ",
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_content_type_pdf_mismatch_is_blocked() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"to nie jest pdf",
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_content_type_zip_mismatch_is_blocked_even_for_pdf_magic() -> None:
    respx.get(PUBLIC_URL).mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/zip"}, content=PDF_BYTES
        )
    )

    with pytest.raises(MpzpDocumentSecurityError):
        await fetch_mpzp_document(PUBLIC_URL)


def test_pick_best_pdf_prefers_keyword_over_pure_size() -> None:
    keyword = zipfile.ZipInfo("uchwala.pdf")
    keyword.file_size = 10
    large = zipfile.ZipInfo("zalacznik.pdf")
    large.file_size = 1000

    name, _ = _pick_best_pdf_candidate(
        [("uchwala.pdf", keyword), ("zalacznik.pdf", large)]
    )

    assert name == "uchwala.pdf"


def test_find_pdf_link_prefers_matching_keywords() -> None:
    html = b"""
    <html><body>
      <a href="/inne.pdf">inne</a>
      <a href="/uchwala.pdf">Pobierz uchwale PDF</a>
    </body></html>
    """

    result = _find_pdf_link_in_html(html, "https://bip.example.test/page")

    assert result == "https://bip.example.test/uchwala.pdf"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_timeout_raises_fetch_error_not_security_error() -> None:
    respx.get(PUBLIC_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(MpzpDocumentFetchError):
        await fetch_mpzp_document(PUBLIC_URL)


@pytest.mark.asyncio
@respx.mock
async def test_http_status_error_raises_fetch_error() -> None:
    respx.get(PUBLIC_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(MpzpDocumentFetchError):
        await fetch_mpzp_document(PUBLIC_URL)


def test_magic_byte_recognition_with_octet_stream() -> None:
    assert _looks_like_pdf(PDF_BYTES, "application/octet-stream", PUBLIC_URL) is True
    zip_bytes = make_zip_bytes([("plan.pdf", PDF_BYTES)])
    assert _looks_like_zip(zip_bytes, "application/octet-stream", PUBLIC_URL) is True
    assert _looks_like_html(b"  <!DOCTYPE html><p>x</p>", "application/octet-stream")
