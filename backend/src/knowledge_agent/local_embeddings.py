"""Local CPU embedding service; weights loaded once before accepting requests."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from .config import ROOT

MODEL_ID = 'BAAI/bge-small-zh-v1.5'
MODEL_DIR = ROOT / 'runtime/models/bge-small-zh-v1.5'
encoder = None


def load_encoder(download=False):
    from sentence_transformers import SentenceTransformer
    if download:
        from huggingface_hub import snapshot_download
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_download(MODEL_ID, local_dir=str(MODEL_DIR),
                          allow_patterns=['*.json','*.txt','*.safetensors','1_Pooling/*'])
    return SentenceTransformer(str(MODEL_DIR), device='cpu', local_files_only=True, trust_remote_code=False)


@asynccontextmanager
async def lifespan(app):
    global encoder
    encoder = load_encoder()
    encoder.max_seq_length = 512
    encoder.encode(['预热'], normalize_embeddings=True)
    yield


app = FastAPI(title='Local Chinese Embeddings', lifespan=lifespan)


class Input(BaseModel):
    model: str
    input: list[str] = Field(min_length=1,max_length=32)


@app.get('/health')
def health():
    return {'ready': encoder is not None, 'model': MODEL_ID, 'dimensions':512}


@app.post('/embeddings')
def embeddings(body:Input):
    if body.model != MODEL_ID or any(len(t)>4000 for t in body.input):
        raise HTTPException(400,'INVALID_INPUT')
    vectors=encoder.encode(body.input, normalize_embeddings=True, show_progress_bar=False)
    return {'data':[{'index':i,'embedding':v.tolist()} for i,v in enumerate(vectors)],
            'model':MODEL_ID,'usage':None}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true');args=parser.parse_args()
    if args.download:
        model=load_encoder(download=True)
        result=model.encode(['项目负责人','验收标准'],normalize_embeddings=True)
        print({'model':MODEL_ID,'shape':list(result.shape),'semantic_embeddings':True})
