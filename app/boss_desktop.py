"""Run with python -m app.boss_desktop. Manual login, foreground browser only."""

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import models as m
from app.api.resources import get_row, policy_read, profile_read
from app.collected_jobs import persist_analysis
from app.core.config import Settings
from app.database import make_engine
from app.desktop import Desktop, pixel
from app.desktop_collection import collect_detail
from app.desktop_report import ground_card, observe, verify_selection
from app.mouse_bridge import BridgeMouse
from app.vision import Vision, match_report


def identity(card):
    return hashlib.sha256(
        json.dumps(
            [card.title.strip(), card.company.strip(), card.city.strip(), card.salary.strip()],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(desktop, vision, profile, policy, root, args, before_communicate=None, on_analysis=None):
    """Bounded loop. One fresh card click at a time; no stale per-page coordinate batch."""
    state_path = root / "history.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    seen = set()
    processed = communications = 0
    skills = profile["skills"]
    # Never send contact details, resume bytes, or arbitrary facts to the vision service.
    evidence = {
        key: profile[key]
        for key in (
            "skills",
            "education",
            "experience_years",
            "preferred_cities",
            "remote_preference",
        )
    }
    for page in range(args.pages):
        while processed < args.jobs:
            image, size = desktop.capture()
            screen, size, observation_folder = observe(vision, image, evidence, "list", root)
            if screen.blocked or screen.uncertain:
                raise RuntimeError("页面验证、弹窗或识别不确定，已暂停")
            left_scroll = args.left_scroll or (
                screen.list_scroll.center if screen.list_scroll else None
            )
            right_scroll = args.right_scroll or (
                screen.detail_scroll.center if screen.detail_scroll else None
            )
            if left_scroll is None or right_scroll is None:
                raise RuntimeError("未能识别左右滚动区域；请查看报告或显式提供滚动坐标")
            card = next((item for item in screen.cards if identity(item) not in seen), None)
            if card is None:
                break
            key = identity(card)
            seen.add(key)
            if card.company in policy.get("blocked_companies", []):
                continue
            history_key = str(profile["id"]) + ":" + key
            previous = state.get(history_key)
            if previous and (
                previous["state"].startswith("communication_")
                or (
                    not args.communicate
                    and previous.get("profile_version") == profile["version_id"]
                    and previous.get("policy_version") == policy["version_id"]
                )
            ):
                continue
            target = ground_card(vision, image, card, screen.cards, observation_folder)
            print("精确标题点击坐标:", target)
            desktop.click(*target, expected=image, target_only=True, diagnostics=observation_folder)
            (observation_folder / "action.json").write_text(
                json.dumps(
                    {
                        "action": "card_click",
                        "card": card.model_dump(),
                        "pixel": target,
                        "status": "input_executed",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            time.sleep(args.settle)
            detail, detail_image, collection_folder = collect_detail(
                desktop, vision, card, evidence, root, args, evaluate=True
            )
            combined = detail.detail_text
            complete = detail.detail_complete
            hard = detail.hard_conditions
            decision = match_report(detail, skills, policy)
            value = decision["score"]
            processed += 1
            record = {
                "card": card.model_dump(),
                "score": value,
                "decision": decision,
                "hard_conditions": hard,
                "profile_version": profile["version_id"],
                "policy_version": policy["version_id"],
                "state": "analyzed" if value is not None and hard != "unknown" else "needs_review",
                "requirements": [item.model_dump() for item in detail.requirements],
                "detail_text": combined,
                "detail_complete": complete,
                "collection_report": str(collection_folder / "collection.json"),
            }
            screenshot_path = root / f"detail-{uuid4().hex}.png"
            screenshot_path.write_bytes(detail_image)
            if on_analysis is not None:
                record.update(on_analysis(key, card, detail, record, screenshot_path))
            if args.communicate and decision["eligible"]:
                if before_communicate is None:
                    raise RuntimeError("缺少投递前版本校验，禁止沟通")
                before_communicate()
                day = datetime.now(ZoneInfo(policy["timezone"])).date().isoformat()
                used = sum(
                    1
                    for name, item in state.items()
                    if name.startswith(str(profile["id"]) + ":")
                    and item.get("communication_day") == day
                    and item["state"].startswith("communication_")
                )
                if used >= policy["daily_limit"]:
                    raise RuntimeError("本次沟通次数达到策略限额，停止")
                # Persist before irreversible input; never retry uncertain communication.
                record["state"] = "communication_pending"
                record["communication_day"] = day
                state[history_key] = record
                save_state(state_path, state)
                fresh_image, fresh_size = desktop.capture()
                fresh = vision.inspect(fresh_image, evidence, "confirm_communication_target")
                if (
                    fresh.blocked
                    or fresh.uncertain
                    or fresh.selected is None
                    or identity(fresh.selected) != key
                    or fresh.communicate is None
                ):
                    raise RuntimeError("沟通前无法确认职位和按钮，保留 pending，不自动重试")
                desktop.click(*pixel(fresh.communicate.center, fresh_size), expected=fresh_image)
                communications += 1
                time.sleep(args.settle)
                result_image, _ = desktop.capture()
                result = vision.inspect(result_image, evidence, "communication_result")
                record["state"] = (
                    "communication_confirmed"
                    if result.communication_confirmed
                    and not result.blocked
                    and not result.uncertain
                    else "communication_unknown"
                )
                state[history_key] = record
                save_state(state_path, state)
                # Chat navigation may replace the job list. Do not click through it blindly.
                if record["state"] != "communication_confirmed" or not result.cards:
                    raise RuntimeError("沟通结果已记录；请核查并回到职位列表后再启动")
                continue
            state[history_key] = record
            save_state(state_path, state)
            # Return the detail pane to its top for the next card.
            print(f"已分析 {processed}: {card.title} | 匹配度={value} | 硬条件={hard}")
            for _ in range(args.detail_scrolls):
                desktop.scroll(*pixel(right_scroll, size), ticks=args.ticks)
            time.sleep(args.settle)
        if processed >= args.jobs:
            break
        if page + 1 < args.pages:
            desktop.scroll(*pixel(left_scroll, size), ticks=-args.ticks)
            time.sleep(args.settle)
    return processed


def main():
    parser = argparse.ArgumentParser(description="BOSS直聘截图/坐标执行器")
    parser.add_argument(
        "action",
        choices=[
            "screenshot",
            "move",
            "click",
            "scroll",
            "inspect",
            "click-card",
            "collect-detail",
            "run",
        ],
    )
    parser.add_argument("--image", type=Path, help="仅用于 inspect：识别已有图片，不操作桌面")
    parser.add_argument(
        "--card-index", type=int, default=1, help="click-card：本次实时识别的卡片序号，从1开始"
    )
    parser.add_argument("--title", default="BOSS直聘")
    parser.add_argument("--x", type=int)
    parser.add_argument("--y", type=int)
    parser.add_argument("--ticks", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--profile-id", type=UUID)
    parser.add_argument("--policy-id", type=UUID)
    parser.add_argument("--pages", type=int, choices=range(1, 21), default=3)
    parser.add_argument("--jobs", type=int, choices=range(1, 201), default=20)
    parser.add_argument("--detail-scrolls", type=int, choices=range(1, 21), default=5)
    parser.add_argument("--settle", type=float, default=2)
    parser.add_argument(
        "--left-scroll", nargs=2, type=int, help="覆盖模型识别的左侧滚动点，归一化坐标"
    )
    parser.add_argument(
        "--right-scroll", nargs=2, type=int, help="覆盖模型识别的右侧滚动点，归一化坐标"
    )
    parser.add_argument("--communicate", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    root = settings.artifacts_dir / "boss-desktop"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "desktop.lock"
    try:
        lock = lock_path.open("xb")
    except FileExistsError:
        parser.exit(1, "已有执行器或遗留锁；确认旧进程已停止后再移除 desktop.lock\n")
    engine = None
    try:
        if not 1 <= args.settle <= 30:
            raise ValueError("settle 必须为 1..30 秒")
        if args.image and args.action != "inspect":
            raise ValueError("--image 只能配合 inspect；离线图片不能用于点击")
        desktop = None
        if args.image is None:
            desktop = Desktop(args.title)
            if getattr(settings, "mouse_python", None) is not None:
                desktop.mouse = BridgeMouse(settings.mouse_python, args.title)
                print("鼠标工作进程:", settings.mouse_python)
            print("请在5秒内切换到已手动登录的 BOSS直聘职位列表；Ctrl+C停止")
            time.sleep(5)
        if args.action in {"inspect", "click-card"}:
            image = args.image.read_bytes() if args.image else desktop.capture()[0]
            vision = Vision(settings)
            screen, size, folder = observe(vision, image, {"skills": []}, "list", root)
            if screen.blocked or screen.uncertain:
                raise RuntimeError("识别不确定，报告已保存，不执行点击")
            for index, card in enumerate(screen.cards, 1):
                print(
                    f"#{index} {card.title} | {card.company} | {card.salary} | "
                    f"像素{pixel(card.center, size)}"
                )
            print("坐标预览图:", (folder / "targets.png").resolve())
            if args.action == "click-card":
                if not 1 <= args.card_index <= len(screen.cards):
                    raise ValueError("卡片序号超出本次识别范围")
                card = screen.cards[args.card_index - 1]
                target = ground_card(vision, image, card, screen.cards, folder)
                print("精确标题点击坐标:", target)
                desktop.click(*target, expected=image, target_only=True, diagnostics=folder)
                (folder / "action.json").write_text(
                    json.dumps(
                        {
                            "action": "card_click",
                            "card_index": args.card_index,
                            "pixel": target,
                            "status": "input_executed",
                        }
                    ),
                    encoding="utf-8",
                )
                time.sleep(args.settle)
                after, _ = desktop.capture()
                result, _, _ = observe(vision, after, {"skills": []}, "detail", root)
                verify_selection(card, result)
                print("已确认：右侧详情切换到选中的职位；没有执行沟通")
        elif args.action == "collect-detail":
            vision = Vision(settings)
            image, _ = desktop.capture()
            screen, _, _ = observe(vision, image, {"skills": []}, "list", root)
            if screen.blocked or screen.uncertain or screen.selected is None:
                raise RuntimeError("无法确认当前选中的职位，未采集")
            detail, _, folder = collect_detail(
                desktop, vision, screen.selected, {"skills": []}, root, args
            )
            print("正文采集完整:", detail.detail_complete)
            print("正文文本:", (folder / "detail.txt").resolve())
            print("未提供能力档案，本次只采集，不评分、不沟通")
        elif args.action == "screenshot":
            image, _ = desktop.capture()
            path = root / "screen.png"
            path.write_bytes(image)
            print("截图已保存到:", path.resolve())
        elif args.action in {"move", "click", "scroll"}:
            if args.x is None or args.y is None:
                raise ValueError("需要 --x 和 --y 原始屏幕像素坐标")
            before, _ = desktop.capture()
            before_path = root / f"{args.action}-before.png"
            before_path.write_bytes(before)
            print("操作前截图:", before_path.resolve())
            if args.action == "move":
                actual = desktop.move(args.x, args.y, diagnostics=root)
                print("已核对鼠标坐标:", actual, "；仅移动，未点击，未调用千问")
            elif args.action == "click":
                desktop.click(args.x, args.y, expected=before, diagnostics=root)
                print(f"已执行鼠标点击: ({args.x}, {args.y})；不代表网页业务操作已成功")
            else:
                desktop.scroll(args.x, args.y, ticks=-args.ticks)
                print(f"已执行向下滚动: ({args.x}, {args.y})，{args.ticks} 刻度")
            time.sleep(args.settle)
            try:
                after, _ = desktop.capture()
            except RuntimeError:
                print("输入操作已执行，但窗口发生变化，无法保存操作后截图；请人工核查")
            else:
                after_path = root / f"{args.action}-after.png"
                after_path.write_bytes(after)
                print("操作后截图:", after_path.resolve())
        else:
            if not args.profile_id or not args.policy_id or not settings.database_url:
                raise ValueError("run 需要档案ID、策略ID和数据库连接")
            vision = Vision(settings)
            engine = make_engine(settings.database_url.get_secret_value())
            with Session(engine) as session:
                profile = profile_read(
                    session, get_row(session, m.profiles, args.profile_id)
                ).model_dump(mode="json")
                policy = policy_read(
                    session, get_row(session, m.policies, args.policy_id)
                ).model_dump(mode="json")
            if "boss" not in policy["allowed_platforms"]:
                raise ValueError("策略未允许 BOSS直聘")
            if args.communicate and not policy["auto_apply"]:
                raise ValueError("需先显式开启策略 auto_apply 才可启用沟通")

            def check_versions():
                with Session(engine) as session:
                    current_profile = get_row(session, m.profiles, args.profile_id)
                    current_policy = get_row(session, m.policies, args.policy_id)
                    if (
                        str(current_profile["current_version_id"]) != profile["version_id"]
                        or str(current_policy["current_version_id"]) != policy["version_id"]
                    ):
                        raise RuntimeError("能力档案或策略已经变化，请重新启动")

            def store_analysis(key, card, detail, record, image_path):
                return persist_analysis(
                    engine, settings, profile, policy, key, card, detail, record, image_path
                )

            print(
                "本次分析数量:",
                run(desktop, vision, profile, policy, root, args, check_versions, store_analysis),
            )
    except KeyboardInterrupt:
        print("用户已停止")
    except Exception as exc:
        # Avoid leaking database/model credentials in provider or driver exceptions.
        print("执行器已停止:", type(exc).__name__)
        if isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc))
        raise SystemExit(1) from None
    finally:
        if engine:
            engine.dispose()
        lock.close()
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
