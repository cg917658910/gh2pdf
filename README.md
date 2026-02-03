# gh2pdf

Convert a GitHub repository (URL or local path) into a PDF for code reading.

## Install

```
pip install -r requirements.txt
```

## Usage

```
python gh2pdf.py https://github.com/user/repo -o repo.pdf
python gh2pdf.py /path/to/repo -o repo.pdf
```

## Options

- `--include-ext` Comma-separated extensions to include (default includes common code files).
- `--exclude-dirs` Comma-separated dirs to skip (default includes .git, node_modules, vendor, etc.).
- `--max-file-size-kb` Skip files larger than this size.
- `--no-highlight` Disable syntax highlighting.
- `--no-line-numbers` Disable line numbers.

## Notes

- This version generates a directory section, a table of contents, page numbers, and syntax highlighting.
