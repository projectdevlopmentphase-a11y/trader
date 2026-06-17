export async function onRequestGet({ env }) {
  const { results } = await env.DB.prepare(
    "SELECT trade_date, realized_pnl, trade_count, halted FROM daily_pnl ORDER BY trade_date DESC LIMIT 90"
  ).all();
  return Response.json(results);
}
