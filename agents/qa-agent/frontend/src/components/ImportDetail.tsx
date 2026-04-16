import { ImportRecord, importArtifactUrl } from "../lib/api";

function readCoverage(report: Record<string, unknown>) {
  return (report.difficulty_coverage as {
    all_levels?: string[];
    covered_levels?: string[];
    missing_levels?: string[];
    is_complete?: boolean;
  }) || null;
}

function readLanguageCoverage(report: Record<string, unknown>) {
  return (report.language_coverage as {
    all_styles?: string[];
    covered_styles?: string[];
    missing_styles?: string[];
    is_complete?: boolean;
  }) || null;
}

function readQueryTypeCoverage(report: Record<string, unknown>) {
  return (report.query_type_coverage as {
    all_query_types?: string[];
    covered_query_types?: string[];
    missing_query_types?: string[];
    is_complete?: boolean;
  }) || null;
}

function readFamilyCoverage(report: Record<string, unknown>) {
  return (report.structure_family_coverage as {
    all_families?: string[];
    covered_families?: string[];
    missing_families?: string[];
    is_complete?: boolean;
  }) || null;
}

function readDispatch(report: Record<string, unknown>) {
  return (report.dispatch as {
    enabled?: boolean;
    status?: string;
    host?: string | null;
    total?: number;
    success?: number;
    partial?: number;
    failed?: number;
    message?: string;
    results?: Array<{
      id: string;
      status: string;
      question: { ok: boolean; attempts: number; error?: string | null };
      golden: { ok: boolean; attempts: number; error?: string | null };
    }>;
  }) || null;
}

export function ImportDetail({ record, onClose }: { record?: ImportRecord; onClose: () => void }) {
  if (!record) {
    return (
      <section className="surface detail-surface">
        <div className="empty-state">
          <div className="eyebrow">导入详情</div>
          <h3>选择一个导入批次</h3>
          <p>这里会显示导入数量、下载入口和传播结果。</p>
        </div>
      </section>
    );
  }

  const coverage = readCoverage(record.report);
  const languageCoverage = readLanguageCoverage(record.report);
  const queryTypeCoverage = readQueryTypeCoverage(record.report);
  const familyCoverage = readFamilyCoverage(record.report);
  const dispatch = readDispatch(record.report);

  return (
    <section className="surface detail-surface">
      <div className="detail-hero">
        <div>
          <div className="eyebrow">导入详情</div>
          <h3>{record.import_id}</h3>
          <p>手动导入的 QA 资产会显示在这里。</p>
        </div>
        <div className="detail-hero-actions">
          <span className={`status-pill status-${record.status}`}>{record.status}</span>
          <button className="button tertiary" type="button" onClick={onClose}>
            收起
          </button>
        </div>
      </div>
      <div className="detail-grid">
        <div className="detail-card glow">
          <div className="detail-label">样本数量</div>
          <strong className="detail-value">{record.sample_count}</strong>
        </div>
        <div className="detail-card">
          <div className="detail-label">导入时间</div>
          <strong className="detail-value small">{new Date(record.updated_at).toLocaleString()}</strong>
        </div>
      </div>
      <div className="detail-section-block">
        <div className="detail-label">导入产物</div>
        <div className="artifact-list">
          {Object.entries(record.artifacts).map(([key, value]) => (
            <div className="artifact-row" key={key}>
              <div>
                <span>{key}</span>
                <code>{value.split("/").slice(-1)[0]}</code>
              </div>
              <a href={importArtifactUrl(record.import_id, key)} target="_blank" rel="noreferrer">
                下载
              </a>
            </div>
          ))}
        </div>
      </div>
      <div className="detail-section-block">
        <div className="detail-label">报告摘要</div>
        {coverage ? (
          <>
            <div className="coverage-strip">
              {(coverage.all_levels || []).map((level) => {
                const covered = (coverage.covered_levels || []).includes(level);
                return (
                  <span key={level} className={`coverage-pill ${covered ? "covered" : "missing"}`}>
                    {level}
                  </span>
                );
              })}
            </div>
            <p className="coverage-note">
              {coverage.is_complete ? "导入集已覆盖 L1-L8。" : `当前缺少 ${(coverage.missing_levels || []).join(", ") || "部分等级"}`}
            </p>
          </>
        ) : null}
        {queryTypeCoverage ? (
          <>
            <div className="coverage-strip">
              {(queryTypeCoverage.all_query_types || []).map((queryType) => {
                const covered = (queryTypeCoverage.covered_query_types || []).includes(queryType);
                return (
                  <span key={queryType} className={`coverage-pill ${covered ? "covered" : "missing"}`}>
                    {queryType}
                  </span>
                );
              })}
            </div>
            <p className="coverage-note">
              {queryTypeCoverage.is_complete
                ? "导入集主查询类型已完整覆盖。"
                : `当前缺少 ${(queryTypeCoverage.missing_query_types || []).join(", ") || "部分类型"}`}
            </p>
          </>
        ) : null}
        {familyCoverage ? (
          <>
            <div className="coverage-strip">
              {(familyCoverage.all_families || []).map((family) => {
                const covered = (familyCoverage.covered_families || []).includes(family);
                return (
                  <span key={family} className={`coverage-pill ${covered ? "covered" : "missing"}`}>
                    {family}
                  </span>
                );
              })}
            </div>
            <p className="coverage-note">
              {familyCoverage.is_complete
                ? "导入集结构族已完整覆盖。"
                : `当前缺少 ${(familyCoverage.missing_families || []).join(", ") || "部分结构族"}`}
            </p>
          </>
        ) : null}
        {languageCoverage ? (
          <>
            <div className="coverage-strip">
              {(languageCoverage.all_styles || []).map((style) => {
                const covered = (languageCoverage.covered_styles || []).includes(style);
                return (
                  <span key={style} className={`coverage-pill ${covered ? "covered" : "missing"}`}>
                    {style}
                  </span>
                );
              })}
            </div>
            <p className="coverage-note">
              {languageCoverage.is_complete
                ? "导入集语言风格已完整覆盖。"
                : `当前缺少 ${(languageCoverage.missing_styles || []).join(", ") || "部分风格"}`}
            </p>
          </>
        ) : null}
        {dispatch ? (
          <>
            <div className="detail-grid">
              <div className="detail-card">
                <div className="detail-label">发送状态</div>
                <strong className="detail-value small">{dispatch.status || "unknown"}</strong>
              </div>
              <div className="detail-card">
                <div className="detail-label">成功 / 失败</div>
                <strong className="detail-value small">
                  {(dispatch.success ?? 0)} / {(dispatch.failed ?? 0)}
                </strong>
              </div>
            </div>
            <p className="coverage-note">
              {dispatch.enabled
                ? `目标: ${dispatch.host || "unknown"}`
                : dispatch.message || "未启用发送"}
            </p>
            {dispatch.results?.length ? (
              <div className="artifact-list">
                {dispatch.results.slice(0, 8).map((item) => (
                  <div key={item.id} className="artifact-row">
                    <div>
                      <span>{item.id}</span>
                      <code>
                        question={item.question.ok ? "ok" : item.question.error || "failed"} | golden=
                        {item.golden.ok ? "ok" : item.golden.error || "failed"}
                      </code>
                    </div>
                    <span className={`status-pill status-${item.status === "success" ? "completed" : "failed"}`}>
                      {item.status}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
