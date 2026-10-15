import os

from fastapi import FastAPI, Response

app = FastAPI()

VERSION = "v2"


@app.get("/")
def root():
    return {"version": VERSION, "pod": os.getenv("HOSTNAME", "local")}


@app.get("/health")
def health():
    return {"status": "alive"}


@app.get("/ready")
def ready(response: Response):
    if os.getenv("BREAK_READY") == "true":
        response.status_code = 503
        return {"status": "not ready"}
    return {"status": "ready"}
