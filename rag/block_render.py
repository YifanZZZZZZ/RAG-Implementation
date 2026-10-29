"""Render existing corpus blocks without changing extraction or cleaning.
Copied from this project's document_pipeline.markdown for consistent offsets.
"""
import re
import html

def markdown(blocks):
    out = []
    for b in blocks:
        kind = b["type"]
        if kind == "heading":
            out.append("#" * b["level"] + " " + b["text"])
        elif kind == "paragraph":
            out.append(b["text"])
        elif kind in {"code", "pdf_layout"}:
            fence = "`" * max(3, max([len(x) for x in re.findall(r"`+", b["text"])] or [0]) + 1)
            out.append(fence + b.get("language", "text") + "\n" + b["text"] + "\n" + fence)
        elif kind == "list":
            lines = []
            try:
                start = int(b.get("start", 1))
            except ValueError:
                start = 1
            for i, item in enumerate(b["items"]):
                prefix = f"{start+i}. " if b["ordered"] else "- "
                parts = markdown(item).splitlines() or [""]
                lines.append(prefix + parts[0])
                lines.extend(" " * len(prefix) + line for line in parts[1:])
            out.append("\n".join(lines))
        elif kind == "facts":
            out.append("\n".join(f"- **{x['label']}**: {x['value']}" for x in b["entries"]))
        elif kind == "table":
            rows = b["rows"]
            if not rows:
                continue
            simple = all(c["rowspan"] == c["colspan"] == 1 for r in rows for c in r)
            simple = simple and len({len(r) for r in rows}) == 1 and all(c["header"] for c in rows[0])
            if b["caption"]:
                out.append(b["caption"])
            if simple:
                formatted = ["| " + " | ".join(c["text"].replace("|", "\\|") for c in row) + " |" for row in rows]
                formatted.insert(1, "| " + " | ".join("---" for _ in rows[0]) + " |")
                out.append("\n".join(formatted))
            else:
                # Raw HTML tables are valid within Markdown; preserve spans explicitly.
                rendered = ["<table>"]
                for row in rows:
                    cells = []
                    for c in row:
                        tag = "th" if c["header"] else "td"
                        cells.append(f'<{tag} rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">{html.escape(c["text"])}</{tag}>')
                    rendered.append("<tr>" + "".join(cells) + "</tr>")
                out.append("\n".join(rendered + ["</table>"]))
        elif kind in {"group", "quote"}:
            text = markdown(b["children"])
            out.append("\n".join("> " + line for line in text.splitlines()) if kind == "quote" else text)
    return "\n\n".join(x for x in out if x).strip()

