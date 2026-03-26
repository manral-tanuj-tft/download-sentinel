import { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = window.location.origin;
const API = `${API_BASE}/api`;

const OUTCOME_CONFIG = {
  success_executed:        { label: "Success",    color: "bg-green-100 text-green-800",   icon: "✓"  },
  success_smartscreen:     { label: "SmartScreen",color: "bg-yellow-100 text-yellow-800", icon: "⚠"  },
  browser_blocked:         { label: "Blocked",    color: "bg-red-100 text-red-800",       icon: "✕"  },
  browser_warned_dangerous:{ label: "Dangerous",  color: "bg-orange-100 text-orange-800", icon: "!"  },
  browser_warned_uncommon: { label: "Uncommon",   color: "bg-amber-100 text-amber-800",   icon: "?"  },
  defender_blocked:        { label: "Defender",   color: "bg-red-200 text-red-900",       icon: "🛡" },
  download_failed:         { label: "Failed",     color: "bg-gray-100 text-gray-700",     icon: "✕"  },
  timeout:                 { label: "Timeout",    color: "bg-gray-100 text-gray-600",     icon: "⏱" },
  pending:                 { label: "Pending",    color: "bg-blue-50 text-blue-600",      icon: "…"  },
  running:                 { label: "Running",    color: "bg-blue-100 text-blue-700",     icon: "↻"  },
};

const STATUS_COLOR = {
  queued:         "bg-gray-100 text-gray-600",
  waiting_for_vm: "bg-purple-100 text-purple-700",
  provisioning:   "bg-indigo-100 text-indigo-700",
  running:        "bg-blue-100 text-blue-700",
  completed:      "bg-green-100 text-green-700",
  failed:         "bg-red-100 text-red-700",
  cancelled:      "bg-gray-200 text-gray-500",
};

const VM_STATUS_COLOR = {
  idle:         "bg-green-100 text-green-700",
  busy:         "bg-blue-100 text-blue-700",
  offline:      "bg-red-100 text-red-700",
  provisioning: "bg-yellow-100 text-yellow-700",
};

const BROWSERS = ["edge", "chrome", "firefox", "curl", "powershell"];

// ── Tiny helpers ──────────────────────────────────────────────────────

function Badge({ outcome }) {
  const cfg = OUTCOME_CONFIG[outcome] || OUTCOME_CONFIG.pending;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      <span>{cfg.icon}</span> {cfg.label}
    </span>
  );
}

