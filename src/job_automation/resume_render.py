"""Render tailored resume text to a PDF the Playwright apply flow can upload.

Reportlab is imported lazily so this module (and anything importing it) keeps
working in environments without the PDF stack — only actual rendering needs it.
"""

from __future__ import annotations

from pathlib import Path

TAILORED_SUBDIR = Path("output/tailored_resumes")


def tailored_pdf_path(job_id_value: str) -> Path:
    """Conventional location for a job's tailored-resume PDF."""
    return TAILORED_SUBDIR / f"{job_id_value}_tailored.pdf"


def _body_lines(text: str) -> list[str]:
    """Strip the human-review header/comments (``# ...`` lines) from the
    tailored resume text before rendering it into the uploadable PDF."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def render_resume_pdf(text: str, out_path: Path) -> Path:
    """Write ``text`` as a simple multi-paragraph PDF and return its path."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    body_style = ParagraphStyle(
        name="TailoredResume",
        fontName="Helvetica",
        fontSize=10,
        leading=13.5,
        alignment=TA_LEFT,
        spaceAfter=5,
    )
    bullet_style = ParagraphStyle(
        name="TailoredResumeBullet",
        parent=body_style,
        leftIndent=0.2 * inch,
        bulletIndent=0,
        spaceAfter=3,
    )

    def escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story: list[object] = []
    for raw_line in _body_lines(text):
        line = raw_line.rstrip()
        if not line.strip():
            story.append(Spacer(1, 4))
            continue
        is_bullet = line.lstrip().startswith(("- ", "* ", "• "))
        content = line.lstrip()[2:].strip() if is_bullet else line.strip()
        if not content:
            continue
        if is_bullet:
            story.append(Paragraph(escape(content), bullet_style, bulletText="•"))
        else:
            story.append(Paragraph(escape(content), body_style))

    if not story:  # nothing renderable — still produce a valid, empty PDF
        story.append(Paragraph("", body_style))

    document = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title="Tailored resume",
        author="JobCrew",
    )
    document.build(story)
    return out_path
