"""
Probity Subnet Dashboard — SWPE Oracle & Miner Leaderboard.

Serves a professional dashboard UI and REST API for the Probity subnet.

Usage:
    # Generate demo data first:
    PYTHONPATH=. python scripts/generate_demo_data.py --output-dir /tmp/probity
    # Then launch:
    PYTHONPATH=. python scripts/dashboard.py --data-dir /tmp/probity

API Endpoints:
    GET /              → Dashboard UI
    GET /api/oracle    → SWPE oracle records (JSON)
    GET /api/leaderboard → Miner skill rankings (JSON)
    GET /api/status    → Subnet status summary (JSON)
"""

import argparse
import json
import math
import os
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
import uvicorn

app = FastAPI(title="Probity Subnet API", version="1.0.0")

DATA_DIR = "."
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/static/logo.png")
def serve_logo():
    return FileResponse(os.path.join(SCRIPT_DIR, "ProbityLogo.png"), media_type="image/png")


def load_oracle():
    path = os.path.join(DATA_DIR, "swpe_oracle.jsonl")
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                correct_side = (r["outcome"] == 1 and r["swpe"] > 0.5) or \
                               (r["outcome"] == 0 and r["swpe"] < 0.5)
                market_correct = (r["outcome"] == 1 and r["market_prob"] > 0.5) or \
                                 (r["outcome"] == 0 and r["market_prob"] < 0.5)
                swpe_loss = abs(r["outcome"] - r["swpe"])
                market_loss = abs(r["outcome"] - r["market_prob"])
                r["swpe_better"] = swpe_loss < market_loss
                r["edge"] = round(market_loss - swpe_loss, 6)
                records.append(r)
    return records


def load_leaderboard():
    path = os.path.join(DATA_DIR, "skill_tracker.json")
    if not os.path.exists(path):
        return [], 10.0
    with open(path) as f:
        data = json.load(f)
    N0 = data.get("N0", 10.0)
    names = data.get("miner_names", [])
    hotkeys = data.get("hotkeys", [])
    miners = []
    for i, (ss, c) in enumerate(zip(data["sum_skill"], data["count"])):
        if c == 0 and ss == 0:
            continue  # skip miners with no activity
        rs = ss / (N0 + c) if (N0 + c) > 0 else 0
        avg = ss / c if c > 0 else 0
        w = math.exp(5.0 * rs)
        # Use miner_names (demo) or hotkeys (live) or fallback to UID
        if i < len(names):
            name = names[i]
        elif i < len(hotkeys) and hotkeys[i]:
            name = hotkeys[i][:8] + "..." + hotkeys[i][-4:]
        else:
            name = f"Miner-{i}"
        miners.append({
            "uid": i,
            "name": name,
            "hotkey": hotkeys[i] if i < len(hotkeys) else None,
            "rolling_skill": round(rs, 6),
            "sum_skill": round(ss, 6),
            "events_scored": int(c),
            "avg_skill": round(avg, 6),
            "weight": round(w, 6),
        })
    miners.sort(key=lambda m: m["rolling_skill"], reverse=True)
    for rank, m in enumerate(miners, 1):
        m["rank"] = rank
    return miners, N0


@app.get("/api/oracle")
def api_oracle():
    records = load_oracle()
    return {"records": list(reversed(records)), "count": len(records)}


@app.get("/api/leaderboard")
def api_leaderboard():
    miners, N0 = load_leaderboard()
    return {"miners": miners, "N0": N0}


@app.get("/api/status")
def api_status():
    records = load_oracle()
    miners, _ = load_leaderboard()
    last_ts = max((r["ts"] for r in records), default=0)
    total_edge = sum(r["edge"] for r in records)
    wins = sum(1 for r in records if r["swpe_better"])
    return {
        "subnet_name": "Probity",
        "netuid": 290,
        "total_events_scored": len(records),
        "total_miners": len(miners),
        "swpe_wins": wins,
        "swpe_win_rate": round(wins / len(records) * 100, 1) if records else 0,
        "avg_edge": round(total_edge / len(records), 6) if records else 0,
        "last_update_ts": last_ts,
    }


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Probity Subnet Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #0a0e17;
  --card: #111827;
  --card-border: #1e293b;
  --text: #e2e8f0;
  --text-dim: #94a3b8;
  --cyan: #06b6d4;
  --green: #10b981;
  --red: #ef4444;
  --amber: #f59e0b;
  --purple: #8b5cf6;
  --gold: #fbbf24;
  --silver: #9ca3af;
  --bronze: #d97706;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
}
.container { max-width: 1280px; margin: 0 auto; padding: 24px 20px; }