function StatusPill({ status }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLOR[status] || "bg-gray-100 text-gray-600"}`}>
      {status?.replace(/_/g, " ")}
    </span>
  );
}

function ProgressBar({ completed, total }) {
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  return (
    <div className="w-full bg-gray-200 rounded-full h-1.5">
      <div
        className="h-1.5 rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: pct === 100 ? "#22c55e" : "#3b82f6" }}
      />
    </div>
  );
}

function Spinner() {
  return <span className="inline-block w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />;
}

// ── Settings Dialog ───────────────────────────────────────────────────

function SettingsDialog({ onClose }) {
  const [webhook, setWebhook] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [newBrand, setNewBrand] = useState("");
  const [brands, setBrands] = useState([]);

  useEffect(() => {
    fetch(`${API}/brands`).then(r => r.json()).then(setBrands);
    fetch(`${API}/settings`).then(r => r.json()).then(d => {
      if (d.slack_webhook_configured) setWebhook("••••••••••");
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
    const d = await res.json();
    setTestResult(d.ok ? "Sent! Check Slack." : `Error: ${d.detail || "failed"}`);
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
    fetch(`${API}/brands`).then(r => r.json()).then(setBrands);
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
            <input className="flex-1 border rounded-lg px-3 py-2 text-sm font-mono"
              placeholder="https://hooks.slack.com/services/..."
              value={webhook} onChange={e => setWebhook(e.target.value)} />
            <button onClick={saveWebhook} className="px-3 py-2 bg-blue-600 text-white rounded-lg text-sm">Save</button>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={testSlack} className="text-sm text-blue-600 hover:text-blue-800">Send test</button>
            {testResult && <span className="text-sm text-gray-500">{testResult}</span>}
          </div>
        </div>
        <div className="border-t pt-4 space-y-3">
          <h3 className="text-sm font-medium text-gray-700">Brands</h3>
          <div className="flex gap-2">
            <input className="flex-1 border rounded-lg px-3 py-2 text-sm"
              placeholder="Brand name" value={newBrand}
              onChange={e => setNewBrand(e.target.value)}
              onKeyDown={e => e.key === "Enter" && addBrand()} />
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

// ── New Run Dialog ────────────────────────────────────────────────────

function NewRunDialog({ onClose, onSubmit, brands }) {
  const [urls, setUrls] = useState("");
  const [name, setName] = useState("");
  const [brandId, setBrandId] = useState("");
  const [browsers, setBrowsers] = useState([...BROWSERS]);
  const fileRef = useRef();

  const toggle = b => setBrowsers(p => p.includes(b) ? p.filter(x => x !== b) : [...p, b]);

  const handleFile = e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => setUrls(prev => [prev, ev.target.result.trim()].filter(Boolean).join("\n"));
    reader.readAsText(file);
  };

  const submit = () => {
    const urlList = urls.split("\n").map(u => u.trim()).filter(Boolean);
    if (!urlList.length || !browsers.length) return;
    onSubmit({ urls: urlList, name: name || null, brand_id: brandId || null, browsers });
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4">
        <h2 className="text-lg font-semibold">New test run</h2>
        <input className="w-full border rounded-lg px-3 py-2 text-sm"
          placeholder="Run name (optional)" value={name} onChange={e => setName(e.target.value)} />
        <select className="w-full border rounded-lg px-3 py-2 text-sm"
          value={brandId} onChange={e => setBrandId(e.target.value)}>
          <option value="">All brands</option>
          {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <textarea className="w-full border rounded-lg px-3 py-2 text-sm font-mono h-28"
          placeholder="Paste download URLs (one per line)"
          value={urls} onChange={e => setUrls(e.target.value)} />
        <div className="flex items-center gap-3">
          <button onClick={() => fileRef.current?.click()}
            className="px-3 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50">
            📂 Upload file list
          </button>
          <input ref={fileRef} type="file" accept=".txt,.csv" className="hidden" onChange={handleFile} />
          <span className="text-xs text-gray-400">or paste URLs above</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {BROWSERS.map(b => (
            <button key={b} onClick={() => toggle(b)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition ${
                browsers.includes(b) ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-500 border-gray-300"
              }`}>{b}</button>
          ))}
        </div>
        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600">Cancel</button>
          <button onClick={submit} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            Start run
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Task Detail Modal ─────────────────────────────────────────────────

