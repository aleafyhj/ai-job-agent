from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    token = request.app.state.settings.api_token
    if token is None:
        raise HTTPException(503, "请先配置 JOB_AGENT_API_TOKEN")
    if credentials is None or not compare_digest(
        credentials.credentials.encode(), token.get_secret_value().encode()
    ):
        raise HTTPException(401, "认证失败", headers={"WWW-Authenticate": "Bearer"})


def db_session(request: Request):
    factory = request.app.state.db_sessions
    if factory is None:
        raise HTTPException(503, "数据库未配置")
    try:
        with factory() as session:
            yield session
    except SQLAlchemyError:
        raise HTTPException(503, "数据库操作失败") from None


DB = Annotated[Session, Depends(db_session)]