/* Header */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--card-border);
}
.header-left { display: flex; align-items: center; gap: 16px; }
.logo {
  width: 48px; height: 48px;
  border-radius: 12px;
  object-fit: contain;
}
.header h1 {
  font-size: 24px; font-weight: 700; color: #fff;
  text-shadow: 0 0 30px rgba(6,182,212,0.2);
}
.header .subtitle { font-size: 13px; color: var(--text-dim); margin-top: 2px; }
.live-badge {
  display: flex; align-items: center; gap: 8px;
  background: rgba(16,185,129,0.1);
  border: 1px solid rgba(16,185,129,0.3);
  padding: 6px 14px; border-radius: 20px;
  font-size: 12px; font-weight: 600; color: var(--green);
}
.live-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--green);
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16,185,129,0.4); }
  50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(16,185,129,0); }
}
.netuid-badge {
  background: rgba(6,182,212,0.1);
  border: 1px solid rgba(6,182,212,0.3);
  padding: 6px 14px; border-radius: 20px;
  font-size: 12px; font-weight: 600; color: var(--cyan);
  font-family: 'JetBrains Mono', monospace;
}
.header-right { display: flex; gap: 10px; align-items: center; }

/* Status cards */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}
.stat-card {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 20px;
  position: relative;
  overflow: hidden;
}
.stat-card::before {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px; border-radius: 3px 0 0 3px;
}
.stat-card:nth-child(1)::before { background: var(--cyan); }
.stat-card:nth-child(2)::before { background: var(--green); }
.stat-card:nth-child(3)::before { background: var(--purple); }
.stat-card:nth-child(4)::before { background: var(--amber); }
.stat-label { font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
.stat-value { font-size: 28px; font-weight: 700; color: #fff; margin-top: 4px; font-family: 'JetBrains Mono', monospace; }
.stat-sub { font-size: 12px; color: var(--text-dim); margin-top: 4px; }

/* Section headers */
.section-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.section-title {
  font-size: 16px; font-weight: 700; color: #fff;
  display: flex; align-items: center; gap: 8px;
}
.section-title .icon { font-size: 18px; }

/* Tables */
.table-wrap {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 32px;
}
table { width: 100%; border-collapse: collapse; }
thead th {
  padding: 12px 16px;
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  font-weight: 600;
  background: rgba(255,255,255,0.02);
  border-bottom: 1px solid var(--card-border);
}
tbody td {
  padding: 12px 16px;
  font-size: 13px;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  font-family: 'JetBrains Mono', monospace;
}
tbody tr:hover { background: rgba(6,182,212,0.04); }
tbody tr:last-child td { border-bottom: none; }
.mono { font-family: 'JetBrains Mono', monospace; }
.text-green { color: var(--green); }
.text-red { color: var(--red); }
.text-dim { color: var(--text-dim); }
.text-cyan { color: var(--cyan); }

/* Badges */
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}
.badge-yes { background: rgba(16,185,129,0.15); color: var(--green); }
.badge-no { background: rgba(239,68,68,0.15); color: var(--red); }
.badge-better { background: rgba(16,185,129,0.15); color: var(--green); }
.badge-worse { background: rgba(239,68,68,0.1); color: var(--text-dim); }

/* Skill bar */
.skill-bar-wrap { display: flex; align-items: center; gap: 8px; }
.skill-bar-bg {
  width: 80px; height: 6px;
  background: rgba(255,255,255,0.05);
  border-radius: 3px; overflow: hidden;
  position: relative;
}
.skill-bar {
  height: 100%; border-radius: 3px;
  position: absolute; top: 0;
}
.skill-bar.positive { background: var(--green); left: 50%; }
.skill-bar.negative { background: var(--red); right: 50%; }

/* Rank medals */
.rank { font-weight: 700; }
.rank-1 { color: var(--gold); }
.rank-2 { color: var(--silver); }
.rank-3 { color: var(--bronze); }

/* Chart area */
.chart-container {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 32px;
}
.chart-container canvas { max-height: 320px; }

/* Two column layout */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }

/* Footer */
.footer {
  text-align: center;
  padding: 24px;
  color: var(--text-dim);
  font-size: 12px;
  border-top: 1px solid var(--card-border);
  margin-top: 16px;
}
.footer a { color: var(--cyan); text-decoration: none; }

/* API badge */
.api-link {
  font-size: 11px;
  color: var(--text-dim);
  font-family: 'JetBrains Mono', monospace;
  background: rgba(255,255,255,0.03);
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid var(--card-border);
  text-decoration: none;
}
.api-link:hover { color: var(--cyan); border-color: var(--cyan); }

