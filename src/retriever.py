"""retriever.py - 시행계획·모집요강·권장과목 문서 RAG (하이브리드 검색)

입력: data/raw/text/*.txt — 원본 PDF·HWP에서 쪽 표시("=== page N ===")를 붙여 미리 뽑아 둔 텍스트(데이터 준비 단계, 커밋 안 함)
검색: Chroma(Titan v2 임베딩) 벡터 검색 + BM25(kiwipiepy 형태소) 를 RRF로 합친다 (교안 day2 hybrid.py 방식).
대학을 지정하면 그 대학 문서 안에서만 찾는다. 결과에는 문서명·학년도·쪽을 붙인다.

인덱스 만들기: python -m src.retriever --build   (chroma_db/ 에 저장, 커밋 안 함)
"""
import re
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from kiwipiepy import Kiwi
from langchain_aws import BedrockEmbeddings
from langchain_chroma import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
TEXT_DIR = ROOT / "data" / "raw" / "text"
RAW_DIRS = [ROOT / "data" / "raw" / "policies", ROOT / "data" / "raw" / "courses"]
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION = "ipsi_docs"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"   # 교안 day2·day6와 같은 임베딩
PAGE = re.compile(r"\n=== page (\d+) ===\n")
RRF_K = 60
WEIGHTS = {"bm25": 0.4, "vector": 0.6}         # 교안 hybrid.py와 비슷한 비중(키워드형 질문이 많아 BM25를 조금 높임)

_kiwi = Kiwi()


def korean_tokenizer(text: str) -> list[str]:
    """한국어 형태소 토크나이저 (교안 day2 사전 검증본): 명사·어간·숫자·외래어만 남긴다."""
    keep = {"NNG", "NNP", "NNB", "NR", "NP", "VV", "VA", "SL", "SN"}
    return [t.form for t in _kiwi.tokenize(text) if t.tag in keep]


def _source_name(stem: str) -> str:
    """텍스트 파일 이름에 맞는 원본 파일 이름(확장자 포함)을 찾는다."""
    for d in RAW_DIRS:
        for p in d.glob(f"{stem}.*"):
            return p.name
    return f"{stem}.txt"


def _meta(stem: str) -> dict:
    """'한양대_2028_시행계획' → 대학·학년도·문서 종류"""
    parts = stem.split("_")
    year = next((int(p) for p in parts if p.isdigit() and len(p) == 4), 0)
    kind = "_".join(p for p in parts[1:] if not (p.isdigit() and len(p) == 4)) or "문서"
    return {"univ": parts[0], "year": year, "kind": kind, "source": _source_name(stem)}


@lru_cache(maxsize=1)
def load_chunks() -> tuple[Document, ...]:
    """쪽 단위로 나눈 뒤 700자 청크로 자른다. 청크 앞에 [대학 학년도 문서 p.쪽]을 붙여 검색이 문서를 구분하게 한다."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
    chunks = []
    for path in sorted(TEXT_DIR.glob("*.txt")):
        meta = _meta(path.stem)
        parts = PAGE.split(path.read_text(encoding="utf-8"))
        for i in range(1, len(parts) - 1, 2):
            page, text = int(parts[i]), re.sub(r"[ \t]+", " ", parts[i + 1]).strip()
            if len(text) < 30:
                continue
            head = f"[{meta['univ']} {meta['year']} {meta['kind']} p.{page}]"
            for j, piece in enumerate(splitter.split_text(text)):
                chunks.append(Document(page_content=f"{head}\n{piece}",
                                       metadata={**meta, "page": page, "chunk_id": f"{path.stem}#p{page}#{j}"}))
    return tuple(chunks)


def _embeddings() -> BedrockEmbeddings:
    return BedrockEmbeddings(model_id=EMBED_MODEL, region_name="us-east-1")


def build_index() -> int:
    """청크를 임베딩해 chroma_db/에 저장한다. 같은 chunk_id는 덮어쓴다."""
    store = Chroma(collection_name=COLLECTION, embedding_function=_embeddings(), persist_directory=str(CHROMA_DIR))
    docs = list(load_chunks())
    for s in range(0, len(docs), 64):
        batch = docs[s:s + 64]
        store.add_documents(batch, ids=[d.metadata["chunk_id"] for d in batch])
        print(f"  임베딩 {min(s + 64, len(docs))}/{len(docs)}", flush=True)
    return len(docs)


@lru_cache(maxsize=1)
def _store() -> Chroma:
    store = Chroma(collection_name=COLLECTION, embedding_function=_embeddings(), persist_directory=str(CHROMA_DIR))
    if store._collection.count() == 0:
        raise RuntimeError("검색 인덱스가 없습니다. 먼저 `python -m src.retriever --build`를 실행하세요.")
    return store


@lru_cache(maxsize=64)
def _bm25(univs: tuple[str, ...] | None) -> BM25Retriever | None:
    docs = [d for d in load_chunks() if univs is None or d.metadata["univ"] in univs]
    if not docs:
        return None
    r = BM25Retriever.from_documents(docs, preprocess_func=korean_tokenizer)
    r.k = 20
    return r


def doc_univs() -> list[str]:
    """색인된 문서의 대학(문서 이름 앞부분) 목록"""
    return sorted({d.metadata["univ"] for d in load_chunks()})


def match_univ(name: str | None) -> str | None:
    """'한양대학교'·'건국대'·'인천대' 같은 이름을 문서 이름의 대학 부분으로 맞춘다. 못 찾으면 None."""
    if not name:
        return None
    q = name.replace("국립", "").replace("대학교", "대").replace(" ", "")
    cands = [u for u in doc_univs() if u.startswith(q) or q.startswith(u)]
    return min(cands, key=len) if cands else None


def search(query: str, univ: str | list[str] | None = None, k: int = 5, per_univ: int | None = None) -> list[Document]:
    """하이브리드 검색. univ(문서 이름의 대학 부분, 여럿이면 목록)를 주면 그 대학 문서만 본다.

    per_univ: 대학마다 최대 몇 청크까지 담을지. '전문대 간호는 내신을 어떻게 반영해?'처럼 여러 대학을 묻는 질문에서
    한 대학 문서가 상위를 독차지하지 않게 한다.
    """
    univs = (univ,) if isinstance(univ, str) else tuple(sorted(univ)) if univ else None
    where = None if not univs else {"univ": univs[0]} if len(univs) == 1 else {"univ": {"$in": list(univs)}}
    vec = _store().similarity_search(query, k=20, filter=where)
    bm = _bm25(univs)
    lex = bm.invoke(query) if bm else []
    scores, docs = {}, {}
    for name, ranked in (("vector", vec), ("bm25", lex)):
        for rank, d in enumerate(ranked):
            cid = d.metadata["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + WEIGHTS[name] / (RRF_K + rank + 1)
            docs.setdefault(cid, d)
    top, count = [], {}
    for cid in sorted(scores, key=scores.get, reverse=True):
        u = docs[cid].metadata["univ"]
        if per_univ and count.get(u, 0) >= per_univ:
            continue
        count[u] = count.get(u, 0) + 1
        top.append(docs[cid])
        if len(top) == k:
            break
    return top


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--build" in sys.argv:
        print("청크", len(load_chunks()), "개 → 인덱스 생성")
        print("완료:", build_index())
    else:
        q = " ".join(a for a in sys.argv[1:]) or "한양대 간호 추천형 수능최저"
        for d in search(q, univ=match_univ(q.split()[0])):
            print(f"{d.metadata['source']} p.{d.metadata['page']} | {d.page_content[:80].replace(chr(10), ' ')}")
