'use client';
import { useEffect, useRef, useState } from 'react';
import { Focus, Minus, Pause, Play, Plus } from 'lucide-react';
import type { Point } from '@/lib/graph-layout';
import type { GraphEdge, GraphNode, Highlight } from './graph';

const DISTANCE = 3.2; // Camera distance, in radii of the graph's bounding sphere.
// The canvas draws with the theme's graph colors (theme.css), read again when the theme changes.
const COLORS = {
  keywordLight: '--graph-keyword-light',
  keyword: '--graph-keyword',
  documentLight: '--graph-document-light',
  document: '--graph-document',
  member: '--graph-member-3d',
  link: '--graph-link',
  lit: '--ink',
  label: '--ink-2',
  halo: '--graph-label-halo',
};
type Palette = Record<keyof typeof COLORS, string>;
const paletteOf = (element: Element) => {
  const style = getComputedStyle(element);
  return Object.fromEntries(
    Object.entries(COLORS).map(([name, token]) => [name, style.getPropertyValue(token).trim()]),
  ) as Palette;
};

// Whether the screen is dark now, by the theme setting or by Windows (components/theme.ts); it changes when either
// does, and the canvas, which CSS cannot reach, draws again with it.
function useDarkScreen() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const root = document.documentElement;
    const update = () =>
      setDark(root.dataset.theme ? root.dataset.theme === 'dark' : media.matches);
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    media.addEventListener('change', update);
    return () => {
      observer.disconnect();
      media.removeEventListener('change', update);
    };
  }, []);
  return dark;
}

const nodeRadius = (node: GraphNode) =>
  node.kind === 'keyword' ? 6 + Math.sqrt(node.count) * 2.8 : 6;
const clip = (value: string) => (value.length > 18 ? `${value.slice(0, 17)}…` : value);

