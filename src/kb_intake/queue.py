"""The review queue (intake-ui-llm-design-v1 §3.1 `queue`, §3.4): what the reviewer should look at after extraction,
grouped by problem.

- Items: every gap of the job (label, extract and review stages), and every table whose `continued_hint` is the weak
  kind (`same_first_row`; the strong `continued_heading` needs no look). Before extraction the queue is empty.
- Status: `resolved` when review dealt with the item (the gap carries `resolved`, or an `ack` op targets the item);
  else `seen` when the UI reported it shown (work state only, never delivered); else `open`.
- Item ids are stable across review: `gap:{code}:{page or 0}:{region_id or table_id or -}`, `hint:{region_id}` (the
  table's region; table ids are renumbered by review), `llm:{kind}:{region_id}`.
"""

from kb_intake import review
from kb_intake.jobs import Jobs
from kb_intake.package import read_jsonl
from kb_intake.vocab import GAP_CODES, GAP_LABELS

POST_EXTRACT = ("extracted", "in_review", "confirmed", "delivered")
GROUPS = {**{c: GAP_LABELS[c] for c in GAP_CODES}, "continued_hint": "Continuation hints (weak)"}


def item_id(target: dict) -> str:
    if target["kind"] == "hint":
        return f"hint:{target['region_id']}"
    if target["kind"] == "llm":
        return f"llm:{target['code']}:{target['region_id']}"
    return f"gap:{target['code']}:{target['page_no'] or 0}:{target['region_id'] or target['table_id'] or '-'}"


def _target(kind: str, code: str, page_no, region_id, table_id) -> dict:
    return {"kind": kind, "code": code, "page_no": page_no, "region_id": region_id, "table_id": table_id}


def items(jobs: Jobs, job_id: str) -> list[dict]:
    """The queue items of a job, in group order then page order. KeyError for a job not labelled yet."""
    job = jobs.get(job_id)
    d = job.dir
    if not (d / "label" / "regions.jsonl").is_file():
        raise KeyError(f"job {job_id} is not labelled yet")
    if job.status not in POST_EXTRACT or not (d / "extract" / "regions.jsonl").is_file():
        return []
    state = review.current(d)
    regions = {r["region_id"]: r for r in state["regions"]}
    acked = {}
    for seq, op in enumerate(jobs.ops(job_id), 1):
        if op.get("action") == "ack" and op.get("target"):
            acked[item_id(op["target"])] = {"action": "ack", "review_seq": seq}
    seen = jobs.seen(job_id)
    out, used = [], set()

    def add(group: str, target: dict, bbox, detail: str, resolution: dict | None) -> None:
        iid = base = item_id(target)
        n = 1
        while iid in used:                  # two gaps with the same code on the same region: keep both
            n += 1
            iid = f"{base}:{n}"
        used.add(iid)
        resolution = resolution or acked.get(iid)
        status = "resolved" if resolution else "seen" if iid in seen else "open"
        out.append({"item_id": iid, "group": group, "kind": target["kind"], "code": target["code"],
                    "page_no": target["page_no"], "region_id": target["region_id"], "table_id": target["table_id"],
                    "bbox": bbox, "detail": detail, "status": status, "resolution": resolution, "target": target})

    for g in review.all_gaps(d, state):
        rid = g.get("region_id")
        bbox = g.get("bbox") or (regions[rid]["bbox"] if rid in regions else None)
        add(g["code"], _target("gap", g["code"], g["page_no"], rid, g.get("table_id")), bbox, g["detail"],
            g.get("resolved"))
    for t in state["tables"]:
        hint = t.get("continued_hint")
        if hint and hint["reason"] == "same_first_row":
            add("continued_hint", _target("hint", hint["reason"], t["page_no"], t["region_id"], t["table_id"]),
                t["bbox"], f"May continue table {hint['table_id']} (same column count and first row); a hint only, "
                           "intake does not merge tables", None)
    order = list(GROUPS)
    return sorted(out, key=lambda i: (order.index(i["group"]) if i["group"] in order else len(order),
                                      i["page_no"] or 0, i["item_id"]))


def view(queue_items: list[dict]) -> dict:
    """{"total", "done", "groups": [{"key", "label", "total", "open", "seen", "resolved", "items"}]}."""
    groups: dict[str, dict] = {}
    for i in queue_items:
        g = groups.setdefault(i["group"], {"key": i["group"], "label": GROUPS.get(i["group"], i["group"]), "total": 0,
                                           "open": 0, "seen": 0, "resolved": 0, "items": []})
        g["total"] += 1
        g[i["status"]] += 1
        g["items"].append(i)
    return {"total": len(queue_items), "done": sum(i["status"] == "resolved" for i in queue_items),
            "groups": list(groups.values())}
