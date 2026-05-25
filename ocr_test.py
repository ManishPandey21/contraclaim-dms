#!/usr/bin/env python3
"""
Standalone Docling + OCR + Qdrant Indexer for scanned PDF letters.
Extracts metadata, summary, keywords and stores vector embeddings in Qdrant.
"""

import os, re, json
from pathlib import Path
from collections import Counter
import shutil

import pytesseract
from pdf2image import convert_from_path
from dotenv import load_dotenv
import subprocess, tempfile
from PIL import Image

import nltk, spacy
from nltk.corpus import stopwords
from nltk.tokenize import sent_tokenize, word_tokenize
# LlamaIndex/Qdrant optional imports with version compatibility
LLAMA_AVAILABLE = False
HAS_SETTINGS = False
try:
    # Newer LlamaIndex >=0.10 namespace
    from llama_index.core import Document, VectorStoreIndex, Settings
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient
    LLAMA_AVAILABLE = True
    HAS_SETTINGS = True
except Exception:
    try:
        # Older LlamaIndex <0.10 fallback
        from llama_index import Document, VectorStoreIndex, ServiceContext
        from llama_index.embeddings.openai import OpenAIEmbedding
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient
        LLAMA_AVAILABLE = True
        HAS_SETTINGS = False
    except Exception:
        LLAMA_AVAILABLE = False
        HAS_SETTINGS = False

# ---- NLP setup --------------------------------------------------------------
nltk.download("punkt", quiet=True)
nltk.download("punkt_tab", quiet=True)
nltk.download("stopwords", quiet=True)
nlp = spacy.load("en_core_web_sm")

# ---- CONFIG -----------------------------------------------------------------
INPUT_PDF = "GLM-SAM-KNPCC-05-UPMRC-OL-2025-4763.pdf"
OUTPUT_JSON = "parsed_letter_output.json"

QDRANT_URL = (os.getenv("QDRANT_URL", "http://localhost:6333") or "").strip()
QDRANT_API_KEY = (os.getenv("QDRANT_API_KEY") or "").strip()  # optional for local
QDRANT_COLLECTION = "letters"

# Load variables from .env if present
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # required for embeddings

# Resolve Tesseract and Poppler paths (Windows-friendly)
def _find_tesseract_path() -> str | None:
    # Check env override first
    p = os.getenv("TESSERACT_PATH")
    if p and Path(p).exists():
        return str(Path(p))
    # Check on PATH
    t = shutil.which("tesseract")
    if t:
        return t
    # Common install locations
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

def _find_poppler_dir() -> str | None:
    # POPPLER_PATH may be a bin dir or root
    p = os.getenv("POPPLER_PATH")
    if p:
        p_path = Path(p)
        if p_path.is_file() and p_path.name.lower() == "pdftoppm.exe":
            return str(p_path.parent)
        if (p_path / "pdftoppm.exe").exists():
            return str(p_path)
        if (p_path / "bin" / "pdftoppm.exe").exists():
            return str(p_path / "bin")
    # Check local bundled poppler (repo path)
    local_base = Path("services") / "docling" / "poppler"
    if local_base.exists():
        # Try common structure poppler-*/Library/bin
        for sub in local_base.iterdir():
            candidate = sub / "Library" / "bin"
            if (candidate / "pdftoppm.exe").exists():
                return str(candidate)
        # Fallback: search recursively for pdftoppm.exe
        try:
            exe = next(local_base.rglob("pdftoppm.exe"))
            return str(exe.parent)
        except StopIteration:
            pass
    # Check on PATH
    pp = shutil.which("pdftoppm")
    if pp:
        return str(Path(pp).parent)
    # Search common locations (can be a bit slow but robust)
    for root in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if root.exists():
            try:
                exe = next(root.rglob("pdftoppm.exe"))
                return str(exe.parent)
            except StopIteration:
                pass
    return None

# Apply detections
_TESSERACT_CMD = _find_tesseract_path()
if _TESSERACT_CMD:
    try:
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
    except Exception:
        pass

_POPPLER_DIR = _find_poppler_dir()
print("[DEBUG] POPPLER_DIR resolved to:", _POPPLER_DIR)
if _POPPLER_DIR:
    # Ensure pdf2image can find Poppler executables
    os.environ["POPPLER_PATH"] = _POPPLER_DIR

# ---- OCR --------------------------------------------------------------------
def ocr_pdf_to_text(pdf_path: str) -> str:
    kwargs = {"dpi": 300}
    if _POPPLER_DIR:
        kwargs["poppler_path"] = _POPPLER_DIR
    print(f"[DEBUG] pdf2image kwargs: {kwargs}")
    try:
        pages = convert_from_path(pdf_path, **kwargs)
    except Exception as e:
        print(f"[WARN] pdf2image failed ({e}); falling back to pdftoppm.")
        if not _POPPLER_DIR:
            raise
        pdftoppm = str(Path(_POPPLER_DIR) / ("pdftoppm.exe" if os.name == "nt" else "pdftoppm"))
        tmpdir = Path(tempfile.mkdtemp(prefix="ocr_pdf_"))
        outprefix = tmpdir / "page"
        cmd = f'"{pdftoppm}" -r {kwargs.get("dpi",300)} -png "{Path(pdf_path).resolve()}" "{outprefix}"'
        print(f"[DEBUG] Running: {cmd}")
        subprocess.run(cmd, shell=True, check=True)
        images = sorted(tmpdir.glob("page-*.png"))
        pages = [Image.open(p) for p in images]
    text = ""
    for i, page in enumerate(pages, 1):
        t = pytesseract.image_to_string(page)
        text += f"\n--- Page {i} ---\n{t}"
    return text.strip()

