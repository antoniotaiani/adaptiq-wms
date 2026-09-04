from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.config import TEMPLATES_DIR

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

@router.get("/", response_class=HTMLResponse)
async def serve_workstation(request: Request):
    return templates.TemplateResponse(request, "operator.html")

@router.get("/portal", response_class=HTMLResponse)
async def serve_portal(request: Request):
    return templates.TemplateResponse(request, "portal.html")
