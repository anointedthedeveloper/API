import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  VscAdd,
  VscCircleFilled,
  VscClearAll,
  VscCopy,
  VscDebugStop,
  VscHistory,
  VscSend,
} from "react-icons/vsc";
import "./AiChat.css";

const API_URL = "http://localhost:8080/v1/chat/completions";

const DEFAULT_SESSION = () => ({
  id: `${Date.now()}`,
  title: "New Chat",
  createdAt: Date.now(),
  messages: [
    {
      role: "assistant",
      content: "Hi, I am ANAI. I can explain code, create or edit files in the open folder, and run terminal commands when you ask.",
      timestamp: Date.now(),
    },
  ],
});

const buildSystemPrompt = (workspaceName, fileTree) => `You are ANAI, a coding AI assistant inside a VS Code-like IDE.
The owner and creator of this AI is anointedthedeveloper.
Be concise, practical, and friendly.
Current workspace: ${workspaceName || "No folder selected"}.

You can request file actions by including fenced tool blocks:
\`\`\`anai-write path=relative/path.ext
file contents here
\`\`\`
\`\`\`anai-read path=relative/path.ext
\`\`\`
\`\`\`anai-mkdir path=relative/folder
\`\`\`

Only write files when the user asks. Use relative paths inside the workspace.
${fileTree ? `\nWorkspace files:\n${fileTree}` : ""}`;

const extractActions = (text) => {
  const actions = [];
  const writeRe = /```anai-(?:write|create|edit)\s+path=([^\n]+)\n([\s\S]*?)```/gi;
  const readRe  = /```anai-read\s+path=([^\n]+)\s*```/gi;
  const mkdirRe = /```anai-mkdir\s+path=([^\n]+)\s*```/gi;
  let m;
  while ((m = writeRe.exec(text))  !== null) actions.push({ type: "write",  path: m[1].trim(), content: m[2].replace(/\n$/, "") });
  while ((m = readRe.exec(text))   !== null) actions.push({ type: "read",   path: m[1].trim() });
  while ((m = mkdirRe.exec(text))  !== null) actions.push({ type: "mkdir",  path: m[1].trim() });
  return actions;
};

const stripActionBlocks = (text) =>
  text
    .replace(/```anai-(?:write|create|edit)\s+path=[^\n]+\n[\s\S]*?```/gi, "")
    .replace(/```anai-(?:read|mkdir)\s+path=[^\n]+\s*```/gi, "")
    .trim();

const cleanText = (text) =>
  stripActionBlocks(text)
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/<think>[\s\S]*$/gi, "")
    .trim();

// Resolve a relative path like "src/foo/bar.js" into a FileSystemDirectoryHandle
async function resolveHandle(rootHandle, relPath, isFile) {
  const parts = relPath.replace(/\\/g, "/").split("/").filter(Boolean);
  let dir = rootHandle;
  for (let i = 0; i < parts.length - 1; i++) {
    dir = await dir.getDirectoryHandle(parts[i], { create: true });
  }
  const name = parts[parts.length - 1];
  if (isFile) return dir.getFileHandle(name, { create: true });
  return dir.getDirectoryHandle(name, { create: true });
}

async function readHandleFile(rootHandle, relPath) {
  const fh = await resolveHandle(rootHandle, relPath, true);
  const file = await fh.getFile();
  return file.text();
}

async function writeHandleFile(rootHandle, relPath, content) {
  const fh = await resolveHandle(rootHandle, relPath, true);
  const w = await fh.createWritable();
  await w.write(content);
  await w.close();
}

const flattenTree = (items = [], depth = 0, limit = 80, lines = []) => {
  for (const item of items) {
    if (lines.length >= limit) break;
    lines.push(`${"  ".repeat(depth)}${item.type === "folder" ? "/" : ""}${item.name}`);
    if (item.children) flattenTree(item.children, depth + 1, limit, lines);
  }
  return lines;
};

const renderContent = (content) =>
  content.split(/(```[\s\S]*?```)/g).filter(Boolean).map((part, i) => {
    if (!part.startsWith("```")) return <span key={i} className="message-text-part">{part}</span>;
    const m = part.match(/^```([^\n]*)\n?([\s\S]*?)```$/);
    const lang = m?.[1]?.trim() || "text";
    const code = m?.[2] || "";
    return (
      <div key={i} className="code-block">
        <div className="code-block-header">
          <span>{lang}</span>
          <button type="button" onClick={() => navigator.clipboard?.writeText(code)}><VscCopy /> Copy</button>
        </div>
        <pre><code>{code}</code></pre>
      </div>
    );
  });

