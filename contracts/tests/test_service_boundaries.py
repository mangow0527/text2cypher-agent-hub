from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_service_agent_directories_exist() -> None:
    assert (ROOT / "services" / "cypher_generator_agent").is_dir()
    assert not (ROOT / "services" / "query_generator_agent").exists()
    assert (ROOT / "services" / "testing_agent").is_dir()
    assert (ROOT / "services" / "repair_agent").is_dir()
    assert (ROOT / "console" / "runtime_console").is_dir()


def test_service_code_no_longer_imports_root_shared() -> None:
    disallowed = []
    for path in (ROOT / "services").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "from shared" in text or "import shared" in text:
            disallowed.append(path.relative_to(ROOT).as_posix())

    assert disallowed == []


def test_root_level_shared_and_docs_are_removed() -> None:
    assert not (ROOT / "shared").exists()
    docs_dir = ROOT / "docs"
    if not docs_dir.exists():
        return
    allowed_children = {"superpowers"}
    actual_children = {path.name for path in docs_dir.iterdir()}
    assert actual_children <= allowed_children


def test_contract_models_do_not_export_legacy_prompt_snapshot_response() -> None:
    from contracts import models

    assert not hasattr(models, "PromptSnapshotResponse")