# ---- METADATA EXTRACTION ----------------------------------------------------
def extract_metadata(text: str) -> dict:
    m = {}
    m["letter_no"] = re.search(r"[A-Z]{2,5}-[A-Z]{2,5}-[A-Z0-9-]+-\d{4}-\d{3,5}", text)
    m["letter_no"] = m["letter_no"].group(0) if m["letter_no"] else None

    d = re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", text)
    m["letter_date"] = d[0] if d else None

    s = re.search(r"(?i)(subject\s*[:\-–]\s*)(.*)", text)
    m["subject"] = s.group(2).strip() if s else None

    refs = re.findall(r"Ref[:\-–]?\s*([A-Z0-9-/]+).*?(?:dated\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))?", text)
    m["references"] = [{"ref_no": r[0], "ref_date": r[1]} for r in refs if r[0]]

    to_b = re.search(r"(?i)(To[:\n].+?)(?:Subject|Sub:|Ref|Dear)", text, re.S)
    from_b = re.search(r"(?i)(for\s+[A-Z].+?Signatory|Yours\s+faithfully.+)", text, re.S)
    m["to_block"] = to_b.group(1).strip() if to_b else None
    m["from_block"] = from_b.group(1).strip() if from_b else None

    clauses = re.findall(r"(?:Clause|GCC|SCC|Sub-Clause)\s*[\d.]+", text)
    m["clauses"] = list(set(clauses)) if clauses else []
    return m

# ---- SUMMARY + KEYWORDS -----------------------------------------------------
def summarize_text(text: str, n_sentences: int = 5):
    sents = sent_tokenize(text)
    if len(sents) <= n_sentences: return sents
    doc = nlp(text)
    stop = set(stopwords.words("english"))
    freqs = {}
    for w in doc:
        if w.is_alpha and w.text.lower() not in stop:
            freqs[w.lemma_.lower()] = freqs.get(w.lemma_.lower(), 0) + 1
    maxf = max(freqs.values()) if freqs else 1
    for w in freqs: freqs[w] /= maxf
    scores = {}
    for s in sents:
        for w in word_tokenize(s.lower()):
            if w in freqs: scores[s] = scores.get(s,0)+freqs[w]
    ranked = sorted(scores, key=scores.get, reverse=True)
    return ranked[:n_sentences]

def extract_keywords(text: str, top_n=10):
    words = [w.lower() for w in word_tokenize(text) if w.isalpha()]
    stop = set(stopwords.words("english"))
    words = [w for w in words if w not in stop]
    return [w for w,_ in Counter(words).most_common(top_n)]

# ---- QDRANT + LLAMAINDEX ----------------------------------------------------
def index_to_qdrant(docs, metadata):
    if not LLAMA_AVAILABLE:
        print("[WARN] LlamaIndex/Qdrant not installed; skipping Qdrant indexing.")
        return
    if not OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY not set; skipping Qdrant indexing.")
        return
    if not QDRANT_API_KEY:
        print("[WARN] QDRANT_API_KEY not set; skipping Qdrant indexing.")
        return
    print("[INFO] Connecting to Qdrant ...")
    try:
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        exists = False
        if hasattr(client, "collection_exists"):
            try:
                exists = client.collection_exists(QDRANT_COLLECTION)
            except Exception:
                exists = False
        else:
            try:
                client.get_collection(QDRANT_COLLECTION)
                exists = True
            except Exception:
                exists = False
        if not exists:
            client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config={"size": 1536, "distance": "Cosine"},
            )
        vector_store = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION)
        if HAS_SETTINGS:
            prev_embed = getattr(Settings, "embed_model", None)
            try:
                Settings.embed_model = OpenAIEmbedding(api_key=OPENAI_API_KEY)
                index = VectorStoreIndex.from_documents(docs, vector_store=vector_store)
            finally:
                Settings.embed_model = prev_embed
        else:
            service_context = ServiceContext.from_defaults(embed_model=OpenAIEmbedding(api_key=OPENAI_API_KEY))
            index = VectorStoreIndex.from_documents(docs, service_context=service_context,
                                                    vector_store=vector_store)
        index.storage_context.persist()
        print(f"[DONE] Indexed document: {metadata.get('letter_no') or Path(INPUT_PDF).name}")
    except Exception as e:
        print(f"[WARN] Qdrant indexing failed: {e}. Skipping.")

# ---- MAIN -------------------------------------------------------------------
def main():
    print(f"[INFO] Reading and OCR: {INPUT_PDF}")
    raw_text = ocr_pdf_to_text(INPUT_PDF)
    if len(raw_text) < 50:
        print("[ERROR] Empty or invalid OCR output."); return

    print("[INFO] Extracting metadata ...")
    meta = extract_metadata(raw_text)

    print("[INFO] Generating summary & keywords ...")
    summary = summarize_text(raw_text)
    keywords = extract_keywords(raw_text)

    record = {
        "file": Path(INPUT_PDF).name,
        "metadata": meta,
        "body_text": raw_text,
        "summary": summary,
        "keywords": keywords,
    }
    Path(OUTPUT_JSON).write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"[INFO] Saved parsed JSON -> {OUTPUT_JSON}")

    if OPENAI_API_KEY and LLAMA_AVAILABLE:
        print("[INFO] Indexing to Qdrant ...")
        doc = Document(text=raw_text, metadata=meta)
        index_to_qdrant([doc], meta)
    elif not OPENAI_API_KEY:
        print("[WARN] OPENAI_API_KEY not set; skipping Qdrant indexing.")
    else:
        print("[WARN] LlamaIndex/Qdrant not installed; skipping Qdrant indexing.")

    print("\n[SUMMARY]")
    for s in summary: print("•", s)
    print("\n[KEYWORDS]", ", ".join(keywords))

if __name__ == "__main__":
    main()
