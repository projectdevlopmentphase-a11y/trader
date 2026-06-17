// Forwards the dashboard's Refresh click to the droplet's /sync endpoint.
// Keeps DROPLET_SYNC_TOKEN server-side — set DROPLET_SYNC_URL and
// DROPLET_SYNC_TOKEN as Pages secrets (Cloudflare dashboard -> Pages
// project -> Settings -> Environment variables), never in client code.
export async function onRequestPost({ env }) {
  if (!env.DROPLET_SYNC_URL || !env.DROPLET_SYNC_TOKEN) {
    return Response.json({ error: "DROPLET_SYNC_URL / DROPLET_SYNC_TOKEN not configured" }, { status: 500 });
  }

  const upstream = await fetch(env.DROPLET_SYNC_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.DROPLET_SYNC_TOKEN}` },
  });

  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}
