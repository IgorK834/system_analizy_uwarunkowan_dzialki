"""Daty zestawów z odpowiedzi źródeł; czas serwera/pobrania nie jest datą danych (AU-111)."""
from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import httpx


def dataset_date(value: object, basis: str) -> dict[str, Any]:
    try:
        day = value.date() if isinstance(value, datetime) else value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return {"data_as_of": None, "data_as_of_basis": None,
                "data_as_of_reason": f"Źródło nie podaje poprawnej daty zestawu w polu {basis}."}
    return {"data_as_of": day, "data_as_of_basis": basis, "data_as_of_reason": None}


def response_dataset_date(response: httpx.Response) -> dict[str, Any]:
    # ISO 19139: CI_Date odnosi się do zasobu; dateStamp jest datą metadanych, nie danych.
    if "xml" in response.headers.get("content-type", "").lower() and len(response.content) < 2_000_000:
        try:
            root = ET.fromstring(response.content)
            ns = {"gmd": "http://www.isotc211.org/2005/gmd", "gco": "http://www.isotc211.org/2005/gco"}
            for kind in ("revision", "publication", "creation"):
                dates = []
                for item in root.findall(".//gmd:CI_Date", ns):
                    code = item.find("gmd:dateType/gmd:CI_DateTypeCode", ns)
                    if code is not None and code.get("codeListValue") == kind:
                        value = item.find("gmd:date/gco:Date", ns)
                        if value is not None and value.text:
                            fields = dataset_date(value.text, f"ISO19139:{kind}")
                            if fields["data_as_of"]:
                                dates.append(fields["data_as_of"])
                if dates:
                    return dataset_date(min(dates), f"ISO19139:{kind}")
        except ET.ParseError:
            pass
    modified = response.headers.get("last-modified")
    if modified:
        try:
            return dataset_date(parsedate_to_datetime(modified), "HTTP Last-Modified")
        except (ValueError, TypeError, OverflowError):
            return dataset_date(None, "HTTP Last-Modified")
    return {"data_as_of": None, "data_as_of_basis": None,
            "data_as_of_reason": "Odpowiedź źródła nie zawiera daty zestawu ani nagłówka Last-Modified."}


def aggregate_dataset_dates(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    dates = list(items)
    if not dates or any(item.get("data_as_of") is None for item in dates):
        return {"data_as_of": None, "data_as_of_basis": None,
                "data_as_of_reason": "Nie wszystkie warstwy/akty źródła podają datę zestawu; wspólny stan na jest nieznany."}
    return dataset_date(min(item["data_as_of"] for item in dates),
                        "Najstarsza data składowa: " + "; ".join(sorted({item["data_as_of_basis"] for item in dates})))
