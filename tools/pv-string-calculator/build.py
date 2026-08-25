#!/usr/bin/env python3
"""Build static/string-calculator.html from ui.html + engine.js.

The served page is a single self-contained file: this script inlines
engine.js into ui.html and wraps it in a full HTML document skeleton.
Run from this directory after editing either source file:

    python3 build.py
    node test.js        # always re-run the engine tests too
"""
from pathlib import Path

here = Path(__file__).parent
ui = (here / "ui.html").read_text()
engine = (here / "engine.js").read_text()
merged = ui.replace("/*__ENGINE__*/", engine)

lines = merged.split("\n")
title, rest = lines[0], "\n".join(lines[1:])
idx = rest.index("</style>") + len("</style>")
full = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + title + "\n" + rest[:idx] + "\n</head>\n<body>" + rest[idx:] + "\n</body>\n</html>\n"
)
out = here / ".." / ".." / "static" / "string-calculator.html"
out.write_text(full)
print(f"wrote {out.resolve()} ({len(full)} bytes)")
