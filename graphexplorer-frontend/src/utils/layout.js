/**
 * Auto-layout via elkjs (runs in-browser). One ELK instance for the app.
 *
 * A force-directed profile suits an association/relationship graph (as opposed
 * to a strict hierarchy). Positions are mapped back onto the React Flow nodes;
 * a simple grid is used as a fallback if ELK throws.
 */
import ELK from 'elkjs/lib/elk.bundled.js'

const elk = new ELK()

const NODE_W = 220
const NODE_H = 92

// Bridge badges (EntityNode's compact variant for helper-bridge entities) are
// much smaller than a full card, so elk gets a slot of their own size. A
// card-sized slot would space them out as if they were full cards.
const BRIDGE_W = 90
const BRIDGE_H = 34

const LAYOUT_OPTIONS = {
  'elk.algorithm': 'force',
  'elk.force.iterations': '120',
  'elk.force.repulsivePower': '2.0',
  'elk.spacing.nodeNode': '120',
  'elk.separateConnectedComponents': 'true',
}

/**
 * Compute positions for `nodes` given `edges` and return the nodes with a
 * `position` set. `nodes`/`edges` are React Flow shapes ({id}, {source,target}).
 */
export async function applyElkLayout(nodes, edges) {
  const graph = {
    id: 'root',
    layoutOptions: LAYOUT_OPTIONS,
    children: nodes.map((n) => ({
      id: n.id,
      width: n.data?.isBridge ? BRIDGE_W : NODE_W,
      height: n.data?.isBridge ? BRIDGE_H : NODE_H,
    })),
    edges: edges.map((e, i) => ({
      id: e.id || `e${i}`,
      sources: [e.source],
      targets: [e.target],
    })),
  }
  try {
    const laid = await elk.layout(graph)
    const pos = new Map((laid.children || []).map((c) => [c.id, { x: c.x, y: c.y }]))
    return nodes.map((n) => ({ ...n, position: pos.get(n.id) || n.position || { x: 0, y: 0 } }))
  } catch {
    return nodes.map((n, i) => ({
      ...n,
      position: { x: (i % 5) * 260, y: Math.floor(i / 5) * 170 },
    }))
  }
}

const RING_RADIUS = 280
const RING_STEP = 130

/**
 * Position only the freshly-added nodes, leaving already-placed nodes untouched
 * so expanding a node grows the graph outward instead of relaying everything.
 *
 * Each new node is arranged on a ring around a placed neighbour (its "parent");
 * new nodes with no placed neighbour ring the origin. This is intentionally not
 * a full layout — call applyElkLayout for that (the explicit "Re-layout" action).
 *
 * @param {{id:string}[]} freshNodes - nodes without a remembered position
 * @param {{source:string,target:string}[]} edges
 * @param {Map<string,{x:number,y:number}>} placed - id -> position of placed nodes
 * @returns {Map<string,{x:number,y:number}>} id -> position for each fresh node
 */
export function placeNewNodes(freshNodes, edges, placed) {
  const byParent = new Map()
  for (const n of freshNodes) {
    let parent = null
    for (const e of edges) {
      const other = e.source === n.id ? e.target : e.target === n.id ? e.source : null
      if (other && placed.has(other)) {
        parent = other
        break
      }
    }
    const key = parent ?? '__origin__'
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key).push(n.id)
  }

  const result = new Map()
  for (const [parent, ids] of byParent) {
    const center = parent === '__origin__' ? { x: 0, y: 0 } : placed.get(parent)
    ids.forEach((id, i) => {
      const angle = (2 * Math.PI * i) / ids.length - Math.PI / 2
      const radius = RING_RADIUS + Math.floor(i / 12) * RING_STEP
      result.set(id, {
        x: Math.round(center.x + radius * Math.cos(angle)),
        y: Math.round(center.y + radius * Math.sin(angle)),
      })
    })
  }
  return result
}
