#!/usr/bin/env python3
"""
Convert a GitHub repository (URL or local path) into a PDF for code reading.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from xml.sax.saxutils import escape
from pathlib import Path


DEFAULT_EXTS = [
    ".go",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".kt",
    ".m",
    ".scala",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
]

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "__pycache__",
}


def is_github_url(value: str) -> bool:
    return bool(re.match(r"^https?://(www\.)?github\.com/", value))


def run(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip()


def clone_repo(url: str, target_dir: str) -> str:
    run(["git", "clone", "--depth", "1", url, target_dir])
    return target_dir


def build_tree(root: Path, include_exts: set[str], exclude_dirs: set[str]) -> str:
    lines = []
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        rel_dir = os.path.relpath(dirpath, root)
        indent_level = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        indent = "  " * indent_level
        if rel_dir == ".":
            lines.append(f"{root.name}/")
        else:
            lines.append(f"{indent}{os.path.basename(dirpath)}/")

        for filename in sorted(filenames):
            ext = Path(filename).suffix.lower()
            if ext in include_exts:
                lines.append(f"{indent}  {filename}")

    return "\n".join(lines)


def collect_files(root: Path, include_exts: set[str], exclude_dirs: set[str], max_size_kb: int):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            ext = path.suffix.lower()
            if ext not in include_exts:
                continue
            try:
                size_kb = path.stat().st_size / 1024
            except OSError:
                continue
            if size_kb > max_size_kb:
                continue
            files.append(path)
    return files


def _token_color(style, token_type):
    try:
        style_def = style.style_for_token(token_type)
    except KeyError:
        parent = token_type.parent
        style_def = {}
        while parent is not None:
            try:
                style_def = style.style_for_token(parent)
                break
            except KeyError:
                parent = parent.parent
    return style_def.get("color")


def build_code_lines(text: str, filename: str, highlight: bool) -> list[str]:
    if not highlight:
        return [escape(line) for line in text.split("\n")]

    try:
        from pygments import lex
        from pygments.lexers import get_lexer_for_filename, TextLexer
        from pygments.styles import get_style_by_name
    except ImportError:
        return [escape(line) for line in text.split("\n")]

    try:
        lexer = get_lexer_for_filename(filename, text)
    except Exception:
        lexer = TextLexer()

    style = get_style_by_name("default")
    lines = [""]
    for token_type, value in lex(text, lexer):
        if not value:
            continue
        color = _token_color(style, token_type)
        parts = value.split("\n")
        for idx, part in enumerate(parts):
            if part:
                escaped = escape(part)
                if color:
                    lines[-1] += f'<font color="#{color}">{escaped}</font>'
                else:
                    lines[-1] += escaped
            if idx < len(parts) - 1:
                lines.append("")
    return lines


def add_line_numbers(lines: list[str]) -> list[str]:
    width = len(str(len(lines)))
    numbered = []
    for idx, line in enumerate(lines, start=1):
        numbered.append(f"{idx:>{width}}  {line}")
    return numbered


def generate_pdf(
    output_path: Path,
    repo_root: Path,
    files: list[Path],
    tree_text: str,
    source_label: str,
    highlight: bool,
    show_line_numbers: bool,
):
    try:
        from reportlab.lib import pagesizes
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            PageTemplate,
            Paragraph,
            Preformatted,
            Spacer,
            PageBreak,
            XPreformatted,
        )
        from reportlab.platypus.tableofcontents import TableOfContents
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: reportlab. Install with: pip install -r requirements.txt"
        ) from exc

    class TOCDocTemplate(BaseDocTemplate):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._heading_count = 0
            self._last_outline_level = -1

        def afterFlowable(self, flowable):
            if not hasattr(flowable, "style"):
                return
            level_map = {
                "Heading1": 0,
                "Heading2": 1,
                "Heading3": 2,
            }
            level = level_map.get(flowable.style.name)
            if level is None:
                return
            text = flowable.getPlainText()
            self._heading_count += 1
            if level > self._last_outline_level + 1:
                level = self._last_outline_level + 1
            bookmark_key = f"h{level}_{self._heading_count}"
            # Create PDF outline entry (clickable in most PDF readers).
            self.canv.bookmarkPage(bookmark_key)
            self.canv.addOutlineEntry(text, bookmark_key, level=level, closed=False)
            self.notify("TOCEntry", (level, text, self.page))
            self._last_outline_level = level

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(
            doc.pagesize[0] - 0.6 * inch,
            0.5 * inch,
            str(canvas.getPageNumber()),
        )
        canvas.restoreState()

    styles = getSampleStyleSheet()
    if "CodeBlock" not in styles:
        styles.add(
            ParagraphStyle(
                name="CodeBlock",
                fontName="Courier",
                fontSize=9,
                leading=12,
            )
        )
    if "Heading1" not in styles:
        styles.add(ParagraphStyle(name="Heading1", fontSize=16, leading=18, spaceAfter=10))
    if "Heading2" not in styles:
        styles.add(ParagraphStyle(name="Heading2", fontSize=13, leading=15, spaceAfter=8))
    if "Heading3" not in styles:
        styles.add(ParagraphStyle(name="Heading3", fontSize=11, leading=13, spaceAfter=6))

    doc = TOCDocTemplate(
        str(output_path),
        pagesize=pagesizes.LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
    )

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])

    story = []
    title = repo_root.name
    now = datetime.now().strftime("%Y-%m-%d")

    story.append(Paragraph(f"Repository: {title}", styles["Title"]))
    story.append(Paragraph(f"Source: {source_label}", styles["Normal"]))
    story.append(Paragraph(f"Generated: {now}", styles["Normal"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Directory", styles["Heading1"]))
    story.append(Preformatted(tree_text, styles["CodeBlock"]))
    story.append(PageBreak())

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            fontName="Helvetica",
            name="TOCLevel0",
            fontSize=10,
            leftIndent=20,
            firstLineIndent=-20,
            spaceBefore=5,
            leading=12,
        ),
        ParagraphStyle(
            fontName="Helvetica",
            name="TOCLevel1",
            fontSize=9,
            leftIndent=35,
            firstLineIndent=-20,
            spaceBefore=3,
            leading=11,
        ),
        ParagraphStyle(
            fontName="Helvetica",
            name="TOCLevel2",
            fontSize=9,
            leftIndent=50,
            firstLineIndent=-20,
            spaceBefore=2,
            leading=10,
        ),
    ]
    story.append(Paragraph("Table of Contents", styles["Heading1"]))
    story.append(toc)
    story.append(PageBreak())

    current_dir_parts = []
    for path in files:
        rel = path.relative_to(repo_root)
        dir_parts = list(rel.parts[:-1])
        for idx, part in enumerate(dir_parts):
            if len(current_dir_parts) > idx and current_dir_parts[idx] == part:
                continue
            current_dir_parts = dir_parts[: idx + 1]
            heading_text = "/".join(current_dir_parts)
            style_name = "Heading1" if idx == 0 else "Heading2"
            story.append(Paragraph(heading_text, styles[style_name]))

        if len(dir_parts) >= 2:
            file_heading_style = "Heading3"
        else:
            file_heading_style = "Heading2"
        story.append(Paragraph(str(rel), styles[file_heading_style]))
        try:
            text = path.read_text(errors="replace")
        except OSError:
            text = "[Error reading file]"
        text = text.replace("\t", "    ")
        lines = build_code_lines(text, str(path), highlight=highlight)
        if show_line_numbers:
            lines = add_line_numbers(lines)
        joined = "\n".join(lines)
        if highlight:
            story.append(XPreformatted(joined, styles["CodeBlock"]))
        else:
            story.append(Preformatted(joined, styles["CodeBlock"]))
        story.append(PageBreak())

    doc.build(story)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Convert a GitHub repo to PDF.")
    parser.add_argument("source", help="GitHub repo URL or local path")
    parser.add_argument(
        "-o",
        "--output",
        default="repo.pdf",
        help="Output PDF path (default: repo.pdf)",
    )
    parser.add_argument(
        "--include-ext",
        default=",".join(DEFAULT_EXTS),
        help="Comma-separated list of file extensions to include",
    )
    parser.add_argument(
        "--exclude-dirs",
        default=",".join(sorted(DEFAULT_EXCLUDE_DIRS)),
        help="Comma-separated list of directories to exclude",
    )
    parser.add_argument(
        "--max-file-size-kb",
        type=int,
        default=512,
        help="Skip files larger than this size (KB)",
    )
    parser.add_argument(
        "--no-highlight",
        action="store_true",
        help="Disable syntax highlighting",
    )
    parser.add_argument(
        "--no-line-numbers",
        action="store_true",
        help="Disable line numbers",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    include_exts = {e.strip().lower() for e in args.include_ext.split(",") if e.strip()}
    exclude_dirs = {d.strip() for d in args.exclude_dirs.split(",") if d.strip()}

    temp_dir = None
    try:
        if is_github_url(args.source):
            temp_dir = tempfile.mkdtemp(prefix="gh2pdf_")
            repo_root = Path(temp_dir) / "repo"
            clone_repo(args.source, str(repo_root))
            source_label = args.source
        else:
            repo_root = Path(args.source).expanduser().resolve()
            if not repo_root.exists():
                raise RuntimeError(f"Path does not exist: {repo_root}")
            source_label = str(repo_root)

        tree_text = build_tree(repo_root, include_exts, exclude_dirs)
        files = collect_files(repo_root, include_exts, exclude_dirs, args.max_file_size_kb)

        if not files:
            raise RuntimeError("No files found with the selected extensions.")

        output_path = Path(args.output).expanduser().resolve()
        generate_pdf(
            output_path,
            repo_root,
            files,
            tree_text,
            source_label,
            highlight=not args.no_highlight,
            show_line_numbers=not args.no_line_numbers,
        )
        print(f"PDF created: {output_path}")

    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
