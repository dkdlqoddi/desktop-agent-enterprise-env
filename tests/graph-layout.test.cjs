const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// src/lib/graph-layout.ts as CommonJS: it imports types only, which the transpiler drops.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('module', 'exports', 'require', outputText)(module, module.exports, require);
  return module.exports;
}
const { DOCUMENT_LIMIT, SPACING, forceLayout, graphModel, separate } =
  load('src/lib/graph-layout.ts');

// A Wiki's graph as wiki.graph answers it: `docs` documents, each with keywords from one of
// `topics` topics (t0-0, t0-1 … of topic 0) and now and then a common one (common-0 …).
function wiki(docs, { topics = 3, seed = 7 } = {}) {
  let state = seed;
  const random = () => (state = (state * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const keywordDocs = new Map();
  const relations = new Map();
  const documents = [];
  for (let d = 0; d < docs; d++) {
    const topic = d % topics;
    const id = `doc-${d}`;
    documents.push({ id, title: `문서 ${d}` });
    const keys = new Set();
    while (keys.size < 5)
      keys.add(
        random() < 0.9
          ? `t${topic}-${Math.floor(random() ** 1.5 * 10)}`
          : `common-${Math.floor(random() * 3)}`,
      );
    for (const key of keys) keywordDocs.set(key, [...(keywordDocs.get(key) || []), id]);
    const [source, target] = keys;
    const relation = `${source}\u001f${target}`;
    if (!relations.has(relation))
      relations.set(relation, { source, target, label: '관련', docs: [] });
    relations.get(relation).docs.push(id);
  }
  const nodes = [...keywordDocs]
    .map(([key, ids]) => ({ id: key, label: key, count: ids.length, docs: ids }))
    .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id));
  return {
    nodes,
    edges: [...relations.values()].map((r) => ({ ...r, count: r.docs.length })),
    documents: documents.reverse(),
    total_keywords: nodes.length,
    total_relations: relations.size,
  };
}
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
// How far the nodes of `ids` moved between two layouts, whatever their size: each layout centered
// and scaled to a mean (root mean square) radius of 1, as the 2D view fits it to the screen.
function reshaped(ids, before, after) {
  const shape = (positions) => {
    const points = ids.map((id) => positions.get(id));
    const [cx, cy] = ['x', 'y'].map(
      (axis) => points.reduce((sum, point) => sum + point[axis], 0) / points.length,
    );
    const radius = Math.sqrt(
      points.reduce((sum, p) => sum + (p.x - cx) ** 2 + (p.y - cy) ** 2, 0) / points.length,
    );
    return points.map((p) => ({ x: (p.x - cx) / radius, y: (p.y - cy) / radius, z: 0 }));
  };
  const [a, b] = [shape(before), shape(after)];
  return a.reduce((sum, point, i) => sum + distance(point, b[i]), 0) / ids.length;
}

test('only what is drawn is laid out: documents only when shown, and at most DOCUMENT_LIMIT of them', () => {
  const graph = wiki(1000);
  const hidden = graphModel(graph, 20, false);
  assert.equal(hidden.ids.length, 20);
  assert.ok(hidden.ids.every((id) => id.startsWith('k:')));
  assert.ok(hidden.edges.every((edge) => edge.kind === 'relation'));
  assert.equal(hidden.omitted, 0);
  // Keywords that share documents still pull together, without the documents.
  assert.ok(hidden.links.length > hidden.edges.length);
  assert.ok(
    hidden.links.every(
      (link) => hidden.ids.includes(link.source) && hidden.ids.includes(link.target),
    ),
  );

  const shown = graphModel(graph, 20, true);
  const drawn = shown.nodes.filter((node) => node.kind === 'document');
  assert.equal(drawn.length, DOCUMENT_LIMIT);
  assert.equal(shown.ids.length, 20 + DOCUMENT_LIMIT);
  assert.ok(shown.omitted > 0);
  // The documents that carry the most of the drawn keywords come first.
  const counts = drawn.map((node) => node.count);
  assert.deepEqual(
    counts,
    [...counts].sort((a, b) => b - a),
  );
  assert.ok(shown.edges.some((edge) => edge.kind === 'membership'));
});

