export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    "SELECT id, ts, mode, strategy, symbol, side, quantity, price, order_id, status, pnl FROM trades ORDER BY id DESC LIMIT 500"
  ).all();
  return Response.json(results);
}
