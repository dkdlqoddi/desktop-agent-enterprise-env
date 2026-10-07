import type { Graph } from './api';

export type Point = { x: number; y: number; z: number };
export type LayoutEdge = { source: string; target: string; weight: number };
export type GraphNode = {
  id: string;
  kind: 'keyword' | 'document';
  key: string;
  label: string;
  count: number;
};
export type GraphEdge = {
  id: string;
  kind: 'relation' | 'membership';
  source: string;
  target: string;
  label: string;
};
type Relation = Graph['edges'][number];

// ‘문서 함께 보기’ draws at most this many documents: those that carry the most of the drawn keywords.
export const DOCUMENT_LIMIT = 200;
// The distance the layout keeps between nodes, in its own units.
export const SPACING = 60;
// A keyword is pulled toward the keywords it shares the most documents with, at most this many.
const PARTNERS = 8;

export const relationId = (r: Pick<Relation, 'source' | 'target' | 'label'>) =>
  `r:${r.source}\u001f${r.target}\u001f${r.label}`;
export const membershipId = (doc: string, keyword: string) => `m:${doc}\u001f${keyword}`;

/**
 * What the knowledge graph draws: the `limit` most frequent keywords and the relations between
 * them, and with `docs` the documents that carry the most of them. Only what is drawn is laid out.
 * Keywords that share documents pull together whether documents are drawn or not, so keywords
 * from one source stay close; a document's pull is split among the pairs of its keywords.
 * `key` and `layoutKey` change only when a reload changes what is drawn or what is laid out.
 */
export function graphModel(graph: Graph, limit: number, docs: boolean) {
  const keywords = graph.nodes.slice(0, limit);
  const n = keywords.length;
  const index = new Map(keywords.map((k, i) => [k.id, i]));
  const docKeywords = new Map<string, number[]>();
  keywords.forEach((keyword, i) => {
    for (const doc of keyword.docs) {
      const keys = docKeywords.get(doc);
      if (keys) keys.push(i);
      else docKeywords.set(doc, [i]);
    }
  });
  const shared = new Float64Array(n * n);
  for (const keys of docKeywords.values())
    for (let a = 0; a < keys.length; a++)
      for (let b = a + 1; b < keys.length; b++)
        shared[keys[a] * n + keys[b]] += 0.35 / (keys.length - 1);
  const pairs = new Set<number>();
  for (let a = 0; a < n; a++) {
    const partners: [number, number][] = [];
    for (let b = 0; b < n; b++) {
      const pair = Math.min(a, b) * n + Math.max(a, b);
      if (shared[pair] > 0) partners.push([pair, shared[pair]]);
    }
    partners.sort((p, q) => q[1] - p[1] || p[0] - q[0]);
    for (const [pair] of partners.slice(0, PARTNERS)) pairs.add(pair);
  }
  const recent = new Map(graph.documents.map((d, i) => [d.id, i]));
  const titles = new Map(graph.documents.map((d) => [d.id, d.title]));
  const rank = (doc: string) => recent.get(doc) ?? graph.documents.length;
  const drawn = docs
    ? [...docKeywords]
        .sort(([a, x], [b, y]) => y.length - x.length || rank(a) - rank(b))
        .slice(0, DOCUMENT_LIMIT)
    : [];
  const nodes: GraphNode[] = [
    ...keywords.map((k) => ({
      id: `k:${k.id}`,
      kind: 'keyword' as const,
      key: k.id,
      label: k.label,
      count: k.count,
    })),
    ...drawn.map(([doc, keys]) => ({
      id: `d:${doc}`,
      kind: 'document' as const,
      key: doc,
      label: titles.get(doc) || '제목 없는 문서',
      count: keys.length,
    })),
  ];
  const edges: GraphEdge[] = [
    ...graph.edges
      .filter((r) => index.has(r.source) && index.has(r.target))
      .map((r) => ({
        id: relationId(r),
        kind: 'relation' as const,
        source: `k:${r.source}`,
        target: `k:${r.target}`,
        label: r.label,
      })),
    ...drawn.flatMap(([doc, keys]) =>
      keys.map((i) => ({
        id: membershipId(doc, keywords[i].id),
        kind: 'membership' as const,
        source: `d:${doc}`,
        target: `k:${keywords[i].id}`,
        label: '',
      })),
    ),
  ];
  const links: LayoutEdge[] = [
    ...edges.map((e) => ({
      source: e.source,
      target: e.target,
      weight: e.kind === 'relation' ? 1 : 0.35,
    })),
    ...[...pairs].map((pair) => ({
      source: `k:${keywords[Math.floor(pair / n)].id}`,
      target: `k:${keywords[pair % n].id}`,
      weight: Math.min(1, Number(shared[pair].toFixed(4))),
    })),
  ];
  const ids = nodes.map((node) => node.id);
  const layoutKey = JSON.stringify([ids, links]);
  return {
    nodes,
    edges,
    ids,
    links,
    // Documents with a drawn keyword left out past DOCUMENT_LIMIT.
    omitted: docs ? docKeywords.size - drawn.length : 0,
    key: layoutKey + JSON.stringify([nodes, edges]),
    layoutKey,
  };
}
export type GraphModel = ReturnType<typeof graphModel>;

