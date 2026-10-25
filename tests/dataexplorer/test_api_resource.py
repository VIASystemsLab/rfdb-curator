"""Tests for content-negotiated dereference: GET /rdf/data/{id} and /rdf/schema/{name}.

This is the Linked Data surface — the reason RFDB's persisted IRIs are worth
calling identifiers. An entity IRI is ``https://rosfeatr.eu/rdf/data/{name}`` and
production serves that host, so visiting the identifier has to return the
resource. Three properties therefore matter more than the response bodies:

  * **the negotiated variant is honest** — the ``Content-Type`` matches the payload,
    and ``Vary: Accept`` is set so a cache cannot hand one client another's format;
  * **the path shape is stable** — ``/rdf/`` is unversioned and its URLs are stored
    inside triples, so a route that quietly stops matching is a data-integrity bug,
    not a routing bug;
  * **every URL this endpoint hands out can be fetched** — the alternates it
    advertises, the ``Content-Location`` it names, the links on the HTML page.
    Several tests follow those URLs rather than matching them as strings, because
    a URL can be right as a string and still broken: a raw ``+`` in
    ``?_mediatype=application/ld+json`` decodes to a space.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import rdflib
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rdflib import Graph

from api.resource import router
from core.config import settings
from rfdb_core.schema_extractor import SchemaExtractor

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "rfdb_schema.ttl"


RFDB_BASE = settings.data_base_uri
LOCAL = "Src1"
IRI = f"{RFDB_BASE}{LOCAL}"

# One outbound and one inbound statement, so "both directions" is observable.
GRAPH_TURTLE = f"""
@prefix schema: <http://schema.org/> .
<{IRI}> schema:name "A source" .
<{RFDB_BASE}Parent> schema:isPartOf <{IRI}> .
"""

# Everything the HTML page has to render, including the two cases that must not
# reach an attribute unaltered: a literal carrying markup, and a non-http IRI.
# Two labels because this is a bilingual dataset and only one can be the heading.
RICH_TURTLE = f"""
@prefix schema: <http://schema.org/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix lrmoo: <http://iflastandards.info/ns/lrm/lrmoo/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<{IRI}> a lrmoo:F1_Work ;
    rdfs:label "A source"@en, "Источник"@ru ;
    schema:name "<script>alert(1)</script>" ;
    schema:numberOfPages "12"^^xsd:integer ;
    owl:sameAs <http://www.wikidata.org/entity/Q42> ;
    schema:mainEntityOfPage <javascript:alert(1)> ;
    schema:contentUrl "{RFDB_BASE}{LOCAL}/content"^^xsd:anyURI ;
    lrmoo:R3_is_realised_in <{RFDB_BASE}Expr1> .
