import type { RepairChange } from "../lib/api";

type DiffRow =
  | { type: "context"; left?: string; right?: string }
  | { type: "removed"; left: string; right?: string }
  | { type: "added"; left?: string; right: string };

function buildLcs(left: string[], right: string[]) {
  const dp = Array.from({ length: left.length + 1 }, () =>
    Array.from({ length: right.length + 1 }, () => 0),
  );

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      if (left[i] === right[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  return dp;
}

function buildDiffRows(before: string, after: string): DiffRow[] {
  const left = before.split("\n");
  const right = after.split("\n");
  const dp = buildLcs(left, right);
  const rows: DiffRow[] = [];

  let i = 0;
  let j = 0;

  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      rows.push({ type: "context", left: left[i], right: right[j] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: "removed", left: left[i] });
      i += 1;
    } else {
      rows.push({ type: "added", right: right[j] });
      j += 1;
    }
  }

  while (i < left.length) {
    rows.push({ type: "removed", left: left[i] });
    i += 1;
  }

  while (j < right.length) {
    rows.push({ type: "added", right: right[j] });
    j += 1;
  }

  return rows;
}

function markerForRow(type: DiffRow["type"], side: "left" | "right") {
  if (type === "removed" && side === "left") return "-";
  if (type === "added" && side === "right") return "+";
  return " ";
}

export function DiffViewer({ change }: { change: RepairChange }) {
  const rows = buildDiffRows(change.before, change.after);

  return (
    <section className="diff-block">
      <header className="diff-header">
        <div>
          <p className="diff-label">{change.doc_type}</p>
          <h3>{change.section}</h3>
        </div>
        <div className="diff-badges">
          <span className="diff-chip diff-chip-remove">before</span>
          <span className="diff-chip diff-chip-add">after</span>
        </div>
      </header>

      <div className="diff-grid">
        <div className="diff-pane">
          <div className="diff-pane-title">Original</div>
          {rows.map((row, index) => (
            <div key={`left-${index}`} className={`diff-row diff-row-${row.type}`}>
              <span className="diff-marker">{markerForRow(row.type, "left")}</span>
              <code>{row.left ?? ""}</code>
            </div>
          ))}
        </div>

        <div className="diff-pane">
          <div className="diff-pane-title">Updated</div>
          {rows.map((row, index) => (
            <div key={`right-${index}`} className={`diff-row diff-row-${row.type}`}>
              <span className="diff-marker">{markerForRow(row.type, "right")}</span>
              <code>{row.right ?? ""}</code>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
