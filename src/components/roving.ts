// The arrows that move through a radio group, and through a tab list of each orientation.
const STEPS: Record<string, Record<string, number>> = {
  radiogroup: { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 },
  horizontal: { ArrowRight: 1, ArrowLeft: -1 },
  vertical: { ArrowDown: 1, ArrowUp: -1 },
};

// Arrow keys move between the options of a radio group or the tabs of a tab list and choose the one they reach, and
// Home and End go to the first and the last, as the WAI-ARIA patterns have it. Only the chosen option takes Tab focus
// (tabIndex 0, the others -1), so Tab leaves the group in one step.
export function rove(event: React.KeyboardEvent<HTMLElement>) {
  const item = event.currentTarget;
  const group = item.parentElement?.closest('[role="radiogroup"], [role="tablist"]');
  if (!group) return;
  const items = [...group.querySelectorAll<HTMLElement>('[role="radio"], [role="tab"]')].filter(
    (option) => !option.matches(':disabled'),
  );
  const kind =
    group.getAttribute('role') === 'radiogroup'
      ? 'radiogroup'
      : group.getAttribute('aria-orientation') === 'vertical'
        ? 'vertical'
        : 'horizontal';
  const step = STEPS[kind][event.key];
  const index = items.indexOf(item);
  const target =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : step
          ? (index + step + items.length) % items.length
          : -1;
  if (index < 0 || target < 0) return;
  event.preventDefault();
  items[target].focus();
  if (target !== index) items[target].click();
}