<{RFDB_BASE}Parent> schema:isPartOf <{IRI}> .
"""


class _StubStore:
    """Returns a canned CONSTRUCT graph, or raises to simulate an outage."""

    def __init__(self, turtle: str = GRAPH_TURTLE, raise_exc: Exception | None = None):
        self._turtle = turtle
        self._raise_exc = raise_exc
        self.queries: list[str] = []

    def from_clause(self) -> str:
        return ""

    def construct(self, sparql: str) -> Graph:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.queries.append(sparql)
        graph = Graph()
        if self._turtle.strip():
            graph.parse(data=self._turtle, format="turtle")
        return graph


class _ExplodingStore(_StubStore):
    """Fails the test if the store is touched at all."""

    def construct(self, sparql: str) -> Graph:
        raise AssertionError("store was queried before the id was validated")


def _client(store=None, content_route: bool = False) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/rdf")

    if content_route:
        # api/files.py owns this route in the real app. It is registered here only
        # for the "every link on the page resolves" crawl, because the page links
        # across that router boundary via schema:contentUrl. Off by default so
        # test_content_subpath_is_not_swallowed_by_the_dereference_route keeps
        # proving what it says: with no such route, a 200 could only mean the
        # dereference route matched greedily.
        @app.get("/rdf/data/{file_id}/content")
        def _content_stub(file_id: str) -> dict[str, str]:
            return {"stub": file_id}

    app.state.store = store if store is not None else _StubStore()
    app.state.schema_extractor = SchemaExtractor(str(SCHEMA_PATH))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Negotiation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("accept", "expected_type", "rdflib_format"),
    [
        ("text/turtle", "text/turtle", "turtle"),
        ("application/ld+json", "application/ld+json", "json-ld"),
        ("application/rdf+xml", "application/rdf+xml", "xml"),
        ("application/n-triples", "application/n-triples", "nt"),
    ],
)
def test_each_media_type_round_trips(accept, expected_type, rdflib_format) -> None:
    """The body must actually parse as what the Content-Type claims."""
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": accept})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith(expected_type)

    graph = Graph().parse(data=res.text, format=rdflib_format)
    assert (rdflib.URIRef(IRI), None, rdflib.Literal("A source")) in graph


def test_both_directions_are_described() -> None:
    """Inbound statements are included, or the graph is unwalkable from an IRI.

    Dereferencing exists so a client can follow its nose from one identifier to the
    next; a description with only outbound triples is a dead end.
    """
    body = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": "application/n-triples"}).text
    assert IRI in body
    assert f"{RFDB_BASE}Parent" in body


def test_star_accept_falls_back_to_turtle() -> None:
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": "*/*"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/turtle")


def test_no_accept_header_still_answers() -> None:
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": ""})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/turtle")


def test_unsatisfiable_accept_answers_rather_than_refusing() -> None:
    """A type we cannot serve at all falls back, rather than 406ing.

    Refusing a request we can satisfy in *some* representation would be worse than
    answering it in another — a 406 on a persisted identifier makes the identifier
    look broken.
    """
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": "text/csv"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/turtle")


def test_q_values_decide_between_supported_types() -> None:
    """Ranking must be honoured — the lowest-ranked supported type must not win."""
    res = _client().get(
        f"/rdf/data/{LOCAL}",
        headers={"Accept": "text/turtle;q=0.2, application/ld+json;q=0.9"},
    )
    assert res.headers["content-type"].startswith("application/ld+json")


def test_mediatype_parameter_beats_the_accept_header() -> None:
    res = _client().get(
        f"/rdf/data/{LOCAL}",
        params={"_mediatype": "application/ld+json"},
        headers={"Accept": "text/turtle"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/ld+json")


def test_unsupported_mediatype_parameter_is_a_400() -> None:
    """Explicit beats implicit: a named format we cannot serve is an error.

    Unlike an ``Accept`` header, which expresses a preference, ``_mediatype`` is a
    demand — silently substituting something else would be a lie.
    """
    res = _client().get(f"/rdf/data/{LOCAL}", params={"_mediatype": "text/csv"})
    assert res.status_code == 400
    assert "text/csv" in res.json()["detail"]


def test_vary_accept_is_set() -> None:
    """Without it a shared cache may serve one client the variant it stored for another."""
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": "text/turtle"})
    assert res.headers["vary"] == "Accept"


@pytest.mark.parametrize(
    "media_type",
    ["text/turtle", "application/ld+json", "application/rdf+xml", "application/n-triples"],
)
def test_content_location_names_a_variant_that_can_be_fetched(media_type: str) -> None:
    """The named variant URL must *work*, not merely be present.

    Asserting the property instead of the string is deliberate: a header that
    interpolates the media type raw advertises ``?_mediatype=application/ld+json``,
    which answers 400 because a query string is form-encoded and the ``+`` arrives
    as a space. A test comparing strings would pass on that broken URL.
    """
    client = _client()
    location = client.get(f"/rdf/data/{LOCAL}", headers={"Accept": media_type}).headers[
        "content-location"
    ]
    assert location.startswith(f"{IRI}?_mediatype=")

    followed = client.get(location.replace(RFDB_BASE, "/rdf/data/"))
    assert followed.status_code == 200
    assert followed.headers["content-type"].startswith(media_type)


def test_a_hand_typed_plus_is_understood() -> None:
    """``?_mediatype=application/ld+json`` is what a human writes, so it must work.

    The URLs this service emits percent-encode the ``+``; nobody typing curl by
    hand will. Undoing the form-encoding is information-preserving here because no
    media type contains a space — unlike substituting a different type, which the
    400 below still refuses to do.
    """
    res = _client().get(f"/rdf/data/{LOCAL}?_mediatype=application/ld+json")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/ld+json")


# ---------------------------------------------------------------------------
# The HTML representation
# ---------------------------------------------------------------------------

# What a browser actually sends. Written out rather than simplified to
# "text/html" because the q-values are the interesting part: the browser also
# accepts */*, and a negotiator that took the first token or ignored ranking
# would answer this with Turtle.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"


def _html(store=None) -> str:
    return (
        _client(store or _StubStore(turtle=RICH_TURTLE))
        .get(f"/rdf/data/{LOCAL}", headers={"Accept": BROWSER_ACCEPT})
        .text
    )


def test_a_browser_gets_html() -> None:
    res = _client().get(f"/rdf/data/{LOCAL}", headers={"Accept": BROWSER_ACCEPT})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.text.startswith("<!DOCTYPE html>")
    assert res.headers["vary"] == "Accept"


def test_html_shows_the_same_field_set_the_graph_endpoint_returns() -> None:
    """label, types, literals, outbound links, inbound links — all five present.

    The fields are ``GET /api/v1/dataexplorer/graph/node``'s, assembled from the
    graph this route already fetched rather than by calling that endpoint.
    """
    page = _html()
    assert "A source" in page  # label → heading
    assert "lrmoo:F1_Work" in page  # rdf:type
    assert "12" in page  # a literal
    assert 'href="/rdf/data/Expr1"' in page  # outbound relation
    assert 'href="/rdf/data/Parent"' in page  # inbound reference
    assert "wd:Q42" in page  # external authority link


def test_html_keeps_a_label_that_did_not_win_the_heading() -> None:
    """A bilingual record must not lose its Russian label to the English one.

    Only one label can be the ``<h1>``; the rest stay in the property table. The
    JSON endpoint returns one preferred label and can get away with it because its
    client fetches every triple separately — a *representation of the resource*
    cannot silently drop a statement.
    """
    page = _html()
    assert "A source" in page
    assert "Источник" in page


def test_html_escapes_hostile_literal_values() -> None:
    """Values are curator-authored, so every one of them is untrusted input."""
    page = _html()
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_html_links_local_resources_by_path_not_by_persisted_host() -> None:
    """A persisted IRI names rosfeatr.eu; a dev reader answers on localhost.

    Linking the IRI as-is would send a developer's click to the public internet —
    the same re-basing the frontend does for ``schema:contentUrl``. The links must
    therefore be path-only, and the path comes from the namespace itself.
    """
    page = _html()
    assert 'href="/rdf/data/Expr1"' in page
    assert f'href="{RFDB_BASE}' not in page


def test_html_does_not_linkify_a_non_http_object() -> None:
    """An object in the store is curator-supplied: ``javascript:`` must never be an href."""
    page = _html()
    assert "javascript:" in page, "the value is still shown, just not as a link"
    assert 'href="javascript:' not in page


def test_html_links_a_digital_copy_to_its_bytes() -> None:
    """``schema:contentUrl`` is an ``xsd:anyURI`` literal, and must still be clickable.

    It is the whole point of a digital copy's page, and the schema requires a
    literal rather than an IRI (``sh:datatype xsd:anyURI`` admits nothing else), so
    the rendering has to notice the datatype. Re-based like every other local link,
    so a dev page reaches that stack's bytes and not production's.
    """
    page = _html()
    assert f'href="/rdf/data/{LOCAL}/content"' in page


def test_every_relative_link_on_the_html_page_resolves() -> None:
    """No dead link on the page.

    Covers the variant links too: a raw ``+`` in ``?_mediatype=application/ld+json``
    decodes to a space, so an unencoded variant link answers 400.
    """
    client = _client(_StubStore(turtle=RICH_TURTLE), content_route=True)
    page = client.get(f"/rdf/data/{LOCAL}", headers={"Accept": BROWSER_ACCEPT}).text
    targets = sorted(set(re.findall(r'href="((?:\?|/rdf/)[^"]+)"', page)))

    assert len(targets) >= 7, f"expected the variants plus the neighbours, got {targets}"
    for target in targets:
        url = f"/rdf/data/{LOCAL}{target}" if target.startswith("?") else target
        assert client.get(url).status_code == 200, f"dead link on the page: {target}"


def test_html_head_advertises_every_rdf_variant() -> None:
    """``<link rel=alternate>`` is the HTML-native twin of the Link header."""
    page = _html()
    for media_type in ("text/turtle", "application/ld+json", "application/rdf+xml"):
        assert f'type="{media_type}"' in page


def test_html_carries_no_external_asset() -> None:
    """A page in the data space renders from the request that fetched the data.

    No CDN in the trust path of a public Linked Data view, and no page that breaks
    when it is fetched by something without internet access.
    """
    page = _html()
    assert "//cdn" not in page and "<script" not in page


# ---------------------------------------------------------------------------
# ConnegP — the alternates view
# ---------------------------------------------------------------------------

DCTERMS_HAS_FORMAT = rdflib.URIRef("http://purl.org/dc/terms/hasFormat")
DCTERMS_FORMAT = rdflib.URIRef("http://purl.org/dc/terms/format")


def test_alt_profile_lists_every_representation() -> None:
    """``?_profile=alt`` answers with the resource's available representations."""
    res = _client().get(f"/rdf/data/{LOCAL}", params={"_profile": "alt"})

    assert res.status_code == 200
    graph = Graph().parse(data=res.text, format="turtle")
    formats = {str(o) for _, _, o in graph.triples((None, DCTERMS_FORMAT, None))}
    assert formats == {
        "text/turtle",
        "application/ld+json",
        "application/rdf+xml",
        "application/n-triples",
        "text/html",
    }
    assert len(list(graph.triples((rdflib.URIRef(IRI), DCTERMS_HAS_FORMAT, None)))) == 5


