from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Codebase AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/test/zain")
async def keso():
    return "zain"

@app.get("/test/{id}")
async def root(id: int):
    return {"message": f"Hello World, {id}!"}
