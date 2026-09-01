"""Optional OIDC login routes; demo mode remains the zero-configuration default."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.coordination import database_write_lock
from app.services.identity import begin_oidc_login, complete_oidc_login, oidc_enabled

router=APIRouter()

@router.get('/auth/login', name='auth_login')
def auth_login(request: Request):
    if not oidc_enabled(): return RedirectResponse('/', status_code=303)
    try: return RedirectResponse(begin_oidc_login(request.session), status_code=303)
    except Exception as exc: raise HTTPException(status_code=503, detail=f"OIDC login is unavailable: {exc}") from exc

@router.get('/auth/callback', name='auth_callback')
def auth_callback(request: Request, state: str='', code: str='', db: Session=Depends(get_db)):
    if not oidc_enabled(): return RedirectResponse('/', status_code=303)
    try:
        with database_write_lock():
            complete_oidc_login(request.session, db, state=state, code=code); db.commit()
        return RedirectResponse('/', status_code=303)
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=401, detail=f"OIDC login failed: {exc}") from exc

@router.get('/auth/logout', name='auth_logout')
def auth_logout(request: Request):
    request.session.clear(); return RedirectResponse('/portal' if oidc_enabled() else '/', status_code=303)