def test_every_variant_the_alt_view_names_can_be_fetched() -> None:
    """A listing of representations that cannot be retrieved is worse than none."""
    client = _client()
    body = client.get(f"/rdf/data/{LOCAL}", params={"_profile": "alt"}).text
    graph = Graph().parse(data=body, format="turtle")

    variants = [str(o) for _, _, o in graph.triples((None, DCTERMS_HAS_FORMAT, None))]
    assert variants
    for variant in variants:
        res = client.get(variant.replace(RFDB_BASE, "/rdf/data/"))
        assert res.status_code == 200, f"unfetchable variant: {variant}"


def test_alt_profile_is_itself_negotiable() -> None:
    """The alternates view is a representation like any other, HTML included."""
    client = _client()
    for accept, expected in (
        ("application/n-triples", "application/n-triples"),
        ("text/html", "text/html"),
    ):
        res = client.get(
            f"/rdf/data/{LOCAL}", params={"_profile": "alt"}, headers={"Accept": accept}
        )
        assert res.status_code == 200
        assert res.headers["content-type"].startswith(expected)


def test_the_default_profile_can_be_named_explicitly() -> None:
    """``?_profile=rfdb`` is the plain description — a ConnegP client may ask for it."""
    res = _client().get(f"/rdf/data/{LOCAL}", params={"_profile": "rfdb"})
    assert res.status_code == 200
    assert "A source" in res.text


