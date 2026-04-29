import { useMemo, useState } from "react";
import { QADifficultyDefinition, QAStats } from "../lib/api";

const LEVELS = Array.from({ length: 8 }, (_, index) => `L${index + 1}`);

export function QAStatsPanel({ stats }: { stats?: QAStats }) {
  const [selectedLevel, setSelectedLevel] = useState<string | undefined>();
  const total = stats?.total_qa_pairs ?? 0;
  const maxCount = Math.max(...LEVELS.map((level) => stats?.difficulty_distribution[level] ?? 0), 1);
  const definitions = stats?.difficulty_definitions ?? [];
  const definitionByLevel = useMemo(
    () => new Map(definitions.map((item) => [item.level, item])),
    [definitions],
  );
  const selectedDefinition = selectedLevel ? definitionByLevel.get(selectedLevel) : undefined;

  return (
    <section className="surface qa-stats-panel">
      <div className="panel-heading-row compact">
        <div>
          <div className="eyebrow">生成统计</div>
          <h3>QA 对总览</h3>
          <p>统计当前 artifacts 中所有 QA JSONL，包含自动生成和手动导入。</p>
        </div>
        <div className="qa-stats-meta">
          {stats?.latest_updated_at ? `更新 ${new Date(stats.latest_updated_at).toLocaleString()}` : "等待产物"}
        </div>
      </div>

      <div className="qa-stats-layout">
        <div className="qa-stats-counts">
          <div className="qa-total-block">
            <span>当前 QA 对</span>
            <strong>{total}</strong>
          </div>
          <div className="qa-source-grid">
            <div>
              <span>生成</span>
              <strong>{stats?.generated_qa_pairs ?? 0}</strong>
            </div>
            <div>
              <span>导入</span>
              <strong>{stats?.imported_qa_pairs ?? 0}</strong>
            </div>
            <div>
              <span>文件</span>
              <strong>{stats?.files_processed ?? 0}</strong>
            </div>
          </div>
        </div>

        <div className="qa-distribution">
          {LEVELS.map((level) => {
            const count = stats?.difficulty_distribution[level] ?? 0;
            const percentage = stats?.difficulty_percentages[level] ?? 0;
            const width = `${Math.max((count / maxCount) * 100, count > 0 ? 6 : 0)}%`;
            return (
              <button
                key={level}
                type="button"
                className="difficulty-row difficulty-row-button"
                onClick={() => setSelectedLevel(level)}
                aria-label={`查看 ${level} 难度 Cypher 示例`}
              >
                <span className="difficulty-level">{level}</span>
                <div className="difficulty-bar-shell">
                  <div className="difficulty-bar-fill" style={{ width }} />
                </div>
                <span className="difficulty-count">{count} / {percentage}%</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="difficulty-definition-grid">
        {definitions.map((item) => (
          <button
            key={item.level}
            type="button"
            className="difficulty-definition"
            onClick={() => setSelectedLevel(item.level)}
            aria-label={`查看 ${item.level} 难度 Cypher 示例`}
          >
            <div className="difficulty-definition-head">
              <strong>{item.level}</strong>
              <span>{item.paper_band}</span>
            </div>
            <h4>{item.title}</h4>
            <p>{item.definition}</p>
          </button>
        ))}
      </div>
      {stats?.invalid_rows ? <div className="notice">已跳过 {stats.invalid_rows} 行无效 QA 数据。</div> : null}
      {selectedDefinition ? (
        <DifficultyExampleDialog definition={selectedDefinition} onClose={() => setSelectedLevel(undefined)} />
      ) : null}
    </section>
  );
}

function DifficultyExampleDialog({
  definition,
  onClose,
}: {
  definition: QADifficultyDefinition;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="difficulty-modal" role="dialog" aria-modal="true" aria-labelledby="difficulty-modal-title" onClick={(event) => event.stopPropagation()}>
        <div className="difficulty-modal-head">
          <div>
            <div className="eyebrow">{definition.paper_band}</div>
            <h3 id="difficulty-modal-title">{definition.level} · {definition.title}</h3>
            <p>{definition.definition}</p>
          </div>
          <button className="modal-close-button" type="button" onClick={onClose} aria-label="关闭示例弹窗">
            ×
          </button>
        </div>
        <div className="cypher-example-list">
          {definition.cypher_examples.map((example) => (
            <div key={`${example.difficulty}-${example.label}`} className="cypher-example">
              <div className="cypher-example-label">
                <strong>{example.label}</strong>
                <span>{example.difficulty}</span>
              </div>
              <pre><code>{example.cypher}</code></pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
