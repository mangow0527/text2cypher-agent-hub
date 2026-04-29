import { FormEvent, useState } from "react";

const DIFFICULTY_LEVELS = Array.from({ length: 8 }, (_, index) => `L${index + 1}`);

const defaultSchemaPath =
  (import.meta.env.VITE_DEFAULT_SCHEMA_PATH as string | undefined)?.trim() ||
  "/root/multi-agent/qa-agent/schema.json";

const defaultSchema = {
  node_types: ["Person", "Project"],
  edge_types: ["WORKS_ON"],
  node_properties: {
    Person: { name: "string", title: "string" },
    Project: { name: "string", year: "integer" },
  },
  edge_properties: {
    WORKS_ON: { role: "string" },
  },
  value_catalog: {
    "Person.name": ["Alice"],
    "Person.title": ["Researcher"],
    "Project.name": ["Graph QA"],
  },
};

export function JobComposer({
  onCreate,
  onPreflight,
  busy,
  message,
}: {
  onCreate: (payload: {
    mode: "online" | "offline";
    schemaSourceType: "inline" | "file" | "url";
    schemaText: string;
    schemaFilePath: string;
    schemaUrl: string;
    tugraphSourceType: "env" | "inline";
    tugraphBaseUrl: string;
    tugraphUser: string;
    tugraphPassword: string;
    tugraphGraph: string;
    targetQaCount: number;
    difficultyTargets: Record<string, number>;
  }) => Promise<void>;
  onPreflight: (payload: {
    schemaSourceType: "inline" | "file" | "url";
    schemaText: string;
    schemaFilePath: string;
    schemaUrl: string;
    tugraphSourceType: "env" | "inline";
    tugraphBaseUrl: string;
    tugraphUser: string;
    tugraphPassword: string;
    tugraphGraph: string;
    targetQaCount: number;
    difficultyTargets: Record<string, number>;
  }) => Promise<string>;
  busy: boolean;
  message: string;
}) {
  const [schemaText, setSchemaText] = useState(JSON.stringify(defaultSchema, null, 2));
  const [mode, setMode] = useState<"online" | "offline">("online");
  const [schemaSourceType, setSchemaSourceType] = useState<"inline" | "file" | "url">("inline");
  const [schemaFilePath, setSchemaFilePath] = useState(defaultSchemaPath);
  const [schemaUrl, setSchemaUrl] = useState("");
  const [tugraphSourceType, setTugraphSourceType] = useState<"env" | "inline">("env");
  const [tugraphBaseUrl, setTugraphBaseUrl] = useState("");
  const [tugraphUser, setTugraphUser] = useState("");
  const [tugraphPassword, setTugraphPassword] = useState("");
  const [tugraphGraph, setTugraphGraph] = useState("");
  const [targetQaCount, setTargetQaCount] = useState(10);
  const [useDifficultyTargets, setUseDifficultyTargets] = useState(false);
  const [difficultyTargets, setDifficultyTargets] = useState<Record<string, number>>({});
  const [preflightMessage, setPreflightMessage] = useState("");

  const targetedTotal = DIFFICULTY_LEVELS.reduce((sum, level) => sum + (difficultyTargets[level] || 0), 0);
  const difficultyTargetError =
    useDifficultyTargets && (targetedTotal <= 0 || targetedTotal > 50)
      ? targetedTotal <= 0
        ? "至少配置 1 条难度数量"
        : "难度数量合计不能超过 50 条"
      : "";
  const effectiveTargetQaCount = useDifficultyTargets ? targetedTotal : targetQaCount;
  const activeDifficultyTargets = useDifficultyTargets
    ? Object.fromEntries(Object.entries(difficultyTargets).filter(([, count]) => count > 0))
    : {};

  function updateDifficultyTarget(level: string, value: number) {
    setDifficultyTargets((current) => ({
      ...current,
      [level]: Math.max(0, Math.min(50, value || 0)),
    }));
  }

  function stepDifficultyTarget(level: string, delta: number) {
    updateDifficultyTarget(level, (difficultyTargets[level] || 0) + delta);
  }

  function allocateDifficultyTargets(total: number) {
    const base = Math.floor(total / DIFFICULTY_LEVELS.length);
    const remainder = total % DIFFICULTY_LEVELS.length;
    const next: Record<string, number> = {};
    DIFFICULTY_LEVELS.forEach((level, index) => {
      next[level] = base + (index < remainder ? 1 : 0);
    });
    return next;
  }

  function toggleDifficultyTargets() {
    setUseDifficultyTargets((current) => {
      const next = !current;
      if (next && targetedTotal <= 0) {
        setDifficultyTargets(allocateDifficultyTargets(targetQaCount));
      }
      return next;
    });
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await onCreate({
      mode,
      schemaSourceType,
      schemaText,
      schemaFilePath,
      schemaUrl,
      tugraphSourceType,
      tugraphBaseUrl,
      tugraphUser,
      tugraphPassword,
      tugraphGraph,
      targetQaCount: effectiveTargetQaCount,
      difficultyTargets: activeDifficultyTargets,
    });
  }

  async function handlePreflight() {
    try {
      const message = await onPreflight({
        schemaSourceType,
        schemaText,
        schemaFilePath,
        schemaUrl,
        tugraphSourceType,
        tugraphBaseUrl,
        tugraphUser,
        tugraphPassword,
        tugraphGraph,
        targetQaCount: effectiveTargetQaCount,
        difficultyTargets: activeDifficultyTargets,
      });
      setPreflightMessage(message);
    } catch (error) {
      setPreflightMessage(`预检失败：${error instanceof Error ? error.message : "未知错误"}`);
    }
  }

  return (
    <section className="surface">
      <div className="panel-heading-row">
        <div>
          <div className="eyebrow">生成任务</div>
          <h3>批量生成 QA 对</h3>
          <p>创建一个任务，生成多条带难度标签的 QA 对。</p>
        </div>
      </div>
      <form className="stack-form" onSubmit={handleSubmit}>
        <div className="segmented">
          <button
            type="button"
            className={mode === "online" ? "segment active" : "segment"}
            onClick={() => setMode("online")}
          >
            在线试跑
          </button>
          <button
            type="button"
            className={mode === "offline" ? "segment active" : "segment"}
            onClick={() => setMode("offline")}
          >
            离线批量
          </button>
        </div>
        <div className="segmented">
          <button type="button" className={schemaSourceType === "inline" ? "segment active" : "segment"} onClick={() => setSchemaSourceType("inline")}>界面输入</button>
          <button type="button" className={schemaSourceType === "file" ? "segment active" : "segment"} onClick={() => setSchemaSourceType("file")}>文件读取</button>
          <button type="button" className={schemaSourceType === "url" ? "segment active" : "segment"} onClick={() => setSchemaSourceType("url")}>接口读取</button>
        </div>
        {schemaSourceType === "inline" ? (
          <textarea
            className="editor"
            value={schemaText}
            onChange={(event) => setSchemaText(event.target.value)}
            placeholder="在这里粘贴 schema JSON"
          />
        ) : null}
        {schemaSourceType === "file" ? (
          <input className="text-input dark" value={schemaFilePath} onChange={(event) => setSchemaFilePath(event.target.value)} placeholder="本地 schema 文件路径" />
        ) : null}
        {schemaSourceType === "url" ? (
          <input className="text-input dark" value={schemaUrl} onChange={(event) => setSchemaUrl(event.target.value)} placeholder="返回 JSON 的 schema 接口 URL" />
        ) : null}
        <div className="section-divider" />
        <div className="field-grid single">
          <label className="field-label">
            <span>目标 QA 数量</span>
            <input
              className="text-input dark"
              type="number"
              min={1}
              max={50}
              value={useDifficultyTargets ? targetedTotal : effectiveTargetQaCount}
              disabled={useDifficultyTargets}
              onChange={(event) => setTargetQaCount(Math.max(1, Math.min(50, Number(event.target.value) || 1)))}
            />
          </label>
        </div>
        <div className="difficulty-targets-box">
          <div className="difficulty-targets-head">
            <div>
              <strong>按难度指定 QA 对数量</strong>
              <span>{useDifficultyTargets ? `当前合计 ${targetedTotal} 条` : "开启后可直接填写每个难度的 QA 对数量"}</span>
            </div>
            <button
              type="button"
              className={useDifficultyTargets ? "segment active" : "segment"}
              onClick={toggleDifficultyTargets}
            >
              {useDifficultyTargets ? "已开启" : "开启"}
            </button>
          </div>
          <div className={useDifficultyTargets ? "difficulty-target-grid" : "difficulty-target-grid disabled"}>
            {DIFFICULTY_LEVELS.map((level) => (
              <label key={level} className="difficulty-target-field">
                <span>{level}</span>
                <div className="difficulty-target-input">
                  <input
                    className="text-input compact-input"
                    type="number"
                    min={0}
                    max={50}
                    value={difficultyTargets[level] || 0}
                    disabled={!useDifficultyTargets}
                    onChange={(event) => updateDifficultyTarget(level, Number(event.target.value))}
                  />
                  <div className="quantity-stepper" aria-label={`${level} 数量调整`}>
                    <button
                      type="button"
                      aria-label={`${level} 增加 1 条`}
                      disabled={!useDifficultyTargets || (difficultyTargets[level] || 0) >= 50}
                      onClick={() => stepDifficultyTarget(level, 1)}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      aria-label={`${level} 减少 1 条`}
                      disabled={!useDifficultyTargets || (difficultyTargets[level] || 0) <= 0}
                      onClick={() => stepDifficultyTarget(level, -1)}
                    >
                      ▼
                    </button>
                  </div>
                  <em>条</em>
                </div>
              </label>
            ))}
          </div>
          {difficultyTargetError ? <div className="field-error">{difficultyTargetError}</div> : null}
        </div>
        <div className="subsection-head">
          <p>TuGraph 连接</p>
          <div className="segmented compact">
            <button type="button" className={tugraphSourceType === "env" ? "segment active" : "segment"} onClick={() => setTugraphSourceType("env")}>环境变量</button>
            <button type="button" className={tugraphSourceType === "inline" ? "segment active" : "segment"} onClick={() => setTugraphSourceType("inline")}>界面输入</button>
          </div>
        </div>
        {tugraphSourceType === "inline" ? (
          <div className="field-grid">
            <input className="text-input dark" value={tugraphBaseUrl} onChange={(event) => setTugraphBaseUrl(event.target.value)} placeholder="TuGraph Base URL" />
            <input className="text-input dark" value={tugraphUser} onChange={(event) => setTugraphUser(event.target.value)} placeholder="用户名" />
            <input className="text-input dark" value={tugraphPassword} onChange={(event) => setTugraphPassword(event.target.value)} placeholder="密码" />
            <input className="text-input dark" value={tugraphGraph} onChange={(event) => setTugraphGraph(event.target.value)} placeholder="图名" />
          </div>
        ) : (
          <div className="meta-row">
            <span>连接来源</span>
            <strong>TUGRAPH_BASE_URL / USER / PASSWORD / GRAPH</strong>
          </div>
        )}
        <div className="meta-row">
          <span>Schema 来源</span>
          <strong>{schemaSourceType === "inline" ? "界面输入" : schemaSourceType === "file" ? "文件读取" : "接口读取"}</strong>
        </div>
        <div className="meta-row">
          <span>本批目标</span>
          <strong>{effectiveTargetQaCount} 条 QA</strong>
        </div>
        <div className="notice">{message}</div>
        {preflightMessage ? <div className="signal-box">{preflightMessage}</div> : null}
        <div className="form-footer">
          <p>结果面板展示生成进度、难度覆盖和下载入口。</p>
          <div className="action-row">
            <button className="button secondary" type="button" disabled={busy || Boolean(difficultyTargetError)} onClick={() => void handlePreflight()}>
            预检
            </button>
            <button className="button primary" type="submit" disabled={busy || Boolean(difficultyTargetError)}>
            {busy ? "任务运行中..." : "创建并运行"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
