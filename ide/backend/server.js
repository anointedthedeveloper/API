const express = require("express");
const cors = require("cors");
const fs = require("fs-extra");
const path = require("path");
const { exec } = require("child_process");
const { Readable } = require("stream");

const app = express();
app.use(cors());
app.use(express.json({ limit: "15mb" }));

const PORT = process.env.PORT || 3001;
const DEEPSEEK_API_URL = process.env.DEEPSEEK_API_URL || "http://localhost:8080/v1/chat/completions";
const DEFAULT_PROJECT_ROOT = path.resolve(process.env.ACTIVE_PROJECT_DIR || process.cwd());

function resolveProjectRoot(projectRoot) {
  const root = path.resolve(projectRoot || DEFAULT_PROJECT_ROOT);
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    throw new Error(`Project root is not a valid directory: ${root}`);
  }
  return root;
}

function resolveSafePath(relativePath, projectRoot) {
  if (!relativePath || typeof relativePath !== "string") {
    throw new Error("relativePath is required and must be a string.");
  }
  const root = resolveProjectRoot(projectRoot);
  const target = path.resolve(root, relativePath);
  if (!target.startsWith(root)) {
    throw new Error("Invalid path. Access outside the project root is forbidden.");
  }
  return target;
}

app.get("/", (req, res) => {
  res.json({ message: "ANAI backend bridge is running.", projectRoot: DEFAULT_PROJECT_ROOT });
});

app.post("/api/read-file", async (req, res) => {
  try {
    const { relativePath, projectRoot } = req.body;
    const filePath = resolveSafePath(relativePath, projectRoot);
    if (!await fs.pathExists(filePath)) {
      return res.status(404).json({ error: `File not found: ${relativePath}` });
    }
    const content = await fs.readFile(filePath, "utf8");
    res.json({ content });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post("/api/write-file", async (req, res) => {
  try {
    const { relativePath, newContent, projectRoot } = req.body;
    const filePath = resolveSafePath(relativePath, projectRoot);
    await fs.ensureDir(path.dirname(filePath));
    await fs.writeFile(filePath, newContent, "utf8");
    res.json({ success: true, message: `Updated ${relativePath} successfully.` });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.post("/api/terminal", async (req, res) => {
  const { command, cwd } = req.body;
  if (!command || typeof command !== "string") {
    return res.status(400).json({ output: "Error: command is required.", exitCode: 1 });
  }

  const workingDir = cwd ? path.resolve(cwd) : DEFAULT_PROJECT_ROOT;
  exec(command, { cwd: workingDir, shell: true, maxBuffer: 1024 * 1024 * 2 }, (err, stdout, stderr) => {
    if (err && err.code !== 0) {
      return res.json({ output: `${stdout || ""}${stderr ? `\n${stderr}` : ""}`.trim(), exitCode: err.code || 1 });
    }
    res.json({ output: `${stdout || ""}${stderr ? `\n${stderr}` : ""}`.trim(), exitCode: 0 });
  });
});

app.post("/v1/chat/completions", async (req, res) => {
  try {
    const response = await fetch(DEEPSEEK_API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });

    res.status(response.status);
    response.headers.forEach((value, key) => res.setHeader(key, value));

    if (!response.body) {
      const text = await response.text();
      return res.send(text);
    }

    if (typeof response.body.pipe === "function") {
      return response.body.pipe(res);
    }

    if (Readable.fromWeb) {
      return Readable.fromWeb(response.body).pipe(res);
    }

    const text = await response.text();
    return res.send(text);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.listen(PORT, () => {
  console.log(`ANAI backend bridge running on http://localhost:${PORT}`);
  console.log(`Proxying DeepSeek chat to ${DEEPSEEK_API_URL}`);
});
