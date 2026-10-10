import type { Metadata } from 'next';
import { THEME_SCRIPT } from '@/components/theme';
import './globals.css';

export const metadata: Metadata = {
  title: 'Private Agent · 나만의 업무 파트너',
  description: 'LLM으로 연결하는 나의 프롬프트와 지식',
};
// The saved theme is set on <html> by a script before the first paint, so React leaves that attribute alone.
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