// Seeded from the node ID so the same knowledge always lands in the same shape.
function seeded(id: string) {
  let hash = 2166136261;
  for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619);
  return () => {
    hash ^= hash << 13;
    hash ^= hash >>> 17;
    hash ^= hash << 5;
    return (hash >>> 0) / 4294967296;
  };
}

/**
 * Fruchterman–Reingold force layout with linear cooling, in two or three dimensions.
 * Every pair repels and edges pull their ends together by weight. A firm pull toward the
 * center packs separate clusters together; weaker pulls left them far apart, so fitting
 * the view shrank everything else. O(n²) per step: the graph draws at most a few hundred nodes.
 *
 * Given `previous`, an earlier layout of mostly the same nodes, those nodes start where they were
 * and new ones next to the nodes they link to, and a shorter run settles the new ones while the
 * earlier ones move a tenth as far: a graph that gains a few keywords or shows its documents keeps
 * its shape, yet clusters still form as it grows. (Earlier nodes moving freely went far: a cooled
 * layout is held by its low temperature, not by balanced forces.) With no new node it stays as is.
 */
export function forceLayout(
  ids: string[],
  edges: LayoutEdge[],
  dimensions: 2 | 3,
  previous?: Map<string, Point>,
  spacing = SPACING,
) {
  const n = ids.length;
  const index = new Map(ids.map((id, i) => [id, i]));
  const position = new Float64Array(n * 3);
  const radius = spacing * (dimensions === 3 ? Math.cbrt(n) : Math.sqrt(n));
  ids.forEach((id, i) => {
    const random = seeded(id);
    for (let axis = 0; axis < dimensions; axis++)
      position[i * 3 + axis] = (random() - 0.5) * radius;
  });
  const links = edges.flatMap((edge) => {
    const a = index.get(edge.source);
    const b = index.get(edge.target);
    return a === undefined || b === undefined || a === b ? [] : [[a, b, edge.weight] as const];
  });
  // The earlier layout counts when most nodes were in it or link to one that was.
  const known = ids.map((id) => previous?.get(id));
  const near = Array.from({ length: n }, () => ({ x: 0, y: 0, z: 0, count: 0 }));
  for (const [a, b] of links)
    for (const [from, to] of [
      [a, b],
      [b, a],
    ]) {
      const point = known[from];
      if (!point || known[to]) continue;
      near[to].x += point.x;
      near[to].y += point.y;
      near[to].z += point.z;
      near[to].count++;
    }
  const warm = n > 0 && known.filter((point, i) => point || near[i].count).length * 2 >= n;
  const mobility = Float64Array.from(known, (point) => (warm && point ? 0.1 : 1));
  if (warm)
    ids.forEach((id, i) => {
      const point = known[i];
      const { x, y, z, count } = near[i];
      const start = point ? point : count ? { x: x / count, y: y / count, z: z / count } : null;
      if (!start) return;
      // A new node starts off its neighbors' center, so that two new ones never coincide.
      const random = seeded(`${id}\u0000near`);
      const nudge = () => (point ? 0 : (random() - 0.5) * spacing);
      position[i * 3] = start.x + nudge();
      position[i * 3 + 1] = start.y + nudge();
      if (dimensions === 3) position[i * 3 + 2] = start.z + nudge();
    });
  const k2 = spacing * spacing;
  // A few new nodes settle in a few steps, which move the earlier ones less; with none, nothing moves.
  const added = known.filter((point) => !point).length;
  const steps = warm ? Math.min(80, added && 20 + added * 4) : n > 250 ? 160 : 300;
  const heat = warm ? spacing : radius / 3;
  const displacement = new Float64Array(n * 3);
  for (let step = 0; step < steps; step++) {
    displacement.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = position[i * 3] - position[j * 3];
        let dy = position[i * 3 + 1] - position[j * 3 + 1];
        const dz = position[i * 3 + 2] - position[j * 3 + 2];
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 0.01) {
          // Coincident points: separate them deterministically.
          dx = ((i % 7) - 3) * 0.1 + 0.05;
          dy = ((j % 5) - 2) * 0.1 + 0.05;
          d2 = dx * dx + dy * dy;
        }
        const force = k2 / d2;
        displacement[i * 3] += dx * force;
        displacement[i * 3 + 1] += dy * force;
        displacement[i * 3 + 2] += dz * force;
        displacement[j * 3] -= dx * force;
        displacement[j * 3 + 1] -= dy * force;
        displacement[j * 3 + 2] -= dz * force;
      }
    }
    for (const [a, b, weight] of links) {
      const dx = position[a * 3] - position[b * 3];
      const dy = position[a * 3 + 1] - position[b * 3 + 1];
      const dz = position[a * 3 + 2] - position[b * 3 + 2];
      const force = (Math.sqrt(dx * dx + dy * dy + dz * dz) * weight) / spacing;
      displacement[a * 3] -= dx * force;
      displacement[a * 3 + 1] -= dy * force;
      displacement[a * 3 + 2] -= dz * force;
      displacement[b * 3] += dx * force;
      displacement[b * 3 + 1] += dy * force;
      displacement[b * 3 + 2] += dz * force;
    }
    const temperature = heat * (1 - step / steps) + 0.5;
    for (let i = 0; i < n; i++) {
      for (let axis = 0; axis < 3; axis++)
        displacement[i * 3 + axis] -= position[i * 3 + axis] * 1.6;
      const dx = displacement[i * 3];
      const dy = displacement[i * 3 + 1];
      const dz = displacement[i * 3 + 2];
      const length = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (!length) continue;
      const move = Math.min(length, temperature * mobility[i]) / length;
      position[i * 3] += dx * move;
      position[i * 3 + 1] += dy * move;
      if (dimensions === 3) position[i * 3 + 2] += dz * move;
    }
  }
  return new Map<string, Point>(
    ids.map((id, i) => [
      id,
      { x: position[i * 3], y: position[i * 3 + 1], z: position[i * 3 + 2] },
    ]),
  );
}

/**
 * Pushes overlapping circles apart, in place, for at most `steps` rounds. Sorted by their left
 * edge, a circle is compared only with those that start before it ends, not with every other.
 */
export function separate(items: { x: number; y: number; r: number }[], steps = 60) {
  const order = [...items];
  for (let step = 0; step < steps; step++) {
    let moved = false;
    order.sort((a, b) => a.x - a.r - (b.x - b.r));
    for (let i = 0; i < order.length; i++) {
      const a = order[i];
      for (let j = i + 1; j < order.length && order[j].x - order[j].r < a.x + a.r; j++) {
        const b = order[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.hypot(dx, dy);
        const overlap = a.r + b.r - distance;
        if (overlap <= 0) continue;
        const [ux, uy] = distance ? [dx / distance, dy / distance] : [1, 0];
        a.x -= (ux * overlap) / 2;
        a.y -= (uy * overlap) / 2;
        b.x += (ux * overlap) / 2;
        b.y += (uy * overlap) / 2;
        moved = true;
      }
    }
    if (!moved) break;
  }
  return items;
}
