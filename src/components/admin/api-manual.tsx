import { useEffect, useState } from 'react';
import { LoaderCircle, Search } from 'lucide-react';
import { api, type ApiManual, type ApiParam } from '@/lib/api';
import { useApp } from '../app';

const SCOPES: Record<string, string> = {
  public: '공개',
  read: '조회',
  write: '변경',
  control: '앱 제어',
  files: '파일 가져오기',
  admin: '설정',
  varies: '메서드마다',
};

function Params({ params }: { params: ApiParam[] }) {
  if (!params.length) return null;
  return (
    <ul className="api-params">
      {params.map((param) => (
        <li key={param.name}>
          <code>{param.name}</code>{' '}
          <small>
            {param.in === 'argument' ? '' : `${param.in} · `}
            {param.type}
            {param.required && ' · 필수'}
          </small>{' '}
          {param.description}
          {param.enum && <small> ({param.enum.join(' · ')})</small>}
          {param.default !== undefined && <small> 기본 {JSON.stringify(param.default)}</small>}
        </li>
      ))}
    </ul>
  );
}

function Scope({ scope, slow }: { scope: string; slow?: boolean }) {
  return (
    <>
      <span className={`api-scope ${scope}`}>{SCOPES[scope] ?? scope}</span>
      {slow && <span className="api-scope slow">느림</span>}
    </>
  );
}

