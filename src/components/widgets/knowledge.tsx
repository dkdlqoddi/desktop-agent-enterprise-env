import { useEffect, useState } from 'react';
import { api, type Graph } from '@/lib/api';
import { useApp } from '../app';
import { useWidgetFrame, WidgetHead } from '../dashboard';
import KnowledgeExplorer, { type Panel } from '../graph';
import { Bars, Loading } from './parts';

// Analysis and imports change the keywords; the graph loads again with every document change.
function useGraph() {
  const { version, notify } = useApp();
  const [graph, setGraph] = useState<Graph | null>(null);
  useEffect(() => {
    let live = true;
    api<Graph>('wiki.graph')
      .then((data) => live && setGraph(data))
      .catch((error) => live && notify(error.message, true));
    return () => {
      live = false;
    };
  }, [version, notify]);
  return graph;
}

const NO_KEYWORDS = '문서를 AI로 분석하면 키워드가 모여 이곳에 놓입니다.';

export default function KnowledgeWidget() {
  const { stats, searchKeyword, openDocument } = useApp();
  const graph = useGraph();
  const [panel, setPanel] = useState<Panel>('keywords');
  return (
    <>
      <WidgetHead id="knowledge" title="지식 관계도">
        <small className="muted">
          키워드 {stats.keywords} · 관계 {stats.relations}
        </small>
      </WidgetHead>
      {!graph ? (
        <Loading />
      ) : graph.nodes.length ? (
        <KnowledgeExplorer
          graph={graph}
          panel={panel}
          onPanel={setPanel}
          onKeyword={searchKeyword}
          onOpenDocument={(id) => openDocument(id)}
        />
      ) : (
        <p className="widget-note">
          문서를 AI로 분석하면 키워드와 관계가 모여 이곳에 그래프로 그려집니다.
        </p>
      )}
    </>
  );
}

// The keywords most documents share, as bars; a keyword searches for itself.
export function KeywordRanking() {
  const { searchKeyword } = useApp();
  const { size } = useWidgetFrame('knowledge');
  const graph = useGraph();
  const top = [...(graph?.nodes ?? [])]
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
    .slice(0, size === 's' ? 8 : 12);
  return (
    <>
      <WidgetHead count={graph?.nodes.length} />
      {!graph ? (
        <Loading />
      ) : top.length ? (
        <>
          <Bars
            label="키워드별 문서 수"
            items={top.map((node) => ({
              key: node.label,
              label: node.label,
              value: node.count,
              note: `${node.count}개 문서`,
            }))}
            onPick={searchKeyword}
          />
          <small className="widget-foot">키워드를 누르면 그 키워드로 검색합니다.</small>
        </>
      ) : (
        <p className="widget-note">{NO_KEYWORDS}</p>
      )}
    </>
  );
}

// Up to 40 keywords in alphabetical order, larger the more documents share them; a keyword searches for itself.
export function KeywordCloud() {
  const { searchKeyword } = useApp();
  const graph = useGraph();
  const words = [...(graph?.nodes ?? [])]
    .sort((a, b) => b.count - a.count)
    .slice(0, 40)
    .sort((a, b) => a.label.localeCompare(b.label));
  const most = Math.max(1, ...words.map((node) => node.count));
  return (
    <>
      <WidgetHead count={graph?.nodes.length} />
      {!graph ? (
        <Loading />
      ) : words.length ? (
        <ul className="keyword-cloud" aria-label="키워드">
          {words.map((node) => {
            const weight = Math.sqrt(node.count / most);
            return (
              <li key={node.id}>
                <button
                  className={`cloud-word w${Math.round(weight * 4)}`}
                  style={{ fontSize: `${(12 + weight * 14) / 16}rem` }}
                  aria-label={`${node.label}, 문서 ${node.count}개`}
                  onClick={() => searchKeyword(node.label)}
                >
                  {node.label}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="widget-note">{NO_KEYWORDS}</p>
      )}
    </>
  );
}
