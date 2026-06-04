import { useState, useEffect, useRef } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:5001";

const STATUS = {
  idle:     { label: "Ready",    color: "#6b7280" },
  running:  { label: "Running",  color: "#f59e0b" },
  success:  { label: "Done",     color: "#10b981" },
  error:    { label: "#Error",   color: "#ef4444" },
};

function Dot({ status }) {
  const s = STATUS[status] || STATUS.idle;
  return (
    <span style={{
      display: "inline-block", width: 8, height: 8,
      borderRadius: "50%", background: s.color,
      marginRight: 6,
      boxShadow: status === "running" ? `0 0 0 3px ${s.color}33` : "none",
      animation: status === "running" ? "pulse 1.5s ease-in-out infinite" : "none"
    }} />
  );
}

function GoalTag({ goal, index, onDelete }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      background: "#1a1a2e", border: "1px solid #2d2d44",
      borderRadius: 8, padding: "8px 12px",
      fontSize: 14, color: "#e2e8f0"
    }}>
      <span style={{ flex: 1 }}>{goal}</span>
      <button
        onClick={() => onDelete(index)}
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: "#6b7280", fontSize: 16, padding: "0 2px",
          lineHeight: 1, borderRadius: 4
        }}
        title="Remove"
      >×</button>
    </div>
  );
}

function LogEntry({ deploy }) {
  const d = deploy?.deploy || deploy;
  const status = d?.status || "unknown";
  const finishedAt = d?.finishedAt ? new Date(d.finishedAt).toLocaleString() : "—";
  const color = status === "live" ? "#10b981" : status === "failed" ? "#ef4444" : "#f59e0b";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 0", borderBottom: "1px solid #1e1e30",
      fontSize: 13, color: "#94a3b8"
    }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0 }} />
      <span style={{ flex: 1, color: "#e2e8f0" }}>{d?.id?.slice(0, 10) || "—"}</span>
      <span style={{ color }}>{status}</span>
      <span>{finishedAt}</span>
    </div>
  );
}

