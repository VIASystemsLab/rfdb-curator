"""Content-negotiated dereference — the Linked Data surface of the ``/rdf/`` space.

This is what makes RFDB's persisted IRIs *cool*: an entity's IRI is
``https://rosfeatr.eu/rdf/data/{local_name}``, production serves that host, so
visiting the identifier returns the resource (D9). No ``?iri=`` parameter, which an
earlier sketch of this endpoint had — once the IRI *is* the URL, there is nothing
to pass.

Two things follow from being a data space rather than an application surface (D8):

  * **Unversioned.** Versioning an identifier that is persisted inside triples is a
    contradiction; the ``/api/v1/…`` routes carry the version instead.
  * **Local names, not encoded IRIs.** The path segment is the local name, and the
    full IRI is rebuilt from the ``DATA_BASE_URI`` setting. The editor-facing
    ``/api/v1/dataexplorer/entities/get?id=`` still takes a URL-encoded absolute
    IRI because it answers a different question (an editor's bespoke triple list,
    not a representation), but a client that has an RFDB IRI in hand never needs
    to encode anything here.

Five representations, none of them costing a dependency: four are rdflib
serializations, and the HTML one is an f-string. Deliberately *not* a template
engine — ``jinja2`` does not resolve transitively here (Starlette's
``Jinja2Templates`` is an optional extra), so one page would have meant adding a
dependency, and this page has no sibling to share a layout with.

So a browser gets HTML on ``/rdf/data/{id}``. ``/rdf/schema/{name}`` stays
RDF-only: a shape's description is mostly blank nodes — a shape *is* its property
descriptors — which a flat subject-centric page would silently drop, and the shape
catalogue already has a rendered form in the editor, fed by
``/api/v1/dataexplorer/shapes``. An ``Accept`` we cannot satisfy at all still
falls back to Turtle rather than 406ing: answering in some representation beats
making a persisted identifier look broken.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS, XSD

from api.graph import preferred_label
from core.config import settings
from rfdb_core.external_ids import external_id_url

router = APIRouter()

# Local names are minted by this project — ``rfdb:PascalCase`` entities and
# ``File_xxxxxxxx`` copies — so this is a whitelist, not the blocklist
# ``rfdb_core.iri.iri_error`` applies to client-supplied absolute IRIs. Here we
# know exactly what a legal id looks like, and the id is interpolated into SPARQL.
LOCAL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# rdflib serializer keyed by media type. All four ship with rdflib.
MEDIA_TYPES: dict[str, str] = {
    "text/turtle": "turtle",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
    "application/n-triples": "nt",
}

# The human representation. Not in MEDIA_TYPES because it is not an rdflib
# format; it is the same description rendered for a reader instead of a parser.
HTML_MEDIA_TYPE = "text/html"

# What ``*/*`` — or an unsatisfiable preference — resolves to. Turtle because this
# is a data space: the default should be machine-readable and human-legible, and
# it is the most compact of the four. A browser does not land here, because it
# ranks ``text/html`` above its own ``*/*``.
DEFAULT_MEDIA_TYPE = "text/turtle"

# ConnegP profile tokens, honoured on every ``/rdf/`` route:
#   ``rfdb`` — the default view: every triple that mentions the resource.
#   ``alt``  — the alternates listing (which representations exist).
# Tokens rather than profile URIs, deliberately: a profile URI minted under
# ``/rdf/`` would be a permanent public identifier we then owe a representation
# (D8), and no client has asked for one. For the same reason no
# ``Link: rel="profile"`` header is emitted — the alternates it would advertise
# are already carried by ``rel="alternate"``, which needs no vocabulary of ours.
PROFILES = ("rfdb", "alt")
ALT_PROFILE = "alt"


def _negotiate(accept: str, override: str | None, supported: tuple[str, ...]) -> str:
    """Choose a media type from an ``Accept`` header, or from ``?_mediatype=``.

    Honours q-values, since browsers rank with them (``…;q=0.9``) and ignoring
    them would pick a type the client ranked last. Unparseable q-values score 0
    rather than raising — a malformed ``Accept`` should not fail a GET.

    Args:
        accept: Raw ``Accept`` header; may be empty.
        override: Explicit ``_mediatype`` query parameter (ConnegP), which wins
            over the header when present.
        supported: The media types this route can serve, so ``/rdf/schema/…``
            can offer RDF only without a second negotiation function.

    Raises:
        HTTPException 400: ``override`` names a media type this route cannot
            serve. Explicit beats implicit here: a client that *asked* for a
            representation by name deserves an error rather than a silent
            substitution.
    """
    if override is not None:
        # A query string is form-encoded, so a literal '+' arrives as a space and
        # 'application/ld+json' would be unrecognisable. No media type contains a
        # space, which makes undoing that transport artifact information-
        # preserving — unlike substituting a *different* type, which is what the
        # 400 below refuses to do. URLs this service emits percent-encode the '+'
        # (see _variant_url); this is for the ones typed by hand.
        override = override.replace(" ", "+")
        if override not in supported:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported _mediatype '{override}'. Supported: {', '.join(supported)}",
            )
        return override

    ranked: list[tuple[float, str]] = []
    for entry in accept.split(","):
        token, _, params = entry.strip().partition(";")
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if token:
            ranked.append((quality, token.strip()))

    # Stable sort, so equal-q types keep the client's stated order.
    for _, token in sorted(ranked, key=lambda pair: -pair[0]):
        if token in supported:
            return token
        if token in ("*/*", "application/*"):
            return DEFAULT_MEDIA_TYPE
    return DEFAULT_MEDIA_TYPE


def _check_profile(profile: str | None) -> None:
    """Reject an unknown ``?_profile=`` token, for the same reason as ``_mediatype``.

    Raises:
        HTTPException 400: the token is not one of ``PROFILES``.
    """
    if profile is not None and profile not in PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported _profile '{profile}'. Supported: {', '.join(PROFILES)}",
        )


def _variant_url(media_type: str, iri: str = "") -> str:
    """The URL that returns one specific representation.

    ``quote`` is load-bearing rather than defensive: a query string is
    form-encoded, so the ``+`` in ``application/ld+json`` would reach the handler
    as a space. Emitting it raw — which the first cut of this endpoint did in
    ``Content-Location`` — advertises a URL that answers 400. Relative when
    ``iri`` is omitted, which is what the HTML page's own links want so they
    resolve against whichever host is serving.
    """
    return f"{iri}?_mediatype={quote(media_type)}"


def _alternates(iri: str, supported: tuple[str, ...]) -> Graph:
    """Build the ConnegP ``alt`` view: which representations this resource has.

    Described with DCMI terms — ``dcterms:hasFormat`` is defined as "a related
    resource that is substantially the same as the described resource, but in
    another format", which is precisely an alternate representation — rather than
    with the ALTR vocabulary Prez uses. ``dcterms:`` is already one of the
    project's prefixes, and following the ConnegP *convention* without adopting
    its stack is this refactor's recorded non-goal.

    devnote: the ceiling is a client that insists on literal ALTR predicates. The
    upgrade is to add those triples alongside these, not to restructure — the
    answer's shape (one node per variant, carrying its media type) is the same in
    both vocabularies.
    """
    graph = Graph()
    resource = URIRef(iri)
    for media_type in supported:
        variant = URIRef(_variant_url(media_type, iri))
        graph.add((resource, DCTERMS.hasFormat, variant))
        graph.add((variant, DCTERMS.format, Literal(media_type)))
    return graph


def _respond(
    graph: Graph,
    iri: str,
    media_type: str,
    supported: tuple[str, ...],
    prefixes: dict[str, str],
) -> Response:
    """Serialize ``graph`` as ``media_type``, with the schema's CURIE prefixes bound.

    ``Vary: Accept`` is not decoration: without it a shared cache may hand a
    JSON-LD client the Turtle it stored for someone else. ``Content-Location``
    names the specific representation, so the negotiated variant has its own URL,
    and the ``Link`` header lists every other variant — which is what makes the
    alternates discoverable without ConnegP-specific knowledge.
    """
    if media_type == HTML_MEDIA_TYPE:
        body = _render_html(graph, iri, _namespaces(prefixes))
    else:
        for prefix, namespace in prefixes.items():
            graph.bind(prefix, namespace)
        body = graph.serialize(format=MEDIA_TYPES[media_type])

    links = [f'<{iri}>; rel="canonical"'] + [
        f'<{_variant_url(variant, iri)}>; rel="alternate"; type="{variant}"'
        for variant in supported
    ]
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Vary": "Accept",
            "Content-Location": _variant_url(media_type, iri),
            "Link": ", ".join(links),
        },
    )


# ---------------------------------------------------------------------------
# The HTML representation
# ---------------------------------------------------------------------------

# The path each served namespace lives at, derived from the namespace rather than
# written out: ``DATA_BASE_URI`` is ``https://rosfeatr.eu/rdf/data/`` and this router
# is mounted at ``/rdf``, so the namespace's path component *is* the route. That
# identity is the whole of D9, and deriving it keeps these links correct if the
# namespace ever moves.
#
# Links are emitted path-only for a second reason: a persisted IRI names
# rosfeatr.eu, but the reader may answer on any non-production host, so an
# absolute link would send that click to the public site instead. This is the server-side
# twin of the frontend's ``resolveFileUrl`` re-basing.
_LOCAL_SPACES = tuple(
    (ns, urlsplit(ns).path) for ns in (settings.data_base_uri, settings.schema_base_uri)
)

# RDF 1.1 makes a plain literal and an ``xsd:string``-typed one the same term, so
# printing that datatype would be noise rather than information.
_IMPLICIT_DATATYPE = str(XSD.string)

# The datatype that says a literal's text is a URL. ``schema:contentUrl`` carries
# it, and must stay a literal rather than an IRI: the shipped schema declares
# ``sh:datatype xsd:anyURI``, so the IRI form dereferences perfectly and fails
# SHACL (D9, the way that was learned).
_URI_DATATYPE = str(XSD.anyURI)

_TYPE = str(RDF.type)
_LABEL = str(RDFS.label)

# One <style> block, no external assets: a page in the data space must render
# from the same request that fetched the data, with no CDN in the trust path.
_STYLE = """
:root { color-scheme: light dark }
body { font: 16px/1.55 system-ui, sans-serif; max-width: 62rem; margin: 2rem auto; padding: 0 1rem }
h1 { font-size: 1.55rem; margin: 0 0 .3rem }
h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: .07em;
     opacity: .65; margin: 2.2rem 0 .4rem }
