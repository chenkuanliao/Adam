const INTERNAL_API_BASE = process.env.ADAM_INTERNAL_API_BASE ?? 'http://localhost:8000';

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const target = `${INTERNAL_API_BASE}/api/${path.join('/')}${incoming.search}`;
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('content-length');

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
    redirect: 'manual',
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: upstream.headers,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
