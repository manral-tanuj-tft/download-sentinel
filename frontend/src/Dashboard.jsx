import { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = "http://localhost:8000";
const API = `${API_BASE}/api`;

const OUTCOME_CONFIG = {
  success_executed: { label: "Success", color: "bg-green-100 text-green-800", icon: "✓" },
  success_smartscreen: { label: "SmartScreen", color: "bg-yellow-100 text-yellow-800", icon: "⚠" },
  browser_blocked: { label: "Blocked", color: "bg-red-100 text-red-800", icon: "✕" },
  browser_warned_dangerous: { label: "Dangerous", color: "bg-orange-100 text-orange-800", icon: "!" },
  browser_warned_uncommon: { label: "Uncommon", color: "bg-amber-100 text-amber-800", icon: "?" },
  defender_blocked: { label: "Defender", color: "bg-red-200 text-red-900", icon: "🛡" },
  download_failed: { label: "Failed", color: "bg-gray-100 text-gray-700", icon: "✕" },
  timeout: { label: "Timeout", color: "bg-gray-100 text-gray-600", icon: "⏱" },
  pending: { label: "Pending", color: "bg-blue-50 text-blue-600", icon: "…" },
  running: { label: "Running", color: "bg-blue-100 text-blue-700", icon: "↻" },
};

const BROWSERS = ["edge", "chrome", "firefox", "curl", "powershell"];

function Badge({ outcome }) {
  const cfg = OUTCOME_CONFIG[outcome] || OUTCOME_CONFIG.pending;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <span>{cfg.icon}</span> {cfg.label}
    </span>
  );
}

function ProgressBar({ completed, total }) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  return (
    <div className="w-full bg-gray-200 rounded-full h-2">
      <div
        className="h-2 rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: pct === 100 ? "#22c55e" : "#3b82f6" }}
      />
    </div>
  );
}