function AiChat({ workspaceName, dirHandle, fileTree, onWorkspaceRefresh, onOpenFile, onTerminalOutput }) {
  const [sessions, setSessions] = useState(() => {
    try {
      const s = JSON.parse(localStorage.getItem("anai.chatSessions") || "[]");
      return s.length ? s : [DEFAULT_SESSION()];
    } catch { return [DEFAULT_SESSION()]; }
  });
  const [activeId, setActiveId] = useState(() => sessions[0]?.id);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const abortRef = useRef(null);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  const active = sessions.find((s) => s.id === activeId) || sessions[0];
  const messages = useMemo(() => active?.messages || [], [active]);

  useEffect(() => {
    localStorage.setItem("anai.chatSessions", JSON.stringify(sessions.slice(0, 20)));
  }, [sessions]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const updateActive = useCallback((fn) => {
    setSessions((prev) => prev.map((s) => s.id === activeId ? fn(s) : s));
  }, [activeId]);

  const push = useCallback((msg) => {
    updateActive((s) => ({
      ...s,
      title: s.title === "New Chat" && msg.role === "user" ? msg.content.slice(0, 36) || "New Chat" : s.title,
      messages: [...s.messages, { ...msg, timestamp: Date.now() }],
    }));
  }, [updateActive]);

  const patchLast = useCallback((fn) => {
    updateActive((s) => {
      const msgs = [...s.messages];
      const idx = msgs.map((m) => m.role).lastIndexOf("assistant");
      if (idx >= 0) msgs[idx] = fn(msgs[idx]);
      return { ...s, messages: msgs };
    });
  }, [updateActive]);

  const runActions = useCallback(async (text) => {
    const actions = extractActions(text);
    if (!actions.length) return;
    if (!dirHandle) {
      push({ role: "assistant", content: "No folder open — cannot write files." });
      return;
    }
    const results = [];
    for (const a of actions) {
      try {
        if (a.type === "write") {
          await writeHandleFile(dirHandle, a.path, a.content);
          onWorkspaceRefresh?.();
          results.push(`✓ Wrote ${a.path}`);
          // Try to open the file in the editor
          try {
            const fh = await resolveHandle(dirHandle, a.path, true);
            onOpenFile?.(fh, a.path);
          } catch {}
        } else if (a.type === "read") {
          const content = await readHandleFile(dirHandle, a.path);
          results.push(`Read ${a.path}:\n\`\`\`\n${content.slice(0, 4000)}\n\`\`\``);
        } else if (a.type === "mkdir") {
          await resolveHandle(dirHandle, a.path, false);
          onWorkspaceRefresh?.();
          results.push(`✓ Created folder ${a.path}`);
        }
      } catch (e) {
        results.push(`✗ ${a.type} ${a.path}: ${e.message}`);
      }
    }
    push({ role: "assistant", content: results.join("\n") });
  }, [dirHandle, onOpenFile, onWorkspaceRefresh, push]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
    patchLast((m) => ({ ...m, pending: false, stopped: true }));
  }, [patchLast]);

  const send = useCallback(async (e) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text) { if (loading) stop(); return; }
    if (loading) stop();

    push({ role: "user", content: text });
    push({ role: "assistant", content: "", pending: true });
    setInput("");
    setLoading(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let full = "";

    try {
      const history = messages.slice(-8).map((m) => ({
        role: m.role,
        content: m.content.slice(0, 600),
      }));

      const treeStr = fileTree ? flattenTree(fileTree).join("\n") : "";
      const sysPrompt = buildSystemPrompt(workspaceName, treeStr);

      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: ctrl.signal,
        body: JSON.stringify({
          model: "deepseek-chat",
          stream: true,
          messages: [
            { role: "user", content: sysPrompt },
            ...history,
            { role: "user", content: text },
          ],
        }),
      });

      if (!res.ok) throw new Error(`API error ${res.status}`);

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") break;
          try {
            const chunk = JSON.parse(payload);
            const delta = chunk.choices?.[0]?.delta?.content || "";
            if (delta) {
              full += delta;
              const snapshot = full;
              patchLast((m) => ({ ...m, content: cleanText(snapshot) }));
            }
          } catch {}
        }
      }

      patchLast((m) => ({ ...m, content: cleanText(full) || "Done.", pending: false }));
      await runActions(full);
    } catch (err) {
      if (err.name !== "AbortError") {
        patchLast((m) => ({
          ...m,
          content: `Error: ${err.message}. Make sure the DeepSeek API server is running on port 8080.`,
          pending: false,
          error: true,
        }));
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [fileTree, input, loading, messages, patchLast, push, runActions, stop, workspaceName]);

  const newChat = () => {
    const s = DEFAULT_SESSION();
    setSessions((prev) => [s, ...prev]);
    setActiveId(s.id);
    setInput("");
  };

  return (
    <div className="ai-chat-container q-chat">
      <aside className="chat-history">
        <button className="history-action" onClick={newChat}><VscAdd /> New</button>
        <div className="history-title"><VscHistory /> History</div>
        <div className="history-list">
          {sessions.map((s) => (
            <button key={s.id} className={`history-item ${s.id === activeId ? "active" : ""}`} onClick={() => setActiveId(s.id)}>
              {s.title}
            </button>
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <div className="chat-header">
          <div className="chat-title-section">
            <h3 className="chat-title">ANAI</h3>
            <p className="chat-subtitle">Owner: anointedthedeveloper</p>
            <div className="model-status">
              <VscCircleFilled /> deepseek-chat via localhost:8080
            </div>
          </div>
          <button className="clear-btn" onClick={() => updateActive((s) => ({ ...s, messages: DEFAULT_SESSION().messages }))} title="Clear chat"><VscClearAll /></button>
        </div>

        <div className="messages-container">
          {messages.map((msg, i) => (
            <div key={i} className={`message message-${msg.role} ${msg.error ? "error" : ""} ${msg.pending ? "message-pending" : ""}`}>
              <div className="message-role">{msg.role === "user" ? "You" : "ANAI"}</div>
              <div className="message-content">{renderContent(msg.content)}</div>
              {msg.pending && !msg.content && (
                <div className="loader-row"><span /><span /><span /> Thinking...</div>
              )}
              <div className="message-meta">
                <span>{new Date(msg.timestamp).toLocaleTimeString()}</span>
                {msg.stopped && <span>Stopped</span>}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <form className="input-form" onSubmit={send}>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={loading ? "Generating... press Enter to stop" : "Ask ANAI anything..."}
            className="chat-input"
          />
          <button type={loading ? "button" : "submit"} className={`send-btn ${loading ? "stop" : ""}`} onClick={loading ? stop : undefined}>
            {loading ? <VscDebugStop /> : <VscSend />}
          </button>
        </form>
      </section>
    </div>
  );
}

export default AiChat;
