"""Rebuild the repo-root README.md from the platform docs.

The README is the hand-written header in docs/platform/_build/README.header.md
followed by a generated atlas of every Mermaid diagram in docs/platform/0*.md,
each linked back to the section it came from. Edit the header or the docs, then
run from the repo root:

    python scripts/build_docs_readme.py
"""
import collections
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "platform")
HEADER = os.path.join(DOCS, "_build", "README.header.md")
OUT = os.path.join(ROOT, "README.md")
LINK = "docs/platform/"

NAMES = {
    "01_HLD.md": "01 High-Level Design",
    "02_LLD.md": "02 Low-Level Design",
    "03_USER_FLOWS.md": "03 User Flows",
    "04_DATA_MODEL.md": "04 Data Model",
    "05_API_REFERENCE.md": "05 API Reference",
    "06_DEPLOYMENT_OPERATIONS.md": "06 Deployment & Operations",
    "07_SECURITY.md": "07 Security",
    "08_INTEGRATIONS.md": "08 Integrations",
    "09_GLOSSARY_AND_STATUS.md": "09 Glossary & Status",
}
TYPES = {
    "flowchart": "Flowchart", "graph": "Flowchart", "sequenceDiagram": "Sequence",
    "stateDiagram-v2": "State machine", "classDiagram": "Class diagram",
    "erDiagram": "ER diagram", "journey": "User journey", "gantt": "Schedule",
}
KINDS = ["Flowchart", "Sequence", "State machine", "Class diagram", "ER diagram", "User journey", "Schedule"]


def slug(heading):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    h = heading.strip().lower()
    return "".join(c for c in h if c.isalnum() or c in " -_").replace(" ", "-")


def strip_number(heading):
    return re.sub(r"^\d+(\.\d+)*\.?\s*", "", heading)


def collect():
    entries = []
    for f in NAMES:
        h2 = h3 = None
        buf = None
        n = 0
        for line in open(os.path.join(DOCS, f), encoding="utf-8").read().split("\n"):
            if buf is None:
                m = re.match(r"^(#{2,3}) (.+)$", line)
                if m:
                    if m.group(1) == "##":
                        h2, h3 = m.group(2), None
                    else:
                        h3 = m.group(2)
                if line.strip() == "```mermaid":
                    buf = []
                continue
            if line.strip() == "```":
                n += 1
                kind = buf[0].strip().split()[0] if buf else "?"
                title = strip_number(h2) + (" › " + strip_number(h3) if h3 else "")
                entries.append(dict(doc=f, num=n, head=h3 or h2, title=title,
                                    kind=TYPES.get(kind, kind), code="\n".join(buf)))
                buf = None
                continue
            buf.append(line)

    # Several diagrams under one heading get "(part k of n)".
    totals = collections.Counter((e["doc"], e["title"]) for e in entries)
    seen = collections.Counter()
    for e in entries:
        key = (e["doc"], e["title"])
        if totals[key] > 1:
            seen[key] += 1
            e["title"] += f" (part {seen[key]} of {totals[key]})"
    return entries


def build():
    entries = collect()
    header = open(HEADER, encoding="utf-8").read()
    header = header.replace("__DIAGRAM_COUNT__", str(len(entries)))
    out = [header]

    counts = collections.Counter((e["doc"], e["kind"]) for e in entries)
    out.append("### 8.0 Atlas summary\n")
    out.append(f"{len(entries)} diagrams across {len(NAMES)} documents "
               f"(plus {header.count('```mermaid')} in the sections above).\n")
    out.append("| Document | " + " | ".join(KINDS) + " | Total |")
    out.append("|---|" + "---:|" * (len(KINDS) + 1))
    for f, name in NAMES.items():
        row = [counts[(f, k)] for k in KINDS]
        cells = " | ".join(str(x) if x else "·" for x in row)
        out.append(f"| [{name}]({LINK}{f}) | {cells} | **{sum(row)}** |")
    totals = [sum(counts[(f, k)] for f in NAMES) for k in KINDS]
    out.append("| **All** | " + " | ".join(f"**{x}**" for x in totals) + f" | **{len(entries)}** |\n")

    for i, (f, name) in enumerate(NAMES.items(), 1):
        doc_entries = [e for e in entries if e["doc"] == f]
        if not doc_entries:
            continue
        out.append(f"### 8.{i} {name}\n")
        out.append(f"Source: [{f}]({LINK}{f}) · {len(doc_entries)} diagrams\n")
        out.append("| ID | Diagram | Type |")
        out.append("|---|---|---|")
        for e in doc_entries:
            did = f"D{f[:2]}-{e['num']:02d}"
            out.append(f"| [{did}](#{slug(did + ' ' + e['title'])}) | {e['title']} | {e['kind']} |")
        out.append("")
        for e in doc_entries:
            did = f"D{f[:2]}-{e['num']:02d}"
            out.append(f"#### {did} {e['title']}\n")
            out.append(f"*{e['kind']}* · from [{name} › {e['head']}]({LINK}{f}#{slug(e['head'])})\n")
            out.append("```mermaid\n" + e["code"] + "\n```\n")
        out.append("[Back to atlas summary](#80-atlas-summary)\n\n---\n")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"Wrote {OUT}: {len(entries)} atlas diagrams")


if __name__ == "__main__":
    build()