def test_unsupported_profile_is_a_400() -> None:
    """Same rule as ``_mediatype``: a named thing we cannot serve is an error."""
    res = _client().get(f"/rdf/data/{LOCAL}", params={"_profile": "dcat"})
    assert res.status_code == 400
    assert "dcat" in res.json()["detail"]


def test_alt_profile_for_an_unknown_resource_is_404() -> None:
    """A resource that does not exist has no representations to list.

    One 404 condition per identifier, whatever profile or format is asked for —
    otherwise existence in the data space depends on how you ask.
    """
    res = _client(_StubStore(turtle="")).get("/rdf/data/Nope", params={"_profile": "alt"})
    assert res.status_code == 404


def test_link_header_names_the_canonical_resource_and_every_alternate() -> None:
    """Discovery without ConnegP-specific knowledge: plain RFC 8288 relations."""
    header = _client().get(f"/rdf/data/{LOCAL}").headers["link"]
    assert f'<{IRI}>; rel="canonical"' in header
    assert header.count('rel="alternate"') == 5


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["a b", "a<b", 'a"b', "a{b", "a\\b", "a|b"])
def test_malformed_local_name_is_refused_before_the_store(bad: str) -> None:
    """The id is interpolated into SPARQL, so it is validated first, not queried first."""
    res = _client(_ExplodingStore()).get(f"/rdf/data/{bad}")
    assert res.status_code == 404


