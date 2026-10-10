import { useCallback, useLayoutEffect, useMemo, useState } from 'react';

// How far beyond what the box shows rows are drawn, so a scroll finds them drawn already.
const OVERSCAN = 600;
// The height the box is taken to show before it is measured (the first drawing).
const FIRST_VIEW = 800;

// The rows of a long list in a scrolling box that are drawn: those within OVERSCAN pixels of what the box shows. The
// rows above and below are stood for by the room they take (`before` and `after`, the heights of two spacers), so the
// scrollbar and the place of every row are as if all were drawn. A row's height is measured once it is drawn, and
// estimated until then, so rows of any height (a title that wraps on a narrow window, a question opened in a row) keep
// their places. A list of thousands of rows thus draws a few dozen of them.
//
// The box is the element `ref` is given; the rows are its elements with data-window-key, the key of their item.
export function useWindowing(keys: readonly string[], estimate: number) {
  const [box, setBox] = useState<HTMLElement | null>(null);
  const [view, setView] = useState({ top: 0, height: 0 });
  // The measured heights by key, as React keeps them: a new map when a measure changed.
  const [heights, setHeights] = useState<ReadonlyMap<string, number>>(() => new Map());

  useLayoutEffect(() => {
    if (!box) return;
    const read = () =>
      setView((current) =>
        current.top === box.scrollTop && current.height === box.clientHeight
          ? current
          : { top: box.scrollTop, height: box.clientHeight },
      );
    read();
    box.addEventListener('scroll', read, { passive: true });
    const resized = new ResizeObserver(read);
    resized.observe(box);
    return () => {
      box.removeEventListener('scroll', read);
      resized.disconnect();
    };
  }, [box]);

  // Where each row starts: offsets[i] is the room the rows before row i take, offsets[keys.length] all of them.
  const offsets = useMemo(() => {
    const found = new Float64Array(keys.length + 1);
    for (let i = 0; i < keys.length; i++)
      found[i + 1] = found[i] + (heights.get(keys[i]) ?? estimate);
    return found;
  }, [keys, heights, estimate]);

  const top = view.top - OVERSCAN;
  const bottom = view.top + (view.height || FIRST_VIEW) + OVERSCAN;
  // The first row whose end is past `top`, and the first that starts at or past `bottom`.
  const start = Math.min(Math.max(0, search(offsets, top) - 1), Math.max(0, keys.length - 1));
  const end = Math.max(start, Math.min(keys.length, search(offsets, bottom - 0.5)));

  // The drawn rows' heights, measured after each drawing; a change draws again before the screen shows it.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- after every drawing; it sets heights only when one changed
  useLayoutEffect(() => {
    if (!box) return;
    let changed: Map<string, number> | null = null;
    for (const row of box.querySelectorAll<HTMLElement>('[data-window-key]')) {
      const key = row.dataset.windowKey!;
      const height = row.getBoundingClientRect().height;
      if (height > 0 && Math.abs((heights.get(key) ?? -1) - height) > 0.5)
        (changed ??= new Map(heights)).set(key, height);
    }
    if (changed) setHeights(changed);
  });

  // Scrolls the box just enough that row `index` is in sight.
  const reveal = useCallback(
    (index: number) => {
      if (!box || index < 0 || index >= keys.length) return;
      const from = offsets[index];
      const to = offsets[index + 1];
      if (from < box.scrollTop) box.scrollTop = from;
      else if (to > box.scrollTop + box.clientHeight) box.scrollTop = to - box.clientHeight;
    },
    [box, keys.length, offsets],
  );

  return {
    ref: setBox,
    box,
    start,
    end,
    before: offsets[start] ?? 0,
    after: (offsets[keys.length] ?? 0) - (offsets[end] ?? 0),
    // How many rows the box shows at once, for Page Up and Page Down.
    page: Math.max(1, Math.floor((view.height || FIRST_VIEW) / estimate) - 1),
    reveal,
  };
}

// The first index whose offset is greater than `value` (offsets ascend).
function search(offsets: Float64Array, value: number) {
  let low = 0;
  let high = offsets.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (offsets[middle] > value) high = middle;
    else low = middle + 1;
  }
  return low;
}