export default function App() {
  const [goals,     setGoals]     = useState([]);
  const [input,     setInput]     = useState("");
  const [runStatus, setRunStatus] = useState("idle");
  const [runMsg,    setRunMsg]    = useState("");
  const [logs,      setLogs]      = useState([]);
  const [tab,       setTab]       = useState("goals");
  const inputRef = useRef(null);

  useEffect(() => {
    fetch(`${API}/api/goals`)
      .then(r => r.json())
      .then(setGoals)
      .catch(() => {});
  }, []);

  async function addGoal() {
    const g = input.trim();
    if (!g) return;
    const res = await fetch(`${API}/api/goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal: g })
    });
    const data = await res.json();
    setGoals(data);
    setInput("");
    inputRef.current?.focus();
  }

  async function deleteGoal(index) {
    const res = await fetch(`${API}/api/goals/${index}`, { method: "DELETE" });
    const data = await res.json();
    setGoals(data);
  }

  async function triggerRun() {
    setRunStatus("running");
    setRunMsg("Triggering run on Render...");
    try {
      const res  = await fetch(`${API}/api/run`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        setRunStatus("error");
        setRunMsg(data.error);
      } else {
        setRunStatus("success");
        setRunMsg("Run triggered! Check Slack in a few minutes.");
        setTimeout(() => { setRunStatus("idle"); setRunMsg(""); }, 5000);
      }
    } catch {
      setRunStatus("error");
      setRunMsg("Could not reach backend.");
    }
  }

  async function fetchLogs() {
    setTab("logs");
    try {
      const res  = await fetch(`${API}/api/logs`);
      const data = await res.json();
      setLogs(Array.isArray(data) ? data : []);
    } catch {
      setLogs([]);
    }
  }

  return (
    <div style={{
      minHeight: "100vh", background: "#0d0d1a", color: "#e2e8f0",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      display: "flex", flexDirection: "column", alignItems: "center",
      padding: "48px 16px"
    }}>
      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
        * { box-sizing: border-box; }
        input::placeholder { color: #4b5563; }
        button:hover { opacity: .85; }
      `}</style>

      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: 48 }}>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 10,
          background: "#1a1a2e", border: "1px solid #2d2d44",
          borderRadius: 12, padding: "8px 20px", marginBottom: 20
        }}>
          <span style={{ fontSize: 18 }}>🎯</span>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "0.02em" }}>Job Scout</span>
        </div>
        <p style={{ color: "#6b7280", fontSize: 14, margin: 0 }}>
          Describe what you want. Scout finds it.
        </p>
      </div>

      {/* Main card */}
      <div style={{
        width: "100%", maxWidth: 640,
        background: "#13131f", border: "1px solid #1e1e30",
        borderRadius: 16, overflow: "hidden"
      }}>

        {/* Tabs */}
        <div style={{ display: "flex", borderBottom: "1px solid #1e1e30" }}>
          {[["goals", "Goals"], ["logs", "Run Logs"]].map(([id, label]) => (
            <button key={id}
              onClick={() => id === "logs" ? fetchLogs() : setTab(id)}
              style={{
                flex: 1, padding: "14px 0", background: "none",
                border: "none", cursor: "pointer", fontSize: 13, fontWeight: 500,
                color: tab === id ? "#818cf8" : "#6b7280",
                borderBottom: tab === id ? "2px solid #818cf8" : "2px solid transparent",
                transition: "color .15s"
              }}
            >{label}</button>
          ))}
        </div>

        {/* Goals tab */}
        {tab === "goals" && (
          <div style={{ padding: 24 }}>

            {/* Input */}
            <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
              <input
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && addGoal()}
                placeholder='e.g. "founders office roles in Bengaluru"'
                style={{
                  flex: 1, background: "#0d0d1a", border: "1px solid #2d2d44",
                  borderRadius: 8, padding: "10px 14px", fontSize: 14,
                  color: "#e2e8f0", outline: "none"
                }}
              />
              <button
                onClick={addGoal}
                style={{
                  background: "#818cf8", border: "none", borderRadius: 8,
                  padding: "10px 18px", fontSize: 14, fontWeight: 500,
                  color: "#fff", cursor: "pointer", whiteSpace: "nowrap"
                }}
              >Add goal</button>
            </div>

            {/* Goals list */}
            {goals.length === 0 ? (
              <div style={{
                textAlign: "center", padding: "32px 0",
                color: "#4b5563", fontSize: 14
              }}>
                No goals yet — add one above
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 24 }}>
                {goals.map((g, i) => (
                  <GoalTag key={i} goal={g} index={i} onDelete={deleteGoal} />
                ))}
              </div>
            )}

            {/* Run button */}
            <button
              onClick={triggerRun}
              disabled={runStatus === "running" || goals.length === 0}
              style={{
                width: "100%", padding: "13px 0",
                background: runStatus === "running" ? "#2d2d44" : "#818cf8",
                border: "none", borderRadius: 10, fontSize: 15, fontWeight: 600,
                color: runStatus === "running" ? "#6b7280" : "#fff",
                cursor: runStatus === "running" || goals.length === 0 ? "not-allowed" : "pointer",
                transition: "background .2s"
              }}
            >
              <Dot status={runStatus} />
              {runStatus === "running" ? "Running..." : `Run Scout now (${goals.length} goal${goals.length !== 1 ? "s" : ""})`}
            </button>

            {runMsg && (
              <p style={{
                marginTop: 12, textAlign: "center", fontSize: 13,
                color: runStatus === "error" ? "#ef4444" : "#10b981"
              }}>{runMsg}</p>
            )}

            <p style={{
              marginTop: 16, textAlign: "center", fontSize: 12,
              color: "#4b5563"
            }}>
              Results → your Slack channels + Google Sheet
            </p>
          </div>
        )}

        {/* Logs tab */}
        {tab === "logs" && (
          <div style={{ padding: 24 }}>
            <p style={{ fontSize: 13, color: "#6b7280", marginBottom: 16 }}>
              Last 5 Render deploys
            </p>
            {logs.length === 0 ? (
              <div style={{ textAlign: "center", padding: "32px 0", color: "#4b5563", fontSize: 14 }}>
                No logs found
              </div>
            ) : (
              logs.map((log, i) => <LogEntry key={i} deploy={log} />)
            )}
          </div>
        )}
      </div>

      <p style={{ marginTop: 24, fontSize: 12, color: "#374151" }}>
        Runs automatically at 9am + 6pm IST · Powered by Gemini 2.5 Flash
      </p>
    </div>
  );
}
