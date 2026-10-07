'use client';
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowRight,
  Box,
  FileText,
  Focus,
  Hash,
  Minus,
  Network,
  Plus,
  Search,
  Waypoints,
  X,
} from 'lucide-react';
import type { Graph } from '@/lib/api';
import {
  DOCUMENT_LIMIT,
  forceLayout,
  graphModel,
  membershipId,
  relationId,
  separate,
  SPACING,
  type GraphEdge,
  type GraphModel,
  type GraphNode,
  type Point,
} from '@/lib/graph-layout';
import Graph3D from './graph-3d';
import { rove } from './roving';

export type { GraphEdge, GraphNode };
export type Highlight = { nodes: Set<string>; edges: Set<string> } | null;
export type Panel = 'keywords' | 'relations';
type Selection = { type: 'keyword' | 'relation' | 'document'; id: string } | null;

const LIMITS = [30, 60, 120, 300];
// The keyword and relation lists show this many rows, and ‘더 보기’ as many more.
const PAGE = 100;
const clip = (value: string, size: number) =>
  value.length > size ? `${value.slice(0, size - 1)}…` : value;
export const keywordRadius = (count: number) => 9 + Math.sqrt(count) * 4;

// Graph nodes are prefixed by kind; a keyword and a document can never collide.
function selectionOf(nodeId: string | null): Selection {
  if (!nodeId) return null;
  return { type: nodeId.startsWith('k:') ? 'keyword' : 'document', id: nodeId.slice(2) };
}

// What a selection lights up among the drawn edges; documents are among them only while drawn.
function highlightOf(target: Selection, graph: Graph, edges: GraphEdge[]): Highlight {
  if (!target) return null;
  const nodes = new Set<string>();
  const lit = new Set<string>();
  const connect = (edge: GraphEdge) => {
    lit.add(edge.id);
    nodes.add(edge.source).add(edge.target);
  };
  if (target.type === 'relation') {
    const relation = graph.edges.find((r) => relationId(r) === target.id);
    if (!relation) return null;
    nodes.add(`k:${relation.source}`).add(`k:${relation.target}`);
    lit.add(target.id);
    const sources = new Set(
      relation.docs.flatMap((doc) => [
        membershipId(doc, relation.source),
        membershipId(doc, relation.target),
      ]),
    );
    edges.filter((edge) => sources.has(edge.id)).forEach(connect);
    return { nodes, edges: lit };
  }
  const id = `${target.type === 'keyword' ? 'k' : 'd'}:${target.id}`;
  nodes.add(id);
  for (const edge of edges) if (edge.source === id || edge.target === id) connect(edge);
  return { nodes, edges: lit };
}

// The graph loads again with every document change, often with the same content: the model of the last load is kept
// while nothing drawn changed, so nothing is laid out or drawn again.
function useModel(graph: Graph, limit: number, docs: boolean) {
  const fresh = useMemo(() => graphModel(graph, limit, docs), [graph, limit, docs]);
  return useMemo(() => fresh, [fresh.key]);
}

// The layout is worked out again only when the drawn nodes or their links change, and then from where the nodes
// were, so the graph keeps its shape while documents are analyzed one by one. The last few layouts are kept by what
// they laid out: going back to one (documents hidden again) finds it as it was.
function useLayout(model: GraphModel, dimensions: 2 | 3, active: boolean) {
  const recent = useRef(new Map<string, Map<string, Point>>());
  const positions = useMemo(() => {
    if (!active) return null;
    const kept = recent.current.get(model.layoutKey);
    if (kept) return kept;
    const last = [...recent.current.values()].at(-1);
    return forceLayout(model.ids, model.links, dimensions, last);
  }, [model, dimensions, active]);
  useEffect(() => {
    if (!positions) return;
    // The latest last, four at most.
    const layouts = recent.current;
    layouts.delete(model.layoutKey);
    layouts.set(model.layoutKey, positions);
    for (const key of layouts.keys()) if (layouts.size > 4) layouts.delete(key);
  }, [model, positions]);
  return positions;
}

