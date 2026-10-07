import type { Briefing, Graph, RunStats, SitesState, SiteWindows } from './api';

// Browser previews expose empty read-only collections. All writes and AI requests still
// require the desktop bridge; no preview data is presented as saved user data.
export function previewRead(method: string): unknown {
  switch (method) {
    case 'tasks.list':
    case 'wiki.list':
    case 'prompts.list':
    case 'assistant.conversations':
    case 'schedules.list':
    case 'runs.list':
    case 'runs.active':
    case 'routines.list':
    case 'routines.catalog':
    case 'routines.runs':
      return [];
    case 'briefing.get':
      return {
        generated_at: new Date().toISOString(),
        local_date: '',
        counts: { open: 0, done: 0, paused: 0 },
        overdue: [],
        today: [],
        upcoming: [],
        unscheduled: [],
        completed_today: [],
        reminders: [],
        recent_documents: [],
      } satisfies Briefing;
    case 'reminders.status':
      return { supported: true, error: '' };
    case 'wiki.graph':
      return {
        nodes: [],
        edges: [],
        documents: [],
        total_keywords: 0,
        total_relations: 0,
      } satisfies Graph;
    case 'runs.stats':
      return {
        days: [],
        totals: { total: 0, success: 0, error: 0, scheduled: 0, average_ms: null },
        active_schedules: 0,
        running: [],
      } satisfies RunStats;
    case 'sites.list':
      return { sites: [], methods: [], secure_storage: false, error: '' } satisfies SitesState;
    case 'sites.status':
      return {
        jira: { site_id: null, open: false, error: '', checking: null },
        confluence: { site_id: null, open: false, error: '', checking: null },
      } satisfies SiteWindows;
    default:
      throw new Error(
        '이 기능은 데스크톱 앱에서 사용할 수 있어요. 브라우저에서는 화면만 미리 볼 수 있습니다.',
      );
  }
}
