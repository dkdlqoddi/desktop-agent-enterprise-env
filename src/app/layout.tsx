import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Private Agent · 나만의 업무 파트너',
  description: 'LLM으로 연결하는 나의 프롬프트와 지식',
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