@media (max-width: 900px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  .two-col { grid-template-columns: 1fr; }
  .header { flex-direction: column; gap: 12px; align-items: flex-start; }
}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <img class="logo" src="/static/logo.png" alt="Probity">
      <div>
        <h1>Probity Subnet</h1>
        <div class="subtitle">Skill-Weighted Probability Ensemble Oracle</div>
      </div>
    </div>
    <div class="header-right">
      <div class="live-badge"><div class="live-dot"></div> LIVE</div>
      <div class="netuid-badge">NETUID 290</div>
    </div>
  </div>

  <!-- Status cards -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Events Scored</div>
      <div class="stat-value" id="s-events">-</div>
      <div class="stat-sub">Total resolved events</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">SWPE Win Rate</div>
      <div class="stat-value" id="s-winrate">-</div>
      <div class="stat-sub" id="s-wins-detail">SWPE vs Market accuracy</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Active Miners</div>
      <div class="stat-value" id="s-miners">-</div>
      <div class="stat-sub">Registered forecasters</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Avg Edge</div>
      <div class="stat-value" id="s-edge">-</div>
      <div class="stat-sub">SWPE advantage over market</div>
    </div>
  </div>

  <!-- Chart -->
  <div class="section-header">
    <div class="section-title"><span class="icon">&#9632;</span> SWPE vs Market Probability</div>
  </div>
  <div class="chart-container">
    <canvas id="swpeChart"></canvas>
  </div>

  <!-- Two column: Oracle + Leaderboard -->
  <div class="two-col">
    <div>
      <div class="section-header">
        <div class="section-title"><span class="icon">&#9670;</span> Oracle Feed</div>
        <a href="/api/oracle" class="api-link">GET /api/oracle</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Event</th>
              <th style="text-align:right">SWPE</th>
              <th style="text-align:right">Market</th>
              <th style="text-align:right">Edge</th>
              <th style="text-align:center">Result</th>
            </tr>
          </thead>
          <tbody id="oracle-body"></tbody>
        </table>
      </div>
    </div>
    <div>
      <div class="section-header">
        <div class="section-title"><span class="icon">&#9733;</span> Miner Leaderboard</div>
        <a href="/api/leaderboard" class="api-link">GET /api/leaderboard</a>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Miner</th>
              <th style="text-align:right">Rolling Skill</th>
              <th>Performance</th>
              <th style="text-align:right">Events</th>
              <th style="text-align:right">Weight</th>
            </tr>
          </thead>
          <tbody id="leader-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="footer">
    Probity &mdash; Decentralized Superforecaster Network &bull; Bittensor Subnet 290 &bull;
    <a href="/api/oracle">/api/oracle</a> &bull;
    <a href="/api/leaderboard">/api/leaderboard</a> &bull;
    <a href="/api/status">/api/status</a>
  </div>
</div>

<script>
let chartInstance = null;