export default function Graph3D(props: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  positions: Map<string, Point>;
  highlight: Highlight;
  selected: string | null;
  hover: string | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  label: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(props);
  const camera = useRef({ yaw: 0.5, pitch: -0.3, distance: DISTANCE });
  const [spin, setSpin] = useState(
    () => !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  const spinning = useRef(spin);
  const inside = useRef(false);
  const dirty = useRef(true);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number; moved: boolean } | null>(
    null,
  );
  const hits = useRef<{ id: string; x: number; y: number; r: number }[]>([]);
  const dark = useDarkScreen();
  const palette = useRef<Palette | null>(null);
  useEffect(() => {
    latest.current = props;
    dirty.current = true;
  });
  useEffect(() => {
    if (canvas.current) palette.current = paletteOf(canvas.current);
    dirty.current = true;
  }, [dark]);
  useEffect(() => {
    spinning.current = spin;
  }, [spin]);
  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext('2d');
    if (!element || !context) return;
    const ctx = context;
    let width = 0;
    let height = 0;
    let ratio = 1;
    let frame = 0;
    let last = performance.now();
    const font = getComputedStyle(element).fontFamily;
    const resize = () => {
      const rect = element.getBoundingClientRect();
      ratio = window.devicePixelRatio || 1;
      width = rect.width;
      height = rect.height;
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
      dirty.current = true;
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const view = camera.current;
      view.distance = Math.min(8, Math.max(1.6, view.distance * Math.exp(event.deltaY * 0.0012)));
      dirty.current = true;
    };
    element.addEventListener('wheel', onWheel, { passive: false });

    function draw() {
      const { nodes: shown, edges, positions, highlight, selected, hover } = latest.current;
      if (!shown.length) return;
      const colors = (palette.current ??= paletteOf(element!));
      // Center the shown nodes and scale them into a unit sphere.
      const center = { x: 0, y: 0, z: 0 };
      for (const node of shown) {
        const p = positions.get(node.id)!;
        center.x += p.x / shown.length;
        center.y += p.y / shown.length;
        center.z += p.z / shown.length;
      }
      // Scale by the 90th-percentile distance so one far node cannot shrink the rest.
      const distances = shown
        .map((node) => {
          const p = positions.get(node.id)!;
          return Math.hypot(p.x - center.x, p.y - center.y, p.z - center.z);
        })
        .sort((a, b) => a - b);
      const radius = Math.max(1, distances[Math.floor((distances.length - 1) * 0.9)] * 1.15);
      const { yaw, pitch, distance } = camera.current;
      const [cosYaw, sinYaw, cosPitch, sinPitch] = [
        Math.cos(yaw),
        Math.sin(yaw),
        Math.cos(pitch),
        Math.sin(pitch),
      ];
      const focal = Math.min(width, height) * 0.42 * DISTANCE;
      const projected = new Map<string, { x: number; y: number; depth: number; scale: number }>();
      for (const node of shown) {
        const p = positions.get(node.id)!;
        const [x, y, z] = [
          (p.x - center.x) / radius,
          (p.y - center.y) / radius,
          (p.z - center.z) / radius,
        ];
        const x1 = x * cosYaw - z * sinYaw;
        const z1 = x * sinYaw + z * cosYaw;
        const y2 = y * cosPitch - z1 * sinPitch;
        const depth = y * sinPitch + z1 * cosPitch + distance;
        const scale = focal / depth;
        projected.set(node.id, {
          x: width / 2 + x1 * scale,
          y: height / 2 + y2 * scale,
          depth,
          scale: (scale * DISTANCE) / focal,
        });
      }
      // Depth cue: the far side of the sphere fades, so rotation reads as depth.
      const fog = (depth: number) => 1 - 0.6 * Math.min(1, Math.max(0, (depth - distance + 1) / 2));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const lines = edges
        .flatMap((edge) => {
          const a = projected.get(edge.source);
          const b = projected.get(edge.target);
          return a && b ? [{ edge, a, b, depth: (a.depth + b.depth) / 2 }] : [];
        })
        .sort((p, q) => q.depth - p.depth);
      for (const { edge, a, b, depth } of lines) {
        const lit = !!highlight?.edges.has(edge.id);
        const alpha = highlight && !lit ? 0.07 : edge.kind === 'membership' ? 0.45 : 0.75;
        ctx.globalAlpha = alpha * fog(depth);
        ctx.strokeStyle = lit
          ? colors.lit
          : edge.kind === 'membership'
            ? colors.member
            : colors.link;
        ctx.lineWidth = lit ? 2 : edge.kind === 'membership' ? 0.8 : 1.3;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      // Back to front, so nearer nodes cover farther ones.
      const order = shown
        .map((node) => {
          const p = projected.get(node.id)!;
          return { node, p, r: nodeRadius(node) * p.scale };
        })
        .sort((a, b) => b.p.depth - a.p.depth);
      hits.current = [];
      for (const { node, p, r } of order) {
        const faded = highlight && !highlight.nodes.has(node.id);
        ctx.globalAlpha = (faded ? 0.16 : 1) * fog(p.depth);
        const [light, base] =
          node.kind === 'keyword'
            ? [colors.keywordLight, colors.keyword]
            : [colors.documentLight, colors.document];
        const shade = ctx.createRadialGradient(p.x - r * 0.4, p.y - r * 0.4, r * 0.15, p.x, p.y, r);
        shade.addColorStop(0, light);
        shade.addColorStop(1, base);
        ctx.fillStyle = shade;
        ctx.beginPath();
        if (node.kind === 'document') ctx.roundRect(p.x - r, p.y - r, r * 2, r * 2, r * 0.35);
        else ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = colors.halo;
        ctx.stroke();
        if (node.id === selected || node.id === hover) {
          ctx.globalAlpha = 1;
          ctx.lineWidth = 2;
          ctx.strokeStyle = colors.lit;
          ctx.beginPath();
          ctx.arc(p.x, p.y, r + 4, 0, Math.PI * 2);
          ctx.stroke();
        }
        hits.current.push({ id: node.id, x: p.x, y: p.y, r: Math.max(r + 3, 9) });
      }
      // Labels last: the nearer half of the keywords, or the highlighted neighborhood.
      ctx.font = `600 11px ${font}`;
      ctx.textAlign = 'center';
      ctx.lineJoin = 'round';
      const labelled = order.filter(
        ({ node, p }) =>
          node.id === selected ||
          node.id === hover ||
          (highlight
            ? highlight.nodes.has(node.id)
            : node.kind === 'keyword' && p.depth < distance),
      );
      for (const { node, p, r } of labelled.slice(-40)) {
        const text = clip(node.label);
        ctx.globalAlpha = Math.max(0.4, fog(p.depth));
        ctx.lineWidth = 4;
        ctx.strokeStyle = colors.halo;
        ctx.strokeText(text, p.x, p.y + r + 13);
        ctx.fillStyle = colors.label;
        ctx.fillText(text, p.x, p.y + r + 13);
      }
      ctx.globalAlpha = 1;
    }

    const loop = (now: number) => {
      const elapsed = Math.min(64, now - last) / 1000;
      last = now;
      // Hold still under the pointer so nodes stay clickable.
      if (spinning.current && !drag.current && !inside.current) {
        camera.current.yaw += elapsed * 0.22;
        dirty.current = true;
      }
      if (dirty.current && width > 0) {
        dirty.current = false;
        draw();
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      element.removeEventListener('wheel', onWheel);
    };
  }, []);

  const hitAt = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const [x, y] = [event.clientX - rect.left, event.clientY - rect.top];
    // Nearest nodes were drawn last and win overlapping hits.
    for (let i = hits.current.length - 1; i >= 0; i--) {
      const hit = hits.current[i];
      if (Math.hypot(hit.x - x, hit.y - y) <= hit.r) return hit.id;
    }
    return null;
  };
  const turn = (yaw: number, pitch: number, zoom = 1) => {
    const view = camera.current;
    view.yaw += yaw;
    view.pitch = Math.min(1.4, Math.max(-1.4, view.pitch + pitch));
    view.distance = Math.min(8, Math.max(1.6, view.distance * zoom));
    dirty.current = true;
  };
  return (
    <div className="graph-3d">
      <canvas
        ref={canvas}
        role="img"
        aria-label={props.label}
        tabIndex={0}
        onPointerEnter={() => (inside.current = true)}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          const { yaw, pitch } = camera.current;
          drag.current = { x: e.clientX, y: e.clientY, yaw, pitch, moved: false };
        }}
        onPointerMove={(e) => {
          const start = drag.current;
          if (start) {
            const [dx, dy] = [e.clientX - start.x, e.clientY - start.y];
            if (!start.moved && Math.hypot(dx, dy) < 4) return;
            start.moved = true;
            camera.current.yaw = start.yaw + dx * 0.008;
            camera.current.pitch = Math.min(1.4, Math.max(-1.4, start.pitch + dy * 0.008));
            dirty.current = true;
            return;
          }
          const id = hitAt(e);
          if (id !== latest.current.hover) latest.current.onHover(id);
          e.currentTarget.style.cursor = id ? 'pointer' : 'grab';
        }}
        onPointerUp={(e) => {
          const start = drag.current;
          drag.current = null;
          if (!start || start.moved) return;
          const id = hitAt(e);
          latest.current.onSelect(id === latest.current.selected ? null : id);
        }}
        onPointerLeave={() => {
          inside.current = false;
          if (!drag.current) latest.current.onHover(null);
        }}
        onKeyDown={(e) => {
          const moves: Record<string, [number, number, number?]> = {
            ArrowLeft: [-0.15, 0],
            ArrowRight: [0.15, 0],
            ArrowUp: [0, -0.15],
            ArrowDown: [0, 0.15],
            '+': [0, 0, 0.85],
            '=': [0, 0, 0.85],
            '-': [0, 0, 1.18],
          };
          if (moves[e.key]) {
            e.preventDefault();
            turn(...moves[e.key]);
          } else if (e.key === ' ') {
            e.preventDefault();
            setSpin(!spin);
          } else if (e.key === 'Escape') latest.current.onSelect(null);
        }}
      />
      <div className="zoom-controls graph-zoom">
        <button
          aria-label={spin ? '자동 회전 멈추기' : '자동 회전'}
          aria-pressed={spin}
          onClick={() => setSpin(!spin)}
        >
          {spin ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button aria-label="축소" onClick={() => turn(0, 0, 1.18)}>
          <Minus size={16} />
        </button>
        <button
          aria-label="시점 초기화"
          onClick={() => {
            camera.current = { yaw: 0.5, pitch: -0.3, distance: DISTANCE };
            dirty.current = true;
          }}
        >
          <Focus size={16} />
        </button>
        <button aria-label="확대" onClick={() => turn(0, 0, 0.85)}>
          <Plus size={16} />
        </button>
      </div>
    </div>
  );
}