// ── Settings Dialog ──
function SettingsDialog({ onClose }) {
  const [webhook, setWebhook] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [newBrand, setNewBrand] = useState("");
  const [brands, setBrands] = useState([]);

  useEffect(() => {
    fetch(`${API}/brands`).then(r => r.json()).then(setBrands);
    fetch(`${API}/settings`).then(r => r.json()).then(data => {
      if (data.slack_webhook_configured) setWebhook("••••••••••");
    });
  }, []);

  const saveWebhook = async () => {
    await fetch(`${API}/settings/slack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ webhook_url: webhook }),
    });
    setTestResult("Saved!");
  };

  const testSlack = async () => {
    setTestResult("Sending...");
    const res = await fetch(`${API}/settings/slack/test`, { method: "POST" });
    const data = await res.json();
    setTestResult(data.ok ? "Sent! Check Slack." : `Error: ${data.detail || "failed"}`);
  };

  const addBrand = async () => {
    if (!newBrand.trim()) return;
    const slug = newBrand.trim().toLowerCase().replace(/\s+/g, "-");
    await fetch(`${API}/brands`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newBrand.trim(), slug }),
    });
    setNewBrand("");
    const updated = await fetch(`${API}/brands`).then(r => r.json());
    setBrands(updated);
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Settings</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>

        <div className="space-y-3">
          <h3 className="text-sm font-medium text-gray-700">Slack webhook</h3>
          <div className="flex gap-2">
            <input
              className="flex-1 border rounded-lg px-3 py-2 text-sm font-mono"
              placeholder="https://hooks.slack.com/services/..."
              value={webhook}
              onChange={(e) => setWebhook(e.target.value)}
            />
            <button onClick={saveWebhook} className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm">Save</button>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={testSlack} className="text-sm text-blue-600 hover:text-blue-800">Send test message</button>
            {testResult && <span className="text-sm text-gray-500">{testResult}</span>}
          </div>
        </div>

        <div className="border-t pt-4 space-y-3">
          <h3 className="text-sm font-medium text-gray-700">Brands</h3>
          <div className="flex gap-2">
            <input
              className="flex-1 border rounded-lg px-3 py-2 text-sm"
              placeholder="Brand name (e.g. PDFriend)"
              value={newBrand}
              onChange={(e) => setNewBrand(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addBrand()}
            />
            <button onClick={addBrand} className="px-3 py-2 bg-gray-800 text-white rounded-lg text-sm">Add</button>
          </div>
          <div className="flex flex-wrap gap-2">
            {brands.map(b => (
              <span key={b.id} className="px-3 py-1 bg-gray-100 rounded-full text-sm text-gray-700">{b.name}</span>
            ))}
            {brands.length === 0 && <span className="text-sm text-gray-400">No brands yet</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── New Run Dialog ──
function NewRunDialog({ onClose, onSubmit, brands }) {
  const [urls, setUrls] = useState("");
  const [name, setName] = useState("");
  const [brandId, setBrandId] = useState("");
  const [browsers, setBrowsers] = useState(BROWSERS);

  const toggle = (b) =>
    setBrowsers((prev) => (prev.includes(b) ? prev.filter((x) => x !== b) : [...prev, b]));

  const submit = () => {
    const urlList = urls.split("\n").map((u) => u.trim()).filter(Boolean);
    if (!urlList.length) return;
    onSubmit({ urls: urlList, name: name || null, brand_id: brandId || null, browsers });
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4">
        <h2 className="text-lg font-semibold">New test run</h2>
        <input
          className="w-full border rounded-lg px-3 py-2 text-sm"
          placeholder="Run name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          className="w-full border rounded-lg px-3 py-2 text-sm"
          value={brandId}
          onChange={(e) => setBrandId(e.target.value)}
        >
          <option value="">All brands</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
        <textarea
          className="w-full border rounded-lg px-3 py-2 text-sm font-mono h-32"
          placeholder="Paste download URLs (one per line)"
          value={urls}
          onChange={(e) => setUrls(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          {BROWSERS.map((b) => (
            <button
              key={b}
              onClick={() => toggle(b)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition ${
                browsers.includes(b) ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-500 border-gray-300"
              }`}
            >
              {b}
            </button>
          ))}
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
          <button onClick={submit} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            Start run
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Task Detail Modal ──
function TaskDetail({ task, onClose }) {
  const [screenshots, setScreenshots] = useState([]);
  
  useEffect(() => {
    if (task) {
      fetch(`${API}/tasks/${task.id}/screenshots`)
        .then((r) => r.json())
        .then(setScreenshots)
        .catch(() => setScreenshots([]));
    }
  }, [task]);

  if (!task) return null;
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6 space-y-4 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Task detail</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-gray-500">URL:</span> <span className="font-mono break-all">{task.url}</span></div>
          <div><span className="text-gray-500">Browser:</span> {task.browser}</div>
          <div><span className="text-gray-500">Outcome:</span> <Badge outcome={task.outcome} /></div>
          <div><span className="text-gray-500">File:</span> {task.file_name || "—"}</div>
        </div>
        {task.browser_message && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm">
            <div className="font-medium text-amber-800 mb-1">Browser message</div>
            <div className="text-amber-700">{task.browser_message}</div>
          </div>
        )}
        {task.defender_message && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm">
            <div className="font-medium text-red-800 mb-1">Defender message</div>
            <div className="text-red-700">{task.defender_message}</div>
          </div>
        )}
        {task.error_details && (
          <div className="bg-gray-50 border rounded-lg p-3 text-sm font-mono whitespace-pre-wrap">
            {task.error_details}
          </div>
        )}
        {screenshots.length > 0 && (
          <div className="space-y-3">
            <div className="font-medium text-sm">Screenshots ({screenshots.length})</div>
            <div className="grid grid-cols-2 gap-3">
              {screenshots.map((ss, i) => (
                <div key={i} className="border rounded-lg overflow-hidden">
                  <img src={`${API_BASE}${ss.s3_url}`} alt={ss.step} className="w-full" />
                  <div className="px-2 py-1 text-xs text-gray-500 bg-gray-50">{ss.step}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {task.screenshot_url && screenshots.length === 0 && (
          <div className="space-y-3">
            <div className="font-medium text-sm">Screenshot</div>
            <div className="border rounded-lg overflow-hidden">
              <img src={`${API_BASE}${task.screenshot_url}`} alt="screenshot" className="w-full" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Run Detail View ──
function RunDetail({ run, onBack }) {
  const [detail, setDetail] = useState(null);
  const [selectedTask, setSelectedTask] = useState(null);
  const [filter, setFilter] = useState("all");

  const loadDetail = useCallback(() => {
    fetch(`${API}/runs/${run.id}`).then((r) => r.json()).then(setDetail);
  }, [run.id]);

  useEffect(() => {
    loadDetail();
    // Auto-refresh while running, stop when completed
    const timer = setInterval(() => {
      if (detail && detail.status === "completed") return;
      loadDetail();
    }, 3000);
    return () => clearInterval(timer);
  }, [loadDetail, detail?.status]);

  if (!detail) return <div className="p-8 text-center text-gray-400">Loading...</div>;

  const tasks = filter === "all" ? detail.tasks : detail.tasks.filter((t) => t.outcome === filter);

  // Group by URL
  const grouped = {};
  tasks.forEach((t) => {
    if (!grouped[t.url]) grouped[t.url] = [];
    grouped[t.url].push(t);
  });

  const outcomeCounts = {};
  detail.tasks.forEach((t) => { outcomeCounts[t.outcome] = (outcomeCounts[t.outcome] || 0) + 1; });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-sm">&larr; Back</button>
        <h2 className="text-lg font-semibold">{detail.name || `Run ${detail.id.slice(0, 8)}`}</h2>
        <span className={`text-xs px-2 py-0.5 rounded-full ${
          detail.status === "completed" ? "bg-green-100 text-green-700" :
          detail.status === "running" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600"
        }`}>{detail.status}</span>
      </div>

      <ProgressBar completed={detail.completed_tasks} total={detail.total_tasks} />

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilter("all")}
          className={`px-3 py-1 rounded-full text-xs border transition ${filter === "all" ? "bg-gray-800 text-white" : "bg-white text-gray-600"}`}
        >All ({detail.tasks.length})</button>
        {Object.entries(outcomeCounts).map(([o, c]) => (
          <button
            key={o}
            onClick={() => setFilter(o)}
            className={`px-3 py-1 rounded-full text-xs border transition ${filter === o ? "bg-gray-800 text-white" : "bg-white text-gray-600"}`}
          >{OUTCOME_CONFIG[o]?.label || o} ({c})</button>
        ))}
      </div>

      <div className="space-y-4">
        {Object.entries(grouped).map(([url, urlTasks]) => (
          <div key={url} className="border rounded-xl overflow-hidden">
            <div className="bg-gray-50 px-4 py-2 text-sm font-mono text-gray-600 truncate">{url}</div>
            <div className="divide-y">
              {urlTasks.map((t) => (
                <div
                  key={t.id}
                  onClick={() => setSelectedTask(t)}
                  className="flex items-center justify-between px-4 py-2 hover:bg-blue-50 cursor-pointer transition"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-medium text-gray-500 w-20">{t.browser}</span>
                    <Badge outcome={t.outcome} />
                  </div>
                  <div className="flex items-center gap-3">
                    {t.screenshot_url && (
                      <span className="text-xs text-blue-500">📷</span>
                    )}
                    {t.browser_message && (
                      <span className="text-xs text-gray-400 max-w-xs truncate">{t.browser_message}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {selectedTask && <TaskDetail task={selectedTask} onClose={() => setSelectedTask(null)} />}
    </div>
  );
}

// ── Main Dashboard ──
export default function Dashboard() {
  const [runs, setRuns] = useState([]);
  const [brands, setBrands] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [showNewRun, setShowNewRun] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [brandFilter, setBrandFilter] = useState("");
  const wsRef = useRef(null);

  // Fetch data
  const loadRuns = useCallback(() => {
    const params = new URLSearchParams();
    if (brandFilter) params.set("brand_id", brandFilter);
    fetch(`${API}/runs?${params}`).then((r) => r.json()).then(setRuns);
  }, [brandFilter]);

  useEffect(() => {
    fetch(`${API}/brands`).then((r) => r.json()).then(setBrands);
    loadRuns();
  }, [loadRuns]);

  // WebSocket for live updates
  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connect = () => {
      ws = new WebSocket("ws://localhost:8000/ws");
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === "run_created" || data.type === "task_update") {
          loadRuns();
        }
      };
      ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 3000);
      };
      wsRef.current = ws;
    };

    connect();

    // Also poll every 5 seconds as fallback
    const pollTimer = setInterval(loadRuns, 5000);

    return () => {
      clearInterval(pollTimer);
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [loadRuns]);

  const createRun = async (data) => {
    await fetch(`${API}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setShowNewRun(false);
    loadRuns();
  };

  if (selectedRun) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <RunDetail run={selectedRun} onBack={() => { setSelectedRun(null); loadRuns(); }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Download monitor</h1>
          <p className="text-sm text-gray-500 mt-1">Track file download outcomes across browsers</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowSettings(true)}
            className="px-4 py-2 border rounded-lg text-sm font-medium text-gray-600 hover:text-gray-800 transition"
          >Settings</button>
          <button
            onClick={() => setShowNewRun(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition"
          >New run</button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <select
          className="border rounded-lg px-3 py-2 text-sm"
          value={brandFilter}
          onChange={(e) => setBrandFilter(e.target.value)}
        >
          <option value="">All brands</option>
          {brands.map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
        <button onClick={loadRuns} className="text-sm text-blue-600 hover:text-blue-800">Refresh</button>
      </div>

      <div className="space-y-3">
        {runs.length === 0 ? (
          <div className="text-center py-12 text-gray-400">No test runs yet. Click "New run" to start.</div>
        ) : (
          runs.map((run) => {
            const summary = run.outcome_summary || {};
            return (
              <div
                key={run.id}
                onClick={() => setSelectedRun(run)}
                className="border rounded-xl p-4 hover:border-blue-300 hover:shadow-sm cursor-pointer transition space-y-3"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-medium text-gray-900">{run.name || `Run ${run.id.slice(0, 8)}`}</div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {new Date(run.created_at).toLocaleString()} — {run.total_tasks} tasks
                    </div>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    run.status === "completed" ? "bg-green-100 text-green-700" :
                    run.status === "running" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-600"
                  }`}>{run.status}</span>
                </div>
                <ProgressBar completed={run.completed_tasks} total={run.total_tasks} />
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(summary).map(([outcome, count]) => (
                    <span key={outcome} className="flex items-center gap-1">
                      <Badge outcome={outcome} />
                      <span className="text-xs text-gray-400">{count}</span>
                    </span>
                  ))}
                </div>
              </div>
            );
          })
        )}
      </div>

      {showNewRun && <NewRunDialog onClose={() => setShowNewRun(false)} onSubmit={createRun} brands={brands} />}
      {showSettings && <SettingsDialog onClose={() => { setShowSettings(false); loadRuns(); fetch(`${API}/brands`).then(r => r.json()).then(setBrands); }} />}
    </div>
  );
}