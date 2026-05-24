import React, { useState, useEffect, useRef, useCallback, useMemo, lazy, Suspense } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import {
  VscChromeClose, VscFile, VscFolderOpened, VscNewFolder, VscNewFile,
  VscSave, VscSync, VscTerminal, VscFileCode, VscJson, VscGear,
  VscColorMode,
} from "react-icons/vsc";
import AiChat from "./AiChat";
import "./App.css";

const CodeEditor = lazy(() => import("./EnhancedCodeEditor"));

// ── File System Access API helpers ──────────────────────────────────────────

async function resolveHandle(rootHandle, relPath, isFile) {
  const parts = relPath.replace(/\\/g, "/").split("/").filter(Boolean);
  let dir = rootHandle;
  for (let i = 0; i < parts.length - 1; i++) {
    dir = await dir.getDirectoryHandle(parts[i], { create: true });
  }
  const name = parts[parts.length - 1];
  return isFile
    ? dir.getFileHandle(name, { create: true })
    : dir.getDirectoryHandle(name, { create: true });
}

async function readFileHandle(fh) {
  const file = await fh.getFile();
  return file.text();
}

async function writeFileHandle(fh, content) {
  const w = await fh.createWritable();
  await w.write(content);
  await w.close();
}

async function buildTree(dirHandle, path = "") {
  const items = [];
  for await (const [name, handle] of dirHandle.entries()) {
    if (name === "node_modules" || name === ".git") continue;
    const relPath = path ? `${path}/${name}` : name;
    if (handle.kind === "directory") {
      items.push({ name, path: relPath, type: "folder", handle, children: await buildTree(handle, relPath) });
    } else {
      items.push({ name, path: relPath, type: "file", handle });
    }
  }
  return items.sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

// ── Language / icon helpers ──────────────────────────────────────────────────

const EXT_LANG = { js:"javascript", jsx:"javascript", ts:"typescript", tsx:"typescript", py:"python", java:"java", cpp:"cpp", c:"c", cs:"csharp", php:"php", rb:"ruby", go:"go", rs:"rust", sql:"sql", html:"html", css:"css", scss:"scss", json:"json", xml:"xml", yaml:"yaml", yml:"yaml", md:"markdown", sh:"shell", bash:"shell" };
const getLang = (name) => EXT_LANG[name.split(".").pop()?.toLowerCase()] || "plaintext";

const getIcon = (name, isFolder) => {
  if (isFolder) return <VscFolderOpened />;
  const ext = name.split(".").pop()?.toLowerCase();
  if (["js","jsx","ts","tsx","html","css","scss","py","java","cpp","c","cs","rb","go","rs","php"].includes(ext)) return <VscFileCode />;
  if (["json","jsonc"].includes(ext)) return <VscJson />;
  if (["env","gitignore","makefile"].includes(ext)) return <VscGear />;
  return <VscFile />;
};

// ── App ──────────────────────────────────────────────────────────────────────

function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem("anai.theme") || "dark");
  const [dirHandle, setDirHandle] = useState(null);       // FileSystemDirectoryHandle
  const [fileTree, setFileTree] = useState([]);
  const [openFiles, setOpenFiles] = useState([]);          // [{name, path, handle}]
  const [activeFile, setActiveFile] = useState(null);      // path string
  const [fileContent, setFileContent] = useState("");
  const [fileHandles, setFileHandles] = useState({});      // path → FileSystemFileHandle
  const [terminalOutput, setTerminalOutput] = useState([]);
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalCwd, setTerminalCwd] = useState(null);
  const [showExplorer, setShowExplorer] = useState(true);
  const [showChat, setShowChat] = useState(true);
  const [showTerminal, setShowTerminal] = useState(true);
  const [createDialog, setCreateDialog] = useState(null);  // {mode:'file'|'folder', value:'', error:''}
  const terminalRef = useRef(null);
  const terminalInputRef = useRef(null);
  const editorRef = useRef(null);

  useEffect(() => {
    localStorage.setItem("anai.theme", theme);
    document.documentElement.classList.toggle("light-theme", theme === "light");
  }, [theme]);

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
  }, [terminalOutput]);

  // ── Workspace ──────────────────────────────────────────────────────────────

  const refreshTree = useCallback(async (handle) => {
    const root = handle || dirHandle;
    if (!root) return;
    const tree = await buildTree(root);
    setFileTree(tree);
  }, [dirHandle]);

  const openFolder = useCallback(async () => {
    try {
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      setDirHandle(handle);
      setOpenFiles([]);
      setActiveFile(null);
      setFileContent("");
      setFileHandles({});
      const tree = await buildTree(handle);
      setFileTree(tree);
    } catch (e) {
      if (e.name !== "AbortError") alert(`Could not open folder: ${e.message}`);
    }
  }, []);

  // ── File ops ───────────────────────────────────────────────────────────────

  const openFileByHandle = useCallback(async (fh, relPath) => {
    try {
      const content = await readFileHandle(fh);
      const name = relPath.split("/").pop();
      setFileHandles((prev) => ({ ...prev, [relPath]: fh }));
      setOpenFiles((prev) => prev.some((f) => f.path === relPath) ? prev : [...prev, { name, path: relPath }]);
      setActiveFile(relPath);
      setFileContent(content);
    } catch (e) {
      console.error("open file error", e);
    }
  }, []);

  const openFileByPath = useCallback(async (relPath) => {
    if (!dirHandle) return;
    try {
      const fh = await resolveHandle(dirHandle, relPath, true);
      await openFileByHandle(fh, relPath);
    } catch (e) {
      console.error("open file by path error", e);
    }
  }, [dirHandle, openFileByHandle]);

  const saveFile = useCallback(async () => {
    if (!activeFile) return;
    const fh = fileHandles[activeFile];
    if (!fh) return;
    try {
      await writeFileHandle(fh, fileContent);
    } catch (e) {
      alert(`Save failed: ${e.message}`);
    }
  }, [activeFile, fileContent, fileHandles]);

  const closeFile = useCallback((path) => {
    setOpenFiles((prev) => {
      const next = prev.filter((f) => f.path !== path);
      if (activeFile === path) {
        if (next.length) openFileByPath(next[0].path);
        else { setActiveFile(null); setFileContent(""); }
      }
      return next;
    });
  }, [activeFile, openFileByPath]);

  // ── Create file/folder dialog ──────────────────────────────────────────────

  const submitCreate = useCallback(async () => {
    if (!createDialog?.value?.trim()) {
      setCreateDialog((d) => ({ ...d, error: "Name cannot be empty" }));
      return;
    }
    if (/[<>:"|?*\\]/.test(createDialog.value)) {
      setCreateDialog((d) => ({ ...d, error: "Invalid characters in name" }));
      return;
    }
    try {
      if (createDialog.mode === "file") {
        const fh = await resolveHandle(dirHandle, createDialog.value.trim(), true);
        await writeFileHandle(fh, "");
        await refreshTree();
        await openFileByHandle(fh, createDialog.value.trim());
      } else {
        await resolveHandle(dirHandle, createDialog.value.trim(), false);
        await refreshTree();
      }
      setCreateDialog(null);
    } catch (e) {
      setCreateDialog((d) => ({ ...d, error: e.message }));
    }
  }, [createDialog, dirHandle, openFileByHandle, refreshTree]);

  // ── Terminal ───────────────────────────────────────────────────────────────
  const runTerminal = useCallback(async (cmd) => {
    if (!cmd.trim()) return;
    setTerminalOutput((prev) => [...prev, `$ ${cmd}`]);
    setTerminalInput("");
    if (cmd.trim() === "clear") { setTerminalOutput([]); return; }
    try {
      const res = await fetch("http://localhost:8080/api/terminal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd, cwd: terminalCwd }),
      });
      const data = await res.json();
      if (data.output) setTerminalOutput((prev) => [...prev, data.output]);
      // track cd commands
      const cdMatch = cmd.trim().match(/^cd\s+(.+)/);
      if (cdMatch && data.exitCode === 0) {
        setTerminalCwd((prev) => {
          const target = cdMatch[1].trim();
          if (!prev) return target;
          return target.startsWith("/") || /^[A-Za-z]:/.test(target) ? target : `${prev}/${target}`;
        });
      }
    } catch {
      setTerminalOutput((prev) => [...prev, "⚠ Could not reach backend on port 8080."]);
    }
  }, [terminalCwd]);

  // ── Keyboard shortcuts ─────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === "b") { e.preventDefault(); setShowExplorer((v) => !v); }
      else if (mod && e.key === "`") { e.preventDefault(); setShowTerminal((v) => !v); }
      else if (mod && e.shiftKey && e.key.toLowerCase() === "a") { e.preventDefault(); setShowChat((v) => !v); }
      else if (mod && e.key === "s") { e.preventDefault(); saveFile(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [saveFile]);

  // ── File tree renderer ─────────────────────────────────────────────────────

  const renderTree = useCallback((items, depth = 0) =>
    items.map((item, i) => (
      <div key={`${item.path}-${i}`} className={`file-item file-depth-${depth}`}
        onClick={() => item.type === "file" ? openFileByHandle(item.handle, item.path) : null}
        style={{ cursor: item.type === "file" ? "pointer" : "default" }}
      >
        <span className="file-icon">{getIcon(item.name, item.type === "folder")}</span>
        <span className="file-name">{item.name}</span>
        {item.children?.length > 0 && renderTree(item.children, depth + 1)}
      </div>
    )), [openFileByHandle]);

  const treeEl = useMemo(() => renderTree(fileTree), [fileTree, renderTree]);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="ide-container">
      <header className="header">
        <div className="header-left">
          <div className="brand-mark">A</div>
          <div><h1>ANAI</h1><p>AI development workspace</p></div>
        </div>
        <div className="header-actions">
          <button onClick={openFolder}><VscNewFolder className="header-icon" /> Open Folder</button>
          <button onClick={() => refreshTree()} disabled={!dirHandle}><VscSync className="header-icon" /> Refresh</button>
          <button onClick={() => setTerminalOutput([])}><VscTerminal className="header-icon" /> Clear Terminal</button>
          <button onClick={() => setShowExplorer((v) => !v)} title="Ctrl+B">Explorer</button>
          <button onClick={() => setShowTerminal((v) => !v)} title="Ctrl+`">Terminal</button>
          <button onClick={() => setShowChat((v) => !v)} title="Ctrl+Shift+A">Chat</button>
          <button onClick={() => setTheme((t) => t === "dark" ? "light" : "dark")} title="Toggle theme">
            <VscColorMode className="header-icon" /> {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>

      <div className="workspace-shell">
        <PanelGroup direction="horizontal" className="panel-workspace">
          {showExplorer && (
            <>
              <Panel defaultSize={18} minSize={12} maxSize={34} className="explorer-panel">
                <div className="panel-header">
                  <h2>Explorer</h2>
                  <div className="panel-actions">
                    <button onClick={() => dirHandle && setCreateDialog({ mode: "file", value: "", error: "" })} title="New File" disabled={!dirHandle}><VscNewFile /></button>
                    <button onClick={() => dirHandle && setCreateDialog({ mode: "folder", value: "", error: "" })} title="New Folder" disabled={!dirHandle}><VscNewFolder /></button>
                    <button onClick={() => refreshTree()} disabled={!dirHandle} title="Refresh"><VscSync /></button>
                  </div>
                </div>
                {!dirHandle ? (
                  <div className="empty-workspace">
                    <div className="empty-workspace-title">No folder open</div>
                    <button onClick={openFolder}><VscNewFolder /> Open Folder</button>
                  </div>
                ) : (
                  <div className="file-tree">{treeEl}</div>
                )}
              </Panel>
              <PanelResizeHandle className="resize-handle" />
            </>
          )}

          <Panel minSize={36} defaultSize={showChat ? 52 : 82} className="center-panel">
            <PanelGroup direction="vertical">
              <Panel defaultSize={68} minSize={35} className="center-panel">
                <div className="editor-toolbar">
                  <div className="editor-path">{activeFile || "No file open"}</div>
                  <div className="editor-actions">
                    <button className="save-button" onClick={() => editorRef.current?.undo()} disabled={!activeFile}>Undo</button>
                    <button className="save-button" onClick={() => editorRef.current?.redo()} disabled={!activeFile}>Redo</button>
                    <button className="save-button" onClick={saveFile} disabled={!activeFile} title="Ctrl+S"><VscSave /> Save</button>
                    <button className="save-button" onClick={() => editorRef.current?.formatDocument?.()} disabled={!activeFile}>⚡ Format</button>
                  </div>
                </div>
                <div className="file-tabs">
                  {openFiles.map((f, i) => (
                    <div key={`${f.path}-${i}`} className={`file-tab ${activeFile === f.path ? "active" : ""}`} onClick={() => openFileByPath(f.path)}>
                      <span>{f.name}</span>
                      <button className="close-tab" onClick={(e) => { e.stopPropagation(); closeFile(f.path); }}><VscChromeClose /></button>
                    </div>
                  ))}
                </div>
                <div className="code-editor-area">
                  {activeFile ? (
                    <Suspense fallback={<div className="editor-loading">Loading editor...</div>}>
                      <CodeEditor ref={editorRef} value={fileContent} onChange={setFileContent} language={getLang(activeFile)} theme="vs-dark" />
                    </Suspense>
                  ) : (
                    <div className="no-file-open"><p>Select a file from the explorer to start editing.</p></div>
                  )}
                </div>
              </Panel>

              {showTerminal && (
                <>
                  <PanelResizeHandle className="resize-handle horizontal" />
                  <Panel defaultSize={32} minSize={18} maxSize={60} className="terminal-panel">
                    <div className="panel-header terminal-header">
                      <h2><VscTerminal /> Terminal</h2>
                    </div>
                    <div className="terminal-body terminal-interactive" ref={terminalRef} onClick={() => terminalInputRef.current?.focus()}>
                      {terminalOutput.length === 0
                        ? <div className="terminal-empty">Type a command and press Enter.</div>
                        : terminalOutput.map((line, i) => <pre key={i} className="terminal-line">{line}</pre>)
                      }
                      <div className="terminal-live-line">
                        <span className="terminal-prompt">$</span>
                        <input ref={terminalInputRef} value={terminalInput} onChange={(e) => setTerminalInput(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && runTerminal(terminalInput)}
                          placeholder="Type command..." className="terminal-input" />
                      </div>
                    </div>
                  </Panel>
                </>
              )}
            </PanelGroup>
          </Panel>

          {showChat && (
            <>
              <PanelResizeHandle className="resize-handle" />
              <Panel defaultSize={30} minSize={22} maxSize={48} className="ai-panel">
                <div className="panel-header"><h2>AI Chat</h2></div>
                <AiChat
                  workspaceName={dirHandle?.name || ""}
                  dirHandle={dirHandle}
                  fileTree={fileTree}
                  onWorkspaceRefresh={() => refreshTree()}
                  onOpenFile={openFileByHandle}
                  onTerminalOutput={(line) => setTerminalOutput((prev) => [...prev, line])}
                />
              </Panel>
            </>
          )}
        </PanelGroup>
      </div>

      <footer className="status-bar">
        <div className="status-left">
          <span>{activeFile ? activeFile.split(/[/\\]/).pop() : "No file selected"}</span>
          <span>{openFiles.length} open file{openFiles.length === 1 ? "" : "s"}</span>
          <span>{dirHandle ? `Folder: ${dirHandle.name}` : "No folder open"}</span>
        </div>
        <div className="status-right">
          <span>Ctrl+B Explorer</span>
          <span>Ctrl+` Terminal</span>
          <span>Ctrl+Shift+A Chat</span>
          <span>API: localhost:8080</span>
        </div>
      </footer>

      {/* Create file/folder dialog */}
      {createDialog && (
        <div className="vscode-dialog-overlay" onClick={() => setCreateDialog(null)}>
          <div className="vscode-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="vscode-dialog-header">
              <h3>{createDialog.mode === "file" ? "New File" : "New Folder"}</h3>
              <button className="vscode-dialog-close" onClick={() => setCreateDialog(null)}><VscChromeClose /></button>
            </div>
            <div className="vscode-dialog-body">
              <input
                type="text"
                className={`vscode-input ${createDialog.error ? "error" : ""}`}
                value={createDialog.value}
                autoFocus
                onChange={(e) => setCreateDialog((d) => ({ ...d, value: e.target.value, error: "" }))}
                onKeyDown={(e) => { if (e.key === "Enter") submitCreate(); else if (e.key === "Escape") setCreateDialog(null); }}
                placeholder={createDialog.mode === "file" ? "filename.ext or src/filename.ext" : "folder or nested/folder"}
              />
              {createDialog.error && <div className="vscode-error-message">{createDialog.error}</div>}
            </div>
            <div className="vscode-dialog-footer">
              <button className="vscode-button secondary" onClick={() => setCreateDialog(null)}>Cancel</button>
              <button className="vscode-button primary" onClick={submitCreate} disabled={!createDialog.value.trim()}>
                {createDialog.mode === "file" ? "Create File" : "Create Folder"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