def test_unknown_resource_is_404() -> None:
    res = _client(_StubStore(turtle="")).get("/rdf/data/Nope")
    assert res.status_code == 404


def test_store_outage_is_503_not_404() -> None:
    """ "Cannot answer" must not be reported as "does not exist".

    Conflating them would make an outage look like deletion to any client
    harvesting the data space.
    """
    res = _client(_StubStore(raise_exc=RuntimeError("store down"))).get(f"/rdf/data/{LOCAL}")
    assert res.status_code == 503


def test_content_subpath_is_not_swallowed_by_the_dereference_route() -> None:
    """``/rdf/data/{id}`` must not match ``/rdf/data/{id}/content``.

    The content route lives in another module, so if this one matched greedily the
    bytes URL — the one stored in every ``schema:contentUrl`` — would start
    returning Turtle. Pinned because the failure would be silent and in *persisted*
    data.
    """
    res = _client().get(f"/rdf/data/{LOCAL}/content")
    assert res.status_code == 404, "the dereference route matched a sub-resource path"


# ---------------------------------------------------------------------------
# Shape dereference
# ---------------------------------------------------------------------------


def test_known_shape_dereferences_with_its_property_nodes() -> None:
    """A shape without its property nodes describes nothing — they are blank nodes."""
    res = _client().get("/rdf/schema/SourceShape", headers={"Accept": "text/turtle"})

    assert res.status_code == 200
    graph = Graph().parse(data=res.text, format="turtle")
    assert len(graph) > 1, "expected the shape plus its property descriptors"
    assert any("SourceShape" in str(s) for s in graph.subjects())


def test_unknown_shape_is_404() -> None:
    res = _client().get("/rdf/schema/NoSuchShape")
    assert res.status_code == 404


def test_shape_route_does_not_touch_the_triplestore() -> None:
    """Shapes come from the parsed schema file, which is not in the data graph."""
    res = _client(_ExplodingStore()).get("/rdf/schema/SourceShape")
    assert res.status_code == 200


def test_shapes_serve_rdf_only_and_say_so() -> None:
    """No HTML here, on purpose — and the refusal must be legible, not a 500.

    A shape's description is mostly blank nodes (a shape *is* its property
    descriptors), which the flat subject-centric page would silently drop. So a
    browser gets Turtle, and an explicit ``_mediatype=text/html`` gets a 400 naming
    what this route does serve.
    """
    client = _client()
    assert (
        client.get("/rdf/schema/SourceShape", headers={"Accept": "text/html"})
        .headers["content-type"]
        .startswith("text/turtle")
    )

    refused = client.get("/rdf/schema/SourceShape", params={"_mediatype": "text/html"})
    assert refused.status_code == 400
    assert "text/turtle" in refused.json()["detail"]


def test_shape_alternates_list_only_what_shapes_serve() -> None:
    """The alt view must describe *this* route's representations, not the data route's."""
    res = _client().get("/rdf/schema/SourceShape", params={"_profile": "alt"})

    assert res.status_code == 200
    graph = Graph().parse(data=res.text, format="turtle")
    formats = {str(o) for _, _, o in graph.triples((None, DCTERMS_FORMAT, None))}
    assert formats == {
        "text/turtle",
        "application/ld+json",
        "application/rdf+xml",
        "application/n-triples",
    }, "the shape route offers no HTML, so its alternates must not advertise it"


def test_html_links_an_identifier_stored_as_text() -> None:
    """A VIAF ID literal links to its VIAF page, per EXTERNAL_ID_LINKS."""
    turtle = f"""
    <{IRI}> <http://www.wikidata.org/prop/direct/P214> "293925180" .
    """
    page = _html(_StubStore(turtle=turtle))
    assert '<a href="https://viaf.org/en/viaf/293925180">293925180</a>' in page


def test_html_title_names_the_configured_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page title ends with PROJECT_NAME, not a fixed project name."""
    monkeypatch.setattr(settings, "project_name", "MovieDB")
    assert "<title>A source — MovieDB</title>" in _html()
