"""Excepciones de dominio compartidas entre los tres microservicios."""


# Clase base que permite capturar cualquier error propio de la aplicacion
class TalentRefineError(Exception):
    """Base de todas las excepciones de la aplicacion."""


# Se lanza cuando se busca un documento que no existe en el sistema
class DocumentNotFoundError(TalentRefineError):
    pass


# Cubre fallos al leer o escribir en Azure Blob Storage
class StorageError(TalentRefineError):
    pass


# Cubre errores durante la extraccion de texto o el chunking del PDF
class ProcessingError(TalentRefineError):
    pass


# Cubre fallos en operaciones contra Qdrant (lectura o escritura)
class VectorStoreError(TalentRefineError):
    pass


# Cubre errores en la llamada al modelo de lenguaje (Azure OpenAI)
class LLMError(TalentRefineError):
    pass
