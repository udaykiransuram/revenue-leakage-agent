"""Small dependency-free graph renderer for factual execution events."""
from html import escape
import textwrap


def latest_steps(events):
    if not events:
        return []
    run = events[-1].get("run_id")
    return [e for e in events if e.get("run_id") == run]


def event_title(event):
    kind = event.get("kind", "tool")
    if kind == "model":
        return "Model response"
    if kind == "approval":
        return "Approval check"
    return str(event.get("name", "Tool")).replace("_", " ").capitalize()


def trace_graph(events):
    """Arrows encode recorded sequence, not inferred dependencies or reasoning."""
    steps = latest_steps(events)
    if not steps:
        return ''
    nodes = []
    for i, event in enumerate(steps):
        y = 16 + i * 142
        status = str(event.get("status", "recorded")).lower()
        failed = status in ("error", "failed", "timeout")
        blocked = status == "blocked"
        color, fill = ("#b42318", "#fff3f1") if failed else ("#946200", "#fff9e9") if blocked else ("#087f74", "#effaf6") if status in ("success", "completed", "ok") else ("#52647d", "#f3f6fa")
        state = {"success":"Completed", "ok":"Completed", "error":"Failed", "timeout":"Timed out"}.get(status, status.capitalize())
        duration = event.get("duration_seconds")
        timing = f" · {duration:.2f}s" if isinstance(duration, (int, float)) else ""
        title = escape(event_title(event))
        summary = str(event.get("summary", "No summary recorded."))
        lines = textwrap.wrap(summary, width=48)[:2]
        if len(textwrap.wrap(summary, width=48)) > 2:
            lines[-1] = lines[-1][:-1] + '…'
        texts = ''.join(f'<text x="52" y="{y+77+j*18}" font-size="12" fill="#52647d">{escape(line)}</text>' for j, line in enumerate(lines))
        nodes.append(f'<rect x="12" y="{y}" width="366" height="112" rx="12" fill="{fill}" stroke="{color}" stroke-opacity=".3"/><circle cx="34" cy="{y+29}" r="11" fill="{color}"/><text x="34" y="{y+33}" text-anchor="middle" font-size="11" fill="white">{i+1}</text><text x="52" y="{y+33}" font-size="15" font-weight="600" fill="#152b49">{title}</text><text x="52" y="{y+54}" font-size="12" fill="{color}">{escape(state+timing)}</text>{texts}')
        if i < len(steps)-1:
            nodes.append(f'<path d="M195 {y+112}v22" stroke="#93a7ba" stroke-width="1.5" marker-end="url(#trace-arrow)"/>')
    height = len(steps)*142
    return f'<div style="max-width:480px;margin:12px auto"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 390 {height}" style="width:100%;height:auto" role="img" aria-label="Execution trace: {len(steps)} recorded steps in order" font-family="system-ui,sans-serif"><defs><marker id="trace-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6" fill="none" stroke="#93a7ba"/></marker></defs>{"".join(nodes)}</svg></div>'
