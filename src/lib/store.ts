import { useRef, useSyncExternalStore } from 'react';

// A value held outside React that components read through a selector, drawing again only when what they selected
// changed. The app's state is a few of these (src/components/state.tsx), so that a job's progress draws the import
// box again rather than every component of the app.
export type Store<T> = {
  get: () => T;
  set: (next: T | ((current: T) => T)) => void;
  subscribe: (listener: () => void) => () => void;
};

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next) {
      const value = typeof next === 'function' ? (next as (current: T) => T)(state) : next;
      if (Object.is(value, state)) return;
      state = value;
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

// What `select` picks of the store's state. useSyncExternalStore wants the same value back while nothing changed, so
// the last value is kept for as long as the state and the selector are the same, and also when a new selector (one
// written inline, made anew with every drawing) picks a value `equal` to it.
export function useStore<T, S>(
  store: Store<T>,
  select: (state: T) => S,
  equal: (a: S, b: S) => boolean = Object.is,
): S {
  const last = useRef<{ state: T; select: (state: T) => S; value: S } | null>(null);
  const snapshot = () => {
    const state = store.get();
    const kept = last.current;
    if (kept && kept.state === state && kept.select === select) return kept.value;
    const picked = select(state);
    const value = kept && equal(kept.value, picked) ? kept.value : picked;
    last.current = { state, select, value };
    return value;
  };
  // The static export draws the page once at build time, from the stores' first state.
  return useSyncExternalStore(store.subscribe, snapshot, snapshot);
}
