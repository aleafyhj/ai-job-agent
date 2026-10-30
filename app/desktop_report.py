"""Traceable image observations; never expose image payloads or provider credentials."""

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageDraw

from app.desktop import pixel


def observe(vision, image, evidence, phase, root):
    folder = (
        root
        / "observations"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    )
    folder.mkdir(parents=True)
    with Image.open(BytesIO(image)) as source:
        size = source.size
        canvas = source.convert("RGB")
    (folder / "screen.png").write_bytes(image)
    report = {
        "phase": phase,
        "created_at": datetime.now(UTC).isoformat(),
        "width": size[0],
        "height": size[1],
        "sha256": hashlib.sha256(image).hexdigest(),
        "status": "recognizing",
    }
    path = folder / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        screen = vision.inspect(image, evidence, phase)
        report.update(status="recognized", result=screen.model_dump(), cards=[])
        draw = ImageDraw.Draw(canvas)
        for index, card in enumerate(screen.cards, 1):
            x, y = pixel(card.center, size)
            report["cards"].append({"index": index, **card.model_dump(), "pixel": [x, y]})
            draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline="red", width=3)
            draw.line((x - 20, y, x + 20, y), fill="red", width=2)
            draw.line((x, y - 20, x, y + 20), fill="red", width=2)
            draw.text((x + 15, y + 15), f"#{index} ({x}, {y})", fill="red", stroke_width=1)
        canvas.save(folder / "targets.png")
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("本次识别报告:", path.resolve())
    return screen, size, folder


def verify_selection(expected, observed):
    """Identity checks ignore position, which can change after selecting a card."""
    if observed.blocked or observed.uncertain or observed.selected is None:
        raise RuntimeError("详情尚未就绪或无法确认身份，停止后续操作")

    def normalized(value):
        return "".join(value.split()).casefold()

    if any(
        normalized(getattr(expected, field)) != normalized(getattr(observed.selected, field))
        for field in ("title", "company", "city", "salary")
    ):
        raise RuntimeError("点击后职位身份不匹配，停止后续操作")
