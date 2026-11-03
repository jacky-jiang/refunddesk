from fastapi import FastAPI

app = FastAPI(title="RefundDesk Mock Payment")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive", "service": "mock-payment"}
