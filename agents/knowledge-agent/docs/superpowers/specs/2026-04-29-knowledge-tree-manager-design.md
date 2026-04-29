# Knowledge Tree Manager Design

## Context

The current Knowledge Agent editor exposes knowledge as a short list of fixed documents:

- `schema.json`
- `system_prompt.md`
- `cypher_syntax.md`
- `business_knowledge.md`
- `few_shot.md`

This is useful for raw document editing, but it does not match how a user expects to manage domain knowledge. The expected experience is a simple tree structure organized around business objects and their related knowledge. For example, under `网元 NetworkElement`, the user should see its schema label, business aliases, relation paths, and few-shot examples.

The design should stay simple. It should not be a knowledge graph canvas, a visual relationship map, or a new graph database. It should be a tree manager that represents current knowledge relationships clearly and supports editing.

## Goals

1. Replace the current document-list editor with a simple knowledge tree manager.
2. Organize knowledge primarily by business object or concept.
3. Show schema-derived knowledge as read-only tree nodes.
4. Allow editable markdown-backed knowledge nodes to be added, edited, and deleted.
5. Preserve the existing file storage, prompt package flow, repair flow, and history snapshots.
6. Keep the interface utilitarian and easy to scan.

## Non-Goals

- Do not introduce a graph database.
- Do not build a graph-style node-and-edge canvas.
- Do not replace `schema.json`, `business_knowledge.md`, or `few_shot.md` as the underlying storage in the first implementation.
- Do not make `schema.json` editable.
- Do not redesign prompt assembly beyond what is needed to read the same knowledge sources.

## User Experience

The editor has two main regions:

1. Left tree panel
   - Shows a searchable, expandable knowledge tree.
   - Primary grouping is `业务对象`.
   - Example path:
     - `业务对象`
       - `网元 NetworkElement`
         - `Label / 属性`
         - `业务定义 / 别名`
         - `关系路径`
           - `网元 -> 端口`
           - `网元 -> 隧道`
         - `Few-shot 示例`
           - `查询协议版本为 v2.0 的隧道所属网元`
       - `端口 Port`
       - `隧道 Tunnel`
     - `通用规则`
       - `Cypher 方言`
       - `System Prompt`

2. Right detail panel
   - Shows the selected tree node title, source, type, editability, and write-back location.
   - Editable nodes show a textarea or structured form for the node content.
   - Read-only nodes show a viewer.
   - Actions include save, cancel, add child node, delete node, and view source file.

The first implementation should use a plain tree and form layout. Visual polish should be restrained: clear hierarchy, compact spacing, predictable controls, no decorative graph layout.

## Tree Model

The backend exposes a logical tree view derived from the current knowledge files.

Each tree node has:

- `id`: stable identifier for frontend selection.
- `parent_id`: parent node id, or null for roots.
- `title`: display label.
- `kind`: node category.
- `concept`: optional business concept, such as `NetworkElement`.
- `source_file`: backing file when applicable.
- `section_id`: backing markdown section or block id when applicable.
- `editable`: whether the node can be changed from the UI.
- `children`: nested tree nodes.
- `content_preview`: short preview for tree or detail display.

Initial node kinds:

- `group`: container such as `业务对象` or `通用规则`.
- `concept`: business object or concept such as `网元 NetworkElement`.
- `schema_label`: read-only schema-derived label and properties.
- `business_semantic`: editable business definition, alias, or terminology node.
- `relation_path`: editable relationship/path knowledge.
- `few_shot`: editable question/Cypher example.
- `rule`: editable generic rule, such as Cypher syntax or system prompt section.

## Source Mapping

The tree is a logical projection of existing files:

- `schema.json`
  - Creates read-only `schema_label` nodes under matching concepts.
  - May also create read-only schema relation summaries when useful.
- `business_knowledge.md`
  - Creates editable `business_semantic` and `relation_path` nodes.
  - Blocks should use stable `[id: ...]` markers.
  - Optional metadata can be added later, such as `[concept: NetworkElement]` and `[kind: relation_path]`.
- `few_shot.md`
  - Creates editable `few_shot` nodes.
  - Existing `[id: ...]` blocks are reused.
- `cypher_syntax.md` and `system_prompt.md`
  - Create editable `rule` nodes under `通用规则`.

If a markdown block cannot be confidently attached to a concept, it should appear under an `未分类` group instead of being hidden.

## Editing Behavior

### Read

The frontend loads the full tree, then requests node detail when a user selects a node.

### Update

Saving an editable node updates the corresponding markdown block or document section. The backend writes through `KnowledgeStore.write_versioned` so history snapshots remain available.

### Add

Adding a child node creates a new markdown block in the appropriate source file:

- Under `业务定义 / 别名`: add to `business_knowledge.md`.
- Under `关系路径`: add to `business_knowledge.md`.
- Under `Few-shot 示例`: add to `few_shot.md`.
- Under `通用规则`: add to the selected rule file.

The backend generates a stable section id if the user does not provide one.

### Delete

Deleting an editable node removes or tombstones the backing markdown block. The first implementation should physically remove the block from the active file and rely on `_history` for recovery.

Read-only schema nodes cannot be deleted.

## API Design

Add endpoints under `/api/knowledge/tree`:

- `GET /api/knowledge/tree`
  - Returns the full logical knowledge tree.
- `GET /api/knowledge/tree/nodes/{node_id}`
  - Returns node detail including full content.
- `PUT /api/knowledge/tree/nodes/{node_id}`
  - Updates an editable node.
- `POST /api/knowledge/tree/nodes`
  - Adds a child node under a parent.
- `DELETE /api/knowledge/tree/nodes/{node_id}`
  - Deletes an editable node.

Existing document endpoints can remain for fallback and source-file viewing.

## Error Handling

- Unknown node id returns `KNOWLEDGE_TREE_NODE_NOT_FOUND`.
- Attempts to edit or delete read-only nodes return `KNOWLEDGE_TREE_NODE_READ_ONLY`.
- Invalid parent/kind combinations return `KNOWLEDGE_TREE_INVALID_PARENT`.
- Markdown parse failures should not break the whole tree. Unparseable blocks should be listed under `未分类` with a warning in node detail.
- Save conflicts can be handled by last-write-wins in the first implementation, because the local single-user workflow is the current target.

## Testing

Backend tests:

- Tree generation creates concept nodes from schema labels.
- `schema_label` nodes are read-only.
- Business knowledge blocks become editable tree nodes.
- Few-shot blocks become editable child nodes.
- Updating a node writes the expected markdown block and creates history.
- Adding a relation path node appends a valid block.
- Deleting an editable node removes it from the active tree and writes history.
- Unknown/read-only operations return clear application errors.

Frontend checks:

- TypeScript build passes.
- Tree renders nested nodes and selected detail state.
- Read-only nodes show viewer state, not save/delete controls.
- Editable nodes show editor and save state.
- Add/delete actions update the tree after API success.

## Implementation Notes

The first implementation should prefer deterministic markdown parsing with stable block ids. It should not require an LLM to classify nodes.

Existing markdown files can continue working. New metadata markers may be added gradually to improve concept attachment. For example:

```text
[id: network_element_alias]
[concept: NetworkElement]
[kind: business_semantic]
- “网元”对应 `NetworkElement`。
```

This keeps the storage understandable to humans while giving the UI enough structure to build a useful tree.
