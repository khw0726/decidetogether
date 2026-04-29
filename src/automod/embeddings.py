"""Bedrock Titan text embedding helper.

Used by the rule-text suggestion feature to embed rule titles for cosine retrieval
across the reference-community corpus.
"""

import asyncio
import json
import struct
from typing import Optional

import boto3
import numpy as np

from .config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key or None,
            aws_secret_access_key=settings.aws_secret_key or None,
        )
    return _client


def _embed_sync(text: str) -> np.ndarray:
    body = json.dumps({"inputText": text, "dimensions": settings.embedding_dim, "normalize": True})
    resp = _get_client().invoke_model(
        modelId=settings.embedding_model,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    vec = np.asarray(payload["embedding"], dtype=np.float32)
    return vec


async def embed_text(text: str) -> np.ndarray:
    return await asyncio.to_thread(_embed_sync, text)


def pack_vector(vec: np.ndarray) -> bytes:
    arr = np.ascontiguousarray(vec, dtype=np.float32)
    return arr.tobytes()


def unpack_vector(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
