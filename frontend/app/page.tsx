'use client';

import dynamic from 'next/dynamic';

const ReaderClient = dynamic(() => import('./reader-client'), { ssr: false });

export default function Home() {
  return <ReaderClient />;
}
