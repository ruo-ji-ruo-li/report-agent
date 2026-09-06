import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError

from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem, ReportMeta

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["reports"])


class ManualItem(BaseModel):
    name: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    ref_range_text: str | None = None
    abnormal_flag: str | None = None


class ManualEntry(BaseModel):
    meta: dict = Field(default_factory=dict)
    items: list[ManualItem]


class MetaPatch(BaseModel):
    sex: str | None = None
    age: float | None = None


async def _save_upload(file: UploadFile, upload_dir: str) -> str:
    suffix = Path(file.filename or "upload").suffix or ".bin"
    path = Path(upload_dir) / f"{uuid.uuid4().hex}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:  # noqa: ASYNC230 —— 上传落盘,本地磁盘小文件,阻塞可接受
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return str(path)


@router.post("/reports")
async def create_report(request: Request,
                        file: UploadFile | None = File(default=None)):  # noqa: B008 —— FastAPI 惯用法,File() 即参数声明
    """multipart `file` 上传 或 JSON 手动录入,二者必居其一。

    (brief 偏差,最小修正:fastapi>=0.141 把 File/UploadFile 参数并入 body 字段并
    整体按 form-data 处理 —— 保留 `manual: ManualEntry | None = None` 签名时,
    纯 JSON 请求也会走 request.form() 得到空表单,manual 恒为 None,JSON 手动录入
    不可用。故 manual 分支改为手动读取 body 并校验,上传分支与其余逻辑不变。)
    """
    db = request.app.state.db_access
    tasks = request.app.state.tasks
    runner = request.app.state.runner
    settings = request.app.state.deps.settings

    if file is not None:
        path = await _save_upload(file, settings.upload_dir)
        source = "pdf" if (file.filename or "").lower().endswith(".pdf") else "photo"
        report_id = await db.create_report(source, path, ReportMeta(source=source))
    else:
        body = await request.body()
        if not body:
            raise HTTPException(400, "必须提供文件(file)或手动录入 JSON(manual)")
        try:
            manual = ManualEntry.model_validate_json(body)
        except ValidationError as e:
            raise HTTPException(422, f"手动录入 JSON 校验失败: {str(e)[:200]}") from None
        meta = manual.meta or {}
        report_id = await db.create_report(
            "manual", None,
            ReportMeta(source="manual", name=meta.get("name"),
                       sex=meta.get("sex"), age=meta.get("age"),
                       institution=meta.get("institution"), report_date=meta.get("report_date")),
        )
        await db.save_raw_items(report_id, [
            RawReportItem(name=it.name, value_text=it.value_text,
                          value_num=it.value_num, unit=it.unit,
                          ref_range_text=it.ref_range_text, abnormal_flag=it.abnormal_flag)
            for it in manual.items
        ])
    task = await tasks.create(report_id)
    asyncio.create_task(runner.run_task(task.id))
    log.info("report_created", report_id=report_id, task_id=task.id, source=file and "upload")
    return {"report_id": report_id, "task_id": task.id}


@router.get("/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    detail = await request.app.state.db_access.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    task = await request.app.state.db_access.get_task_for_report(report_id)
    return {**detail, "task": task}


@router.patch("/reports/{report_id}/meta")
async def patch_meta(report_id: str, patch: MetaPatch, request: Request):
    db = request.app.state.db_access
    detail = await db.get_report_detail(report_id)
    if detail is None:
        raise HTTPException(404, "报告不存在")
    await db.update_report_meta(report_id, ReportMeta(sex=patch.sex, age=patch.age))
    task = await db.get_task_for_report(report_id)
    if task and task["status"] == "awaiting_meta":
        await request.app.state.tasks.resume_from_meta(task["id"])
        asyncio.create_task(request.app.state.runner.run_task(task["id"]))
        log.info("task_resumed_from_meta", task_id=task["id"])
    return {"report_id": report_id, "meta_updated": True}


@router.get("/reports/{report_id}/interpretation")
async def get_interpretation(report_id: str, request: Request):
    doc = await request.app.state.db_access.get_interpretation(report_id)
    if doc is None:
        raise HTTPException(404, "解读尚未生成")
    return doc


@router.get("/reports/{report_id}/followup-plan")
async def get_followup_plan(report_id: str, request: Request):
    doc = await request.app.state.db_access.get_followup(report_id)
    if doc is None:
        raise HTTPException(404, "复查计划尚未生成")
    return doc
