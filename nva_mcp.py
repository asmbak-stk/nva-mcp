"""
NVA MCP — en MCP-server for Nasjonalt vitenarkiv (NVA) sitt åpne søke-API.

Wrapper det offentlige søke-API-et til NVA (api.nva.unit.no), som ikke krever
autentisering for publiserte resultater. Skriveoperasjoner (registrering,
filopplasting) er IKKE dekket her — de krever Feide/Cognito-token.

Dokumentasjon:
  - API-base:  https://api.nva.unit.no
  - Swagger:   https://swagger-ui.nva.unit.no/#/
  - Søke-API:  https://github.com/BIBSYSDEV/nva-search-api

Kjør:
  pip install fastmcp httpx     # eller: uv add fastmcp httpx
  fastmcp run nva_mcp.py        # eller: python nva_mcp.py

Legg til i Claude Desktop (claude_desktop_config.json):
  {
    "mcpServers": {
      "nva": {
        "command": "fastmcp",
        "args": ["run", "/full/sti/til/nva_mcp.py"]
      }
    }
  }
"""

from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from pydantic import Field

# Bruk produksjon. Bytt til test ved behov: https://api.test.nva.aws.unit.no
API_BASE = "https://api.nva.unit.no"

mcp = FastMCP(
    name="NVA — Nasjonalt vitenarkiv",
    instructions=(
        "Søk i norsk forskning via Nasjonalt vitenarkiv (NVA). "
        "Bruk search_publications for publikasjoner/resultater. "
        "Bare publiserte, åpne resultater er tilgjengelige (ingen autentisering)."
    ),
)

# Delt HTTP-klient med fornuftige defaults.
_client = httpx.AsyncClient(
    base_url=API_BASE,
    headers={
        "Accept": "application/json",
        "User-Agent": "nva-mcp/0.1 (+https://github.com/BIBSYSDEV/nva-api-documentation)",
    },
    timeout=30.0,
)


def _trim_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Plukk ut de mest nyttige feltene fra en treff-post, så konteksten holdes liten."""
    entity = hit.get("entityDescription", {}) or {}
    pub_date = entity.get("publicationDate", {}) or {}
    contributors = [
        c.get("identity", {}).get("name")
        for c in (entity.get("contributors") or [])
        if c.get("identity", {}).get("name")
    ]
    ref = (entity.get("reference") or {}).get("publicationInstance", {}) or {}
    return {
        "id": hit.get("id"),
        "identifier": hit.get("identifier"),
        "title": entity.get("mainTitle"),
        "type": ref.get("type"),
        "year": pub_date.get("year"),
        "contributors": contributors[:10],
        "abstract": (entity.get("abstract") or "")[:600] or None,
        "handle": hit.get("handle"),
        "doi": hit.get("doi") or (entity.get("reference") or {}).get("doi"),
    }


@mcp.tool
async def search_publications(
    query: Annotated[str, Field(description="Søkeord eller frase, f.eks. 'klimatilpasning kommuner'")],
    category: Annotated[
        str | None,
        Field(description="Filtrer på resultattype, f.eks. 'AcademicArticle', 'AcademicMonograph', 'DegreeMaster'"),
    ] = None,
    year: Annotated[int | None, Field(description="Filtrer på publiseringsår, f.eks. 2024")] = None,
    results: Annotated[int, Field(description="Maks antall treff (1-100)", ge=1, le=100)] = 10,
    offset: Annotated[int, Field(description="Startposisjon, 0-basert (for paginering)", ge=0)] = 0,
    order_by: Annotated[
        str, Field(description="Sorteringsfelt, f.eks. 'modifiedDate', 'relevance', 'publicationDate'")
    ] = "relevance",
    sort_order: Annotated[str, Field(description="'asc' eller 'desc'")] = "desc",
) -> dict[str, Any]:
    """Søk etter publikasjoner og forskningsresultater i NVA.

    Returnerer totalt antall treff og en liste med forenklede poster
    (tittel, type, år, forfattere, sammendrag, DOI).
    """
    params: dict[str, Any] = {
        "query": query,
        "results": results,
        "from": offset,
        "orderBy": order_by,
        "sortOrder": sort_order,
    }
    if category:
        params["category"] = category
    if year:
        params["publicationYearSince"] = year
        params["publicationYearBefore"] = year + 1

    resp = await _client.get("/search/resources", params=params)
    resp.raise_for_status()
    data = resp.json()

    hits = [_trim_hit(h) for h in (data.get("hits") or [])]
    return {
        "total": data.get("totalHits", data.get("total")),
        "took_ms": data.get("took"),
        "returned": len(hits),
        "offset": offset,
        "hits": hits,
    }


@mcp.tool
async def get_publication(
    identifier: Annotated[str, Field(description="Publikasjonens identifier (UUID) fra et søketreff")],
) -> dict[str, Any]:
    """Hent full metadata for én publikasjon basert på dens identifier."""
    resp = await _client.get(f"/publication/{identifier}")
    resp.raise_for_status()
    return resp.json()


@mcp.tool
async def search_raw(
    endpoint: Annotated[
        str, Field(description="Søke-endepunkt, f.eks. 'resources', 'tickets', 'import-candidates'")
    ] = "resources",
    params: Annotated[
        dict[str, Any] | None,
        Field(description="Vilkårlige query-parametre som sendes direkte til API-et"),
    ] = None,
) -> dict[str, Any]:
    """Lavnivå-tilgang: send vilkårlige parametre til /search/{endpoint}.

    Nyttig for avanserte filtre (contributor, institution, fundingSource osv.)
    som ikke er eksponert som egne argumenter. Se nva-search-api for feltnavn.
    """
    resp = await _client.get(f"/search/{endpoint}", params=params or {})
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    import os

    # Render (og lignende tjenester) gir oss et portnummer via PORT-variabelen,
    # og serveren må svare på adressen 0.0.0.0. Vi bruker "http"-transport slik
    # at serveren blir tilgjengelig på en nettadresse (på stien /mcp).
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
    )