test('a reload with the same graph keeps both keys; a new title redraws without laying out again', () => {
  const graph = wiki(200);
  const first = graphModel(graph, 60, true);
  const again = graphModel(structuredClone(graph), 60, true);
  assert.equal(again.key, first.key);
  assert.equal(again.layoutKey, first.layoutKey);
  const renamed = structuredClone(graph);
  renamed.documents[0].title = '새 제목';
  const changed = graphModel(renamed, 60, true);
  assert.equal(changed.layoutKey, first.layoutKey);
  assert.notEqual(changed.key, first.key);
  const more = graphModel(wiki(201), 60, true);
  assert.notEqual(more.layoutKey, first.layoutKey);
});

test('keywords of one topic land closer to each other than to other topics', () => {
  const model = graphModel(wiki(300), 30, false);
  const positions = forceLayout(model.ids, model.links, 2);
  const topic = (id) => id.slice(2).split('-')[0];
  let [same, other] = [[], []];
  for (const a of model.ids)
    for (const b of model.ids)
      if (a < b && !a.includes('common') && !b.includes('common'))
        (topic(a) === topic(b) ? same : other).push(distance(positions.get(a), positions.get(b)));
  const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;
  assert.ok(mean(same) * 1.5 < mean(other), `${mean(same)} vs ${mean(other)}`);
});

test('a layout is the same for the same graph, and keeps its shape when a reload adds a node', () => {
  const model = graphModel(wiki(300), 30, false);
  const first = forceLayout(model.ids, model.links, 2);
  assert.deepEqual(forceLayout(model.ids, model.links, 2), first);
  // Nothing new: nothing moves.
  assert.deepEqual(forceLayout(model.ids, model.links, 2, first), first);
  // One more keyword: it settles by its partners and the others barely move, as a fresh layout would not.
  const grown = graphModel(wiki(300), 31, false);
  const warm = forceLayout(grown.ids, grown.links, 2, first);
  const cold = forceLayout(grown.ids, grown.links, 2);
  const moved = model.ids.reduce((sum, id) => sum + distance(first.get(id), warm.get(id)), 0);
  assert.ok(moved / model.ids.length < SPACING / 2, `moved ${moved / model.ids.length}`);
  assert.ok(reshaped(model.ids, first, warm) < 0.2);
  assert.ok(reshaped(model.ids, first, cold) > reshaped(model.ids, first, warm) * 3);
  const added = grown.ids.find((id) => !first.has(id));
  const nearest = Math.min(...model.ids.map((id) => distance(warm.get(added), first.get(id))));
  assert.ok(nearest < SPACING * 3, `nearest ${nearest}`);
  // Shown documents start by their keywords, which keep their arrangement as the graph grows around them.
  const shown = graphModel(wiki(300), 30, true);
  const withDocs = forceLayout(shown.ids, shown.links, 2, first);
  const freshDocs = forceLayout(shown.ids, shown.links, 2);
  assert.ok(reshaped(model.ids, first, withDocs) < 0.3);
  assert.ok(reshaped(model.ids, first, freshDocs) > reshaped(model.ids, first, withDocs));
});

test('three dimensions spread the nodes in depth too', () => {
  const model = graphModel(wiki(100), 20, false);
  const positions = [...forceLayout(model.ids, model.links, 3).values()];
  assert.ok(positions.some((point) => Math.abs(point.z) > SPACING / 2));
  assert.ok(positions.every((point) => Number.isFinite(point.x + point.y + point.z)));
});

test('the overlap pass leaves no circles overlapping where there is room, also two on one spot', () => {
  const items = Array.from({ length: 300 }, (_, i) => ({
    x: (i * 37) % 1200,
    y: (i * 53) % 800,
    r: 8 + (i % 5) * 4,
  }));
  items.push({ x: 100, y: 100, r: 60 }, { x: 100, y: 100, r: 30 });
  separate(items, 200);
  for (let i = 0; i < items.length; i++)
    for (let j = i + 1; j < items.length; j++) {
      const [a, b] = [items[i], items[j]];
      assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= a.r + b.r - 0.01, `${i} and ${j} overlap`);
    }
});
