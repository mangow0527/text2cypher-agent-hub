import { FormEvent, useState } from "react";

const defaultImport = `{"question_canonical_zh":"网络元素总共有多少个？","question_variants_zh":["网络元素总共有多少个？","一共有多少个网络元素？"],"cypher":"MATCH (n:NetworkElement) RETURN count(n) AS total","query_types":["AGGREGATION"],"difficulty":"medium","validation":{"syntax":true,"schema":true,"type_value":true,"runtime":true,"result_sanity":true,"roundtrip_check":true},"result_signature":{"columns":["total"],"column_types":["0"],"row_count":1,"result_preview":[{"col_0":40}]}}`;

export function ImportComposer({
  onImport,
  busy,
  message,
}: {
  onImport: (payload: { sourceType: "inline" | "file"; payloadText: string; filePath: string }) => Promise<void>;
  busy: boolean;
  message: string;
}) {
  const [sourceType, setSourceType] = useState<"inline" | "file">("inline");
  const [payloadText, setPayloadText] = useState(defaultImport);
  const [filePath, setFilePath] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await onImport({ sourceType, payloadText, filePath });
  }

  return (
    <section className="surface">
      <div className="panel-heading-row">
        <div>
          <div className="eyebrow">导入资产</div>
          <h3>手动导入 QA 对</h3>
          <p>这个区域只负责导入，不会再跟生成任务共用忙碌状态。</p>
        </div>
      </div>
      <form className="stack-form" onSubmit={handleSubmit}>
        <div className="segmented">
          <button type="button" className={sourceType === "inline" ? "segment active" : "segment"} onClick={() => setSourceType("inline")}>
            粘贴内容
          </button>
          <button type="button" className={sourceType === "file" ? "segment active" : "segment"} onClick={() => setSourceType("file")}>
            文件路径
          </button>
        </div>
        {sourceType === "inline" ? (
          <textarea
            className="editor editor-short"
            value={payloadText}
            onChange={(event) => setPayloadText(event.target.value)}
            placeholder="在这里粘贴 QA JSONL"
          />
        ) : (
          <input
            className="text-input dark"
            value={filePath}
            onChange={(event) => setFilePath(event.target.value)}
            placeholder="本地 QA 文件路径"
          />
        )}
        <div className="notice">{message}</div>
        <div className="form-footer">
          <p>导入完成后会在“手动导入”标签页里看到结果。</p>
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? "导入中..." : "导入 QA 资产"}
          </button>
        </div>
      </form>
    </section>
  );
}
