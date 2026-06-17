export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    "SELECT id, ts, strategy, symbol, action, price, meta FROM signals ORDER BY id DESC LIMIT 500"
  ).all();
  return Response.json(results);
}