function timeAgo(ts) {
  const diff = Math.floor(Date.now()/1000) - ts;
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

function truncate(s, n) {
  return s.length > n ? s.substring(0, n) + '...' : s;
}

function renderStatus(status) {
  document.getElementById('s-events').textContent = status.total_events_scored;
  document.getElementById('s-winrate').textContent = status.swpe_win_rate + '%';
  document.getElementById('s-wins-detail').textContent =
    status.swpe_wins + '/' + status.total_events_scored + ' events SWPE closer to outcome';
  document.getElementById('s-miners').textContent = status.total_miners;
  const edgeVal = (status.avg_edge * 100).toFixed(1);
  document.getElementById('s-edge').textContent = (status.avg_edge >= 0 ? '+' : '') + edgeVal + '%';
}

function renderOracle(data) {
  const tbody = document.getElementById('oracle-body');
  let html = '';
  for (const r of data.records) {
    const outcomeLabel = r.outcome === 1 ? 'YES' : 'NO';
    const outcomeCls = r.outcome === 1 ? 'badge-yes' : 'badge-no';
    const edgePct = (r.edge * 100).toFixed(1);
    const edgeCls = r.edge >= 0 ? 'text-green' : 'text-red';
    const edgeSign = r.edge >= 0 ? '+' : '';
    const betterBadge = r.swpe_better
      ? '<span class="badge badge-better">SWPE</span>'
      : '<span class="badge badge-worse">MKT</span>';
    html += '<tr>' +
      '<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:Inter,sans-serif;font-size:12px;" title="' +
        r.question + '">' + truncate(r.question, 42) + '</td>' +
      '<td style="text-align:right;font-weight:600;">' + r.swpe.toFixed(4) + '</td>' +
      '<td style="text-align:right;" class="text-dim">' + r.market_prob.toFixed(4) + '</td>' +
      '<td style="text-align:right;" class="' + edgeCls + '">' + edgeSign + edgePct + '% ' + betterBadge + '</td>' +
      '<td style="text-align:center;"><span class="badge ' + outcomeCls + '">' + outcomeLabel + '</span></td>' +
      '</tr>';
  }
  tbody.innerHTML = html;
}

function renderLeaderboard(data) {
  const tbody = document.getElementById('leader-body');
  const maxSkill = Math.max(...data.miners.map(m => Math.abs(m.rolling_skill)), 0.01);
  let html = '';
  for (const m of data.miners) {
    const rankCls = m.rank <= 3 ? 'rank-' + m.rank : '';
    const rankIcon = m.rank === 1 ? '&#9733; ' : m.rank === 2 ? '&#9734; ' : m.rank === 3 ? '&#9671; ' : '';
    const skillCls = m.rolling_skill >= 0 ? 'text-green' : 'text-red';
    const skillSign = m.rolling_skill >= 0 ? '+' : '';
    const barPct = Math.min(Math.abs(m.rolling_skill) / maxSkill * 50, 50);
    const barCls = m.rolling_skill >= 0 ? 'positive' : 'negative';
    const barStyle = m.rolling_skill >= 0
      ? 'left:50%;width:' + barPct + '%'
      : 'right:50%;width:' + barPct + '%';
    html += '<tr>' +
      '<td class="rank ' + rankCls + '">' + rankIcon + m.rank + '</td>' +
      '<td style="font-family:Inter,sans-serif;font-weight:500;">' + m.name + '</td>' +
      '<td style="text-align:right;" class="' + skillCls + '">' + skillSign + m.rolling_skill.toFixed(4) + '</td>' +
      '<td><div class="skill-bar-wrap"><div class="skill-bar-bg"><div class="skill-bar ' + barCls + '" style="' + barStyle + '"></div></div></div></td>' +
      '<td style="text-align:right;" class="text-dim">' + m.events_scored + '</td>' +
      '<td style="text-align:right;">' + m.weight.toFixed(3) + '</td>' +
      '</tr>';
  }
  tbody.innerHTML = html;
}

function renderChart(data) {
  const ctx = document.getElementById('swpeChart').getContext('2d');
  const records = [...data.records].reverse();
  const labels = records.map(r => truncate(r.question, 30));
  const swpeData = records.map(r => r.swpe);
  const marketData = records.map(r => r.market_prob);
  const outcomeData = records.map(r => r.outcome);

  if (chartInstance) chartInstance.destroy();

  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'SWPE',
          data: swpeData,
          backgroundColor: 'rgba(6,182,212,0.7)',
          borderColor: 'rgba(6,182,212,1)',
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: 'Market',
          data: marketData,
          backgroundColor: 'rgba(148,163,184,0.3)',
          borderColor: 'rgba(148,163,184,0.6)',
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: 'Outcome',
          data: outcomeData,
          type: 'line',
          borderColor: 'rgba(251,191,36,0.8)',
          backgroundColor: 'rgba(251,191,36,0.1)',
          pointBackgroundColor: 'rgba(251,191,36,1)',
          pointRadius: 6,
          pointHoverRadius: 8,
          borderWidth: 2,
          fill: false,
          tension: 0,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: '#94a3b8', font: { family: 'Inter', size: 12 } }
        },
        tooltip: {
          backgroundColor: '#1e293b',
          titleColor: '#e2e8f0',
          bodyColor: '#94a3b8',
          borderColor: '#334155',
          borderWidth: 1,
        }
      },
      scales: {
        x: {
          ticks: { color: '#64748b', font: { size: 10 }, maxRotation: 45 },
          grid: { color: 'rgba(255,255,255,0.03)' }
        },
        y: {
          min: 0, max: 1,
          ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 11 } },
          grid: { color: 'rgba(255,255,255,0.05)' }
        }
      }
    }
  });
}

async function loadDashboard() {
  try {
    const [oracle, leaderboard, status] = await Promise.all([
      fetch('/api/oracle').then(r => r.json()),
      fetch('/api/leaderboard').then(r => r.json()),
      fetch('/api/status').then(r => r.json()),
    ]);
    renderStatus(status);
    renderOracle(oracle);
    renderLeaderboard(leaderboard);
    renderChart(oracle);
  } catch (e) {
    console.error('Dashboard load failed:', e);
  }
}

loadDashboard();
setInterval(loadDashboard, 15000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return DASHBOARD_HTML


def main():
    global DATA_DIR
    parser = argparse.ArgumentParser(description="Probity Subnet Dashboard")
    parser.add_argument("--data-dir", default=".", help="Directory with swpe_oracle.jsonl and skill_tracker.json")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    DATA_DIR = args.data_dir
    print(f"\n  Probity Dashboard")
    print(f"  Data dir : {os.path.abspath(DATA_DIR)}")
    print(f"  Dashboard: http://localhost:{args.port}")
    print(f"  API:       http://localhost:{args.port}/api/oracle")
    print(f"             http://localhost:{args.port}/api/leaderboard")
    print(f"             http://localhost:{args.port}/api/status\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