table { border-collapse: collapse; width: 100% }
th, td { text-align: left; vertical-align: top; padding: .35rem .9rem .35rem 0;
         border-top: 1px solid #8883 }
th { font-weight: 500; width: 18rem; white-space: nowrap }
.iri { margin: 0 0 .2rem; word-break: break-all; opacity: .8; font-size: .9rem }
.meta { opacity: .55; font-size: .85em }
footer { margin-top: 2.6rem; padding-top: .9rem; border-top: 1px solid #8883;
         font-size: .9rem; opacity: .85 }
"""


def _namespaces(prefixes: dict[str, str]) -> list[tuple[str, str]]:
    """``(namespace, prefix)`` pairs, longest namespace first.

    Longest first so a namespace that is a prefix of another cannot shadow it.
    """
    return sorted(((ns, prefix) for prefix, ns in prefixes.items()), key=lambda pair: -len(pair[0]))


def _curie(iri: str, namespaces: list[tuple[str, str]]) -> str:
    """Compact an IRI to ``prefix:local`` using the schema's declared prefix map.

    The declared map, not the graph's namespace manager: a bare ``rdflib.Graph``
    pre-binds ~29 unrelated vocabularies that would leak into the output (see
    ``rfdb_core.prefixes``). Falls back to the full IRI, which is never wrong.
    """
    for namespace, prefix in namespaces:
        if iri.startswith(namespace):
            return f"{prefix}:{iri[len(namespace) :]}"
    return iri


def _href(iri: str) -> str | None:
    """Return the URL to link an IRI to, or ``None`` if it must not become a link.

    IRIs in a namespace this service serves are rewritten to a path, so the link
    resolves on whatever host is answering. Anything else is linked as-is if it
    is ``http(s)`` and not at all otherwise: objects in the store are
    curator-supplied, so a ``javascript:`` value must never reach an ``href``.
    """
    for namespace, path in _LOCAL_SPACES:
        if iri.startswith(namespace):
            return f"{path}{iri[len(namespace) :]}"
    return iri if iri.startswith(("http://", "https://")) else None


def _term(term: object, namespaces: list[tuple[str, str]], link_template: str | None = None) -> str:
    """Render one RDF term as an HTML fragment — a link where it can be one.

    ``link_template`` is the ``EXTERNAL_ID_LINKS`` template for the predicate a
    literal is the value of, if any: the literal is then an identifier stored as
    text, and links to the authority's page.
    """
    if isinstance(term, Literal):
        value = escape(str(term))
        if link_template:
            href = external_id_url(link_template, str(term))
            value = f'<a href="{escape(href, quote=True)}">{value}</a>'
        if term.language:
            return f'{value} <span class="meta">@{escape(term.language)}</span>'

        datatype = str(term.datatype) if term.datatype else ""
        # An xsd:anyURI literal *is* a URL, and on a digital copy it is the one
        # that matters: schema:contentUrl, which D9 made absolute and
        # dereferenceable. Linking it through _href re-bases it onto the serving
        # host, so the page a developer is reading reaches that stack's bytes
        # rather than production's. Left as text before it was clear _href already
        # solved that — the page it appears on is otherwise a dead end.
        if datatype == _URI_DATATYPE and (href := _href(str(term))):
            value = f'<a href="{escape(href, quote=True)}">{value}</a>'
        if datatype and datatype != _IMPLICIT_DATATYPE:
            value += f' <span class="meta">{escape(_curie(datatype, namespaces))}</span>'
        return value

    label = escape(_curie(str(term), namespaces))
    href = _href(str(term))
    return f'<a href="{escape(href, quote=True)}">{label}</a>' if href else f"<code>{label}</code>"


def _section(
    title: str, rows: list[tuple[object, object]], namespaces: list[tuple[str, str]]
) -> str:
    """One ``<h2>`` + table, or nothing at all when there is nothing to show."""
    if not rows:
        return ""
    cells = "".join(
        f"<tr><th>{_term(a, namespaces)}</th>"
        f"<td>{_term(b, namespaces, settings.external_id_links.get(str(a)))}</td></tr>"
        for a, b in rows
    )
    return f"<h2>{escape(title)}</h2><table>{cells}</table>"


def _render_html(graph: Graph, iri: str, namespaces: list[tuple[str, str]]) -> str:
    """Render a resource's description as one self-contained HTML page.

    The field set is the one ``GET /api/v1/dataexplorer/graph/node`` assembles —
    label, types, literal properties, outbound IRI links, inbound links — but
    built from the graph this route already fetched rather than by calling that
    endpoint. Calling it would cost three more SPARQL queries and, worse, give
    the two representations different 404 conditions: in a data space, whether an
    identifier exists must not depend on the format asked for.

    The one thing not reproduced is that endpoint's schema-driven split of
    outbound IRIs into ``edges`` (a shape declares the predicate a relation) and
    ``externalLinks``. A reader learns the same thing from the target — ``rfdb:``
    stays here, ``wd:`` leaves — so this groups by direction instead and needs no
    SHACL extractor. Neighbour *labels* are likewise not fetched: that is the
    query the graph endpoint exists to run, and a CURIE identifies the neighbour
    well enough to click.
    """
    subject = URIRef(iri)
    labels: list[Literal] = []
    types: list[str] = []
    literals: list[tuple[object, object]] = []
    outbound: list[tuple[object, object]] = []

    for _, predicate, obj in graph.triples((subject, None, None)):
        pred = str(predicate)
        if pred == _TYPE:
            types.append(str(obj))
        elif isinstance(obj, Literal):
            # Labels are listed as properties *as well as* supplying the heading.
            # Only one can be the heading, and this is a bilingual dataset — a
            # page that showed "A source" and dropped "Источник"@ru would be
            # describing the resource incompletely, which the JSON endpoint can
            # get away with (its client fetches every triple separately) and a
            # representation of the resource cannot.
            literals.append((predicate, obj))
            if pred == _LABEL:
                labels.append(obj)
        else:
            outbound.append((predicate, obj))

    inbound = [(pred, subj) for subj, pred, _ in graph.triples((None, None, subject))]

    for rows in (literals, outbound, inbound):
        rows.sort(key=lambda row: (str(row[0]), str(row[1])))

    heading = preferred_label(labels) or _curie(iri, namespaces)
    head_links = "\n".join(
        f'<link rel="alternate" type="{media_type}" href="{_variant_url(media_type)}">'
        for media_type in MEDIA_TYPES
    )
    type_line = " · ".join(_term(URIRef(t), namespaces) for t in sorted(types))
    variants = " · ".join(
        f'<a href="{_variant_url(media_type)}">{media_type}</a>' for media_type in MEDIA_TYPES
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(heading)} — {escape(settings.project_name)}</title>
{head_links}
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>{escape(heading)}</h1>
<p class="iri"><code>{escape(iri)}</code></p>
{f'<p><span class="meta">rdf:type</span> {type_line}</p>' if type_line else ""}
{_section("Properties", literals, namespaces)}
{_section("Links", outbound, namespaces)}
{_section("Referenced by", inbound, namespaces)}
<footer>
Also available as: {variants} ·
<a href="?_profile={ALT_PROFILE}">alternate representations</a>
</footer>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/data/{local_name}")
def dereference_entity(
    local_name: str,
    request: Request,
    _mediatype: str | None = None,
    _profile: str | None = None,
):
    """Return every triple that mentions this resource, in the negotiated format.

    Both directions are fetched — statements *about* the resource and statements
    *pointing at* it — because a description that omitted inbound links would make
    the graph unwalkable from an IRI, which is the whole point of dereferencing.

    Args:
        local_name: The IRI's last segment; the full IRI is ``DATA_BASE_URI`` + this.
        _mediatype: ConnegP override, beating the ``Accept`` header.
        _profile: ConnegP profile token — ``rfdb`` (default) or ``alt`` for the
            list of available representations.

    Raises:
        HTTPException 400: ``_mediatype`` or ``_profile`` is unsupported.
        HTTPException 404: malformed local name, or no triples mention the IRI.
        HTTPException 503: the triplestore is unreachable.
    """
    if not LOCAL_NAME_RE.match(local_name):
        raise HTTPException(status_code=404, detail="Resource not found")

    supported = (*MEDIA_TYPES, HTML_MEDIA_TYPE)
    media_type = _negotiate(request.headers.get("accept", ""), _mediatype, supported)
    _check_profile(_profile)
    iri = f"{settings.data_base_uri}{local_name}"
    store = request.app.state.store

    # Unbound template variables drop their triple, so each UNION branch
    # contributes only its own direction.
    sparql = f"""
        CONSTRUCT {{
            <{iri}> ?p ?o .
            ?s ?ip <{iri}> .
        }}
        {store.from_clause()}
        WHERE {{
            {{ <{iri}> ?p ?o . }}
            UNION
            {{ ?s ?ip <{iri}> . }}
        }}
    """

    try:
        graph = store.construct(sparql)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Store unavailable") from exc

    if len(graph) == 0:
        raise HTTPException(status_code=404, detail=f"Resource '{iri}' not found")

    # Existence is checked first even for `alt`: a resource that does not exist
    # has no representations to list, and one 404 condition per identifier is
    # what keeps the data space coherent.
    if _profile == ALT_PROFILE:
        graph = _alternates(iri, supported)

    return _respond(graph, iri, media_type, supported, request.app.state.schema_extractor.prefixes)


@router.get("/schema/{shape_name}")
def dereference_shape(
    shape_name: str,
    request: Request,
    _mediatype: str | None = None,
    _profile: str | None = None,
):
    """Return a SHACL shape's own definition, in the negotiated format.

    Shape IRIs (``rfdbs:MusicalWorkShape``) appear in data, in every shapes
    response, and in ``READ_ONLY_SHAPES``, so they should dereference like anything
    else. Served from the parsed schema rather than the triplestore — the
    schema is a file this service already holds in memory, and it is not loaded
    into the data graph.

    Uses rdflib's concise bounded description so the shape's property nodes come
    along: they are blank nodes, and a shape without them describes nothing. Those
    blank nodes are also why this route serves no HTML — see the module docstring.

    Raises:
        HTTPException 400: ``_mediatype`` or ``_profile`` is unsupported.
        HTTPException 404: malformed or unknown shape name.
    """
    if not LOCAL_NAME_RE.match(shape_name):
        raise HTTPException(status_code=404, detail="Shape not found")

    supported = tuple(MEDIA_TYPES)
    media_type = _negotiate(request.headers.get("accept", ""), _mediatype, supported)
    _check_profile(_profile)
    iri = f"{settings.schema_base_uri}{shape_name}"

    if request.app.state.schema_extractor.get_shape(iri) is None:
        raise HTTPException(status_code=404, detail=f"Shape '{iri}' not found")

    graph = (
        _alternates(iri, supported)
        if _profile == ALT_PROFILE
        else request.app.state.schema_extractor.graph.cbd(URIRef(iri))
    )
    return _respond(graph, iri, media_type, supported, request.app.state.schema_extractor.prefixes)
