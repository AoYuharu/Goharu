import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db import AgentNetDB
from embedder import Embedder
from retriever import Retriever
from curator import Curator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentnet")

app = FastAPI(title="AgentNet Registry")

db = AgentNetDB()
embedder = Embedder()
retriever = Retriever(db, embedder)
curator = Curator(db, retriever, embedder)


class RegisterRequest(BaseModel):
    agent_id: str
    name: str
    summary: str | None = None
    device_id: str | None = None


class CapabilityRequest(BaseModel):
    agent_id: str
    capability_id: str
    name: str
    description: str


@app.post("/register")
def register(req: RegisterRequest):
    if db.agent_exists(req.agent_id):
        raise HTTPException(status_code=409, detail="agent_id already exists")
    try:
        result = db.register_agent(req.agent_id, req.name, req.summary, req.device_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return result


@app.post("/capabilities")
def add_capability(req: CapabilityRequest):
    if not db.agent_exists(req.agent_id):
        raise HTTPException(status_code=404, detail="agent not found")
    decision = curator.reconcile(
        req.agent_id, req.capability_id, req.name, req.description
    )
    return {"agent_id": req.agent_id, "decision": decision}


@app.get("/search")
def search(q: str, top_k: int = 10):
    results = retriever.search(q, top_k=top_k)
    name_matches = db.search_agents_by_name(q)
    return {"query": q, "results": results, "name_matches": name_matches}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    agent = db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    capabilities = db.list_capabilities(agent_id)
    return {"agent": agent, "capabilities": capabilities}


@app.get("/list")
def list_agents():
    agents = []
    for row in db.query_all("SELECT agent_id, name, summary, status FROM agents ORDER BY agent_id"):
        agent = dict(row)
        agent["capabilities"] = db.list_capabilities(agent["agent_id"])
        agents.append(agent)
    return {"agents": agents}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "fts": db.fts_enabled,
        "embedding": embedder.available,
    }
