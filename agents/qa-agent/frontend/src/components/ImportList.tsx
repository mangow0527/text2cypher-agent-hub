import { ImportRecord } from "../lib/api";

export function ImportList({
  imports,
  selectedImportId,
  onSelect,
}: {
  imports: ImportRecord[];
  selectedImportId?: string;
  onSelect: (importId: string) => void;
}) {
  return (
    <section className="surface list-surface">
      <div className="panel-heading-row compact">
        <div>
          <div className="eyebrow">导入批次</div>
          <h3>最近导入</h3>
        </div>
      </div>
      <div className="list-stack">
        {imports.length === 0 ? <div className="empty-inline">还没有导入记录。</div> : null}
        {imports.map((item) => (
          <button
            key={item.import_id}
            className={selectedImportId === item.import_id ? "list-item active" : "list-item"}
            onClick={() => onSelect(item.import_id)}
          >
            <div className="list-head">
              <strong>{item.import_id.slice(0, 14)}</strong>
              <span className={`status-pill status-${item.status}`}>{item.status}</span>
            </div>
            <div className="list-meta">
              <span>{item.sample_count} 条</span>
              <span>{new Date(item.updated_at).toLocaleString()}</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
