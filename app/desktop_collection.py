"""Collect a bounded sequence of visible job-detail pages with image evidence."""

import json
import re
import time
from uuid import uuid4

from app.desktop import pixel
from app.desktop_report import observe, verify_selection


def has_detail_start(detail):
    # A model flag alone must not turn a middle paragraph into a full job.
    first_number = re.match(r"\s*(\d+)\s*[、.．)]", detail.detail_text)
    return (
        detail.detail_start_visible
        and bool(
            re.search(
                r"岗位职责|职位描述|工作职责|岗位描述|工作内容|任职要求|职位要求",
                detail.detail_text,
            )
        )
        and (first_number is None or int(first_number[1]) == 1)
    )


def collect_detail(desktop, vision, card, evidence, root, args, *, evaluate=False):
    folder = root / "collections" / uuid4().hex
    folder.mkdir(parents=True)
    report = {
        "status": "collecting",
        "card": card.model_dump(),
        "observations": [],
        "detail_text": "",
        "detail_complete": False,
    }
    path = folder / "collection.json"

    def read(phase):
        image, _ = desktop.capture()
        detail, size, observation = observe(vision, image, evidence, phase, root)
        report["observations"].append(str(observation.relative_to(root)))
        verify_selection(card, detail)
        return detail, image, size

    def scroll(detail, size, ticks):
        point = args.right_scroll or (detail.detail_scroll.center if detail.detail_scroll else None)
        if point is None:
            raise RuntimeError("未识别详情滚动区域，停止采集")
        desktop.scroll(*pixel(point, size), ticks=ticks)
        time.sleep(args.settle)

    try:
        detail, image, size = read("detail")
        # Observe before scrolling: short jobs may already be fully visible.
        for _ in range(args.detail_scrolls):
            if has_detail_start(detail):
                break
            scroll(detail, size, 10)
            detail, image, size = read("detail")
        if not has_detail_start(detail):
            raise RuntimeError("未确认详情正文顶部，停止采集")

        parts = []
        complete = False
        for part in range(args.detail_scrolls + 1):
            parts.append(detail.detail_text)
            report["detail_text"] = "\n".join(parts)
            if detail.detail_complete:
                complete = True
                break
            if part == args.detail_scrolls:
                break
            # A repeated visible body after scrolling is evidence of no progress,
            # never evidence that all requirements were read.
            if len(parts) >= 2 and parts[-1].strip() == parts[-2].strip():
                report["stop_reason"] = "no_scroll_progress"
                break
            scroll(detail, size, -args.ticks)
            detail, image, size = read("detail")

        combined = report["detail_text"]
        if not combined.strip():
            raise RuntimeError("职位正文为空，停止采集")
        if complete and evaluate:
            detail, _, observation = observe(
                vision,
                image,
                {**evidence, "complete_detail_text": combined},
                "evaluate_all_text",
                root,
            )
            report["observations"].append(str(observation.relative_to(root)))
            verify_selection(card, detail)
        else:
            # Per-screen semantic matches can disagree on a sentence clipped at
            # the edge. Only the later full-text evaluation may produce a score.
            detail.requirements = []
            detail.hard_conditions = "unknown"
        detail.detail_text = combined
        detail.detail_complete = complete
        report.update(
            status="complete" if complete else "incomplete",
            detail_complete=complete,
            requirements=[item.model_dump() for item in detail.requirements],
            hard_conditions=detail.hard_conditions,
        )
        (folder / "detail.txt").write_text(combined, encoding="utf-8")
        return detail, image, folder
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("完整正文采集报告:", path.resolve())