export default function KnowledgeExplorer({
  graph,
  panel,
  onPanel,
  onKeyword,
  onOpenDocument,
}: {
  graph: Graph;
  panel: Panel;
  onPanel: (panel: Panel) => void;
  onKeyword: (keyword: string) => void;
  onOpenDocument: (id: string) => void;
}) {
  const [mode, setMode] = useState<'2d' | '3d'>('2d');
  const [limit, setLimit] = useState(60);
  const [docs, setDocs] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [hover, setHover] = useState<string | null>(null);
  const model = useModel(graph, limit, docs);
  const flat = useLayout(model, 2, mode === '2d');
  const deep = useLayout(model, 3, mode === '3d');
  const highlight = useMemo(
    () => highlightOf(selectionOf(hover) || selection, graph, model.edges),
    [hover, selection, graph, model.edges],
  );
  const selectedNode =
    selection && selection.type !== 'relation'
      ? `${selection.type === 'keyword' ? 'k' : 'd'}:${selection.id}`
      : null;
  const pick = (nodeId: string | null) => {
    const next = selectionOf(nodeId);
    setSelection(next);
    if (next?.type === 'keyword') onPanel('keywords');
  };
  if (!graph.nodes.length)
    return (
      <div className="empty">
        <div className="empty-icon">
          <Network size={30} />
        </div>
        <h3>지식의 연결이 시작되는 곳</h3>
        <p>
          문서를 저장하고 AI 분석을 실행하면
          <br />
          키워드 사이의 관계가 이곳에 나타납니다.
        </p>
      </div>
    );
  const props = {
    nodes: model.nodes,
    edges: model.edges,
    highlight,
    selected: selectedNode,
    hover,
    onSelect: pick,
    onHover: setHover,
  };
  return (
    <div className="knowledge-explorer">
      <div className="explorer-toolbar">
        <div className="segmented" role="radiogroup" aria-label="관계도 보기 방식">
          {(
            [
              ['2d', '2D 그래프', Network],
              ['3d', '3D 그래프', Box],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              role="radio"
              aria-checked={mode === value}
              tabIndex={mode === value ? 0 : -1}
              className={mode === value ? 'active' : ''}
              onClick={() => setMode(value)}
              onKeyDown={rove}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        <label className="explorer-select">
          표시할 키워드
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {LIMITS.filter((n, i) => i === 0 || LIMITS[i - 1] < graph.nodes.length).map((n) => (
              <option key={n} value={n}>
                {n >= graph.nodes.length ? `전체 ${graph.nodes.length}개` : `상위 ${n}개`}
              </option>
            ))}
          </select>
        </label>
        <label className="explorer-toggle">
          <input type="checkbox" checked={docs} onChange={(e) => setDocs(e.target.checked)} />
          문서 함께 보기
        </label>
        <div className="explorer-legend" aria-hidden="true">
          <span>
            <i className="key-keyword" />
            키워드
          </span>
          {docs && (
            <span>
              <i className="key-document" />
              문서
            </span>
          )}
          <span>
            <i className="key-relation" />
            관계
          </span>
        </div>
      </div>
      <div className="explorer-body">
        <div className="explorer-canvas">
          {mode === '2d'
            ? flat && <Graph2D {...props} positions={flat} scope={`${limit}:${docs}`} />
            : deep && (
                <Graph3D
                  {...props}
                  positions={deep}
                  label={`3D 지식 그래프 · 키워드 ${Math.min(limit, graph.nodes.length)}개`}
                />
              )}
          <p className="explorer-hint">
            {mode === '2d'
              ? '드래그로 이동 · 휠로 확대 · 키워드를 눌러 연결 보기'
              : '드래그로 회전 · 휠로 확대 · 키워드를 눌러 연결 보기'}
            {model.omitted > 0 && ` · 문서는 키워드가 많은 ${DOCUMENT_LIMIT}개만 표시`}
          </p>
        </div>
        <ExplorerPanel
          graph={graph}
          panel={panel}
          onPanel={onPanel}
          selection={selection}
          onSelect={setSelection}
          onKeyword={onKeyword}
          onOpenDocument={onOpenDocument}
          shown={limit}
        />
      </div>
    </div>
  );
}

type RendererProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  positions: Map<string, Point>;
  highlight: Highlight;
  selected: string | null;
  hover: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
};
// Where the overlap pass put a node, against where the layout had it.
type Spot = { layout: Point; dx: number; dy: number };

const WIDTH = 880;
const HEIGHT = 560;

function Graph2D({
  nodes,
  edges,
  positions,
  highlight,
  selected,
  hover,
  onSelect,
  onHover,
  scope,
}: RendererProps & { scope: string }) {
  const svg = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const pan = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  // The last placement of each choice of keywords and documents (`scope`): a node the layout left nearly in place
  // starts where the overlap pass put it then, as pushing crowded nodes apart afresh shuffled them on every reload.
  const placements = useRef(new Map<string, Map<string, Spot>>());
  const placed = useMemo(() => {
    const points = nodes.map((n) => positions.get(n.id)!);
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const [minX, maxX, minY, maxY] = [
      Math.min(...xs),
      Math.max(...xs),
      Math.min(...ys),
      Math.max(...ys),
    ];
    const scale = Math.min(
      (WIDTH - 140) / Math.max(1, maxX - minX),
      (HEIGHT - 120) / Math.max(1, maxY - minY),
      1.6,
    );
    const before = placements.current.get(scope) ?? new Map<string, Spot>();
    const fitted = nodes.map((n, i) => ({
      x: WIDTH / 2 + (points[i].x - (minX + maxX) / 2) * scale,
      y: HEIGHT / 2 + (points[i].y - (minY + maxY) / 2) * scale,
    }));
    const items = nodes.map((n, i) => {
      const spot = before.get(n.id);
      const kept =
        spot && Math.hypot(spot.layout.x - points[i].x, spot.layout.y - points[i].y) < SPACING;
      return {
        x: fitted[i].x + (kept ? spot.dx : 0),
        y: fitted[i].y + (kept ? spot.dy : 0),
        // Keywords keep room for the label drawn under them.
        r: n.kind === 'keyword' ? keywordRadius(n.count) + 9 : 10,
      };
    });
    // Fitting a dense graph can still overlap nodes; push overlapping pairs apart.
    separate(items);
    const spots = new Map(
      nodes.map((n, i) => [
        n.id,
        { layout: points[i], dx: items[i].x - fitted[i].x, dy: items[i].y - fitted[i].y },
      ]),
    );
    return { scope, spots, at: new Map(nodes.map((n, i) => [n.id, items[i]])) };
  }, [nodes, positions, scope]);
  useEffect(() => {
    placements.current.set(placed.scope, placed.spots);
  }, [placed]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  // React's wheel listener is passive, so zoom needs a native one that can stop page scrolling.
  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const px = ((event.clientX - rect.left) * WIDTH) / rect.width;
      const py = ((event.clientY - rect.top) * HEIGHT) / rect.height;
      setView((v) => {
        const k = Math.min(4, Math.max(0.4, v.k * Math.exp(-event.deltaY * 0.0015)));
        return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
      });
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  }, []);
  const zoom = (factor: number) =>
    setView((v) => {
      const k = Math.min(4, Math.max(0.4, v.k * factor));
      return {
        k,
        x: WIDTH / 2 - ((WIDTH / 2 - v.x) * k) / v.k,
        y: HEIGHT / 2 - ((HEIGHT / 2 - v.y) * k) / v.k,
      };
    });
  const dim = (id: string, set?: Set<string>) => !!highlight && !set?.has(id);
  const nodeEvents = (node: GraphNode) => ({
    role: 'button',
    tabIndex: 0,
    onClick: () => onSelect(node.id === selected ? null : node.id),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      onSelect(node.id === selected ? null : node.id);
    },
    onPointerEnter: () => onHover(node.id),
    onPointerLeave: () => onHover(null),
    onFocus: () => onHover(node.id),
    onBlur: () => onHover(null),
  });
  return (
    <div className="graph-2d">
      <svg
        ref={svg}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-label="2D 지식 그래프"
        onPointerDown={(e) => {
          if ((e.target as Element).closest('.graph-node, .graph-doc')) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          pan.current = { x: e.clientX, y: e.clientY, moved: false };
        }}
        onPointerMove={(e) => {
          const start = pan.current;
          if (!start) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const dx = ((e.clientX - start.x) * WIDTH) / rect.width;
          const dy = ((e.clientY - start.y) * HEIGHT) / rect.height;
          if (!start.moved && Math.hypot(dx, dy) < 3) return;
          start.moved = true;
          start.x = e.clientX;
          start.y = e.clientY;
          setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
        }}
        onPointerUp={() => {
          if (pan.current && !pan.current.moved) onSelect(null);
          pan.current = null;
        }}
      >
        <defs>
          <pattern id="graph-dots" width="24" height="24" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="1" fill="#dde2e8" />
          </pattern>
          {['arrow', 'arrow-lit'].map((id) => (
            <marker
              key={id}
              id={id}
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
              markerUnits="userSpaceOnUse"
            >
              <path d="M0,0 L8,4 L0,8 Z" fill={id === 'arrow' ? '#b8c2cf' : '#1c2433'} />
            </marker>
          ))}
        </defs>
        <rect width={WIDTH} height={HEIGHT} fill="url(#graph-dots)" />
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {edges.map((edge) => {
            const a = placed.at.get(edge.source);
            const b = placed.at.get(edge.target);
            if (!a || !b) return null;
            const lit = !!highlight?.edges.has(edge.id);
            if (edge.kind === 'membership')
              return (
                <line
                  key={edge.id}
                  className={`graph-link member${lit ? ' lit' : ''}`}
                  opacity={dim(edge.id, highlight?.edges) ? 0.12 : 1}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                />
              );
            // End at the target's rim so the arrowhead stays visible.
            const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
            const rim = keywordRadius(byId.get(edge.target)?.count || 1) + 2;
            const x2 = b.x - ((b.x - a.x) / length) * rim;
            const y2 = b.y - ((b.y - a.y) / length) * rim;
            return (
              <g key={edge.id} opacity={dim(edge.id, highlight?.edges) ? 0.1 : 1}>
                <line
                  className={`graph-link${lit ? ' lit' : ''}`}
                  x1={a.x}
                  y1={a.y}
                  x2={x2}
                  y2={y2}
                  markerEnd={`url(#${lit ? 'arrow-lit' : 'arrow'})`}
                />
                {lit && (
                  <text className="edge-label" x={(a.x + x2) / 2} y={(a.y + y2) / 2 - 6}>
                    {edge.label}
                  </text>
                )}
                <title>
                  {edge.source.slice(2)} → {edge.label} → {edge.target.slice(2)}
                </title>
              </g>
            );
          })}
          {nodes.map((node) => {
            const p = placed.at.get(node.id)!;
            const active = node.id === selected || node.id === hover;
            if (node.kind === 'document')
              return (
                <g
                  key={node.id}
                  {...nodeEvents(node)}
                  aria-label={`${node.label} 문서`}
                  className={`graph-doc${active ? ' active' : ''}`}
                  transform={`translate(${p.x},${p.y})`}
                  opacity={dim(node.id, highlight?.nodes) ? 0.2 : 1}
                >
                  <rect x={-7} y={-7} width={14} height={14} rx={3.5} />
                  {(active || highlight?.nodes.has(node.id)) && (
                    <text y={23}>{clip(node.label, 16)}</text>
                  )}
                  <title>{node.label}</title>
                </g>
              );
            const r = keywordRadius(node.count);
            return (
              <g
                key={node.id}
                {...nodeEvents(node)}
                aria-label={`${node.label} 키워드 · 문서 ${node.count}개`}
                className={`graph-node${active ? ' active' : ''}`}
                transform={`translate(${p.x},${p.y})`}
                opacity={dim(node.id, highlight?.nodes) ? 0.22 : 1}
              >
                <circle className="halo" r={r} />
                <circle className="core" r={3.5} />
                <text y={r + 13}>{clip(node.label, 18)}</text>
                <title>
                  {node.label} · 문서 {node.count}개
                </title>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="zoom-controls graph-zoom">
        <button aria-label="축소" onClick={() => zoom(1 / 1.25)}>
          <Minus size={16} />
        </button>
        <button aria-label="배율 초기화" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
          <Focus size={16} />
        </button>
        <button aria-label="확대" onClick={() => zoom(1.25)}>
          <Plus size={16} />
        </button>
      </div>
    </div>
  );
}

// The keyword and relation lists with the selection's details; hovering the graph leaves them be.
const ExplorerPanel = memo(function ExplorerPanel({
  graph,
  panel,
  onPanel,
  selection,
  onSelect,
  onKeyword,
  onOpenDocument,
  shown,
}: {
  graph: Graph;
  panel: Panel;
  onPanel: (panel: Panel) => void;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onKeyword: (keyword: string) => void;
  onOpenDocument: (id: string) => void;
  shown: number;
}) {
  const [filter, setFilter] = useState('');
  const titles = useMemo(() => new Map(graph.documents.map((d) => [d.id, d.title])), [graph]);
  const rank = useMemo(() => new Map(graph.nodes.map((n, i) => [n.id, i])), [graph]);
  const needle = filter.trim().toLowerCase();
  const keywords = useMemo(
    () => graph.nodes.filter((n) => n.label.toLowerCase().includes(needle)),
    [graph, needle],
  );
  const relations = useMemo(
    () =>
      graph.edges.filter((r) =>
        `${r.source} ${r.label} ${r.target}`.toLowerCase().includes(needle),
      ),
    [graph, needle],
  );
  const items = panel === 'keywords' ? keywords : relations;
  // Up to a thousand keywords and every relation: the list grows by ‘더 보기’, and starts over with another tab or search.
  const scope = `${panel}\u001f${needle}`;
  const [more, setMore] = useState({ scope, size: PAGE });
  const size = more.scope === scope ? more.size : PAGE;
  const documentList = (ids: string[]) => (
    <div className="detail-section">
      <h4>문서 {ids.length}개</h4>
      {ids.slice(0, 8).map((id) => (
        <button key={id} className="detail-link" onClick={() => onOpenDocument(id)}>
          <FileText size={13} />
          <span>{titles.get(id) || '제목 없는 문서'}</span>
        </button>
      ))}
      {ids.length > 8 && <small>외 {ids.length - 8}개</small>}
    </div>
  );
  let detail: React.ReactNode = null;
  if (selection?.type === 'keyword') {
    const keyword = graph.nodes.find((n) => n.id === selection.id);
    const linked = graph.edges.filter(
      (r) => r.source === selection.id || r.target === selection.id,
    );
    if (keyword)
      detail = (
        <>
          <span className="detail-kind">
            <Hash size={12} />
            키워드
          </span>
          <h3>{keyword.label}</h3>
          <p>
            문서 {keyword.count}개 · 관계 {linked.length}개
            {(rank.get(keyword.id) ?? 0) >= shown && ' · 그래프에 표시되지 않은 키워드'}
          </p>
          <button className="button secondary small" onClick={() => onKeyword(keyword.id)}>
            <Search size={14} />
            관련 문서 검색
          </button>
          {linked.length > 0 && (
            <div className="detail-section">
              <h4>관계 {linked.length}개</h4>
              {linked.slice(0, 8).map((r) => (
                <button
                  key={relationId(r)}
                  className="detail-link"
                  onClick={() => onSelect({ type: 'relation', id: relationId(r) })}
                >
                  <Waypoints size={13} />
                  <span>
                    {r.source} → {r.label} → {r.target}
                  </span>
                </button>
              ))}
            </div>
          )}
          {documentList(keyword.docs)}
        </>
      );
  } else if (selection?.type === 'relation') {
    const relation = graph.edges.find((r) => relationId(r) === selection.id);
    if (relation)
      detail = (
        <>
          <span className="detail-kind">
            <Waypoints size={12} />
            관계
          </span>
          <div className="relation-path">
            {[relation.source, relation.target].map((key, i) => (
              <span key={key}>
                {i === 1 && (
                  <span className="relation-label">
                    <ArrowRight size={13} />
                    {relation.label}
                    <ArrowRight size={13} />
                  </span>
                )}
                <button
                  className="tag"
                  onClick={() => onSelect({ type: 'keyword', id: key })}
                  aria-label={`${key} 키워드 보기`}
                >
                  # {key}
                </button>
              </span>
            ))}
          </div>
          {documentList(relation.docs)}
        </>
      );
  } else if (selection?.type === 'document') {
    const keys = graph.nodes.filter((n) => n.docs.includes(selection.id));
    detail = (
      <>
        <span className="detail-kind">
          <FileText size={12} />
          문서
        </span>
        <h3>{titles.get(selection.id) || '제목 없는 문서'}</h3>
        <div className="tags">
          {keys.map((k) => (
            <button
              key={k.id}
              className="tag"
              onClick={() => onSelect({ type: 'keyword', id: k.id })}
            >
              # {k.label}
            </button>
          ))}
        </div>
        <button className="button secondary small" onClick={() => onOpenDocument(selection.id)}>
          <FileText size={14} />
          문서 열기
        </button>
      </>
    );
  }
  return (
    <aside className="explorer-panel" aria-label="지식 목록">
      {detail && (
        <section className="explorer-detail" aria-live="polite">
          <button
            className="icon-button detail-close"
            aria-label="선택 해제"
            onClick={() => onSelect(null)}
          >
            <X size={15} />
          </button>
          {detail}
        </section>
      )}
      <div className="panel-tabs" role="tablist" aria-label="지식 목록 종류">
        {(
          [
            ['keywords', '지식 키워드', graph.total_keywords, Hash],
            ['relations', '발견한 관계', graph.total_relations, Waypoints],
          ] as const
        ).map(([value, label, count, Icon]) => (
          <button
            key={value}
            role="tab"
            aria-selected={panel === value}
            tabIndex={panel === value ? 0 : -1}
            className={panel === value ? 'active' : ''}
            onClick={() => onPanel(value)}
            onKeyDown={rove}
          >
            <Icon size={14} />
            {label}
            <span>{count}</span>
          </button>
        ))}
      </div>
      <label className="panel-filter">
        <Search size={14} />
        <input
          aria-label={panel === 'keywords' ? '키워드 찾기' : '관계 찾기'}
          placeholder={panel === 'keywords' ? '키워드 찾기' : '키워드나 관계 이름으로 찾기'}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </label>
      <div className="panel-list" role="tabpanel">
        {panel === 'keywords'
          ? keywords.slice(0, size).map((k) => (
              <button
                key={k.id}
                className={
                  selection?.type === 'keyword' && selection.id === k.id ? 'active' : undefined
                }
                onClick={() => onSelect({ type: 'keyword', id: k.id })}
              >
                <span># {k.label}</span>
                <small>문서 {k.count}</small>
              </button>
            ))
          : relations.slice(0, size).map((r) => (
              <button
                key={relationId(r)}
                className={
                  selection?.type === 'relation' && selection.id === relationId(r)
                    ? 'active relation-row'
                    : 'relation-row'
                }
                onClick={() => onSelect({ type: 'relation', id: relationId(r) })}
              >
                <span>
                  <strong>{r.source}</strong> → {r.label} → <strong>{r.target}</strong>
                </span>
                <small>문서 {r.count}</small>
              </button>
            ))}
        {items.length > size && (
          <button className="panel-more" onClick={() => setMore({ scope, size: size + PAGE })}>
            <span>더 보기</span>
            <small>남은 {(items.length - size).toLocaleString()}개</small>
          </button>
        )}
        {items.length === 0 && (
          <p className="panel-empty">
            {filter
              ? '찾는 항목이 없어요.'
              : panel === 'keywords'
                ? '문서를 AI로 분석하면 키워드가 쌓입니다.'
                : '문서를 AI로 분석하면 키워드 사이의 관계가 쌓입니다.'}
          </p>
        )}
        {graph.nodes.length < graph.total_keywords && panel === 'keywords' && !filter && (
          <small className="panel-note">
            자주 나온 키워드 {graph.nodes.length}개까지 보여줍니다.
          </small>
        )}
      </div>
    </aside>
  );
});
