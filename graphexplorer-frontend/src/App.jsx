/**
 * Graph Explorer — a read-only, schema-driven RDF lineage & relationship graph.
 *
 * App owns all state and the graph model. It hydrates the prefix map + SHACL
 * schema (labels, colours, relation names all come from the schema — nothing
 * domain-specific is baked in) once, then builds the graph incrementally: each
 * node the user opens is fetched from GET /api/v1/dataexplorer/graph/node
 * (schema-defined inbound/outbound relations), its neighbours are added as
 * collapsed nodes, and each node's own button expands it in turn. A ?id=<iri>
 * query param deep-links straight to a node — the editor uses this to "open in
 * Explorer".
 *
 * Child components are presentational: GraphView renders the model with React
 * Flow + elk; Inspector shows the selected node; SourcePicker seeds it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client.js'
import GraphView from './components/GraphView.jsx'
import Inspector from './components/Inspector.jsx'
import SourcePicker from './components/SourcePicker.jsx'
import { compactIri, hydratePrefixes } from './utils/prefixes.js'
import { entityKind, hydrateSchema, isBridgeType, predicateLabel } from './utils/types.js'

// Deployment-branding only; the code itself is domain-agnostic.
const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'RFDB Explorer'

/** A collapsed node, from its IRI and whatever label and types are known so far. */
function stubNode(id, label = '', types = []) {
  const kind = entityKind(types)
  return {
    id,
    label: label || compactIri(id),
    types,
    kind: kind.kind,
    typeLabel: kind.label,
    isBridge: isBridgeType(types),
    expanded: false,
    loading: false,
    edgeCount: null, // null = link count not known yet; a number (incl. 0) once fetched
  }
}

function errMessage(err) {
  return err?.response?.data?.detail || err?.message || 'Request failed'
}

