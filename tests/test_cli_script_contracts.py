from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_a0_metadata_initialization_uses_loaded_engine_revision():
    source = (ROOT / "scripts" / "run_a0_v2.py").read_text(encoding="utf-8")
    start = source.index("        metadata = {", source.index("engine = GenerativeKGQA("))
    end = source.index("        metadata_path.write_text(", start)
    init_block = source[start:end]
    assert '"model_revision": engine.resolved_revision' in init_block
    assert '"model_revision": metadata.get("model_revision")' not in init_block


def test_a0_metrics_can_read_initialized_metadata():
    source = (ROOT / "scripts" / "run_a0_v2.py").read_text(encoding="utf-8")
    assert '"model_revision": metadata.get("model_revision")' in source


def test_a1_summary_print_uses_existing_summary_key():
    source = (ROOT / "scripts" / "run_a1_v2.py").read_text(encoding="utf-8")
    assert '"unattributed_retained": reasons.get' in source
    assert "summary['unattributed_retained']" in source
    assert "summary['unattributed_questions']" not in source
