from typing import Annotated, Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://app:app@db:5432/dzialki"
    backend_cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://frontend:3000",
    ]
    app_title: str = "System analizy działki"
    app_version: str = "1.0.0"
    # Domyślne techniczne odsunięcie od granicy działki w metrach. To NIE jest linia
    # zabudowy z MPZP — to przybliżenie wynikające z przepisów o minimalnej odległości
    # od granicy, które może zostać nadpisane przez konkretne ustalenia planistyczne.
    default_technical_setback_m: float = 4.0
    # Adres WFS KIUT/GESUT jest placeholderem opartym o publicznie znaną domenę GUGiK.
    # Rzeczywisty kontrakt zapytania (typename, wersja WFS, przestrzenie nazw) zostanie
    # doprecyzowany, gdy będzie dostępna pełna dokumentacja usługi.
    kiut_wfs_base_url: str = (
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/KIUT/WFS/GESUT"
    )
    # Adres WFS ISOK (mapy zagrożenia i ryzyka powodziowego) jest placeholderem
    # opartym o publicznie znaną domenę ISOK/GUGiK. Rzeczywisty kontrakt zapytania
    # (typename, wersja WFS, nazwy warstw dla Q1%/Q10%/Q0,2%) zostanie doprecyzowany,
    # gdy będzie dostępna pełna dokumentacja usługi.
    isok_wfs_base_url: str = "https://wms.isok.gov.pl/isap/services/PZGIK/ISOK/WFS"
    # WMS jest wyłącznie referencyjnym linkiem do ręcznej weryfikacji wizualnej.
    # Analiza rastra WMS NIE jest zaimplementowana — fetch_flood_risks korzysta
    # tylko z WFS. Zobacz docstring fetch_flood_risks w app/services/isok.py.
    isok_wms_fallback_url: str = "https://wms.isok.gov.pl/isap/services/PZGIK/ISOK/WMS"
    # Adres WFS GDOŚ (formy ochrony przyrody) jest placeholderem opartym o publicznie
    # znaną domenę Generalnej Dyrekcji Ochrony Środowiska. Rzeczywisty kontrakt
    # zapytania (typename, wersja WFS, nazwy warstw) zostanie doprecyzowany po
    # udostępnieniu pełnej dokumentacji usługi.
    gdos_wfs_base_url: str = "https://sdi.gdos.gov.pl/wfs"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_backend_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            # W .env.example trzymamy prosty zapis tekstowy, żeby konfiguracja Compose
            # była czytelna także bez znajomości składni JSON dla list.
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
