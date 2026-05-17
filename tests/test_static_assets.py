from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "wardragon_console" / "static"


def test_compact_viewport_mode_is_defined() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "compactViewportQuery" in script
    assert "compact-screen" in script
    assert ".compact-screen" in styles


def test_mobile_tables_have_cell_labels() -> None:
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'data-label="${escapeAttr(headers[index] || "")}"' in script
    assert "content: attr(data-label)" in styles
