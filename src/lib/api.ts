// What the screens need of the main process and the backend: the shapes of their answers (types), the request itself
// (transport) and the formatting of dates and texts (format), kept apart and gathered here so that every screen
// imports them from '@/lib/api'.
export * from './api/types';
export * from './api/transport';
export * from './api/format';
export type { Method } from './api/methods.generated';