function TaskDetail({ task, onClose }) {
  const [screenshots, setScreenshots] = useState([]);

  useEffect(() => {
    if (task) {
      fetch(`${API}/tasks/${task.id}/screenshots`)
        .then(r => r.json()).then(setScreenshots).catch(() => setScreenshots([]));
    }
  }, [task]);

  if (!task) return null;
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6 space-y-4 max-h-[85vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Task detail</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="col-span-2"><span className="text-gray-500">URL: </span><span className="font-mono break-all">{task.url}</span></div>
          <div><span className="text-gray-500">Browser: </span>{task.browser}</div>
          <div><span className="text-gray-500">Outcome: </span><Badge outcome={task.outcome} /></div>
          <div><span className="text-gray-500">File: </span>{task.file_name || "—"}</div>
          <div><span className="text-gray-500">HTTP: </span>{task.http_status || "—"}</div>
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
          <div className="bg-gray-50 border rounded-lg p-3 text-sm font-mono whitespace-pre-wrap">{task.error_details}</div>
        )}
        {screenshots.length > 0 && (
          <div className="space-y-2">
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
          <div className="border rounded-lg overflow-hidden">
            <img src={`${API_BASE}${task.screenshot_url}`} alt="screenshot" className="w-full" />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Run Detail View ───────────────────────────────────────────────────

function RunDetail({ run, onBack }) {
  const [detail, setDetail] = useState(null);
  const [selectedTask, setSelectedTask] = useState(null);
  const [filter, setFilter] = useState("all");

  const loadDetail = useCallback(() => {
    fetch(`${API}/runs/${run.id}`).then(r => r.json()).then(setDetail);
  }, [run.id]);

  useEffect(() => {
    loadDetail();
    const t = setInterval(() => {
      setDetail(d => {
        if (d && ["completed","failed","cancelled"].includes(d.status)) {
          clearInterval(t);
          return d;
        }
        loadDetail();
        return d;
      });
    }, 3000);
    return () => clearInterval(t);
  }, [loadDetail]);

  if (!detail) return <div className="p-8 text-center text-gray-400">Loading...</div>;

  const tasks = filter === "all" ? detail.tasks : detail.tasks.filter(t => t.outcome === filter);
  const grouped = {};
  tasks.forEach(t => { if (!grouped[t.url]) grouped[t.url] = []; grouped[t.url].push(t); });

  const outcomeCounts = {};
  detail.tasks.forEach(t => { outcomeCounts[t.outcome] = (outcomeCounts[t.outcome] || 0) + 1; });

  const history = detail.state_history || [];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={onBack} className="text-blue-600 hover:text-blue-800 text-sm">&larr; Back</button>
        <h2 className="text-lg font-semibold">{detail.name || `Run ${detail.id.slice(0, 8)}`}</h2>
        <StatusPill status={detail.status} />
        {detail.triggered_by?.startsWith("scheduled:") && (
          <span className="text-xs text-purple-600 bg-purple-50 px-2 py-0.5 rounded-full">⏰ Scheduled</span>
        )}
      </div>

      <ProgressBar completed={detail.completed_tasks} total={detail.total_tasks} />
      <div className="text-xs text-gray-400">{detail.completed_tasks} / {detail.total_tasks} tasks</div>

      {/* State timeline */}
      {history.length > 0 && (
        <div className="border rounded-xl p-4 space-y-2 bg-gray-50">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">State history</div>
          <div className="space-y-1">
            {history.map((h, i) => (
              <div key={i} className="flex items-start gap-3 text-xs">
                <span className="text-gray-400 w-36 shrink-0">{new Date(h.timestamp).toLocaleTimeString()}</span>
                <StatusPill status={h.state} />
                {h.message && <span className="text-gray-500">{h.message}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Outcome filter pills */}
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setFilter("all")}
          className={`px-3 py-1 rounded-full text-xs border transition ${filter === "all" ? "bg-gray-800 text-white" : "bg-white text-gray-600"}`}>
          All ({detail.tasks.length})
        </button>
        {Object.entries(outcomeCounts).map(([o, c]) => (
          <button key={o} onClick={() => setFilter(o)}
            className={`px-3 py-1 rounded-full text-xs border transition ${filter === o ? "bg-gray-800 text-white" : "bg-white text-gray-600"}`}>
            {OUTCOME_CONFIG[o]?.label || o} ({c})
          </button>
        ))}
      </div>

      {/* Tasks grouped by URL */}
      <div className="space-y-4">
        {Object.entries(grouped).map(([url, urlTasks]) => (
          <div key={url} className="border rounded-xl overflow-hidden">
            <div className="bg-gray-50 px-4 py-2 text-xs font-mono text-gray-600 truncate">{url}</div>
            <div className="divide-y">
              {urlTasks.map(t => (
                <div key={t.id} onClick={() => setSelectedTask(t)}
                  className="flex items-center justify-between px-4 py-2 hover:bg-blue-50 cursor-pointer transition">
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-medium text-gray-500 w-20">{t.browser}</span>
                    <Badge outcome={t.outcome} />
                  </div>
                  <div className="flex items-center gap-3">
                    {t.screenshot_url && <span className="text-xs text-blue-400">📷</span>}
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

// ── Scheduled Jobs Tab ────────────────────────────────────────────────

function NewJobDialog({ onClose, onSubmit, brands }) {
  const [name, setName] = useState("");
  const [urls, setUrls] = useState("");
  const [browsers, setBrowsers] = useState([...BROWSERS]);
  const [brandId, setBrandId] = useState("");
  const [schedType, setSchedType] = useState("interval");
  const [intervalHours, setIntervalHours] = useState(24);
  const [cron, setCron] = useState("0 9 * * *");
  const fileRef = useRef();

  const toggle = b => setBrowsers(p => p.includes(b) ? p.filter(x => x !== b) : [...p, b]);

  const handleFile = e => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => setUrls(prev => [prev, ev.target.result.trim()].filter(Boolean).join("\n"));
    reader.readAsText(file);
  };

  const submit = () => {
    const urlList = urls.split("\n").map(u => u.trim()).filter(Boolean);
    if (!name.trim() || !urlList.length || !browsers.length) return;
    onSubmit({
      name: name.trim(),
      urls: urlList,
      browsers,
      brand_id: brandId || null,
      schedule_type: schedType,
      interval_hours: schedType === "interval" ? Number(intervalHours) : null,
      cron_expr: schedType === "cron" ? cron : null,
      enabled: true,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold">New scheduled job</h2>

        <input className="w-full border rounded-lg px-3 py-2 text-sm"
          placeholder="Job name" value={name} onChange={e => setName(e.target.value)} />

        <select className="w-full border rounded-lg px-3 py-2 text-sm"
          value={brandId} onChange={e => setBrandId(e.target.value)}>
          <option value="">No brand</option>
          {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>

        <textarea className="w-full border rounded-lg px-3 py-2 text-sm font-mono h-24"
          placeholder="Download URLs (one per line)"
          value={urls} onChange={e => setUrls(e.target.value)} />

        <div className="flex items-center gap-3">
          <button onClick={() => fileRef.current?.click()}
            className="px-3 py-1.5 border rounded-lg text-xs text-gray-600 hover:bg-gray-50">
            📂 Upload file list
          </button>
          <input ref={fileRef} type="file" accept=".txt,.csv" className="hidden" onChange={handleFile} />
        </div>

        <div className="flex flex-wrap gap-2">
          {BROWSERS.map(b => (
            <button key={b} onClick={() => toggle(b)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition ${
                browsers.includes(b) ? "bg-blue-600 text-white border-blue-600" : "bg-white text-gray-500 border-gray-300"
              }`}>{b}</button>
          ))}
        </div>

        <div className="border rounded-xl p-4 space-y-3">
          <div className="text-sm font-medium text-gray-700">Schedule</div>
          <div className="flex gap-3">
            {["interval","cron"].map(t => (
              <button key={t} onClick={() => setSchedType(t)}
                className={`px-3 py-1 rounded-full text-xs border transition ${
                  schedType === t ? "bg-gray-800 text-white" : "bg-white text-gray-600"
                }`}>{t}</button>
            ))}
          </div>
          {schedType === "interval" ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-600">Every</span>
              <input type="number" min="1" className="w-20 border rounded-lg px-2 py-1 text-sm"
                value={intervalHours} onChange={e => setIntervalHours(e.target.value)} />
              <span className="text-sm text-gray-600">hours</span>
            </div>
          ) : (
            <div className="space-y-1">
              <input className="w-full border rounded-lg px-3 py-2 text-sm font-mono"
                placeholder="cron expr e.g. 0 9 * * *" value={cron} onChange={e => setCron(e.target.value)} />
              <div className="text-xs text-gray-400">minute hour day month weekday</div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600">Cancel</button>
          <button onClick={submit} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            Create job
          </button>
        </div>
      </div>
    </div>
  );
}

function JobDetail({ job, onClose, onRunNow, onToggle }) {
  if (!job) return null;
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6 space-y-4 max-h-[85vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{job.name}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">&times;</button>
        </div>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-gray-500">Schedule: </span>
            {job.schedule_type === "interval" ? `Every ${job.interval_hours}h` : job.cron_expr}
          </div>
          <div><span className="text-gray-500">Status: </span>
            <span className={`font-medium ${job.enabled ? "text-green-600" : "text-gray-400"}`}>
              {job.enabled ? "Enabled" : "Disabled"}
            </span>
          </div>
          <div><span className="text-gray-500">Total runs: </span>{job.run_count}</div>
          <div><span className="text-gray-500">Next run: </span>
            {job.next_run_at ? new Date(job.next_run_at).toLocaleString() : "—"}
          </div>
          <div><span className="text-gray-500">Last run: </span>
            {job.last_run_at ? new Date(job.last_run_at).toLocaleString() : "Never"}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1">URLs ({job.urls.length})</div>
          <div className="bg-gray-50 rounded-lg p-3 max-h-28 overflow-y-auto text-xs font-mono space-y-1">
            {job.urls.map((u, i) => <div key={i} className="text-gray-700">{u}</div>)}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500 mb-1">Browsers</div>
          <div className="flex flex-wrap gap-1">
            {job.browsers.map(b => (
              <span key={b} className="px-2 py-0.5 bg-blue-50 text-blue-700 rounded-full text-xs">{b}</span>
            ))}
          </div>
        </div>
        {job.recent_runs?.length > 0 && (
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Recent runs</div>
            <div className="space-y-1">
              {job.recent_runs.map(r => (
                <div key={r.id} className="flex items-center justify-between text-xs border rounded-lg px-3 py-1.5">
                  <span className="text-gray-600">{new Date(r.created_at).toLocaleString()}</span>
                  <StatusPill status={r.status} />
                  <span className="text-gray-400">{r.completed_tasks}/{r.total_tasks}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="flex gap-3 pt-2">
          <button onClick={() => onRunNow(job.id)}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700">
            ▶ Run now
          </button>
          <button onClick={() => onToggle(job)}
            className={`px-4 py-2 text-sm rounded-lg border ${
              job.enabled ? "text-red-600 border-red-300 hover:bg-red-50" : "text-green-600 border-green-300 hover:bg-green-50"
            }`}>
            {job.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>
    </div>
  );
}

function JobsTab({ brands }) {
  const [jobs, setJobs] = useState([]);
  const [showNew, setShowNew] = useState(false);
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    fetch(`${API}/jobs`).then(r => r.json()).then(setJobs);
  }, []);

  useEffect(() => { load(); }, [load]);

  const createJob = async data => {
    await fetch(`${API}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setShowNew(false);
    load();
  };

  const openDetail = async id => {
    const d = await fetch(`${API}/jobs/${id}`).then(r => r.json());
    setSelected(d);
  };

  const runNow = async id => {
    await fetch(`${API}/jobs/${id}/run`, { method: "POST" });
    setSelected(null);
    load();
  };

  const toggleJob = async job => {
    await fetch(`${API}/jobs/${job.id}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !job.enabled }),
    });
    setSelected(null);
    load();
  };

  const deleteJob = async id => {
    if (!confirm("Delete this job?")) return;
    await fetch(`${API}/jobs/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-500">{jobs.length} scheduled job{jobs.length !== 1 ? "s" : ""}</div>
        <button onClick={() => setShowNew(true)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">
          + New job
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No scheduled jobs yet.</div>
      ) : (
        <div className="space-y-3">
          {jobs.map(job => (
            <div key={job.id} className="border rounded-xl p-4 space-y-2 hover:border-blue-300 transition">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <button onClick={() => openDetail(job.id)}
                    className="font-medium text-gray-900 hover:text-blue-600 text-left">
                    {job.name}
                  </button>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${job.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                    {job.enabled ? "active" : "paused"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => runNow(job.id)}
                    className="text-xs text-blue-600 hover:text-blue-800 px-2 py-1 border border-blue-200 rounded-lg">
                    ▶ Run now
                  </button>
                  <button onClick={() => deleteJob(job.id)}
                    className="text-xs text-red-400 hover:text-red-600">✕</button>
                </div>
              </div>
              <div className="text-xs text-gray-500 flex gap-4">
                <span>⏰ {job.schedule_type === "interval" ? `Every ${job.interval_hours}h` : job.cron_expr}</span>
                <span>🔗 {job.urls.length} URL{job.urls.length !== 1 ? "s" : ""}</span>
                <span>🏃 {job.run_count} runs</span>
                {job.next_run_at && <span>Next: {new Date(job.next_run_at).toLocaleString()}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {showNew && <NewJobDialog onClose={() => setShowNew(false)} onSubmit={createJob} brands={brands} />}
      {selected && (
        <JobDetail job={selected} onClose={() => setSelected(null)}
          onRunNow={runNow} onToggle={toggleJob} />
      )}
    </div>
  );
}

// ── VM Pool Tab ───────────────────────────────────────────────────────

function VMTab() {
  const [vms, setVms] = useState([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: "", azure_resource_group: "", azure_vm_name: "", snapshot_name: "", agent_url: "", agent_token: "" });

  const load = useCallback(() => {
    fetch(`${API}/vms`).then(r => r.json()).then(setVms);
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const addVM = async () => {
    await fetch(`${API}/vms`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    setShowAdd(false);
    setForm({ name: "", azure_resource_group: "", azure_vm_name: "", snapshot_name: "", agent_url: "", agent_token: "" });
    load();
  };

  const deleteVM = async id => {
    if (!confirm("Remove VM from pool?")) return;
    await fetch(`${API}/vms/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-500">{vms.length} VM{vms.length !== 1 ? "s" : ""} in pool</div>
        <button onClick={() => setShowAdd(s => !s)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700">
          + Add VM
        </button>
      </div>

      {showAdd && (
        <div className="border rounded-xl p-4 space-y-3 bg-gray-50">
          <div className="text-sm font-medium text-gray-700">Add VM to pool</div>
          {[
            ["name",                 "VM name (display)"],
            ["agent_url",            "Agent URL (http://ip:5000)"],
            ["agent_token",          "Agent token (optional)"],
            ["azure_resource_group", "Azure resource group"],
            ["azure_vm_name",        "Azure VM name"],
            ["snapshot_name",        "Snapshot name"],
          ].map(([k, ph]) => (
            <input key={k} className="w-full border rounded-lg px-3 py-2 text-sm"
              placeholder={ph} value={form[k]}
              onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
          ))}
          <div className="flex gap-2">
            <button onClick={addVM} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm">Add</button>
            <button onClick={() => setShowAdd(false)} className="px-4 py-2 border rounded-lg text-sm text-gray-600">Cancel</button>
          </div>
        </div>
      )}

      {vms.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No VMs configured yet.</div>
      ) : (
        <div className="space-y-3">
          {vms.map(vm => (
            <div key={vm.id} className="border rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-gray-900">🖥 {vm.name}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${VM_STATUS_COLOR[vm.status] || "bg-gray-100 text-gray-600"}`}>
                    {vm.status}
                  </span>
                </div>
                <button onClick={() => deleteVM(vm.id)} className="text-xs text-red-400 hover:text-red-600">Remove</button>
              </div>
              <div className="text-xs text-gray-500 flex flex-wrap gap-4">
                {vm.agent_url && <span>Agent: {vm.agent_url}</span>}
                {vm.azure_vm_name && <span>Azure: {vm.azure_vm_name}</span>}
                {vm.snapshot_name && <span>Snapshot: {vm.snapshot_name}</span>}
                {vm.current_run_id && <span className="text-blue-600">Run: {vm.current_run_id.slice(0,8)}</span>}
                {vm.last_heartbeat && <span>♥ {new Date(vm.last_heartbeat).toLocaleTimeString()}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────

export default function Dashboard() {
  const [tab, setTab] = useState("runs");   // "runs" | "jobs" | "vms"
  const [runs, setRuns] = useState([]);
  const [brands, setBrands] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [showNewRun, setShowNewRun] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const wsRef = useRef(null);

  // Filters
  const [brandFilter, setBrandFilter]   = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [dateFrom, setDateFrom]         = useState("");
  const [dateTo, setDateTo]             = useState("");

  const loadRuns = useCallback(() => {
    const p = new URLSearchParams();
    if (brandFilter)  p.set("brand_id",  brandFilter);
    if (statusFilter) p.set("status",    statusFilter);
    if (dateFrom)     p.set("date_from", dateFrom);
    if (dateTo)       p.set("date_to",   dateTo);
    fetch(`${API}/runs?${p}`).then(r => r.json()).then(setRuns);
  }, [brandFilter, statusFilter, dateFrom, dateTo]);

  useEffect(() => {
    fetch(`${API}/brands`).then(r => r.json()).then(setBrands);
    loadRuns();
  }, [loadRuns]);

  // WebSocket live updates
  useEffect(() => {
    let ws, reconnectTimer;
    const connect = () => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${window.location.host}/ws`);
      ws.onmessage = e => {
        const d = JSON.parse(e.data);
        if (["run_created","task_update","run_status"].includes(d.type)) loadRuns();
      };
      ws.onclose = () => { reconnectTimer = setTimeout(connect, 3000); };
      wsRef.current = ws;
    };
    connect();
    const poll = setInterval(loadRuns, 6000);
    return () => { clearInterval(poll); clearTimeout(reconnectTimer); ws?.close(); };
  }, [loadRuns]);

  const createRun = async data => {
    await fetch(`${API}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    setShowNewRun(false);
    loadRuns();
  };

  // If a run is selected go full detail
  if (selectedRun) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <RunDetail run={selectedRun} onBack={() => { setSelectedRun(null); loadRuns(); }} />
      </div>
    );
  }

  const TABS = [
    { id: "runs", label: "Test runs" },
    { id: "jobs", label: "Scheduled jobs" },
    { id: "vms",  label: "VM pool" },
  ];

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Download Sentinel</h1>
          <p className="text-sm text-gray-500 mt-0.5">Automated download testing across browsers & VMs</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowSettings(true)}
            className="px-4 py-2 border rounded-lg text-sm font-medium text-gray-600 hover:text-gray-800 transition">
            Settings
          </button>
          <button onClick={() => setShowNewRun(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition">
            New run
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b gap-1">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === t.id ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}>{t.label}</button>
        ))}
      </div>

      {/* ── Runs tab ── */}
      {tab === "runs" && (
        <div className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3">
            <select className="border rounded-lg px-3 py-2 text-sm"
              value={brandFilter} onChange={e => setBrandFilter(e.target.value)}>
              <option value="">All brands</option>
              {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
            <select className="border rounded-lg px-3 py-2 text-sm"
              value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              {["queued","waiting_for_vm","provisioning","running","completed","failed","cancelled"].map(s => (
                <option key={s} value={s}>{s.replace(/_/g," ")}</option>
              ))}
            </select>
            <input type="date" className="border rounded-lg px-3 py-2 text-sm"
              value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            <span className="text-gray-400 text-sm">→</span>
            <input type="date" className="border rounded-lg px-3 py-2 text-sm"
              value={dateTo} onChange={e => setDateTo(e.target.value)} />
            <button onClick={loadRuns} className="text-sm text-blue-600 hover:text-blue-800">Refresh</button>
          </div>

          {/* Run list */}
          {runs.length === 0 ? (
            <div className="text-center py-12 text-gray-400">No test runs yet. Click "New run" to start.</div>
          ) : (
            <div className="space-y-3">
              {runs.map(run => {
                const summary = run.outcome_summary || {};
                return (
                  <div key={run.id} onClick={() => setSelectedRun(run)}
                    className="border rounded-xl p-4 hover:border-blue-300 hover:shadow-sm cursor-pointer transition space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="font-medium text-gray-900 truncate">
                          {run.name || `Run ${run.id.slice(0, 8)}`}
                        </div>
                        {run.triggered_by?.startsWith("scheduled:") && (
                          <span className="text-xs text-purple-500 shrink-0">⏰</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <StatusPill status={run.status} />
                      </div>
                    </div>
                    <ProgressBar completed={run.completed_tasks} total={run.total_tasks} />
                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <span>{new Date(run.created_at).toLocaleString()} — {run.total_tasks} tasks</span>
                      <span>{run.completed_tasks}/{run.total_tasks} done</span>
                    </div>
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
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Jobs tab ── */}
      {tab === "jobs" && <JobsTab brands={brands} />}

      {/* ── VM Pool tab ── */}
      {tab === "vms" && <VMTab />}

      {/* Dialogs */}
      {showNewRun && (
        <NewRunDialog onClose={() => setShowNewRun(false)} onSubmit={createRun} brands={brands} />
      )}
      {showSettings && (
        <SettingsDialog onClose={() => {
          setShowSettings(false);
          loadRuns();
          fetch(`${API}/brands`).then(r => r.json()).then(setBrands);
        }} />
      )}
    </div>
  );
}