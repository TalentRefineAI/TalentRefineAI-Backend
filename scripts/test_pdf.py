"""
Script de prueba local para BK-04 y BK-05.
Prueba la extraccion, limpieza y chunking de un PDF sin necesitar Azure ni Qdrant.

Uso:
    python scripts/test_pdf.py ruta/al/archivo.pdf

Requiere:
    pip install pypdf langchain-text-splitters
"""
import io
import re
import sys
from pathlib import Path

# Verifica que se paso un argumento
if len(sys.argv) < 2:
    print("Uso: python scripts/test_pdf.py ruta/al/archivo.pdf")
    sys.exit(1)

pdf_path = Path(sys.argv[1])

if not pdf_path.exists():
    print(f"Error: no se encontro el archivo '{pdf_path}'")
    sys.exit(1)

if pdf_path.suffix.lower() != ".pdf":
    print("Error: el archivo debe ser un PDF")
    sys.exit(1)

# ── Importaciones de las librerias necesarias ─────────────────────────────────

try:
    from pypdf import PdfReader
except ImportError:
    print("Falta pypdf. Instala con: pip install pypdf")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Falta pymupdf. Instala con: pip install pymupdf")
    sys.exit(1)

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    print("Falta langchain-text-splitters. Instala con: pip install langchain-text-splitters")
    sys.exit(1)

# ── Parametros de chunking (deben coincidir con processing_service.py) ────────

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


# ── Funciones (copia directa de processing_service.py) ───────────────────────

def extract(pdf_bytes: bytes, file_name: str) -> tuple[str, dict]:
    """
    [BK-04] Extrae texto y metadata del PDF.
    Usa PyMuPDF (fitz) como extractor principal por su mayor compatibilidad.
    Cae a pypdf como fallback si fitz no esta disponible.
    """
    # Extraccion con PyMuPDF: soporta fuentes embebidas, Europass, PDFs escaneados con capa de texto
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = [page.get_text() for page in doc]
    text = "\n".join(pages_text)
    num_pages = doc.page_count
    doc.close()

    # Metadata desde pypdf (mas completa para /Title, /Author)
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta = reader.metadata or {}
    except Exception:
        meta = {}

    return text, {
        "file_name": file_name,
        "pages": num_pages,
        "title": meta.get("/Title", ""),
        "author": meta.get("/Author", ""),
    }


def clean_text(text: str) -> str:
    """[BK-04] Limpia el texto con expresiones regulares."""
    # Elimina caracteres no imprimibles
    text = re.sub(r'[^\x20-\x7EáéíóúÁÉÍÓÚüÜñÑ\n]', ' ', text)
    # Colapsa tres o mas saltos de linea en dos
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Colapsa espacios multiples en uno
    text = re.sub(r'[ \t]+', ' ', text)
    # Limpia margenes de cada linea
    text = '\n'.join(line.strip() for line in text.splitlines())
    return text.strip()


# ── Ejecucion del pipeline ────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"  Archivo: {pdf_path.name}")
print(f"{'='*60}\n")

# 1. Leer el archivo
pdf_bytes = pdf_path.read_bytes()
print(f"Tamano del archivo: {len(pdf_bytes) / 1024:.1f} KB")

# 2. Extraer texto y metadata [BK-04]
raw_text, metadata = extract(pdf_bytes, pdf_path.name)
print(f"\n[BK-04] Metadata del PDF:")
for k, v in metadata.items():
    print(f"  {k}: {v}")
print(f"\n[BK-04] Texto extraido: {len(raw_text)} caracteres")

# 3. Limpiar texto [BK-04]
clean = clean_text(raw_text)
print(f"[BK-04] Texto limpio:   {len(clean)} caracteres")

# 4. Chunking [BK-05]
splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=SEPARATORS,
)
chunks = splitter.split_text(clean)
print(f"\n[BK-05] Chunks generados: {len(chunks)}")
print(f"[BK-05] Configuracion: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}\n")

# 5. Guarda el resultado completo en un archivo .txt junto al PDF original
output_path = pdf_path.with_suffix(".resultado.txt")
lines = []
lines.append(f"ARCHIVO: {pdf_path.name}")
lines.append(f"Paginas : {metadata['pages']}")
lines.append(f"Titulo  : {metadata['title']}")
lines.append(f"Autor   : {metadata['author']}")
lines.append(f"Chars extraidos : {len(raw_text)}")
lines.append(f"Chars limpios   : {len(clean)}")
lines.append(f"Chunks generados: {len(chunks)}")
lines.append(f"Configuracion   : chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}")
lines.append("")
lines.append("=" * 60)
lines.append("TEXTO LIMPIO COMPLETO")
lines.append("=" * 60)
lines.append(clean)
lines.append("")
lines.append("=" * 60)
lines.append("CHUNKS GENERADOS")
lines.append("=" * 60)
for i, chunk in enumerate(chunks, 1):
    lines.append(f"\n--- Chunk {i}/{len(chunks)} ({len(chunk)} caracteres) ---")
    lines.append(chunk)

output_path.write_text("\n".join(lines), encoding="utf-8")
print(f"Resultado guardado en: {output_path}\n")

# 6. Muestra resumen de los primeros 3 chunks en terminal
for i, chunk in enumerate(chunks[:3], 1):
    print(f"{'─'*60}")
    print(f"  Chunk {i}/{len(chunks)} — {len(chunk)} caracteres")
    print(f"{'─'*60}")
    preview = chunk[:300].replace('\n', '↵ ')
    print(f"  {preview}{'...' if len(chunk) > 300 else ''}")
    print()

if len(chunks) > 3:
    print(f"  ... y {len(chunks) - 3} chunks mas.\n")

print("Prueba completada correctamente.")
