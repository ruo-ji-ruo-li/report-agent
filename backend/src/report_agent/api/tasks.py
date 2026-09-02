from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    info = await request.app.state.tasks.get(task_id)
    if info is None:
        raise HTTPException(404, "任务不存在")
    return {
        "task_id": info.id, "report_id": info.report_id, "status": info.status,
        "stage": info.stage, "timings": info.timings,
        "completed_stages": list(info.checkpoints.keys()),
        "error": info.error,
    }