export default function App() {
  const [ready, setReady] = useState(false)
  const [shapes, setShapes] = useState([])
  const [nodesMap, setNodesMap] = useState({})
  const [edgesMap, setEdgesMap] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const [error, setError] = useState(null)
  // getNode results are cached and shared: a node is fetched at most once, and
  // whoever needs it — the eager prefetch that shows a node's link count, or an
  // explicit expand — reuses the same request/result. Expanding a node that was
  // already prefetched is therefore instant (no extra round-trip).
  const cache = useRef(new Map()) // id -> getNode data
  const inflight = useRef(new Map()) // id -> in-flight Promise<data>

  const fetchNodeData = useCallback((id) => {
    if (cache.current.has(id)) return Promise.resolve(cache.current.get(id))
    if (inflight.current.has(id)) return inflight.current.get(id)
    const p = api
      .getNode(id)
      .then((data) => {
        cache.current.set(id, data)
        inflight.current.delete(id)
        return data
      })
      .catch((err) => {
        inflight.current.delete(id)
        throw err
      })
    inflight.current.set(id, p)
    return p
  }, [])

  // Merge a fetched node into the model. `expand` decides whether its neighbours
  // and edges are rendered: false only hydrates the node itself (label, type,
  // fields, link count) so a collapsed node can show its count; true additionally
  // adds the neighbour ring and relation edges. Never downgrades an already-
  // expanded node, so a late prefetch can't collapse it.
  const applyNodeData = useCallback((id, data, { expand }) => {
    const kind = entityKind(data.types)
    setNodesMap((prev) => {
      const next = { ...prev }
      next[id] = {
        ...(prev[id] || stubNode(id)),
        id,
        label: data.label || prev[id]?.label || compactIri(id),
        types: data.types,
        kind: kind.kind,
        typeLabel: kind.label,
        isBridge: isBridgeType(data.types),
        loading: false,
        edgeCount: data.edges.length,
        truncated: !!data.truncated,
        details: { literals: data.literals, externalLinks: data.externalLinks },
        expanded: expand || prev[id]?.expanded || false,
      }
      if (expand) {
        for (const e of data.edges) {
          const nb = e.neighbor
          if (!next[nb.id]) next[nb.id] = stubNode(nb.id, nb.label, nb.types)
        }
      }
      return next
    })
    if (expand) {
      setEdgesMap((prev) => {
        const next = { ...prev }
        for (const e of data.edges) {
          const source = e.direction === 'in' ? e.neighbor.id : id
          const target = e.direction === 'in' ? id : e.neighbor.id
          const eid = `${source}|${e.predicate}|${target}`
          if (!next[eid]) {
            next[eid] = { id: eid, source, target, predicate: e.predicate, label: predicateLabel(e.predicate) }
          }
        }
        return next
      })
    }
  }, [])

  // Eager, background hydration of a collapsed node so it shows its link count
  // (and fields) without a click. Silent on failure — the node just keeps its
  // "Expand" affordance and can be fetched again on expand.
  const prefetchNode = useCallback(
    (id) => {
      if (!id) return
      fetchNodeData(id)
        .then((data) => applyNodeData(id, data, { expand: false }))
        .catch(() => {})
    },
    [fetchNodeData, applyNodeData]
  )

  // Expand a node: render its neighbours + edges (instant when already prefetched),
  // then eagerly prefetch those neighbours so their counts appear in turn.
  const expandNode = useCallback(
    (id) => {
      if (!id) return
      if (!cache.current.has(id)) {
        setNodesMap((prev) =>
          prev[id] && !prev[id].expanded ? { ...prev, [id]: { ...prev[id], loading: true } } : prev
        )
      }
      fetchNodeData(id)
        .then((data) => {
          applyNodeData(id, data, { expand: true })
          for (const e of data.edges) prefetchNode(e.neighbor.id)
        })
        .catch((err) => {
          cache.current.delete(id) // allow a later retry
          setNodesMap((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], loading: false } } : prev))
          setError(errMessage(err))
        })
    },
    [fetchNodeData, applyNodeData, prefetchNode]
  )

  const activate = useCallback(
    (id) => {
      setError(null)
      setSelectedId(id)
      expandNode(id)
    },
    [expandNode]
  )

  // Place a node from the search box collapsed (just this node) and select it,
  // then eagerly prefetch it so its link count (and fields) appear. Its links are
  // not rendered until the user expands. If it's already on the map, just select.
  const seedNode = useCallback(
    (id, meta = {}) => {
      setError(null)
      setSelectedId(id)
      setNodesMap((prev) => {
        if (prev[id]) return prev
        return { ...prev, [id]: stubNode(id, meta.label, meta.types || []) }
      })
      prefetchNode(id)
    },
    [prefetchNode]
  )

  const clearGraph = useCallback(() => {
    cache.current = new Map()
    inflight.current = new Map()
    setNodesMap({})
    setEdgesMap({})
    setSelectedId(null)
    setError(null)
  }, [])

  // Drop a single node from the map along with its incident edges. Leaves any
  // now-disconnected neighbours in place (by design). Un-guards the id so it can
  // be re-explored later.
  const hideNode = useCallback((id) => {
    cache.current.delete(id)
    inflight.current.delete(id)
    setNodesMap((prev) => {
      if (!prev[id]) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
    setEdgesMap((prev) => {
      const next = {}
      for (const [eid, e] of Object.entries(prev)) {
        if (e.source !== id && e.target !== id) next[eid] = e
      }
      return next
    })
    setSelectedId((cur) => (cur === id ? null : cur))
  }, [])

  // Startup: hydrate prefixes + shapes, then honour a ?id= deep link.
  useEffect(() => {
    let cancelled = false
    Promise.all([api.getPrefixes(), api.getShapes()])
      .then(([prefixes, allShapes]) => {
        if (cancelled) return
        hydratePrefixes(prefixes)
        hydrateSchema(allShapes)
        setShapes(allShapes.filter((s) => s.shapeRole === 'standalone-entity'))
        setReady(true)
        const id = new URLSearchParams(window.location.search).get('id')
        if (id) activate(id)
      })
      .catch((err) => {
        if (!cancelled) setError(errMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [activate])

  const nodes = useMemo(() => Object.values(nodesMap), [nodesMap])
  const edges = useMemo(() => Object.values(edgesMap), [edgesMap])
  const hasGraph = nodes.length > 0
  const defaultShapeId = useMemo(() => shapes[0]?.id, [shapes])

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1 className="app-title">{APP_TITLE}</h1>
          <p className="app-subtitle">Lineage &amp; relationships</p>
        </div>
        {hasGraph && ready && (
          <SourcePicker
            shapes={shapes}
            defaultShapeId={defaultShapeId}
            onPick={seedNode}
            variant="header"
          />
        )}
        <div className="spacer" />
        {hasGraph && (
          <button className="btn btn-ghost" onClick={clearGraph}>
            Clear
          </button>
        )}
      </header>

      <div className="app-body">
        {error && <div className="banner-error">{error}</div>}

        {!hasGraph ? (
          <div className="intro">
            <div className="intro-card">
              <h2>Explore the graph</h2>
              <p>
                Pick a record to see how it connects — the entities it links to, and the ones
                that link back. Use the Expand button on a node to follow its links.
              </p>
              {ready ? (
                <SourcePicker
                  shapes={shapes}
                  defaultShapeId={defaultShapeId}
                  onPick={seedNode}
                  variant="intro"
                />
              ) : (
                <p className="picker-loading">Loading…</p>
              )}
            </div>
          </div>
        ) : (
          <>
            <GraphView
              nodes={nodes}
              edges={edges}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onExpand={expandNode}
            />
            {selectedId && nodesMap[selectedId] && (
              <Inspector
                node={nodesMap[selectedId]}
                edges={edges}
                nodesById={nodesMap}
                onSelectNeighbor={activate}
                onHide={hideNode}
                onClose={() => setSelectedId(null)}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
