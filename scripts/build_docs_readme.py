"""Rebuild the repo-root README.md from the platform docs.

The README is the product story in docs/platform/_build/README.story.md. Each
{{Dnn-mm}} placeholder in it is replaced by the mm-th Mermaid diagram of
docs/platform/nn_*.md, so the README always carries the current diagrams. The
build fails if any diagram is missing from the story or placed twice. Edit the
story or the docs, then run from the repo root:

    python scripts/build_docs_readme.py
"""
import collections
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "platform")
STORY = os.path.join(DOCS, "_build", "README.story.md")
OUT = os.path.join(ROOT, "README.md")

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
    entries = {f"D{e['doc'][:2]}-{e['num']:02d}": e for e in collect()}
    story = open(STORY, encoding="utf-8").read()
    used = re.findall(r"\{\{(D\d{2}-\d{2})\}\}", story)

    unknown = sorted(set(used) - set(entries))
    repeated = sorted(d for d, n in collections.Counter(used).items() if n > 1)
    missing = sorted(set(entries) - set(used))
    if unknown or repeated or missing:
        raise SystemExit(f"story placeholders out of sync: unknown={unknown} "
                         f"repeated={repeated} missing={missing}")

    def diagram(match):
        return "```mermaid\n" + entries[match.group(1)]["code"] + "\n```"

    readme = re.sub(r"\{\{(D\d{2}-\d{2})\}\}", diagram, story)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(readme)
    print(f"Wrote {OUT}: {len(entries)} doc diagrams + "
          f"{story.count('```mermaid')} README-only diagrams")


if __name__ == "__main__":
    build()
