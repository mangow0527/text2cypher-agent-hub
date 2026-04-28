import { QAStats } from "../lib/api";

const LEVELS = Array.from({ length: 8 }, (_, index) => `L${index + 1}`);

export function QAStatsPanel({ stats }: { stats?: QAStats }) {
  const total = stats?.total_qa_pairs ?? 0;
  const maxCount = Math.max(...LEVELS.map((level) => stats?.difficulty_distribution[level] ?? 0), 1);

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
              <div key={level} className="difficulty-row">
                <span className="difficulty-level">{level}</span>
                <div className="difficulty-bar-shell">
                  <div className="difficulty-bar-fill" style={{ width }} />
                </div>
                <span className="difficulty-count">{count} / {percentage}%</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="difficulty-definition-grid">
        {(stats?.difficulty_definitions ?? []).map((item) => (
          <div key={item.level} className="difficulty-definition">
            <div className="difficulty-definition-head">
              <strong>{item.level}</strong>
              <span>{item.paper_band}</span>
            </div>
            <h4>{item.title}</h4>
            <p>{item.definition}</p>
          </div>
        ))}
      </div>
      {stats?.invalid_rows ? <div className="notice">已跳过 {stats.invalid_rows} 行无效 QA 数据。</div> : null}
    </section>
  );
}
