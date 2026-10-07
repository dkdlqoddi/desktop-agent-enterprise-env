import type { LucideIcon } from 'lucide-react';

export default function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon">
        <Icon size={24} strokeWidth={1.4} />
      </span>
      <strong>{title}</strong>
      <p>{children}</p>
      {action}
    </div>
  );
}
