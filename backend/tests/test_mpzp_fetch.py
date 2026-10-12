import io
import socket
import zipfile
from pathlib import Path

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
    MPZP_MAX_HTML_HOPS,
    _extract_best_pdf_from_zip,
    _find_document_link_in_html,
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


# --- strony BIP: dokument -> karta załącznika -> plik (AU-102) ----------------------------------------

BIP_FIXTURES = Path(__file__).parent / "fixtures" / "mpzp_fetch" / "krakow_bip"
BIP_LANDING_URL = "https://www.bip.krakow.pl/?dok_id=137657"
BIP_TEXT_CARD_URL = "https://www.bip.krakow.pl/zalaczniki/dokumenty/n/298025/karta"
BIP_TEXT_FILE_URL = "https://www.bip.krakow.pl/plik.php?zid=298025&wer=0&new=t&mode=shw"
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}


def bip_page(name: str) -> bytes:
    """Zamrożona strona BIP Krakowa (wszystkie linki ``<a>`` realnej strony, bez reszty szablonu)."""
    return (BIP_FIXTURES / name).read_bytes()


def test_the_text_link_is_chosen_over_every_other_attachment_of_the_real_landing_page() -> None:
    landing = bip_page("landing_kleparz.html")
    assert _find_document_link_in_html(landing, BIP_LANDING_URL) == BIP_TEXT_CARD_URL
    # strona ma też rysunek planu, GML, prognozę i ekofizjografię — żadna z nich nie jest tekstem uchwały
    assert b"/n/298026/karta" in landing and b"/n/636649/karta" in landing


def test_the_card_of_the_text_leads_to_its_single_file() -> None:
    assert _find_document_link_in_html(bip_page("card_kleparz_text.html"), BIP_TEXT_CARD_URL) == BIP_TEXT_FILE_URL


@pytest.mark.asyncio
@respx.mock
async def test_a_bip_landing_page_is_followed_through_the_text_card_to_the_pdf() -> None:
    landing = respx.get(BIP_LANDING_URL).mock(
        return_value=httpx.Response(200, headers=HTML_HEADERS, content=bip_page("landing_kleparz.html"))
    )
    card = respx.get(BIP_TEXT_CARD_URL).mock(
        return_value=httpx.Response(200, headers=HTML_HEADERS, content=bip_page("card_kleparz_text.html"))
    )
    plik = respx.get(BIP_TEXT_FILE_URL).mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"}, content=PDF_BYTES)
    )
    attachment = respx.get(url__regex=r"https://www\.bip\.krakow\.pl/zalaczniki/dokumenty/n/(?!298025/).*").mock(
        return_value=httpx.Response(200, headers=HTML_HEADERS, content=b"<html></html>")
    )

    result = await fetch_mpzp_document(BIP_LANDING_URL)

    assert result.media_type == "application/pdf" and result.content == PDF_BYTES
    assert result.source_metadata.source_url == BIP_TEXT_FILE_URL
    assert result.source_metadata.manual_review_required is False
    assert (landing.call_count, card.call_count, plik.call_count) == (1, 1, 1)
    assert attachment.call_count == 0  # rysunek planu i pozostałe załączniki nie są pobierane


@pytest.mark.asyncio
@respx.mock
async def test_following_stops_after_the_hop_limit_and_returns_the_page() -> None:
    page = b'<html><body><a href="/dokument?n=NEXT">Tekst planu (uchwala nr 1/2020)</a></body></html>'
    for index in range(MPZP_MAX_HTML_HOPS + 3):
        respx.get(f"https://bip.example.test/dokument?n={index}").mock(
            return_value=httpx.Response(200, headers=HTML_HEADERS, content=page.replace(b"NEXT", str(index + 1).encode()))
        )

    result = await fetch_mpzp_document("https://bip.example.test/dokument?n=0")

    assert result.media_type == "text/html" and result.source_metadata.manual_review_required is True
    assert len(respx.calls) == MPZP_MAX_HTML_HOPS + 1
    assert str(respx.calls.last.request.url).endswith(f"n={MPZP_MAX_HTML_HOPS}")


def test_links_to_another_site_are_not_followed_from_a_page_without_pdf() -> None:
    html = b'<html><a href="https://obcy.example.test/plik.php?zid=1">Tekst planu</a><a href="https://obcy.example.test/x/karta">Tekst planu</a></html>'
    assert _find_document_link_in_html(html, "https://bip.example.test/dokument") is None


def test_several_download_links_need_a_unique_text_of_the_resolution() -> None:
    base = "https://bip.example.test/dokument"
    ambiguous = b'<a href="/plik.php?zid=1">zobacz</a><a href="/plik.php?zid=2">zobacz</a>'
    assert _find_document_link_in_html(ambiguous, base) is None
    labelled = (
        b'<a href="/plik.php?zid=1">Rysunek planu - zalacznik nr 1</a>'
        b'<a href="/plik.php?zid=2">Tekst planu (uchwala nr 5/2020)</a>'
        b'<a href="/plik.php?zid=3">Prognoza oddzialywania na srodowisko</a>'
    )
    assert _find_document_link_in_html(labelled, base) == "https://bip.example.test/plik.php?zid=2"
    only_attachments = b'<a href="/pobierz?id=1">Rysunek planu</a><a href="/pobierz?id=2">Zalacznik nr 2</a>'
    assert _find_document_link_in_html(only_attachments, base) is None


@pytest.mark.asyncio
@respx.mock
async def test_a_card_whose_file_redirects_to_a_private_address_is_blocked() -> None:
    respx.get(BIP_LANDING_URL).mock(
        return_value=httpx.Response(200, headers=HTML_HEADERS, content=bip_page("landing_kleparz.html"))
    )
    respx.get(BIP_TEXT_CARD_URL).mock(
        return_value=httpx.Response(200, headers=HTML_HEADERS, content=bip_page("card_kleparz_text.html"))
    )
    respx.get(BIP_TEXT_FILE_URL).mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/plik.pdf"})
    )
    original = socket.getaddrinfo

    def getaddrinfo(hostname: str, port: int | None):
        if hostname == "127.0.0.1":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0))]
        return original(hostname, port)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        with pytest.raises(MpzpDocumentSecurityError):
            await fetch_mpzp_document(BIP_LANDING_URL)
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]