// The manual of the local API, from the same document GET /api/v1/manual serves, so the page and the endpoint
// always agree.
export default function ApiManualSection() {
  const { notify } = useApp();
  const [doc, setDoc] = useState<ApiManual | null>(null);
  const [filter, setFilter] = useState('');
  useEffect(() => {
    api<ApiManual>('api.manual')
      .then(setDoc)
      .catch((error) => notify(error.message, true));
  }, [notify]);
  if (!doc)
    return (
      <p className="muted">
        <LoaderCircle className="spin" size={15} /> 매뉴얼을 불러오고 있습니다.
      </p>
    );
  const query = filter.trim().toLowerCase();
  const matches = (...values: (string | null | undefined)[]) =>
    !query || values.some((value) => value?.toLowerCase().includes(query));
  const groups = doc.groups
    .map((group) => ({
      ...group,
      routes: group.routes.filter((route) =>
        matches(route.path, route.summary, route.method, route.verb, group.title),
      ),
    }))
    .filter((group) => group.routes.length);
  const tools = doc.mcp.tools.filter((tool) => matches(tool.name, tool.title, tool.description));
  const methods = doc.methods.filter((method) => matches(method.name, method.summary));
  return (
    <div className="api-manual">
      <p className="section-lead">
        AI 도구와 스크립트가 쓰는 로컬 API입니다. 같은 내용을 웹 페이지{' '}
        <code>{doc.endpoints.docs}</code>, JSON <code>{doc.endpoints.manual}</code>, AI 도구용
        Markdown <code>{doc.endpoints.manual_markdown}</code>, OpenAPI{' '}
        <code>{doc.endpoints.openapi}</code>로도 받을 수 있습니다(토큰 없이).
      </p>
      <dl className="facts">
        <div>
          <dt>REST</dt>
          <dd>
            <code>{doc.endpoints.rest}</code>
          </dd>
        </div>
        <div>
          <dt>MCP</dt>
          <dd>
            <code>{doc.endpoints.mcp}</code> (Streamable HTTP) ·{' '}
            <code>{doc.endpoints.mcp_sse}</code> (HTTP+SSE) · stdio 연결 프로그램
          </dd>
        </div>
        <div>
          <dt>인증</dt>
          <dd>
            <code>{doc.authentication.header}</code> 또는{' '}
            <code>{doc.authentication.alternative}</code>
          </dd>
        </div>
        <div>
          <dt>응답</dt>
          <dd>
            <code>{doc.responses.success}</code>
            <br />
            <code>{doc.responses.failure}</code>
          </dd>
        </div>
        <div>
          <dt>보안</dt>
          <dd>{doc.authentication.network}</dd>
        </div>
      </dl>
      <ul className="guide">
        {doc.responses.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
      <h3>허용 범위</h3>
      <dl className="facts">
        {doc.access_levels.map((level) => (
          <div key={level.id}>
            <dt>
              {level.label}
              {doc.access === level.id && ' · 현재'}
            </dt>
            <dd>{level.description}</dd>
          </div>
        ))}
      </dl>
      <label className="api-filter">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="API 찾기"
          placeholder="기능, 주소, 메서드나 도구 이름으로 찾기"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
      </label>
      {groups.map((group) => (
        <details className="api-group" key={group.id} open={!!query}>
          <summary>
            <strong>{group.title}</strong> <small>{group.routes.length}개</small>
          </summary>
          <p className="muted">{group.description}</p>
          {group.routes.map((route) => (
            <article className="api-route" key={`${route.verb} ${route.path}`}>
              <header>
                <span className={`api-verb ${route.verb.toLowerCase()}`}>{route.verb}</span>
                <code>{route.path}</code>
                <Scope scope={route.scope} slow={route.slow} />
              </header>
              <p>
                {route.summary}
                {route.method && (
                  <small>
                    {' '}
                    · <code>{route.method}</code>
                  </small>
                )}
              </p>
              <Params params={route.params} />
            </article>
          ))}
        </details>
      ))}
      {tools.length > 0 && (
        <details className="api-group" open={!!query}>
          <summary>
            <strong>MCP 도구</strong> <small>{tools.length}개</small>
          </summary>
          <p className="muted">
            AI 도구가 읽는 설명입니다. 느린 도구는 wait_seconds(기본 50초) 안에 끝나지 않으면
            job_id를 돌려주고, get_result로 결과를 받습니다. 프로토콜{' '}
            {doc.mcp.protocol_versions.join(', ')}.
          </p>
          <ul className="guide">
            {doc.mcp.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
          {tools.map((tool) => (
            <article className="api-route" key={tool.name}>
              <header>
                <code>{tool.name}</code>
                <span>{tool.title}</span>
                <Scope scope={tool.scope} slow={tool.slow} />
              </header>
              <p>{tool.description}</p>
              <Params params={tool.params} />
            </article>
          ))}
          <h4>리소스</h4>
          <ul className="guide">
            {doc.mcp.resources.map((item) => (
              <li key={item.uri}>
                <code>{item.uri}</code> {item.description}
              </li>
            ))}
          </ul>
          <h4>프롬프트</h4>
          <ul className="guide">
            {doc.mcp.prompts.map((item) => (
              <li key={item.name}>
                <code>{item.name}</code> {item.title} — {item.description}
              </li>
            ))}
          </ul>
        </details>
      )}
      {methods.length > 0 && (
        <details className="api-group" open={!!query}>
          <summary>
            <strong>전체 메서드</strong>{' '}
            <small>{methods.length}개 · POST /api/v1/rpc · call_api</small>
          </summary>
          <table className="api-methods">
            <tbody>
              {methods.map((method) => (
                <tr key={method.name}>
                  <td>
                    <code>{method.name}</code>
                  </td>
                  <td>
                    <Scope scope={method.scope} slow={method.slow} />
                  </td>
                  <td>
                    {method.summary}
                    {method.routes.length > 0 && <small> · {method.routes.join(', ')}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {!query && (
        <details className="api-group">
          <summary>
            <strong>오류</strong> <small>{doc.errors.length}가지</small>
          </summary>
          <table className="api-methods">
            <tbody>
              {doc.errors.map((error) => (
                <tr key={error.code}>
                  <td>{error.status}</td>
                  <td>
                    <code>{error.code}</code>
                  </td>
                  <td>{error.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {query && !groups.length && !tools.length && !methods.length && (
        <p className="muted">‘{filter}’에 맞는 항목이 없습니다.</p>
      )}
    </div>
  );
}
