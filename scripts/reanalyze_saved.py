"""Re-evaluate a bounded number of saved jobs through the authenticated local API."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import models as m
from app.core.config import Settings
from app.database import make_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", type=UUID, required=True)
    parser.add_argument("--policy-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=20)
    args = parser.parse_args()
    settings = Settings()
    if settings.database_url is None or settings.api_token is None:
        raise SystemExit("数据库或 API Token 未配置")
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        with Session(engine) as session:
            rows = session.execute(
                sa.select(m.jobs.c.id, m.jobs.c.title)
                .where(m.jobs.c.latest_snapshot_id.is_not(None))
                .order_by(m.jobs.c.created_at)
                .limit(args.limit)
            ).all()
    finally:
        engine.dispose()
    payload = json.dumps(
        {"profile_id": str(args.profile_id), "policy_id": str(args.policy_id)}
    ).encode()
    succeeded = failed = 0
    for index, row in enumerate(rows, 1):
        request = Request(
            f"http://127.0.0.1:{settings.port}/api/v1/jobs/{row.id}/analyses",
            data=payload,
            headers={
                "Authorization": "Bearer " + settings.api_token.get_secret_value(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                result = json.load(response)
            succeeded += 1
            print(
                f"{index}/{len(rows)} {row.title}: "
                f"score={result.get('score')}, status={result.get('status')}"
            )
        except (HTTPError, URLError, TimeoutError):
            failed += 1
            print(f"{index}/{len(rows)} {row.title}: failed")
    print(f"完成：成功 {succeeded}，失败 {failed}；未执行沟通")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
