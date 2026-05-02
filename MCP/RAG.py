from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer, CrossEncoder
from pymilvus import MilvusClient
from tqdm import tqdm
from nltk.tokenize import sent_tokenize
import nltk
import json
from configurationLoader import config

# ===================== NLTK setup =====================
nltk.download("punkt")
nltk.download("punkt_tab")

# ===================== Milvus index params =====================
index_params = {
    "index_type": "DISKANN",
    "metric_type": "COSINE",
    "params": {
        "search_list_size": 100,
        "build_list_size": 100,
        "pq_code_size": 8
    }
}

# ===================== Models =====================
embedding_model = SentenceTransformer(config.get("model.embedding.path"))
cross_encoder = CrossEncoder("model.rerank.path")

# ===================== Milvus =====================
collection_name = "rag_sentence_collection"
milvus_client = MilvusClient(
    uri="./hf_milvus_demo.db",
    index_params=index_params
)

# ===================== Utils =====================
def emb_text(text: str):
    return embedding_model.encode(
        [text],
        normalize_embeddings=True
    ).tolist()[0]

# ===================== PDF → Sentence =====================
def load_pdf_as_sentences_strict(pdf_path):
    loader = PyPDFLoader(pdf_path)
    pages = loader.load()

    sentence_items = []
    global_id = 0

    for page_doc in pages:
        page_num = page_doc.metadata["page"]
        text = page_doc.page_content

        sentences = sent_tokenize(text)

        for idx, sent in enumerate(sentences):
            sent = sent.strip()
            if not sent:
                continue

            sentence_items.append({
                "id": global_id,
                "text": sent,
                "page": page_num,
                "sentence_index": idx
            })
            global_id += 1

    return sentence_items


# ===================== Build Vector DB =====================
def build_vector_db(sentence_items):
    dim = len(emb_text("test"))

    if milvus_client.has_collection(collection_name):
        milvus_client.drop_collection(collection_name)

    milvus_client.create_collection(
        collection_name=collection_name,
        dimension=dim,
        metric_type="IP",
        consistency_level="Strong"
    )

    data = []
    for item in tqdm(sentence_items, desc="Embedding sentences"):
        data.append({
            "id": item["id"],
            "vector": emb_text(item["text"]),
            "text": item["text"],
            "page": item["page"],
            "sentence_index": item["sentence_index"],
        })

    milvus_client.insert(collection_name, data)

# ===================== Vector Search =====================
def vector_search(query, top_k=20):
    res = milvus_client.search(
        collection_name=collection_name,
        data=[emb_text(query)],
        limit=top_k,
        search_params={"metric_type": "IP", "params": {}},
        output_fields=["text", "page", "sentence_index"]
    )[0]

    return [
        {
            "text": r["entity"]["text"],
            "page": r["entity"]["page"],
            "sentence_index": r["entity"]["sentence_index"]
        }
        for r in res
    ]

# ===================== Rerank =====================
def rerank(query, candidates, top_k=5):
    pairs = [(query, c["text"]) for c in candidates]
    scores = cross_encoder.predict(pairs)

    ranked = sorted(
        zip(scores, candidates),
        key=lambda x: x[0],
        reverse=True
    )

    return [item for _, item in ranked[:top_k]]

# ===================== MAIN =====================
if __name__ == "__main__":
    pdf_path = "The-AI-Act.pdf"
    question = "What is the legal basis for the proposal?"

    print("🔹 Loading and splitting PDF...")
    sentence_items = load_pdf_as_sentences_strict(pdf_path)

    print(f"🔹 Total sentences: {len(sentence_items)}")

    print("🔹 Building vector database...")
    build_vector_db(sentence_items)

    print("🔹 Retrieving candidate sentences...")
    candidates = vector_search(question, top_k=30)

    print("🔹 Reranking...")
    top_sentences = rerank(question, candidates, top_k=3)

    print("\n✅ Final retrieved evidence:\n")
    print(json.dumps(top_sentences, indent=4))
